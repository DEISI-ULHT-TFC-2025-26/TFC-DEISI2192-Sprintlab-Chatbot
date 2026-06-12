"""
Commit por IA (Feature GMV #1): o utilizador pede código em linguagem natural,
o LLM gera um PLANO (ficheiros + mensagem de commit), o utilizador confirma num
cartão com pré-visualização, e só então o servidor escreve no GitLab.

Desenho de segurança (by design, sem auth):
  - Geração e execução são passos separados — nada é escrito sem confirmação.
  - As escritas vão SEMPRE para uma branch nova `ai/<slug>` + Merge Request;
    a branch principal nunca é tocada. A revisão humana acontece no MR.
  - Os caminhos/conteúdos do plano são validados no servidor (re-validados na
    execução — o JSON que vem do browser não é confiado às cegas).
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse

from gitlab_api import (_get_languages, _get_repo_tree_top, _gitlab_request,
                        _gl, get_project_info, gitlab_request)
from llm import _groq_complete, _parse_json_obj, _pick_model

log = logging.getLogger("sprintlab")

MAX_FILES = 5
MAX_CONTENT = 200_000   # chars por ficheiro — folga grande, evita payloads absurdos


# ── Helpers puros (unit-testáveis) ────────────────────────────────────────────


def _slugify(text, max_len=32):
    """Nome seguro para branch: minúsculas, só [a-z0-9-], sem '-' repetidos."""
    out, prev = [], "-"
    for ch in str(text or "").lower():
        if ch.isalnum() and ch.isascii():
            out.append(ch)
            prev = ch
        elif prev != "-":
            out.append("-")
            prev = "-"
    s = "".join(out).strip("-")[:max_len].strip("-")
    return s or "alteracao"


def _validate_files(raw):
    """Sanitiza a lista de ficheiros de um plano. Devolve (files, erro)."""
    if not isinstance(raw, list) or not raw:
        return None, "O plano não tem ficheiros."
    if len(raw) > MAX_FILES:
        return None, f"Máximo {MAX_FILES} ficheiros por commit."
    files = []
    for f in raw:
        if not isinstance(f, dict):
            return None, "Formato de ficheiro inválido."
        path = str(f.get("path") or "").strip().replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        content = f.get("content")
        if not path or len(path) > 200 or path.endswith("/"):
            return None, f"Caminho inválido: «{path[:60]}»."
        if path.startswith("/") or ".." in path.split("/"):
            return None, f"Caminho não permitido: «{path[:60]}»."
        if path.split("/")[0] == ".git":
            return None, "Não é permitido escrever em .git/."
        if not isinstance(content, str) or not content.strip():
            return None, f"Conteúdo vazio em «{path}»."
        if len(content) > MAX_CONTENT:
            return None, f"Conteúdo demasiado grande em «{path}»."
        files.append({"path": path, "content": content})
    paths = [f["path"] for f in files]
    if len(set(paths)) != len(paths):
        return None, "Ficheiros duplicados no plano."
    return files, None


def plan_from_llm_text(text):
    """Resposta do modelo → plano validado. Devolve (plan, erro). Puro."""
    obj = _parse_json_obj(text)
    if not obj:
        return None, "O modelo não devolveu um plano JSON válido — tenta de novo."
    files, err = _validate_files(obj.get("files"))
    if err:
        return None, err
    msg = (str(obj.get("commit_message") or "").strip()[:120]
           or "Alterações via SprintLab Chatbox")
    slug = _slugify(obj.get("branch_slug") or msg)
    summary = str(obj.get("summary") or "").strip()[:400]
    return {"branch": f"ai/{slug}", "commit_message": msg,
            "summary": summary, "files": files}, None


# ── Passo 1: gerar o plano (1 chamada Groq; NADA é escrito aqui) ──────────────


def generate_commit_plan(request_text, model_req=None):
    """Pede ao LLM um plano de commit para o pedido do utilizador, com um
    contexto mínimo do repositório (linguagens/estrutura) para o código sair
    na linguagem certa e no sítio certo. Devolve (plan, erro)."""
    try:
        info = get_project_info()
    except Exception:
        info = {}
    ref = (info or {}).get("default_branch") or "main"
    pname = (info or {}).get("name_with_namespace") or "?"
    try:
        langs = ", ".join(list((_get_languages() or {}).keys())[:4]) or "?"
    except Exception:
        langs = "?"
    try:
        tree = _get_repo_tree_top(ref) or []
        struct = ", ".join(e["name"] for e in tree[:12]) or "?"
    except Exception:
        struct = "?"

    gmodel, greff = _pick_model(model_req)
    payload = {
        "model": gmodel,
        "messages": [
            {"role": "system", "content":
                "És um engenheiro de software sénior. Geras código para ser "
                "commitado num repositório GitLab. Responde APENAS com um objeto "
                "JSON válido — sem cercas de código, sem texto antes ou depois:\n"
                '{"branch_slug": "kebab-curto", '
                '"commit_message": "imperativo, máx 72 chars", '
                '"summary": "1-2 frases em português de Portugal sobre o que criaste", '
                '"files": [{"path": "caminho/relativo.ext", "content": "conteúdo COMPLETO do ficheiro"}]}\n'
                "Regras: 1 a 3 ficheiros; código completo, funcional e comentado "
                "(comentários em português de Portugal); segue a linguagem e a "
                "estrutura do projeto salvo pedido em contrário; caminhos "
                "relativos, sem '..'."},
            {"role": "user", "content":
                f"PROJETO: {pname} | branch principal: {ref}\n"
                f"LINGUAGENS: {langs}\n"
                f"ESTRUTURA (topo): {struct}\n\n"
                f"PEDIDO DO UTILIZADOR:\n{request_text}"},
        ],
        "temperature": 0.2,
        "max_tokens": 4096,
    }
    if greff:
        payload["reasoning_effort"] = greff
    try:
        resp = _groq_complete(payload)
        text = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        log.warning("commit plan LLM failed: %s", e)
        return None, "Não consegui contactar o modelo — tenta de novo."
    return plan_from_llm_text(text)


# ── Passo 2: executar o plano confirmado (branch ai/* + commit + MR) ──────────


def _file_exists(path, ref):
    """O commit API exige 'create' vs 'update' conforme o ficheiro exista."""
    try:
        _gitlab_request(
            "GET",
            f"/projects/{_gl()['project']}/repository/files/"
            f"{urllib.parse.quote(path, safe='')}",
            params={"ref": ref},
        )
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def execute_commit_plan(plan):
    """Cria branch ai/* + commit + Merge Request. Corre APENAS depois da
    confirmação explícita do utilizador no cartão de pré-visualização."""
    files, err = _validate_files((plan or {}).get("files"))
    if err:
        return {"ok": False, "error": err}
    msg = (str(plan.get("commit_message") or "").strip()[:120]
           or "Alterações via SprintLab Chatbox")
    raw_branch = str(plan.get("branch") or "").removeprefix("ai/")
    base_branch = f"ai/{_slugify(raw_branch or msg)}"

    try:
        info = get_project_info()
    except Exception as e:
        return {"ok": False, "error": f"Não consegui ler o projeto: {e}"}
    ref = info.get("default_branch") or "main"
    proj = _gl()["project"]

    # 1) Branch nova a partir da principal (sufixo -2..-5 se o nome já existir).
    branch = None
    for i in range(1, 6):
        cand = base_branch if i == 1 else f"{base_branch}-{i}"
        try:
            gitlab_request("POST", f"/projects/{proj}/repository/branches",
                           body={"branch": cand, "ref": ref})
            branch = cand
            break
        except urllib.error.HTTPError as e:
            if e.code != 400:   # 400 = já existe → tenta o sufixo seguinte
                return {"ok": False, "error": f"Falha ao criar a branch ({e.code})."}
    if not branch:
        return {"ok": False, "error": "Já existem demasiadas branches ai/* com este nome."}

    # 2) Um único commit com todas as ações (create/update por ficheiro).
    actions = []
    for f in files:
        try:
            exists = _file_exists(f["path"], ref)
        except Exception:
            exists = False
        actions.append({"action": "update" if exists else "create",
                        "file_path": f["path"], "content": f["content"]})
    try:
        c = gitlab_request("POST", f"/projects/{proj}/repository/commits",
                           body={"branch": branch, "commit_message": msg,
                                 "actions": actions})
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"Falha ao criar o commit ({e.code})."}

    # 3) Merge Request — o coração do desenho: revisão humana antes do merge.
    mr_url, mr_iid = "", None
    try:
        mr = gitlab_request(
            "POST", f"/projects/{proj}/merge_requests",
            body={"source_branch": branch, "target_branch": ref, "title": msg,
                  "description": ("Gerado por IA via **SprintLab Chatbox**.\n\n"
                                  f"{plan.get('summary') or ''}\n\n"
                                  "⚠️ Código gerado por IA — rever antes do merge."),
                  "remove_source_branch": True})
        mr_url, mr_iid = mr.get("web_url") or "", mr.get("iid")
    except Exception as e:
        log.warning("MR creation failed: %s", e)  # o commit já existe — segue

    return {"ok": True, "branch": branch, "target": ref,
            "commit_sha": (c.get("short_id") or c.get("id") or "")[:8],
            "commit_url": c.get("web_url") or "",
            "mr_url": mr_url, "mr_iid": mr_iid,
            "files": [f["path"] for f in files]}
