"""
Configuração central (env-driven, fail-fast) + prompts estáticos.
Tudo o que é ajustável por variável de ambiente vive aqui.
"""

from __future__ import annotations

import logging
import os

# ── Config (env-driven, fail fast) ────────────────────────────────────────────

GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN")
if not GITLAB_TOKEN:
    raise SystemExit(
        "GITLAB_TOKEN is not set. Set it as a Hugging Face Space Secret "
        "(Settings -> Variables and secrets), or export it when running locally."
    )

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise SystemExit(
        "GROQ_API_KEY is not set. Get a free key at https://console.groq.com/keys "
        "and set it as a Hugging Face Space Secret."
    )

GITLAB_PROJECT_ID = os.environ.get("GITLAB_PROJECT_ID", "80767095")
GITLAB_BASE = os.environ.get("GITLAB_BASE", "https://gitlab.com/api/v4")
PORT = int(os.environ.get("PORT", "7860"))  # HF Spaces routes to app_port (7860)
PUBLIC_URL = os.environ.get("PUBLIC_URL", "")  # informational only
CACHE_TTL = int(os.environ.get("CACHE_TTL", "45"))
GITLAB_PAGE_LIMIT = int(os.environ.get("GITLAB_PAGE_LIMIT", "5"))

# Groq inference (OpenAI-compatible API). Model + reasoning are env-configurable
# so you can switch to e.g. llama-3.3-70b-versatile without touching code.
GROQ_URL = os.environ.get(
    "GROQ_URL", "https://api.groq.com/openai/v1/chat/completions"
)
# Default: Llama 3.3 70B — the most reliable tool-calling model on Groq (no
# reasoning quirks). Prefer Qwen? Set GROQ_MODEL="qwen/qwen3-32b" AND
# GROQ_REASONING_EFFORT="none".
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
# Empty for non-reasoning models (Llama). For Qwen3, set "none" to skip <think>.
GROQ_REASONING_EFFORT = os.environ.get("GROQ_REASONING_EFFORT", "")
# Cloudflare (in front of api.groq.com) blocks the default "Python-urllib"
# User-Agent with "error code: 1010". Send a normal browser UA so the request
# reaches the Groq API instead of being bounced at the edge.
GROQ_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(threadName)-12s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sprintlab")

# ── Static context (kept inline; full RAG is a separate task) ─────────────────

DOCUMENT_CONTEXT = """=== RELATÓRIO INTERCALAR TFC — SprintLab ===
Autor: Bernardo Gouveia | Orientador: Daniel Silveira | LEI | Universidade Lusófona | 2025/2026

DESCRIÇÃO: O SprintLab é um middleware e plugin para Microsoft Teams que integra o GitLab com sincronização bidirecional de issues, Kanban, Gantt e IA conversacional.
PROBLEMA: Falta de integração entre GitLab e Microsoft Teams gera processos fragmentados, duplicação de tarefas e perda de eficiência. Parceiro: GMV.
SOLUÇÃO: Middleware Express.js + plugin Teams + chatbox IA com Llama 3.3 70B via Groq (inferência rápida, modelo open-source, com function calling para ações no GitLab).
TECNOLOGIAS: Express.js (middleware), Microsoft Teams API (plugin), GitLab API (webhooks/issues), Llama 3.3 70B via Groq (IA), Hugging Face Spaces (hosting), PostgreSQL (configurações), Docker.
BENCHMARKING: SprintLab é o único com Chatbox IA + NLP + Sincronização bidirecional GitLab↔Teams + Relatórios automáticos IA.
VIABILIDADE: 85% melhoraria eficiência, 70% Kanban+Gantt essenciais, 90% interesse em automação. Redução de 40% em tarefas administrativas. Modelo SaaS.
FUNCIONALIDADES IA: (1) Chatbox NLP — criar/fechar/atualizar issues, queries analíticas, exportar CSV. (2) Relatórios automáticos de sprint. (3) Motor IA↔GitLab↔Teams.
GLOSSÁRIO: LEI=Licenciatura Eng. Informática, TFC=Trabalho Final de Curso, SaaS=Software as a Service, NLP=Natural Language Processing."""

SYSTEM_PROMPT = """És um assistente de IA para projetos GitLab. O projeto atual é indicado nos «DADOS GITLAB» abaixo (nome, contagens, issues e — na secção REPOSITÓRIO — descrição, linguagens, estrutura e excerto do README). Não assumas que é sempre o mesmo — adapta a tua resposta ao projeto em contexto.
Responde SEMPRE em português de Portugal. Sê direto, claro e conciso.
Usa os dados fornecidos. Não inventes informação. NÃO arredondes contagens (commits, issues, autores) — usa o número exato do contexto. O total de commits do projeto é a linha «Total de commits no repositório».
Tens SEMPRE em contexto os dados atuais do GitLab (totais, progresso, issues abertas, assignees, atrasos, milestones) — usa-os para responder com precisão. As issues SÃO as tarefas do projeto. NUNCA digas que não tens acesso às issues/tarefas nem mandes o utilizador ir ao GitLab: tens os dados aqui.
Quando te perguntarem «fala-me do projeto» / «o que é isto» / «a estrutura», descreve o projeto a partir da secção REPOSITÓRIO (o que faz, linguagens, organização) — NÃO respondas só com a contagem de issues. Mesmo que o README esteja em inglês, responde em português de Portugal (ex.: «ficheiro» não «arquivo», «gestão» não «gerenciamento»). Baseia o propósito do projeto no que está escrito no README/descrição — se não for claro o que o projeto faz, di-lo em vez de adivinhar.

AÇÕES NO GITLAB:
- CRIAR issues NÃO é contigo: existe um formulário próprio. Se o utilizador quiser criar, diz-lhe para escrever «criar issue». NUNCA finjas criar nem inventes uma issue.
- ATUALIZAR/EDITAR: chama update_issue APENAS com o `iid` (o número). NÃO preenchas título/descrição/data — o utilizador edita tudo num FORMULÁRIO pré-preenchido que abre a seguir. Se ele pediu para atualizar e depois disser só um número, chama update_issue com esse iid. NUNCA inventes os campos.
- FECHAR (close_issue) e APAGAR (delete_issue): chama com o `iid`; o utilizador confirma num cartão. delete_issue só se ele pedir mesmo para APAGAR/ELIMINAR (é irreversível).
- NUNCA inventes números, títulos, descrições nem datas. Se faltar o número da issue, PERGUNTA.
- "Como faço X?" → responde com instruções, sem chamar ferramentas."""

# Project ID where the SprintLab knowledge (above) actually applies. For any
# other project (custom GitLab via the settings panel), DOCUMENT_CONTEXT is
# replaced by a lightweight per-project header built at request time.
SPRINTLAB_PROJECT_ID = "80767095"

# Orçamento de caracteres do excerto do README injetado no contexto do LLM.
README_MAX = int(os.environ.get("README_MAX", "1500"))

# Models offered in the UI switcher → reasoning_effort to use for each.
# Empty string = not a reasoning model (Llama/GPT-OSS/Kimi).
# "none" = reasoning model (Qwen3) com o passo <think> desligado.
GROQ_MODELS = {
    "llama-3.3-70b-versatile": "",
    "llama-3.1-8b-instant": "",
    "qwen/qwen3-32b": "none",
    "openai/gpt-oss-120b": "",
    "moonshotai/kimi-k2-instruct-0905": "",
}

# Pedidos por minuto, por IP, nos endpoints que consomem Groq (0 = desligado).
# Protege a QUOTA grátis partilhada (ops) — não é autenticação.
RATE_LIMIT = int(os.environ.get("RATE_LIMIT", "20"))
