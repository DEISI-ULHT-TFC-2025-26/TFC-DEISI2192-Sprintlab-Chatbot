"""Tests for the repo-overview context block — the 'what is this project'
section ('fala-me do projeto' needs description/languages/structure/README,
not just issue counts)."""

import server

INFO = {
    "description": "Real-time OS for safety-critical aerospace.",
    "topics": ["arinc653", "rtos", "aerospace"],
    "default_branch": "master",
}
LANGS = {"C": 78.5, "Python": 12.0, "Makefile": 9.5}
TREE = [
    {"name": "src", "type": "tree"},
    {"name": "doc", "type": "tree"},
    {"name": "README.md", "type": "blob", "path": "README.md"},
    {"name": "Makefile", "type": "blob", "path": "Makefile"},
]


def _joined(**over):
    kw = dict(info=INFO, languages=LANGS, tree=TREE, readme="# AIR\nHypervisor.")
    kw.update(over)
    return "\n".join(server._repo_overview_lines(**kw))


# ── _find_readme ──────────────────────────────────────────────────────────────

class TestFindReadme:
    def test_finds_readme_md(self):
        assert server._find_readme(TREE) == "README.md"

    def test_case_insensitive(self):
        tree = [{"name": "readme.rst", "type": "blob", "path": "readme.rst"}]
        assert server._find_readme(tree) == "readme.rst"

    def test_ignores_dirs_named_readme(self):
        tree = [{"name": "readme", "type": "tree"}]
        assert server._find_readme(tree) is None

    def test_none_when_absent(self):
        assert server._find_readme([{"name": "main.c", "type": "blob"}]) is None
        assert server._find_readme([]) is None


# ── _repo_overview_lines ──────────────────────────────────────────────────────

class TestRepoOverviewLines:
    def test_includes_description(self):
        assert "Descrição: Real-time OS" in _joined()

    def test_includes_topics(self):
        assert "Tópicos: arinc653, rtos, aerospace" in _joined()

    def test_languages_sorted_desc_with_pct(self):
        out = _joined()
        assert "Linguagens: C 78%, Python 12%, Makefile 10%" in out

    def test_structure_splits_dirs_and_files(self):
        out = _joined()
        assert "Pastas (topo): src, doc" in out
        assert "Ficheiros (topo): README.md, Makefile" in out

    def test_readme_excerpt_included(self):
        assert "README (excerto):" in _joined()
        assert "Hypervisor." in _joined()

    def test_readme_truncated_with_ellipsis(self):
        out = "\n".join(server._repo_overview_lines(
            {}, {}, [], "X" * 5000, readme_max=100))
        assert "…" in out
        # the giant body must not pass through in full
        assert "X" * 200 not in out

    def test_empty_inputs_give_no_lines(self):
        assert server._repo_overview_lines({}, {}, [], "") == []
        assert server._repo_overview_lines(None, None, None, None) == []

    def test_tolerates_tag_list_instead_of_topics(self):
        info = {"tag_list": ["a", "b"]}
        out = "\n".join(server._repo_overview_lines(info, {}, [], ""))
        assert "Tópicos: a, b" in out

    def test_readme_sections_line_from_headings(self):
        readme = "# AIR\nAIR is a TSP RTOS.\n## Installation\nsteps\n## Examples\n..."
        out = "\n".join(server._repo_overview_lines({}, {}, [], readme))
        assert "README (secções): AIR, Installation, Examples" in out


# README that opens with badge/HTML noise before the real description — the
# exact shape that made the model mislabel AIR as a "project management system".
NOISY = """# AIR

[![pipeline](https://x/badge.svg)](https://x/pipe)
![coverage](https://x/cov.svg)

<p align="center"><img src="logo.png"></p>

AIR is a TSP RTOS hypervisor for safety-critical aerospace, ARINC 653 compliant.

---

## Installation
Clone from GitHub.

[pipeline]: https://x/pipe
"""


class TestCleanReadme:
    def test_drops_badges_and_images(self):
        out = server._clean_readme(NOISY)
        assert "badge" not in out and "cov.svg" not in out and "![" not in out

    def test_keeps_real_description(self):
        out = server._clean_readme(NOISY)
        assert "AIR is a TSP RTOS hypervisor" in out

    def test_strips_html_tags(self):
        out = server._clean_readme(NOISY)
        assert "<p" not in out and "<img" not in out

    def test_drops_reference_link_defs_and_rules(self):
        out = server._clean_readme(NOISY)
        assert "[pipeline]:" not in out and "---" not in out

    def test_description_reaches_excerpt_within_budget(self):
        # the real "what is AIR" sentence must survive in a tight budget,
        # because the badge noise above it is gone
        out = "\n".join(server._repo_overview_lines({}, {}, [], NOISY, readme_max=120))
        assert "AIR is a TSP RTOS hypervisor" in out

    def test_empty(self):
        assert server._clean_readme("") == ""
        assert server._clean_readme(None) == ""


class TestReadmeOutline:
    def test_collects_headings(self):
        assert server._readme_outline(NOISY) == ["AIR", "Installation"]

    def test_ignores_headings_in_code_fences(self):
        md = "# Real\n```bash\n# not a heading\necho hi\n```\n## AlsoReal"
        assert server._readme_outline(md) == ["Real", "AlsoReal"]

    def test_caps_at_limit(self):
        md = "\n".join(f"## H{i}" for i in range(30))
        assert len(server._readme_outline(md, limit=5)) == 5

    def test_empty(self):
        assert server._readme_outline("") == []
        assert server._readme_outline(None) == []
