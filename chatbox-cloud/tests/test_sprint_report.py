"""Tests for the sprint report ('Relatórios automáticos' feature).

The whole point of building the report deterministically server-side is that the
numbers are *exact* — never an LLM guess. These tests lock that contract in."""

from datetime import date

import server

TODAY = date(2026, 6, 11)

PROJECT = {"id": 42, "name_with_namespace": "GMV / air-demo",
           "statistics": {"commit_count": 2318}}
ISSUES = [
    {"state": "closed", "iid": 1, "title": "a"},
    {"state": "closed", "iid": 2, "title": "b"},
    {"state": "opened", "iid": 3, "title": "atrasada",
     "due_date": "2026-06-01", "assignee": {"name": "Ana"}},
    {"state": "opened", "iid": 4, "title": "no prazo",
     "due_date": "2026-12-01", "assignee": {"name": "Rui"}},
    {"state": "opened", "iid": 5, "title": "sem dono"},
]
COMMITS90 = [
    {"author_name": "Ana"}, {"author_name": "Ana"}, {"author_name": "Rui"},
]
CONTRIBS = [
    {"name": "Ana", "commits": 200},
    {"name": "Rui", "commits": 150},
]
MILESTONES = [
    {"title": "Sprint 1", "due_date": "2026-06-01"},   # overdue
    {"title": "Sprint 2", "due_date": "2026-06-15"},   # soon (<=7d)
    {"title": "Sprint 3", "due_date": "2026-09-01"},   # ok
]
MRS = [{"state": "opened"}, {"state": "merged"}, {"state": "opened"}]


def _report(**over):
    kw = dict(project_info=PROJECT, issues=ISSUES, commits90=COMMITS90,
              contribs=CONTRIBS, milestones=MILESTONES, mrs=MRS, today=TODAY)
    kw.update(over)
    return server.build_sprint_report(**kw)


# ── _due_status ───────────────────────────────────────────────────────────────

class TestDueStatus:
    def test_overdue(self):
        assert server._due_status("2026-06-01", TODAY) == "overdue"

    def test_soon_within_7_days(self):
        assert server._due_status("2026-06-15", TODAY) == "soon"
        assert server._due_status("2026-06-18", TODAY) == "soon"  # exactly 7

    def test_ok_when_far(self):
        assert server._due_status("2026-06-19", TODAY) == "ok"   # 8 days

    def test_none_and_bad(self):
        assert server._due_status(None, TODAY) is None
        assert server._due_status("garbage", TODAY) is None


# ── build_sprint_report: numbers ──────────────────────────────────────────────

class TestReportNumbers:
    def test_summary_matches_issue_stats(self):
        d = _report()
        assert d["summary"] == {
            "open": 3, "closed": 2, "total": 5, "progress": 40.0,
            "overdue": 1, "no_assignee": 1,
        }

    def test_project_name_and_id(self):
        d = _report()
        assert d["project"] == "GMV / air-demo"
        assert d["project_id"] == 42

    def test_activity_90d(self):
        d = _report()
        assert d["activity90d"]["commits"] == 3
        assert d["activity90d"]["authors"] == 2
        assert d["activity90d"]["top"][0] == {"name": "Ana", "count": 2}

    def test_contributors_all_time(self):
        d = _report()
        assert d["contributors"]["total"] == 350
        assert d["contributors"]["authors"] == 2

    def test_repo_commit_count_is_canonical_total(self):
        # the headline total comes from statistics.commit_count (same as the stats
        # panel) — NOT the per-author contributor sum (350), which differs
        d = _report()
        assert d["repo_commits"] == 2318
        assert "Total no repositório: 2318" in d["markdown"]

    def test_repo_commits_none_when_no_statistics(self):
        d = _report(project_info={"id": 1, "name_with_namespace": "x / y"})
        assert d["repo_commits"] is None
        assert "Total no repositório" not in d["markdown"]

    def test_merge_requests(self):
        d = _report()
        assert d["mrs"] == {"open": 2, "total": 3}

    def test_milestone_statuses(self):
        d = _report()
        statuses = {m["title"]: m["status"] for m in d["milestones"]}
        assert statuses == {"Sprint 1": "overdue", "Sprint 2": "soon", "Sprint 3": "ok"}

    def test_overdue_issues_listed(self):
        d = _report()
        assert [i["iid"] for i in d["overdue_issues"]] == [3]

    def test_generated_at_uses_injected_today(self):
        assert _report()["generated_at"] == "2026-06-11"


# ── highlights (rule-based) ───────────────────────────────────────────────────

class TestHighlights:
    def test_mentions_progress(self):
        hs = " ".join(_report()["highlights"])
        assert "40" in hs and "%" in hs

    def test_flags_overdue(self):
        hs = " ".join(_report()["highlights"])
        assert "atraso" in hs.lower()

    def test_no_overdue_reads_clean(self):
        only_closed = [{"state": "closed", "iid": 1, "title": "x"}]
        hs = " ".join(_report(issues=only_closed)["highlights"])
        assert "Sem issues em atraso" in hs

    def test_dormant_repo_reads_as_concluded(self):
        # sem commits em 90 dias mas com histórico → "concluído", não "sem commits"
        hs = " ".join(_report(commits90=[])["highlights"])
        assert "concluído" in hs.lower() and "sem atividade" in hs.lower()

    def test_code_project_no_misleading_progress(self):
        # 0 issues → "sem issues registadas", NUNCA "fase inicial / 0% concluído"
        hs = " ".join(_report(issues=[], commits90=[])["highlights"])
        assert "Sem issues registadas" in hs
        assert "fase inicial" not in hs.lower()

    def test_dormant_names_main_author(self):
        hs = " ".join(_report(commits90=[])["highlights"])
        assert "Autor principal" in hs and "Ana" in hs


# ── markdown export ───────────────────────────────────────────────────────────

class TestMarkdown:
    def test_has_title_and_project(self):
        md = _report()["markdown"]
        assert "Relatório do projeto" in md
        assert "GMV / air-demo" in md

    def test_lists_overdue_issue(self):
        md = _report()["markdown"]
        assert "#3" in md and "atrasada" in md

    def test_is_a_string(self):
        assert isinstance(_report()["markdown"], str)


# ── resilience: any section may be None when a fetch fails ────────────────────

class TestResilience:
    def test_all_none_does_not_crash(self):
        d = server.build_sprint_report(None, None, None, None, None,
                                       mrs=None, today=TODAY)
        assert d["summary"]["total"] == 0
        assert d["project"] == "?"
        assert d["mrs"] == {"open": 0, "total": 0}
        assert isinstance(d["markdown"], str)

    def test_missing_project_info(self):
        d = _report(project_info=None)
        assert d["project"] == "?"
        assert d["project_id"] is None


# ── adaptativo ao tipo de projeto (a melhoria do lp2-jogo / air-demo) ─────────

class TestAdaptive:
    def test_status_active(self):
        assert _report()["status"] == "ativo"           # tem commits recentes

    def test_status_concluded_when_dormant(self):
        d = _report(commits90=[])                        # histórico, 0 recentes
        assert d["status"] == "concluido"
        assert d["has_recent_activity"] is False

    def test_status_empty(self):
        d = _report(project_info={"id": 1, "name_with_namespace": "x / y"},
                    issues=[], commits90=[], contribs=[])
        assert d["status"] == "vazio"
        assert "Repositório sem commits" in d["highlights"][0]

    def test_activity_section_dropped_when_concluded(self):
        # a secção "Atividade (90 dias)" não aparece num projeto concluído
        assert "## Atividade (90 dias)" not in _report(commits90=[])["markdown"]
        # mas aparece num projeto ativo
        assert "## Atividade (90 dias)" in _report()["markdown"]

    def test_no_issues_section_is_a_one_liner(self):
        md = _report(issues=[], commits90=[])["markdown"]
        assert "Este projeto não usa issues do GitLab" in md
        assert "Progresso:" not in md   # nada de percentagem enganadora

    def test_about_section_from_languages_and_tree(self):
        md = _report(languages={"Java": 100.0},
                     tree=[{"name": "src", "type": "tree"},
                           {"name": "README.md", "type": "blob"}])["markdown"]
        assert "## Sobre" in md
        assert "Java 100%" in md and "src" in md

    def test_about_uses_description(self):
        pi = dict(PROJECT, description="Jogo de tabuleiro em Java")
        d = _report(project_info=pi)
        assert any("Jogo de tabuleiro" in x for x in d["about"])

    def test_commits_total_survives_in_code_project(self):
        # o lp2-jogo: 0 issues, mas os 153 commits têm de aparecer
        md = _report(issues=[], commits90=[])["markdown"]
        assert "Total no repositório: 2318" in md
