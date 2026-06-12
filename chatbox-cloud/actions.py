"""
Ações de escrita no GitLab (criar/fechar/atualizar/apagar issues): definição
das ferramentas para o function calling do modelo, validação/sanitização de
argumentos e o executor único por onde TODAS as escritas passam.
"""

from __future__ import annotations

import logging
from datetime import date

from gitlab_api import _gl, gitlab_request

log = logging.getLogger("sprintlab")


# ── Groq tool calling (real GitLab actions, no more fabrication) ──────────────

# NOTE: create_issue is intentionally NOT a model tool. Creating issues goes
# through the structured form in the UI (so the model can never invent a title /
# labels). The executor below still handles "create_issue" for the form's
# /api/confirm-action call. The model only proposes close/update.
ISSUE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "close_issue",
            "description": "Fecha uma issue existente pelo seu número (iid).",
            "parameters": {
                "type": "object",
                "properties": {
                    "iid": {"type": "integer", "description": "Número da issue (ex.: 5)"},
                },
                "required": ["iid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_issue",
            "description": "APAGA DEFINITIVAMENTE uma issue pelo número (iid). "
                           "Irreversível — usar só quando o utilizador pede mesmo "
                           "para apagar/eliminar (não para fechar).",
            "parameters": {
                "type": "object",
                "properties": {
                    "iid": {"type": "integer", "description": "Número da issue a apagar"},
                },
                "required": ["iid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_issue",
            "description": "Atualiza uma issue existente: título, descrição e/ou data "
                           "limite (due_date). Usar também para 'adicionar data a uma issue'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "iid": {"type": "integer", "description": "Número da issue"},
                    "title": {"type": "string", "description": "Novo título (opcional)"},
                    "description": {"type": "string", "description": "Nova descrição (opcional)"},
                    "due_date": {"type": "string",
                                 "description": "Data limite, formato AAAA-MM-DD (opcional)"},
                },
                "required": ["iid"],
            },
        },
    },
]


def _clean_labels(raw):
    """Drop placeholder labels like 'label1', 'label2' that the model invents."""
    if not raw:
        return ""
    keep = []
    for lab in str(raw).split(","):
        lab = lab.strip()
        if not lab:
            continue
        base = lab.lower()
        if base.startswith("label") and base[5:].isdigit():
            continue  # label1, label2, ...
        keep.append(lab)
    return ",".join(keep)


def _valid_due_date(s):
    """Accept only a real YYYY-MM-DD date; return it or None."""
    try:
        return date.fromisoformat(str(s).strip()).isoformat()
    except (ValueError, TypeError):
        return None


def _int_ids(raw):
    """Coerce a list/CSV of user ids into a clean list of ints (assignee_ids)."""
    if not raw:
        return []
    items = raw if isinstance(raw, list) else str(raw).split(",")
    return [int(str(x).strip()) for x in items if str(x).strip().isdigit()]


def _norm_iid(raw):
    """Normalise an issue number (the model sometimes sends '#5' or '5 ').
    Returns the digits as a string, or '' if not a valid number."""
    s = str(raw or "").strip().lstrip("#").strip()
    return s if s.isdigit() else ""


def _words(s: str):
    """Tokenise into alphanumeric words (no regex import needed)."""
    out, cur = [], []
    for ch in s:
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def _action_summary(name: str, args: dict) -> str:
    """Human-readable description of a proposed action, shown on the confirm card."""
    if name == "create_issue":
        parts = [f"Criar uma issue com o título «{args.get('title', '?')}»"]
        if _clean_labels(args.get("labels")):
            parts.append(f"labels: {_clean_labels(args.get('labels'))}")
        if args.get("due_date"):
            parts.append(f"data: {args.get('due_date')}")
        return " · ".join(parts)
    if name == "close_issue":
        return f"Fechar a issue #{args.get('iid', '?')}"
    if name == "delete_issue":
        return f"APAGAR DEFINITIVAMENTE a issue #{args.get('iid', '?')} — irreversível"
    if name == "update_issue":
        parts = [f"Atualizar a issue #{args.get('iid', '?')}"]
        if args.get("title"):
            parts.append(f"novo título «{args['title']}»")
        if args.get("description"):
            parts.append("nova descrição")
        if args.get("due_date"):
            parts.append(f"data: {args['due_date']}")
        return " · ".join(parts)
    return f"Ação: {name}"


def execute_issue_tool(name: str, args: dict) -> dict:
    """Run a tool against GitLab. Returns a small dict the model summarises."""
    try:
        if name == "create_issue":
            # Create comes from the UI form (user-filled), so just require a title.
            title = (args.get("title") or "").strip()
            if not title:
                return {"ok": False, "error": "Indica um título para a issue."}
            body = {"title": title,
                    "description": args.get("description") or "Criada via TFC Chatbox"}
            labels = _clean_labels(args.get("labels"))
            if labels:
                body["labels"] = labels
            due = _valid_due_date(args.get("due_date")) if args.get("due_date") else None
            if due:
                body["due_date"] = due
            if args.get("milestone_id"):
                try:
                    body["milestone_id"] = int(args["milestone_id"])
                except (ValueError, TypeError):
                    pass
            ids = _int_ids(args.get("assignee_ids"))
            if ids:
                body["assignee_ids"] = ids
            if args.get("confidential"):
                body["confidential"] = True
            r = gitlab_request("POST", f"/projects/{_gl()['project']}/issues", body=body)
            return {"ok": True, "action": "create", "iid": r.get("iid"),
                    "title": r.get("title"), "web_url": r.get("web_url")}

        if name == "close_issue":
            iid = _norm_iid(args.get("iid"))
            if not iid:
                return {"ok": False, "error": "Número de issue inválido."}
            r = gitlab_request("PUT", f"/projects/{_gl()['project']}/issues/{iid}",
                               body={"state_event": "close"})
            return {"ok": True, "action": "close", "iid": r.get("iid"),
                    "title": r.get("title"), "state": r.get("state")}

        if name == "delete_issue":
            iid = _norm_iid(args.get("iid"))
            if not iid:
                return {"ok": False, "error": "Número de issue inválido."}
            gitlab_request("DELETE", f"/projects/{_gl()['project']}/issues/{iid}")
            return {"ok": True, "action": "delete", "iid": int(iid)}

        if name == "update_issue":
            iid = _norm_iid(args.get("iid"))
            if not iid:
                return {"ok": False, "error": "Número de issue inválido."}
            body = {}
            # "key in args" so the edit form (sends every field) can also clear
            # them, while the model's partial updates only touch what it sends.
            if args.get("title"):
                body["title"] = args["title"].strip()
            if "description" in args:
                body["description"] = args.get("description") or ""
            if "labels" in args:
                body["labels"] = _clean_labels(args.get("labels"))  # "" clears all
            if "due_date" in args:
                raw = args.get("due_date")
                if raw:
                    due = _valid_due_date(raw)
                    if due:
                        body["due_date"] = due   # data válida → define
                else:
                    body["due_date"] = ""        # vazio → limpa a data no GitLab
            if "milestone_id" in args:
                mid = args.get("milestone_id")
                try:
                    body["milestone_id"] = int(mid) if mid else 0  # 0 unassigns
                except (ValueError, TypeError):
                    pass
            if "assignee_ids" in args:
                body["assignee_ids"] = _int_ids(args.get("assignee_ids"))  # [] unassigns
            if "confidential" in args:
                body["confidential"] = bool(args["confidential"])
            if not body:
                return {"ok": False, "error": "Nada para atualizar."}
            r = gitlab_request("PUT", f"/projects/{_gl()['project']}/issues/{iid}", body=body)
            return {"ok": True, "action": "update", "iid": r.get("iid"),
                    "title": r.get("title"), "web_url": r.get("web_url")}

        return {"ok": False, "error": f"ferramenta desconhecida: {name}"}
    except KeyError as e:
        return {"ok": False, "error": f"argumento em falta: {e}"}
    except Exception as e:
        log.exception("tool %s failed", name)
        return {"ok": False, "error": str(e)}
