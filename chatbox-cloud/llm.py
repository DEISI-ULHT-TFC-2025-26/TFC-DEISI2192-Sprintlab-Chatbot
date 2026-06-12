"""
Cliente Groq (OpenAI-compatible): escolha de modelo, chamada não-streaming,
limpeza de <think> e parsing das sugestões de seguimento.
"""

from __future__ import annotations

import json
import urllib.request

from config import (GROQ_API_KEY, GROQ_MODEL, GROQ_MODELS,
                    GROQ_REASONING_EFFORT, GROQ_UA, GROQ_URL)


def _pick_model(requested):
    """(model, reasoning_effort) for an allowed request, else the env default."""
    if requested in GROQ_MODELS:
        return requested, GROQ_MODELS[requested]
    return GROQ_MODEL, GROQ_REASONING_EFFORT


def _strip_think(text: str) -> str:
    """Remove <think>...</think> blocks that reasoning models (e.g. Qwen3)
    prepend, so they never leak into a saved issue description."""
    text = text or ""
    while "<think>" in text and "</think>" in text:
        start = text.index("<think>")
        end = text.index("</think>", start) + len("</think>")
        text = text[:start] + text[end:]
    return text.strip()


def _groq_complete(payload: dict) -> dict:
    """One non-streaming Groq call — used to detect tool calls."""
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
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


# ── Follow-up suggestions (contextual chips shown after each answer) ──────────

DEFAULT_SUGGESTIONS = [
    "Qual o progresso do projeto?",
    "Issues em atraso?",
    "Quem tem mais commits?",
    "O que é este projeto?",
    "Relatório do projeto",
]


def _parse_json_obj(text):
    """Extract the first JSON object {...} from a model reply, tolerating
    markdown fences, <think> blocks and surrounding prose. None if invalid."""
    t = _strip_think(text or "").strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(t[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _parse_suggestions(text: str) -> list:
    """Pull a JSON array of short strings out of the model's reply, tolerating
    markdown fences / extra prose."""
    t = (text or "").strip()
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        arr = json.loads(t[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    out = []
    for s in arr:
        if isinstance(s, str) and s.strip():
            out.append(s.strip()[:60])
        if len(out) == 5:
            break
    return out
