"""
Agregações e contexto: estatísticas de issues, visão geral do repositório
(descrição/linguagens/estrutura/README), o contexto GitLab injetado no LLM
em cada turno, e os exports CSV.
"""

from __future__ import annotations

import csv
import io
import logging
from collections import Counter
from datetime import date

from config import CACHE_TTL, README_MAX
from gitlab_api import (_find_readme, _get_commits, _get_contributors,
                        _get_languages, _get_readme_text, _get_repo_tree_top,
                        _gl, _run_with_ctx, get_all_issues, get_milestones,
                        get_project_info, gitlab_pool)

log = logging.getLogger("sprintlab")


def _strip_html_tags(s):
    """Remove <...> tags without regex (server.py avoids the re module)."""
    out, depth = [], 0
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _clean_readme(text):
    """Strip badge/image/HTML/link-def/rule noise so the excerpt budget is spent
    on actual prose (a model shouldn't have to guess what the project is from a
    wall of badges). Pure → unit-testable."""
    if not text:
        return ""
    kept = []
    for raw in str(text).replace("\r\n", "\n").split("\n"):
        s = raw.strip()
        if not s:
            kept.append("")
            continue
        low = s.lower()
        if (s.startswith("![") or s.startswith("[![")
                or low.startswith("<!--") or low.startswith("-->")):
            continue  # badge / linked-badge / image-only line or HTML comment
        if s.startswith("[") and "]:" in s and ("http" in low or "mailto" in low):
            continue  # reference-style link definition
        if len(s) >= 3 and set(s) <= {"-", "=", "*", "_", " "}:
            continue  # horizontal rule
        s = _strip_html_tags(s).strip()
        if s:
            kept.append(s)
    # collapse runs of blank lines
    out, blank = [], False
    for ln in kept:
        if ln == "":
            if out and not blank:
                out.append("")
            blank = True
        else:
            out.append(ln)
            blank = False
    return "\n".join(out).strip()


def _readme_outline(text, limit=10):
    """Markdown heading titles (the README's table of contents) → the project's
    structure at a glance. Skips headings inside fenced code blocks."""
    heads, in_fence = [], False
    for raw in str(text or "").replace("\r\n", "\n").split("\n"):
        s = raw.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not s.startswith("#"):
            continue
        title = s.lstrip("#").strip().rstrip("#").strip()
        if title and title not in heads and 1 <= len(title) <= 60:
            heads.append(title)
            if len(heads) >= limit:
                break
    return heads


def _repo_overview_lines(info, languages, tree, readme, readme_max=README_MAX):
    """Compact 'what is this project' block. Pure (no network) → unit-testable."""
    info = info or {}
    lines = []
    desc = (info.get("description") or "").strip()
    if desc:
        lines.append(f"Descrição: {desc[:300]}")
    topics = info.get("topics") or info.get("tag_list") or []
    if topics:
        lines.append("Tópicos: " + ", ".join(str(t) for t in topics[:10]))
    if info.get("default_branch"):
        lines.append(f"Branch principal: {info['default_branch']}")
    if languages:
        top = sorted(languages.items(), key=lambda x: -x[1])[:6]
        lines.append("Linguagens: " + ", ".join(f"{k} {float(v):.0f}%" for k, v in top))
    if tree:
        dirs = [e["name"] for e in tree if e.get("type") == "tree"][:15]
        files = [e["name"] for e in tree if e.get("type") == "blob"][:15]
        if dirs:
            lines.append("Pastas (topo): " + ", ".join(dirs))
        if files:
            lines.append("Ficheiros (topo): " + ", ".join(files))
    outline = _readme_outline(readme)
    if outline:
        lines.append("README (secções): " + ", ".join(outline))
    rd = _clean_readme(readme)
    if rd:
        excerpt = rd[:readme_max]
        if len(rd) > readme_max:
            excerpt += " …"
        lines.append("README (excerto):\n" + excerpt)
    return lines


def _get_repo_overview(info):
    """Assemble the repo-overview lines, each piece independently tolerated so a
    failed fetch (e.g. empty repo) never breaks the whole context."""
    ref = (info or {}).get("default_branch") or "main"
    try:
        languages = _get_languages()
    except Exception as e:
        log.warning("languages fetch failed: %s", e)
        languages = {}
    try:
        tree = _get_repo_tree_top(ref)
    except Exception as e:
        log.warning("tree fetch failed: %s", e)
        tree = []
    try:
        readme = _get_readme_text(ref, tree)
    except Exception as e:
        log.warning("readme fetch failed: %s", e)
        readme = ""
    return _repo_overview_lines(info, languages, tree, readme)


def _issue_stats(all_issues, today=None):
    """Shared aggregation over the issue list (used by the LLM context AND the
    /gitlab/stats endpoint — one implementation, no drift).

    `today` is injectable so the overdue calc is deterministic under test;
    callers in production omit it and get the real current date."""
    today = today or date.today()
    opened = [i for i in all_issues if i.get("state") == "opened"]
    closed = [i for i in all_issues if i.get("state") == "closed"]
    overdue = []
    for i in opened:
        d = i.get("due_date")
        if not d:
            continue
        try:
            if date.fromisoformat(d) < today:
                overdue.append(i)
        except ValueError:
            pass  # malformed date in GitLab — skip
    no_assignee = sum(1 for i in opened if not i.get("assignee"))
    progress = round(len(closed) / len(all_issues) * 100, 1) if all_issues else 0
    return {"opened": opened, "closed": closed, "overdue": overdue,
            "no_assignee": no_assignee, "progress": progress}


# ── Aggregate context for the LLM ─────────────────────────────────────────────


def get_gitlab_context() -> str:
    try:
        # Parallel: issues + milestones + commits in flight at the same time.
        # Pass the current GitLab config into the pool workers explicitly.
        cfg = _gl()
        f_issues = gitlab_pool.submit(_run_with_ctx, cfg, get_all_issues, "all")
        f_ms = gitlab_pool.submit(_run_with_ctx, cfg, get_milestones)
        f_commits = gitlab_pool.submit(_run_with_ctx, cfg, _get_commits, 90)
        f_contrib = gitlab_pool.submit(_run_with_ctx, cfg, _get_contributors)
        f_info = gitlab_pool.submit(_run_with_ctx, cfg, get_project_info)
        f_langs = gitlab_pool.submit(_run_with_ctx, cfg, _get_languages)  # warm cache
        all_issues = f_issues.result()
        milestones = f_ms.result()
    except Exception as e:
        log.warning("gitlab fetch failed: %s", e)
        return f"\n=== GITLAB: erro ao carregar ({e}) ===\n"

    s = _issue_stats(all_issues)
    opened, closed, overdue = s["opened"], s["closed"], s["overdue"]

    by_assignee: dict[str, int] = {}
    for i in opened:
        name = i["assignee"]["name"] if i.get("assignee") else "Sem assignee"
        by_assignee[name] = by_assignee.get(name, 0) + 1

    issues_list = "\n".join(
        f"  #{i['iid']} {i['title']} | "
        f"{i['assignee']['name'] if i.get('assignee') else 'Nenhum'} | "
        f"due:{i.get('due_date') or 'N/A'} | "
        f"{','.join(i.get('labels', [])[:2]) or 'sem label'}"
        for i in opened[:10]  # cap the per-turn token cost
    ) or "  Nenhuma issue aberta."

    assignee_rank = "\n".join(
        f"  {n}: {c}" for n, c in sorted(by_assignee.items(), key=lambda x: -x[1])
    ) or "  Nenhum."

    overdue_list = "\n".join(
        f"  #{i['iid']} {i['title']} (due:{i['due_date']})" for i in overdue[:5]
    ) or "  Nenhuma."

    ms_list = "\n".join(
        f"  {m['title']} (due:{m.get('due_date', 'N/A')})" for m in milestones
    ) or "  Nenhum."

    # Commits summary (the chatbot also analyses commits, not just issues).
    try:
        commits90 = f_commits.result()
        c_by = Counter((c.get("author_name") or "?") for c in commits90)
        top_c = ", ".join(f"{n} ({c})" for n, c in c_by.most_common(5)) or "nenhum"
        commits_line = (f"Últimos 90 dias: {len(commits90)} commits "
                        f"por {len(c_by)} autores. Top: {top_c}")
    except Exception as e:
        log.warning("commits fetch for context failed: %s", e)
        commits_line = "indisponível"
    # Per-author commit breakdown ("quem tem mais commits"). Its SUM can differ
    # from the repo's commit_count (the contributors API counts per author on the
    # default branch and excludes some commits), so this is presented ONLY as a
    # per-author ranking — the headline total comes from commit_count below.
    try:
        contribs = f_contrib.result()
        contrib_authors = len(contribs)
        top_all = ", ".join(
            f"{c.get('name') or '?'} ({c.get('commits', 0)})" for c in contribs[:5]
        ) or "nenhum"
    except Exception as e:
        log.warning("contributors fetch for context failed: %s", e)
        contrib_authors, top_all = 0, "indisponível"

    # Project metadata + repo overview so the model knows WHAT the project is
    # (descrição, linguagens, estrutura, README) — não só a contagem de issues.
    f_langs.result()  # garante que as linguagens já estão em cache p/ o overview
    try:
        info = f_info.result()
        pname = info.get("name_with_namespace") or "?"
    except Exception:
        info, pname = {}, "?"
    # Canonical repo commit total — the SAME number the stats panel shows, so the
    # chatbot never contradicts the panel (e.g. 2318, not the ~2000 per-author sum).
    commit_count = (info.get("statistics") or {}).get("commit_count") if info else None
    if commit_count is not None:
        commits_total_line = f"Total de commits no repositório: {commit_count}"
    else:
        commits_total_line = "Total de commits no repositório: indisponível"
    authors_line = f"Autores: {contrib_authors}. Mais ativos: {top_all}"
    try:
        overview = "\n".join(_get_repo_overview(info))
    except Exception as e:
        log.warning("repo overview failed: %s", e)
        overview = ""
    repo_section = f"\n\nREPOSITÓRIO:\n{overview}" if overview else ""

    return f"""
=== DADOS GITLAB (tempo real, cache {CACHE_TTL}s) ===
Projeto: {pname}  |  ID: {_gl()['project']}
Total: {len(all_issues)} | Abertas: {len(opened)} | Fechadas: {len(closed)} | Progresso: {s['progress']}%
Em atraso: {len(overdue)} | Sem assignee: {s['no_assignee']}{repo_section}

ISSUES ABERTAS:
{issues_list}

RANKING ASSIGNEES:
{assignee_rank}

EM ATRASO:
{overdue_list}

MILESTONES:
{ms_list}

COMMITS:
  {commits_total_line}
  {authors_line}
  {commits_line}"""


# ── CSV ───────────────────────────────────────────────────────────────────────


def issues_to_csv(issues) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        ["ID", "Título", "Estado", "Assignee", "Labels",
         "Due Date", "Criada em", "URL"]
    )
    for i in issues:
        w.writerow([
            f"#{i['iid']}",
            i["title"],
            i["state"],
            i["assignee"]["name"] if i.get("assignee") else "",
            ",".join(i.get("labels", [])),
            i.get("due_date", ""),
            i["created_at"][:10],
            i["web_url"],
        ])
    # utf-8-sig writes a BOM so Excel-pt opens accents correctly.
    return out.getvalue().encode("utf-8-sig")


def commits_to_csv(commits) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["SHA", "Autor", "Email", "Data", "Título"])
    for c in commits:
        w.writerow([
            (c.get("short_id") or c.get("id") or "")[:12],
            c.get("author_name") or "",
            c.get("author_email") or "",
            (c.get("created_at") or "")[:10],
            c.get("title") or "",
        ])
    return out.getvalue().encode("utf-8-sig")
