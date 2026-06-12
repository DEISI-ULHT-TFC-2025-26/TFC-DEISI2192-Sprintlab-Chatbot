"""Integration tests: real HTTP server (in a thread) + fake GitLab/Groq.

The fakes replace the two lowest-level network functions — gitlab_api's
_gitlab_request and llm's _groq_complete — so every route is exercised through
the REAL handler/fetcher/cache/aggregation code with zero network access.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

import server
import gitlab_api
import blame as blame_mod
import code_commit as cc_mod

PID = "80767095"  # default project id (env not set in tests)

# ── Fake GitLab dataset ───────────────────────────────────────────────────────

PROJECT = {
    "id": 80767095, "name_with_namespace": "Bernardo / SprintLab",
    "name": "SprintLab", "web_url": "https://gitlab.com/b/sprintlab",
    "default_branch": "main", "description": "Chatbox IA para GitLab",
    "topics": ["ai"], "statistics": {"commit_count": 2318},
    "open_issues_count": 1,
}
ISSUES = [
    {"iid": 5, "title": "Bug no login", "state": "opened",
     "assignee": {"name": "Ana"}, "labels": ["bug"],
     "due_date": None, "created_at": "2026-06-01T10:00:00Z",
     "closed_at": None, "web_url": "https://gitlab.com/b/sprintlab/-/issues/5"},
    {"iid": 6, "title": "Feita", "state": "closed",
     "assignee": None, "labels": [],
     "due_date": None, "created_at": "2026-05-01T10:00:00Z",
     "closed_at": "2026-05-02T10:00:00Z",
     "web_url": "https://gitlab.com/b/sprintlab/-/issues/6"},
]
COMMITS = [
    {"id": "abc123def456", "short_id": "abc123de", "author_name": "Ana",
     "author_email": "ana@x.pt", "created_at": "2026-06-01T09:00:00Z",
     "authored_date": "2026-06-01T09:00:00Z", "title": "Corrige login"},
]
CONTRIBS = [{"name": "Ana", "commits": 1500}, {"name": "Rui", "commits": 818}]
MRS = [{"state": "opened", "author": {"name": "Ana"}},
       {"state": "merged", "author": {"name": "Rui"}}]
MILESTONES = [{"id": 1, "title": "Sprint 1", "due_date": "2026-07-01"}]
LABELS = [{"name": "bug", "color": "#f00"}]
MEMBERS = [{"id": 1, "name": "Ana"}]
TREE = [{"name": "src", "type": "tree", "path": "src"},
        {"name": "README.md", "type": "blob", "path": "README.md"}]

CALLS = []  # (method, endpoint) de cada chamada "GitLab" feita pelos handlers


def fake_gitlab(method, endpoint, body=None, params=None, return_headers=False):
    CALLS.append((method, endpoint))
    params = params or {}

    def ret(payload):
        # headers fake: dict vazio — .get("X-Next-Page") → None (sem mais páginas)
        return (payload, {}) if return_headers else payload

    # Escritas do commit por IA (antes dos matchers GET genéricos)
    if method == "POST" and endpoint.endswith("/repository/branches"):
        return ret({"name": (body or {}).get("branch")})
    if method == "POST" and endpoint.endswith("/repository/commits"):
        return ret({"id": "fffeeeddd000", "short_id": "fffeeedd",
                    "web_url": "https://gitlab.com/b/sprintlab/-/commit/fffeeeddd000"})
    if method == "POST" and endpoint.endswith("/merge_requests"):
        return ret({"iid": 7,
                    "web_url": "https://gitlab.com/b/sprintlab/-/merge_requests/7"})

    if endpoint.endswith("/issues") and method == "GET":
        state = params.get("state", "all")
        rows = [i for i in ISSUES if state in ("all", i["state"])]
        return ret(rows)
    if "/issues/" in endpoint:
        iid = endpoint.rstrip("/").split("/")[-1]
        if method in ("PUT", "POST", "DELETE"):
            return ret({"iid": int(iid), "title": "Bug no login",
                        "state": "closed", "web_url": ISSUES[0]["web_url"]})
        return ret(dict(ISSUES[0], assignees=[{"id": 1, "name": "Ana"}]))
    if endpoint.endswith("/milestones"):
        return ret(MILESTONES)
    if endpoint.endswith("/repository/contributors"):
        return ret(CONTRIBS)
    if endpoint.endswith("/repository/commits"):
        return ret(COMMITS)  # serve p/ janela, export e histórico de ficheiro
    if endpoint.endswith("/merge_requests"):
        return ret(MRS)
    if endpoint.endswith("/languages"):
        return ret({"Python": 100.0})
    if endpoint.endswith("/repository/tree"):
        return ret(TREE)
    if "/repository/files/" in endpoint and endpoint.endswith("/blame"):
        return ret([{"commit": COMMITS[0], "lines": ["linha de código"] * 41}])
    if "/repository/files/" in endpoint:
        return ret({"content": "IyBTcHJpbnRMYWIK", "encoding": "base64"})  # "# SprintLab"
    if endpoint.endswith("/labels"):
        return ret(LABELS)
    if endpoint.endswith("/members/all"):
        return ret(MEMBERS)
    if endpoint.rstrip("/").endswith(f"/projects/{PID}") or "/projects/999" in endpoint:
        return ret(PROJECT)
    return ret({})


def fake_groq(payload):
    return {"choices": [{"message": {"content": '["Sugestão A", "Sugestão B"]'}}]}


# ── Server fixture ────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def base_url():
    srv = server.ThreadingServer(("127.0.0.1", 0), server.Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    """Patch the two network roots everywhere they're referenced, reset state."""
    CALLS.clear()
    gitlab_api.cache.invalidate()
    server.rate_limiter._hits.clear()
    # _gitlab_request: definido em gitlab_api (fetchers) e importado por
    # from-import em server e code_commit — patch nos três namespaces.
    monkeypatch.setattr(gitlab_api, "_gitlab_request", fake_gitlab)
    monkeypatch.setattr(server, "_gitlab_request", fake_gitlab)
    monkeypatch.setattr(cc_mod, "_gitlab_request", fake_gitlab)
    # _groq_complete: usado por from-import em server, blame e code_commit
    monkeypatch.setattr(server, "_groq_complete", fake_groq)
    monkeypatch.setattr(blame_mod, "_groq_complete", fake_groq)
    monkeypatch.setattr(cc_mod, "_groq_complete", fake_groq)
    yield


def get(base, path, headers=None):
    req = urllib.request.Request(base + path, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, dict(r.headers), r.read()


def post(base, path, payload, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                 headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, dict(r.headers), r.read()


# ── Static / misc ─────────────────────────────────────────────────────────────

class TestStatic:
    def test_index_serves_html(self, base_url):
        status, headers, body = get(base_url, "/")
        assert status == 200
        assert "text/html" in headers["Content-Type"]
        assert b"chatbox" in body.lower()

    def test_css_and_js(self, base_url):
        assert get(base_url, "/style.css")[0] == 200
        assert get(base_url, "/app.js")[0] == 200

    def test_healthz(self, base_url):
        _, _, body = get(base_url, "/healthz")
        assert json.loads(body)["ok"] is True

    def test_unknown_route_404(self, base_url):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(base_url, "/nao-existe")
        assert e.value.code == 404


# ── GitLab read endpoints ─────────────────────────────────────────────────────

class TestGitlabReads:
    def test_stats_structure(self, base_url):
        _, _, body = get(base_url, "/gitlab/stats")
        d = json.loads(body)
        assert d["project"]["commit_count"] == 2318
        assert d["issues"]["open"] == 1 and d["issues"]["closed"] == 1
        assert d["contributors"]["total"] == 2318  # 1500 + 818
        assert d["mrs"] == {"open": 1, "total": 2}

    def test_report_has_markdown(self, base_url):
        _, _, body = get(base_url, "/gitlab/report")
        d = json.loads(body)
        assert d["repo_commits"] == 2318
        assert "Relatório do projeto" in d["markdown"]

    def test_export_issues_csv(self, base_url):
        _, headers, body = get(base_url, "/gitlab/export")
        assert "gitlab_issues.csv" in headers["Content-Disposition"]
        assert body.startswith(b"\xef\xbb\xbf")  # BOM p/ Excel
        assert "Bug no login" in body.decode("utf-8-sig")

    def test_export_commits_csv(self, base_url):
        _, headers, body = get(base_url, "/gitlab/export?type=commits")
        assert "gitlab_commits.csv" in headers["Content-Disposition"]
        assert "Corrige login" in body.decode("utf-8-sig")

    def test_chart_state_pie(self, base_url):
        _, _, body = get(base_url, "/gitlab/chart/state-pie")
        d = json.loads(body)
        assert d["labels"] == ["Abertas", "Fechadas"]
        assert d["datasets"][0]["data"] == [1, 1]

    def test_chart_unknown_404(self, base_url):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(base_url, "/gitlab/chart/inventado")
        assert e.value.code == 404

    def test_labels_milestones_members(self, base_url):
        assert json.loads(get(base_url, "/gitlab/labels")[2])["labels"][0]["name"] == "bug"
        assert json.loads(get(base_url, "/gitlab/milestones")[2])["milestones"][0]["title"] == "Sprint 1"
        assert json.loads(get(base_url, "/gitlab/members")[2])["members"][0]["name"] == "Ana"

    def test_issue_get_and_duplicate(self, base_url):
        d = json.loads(get(base_url, "/gitlab/issue/5")[2])
        assert d["iid"] == 5 and d["assignee_name"] == "Ana"
        m = json.loads(get(base_url, "/gitlab/duplicate?title=bug%20no%20login")[2])
        assert m["matches"] and m["matches"][0]["iid"] == 5

    def test_gl_test_ok(self, base_url):
        d = json.loads(get(base_url, "/gitlab/test")[2])
        assert d["ok"] is True and "SprintLab" in d["name"]


# ── Multi-tenant ──────────────────────────────────────────────────────────────

class TestMultiTenant:
    def test_x_gl_project_header_changes_target(self, base_url):
        get(base_url, "/gitlab/test", headers={"X-GL-Project": "999"})
        assert any("/projects/999" in ep for _, ep in CALLS)

    def test_default_project_used_without_header(self, base_url):
        get(base_url, "/gitlab/test")
        assert any(f"/projects/{PID}" in ep for _, ep in CALLS)


# ── Writes (confirm-action) ───────────────────────────────────────────────────

class TestConfirmAction:
    def test_invalid_tool_400(self, base_url):
        with pytest.raises(urllib.error.HTTPError) as e:
            post(base_url, "/api/confirm-action", {"tool": "drop_database"})
        assert e.value.code == 400

    def test_close_issue_hits_gitlab_put(self, base_url):
        _, _, body = post(base_url, "/api/confirm-action",
                          {"tool": "close_issue", "args": {"iid": "#5"}})
        d = json.loads(body)
        assert d["ok"] is True and d["action"] == "close"
        assert ("PUT", f"/projects/{PID}/issues/5") in CALLS

    def test_create_requires_title(self, base_url):
        _, _, body = post(base_url, "/api/confirm-action",
                          {"tool": "create_issue", "args": {"title": "  "}})
        assert json.loads(body)["ok"] is False


# ── LLM endpoints (Groq fake) ─────────────────────────────────────────────────

class TestLlmEndpoints:
    def test_suggestions_parses_model_array(self, base_url):
        _, _, body = post(base_url, "/api/suggestions", {"messages": []})
        assert json.loads(body)["suggestions"] == ["Sugestão A", "Sugestão B"]

    def test_generate_description_empty_title(self, base_url):
        _, _, body = post(base_url, "/api/generate-description", {"title": ""})
        assert json.loads(body)["description"] == ""

    def test_analyze_code_requires_file(self, base_url):
        _, _, body = post(base_url, "/api/analyze-code", {})
        d = json.loads(body)
        assert d["ok"] is False and "ficheiro" in d["error"].lower()

    def test_analyze_code_full_flow(self, base_url):
        _, _, body = post(base_url, "/api/analyze-code",
                          {"file": "src/app.py", "line": 5})
        d = json.loads(body)
        assert d["ok"] is True
        assert d["facts"]["author"] == "Ana"          # facto (blame fake)
        assert d["history"] and d["authors"][0]["name"] == "Ana"

    def test_chat_streams_sse(self, base_url, monkeypatch):
        def fake_stream(self, payload):
            self._emit_done("resposta de teste")
            return None
        monkeypatch.setattr(server.Handler, "_stream_pass1", fake_stream)
        status, headers, body = post(base_url, "/api/chat",
                                     {"messages": [{"role": "user", "content": "olá"}]})
        assert status == 200
        assert "text/event-stream" in headers["Content-Type"]
        assert b"resposta de teste" in body


# ── Commit por IA ─────────────────────────────────────────────────────────────

class TestAiCommit:
    def test_generate_returns_validated_plan(self, base_url, monkeypatch):
        plan_json = json.dumps({
            "branch_slug": "Valida Emails", "commit_message": "Adiciona validador",
            "summary": "ok",
            "files": [{"path": "src/util.py", "content": "x = 1\n"}],
        })
        monkeypatch.setattr(cc_mod, "_groq_complete",
                            lambda p: {"choices": [{"message": {"content": plan_json}}]})
        _, _, body = post(base_url, "/api/generate-commit",
                          {"request": "cria um validador de emails e faz commit"})
        d = json.loads(body)
        assert d["ok"] is True
        assert d["plan"]["branch"] == "ai/valida-emails"
        assert d["plan"]["files"][0]["path"] == "src/util.py"
        # passo 1 NÃO escreve nada no GitLab
        assert not any(m == "POST" for m, _ in CALLS)

    def test_generate_requires_request_text(self, base_url):
        _, _, body = post(base_url, "/api/generate-commit", {"request": "oi"})
        assert json.loads(body)["ok"] is False

    def test_confirm_creates_branch_commit_and_mr(self, base_url):
        plan = {"branch": "ai/teste", "commit_message": "Teste",
                "summary": "", "files": [{"path": "exemplo.py",
                                          "content": "print('olá')\n"}]}
        _, _, body = post(base_url, "/api/confirm-commit", {"plan": plan})
        d = json.loads(body)
        assert d["ok"] is True
        assert d["branch"] == "ai/teste"
        assert d["commit_sha"] == "fffeeedd"
        assert d["mr_iid"] == 7 and "merge_requests/7" in d["mr_url"]
        assert ("POST", f"/projects/{PID}/repository/branches") in CALLS
        assert ("POST", f"/projects/{PID}/repository/commits") in CALLS
        assert ("POST", f"/projects/{PID}/merge_requests") in CALLS

    def test_confirm_rejects_path_traversal(self, base_url):
        plan = {"branch": "ai/mau", "commit_message": "x",
                "files": [{"path": "../../etc/passwd", "content": "pwned"}]}
        _, _, body = post(base_url, "/api/confirm-commit", {"plan": plan})
        d = json.loads(body)
        assert d["ok"] is False
        # nada foi escrito
        assert not any(m == "POST" for m, _ in CALLS)

    def test_confirm_rejects_empty_plan(self, base_url):
        _, _, body = post(base_url, "/api/confirm-commit", {"plan": {}})
        assert json.loads(body)["ok"] is False


# ── Rate limiting ─────────────────────────────────────────────────────────────

class TestRateLimit:
    def test_blocks_after_limit_and_degrades_gracefully(self, base_url, monkeypatch):
        monkeypatch.setattr(server.rate_limiter, "per_minute", 1)
        # 1.º pedido passa (erro de validação ≠ erro de limite)
        _, _, b1 = post(base_url, "/api/analyze-code", {})
        assert "ficheiro" in json.loads(b1)["error"].lower()
        # 2.º é bloqueado, mas a resposta continua 200 + JSON utilizável
        _, _, b2 = post(base_url, "/api/analyze-code", {})
        assert "Limite de pedidos" in json.loads(b2)["error"]

    def test_zero_disables(self, base_url, monkeypatch):
        monkeypatch.setattr(server.rate_limiter, "per_minute", 0)
        for _ in range(3):
            _, _, body = post(base_url, "/api/analyze-code", {})
            assert "ficheiro" in json.loads(body)["error"].lower()
