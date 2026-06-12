"""
SprintLab TFC Chatbox — cloud edition (Groq + GitLab, corre num HF Space).

Ponto de entrada HTTP (stdlib-only, sem dependências externas): serve o
frontend, expõe a API do chatbox e faz o streaming SSE do chat. A lógica
vive em módulos com responsabilidades claras:

  config.py      configuração env-driven (fail-fast) + prompts
  gitlab_api.py  cliente GitLab (cache TTL, multi-tenant, fetchers)
  analytics.py   estatísticas, contexto do LLM, exports CSV
  report.py      relatório do projeto (determinístico)
  charts.py      dados Chart.js para os gráficos inline
  blame.py       investigação de código (git blame + diff + análise IA)
  actions.py     escritas no GitLab (validação + executor único)
  llm.py         cliente Groq (default llama-3.3-70b-versatile, env-driven)

Pontos-chave: ThreadingHTTPServer (um pedido lento não bloqueia os outros);
abort do stream quando o cliente desliga; rate-limit por IP nos endpoints
que consomem Groq (proteção da quota grátis); CSV com BOM utf-8-sig.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from config import (CACHE_TTL, DOCUMENT_CONTEXT, GITLAB_BASE,
                    GITLAB_PAGE_LIMIT, GITLAB_PROJECT_ID, GITLAB_TOKEN,
                    GROQ_API_KEY, GROQ_MODEL, GROQ_REASONING_EFFORT,
                    GROQ_UA, GROQ_URL, PORT, PUBLIC_URL, RATE_LIMIT,
                    SPRINTLAB_PROJECT_ID, SYSTEM_PROMPT)
from gitlab_api import (_ck, _ctx, _get_all_commits, _get_blame,
                        _get_commit_diff, _get_commits, _get_contributors,
                        _get_file_history, _get_languages, _get_merge_requests,
                        _get_repo_tree_top, _gitlab_paginate, _gitlab_request,
                        _gl, _run_with_ctx, cache, get_all_issues,
                        get_milestones, get_project_info, gitlab_pool)
from analytics import (_issue_stats, commits_to_csv, get_gitlab_context,
                       issues_to_csv)
from report import build_sprint_report
from charts import CHART_HANDLERS
from code_commit import execute_commit_plan, generate_commit_plan
from blame import (_analyze_code_llm, _blame_owner, _blame_snippet,
                   _diff_excerpt, _path_candidates)
from actions import (ISSUE_TOOLS, _action_summary, _words,
                     execute_issue_tool)
from llm import (DEFAULT_SUGGESTIONS, _groq_complete, _parse_suggestions,
                 _pick_model, _strip_think)

# Re-exports: os testes unitários (e código antigo) importam tudo via
# `server.<nome>` — manter estes nomes aqui preserva essa interface.
from gitlab_api import _find_readme  # noqa: F401
from analytics import (_clean_readme, _readme_outline,  # noqa: F401
                       _repo_overview_lines, _strip_html_tags)
from report import _due_status  # noqa: F401
from actions import (_clean_labels, _int_ids, _norm_iid,  # noqa: F401
                     _valid_due_date)
from config import GROQ_MODELS  # noqa: F401

log = logging.getLogger("sprintlab")


# ── Rate limiting (ops, não auth) ─────────────────────────────────────────────
# A quota grátis do Groq é partilhada por todos os utilizadores do Space; um
# único cliente em loop podia esgotá-la e "matar" a demo para os restantes.
# Janela deslizante de 60s por IP, só nos endpoints que consomem Groq.


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute  # 0 = desligado
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, ip: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.time()
        with self._lock:
            dq = self._hits.setdefault(ip, deque())
            while dq and now - dq[0] > 60:
                dq.popleft()
            if len(dq) >= self.per_minute:
                return False
            dq.append(now)
            # housekeeping: esquecer IPs sem atividade há >5 min
            stale = [k for k, v in self._hits.items()
                     if k != ip and (not v or now - v[-1] > 300)]
            for k in stale:
                del self._hits[k]
            return True


rate_limiter = RateLimiter(RATE_LIMIT)
RATE_MSG = ("Limite de pedidos atingido (proteção da quota grátis do Groq) — "
            "espera um momento e tenta de novo.")

# Endpoints que fazem chamadas ao Groq — os únicos que vale a pena limitar.
GROQ_ENDPOINTS = {"/api/chat", "/api/suggestions",
                  "/api/generate-description", "/api/analyze-code",
                  "/api/generate-commit"}


# ── HTTP handler ──────────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    server_version = "SprintLabChatbox/1.1"

    def log_message(self, fmt, *args):
        # Funnel into the structured logger; default goes to stderr unconditionally.
        log.debug("%s - %s", self.address_string(), fmt % args)

    # ---- helpers -------------------------------------------------------------

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        # No X-Frame-Options: 'ALLOWALL' is invalid syntax — some browsers
        # treat invalid values as DENY, which blocks the Teams iframe.
        # CSP frame-ancestors is the modern, spec-compliant way.
        self.send_header(
            "Content-Security-Policy",
            "frame-ancestors 'self' "
            "https://teams.microsoft.com "
            "https://*.teams.microsoft.com "
            "https://teams.cloud.microsoft "
            "https://*.cloud.microsoft "
            "https://*.office.com "
            "https://*.microsoft.com "
            "https://*.skype.com",
        )

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, msg, status=500):
        self._json({"error": str(msg)}, status=status)

    def _safe_write(self, chunk: bytes) -> bool:
        """Return False if the socket is dead — caller should bail out."""
        try:
            self.wfile.write(chunk)
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    def _client_ip(self) -> str:
        # HF Spaces fica atrás de um proxy — o IP real vem no X-Forwarded-For.
        fwd = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return fwd or self.client_address[0]

    def _rate_limited(self):
        """Resposta de limite por endpoint — sempre graciosa, o frontend nunca
        parte: o chat mostra a mensagem, as sugestões caem nos defaults, a
        descrição fica vazia e o analyze devolve um erro legível."""
        log.warning("rate limited: %s %s", self._client_ip(), self.path)
        if self.path == "/api/chat":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self._cors()
            self.end_headers()
            return self._emit_done(RATE_MSG)
        if self.path == "/api/suggestions":
            return self._json({"suggestions": DEFAULT_SUGGESTIONS})
        if self.path == "/api/generate-description":
            return self._json({"description": ""})
        return self._json({"ok": False, "error": RATE_MSG})

    # ---- verbs ---------------------------------------------------------------

    def _set_ctx(self):
        """Read per-request GitLab config from headers (falls back to env)."""
        base = (self.headers.get("X-GL-Base") or "").strip()
        token = (self.headers.get("X-GL-Token") or "").strip()
        project = (self.headers.get("X-GL-Project") or "").strip()
        if base:
            base = base.rstrip("/")
            if not base.endswith("/api/v4"):
                base += "/api/v4"
        _ctx.gl = {
            "base": base or GITLAB_BASE,
            "token": token or GITLAB_TOKEN,
            "project": project or GITLAB_PROJECT_ID,
        }

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        self._set_ctx()
        if self.path in ("/", "/chatbox.html"):
            return self._serve_static("chatbox.html", "text/html; charset=utf-8")
        if self.path == "/style.css":
            return self._serve_static("style.css", "text/css; charset=utf-8")
        if self.path == "/app.js":
            return self._serve_static("app.js", "application/javascript; charset=utf-8")
        if self.path == "/healthz":
            return self._json({"ok": True, "ts": int(time.time())})
        if self.path.startswith("/gitlab/export"):
            return self._handle_export()
        if self.path.startswith("/gitlab/chart/"):
            return self._handle_chart()
        if self.path == "/gitlab/labels":
            return self._handle_labels()
        if self.path == "/gitlab/milestones":
            return self._handle_milestones()
        if self.path == "/gitlab/members":
            return self._handle_members()
        if self.path.startswith("/gitlab/duplicate"):
            return self._handle_duplicate()
        if self.path.startswith("/gitlab/issue/"):
            return self._handle_issue_get()
        if self.path == "/gitlab/test":
            return self._handle_gl_test()
        if self.path == "/gitlab/stats":
            return self._handle_stats()
        if self.path == "/gitlab/report":
            return self._handle_report()
        self.send_response(404)
        self._cors()
        self.end_headers()

    def do_POST(self):
        self._set_ctx()
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        # Quota Groq partilhada: limitar por IP os endpoints que chamam o LLM.
        if self.path in GROQ_ENDPOINTS and not rate_limiter.allow(self._client_ip()):
            return self._rate_limited()

        # NOTE: all issue writes go through /api/confirm-action -> execute_issue_tool
        # (the single validated path). The old /gitlab/issues[/.../close|update]
        # routes were unvalidated and are removed.
        if self.path == "/api/chat":
            return self._handle_chat(body)
        if self.path == "/api/confirm-action":
            return self._handle_confirm_action(body)
        if self.path == "/api/generate-description":
            return self._handle_generate_description(body)
        if self.path == "/api/analyze-code":
            return self._handle_analyze_code(body)
        if self.path == "/api/generate-commit":
            return self._handle_generate_commit(body)
        if self.path == "/api/confirm-commit":
            return self._handle_confirm_commit(body)
        if self.path == "/api/suggestions":
            return self._handle_suggestions(body)

        self.send_response(404)
        self._cors()
        self.end_headers()

    # ---- route impls ---------------------------------------------------------

    def _serve_static(self, fname, ctype):
        try:
            with open(fname, "rb") as f:
                content = f.read()
        except FileNotFoundError:
            self.send_response(404)
            self._cors()
            self.end_headers()
            self.wfile.write(b"not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        # Don't let the browser serve a stale chatbox.html/style.css/app.js —
        # the assets change often during development.
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self._cors()
        self.end_headers()
        self.wfile.write(content)

    def _handle_export(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        kind = params.get("type", ["issues"])[0]
        try:
            if kind == "commits":
                rows = _get_all_commits()
                data = commits_to_csv(rows)
                fname = "gitlab_commits.csv"
                log.info("export csv type=commits rows=%d", len(rows))
            else:
                state = params.get("state", ["all"])[0]
                rows = get_all_issues(state)
                data = issues_to_csv(rows)
                fname = "gitlab_issues.csv"
                log.info("export csv state=%s rows=%d", state, len(rows))
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            log.exception("export failed")
            self._error(e)

    def _handle_chart(self):
        parsed = urllib.parse.urlparse(self.path)
        name = parsed.path[len("/gitlab/chart/"):]
        handler = CHART_HANDLERS.get(name)
        if not handler:
            return self._error(f"unknown chart '{name}'", status=404)
        kwargs = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        try:
            data = handler(**kwargs)
            log.info("chart %s rendered", name)
            self._json(data)
        except Exception as e:
            log.exception("chart %s failed", name)
            self._error(str(e))

    def _handle_labels(self):
        try:
            labels = cache.get_or_set(
                _ck("labels:all"),
                lambda: _gitlab_paginate(
                    f"/projects/{_gl()['project']}/labels", {"per_page": 100}
                ),
            )
            out = [{"name": l.get("name"), "color": l.get("color")}
                   for l in labels if l.get("name")]
            self._json({"labels": out})
        except Exception as e:
            log.warning("labels failed: %s", e)
            self._json({"labels": []})

    def _handle_milestones(self):
        # ALL milestones (active + closed), not just active — so the edit form's
        # dropdown always contains the issue's current milestone and can't fall
        # back to "" (which would silently unassign it on save).
        try:
            ms = cache.get_or_set(
                _ck("milestones:list-all"),
                lambda: _gitlab_paginate(
                    f"/projects/{_gl()['project']}/milestones", {"per_page": 100}
                ),
            )
            out = [{"id": m.get("id"), "title": m.get("title"),
                    "due_date": m.get("due_date")} for m in ms if m.get("id")]
            self._json({"milestones": out})
        except Exception as e:
            log.warning("milestones failed: %s", e)
            self._json({"milestones": []})

    def _handle_stats(self):
        """Project stats for the right-hand panel: issues + commits + milestones
        + MRs. Each section is fetched in parallel and tolerated independently —
        a project with no MRs/commits still gets its issue stats."""
        cfg = _gl()

        def _safe(fn, *args):
            try:
                return _run_with_ctx(cfg, fn, *args)
            except Exception as e:
                log.warning("stats section failed (%s): %s",
                            getattr(fn, "__name__", "?"), e)
                return None

        f_info = gitlab_pool.submit(_safe, get_project_info)
        f_issues = gitlab_pool.submit(_safe, get_all_issues, "all")
        f_commits = gitlab_pool.submit(_safe, _get_commits, 90)
        f_contrib = gitlab_pool.submit(_safe, _get_contributors)
        f_ms = gitlab_pool.submit(_safe, get_milestones)
        f_mrs = gitlab_pool.submit(_safe, _get_merge_requests)
        info, issues = f_info.result(), f_issues.result()
        commits, ms, mrs = f_commits.result(), f_ms.result(), f_mrs.result()
        contribs = f_contrib.result()

        out = {"project": None, "issues": None, "commits": None,
               "contributors": None, "milestones": None, "mrs": None}
        if info:
            out["project"] = {
                "name": info.get("name_with_namespace") or info.get("name") or "?",
                "web_url": info.get("web_url"),
                "commit_count": (info.get("statistics") or {}).get("commit_count"),
            }
        if issues is not None:
            s = _issue_stats(issues)
            out["issues"] = {
                "open": len(s["opened"]), "closed": len(s["closed"]),
                "total": len(issues), "progress": s["progress"],
                "overdue": len(s["overdue"]), "no_assignee": s["no_assignee"],
            }
        if commits is not None:
            by = Counter((c.get("author_name") or "?") for c in commits)
            out["commits"] = {
                "last90d": len(commits), "authors": len(by),
                "top": [{"name": n, "count": c} for n, c in by.most_common(5)],
            }
        if contribs is not None:
            out["contributors"] = {
                "authors": len(contribs),
                "total": sum(c.get("commits", 0) for c in contribs),
                "top": [{"name": c.get("name") or "?", "commits": c.get("commits", 0)}
                        for c in contribs[:5]],
            }
        if ms is not None:
            out["milestones"] = [
                {"title": m.get("title"), "due_date": m.get("due_date")}
                for m in ms[:5]
            ]
        if mrs is not None:
            out["mrs"] = {
                "open": sum(1 for m in mrs if m.get("state") == "opened"),
                "total": len(mrs),
            }
        self._json(out)

    def _handle_report(self):
        """Sprint report ('Relatórios automáticos' feature). Gathers the same
        data as /gitlab/stats — in parallel, each section tolerated — then hands
        it to the pure build_sprint_report(). Returns {..., markdown}."""
        cfg = _gl()

        def _safe(fn, *args):
            try:
                return _run_with_ctx(cfg, fn, *args)
            except Exception as e:
                log.warning("report section failed (%s): %s",
                            getattr(fn, "__name__", "?"), e)
                return None

        f_info = gitlab_pool.submit(_safe, get_project_info)
        f_issues = gitlab_pool.submit(_safe, get_all_issues, "all")
        f_commits = gitlab_pool.submit(_safe, _get_commits, 90)
        f_contrib = gitlab_pool.submit(_safe, _get_contributors)
        f_ms = gitlab_pool.submit(_safe, get_milestones)
        f_mrs = gitlab_pool.submit(_safe, _get_merge_requests)
        f_langs = gitlab_pool.submit(_safe, _get_languages)  # secção "Sobre"
        info = f_info.result()
        ref = (info or {}).get("default_branch") or "main"
        tree = _safe(_get_repo_tree_top, ref)             # estrutura p/ "Sobre"
        report = build_sprint_report(
            info, f_issues.result(), f_commits.result(),
            f_contrib.result(), f_ms.result(), f_mrs.result(),
            languages=f_langs.result(), tree=tree,
        )
        self._json(report)

    def _handle_gl_test(self):
        """Validate the current GitLab config (URL/token/project) for the
        settings panel's 'Test connection' button."""
        try:
            p = _gitlab_request("GET", f"/projects/{_gl()['project']}")
            self._json({
                "ok": True,
                "name": p.get("name_with_namespace") or p.get("name") or "?",
                "open_issues": p.get("open_issues_count"),
            })
        except urllib.error.HTTPError as e:
            msg = {401: "Token inválido ou sem permissão.",
                   404: "Projeto não encontrado (verifica o Project ID e o URL).",
                   }.get(e.code, f"Erro HTTP {e.code}.")
            self._json({"ok": False, "error": msg})
        except Exception as e:
            self._json({"ok": False, "error": f"Não consegui ligar: {e}"})

    def _handle_members(self):
        try:
            members = cache.get_or_set(
                _ck("members:all"),
                lambda: _gitlab_paginate(
                    f"/projects/{_gl()['project']}/members/all", {"per_page": 100}
                ),
            )
            out = [{"id": m.get("id"), "name": m.get("name")}
                   for m in members if m.get("id")]
            self._json({"members": out})
        except Exception as e:
            log.warning("members failed: %s", e)
            self._json({"members": []})

    def _handle_issue_get(self):
        iid = urllib.parse.urlparse(self.path).path.rstrip("/").split("/")[-1]
        try:
            i = _gitlab_request("GET", f"/projects/{_gl()['project']}/issues/{iid}")
            assignees = i.get("assignees") or []
            self._json({
                "iid": i.get("iid"),
                "title": i.get("title", ""),
                "description": i.get("description") or "",
                "labels": i.get("labels", []),
                "milestone_id": (i.get("milestone") or {}).get("id") or "",
                "milestone_title": (i.get("milestone") or {}).get("title") or "",
                "due_date": i.get("due_date") or "",
                "assignee_id": assignees[0].get("id") if assignees else "",
                "assignee_name": assignees[0].get("name") if assignees else "",
                "confidential": bool(i.get("confidential")),
                "web_url": i.get("web_url"),
            })
        except Exception as e:
            log.warning("issue get failed: %s", e)
            self._error(e)

    def _handle_duplicate(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        title = (params.get("title", [""])[0]).strip().lower()
        if len(title) < 3:
            return self._json({"matches": []})
        try:
            opened = [i for i in get_all_issues("all") if i.get("state") == "opened"]
            qtoks = {w for w in _words(title) if len(w) >= 3}
            scored = []
            for i in opened:
                t = (i.get("title") or "").lower()
                ttoks = {w for w in _words(t) if len(w) >= 3}
                shared = len(qtoks & ttoks)
                if (qtoks and (title in t or t in title
                               or shared >= max(2, len(qtoks) // 2))):
                    scored.append((shared, i))
            scored.sort(key=lambda x: -x[0])
            out = [{"iid": i.get("iid"), "title": i.get("title")}
                   for _, i in scored[:3]]
            self._json({"matches": out})
        except Exception as e:
            log.warning("duplicate check failed: %s", e)
            self._json({"matches": []})

    def _handle_analyze_code(self, body):
        """Code investigation: which commit last changed a line, by whom — and
        an AI hypothesis of the bug. Facts come from GitLab (blame + diff,
        deterministic); the LLM is called once, at the end, only to explain."""
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return self._json({"ok": False, "error": "Pedido inválido."})
        file_raw = (data.get("file") or "").strip()
        if not file_raw:
            return self._json({"ok": False, "error": "Indica o ficheiro a investigar."})
        try:
            line = int(data.get("line")) if data.get("line") else None
        except (TypeError, ValueError):
            line = None
        if line is not None and line < 1:
            line = None
        question = str(data.get("question") or "")[:1200]

        try:
            info = get_project_info()
        except Exception:
            info = {}
        ref = info.get("default_branch") or "main"
        proj_url = info.get("web_url") or ""

        # Resolve the repo path: pasted paths are often absolute (IDE/traceback),
        # so try progressively shorter suffixes until one has commit history.
        path, hist = None, []
        for cand in _path_candidates(file_raw):
            try:
                h = _get_file_history(cand)
            except Exception as e:
                log.warning("file history failed for %s: %s", cand, e)
                continue
            if h:
                path, hist = cand, h
                break
        if not path:
            return self._json({
                "ok": False,
                "error": f"Não encontrei «{file_raw}» no repositório (ref {ref}). "
                         "Usa o caminho relativo à raiz do projeto.",
            })

        def _c(c):
            sha = c.get("id") or ""
            return {
                "short_sha": (c.get("short_id") or sha)[:8],
                "author": c.get("author_name") or "?",
                "date": (c.get("authored_date") or c.get("created_at") or "")[:10],
                "title": (c.get("title") or c.get("message") or "").strip()[:100],
                "commit_url": f"{proj_url}/-/commit/{sha}" if proj_url and sha else "",
            }

        authors = Counter((c.get("author_name") or "?") for c in hist)
        out = {
            "ok": True, "file": path, "line": line, "ref": ref,
            "facts": None, "line_text": "",
            "history": [_c(c) for c in hist[:8]],
            "authors": [{"name": n, "count": k} for n, k in authors.most_common(5)],
            "analysis": "", "note": "",
        }

        if line:
            start, end = max(1, line - 20), line + 20
            blame = None
            try:
                blame = _get_blame(path, ref, start, end)
            except Exception as e:
                log.warning("blame failed for %s:%d: %s", path, line, e)
            if not blame:
                out["note"] = ("Não consegui obter o git blame desta linha — "
                               "mostro só o histórico do ficheiro.")
            else:
                commit, line_text = _blame_owner(blame, start, line)
                if commit is None:
                    out["note"] = (f"A linha {line} parece estar além do fim do "
                                   f"ficheiro em {ref} — mostro só o histórico.")
                else:
                    sha = commit.get("id") or ""
                    out["facts"] = {
                        "sha": sha, "short_sha": sha[:8],
                        "author": commit.get("author_name") or "?",
                        "date": (commit.get("authored_date") or "")[:10],
                        "message": (commit.get("message") or "").strip()[:200],
                        "commit_url": (f"{proj_url}/-/commit/{sha}"
                                       if proj_url and sha else ""),
                    }
                    out["line_text"] = line_text
                    diff_txt = ""
                    try:
                        diff_txt = _diff_excerpt(_get_commit_diff(sha), path)
                    except Exception as e:
                        log.warning("commit diff failed for %s: %s", sha[:8], e)
                    out["analysis"] = _analyze_code_llm(
                        path, ref, line, line_text, _blame_snippet(blame, start, line),
                        out["facts"], diff_txt, question, data.get("model"),
                    )
        log.info("analyze-code %s:%s facts=%s analysis=%s",
                 path, line, bool(out["facts"]), bool(out["analysis"]))
        self._json(out)

    def _handle_generate_description(self, body):
        try:
            data = json.loads(body or b"{}")
            title = (data.get("title") or "").strip()
            if not title:
                return self._json({"description": ""})
            template = (data.get("template") or "").lower()
            hint = {
                "bug": " Estrutura como relatório de bug: o que acontece, passos para "
                       "reproduzir e resultado esperado.",
                "task": " Estrutura como tarefa: objetivo e critérios de aceitação.",
            }.get(template, "")
            prompt = (
                f"Escreve uma descrição curta e clara (2 a 4 frases) em português de "
                f"Portugal para uma issue de GitLab com o título «{title}».{hint} "
                f"Responde apenas com a descrição — sem título, sem aspas, sem cabeçalhos."
            )
            gmodel, greff = _pick_model(data.get("model"))
            payload = {
                "model": gmodel,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
                "max_tokens": 220,
            }
            if greff:
                payload["reasoning_effort"] = greff
            resp = _groq_complete(payload)
            desc = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")
            desc = _strip_think(desc)   # remove <think>… de modelos de raciocínio
            self._json({"description": desc})
        except Exception as e:
            log.warning("generate description failed: %s", e)
            self._json({"description": ""})

    def _handle_chat(self, body):
        try:
            data = json.loads(body)
            messages = data.get("messages", [])
            model, reff = _pick_model(data.get("model"))   # UI model switcher
            last_user = next(
                (m["content"] for m in reversed(messages) if m["role"] == "user"),
                "",
            )
            log.info("chat: %s  [%s]", last_user[:70], model)

            # Always give the model the live GitLab data so it can answer about
            # issues / tarefas / sprint / progresso regardless of phrasing.
            # The 45s TTL cache means repeated questions don't re-fetch.
            gitlab_ctx = get_gitlab_context()
            # SprintLab knowledge only applies to SprintLab; for any other
            # project, drop the static doc so the model adapts to that project.
            doc = DOCUMENT_CONTEXT if _gl()["project"] == SPRINTLAB_PROJECT_ID else ""
            system_content = SYSTEM_PROMPT
            if doc:
                system_content += "\n\n" + doc
            system_content += "\n" + gitlab_ctx

            convo = [{"role": "system", "content": system_content}, *messages]

            # Start the SSE response up front; every outcome is emitted as events.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self._cors()
            self.end_headers()

            # ── Pass 1: STREAM the answer live; if the model calls a tool
            #    instead, accumulate the call(s) and act on them below. ─────────
            first_payload = {
                "model": model,
                "messages": convo,
                "tools": ISSUE_TOOLS,
                "tool_choice": "auto",
                "stream": True,
                "temperature": 0.3,
                "max_tokens": 1024,
            }
            if reff:
                first_payload["reasoning_effort"] = reff

            try:
                tool_calls = self._stream_pass1(first_payload)
            except urllib.error.HTTPError as e:
                return self._emit_done(self._groq_error_msg(e))
            except urllib.error.URLError as e:
                return self._emit_done(f"Erro de ligação ao Groq: {e}")

            if not tool_calls:
                # Plain answer — already streamed token-by-token + done sent.
                log.info("chat done (no tool)")
                return

            # ── The model wants to act. DO NOT execute — propose it and let the
            #    user confirm (frontend renders a card; execution happens only on
            #    /api/confirm-action). This stops invented/accidental mutations. ─
            tc = tool_calls[0]  # one action at a time keeps the UX clear
            fn = (tc.get("function") or {}).get("name", "")
            try:
                args = json.loads((tc.get("function") or {}).get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            action = {"tool": fn, "args": args, "summary": _action_summary(fn, args)}
            log.info("proposing action for confirmation: %s(%s)", fn, args)
            self._safe_write(
                f"data: {json.dumps({'action': action, 'done': True}, ensure_ascii=False)}\n\n".encode()
            )

        except Exception as e:
            log.exception("chat handler failed")
            self._emit_done(f"Erro: {e}")

    # ---- chat helpers --------------------------------------------------------

    @staticmethod
    def _groq_error_msg(e) -> str:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            pass
        log.warning("groq HTTP %s: %s", getattr(e, "code", "?"), detail)
        if getattr(e, "code", None) == 429:
            return "Limite de pedidos atingido — espera um momento e tenta de novo."
        return f"Erro Groq ({getattr(e, 'code', '?')})."

    def _emit_done(self, text: str):
        """Send a single terminal event (used for errors)."""
        self._safe_write(
            f"data: {json.dumps({'content': text, 'done': True})}\n\n".encode()
        )

    def _stream_pass1(self, payload: dict):
        """Stream pass-1: forward content deltas to the client as they arrive
        (smooth token-by-token typing) while accumulating any tool_call deltas.

        Returns a list of tool_call dicts if the model chose to act (caller then
        executes them and runs pass-2). Returns None for a normal answer — in
        that case the text was already streamed and a 'done' event was sent.
        """
        req = urllib.request.Request(
            GROQ_URL,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "User-Agent": GROQ_UA,
            },
            method="POST",
        )
        tool_acc = {}   # index -> {"id", "name", "args"}
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=120) as r:
            for raw in r:
                line = raw.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    if not self._safe_write(
                        f"data: {json.dumps({'content': content, 'done': False})}\n\n".encode()
                    ):
                        r.close()
                        return None  # client gone
                for tcd in (delta.get("tool_calls") or []):
                    slot = tool_acc.setdefault(
                        tcd.get("index", 0), {"id": None, "name": "", "args": ""}
                    )
                    if tcd.get("id"):
                        slot["id"] = tcd["id"]
                    fn = tcd.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]

        if tool_acc:
            return [
                {"id": tool_acc[i]["id"], "type": "function",
                 "function": {"name": tool_acc[i]["name"],
                              "arguments": tool_acc[i]["args"]}}
                for i in sorted(tool_acc)
            ]
        # Normal answer fully streamed — finalise.
        self._safe_write(
            f"data: {json.dumps({'content': '', 'done': True, 'duration': int((time.time()-t0)*1000)})}\n\n".encode()
        )
        return None

    def _handle_generate_commit(self, body):
        """Commit por IA (1/2): gera o PLANO (ficheiros + mensagem) com uma
        chamada Groq. NADA é escrito no GitLab neste passo — o utilizador vê
        a pré-visualização e decide."""
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return self._json({"ok": False, "error": "Pedido inválido."})
        request_text = str(data.get("request") or "").strip()
        if len(request_text) < 8:
            return self._json({"ok": False,
                               "error": "Descreve o que queres programar e commitar."})
        plan, err = generate_commit_plan(request_text[:2000], data.get("model"))
        if err:
            return self._json({"ok": False, "error": err})
        log.info("commit plan: %s (%d ficheiro(s))", plan["branch"], len(plan["files"]))
        self._json({"ok": True, "plan": plan})

    def _handle_confirm_commit(self, body):
        """Commit por IA (2/2): executa o plano que o utilizador CONFIRMOU —
        branch ai/* + commit + Merge Request. A branch principal nunca é tocada."""
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return self._json({"ok": False, "error": "Pedido inválido."})
        result = execute_commit_plan(data.get("plan") or {})
        log.info("commit execute -> ok=%s branch=%s mr=%s err=%s",
                 result.get("ok"), result.get("branch"),
                 result.get("mr_iid"), result.get("error"))
        self._json(result)

    def _handle_confirm_action(self, body):
        """Execute an action the user explicitly confirmed in the UI."""
        try:
            data = json.loads(body or b"{}")
            tool = data.get("tool", "")
            args = data.get("args") or {}
            if tool not in {"create_issue", "close_issue", "update_issue", "delete_issue"}:
                return self._error(f"ação inválida: {tool}", status=400)
            result = execute_issue_tool(tool, args)
            log.info("confirmed action %s(%s) -> %s", tool, args, result)
            self._json(result)
        except Exception as e:
            log.exception("confirm action failed")
            self._error(e)

    def _handle_suggestions(self, body):
        """Return 3 short, context-aware follow-up chips for the UI. Always
        returns 200 with something usable — falls back to defaults on any error
        so the chips never break the page."""
        try:
            data = json.loads(body or b"{}")
            recent = [m for m in data.get("messages", []) if m.get("content")][-4:]
            convo_txt = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in recent)
            prompt = (
                "És o motor de sugestões de um assistente de IA para projetos "
                "GitLab. O assistente sabe: responder sobre issues, commits, "
                "progresso e autores; descrever o projeto (o que é, linguagens, "
                "estrutura); gerar gráficos (por assignee, burndown, commits por "
                "autor); criar o relatório do projeto; e exportar issues/commits "
                "para CSV. Com base na conversa abaixo, sugere exatamente 5 "
                "sugestões curtas de seguimento (máximo 6 palavras cada), em "
                "português de Portugal, VARIADAS entre esses tipos e relevantes "
                "para o que se falou (ex.: «Relatório do projeto», «Burndown 14 "
                "dias», «O que é este projeto?», «Quem tem mais commits?», "
                "«Exporta os commits para CSV»). REGRAS: só pedidos de "
                "leitura/consulta; NUNCA sugiras ações que alterem dados (criar, "
                "editar, fechar, adicionar, apagar). Responde APENAS com um array "
                "JSON de 5 strings.\n\n"
                f"Conversa:\n{convo_txt or '(início)'}"
            )
            gmodel, greff = _pick_model(data.get("model"))
            payload = {
                "model": gmodel,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
                "max_tokens": 150,
            }
            if greff:
                payload["reasoning_effort"] = greff
            resp = _groq_complete(payload)
            text = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")
            self._json({"suggestions": _parse_suggestions(text) or DEFAULT_SUGGESTIONS})
        except Exception as e:
            log.warning("suggestions failed: %s", e)
            self._json({"suggestions": DEFAULT_SUGGESTIONS})


class ThreadingServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ── Entrypoint ────────────────────────────────────────────────────────────────


def main():
    log.info("=" * 60)
    log.info("SprintLab chatbox listening on http://0.0.0.0:%d", PORT)
    if PUBLIC_URL:
        log.info("Public URL: %s", PUBLIC_URL)
    log.info("GitLab project: %s", GITLAB_PROJECT_ID)
    log.info(
        "Cache TTL: %ds  |  Page limit: %d  |  Threads: on",
        CACHE_TTL, GITLAB_PAGE_LIMIT,
    )
    log.info("Groq model: %s (reasoning_effort=%s)",
             GROQ_MODEL, GROQ_REASONING_EFFORT or "off")
    log.info("=" * 60)
    ThreadingServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
