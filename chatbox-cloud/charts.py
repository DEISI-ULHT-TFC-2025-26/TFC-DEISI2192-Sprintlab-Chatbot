"""
Construtores de dados Chart.js (gráficos inline no chat) + registo
CHART_HANDLERS usado pela rota /gitlab/chart/<nome>.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date, timedelta

from gitlab_api import (_get_commits, _get_contributors,
                        _get_merge_requests, get_all_issues)

log = logging.getLogger("sprintlab")


# ── Chart data builders (Chart.js-compatible JSON) ────────────────────────────

_PALETTE = [
    "#8B88F8", "#4caf50", "#ff9800", "#f44336", "#03a9f4",
    "#e91e63", "#ffeb3b", "#9c27b0", "#00bcd4", "#cddc39",
    "#795548", "#607d8b",
]


def _colors(n):
    return [_PALETTE[i % len(_PALETTE)] for i in range(n)]


def _no_data_chart(title):
    return {
        "type": "doughnut",
        "title": title,
        "labels": ["Sem dados"],
        "datasets": [{"data": [1], "backgroundColor": ["#444"]}],
    }

def chart_state_pie(**_):
    issues = get_all_issues("all")
    if not issues:
        return _no_data_chart("Issues por estado")
    opened = sum(1 for i in issues if i.get("state") == "opened")
    closed = sum(1 for i in issues if i.get("state") == "closed")
    return {
        "type": "doughnut",
        "title": f"Issues por estado (total {len(issues)})",
        "labels": ["Abertas", "Fechadas"],
        "datasets": [{
            "data": [opened, closed],
            "backgroundColor": ["#ff9800", "#4caf50"],
        }],
    }


def chart_by_assignee(**_):
    opened = [i for i in get_all_issues("all") if i.get("state") == "opened"]
    if not opened:
        return _no_data_chart("Issues abertas por assignee")
    counter = Counter()
    for i in opened:
        name = i["assignee"]["name"] if i.get("assignee") else "Sem assignee"
        counter[name] += 1
    items = counter.most_common()
    labels = [k for k, _ in items]
    data = [v for _, v in items]
    return {
        "type": "bar",
        "title": "Issues abertas por assignee",
        "labels": labels,
        "datasets": [{
            "label": "Issues",
            "data": data,
            "backgroundColor": _colors(len(labels)),
        }],
    }


def chart_by_label(**_):
    issues = get_all_issues("all")
    counter = Counter()
    for i in issues:
        for lab in (i.get("labels") or []):
            counter[lab] += 1
    if not counter:
        return _no_data_chart("Issues por label")
    items = counter.most_common(10)
    labels = [k for k, _ in items]
    data = [v for _, v in items]
    return {
        "type": "bar",
        "title": "Issues por label (top 10)",
        "labels": labels,
        "datasets": [{
            "label": "Issues",
            "data": data,
            "backgroundColor": _colors(len(labels)),
        }],
    }


def chart_by_milestone(**_):
    issues = get_all_issues("all")
    if not issues:
        return _no_data_chart("Issues por milestone")
    counter = Counter()
    for i in issues:
        ms = (i.get("milestone") or {}).get("title") or "Sem milestone"
        counter[ms] += 1
    items = counter.most_common()
    labels = [k for k, _ in items]
    data = [v for _, v in items]
    return {
        "type": "bar",
        "title": "Issues por milestone",
        "labels": labels,
        "datasets": [{
            "label": "Issues",
            "data": data,
            "backgroundColor": _colors(len(labels)),
        }],
    }


def chart_burndown(days=14, **_):
    days = max(1, min(int(days), 90))
    closed = [i for i in get_all_issues("closed") if i.get("closed_at")]
    today = date.today()
    start = today - timedelta(days=days - 1)
    bucket = {start + timedelta(days=i): 0 for i in range(days)}
    for i in closed:
        try:
            d = date.fromisoformat(i["closed_at"][:10])
            if d in bucket:
                bucket[d] += 1
        except (ValueError, KeyError):
            continue
    labels = [d.strftime("%d/%m") for d in bucket]
    data = [bucket[d] for d in bucket]
    total = sum(data)
    return {
        "type": "line",
        "title": f"Burndown — {total} issues fechadas nos últimos {days} dias",
        "labels": labels,
        "datasets": [{
            "label": "Fechadas/dia",
            "data": data,
            "borderColor": "#8B88F8",
            "backgroundColor": "rgba(139,136,248,0.2)",
            "fill": True,
            "tension": 0.3,
        }],
    }


def chart_cycle_time(**_):
    closed = [i for i in get_all_issues("closed")
              if i.get("closed_at") and i.get("created_at")]
    if not closed:
        return _no_data_chart("Tempo de ciclo")
    buckets = {"0-1d": 0, "2-3d": 0, "4-7d": 0, "8-14d": 0, "15-30d": 0, ">30d": 0}
    total_days = 0
    n = 0
    for i in closed:
        try:
            opened_at = date.fromisoformat(i["created_at"][:10])
            closed_at = date.fromisoformat(i["closed_at"][:10])
            d = (closed_at - opened_at).days
        except (ValueError, KeyError):
            continue
        total_days += d
        n += 1
        if d <= 1:    buckets["0-1d"] += 1
        elif d <= 3:  buckets["2-3d"] += 1
        elif d <= 7:  buckets["4-7d"] += 1
        elif d <= 14: buckets["8-14d"] += 1
        elif d <= 30: buckets["15-30d"] += 1
        else:         buckets[">30d"] += 1
    avg = round(total_days / n, 1) if n else 0
    return {
        "type": "bar",
        "title": f"Tempo de ciclo — média {avg} dias (n={n})",
        "labels": list(buckets.keys()),
        "datasets": [{
            "label": "Issues",
            "data": list(buckets.values()),
            "backgroundColor": _colors(len(buckets)),
        }],
    }


def chart_contributors_mr(**_):
    try:
        mrs = _get_merge_requests()
    except Exception as e:
        log.warning("MR fetch failed: %s", e)
        return _no_data_chart("Top MRs por autor")
    if not mrs:
        return _no_data_chart("Top MRs por autor")
    counter = Counter()
    for mr in mrs:
        name = (mr.get("author") or {}).get("name") or "Desconhecido"
        counter[name] += 1
    items = counter.most_common(10)
    labels = [k for k, _ in items]
    data = [v for _, v in items]
    return {
        "type": "bar",
        "title": f"Top MRs por autor (total {len(mrs)})",
        "labels": labels,
        "datasets": [{
            "label": "Merge Requests",
            "data": data,
            "backgroundColor": _colors(len(labels)),
        }],
    }


def chart_contributors_all(**_):
    """Top contributors of ALL TIME (GitLab contributors API) — the default for
    'top commits', so dormant/mirrored repos show their real history."""
    try:
        contribs = _get_contributors()
    except Exception as e:
        log.warning("contributors fetch failed: %s", e)
        return _no_data_chart("Top contribuidores (commits)")
    if not contribs:
        return _no_data_chart("Top contribuidores (commits)")
    top = contribs[:10]
    total = sum(c.get("commits", 0) for c in contribs)
    return {
        "type": "bar",
        "title": f"Top contribuidores — histórico completo ({total} commits, {len(contribs)} autores)",
        "labels": [c.get("name") or "?" for c in top],
        "datasets": [{
            "label": "Commits",
            "data": [c.get("commits", 0) for c in top],
            "backgroundColor": _colors(len(top)),
        }],
    }


def chart_contributors_commits(days=90, **_):
    days = max(1, min(int(days), 365))
    try:
        commits = _get_commits(days)
    except Exception as e:
        log.warning("commits fetch failed: %s", e)
        return _no_data_chart("Top commits por autor")
    if not commits:
        return _no_data_chart(f"Top commits — últimos {days} dias (0 commits)")
    counter = Counter()
    for c in commits:
        name = c.get("author_name") or "Desconhecido"
        counter[name] += 1
    items = counter.most_common(10)
    labels = [k for k, _ in items]
    data = [v for _, v in items]
    return {
        "type": "bar",
        "title": f"Top commits por autor — últimos {days} dias (total {len(commits)})",
        "labels": labels,
        "datasets": [{
            "label": "Commits",
            "data": data,
            "backgroundColor": _colors(len(labels)),
        }],
    }


CHART_HANDLERS = {
    "state-pie": chart_state_pie,
    "by-assignee": chart_by_assignee,
    "by-label": chart_by_label,
    "by-milestone": chart_by_milestone,
    "burndown": chart_burndown,
    "cycle-time": chart_cycle_time,
    "contributors-mr": chart_contributors_mr,
    "contributors-commits": chart_contributors_commits,
    "contributors-all": chart_contributors_all,
}
