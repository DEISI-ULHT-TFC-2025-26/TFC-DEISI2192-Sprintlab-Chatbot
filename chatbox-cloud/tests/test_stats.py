"""Tests for _issue_stats — the shared aggregation behind both the LLM context
and the /gitlab/stats panel. `today` is injected so 'overdue' is deterministic."""

from datetime import date

import server

TODAY = date(2026, 6, 11)


def _issues():
    return [
        {"state": "closed", "iid": 1, "title": "a"},
        {"state": "closed", "iid": 2, "title": "b"},
        # opened + due in the past + has assignee -> overdue, not no_assignee
        {"state": "opened", "iid": 3, "title": "c",
         "due_date": "2026-06-01", "assignee": {"name": "X"}},
        # opened + due in the future + has assignee -> neither
        {"state": "opened", "iid": 4, "title": "d",
         "due_date": "2026-12-01", "assignee": {"name": "Y"}},
        # opened + no due + no assignee -> no_assignee
        {"state": "opened", "iid": 5, "title": "e"},
    ]


class TestIssueStats:
    def test_open_closed_counts(self):
        s = server._issue_stats(_issues(), today=TODAY)
        assert len(s["opened"]) == 3
        assert len(s["closed"]) == 2

    def test_progress_is_closed_over_total(self):
        s = server._issue_stats(_issues(), today=TODAY)
        assert s["progress"] == 40.0  # 2 / 5

    def test_overdue_uses_injected_today(self):
        s = server._issue_stats(_issues(), today=TODAY)
        assert [i["iid"] for i in s["overdue"]] == [3]

    def test_no_assignee_only_counts_open(self):
        s = server._issue_stats(_issues(), today=TODAY)
        assert s["no_assignee"] == 1

    def test_malformed_due_date_is_skipped(self):
        issues = _issues() + [{"state": "opened", "iid": 6, "due_date": "garbage"}]
        s = server._issue_stats(issues, today=TODAY)  # must not raise
        assert all(i["iid"] != 6 for i in s["overdue"])

    def test_empty_list(self):
        s = server._issue_stats([], today=TODAY)
        assert s["progress"] == 0
        assert s["opened"] == [] and s["closed"] == [] and s["overdue"] == []
        assert s["no_assignee"] == 0
