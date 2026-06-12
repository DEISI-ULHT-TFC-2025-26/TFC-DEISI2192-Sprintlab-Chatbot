"""
Relatório do projeto ("Relatórios automáticos") — 100% determinístico:
números calculados em código a partir de dados já obtidos do GitLab; o LLM
nunca conta nada. Funções puras → unit-testáveis.

O relatório ADAPTA-SE ao tipo de projeto:
  - "ativo"     → há commits nos últimos 90 dias: lidera com a atividade recente.
  - "concluido" → tem histórico mas 0 commits em 90 dias: tratado como terminado;
                  a secção "Atividade (90 dias)" é omitida (não acrescenta nada).
  - "vazio"     → sem commits nem issues.
Projetos só de código (0 issues) mostram "Sem issues registadas" em vez de uma
percentagem de progresso enganadora (0% não significa "fase inicial").
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from analytics import _issue_stats


def _due_status(due, today):
    """Classify a YYYY-MM-DD due date relative to `today`:
    'overdue' (past), 'soon' (≤7 days), 'ok' (further out), or None (no/bad date)."""
    if not due:
        return None
    try:
        d = date.fromisoformat(str(due))
    except (ValueError, TypeError):
        return None
    delta = (d - today).days
    if delta < 0:
        return "overdue"
    if delta <= 7:
        return "soon"
    return "ok"


def _about_lines(project_info, languages, tree):
    """Compact 'what is this project' block para o relatório (descrição,
    linguagens, estrutura de topo). Pure → testável."""
    pi = project_info or {}
    lines = []
    desc = (pi.get("description") or "").strip()
    if desc:
        lines.append(desc[:220])
    if languages:
        top = sorted(languages.items(), key=lambda x: -x[1])[:4]
        lines.append("Linguagens: " + ", ".join(f"{k} {float(v):.0f}%" for k, v in top))
    if tree:
        dirs = [e["name"] for e in tree if e.get("type") == "tree"][:8]
        if dirs:
            lines.append("Estrutura: " + ", ".join(dirs))
    topics = pi.get("topics") or pi.get("tag_list") or []
    if topics:
        lines.append("Tópicos: " + ", ".join(str(t) for t in topics[:8]))
    return lines


def _project_status(summary, activity, repo_commits):
    """'ativo' | 'concluido' | 'vazio' — a regra: sem atividade nos últimos 90
    dias mas com histórico = projeto concluído/terminado."""
    has_issues = summary["total"] > 0
    has_recent = activity["commits"] > 0
    if (repo_commits or 0) == 0 and not has_issues and not has_recent:
        return "vazio"
    return "ativo" if has_recent else "concluido"


def _report_highlights(summary, activity, contributors, mrs, milestones,
                       repo_commits, status):
    """Insights por regras, adaptados ao tipo de projeto. 100% determinístico."""
    if status == "vazio":
        return ["📭 Repositório sem commits nem issues registadas"]

    has_issues = summary["total"] > 0
    has_recent = activity["commits"] > 0
    h = []

    # 1) Atividade / estado do projeto
    if has_recent:
        top = activity["top"][0]["name"] if activity["top"] else "?"
        h.append(f"🔥 {activity['commits']} commits (90 dias) por "
                 f"{activity['authors']} autores — mais ativo: {top}")
    elif repo_commits:
        h.append(f"✅ Projeto concluído — {repo_commits} commits no total, "
                 f"sem atividade nos últimos 90 dias")
    else:
        h.append("✅ Sem atividade nos últimos 90 dias")

    # 2) Issues — só quando o projeto as usa (0 issues ≠ "fase inicial")
    if has_issues:
        p = summary["progress"]
        if p >= 80:
            h.append(f"✅ {p}% das issues fechadas")
        elif p >= 50:
            h.append(f"🔹 A meio — {p}% das issues fechadas")
        else:
            h.append(f"🔸 {p}% das issues fechadas")
        if summary["overdue"]:
            h.append(f"⚠️ {summary['overdue']} issue(s) em atraso a precisar de atenção")
        else:
            h.append("✅ Sem issues em atraso")
    else:
        h.append("📋 Sem issues registadas no GitLab")

    # 3) Autor principal — relevante sobretudo em repos de código: sem atividade
    #    recente, o "mais ativo" acima não aparece, por isso nomeamo-lo aqui.
    if not has_recent and contributors["top"]:
        tc = contributors["top"][0]
        h.append(f"👤 Autor principal: {tc['name']} ({tc['commits']} commits)")

    # 4) MRs / milestones
    if mrs["open"]:
        h.append(f"🔀 {mrs['open']} merge request(s) por rever")
    late = sum(1 for m in milestones if m["status"] == "overdue")
    soon = sum(1 for m in milestones if m["status"] == "soon")
    if late:
        h.append(f"📅 {late} milestone(s) com prazo ultrapassado")
    elif soon:
        h.append(f"📅 {soon} milestone(s) com prazo nos próximos 7 dias")
    return h


def _sprint_report_md(d):
    """Render the report dict as exportable Markdown (.md download). Adaptativo:
    omite o progresso quando não há issues e a atividade quando o projeto está
    concluído (sem commits recentes)."""
    s = d["summary"]
    L = [f"# Relatório do projeto — {d['project']}",
         f"_Gerado em {d['generated_at']}_", ""]

    if d.get("about"):
        L.append("## Sobre")
        L += [f"- {x}" for x in d["about"]]
        L.append("")

    L.append("## Destaques")
    L += [f"- {x}" for x in d["highlights"]]

    L += ["", "## Issues"]
    if d.get("has_issues"):
        L += [f"- Progresso: **{s['progress']}%** ({s['closed']}/{s['total']} fechadas)",
              f"- Abertas: {s['open']} · Fechadas: {s['closed']} · Total: {s['total']}",
              f"- Em atraso: {s['overdue']}"]
        if d["overdue_issues"]:
            L += ["", "### Issues em atraso"]
            L += [f"- #{i['iid']} {i['title']} (prazo: {i['due_date']})"
                  for i in d["overdue_issues"]]
    else:
        L.append("- Este projeto não usa issues do GitLab.")

    # Atividade (90 dias) — apenas em projetos ativos; um repo concluído não
    # precisa de uma secção a dizer "0 commits".
    if d.get("has_recent_activity"):
        a = d["activity90d"]
        L += ["", "## Atividade (90 dias)",
              f"- {a['commits']} commits por {a['authors']} autores"]
        L += [f"- {t['name']}: {t['count']} commits" for t in a["top"]]

    c = d["contributors"]
    L += ["", "## Commits"]
    if d.get("repo_commits") is not None:
        L.append(f"- Total no repositório: {d['repo_commits']}")
    L.append(f"- Autores: {c['authors']} (commits por autor abaixo)")
    L += [f"- {t['name']}: {t['commits']} commits" for t in c["top"]]

    if d["mrs"]["total"]:
        L += ["", "## Merge Requests",
              f"- Abertos: {d['mrs']['open']} · Total: {d['mrs']['total']}"]
    if d["milestones"]:
        L += ["", "## Milestones"]
        tag = {"overdue": " ⚠️ atrasado", "soon": " ⏳ em breve"}
        L += [f"- {m['title']} (prazo: {m['due_date'] or '—'}){tag.get(m['status'], '')}"
              for m in d["milestones"]]
    return "\n".join(L)


def build_sprint_report(project_info, issues, commits90, contribs,
                        milestones, mrs=None, today=None,
                        languages=None, tree=None):
    """Assemble the project report from pre-fetched GitLab data.

    Pure: takes data, returns a dict with a structured view + an exportable
    `markdown` string. `today` is injectable for deterministic tests. Tolerates
    any section being None (a failed fetch) — that section just reads as empty.
    `languages`/`tree` alimentam a secção "Sobre" (opcionais)."""
    today = today or date.today()
    issues = issues or []
    commits90 = commits90 or []
    contribs = contribs or []
    milestones = milestones or []
    mrs = mrs or []

    st = _issue_stats(issues, today=today)
    summary = {
        "open": len(st["opened"]), "closed": len(st["closed"]),
        "total": len(issues), "progress": st["progress"],
        "overdue": len(st["overdue"]), "no_assignee": st["no_assignee"],
    }

    pname = "?"
    pid = None
    repo_commits = None  # canonical total (statistics.commit_count) — same as the panel
    if project_info:
        pname = (project_info.get("name_with_namespace")
                 or project_info.get("name") or "?")
        pid = project_info.get("id")
        repo_commits = (project_info.get("statistics") or {}).get("commit_count")

    by90 = Counter((c.get("author_name") or "?") for c in commits90)
    activity = {
        "commits": len(commits90), "authors": len(by90),
        "top": [{"name": n, "count": c} for n, c in by90.most_common(5)],
    }

    contributors = {
        "total": sum(c.get("commits", 0) for c in contribs),
        "authors": len(contribs),
        "top": [{"name": c.get("name") or "?", "commits": c.get("commits", 0)}
                for c in contribs[:5]],
    }

    mr_data = {"open": sum(1 for m in mrs if m.get("state") == "opened"),
               "total": len(mrs)}

    ms_data = [{"title": m.get("title"), "due_date": m.get("due_date"),
                "status": _due_status(m.get("due_date"), today)}
               for m in milestones]

    overdue_issues = [{"iid": i["iid"], "title": i["title"],
                       "due_date": i.get("due_date")} for i in st["overdue"][:10]]

    status = _project_status(summary, activity, repo_commits)

    data = {
        "generated_at": today.isoformat(),
        "project": pname,
        "project_id": pid,
        "repo_commits": repo_commits,
        "status": status,                              # ativo | concluido | vazio
        "about": _about_lines(project_info, languages, tree),
        "has_issues": summary["total"] > 0,
        "has_recent_activity": activity["commits"] > 0,
        "summary": summary,
        "highlights": _report_highlights(summary, activity, contributors,
                                         mr_data, ms_data, repo_commits, status),
        "activity90d": activity,
        "contributors": contributors,
        "mrs": mr_data,
        "milestones": ms_data,
        "overdue_issues": overdue_issues,
    }
    data["markdown"] = _sprint_report_md(data)
    return data
