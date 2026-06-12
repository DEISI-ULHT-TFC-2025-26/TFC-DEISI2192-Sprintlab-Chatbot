"""Tests for issues_to_csv — the export feature. The BOM matters: without it
Excel-pt mangles the accented column headers."""

import server

ISSUES = [
    {"iid": 1, "title": "Acentuação à prova", "state": "opened",
     "assignee": {"name": "Ana"}, "labels": ["bug", "urgent"],
     "due_date": "2026-07-01", "created_at": "2026-06-11T10:00:00Z",
     "web_url": "https://gitlab.com/x/issues/1"},
    {"iid": 2, "title": "Sem assignee", "state": "closed",
     "assignee": None, "labels": [],
     "due_date": "", "created_at": "2026-05-01T09:00:00Z",
     "web_url": "https://gitlab.com/x/issues/2"},
]


class TestIssuesToCsv:
    def test_returns_bytes_with_bom(self):
        out = server.issues_to_csv(ISSUES)
        assert isinstance(out, bytes)
        assert out.startswith(b"\xef\xbb\xbf")  # utf-8-sig BOM for Excel

    def test_header_row(self):
        text = server.issues_to_csv(ISSUES).decode("utf-8-sig")
        first = text.splitlines()[0]
        for col in ["ID", "Título", "Estado", "Assignee", "Labels", "Due Date"]:
            assert col in first

    def test_rows_and_values(self):
        text = server.issues_to_csv(ISSUES).decode("utf-8-sig")
        assert "#1" in text and "Acentuação à prova" in text and "Ana" in text
        assert "bug,urgent" in text
        # created_at is truncated to the date only
        assert "2026-06-11" in text and "T10:00:00" not in text

    def test_handles_missing_assignee(self):
        text = server.issues_to_csv(ISSUES).decode("utf-8-sig")
        assert "Sem assignee" in text  # row with assignee=None must not crash

    def test_empty_list_is_header_only(self):
        text = server.issues_to_csv([]).decode("utf-8-sig")
        assert len([ln for ln in text.splitlines() if ln.strip()]) == 1


COMMITS = [
    {"id": "abcdef1234567890", "short_id": "abcdef123456",
     "author_name": "Ana Sá", "author_email": "ana@gmv.com",
     "created_at": "2026-06-10T14:00:00Z", "title": "Corrige parser"},
    {"id": "deadbeefcafe", "author_name": "Rui", "author_email": "",
     "created_at": "2026-05-01T09:00:00Z", "title": "Initial commit"},
]


class TestCommitsToCsv:
    def test_returns_bytes_with_bom(self):
        out = server.commits_to_csv(COMMITS)
        assert isinstance(out, bytes)
        assert out.startswith(b"\xef\xbb\xbf")

    def test_header_row(self):
        first = server.commits_to_csv(COMMITS).decode("utf-8-sig").splitlines()[0]
        for col in ["SHA", "Autor", "Email", "Data", "Título"]:
            assert col in first

    def test_values_and_short_sha(self):
        text = server.commits_to_csv(COMMITS).decode("utf-8-sig")
        assert "abcdef123456" in text and "Ana Sá" in text
        # date truncated, full SHA not leaked when short_id exists
        assert "2026-06-10" in text and "T14:00:00" not in text

    def test_falls_back_to_id_when_no_short_id(self):
        text = server.commits_to_csv(COMMITS).decode("utf-8-sig")
        assert "deadbeefcafe" in text  # second commit had no short_id

    def test_empty_list_is_header_only(self):
        text = server.commits_to_csv([]).decode("utf-8-sig")
        assert len([ln for ln in text.splitlines() if ln.strip()]) == 1
