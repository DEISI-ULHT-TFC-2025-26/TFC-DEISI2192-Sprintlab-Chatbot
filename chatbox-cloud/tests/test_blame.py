"""Tests for the code-investigation helpers (git blame feature).

The deterministic layer — which commit owns a line, path resolution, diff
truncation — must be exact: it's presented to the user as fact."""

import server


# ── _path_candidates: pasted paths are often absolute (IDE / stack trace) ─────

class TestPathCandidates:
    def test_relative_path_kept_first(self):
        assert server._path_candidates("src/app.js")[0] == "src/app.js"

    def test_strips_leading_segments_down_to_basename(self):
        out = server._path_candidates("C:/Users/x/proj/src/app.js")
        assert out[0] == "C:/Users/x/proj/src/app.js"
        assert "src/app.js" in out
        assert out[-1] == "app.js"

    def test_backslashes_normalised(self):
        out = server._path_candidates("src\\sub\\file.py")
        assert out[0] == "src/sub/file.py"
        assert out[-1] == "file.py"

    def test_dot_slash_and_leading_slash(self):
        assert server._path_candidates("./src/a.py")[0] == "src/a.py"
        assert server._path_candidates("/src/a.py")[0] == "src/a.py"

    def test_no_duplicates(self):
        out = server._path_candidates("a/a.py")
        assert len(out) == len(set(out))

    def test_caps_at_8(self):
        deep = "/".join(["d"] * 20) + "/f.py"
        assert len(server._path_candidates(deep)) <= 8

    def test_empty(self):
        assert server._path_candidates("") == []
        assert server._path_candidates(None) == []


# ── _blame_owner: which commit owns the target line ───────────────────────────

BLAME = [
    {"commit": {"id": "aaa111", "author_name": "Ana"}, "lines": ["x=1", "y=2"]},
    {"commit": {"id": "bbb222", "author_name": "Rui"}, "lines": ["z=3"]},
]


class TestBlameOwner:
    def test_first_entry_lines(self):
        c, txt = server._blame_owner(BLAME, 10, 10)
        assert c["id"] == "aaa111" and txt == "x=1"
        c, txt = server._blame_owner(BLAME, 10, 11)
        assert c["id"] == "aaa111" and txt == "y=2"

    def test_second_entry_line(self):
        c, txt = server._blame_owner(BLAME, 10, 12)
        assert c["id"] == "bbb222" and txt == "z=3"

    def test_line_beyond_range_returns_none(self):
        c, txt = server._blame_owner(BLAME, 10, 13)
        assert c is None and txt == ""

    def test_empty_blame(self):
        assert server._blame_owner([], 1, 1) == (None, "")
        assert server._blame_owner(None, 1, 1) == (None, "")


# ── _blame_snippet: numbered excerpt for the LLM ──────────────────────────────

class TestBlameSnippet:
    def test_marks_target_line(self):
        snip = server._blame_snippet(BLAME, 10, 11)
        lines = snip.splitlines()
        assert lines[1].startswith(">>")
        assert lines[0].startswith("  ") and lines[2].startswith("  ")

    def test_numbers_and_sha_tags(self):
        snip = server._blame_snippet(BLAME, 10, 11)
        assert "10" in snip and "12" in snip
        assert "[aaa111" in snip and "[bbb222" in snip

    def test_truncates_long_lines(self):
        blame = [{"commit": {"id": "c"}, "lines": ["A" * 500]}]
        snip = server._blame_snippet(blame, 1, 1, width=100)
        assert len(snip.splitlines()[0]) < 150

    def test_empty(self):
        assert server._blame_snippet([], 1, 1) == ""


# ── _diff_excerpt: pick the right file's diff, truncated ──────────────────────

DIFFS = [
    {"old_path": "other.py", "new_path": "other.py", "diff": "@@ other @@"},
    {"old_path": "src/app.js", "new_path": "src/app.js", "diff": "@@ certo @@"},
]


class TestDiffExcerpt:
    def test_picks_matching_file(self):
        assert server._diff_excerpt(DIFFS, "src/app.js") == "@@ certo @@"

    def test_matches_old_path_on_rename(self):
        diffs = [{"old_path": "velho.py", "new_path": "novo.py", "diff": "@@ r @@"}]
        assert server._diff_excerpt(diffs, "velho.py") == "@@ r @@"

    def test_falls_back_to_first_diff(self):
        assert server._diff_excerpt(DIFFS, "inexistente.py") == "@@ other @@"

    def test_truncates_giant_diff(self):
        diffs = [{"new_path": "a.py", "old_path": "a.py", "diff": "x" * 10000}]
        out = server._diff_excerpt(diffs, "a.py", max_chars=100)
        assert len(out) < 200 and "diff truncado" in out

    def test_empty(self):
        assert server._diff_excerpt([], "a.py") == ""
        assert server._diff_excerpt(None, "a.py") == ""
