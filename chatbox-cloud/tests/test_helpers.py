"""Unit tests for the pure helper functions in server.py — the input-sanitising
layer that guards every GitLab write and the model's tool calls."""

import server


# ── _norm_iid: the model sometimes sends '#5', '5 ', or junk ──────────────────

class TestNormIid:
    def test_strips_hash_and_space(self):
        assert server._norm_iid("#5") == "5"
        assert server._norm_iid(" 12 ") == "12"
        assert server._norm_iid("#  7 ") == "7"

    def test_accepts_int(self):
        assert server._norm_iid(5) == "5"

    def test_rejects_non_numeric(self):
        assert server._norm_iid("abc") == ""
        assert server._norm_iid("5x") == ""
        assert server._norm_iid("#") == ""
        assert server._norm_iid(None) == ""
        assert server._norm_iid("") == ""


# ── _clean_labels: drop placeholder labels the model invents (label1, label2) ─

class TestCleanLabels:
    def test_drops_placeholder_labels(self):
        assert server._clean_labels("bug, label1, urgent") == "bug,urgent"
        assert server._clean_labels("label1,label2") == ""

    def test_case_insensitive_placeholder(self):
        assert server._clean_labels("LABEL1, Bug") == "Bug"

    def test_keeps_real_labels_named_like_label(self):
        # only 'label' + digits is a placeholder; 'label' alone / 'labelX' are real
        assert server._clean_labels("label") == "label"
        assert server._clean_labels("labelX") == "labelX"

    def test_trims_whitespace(self):
        assert server._clean_labels(" bug , feature ") == "bug,feature"

    def test_empty_and_none(self):
        assert server._clean_labels("") == ""
        assert server._clean_labels(None) == ""


# ── _valid_due_date: only a real YYYY-MM-DD survives ──────────────────────────

class TestValidDueDate:
    def test_accepts_iso_date(self):
        assert server._valid_due_date("2026-06-11") == "2026-06-11"
        assert server._valid_due_date(" 2026-06-11 ") == "2026-06-11"

    def test_rejects_garbage(self):
        assert server._valid_due_date("not-a-date") is None
        assert server._valid_due_date("2026-13-01") is None  # month 13
        assert server._valid_due_date("11/06/2026") is None
        assert server._valid_due_date(None) is None
        assert server._valid_due_date("") is None


# ── _int_ids: coerce a list/CSV of user ids to clean ints ─────────────────────

class TestIntIds:
    def test_csv_string(self):
        assert server._int_ids("1,2,3") == [1, 2, 3]

    def test_list_input(self):
        assert server._int_ids([1, 2]) == [1, 2]
        assert server._int_ids(["5", " 6 "]) == [5, 6]

    def test_drops_non_numeric(self):
        assert server._int_ids("1, abc, 3") == [1, 3]

    def test_empty(self):
        assert server._int_ids("") == []
        assert server._int_ids(None) == []


# ── _words: regex-free tokeniser ──────────────────────────────────────────────

class TestWords:
    def test_splits_on_punctuation(self):
        assert server._words("issue #5 done") == ["issue", "5", "done"]
        assert server._words("a-b c") == ["a", "b", "c"]

    def test_keeps_accented_words_intact(self):
        assert server._words("Olá, mundo!") == ["Olá", "mundo"]

    def test_empty(self):
        assert server._words("") == []


# ── _strip_think: reasoning models prepend <think>…</think> ───────────────────

class TestStripThink:
    def test_removes_single_block(self):
        assert server._strip_think("<think>hmm</think>Olá") == "Olá"

    def test_removes_multiple_blocks(self):
        assert server._strip_think("a<think>x</think>b<think>y</think>c") == "abc"

    def test_passthrough_when_no_block(self):
        assert server._strip_think("resposta normal") == "resposta normal"

    def test_trims_result(self):
        assert server._strip_think("<think>x</think>  oi  ") == "oi"

    def test_none_and_empty(self):
        assert server._strip_think(None) == ""
        assert server._strip_think("") == ""


# ── _parse_suggestions: pull a JSON array out of a noisy model reply ──────────

class TestParseSuggestions:
    def test_clean_array(self):
        assert server._parse_suggestions('["a","b","c"]') == ["a", "b", "c"]

    def test_array_embedded_in_prose(self):
        assert server._parse_suggestions('Claro: ["x", "y"] pronto') == ["x", "y"]

    def test_caps_at_five(self):
        out = server._parse_suggestions('["1","2","3","4","5","6","7"]')
        assert len(out) == 5

    def test_truncates_long_strings_to_60(self):
        out = server._parse_suggestions('["' + "z" * 100 + '"]')
        assert len(out[0]) == 60

    def test_filters_non_strings(self):
        assert server._parse_suggestions('[1, "ok", null, "go"]') == ["ok", "go"]

    def test_invalid_returns_empty(self):
        assert server._parse_suggestions("sem array nenhum") == []
        assert server._parse_suggestions("") == []


# ── _pick_model: only allow-listed models pass; unknown → env default ─────────

class TestPickModel:
    def test_known_model_returns_configured_effort(self):
        assert server._pick_model("llama-3.3-70b-versatile") == ("llama-3.3-70b-versatile", "")
        assert server._pick_model("qwen/qwen3-32b") == ("qwen/qwen3-32b", "none")

    def test_unknown_falls_back_to_default(self):
        assert server._pick_model("evil/model") == (server.GROQ_MODEL, server.GROQ_REASONING_EFFORT)


# ── _action_summary: human text on the confirm card ───────────────────────────

class TestActionSummary:
    def test_create(self):
        out = server._action_summary("create_issue", {"title": "Bug no login"})
        assert "Criar uma issue" in out and "Bug no login" in out

    def test_close(self):
        assert server._action_summary("close_issue", {"iid": 5}) == "Fechar a issue #5"

    def test_delete_is_marked_irreversible(self):
        out = server._action_summary("delete_issue", {"iid": 9})
        assert "APAGAR" in out and "irreversível" in out and "#9" in out

    def test_update(self):
        out = server._action_summary("update_issue", {"iid": 3, "title": "Novo"})
        assert "Atualizar a issue #3" in out and "Novo" in out

    def test_unknown_tool(self):
        assert server._action_summary("foo", {}) == "Ação: foo"
