"""Tests for the AI-commit feature (code_commit.py) — the pure layer.

The plan validation is the safety net between the LLM's JSON and the GitLab
write: bad paths, oversized content and malformed plans must die HERE."""

import json

import code_commit as cc
from llm import _parse_json_obj


# ── _slugify: branch-safe names ───────────────────────────────────────────────

class TestSlugify:
    def test_basic(self):
        assert cc._slugify("Valida Emails") == "valida-emails"

    def test_collapses_symbols(self):
        assert cc._slugify("a!!b__c  d") == "a-b-c-d"

    def test_only_safe_charset(self):
        out = cc._slugify("ação rápida: criar função!")
        assert out and all(ch.isascii() and (ch.isalnum() or ch == "-") for ch in out)

    def test_caps_length(self):
        assert len(cc._slugify("x" * 100)) <= 32

    def test_empty_falls_back(self):
        assert cc._slugify("") == "alteracao"
        assert cc._slugify("!!!") == "alteracao"


# ── _validate_files: the write guard ──────────────────────────────────────────

def _ok_file(path="src/novo.py", content="print('olá')\n"):
    return {"path": path, "content": content}


class TestValidateFiles:
    def test_accepts_and_normalises(self):
        files, err = cc._validate_files([_ok_file("./src/a.py")])
        assert err is None
        assert files[0]["path"] == "src/a.py"

    def test_rejects_traversal(self):
        _, err = cc._validate_files([_ok_file("../../etc/passwd")])
        assert err and "não permitido" in err

    def test_rejects_absolute(self):
        _, err = cc._validate_files([_ok_file("/etc/passwd")])
        assert err

    def test_rejects_git_dir(self):
        _, err = cc._validate_files([_ok_file(".git/hooks/pre-commit")])
        assert err and ".git" in err

    def test_rejects_empty_content(self):
        _, err = cc._validate_files([_ok_file(content="   ")])
        assert err and "vazio" in err.lower()

    def test_rejects_oversized(self):
        _, err = cc._validate_files([_ok_file(content="x" * (cc.MAX_CONTENT + 1))])
        assert err and "grande" in err

    def test_rejects_too_many(self):
        _, err = cc._validate_files([_ok_file(f"f{i}.py") for i in range(cc.MAX_FILES + 1)])
        assert err and str(cc.MAX_FILES) in err

    def test_rejects_duplicates(self):
        _, err = cc._validate_files([_ok_file("a.py"), _ok_file("a.py")])
        assert err and "duplicado" in err.lower()

    def test_rejects_non_list(self):
        assert cc._validate_files(None)[1]
        assert cc._validate_files([])[1]
        assert cc._validate_files(["str"])[1]


# ── plan_from_llm_text: model JSON → validated plan ───────────────────────────

PLAN_JSON = json.dumps({
    "branch_slug": "Valida Emails",
    "commit_message": "Adiciona validador de emails",
    "summary": "Criei um validador simples.",
    "files": [{"path": "src/validador.py", "content": "def v(e):\n    return '@' in e\n"}],
})


class TestPlanFromText:
    def test_valid_json(self):
        plan, err = cc.plan_from_llm_text(PLAN_JSON)
        assert err is None
        assert plan["branch"] == "ai/valida-emails"
        assert plan["commit_message"] == "Adiciona validador de emails"
        assert plan["files"][0]["path"] == "src/validador.py"

    def test_tolerates_fences_and_prose(self):
        plan, err = cc.plan_from_llm_text(f"Aqui está:\n```json\n{PLAN_JSON}\n```\nEspero que ajude!")
        assert err is None and plan["branch"] == "ai/valida-emails"

    def test_invalid_json_errors(self):
        plan, err = cc.plan_from_llm_text("não sei fazer isso")
        assert plan is None and "JSON" in err

    def test_bad_files_propagate_error(self):
        bad = json.dumps({"branch_slug": "x", "commit_message": "y",
                          "files": [{"path": "../mau.py", "content": "x"}]})
        plan, err = cc.plan_from_llm_text(bad)
        assert plan is None and err

    def test_missing_message_gets_default(self):
        txt = json.dumps({"files": [{"path": "a.py", "content": "x = 1"}]})
        plan, err = cc.plan_from_llm_text(txt)
        assert err is None
        assert plan["commit_message"]            # default aplicado
        assert plan["branch"].startswith("ai/")

    def test_caps_message_length(self):
        txt = json.dumps({"commit_message": "m" * 500,
                          "files": [{"path": "a.py", "content": "x"}]})
        plan, _ = cc.plan_from_llm_text(txt)
        assert len(plan["commit_message"]) <= 120


# ── _parse_json_obj (llm.py) ──────────────────────────────────────────────────

class TestParseJsonObj:
    def test_clean_object(self):
        assert _parse_json_obj('{"a": 1}') == {"a": 1}

    def test_with_think_block_and_prose(self):
        assert _parse_json_obj('<think>hmm</think>Claro: {"a": 1} pronto') == {"a": 1}

    def test_non_object_returns_none(self):
        assert _parse_json_obj('[1, 2]') is None
        assert _parse_json_obj("nada") is None
        assert _parse_json_obj("") is None
