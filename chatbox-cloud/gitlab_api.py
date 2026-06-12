"""
Cliente GitLab: cache TTL thread-safe, contexto multi-tenant por pedido
(thread-local), request/paginação e todos os fetchers (issues, milestones,
projeto, commits, contribuidores, MRs, linguagens, árvore, README, blame).
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from config import (CACHE_TTL, GITLAB_BASE, GITLAB_PAGE_LIMIT,
                    GITLAB_PROJECT_ID, GITLAB_TOKEN)

log = logging.getLogger("sprintlab")


# ── Thread-safe TTL cache ─────────────────────────────────────────────────────


class TTLCache:
    """Tiny thread-safe TTL cache. Producers run *outside* the lock so two
    callers never serialise on the same slow GitLab fetch."""

    def __init__(self, ttl: int):
        self.ttl = ttl
        self._data: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()
        self._gen = 0  # bumped on every invalidate — guards the fetch-write race

    def get_or_set(self, key, producer):
        now = time.time()
        with self._lock:
            entry = self._data.get(key)
            if entry and now - entry[0] < self.ttl:
                return entry[1]
            gen = self._gen
        value = producer()  # runs outside the lock (slow GitLab call)
        with self._lock:
            # If a write invalidated during the fetch, the value we just read
            # may already be stale — return it for this response but do NOT
            # cache it (else the stale snapshot would survive the whole TTL).
            if self._gen == gen:
                self._data[key] = (time.time(), value)
        return value

    def invalidate(self, prefix: str = ""):
        with self._lock:
            self._gen += 1
            for k in [k for k in self._data if k.startswith(prefix)]:
                del self._data[k]


cache = TTLCache(CACHE_TTL)
gitlab_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gitlab")

# ── Per-request GitLab config ─────────────────────────────────────────────────
# Each user can point at their own GitLab via the settings panel: the frontend
# sends X-GL-Base / X-GL-Token / X-GL-Project headers, read into a thread-local
# at the start of every request. Falls back to the env Secrets when absent.
_ctx = threading.local()


def _gl():
    return getattr(_ctx, "gl", None) or {
        "base": GITLAB_BASE, "token": GITLAB_TOKEN, "project": GITLAB_PROJECT_ID,
    }


def _ck(name: str) -> str:
    """Namespace a cache key by instance+project so different GitLabs never mix."""
    g = _gl()
    return f"{g['base']}#{g['project']}:{name}"


def _run_with_ctx(cfg, fn, *args):
    """Run fn in a pool worker carrying the caller's GitLab config (thread-local
    does not propagate into ThreadPoolExecutor worker threads)."""
    _ctx.gl = cfg
    return fn(*args)


# ── GitLab client ─────────────────────────────────────────────────────────────


def _gitlab_request(method, endpoint, body=None, params=None, return_headers=False):
    gl = _gl()
    url = f"{gl['base']}{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"PRIVATE-TOKEN": gl["token"], "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        payload = json.loads(raw) if raw else {}  # DELETE returns 204 / empty body
        if return_headers:
            # Return the HTTPMessage directly — its .get() is case-insensitive.
            # GitLab sends "x-next-page" (lowercase); converting to dict() would
            # lose the case-insensitive lookup and silently break pagination.
            return payload, r.headers
        return payload


def _gitlab_paginate(endpoint, params=None, page_limit=None):
    """Generic paginator. Follows X-Next-Page until exhausted or page_limit hit."""
    if page_limit is None:
        page_limit = GITLAB_PAGE_LIMIT
    items = []
    page = 1
    base = dict(params or {})
    base.setdefault("per_page", 100)
    while page <= page_limit:
        batch, headers = _gitlab_request(
            "GET", endpoint, params={**base, "page": page}, return_headers=True
        )
        items.extend(batch)
        if len(batch) < 100 or not headers.get("X-Next-Page"):
            break
        page += 1
    return items


def gitlab_request(method, endpoint, body=None, params=None):
    """Public entry — invalidates affected caches on writes."""
    if method != "GET":
        cache.invalidate(_ck("issues:"))
        cache.invalidate(_ck("milestones:"))
        cache.invalidate(_ck("labels:"))   # an issue write can create a new label
    return _gitlab_request(method, endpoint, body, params)


def get_all_issues(state: str = "all"):
    return cache.get_or_set(_ck(f"issues:{state}"), lambda: _fetch_all_issues(state))


def _fetch_all_issues(state: str):
    issues: list[dict] = []
    page = 1
    while page <= GITLAB_PAGE_LIMIT:
        params = {"state": state, "per_page": 100, "page": page}
        batch, headers = _gitlab_request(
            "GET",
            f"/projects/{_gl()['project']}/issues",
            params=params,
            return_headers=True,
        )
        issues.extend(batch)
        if len(batch) < 100 or not headers.get("X-Next-Page"):
            break
        page += 1
    log.info("gitlab fetched %d issues (state=%s, %d page(s))",
             len(issues), state, page)
    return issues


def get_milestones():
    return cache.get_or_set(
        _ck("milestones:active"),
        lambda: _gitlab_request(
            "GET",
            f"/projects/{_gl()['project']}/milestones",
            params={"state": "active"},
        ),
    )


def get_project_info():
    """Project metadata (name, web_url, statistics like commit_count) — cached."""
    return cache.get_or_set(
        _ck("project:info"),
        lambda: _gitlab_request(
            "GET", f"/projects/{_gl()['project']}", params={"statistics": "true"}
        ),
    )


# ── Repo overview: WHAT the project is (not just issue counts) ────────────────
# "fala-me do projeto" needs description + languages + structure + README, not a
# list of issues. Injected once per turn (cached) into the LLM context.


def _get_languages():
    return cache.get_or_set(
        _ck("languages"),
        lambda: _gitlab_request("GET", f"/projects/{_gl()['project']}/languages"),
    )


def _get_repo_tree_top(ref):
    """Top-level files/dirs of the default branch (project structure at a glance)."""
    return cache.get_or_set(
        _ck(f"tree:{ref}"),
        lambda: _gitlab_paginate(
            f"/projects/{_gl()['project']}/repository/tree",
            {"ref": ref, "per_page": 100}, page_limit=1,
        ),
    )


def _find_readme(tree):
    """First root entry that looks like a README file."""
    for e in tree or []:
        if e.get("type") == "blob" and (e.get("name") or "").lower().startswith("readme"):
            return e.get("path") or e.get("name")
    return None


def _get_readme_text(ref, tree):
    """Raw README content (decoded) for the default branch, '' if none. The
    Files API returns base64 JSON, so this goes through the normal JSON path."""
    path = _find_readme(tree)
    if not path:
        return ""

    def _fetch():
        f = _gitlab_request(
            "GET",
            f"/projects/{_gl()['project']}/repository/files/"
            f"{urllib.parse.quote(path, safe='')}",
            params={"ref": ref},
        )
        content = f.get("content") or ""
        if (f.get("encoding") or "").lower() == "base64":
            try:
                return base64.b64decode(content).decode("utf-8", "replace")
            except Exception:
                return ""
        return content

    return cache.get_or_set(_ck(f"readme:{ref}"), _fetch)

def _get_merge_requests():
    return cache.get_or_set(
        _ck("mrs:all"),
        lambda: _gitlab_paginate(
            f"/projects/{_gl()['project']}/merge_requests",
            {"state": "all", "scope": "all"},
        ),
    )


def _get_commits(days):
    since = (date.today() - timedelta(days=days)).isoformat() + "T00:00:00Z"
    return cache.get_or_set(
        _ck(f"commits:{days}"),
        lambda: _gitlab_paginate(
            f"/projects/{_gl()['project']}/repository/commits",
            {"since": since},
        ),
    )


def _get_all_commits(page_limit=50):
    """Full commit history (no date window) for CSV export. A higher page limit
    than the default so big mirrors (e.g. AIR, ~2300 commits) export in full."""
    return cache.get_or_set(
        _ck("commits:export"),
        lambda: _gitlab_paginate(
            f"/projects/{_gl()['project']}/repository/commits",
            page_limit=page_limit,
        ),
    )


def _get_contributors():
    """ALL-TIME commits per author, computed by GitLab itself — the right data
    for dormant/mirrored repos where a recent-days window is empty."""
    return cache.get_or_set(
        _ck("contributors:all"),
        lambda: _gitlab_paginate(
            f"/projects/{_gl()['project']}/repository/contributors",
            {"order_by": "commits", "sort": "desc"},
        ),
    )

def _get_file_history(path, n=15):
    """Last commits that touched `path` (newest first). Empty list = path not
    found on the default branch, which doubles as cheap path resolution."""
    return cache.get_or_set(
        _ck(f"fhist:{path}"),
        lambda: _gitlab_paginate(
            f"/projects/{_gl()['project']}/repository/commits",
            {"path": path, "per_page": n}, page_limit=1,
        ),
    )


def _get_blame(path, ref, start, end):
    return cache.get_or_set(
        _ck(f"blame:{ref}:{path}:{start}-{end}"),
        lambda: _gitlab_request(
            "GET",
            f"/projects/{_gl()['project']}/repository/files/"
            f"{urllib.parse.quote(path, safe='')}/blame",
            params={"ref": ref, "range[start]": start, "range[end]": end},
        ),
    )


def _get_commit_diff(sha):
    return cache.get_or_set(
        _ck(f"cdiff:{sha}"),
        lambda: _gitlab_request(
            "GET", f"/projects/{_gl()['project']}/repository/commits/{sha}/diff"
        ),
    )
