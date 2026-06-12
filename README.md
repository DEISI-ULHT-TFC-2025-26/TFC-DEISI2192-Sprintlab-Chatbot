---
title: SprintLab TFC Chatbox
emoji: 🤖
colorFrom: purple
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# SprintLab TFC Chatbox — Cloud edition (Groq + Hugging Face Spaces)

Assistente IA do TFC **SprintLab** (Bernardo Gouveia, LEI, Universidade Lusófona).
Esta versão corre **inteiramente na cloud, grátis**: o `server.py` num Hugging Face
Docker Space e a inferência no **Groq** (`llama-3.3-70b-versatile`, com function
calling para criar/fechar/atualizar issues por linguagem natural). Sem Ollama local, sem
tunnel, sem portátil — URL público e estável (`https://<user>-<space>.hf.space`).

## Arquitetura

```
Microsoft Teams (tab)
        ↓  (iframe, URL estável *.hf.space)
  Hugging Face Space  →  server.py (proxy + GitLab + charts)
        ↓                      ↓
   Groq API              GitLab API
 (llama-3.3-70b)        (issues, MRs, commits)
```

## Estrutura do código

| Ficheiro | Responsabilidade |
|---|---|
| `server.py` | Entrypoint HTTP: rotas, streaming SSE do chat, rate-limit |
| `config.py` | Configuração env-driven (fail-fast) + prompts |
| `gitlab_api.py` | Cliente GitLab: cache TTL, multi-tenant, fetchers |
| `analytics.py` | Estatísticas, contexto do LLM, exports CSV |
| `report.py` | Relatório do projeto (determinístico) |
| `charts.py` | Dados Chart.js dos gráficos inline |
| `blame.py` | Investigação de código (git blame + diff + análise IA) |
| `code_commit.py` | Commit por IA: plano → confirmação → branch `ai/*` + commit + MR |
| `actions.py` | Escritas no GitLab (validação + executor único) |
| `llm.py` | Cliente Groq (modelos, parsing) |
| `chatbox.html` / `style.css` / `app.js` | Frontend (3 temas, workspaces, cards) |
| `tests/` | 142 testes (unitários + integração HTTP) — `python -m pytest` |

## Deploy (uma vez)

1. **Cria uma chave Groq** (grátis, sem cartão): https://console.groq.com/keys
2. **Cria um Space**: huggingface.co → New → Space → **Docker** (Blank), visibilidade *Public*.
3. **Envia estes ficheiros** para o Space (git push ou upload):
   `Dockerfile`, **todos os `.py` da raiz** (`server.py`, `config.py`,
   `gitlab_api.py`, `analytics.py`, `report.py`, `charts.py`, `blame.py`,
   `code_commit.py`, `actions.py`, `llm.py`), `chatbox.html`, `style.css`,
   `app.js`, `README.md`, `requirements.txt`. (A pasta `tests/` não é preciso enviar.)
4. **Define os Secrets** em *Settings → Variables and secrets*:
   - `GROQ_API_KEY` = `gsk_...`
   - `GITLAB_TOKEN` = `glpat-...`
   - (opcional) `GITLAB_PROJECT_ID`, `GROQ_MODEL`, `GROQ_REASONING_EFFORT`
5. O Space faz **build** e arranca sozinho. Quando ficar *Running*, o chatbox está em
   `https://<user>-<space>.hf.space`.

## Ligar ao Teams

Aponta o `manifest.json` para o URL do Space (host = `<user>-<space>.hf.space`).
Podes reutilizar o `package.ps1` da pasta `chatbox/`:

```powershell
.\teams-package\package.ps1 -NgrokHost "bernardo-sprintlab.hf.space"
```

Como o URL do Space **não muda**, fazes isto **uma única vez** — nunca mais mexes no Teams.

## Variáveis de ambiente

| Variável | Obrigatória | Default | Notas |
|---|---|---|---|
| `GROQ_API_KEY` | sim | — | Chave Groq (`gsk_...`) |
| `GITLAB_TOKEN` | sim | — | Token GitLab (`glpat-...`) |
| `GITLAB_PROJECT_ID` | não | `80767095` | ID do projeto GitLab |
| `GROQ_MODEL` | não | `llama-3.3-70b-versatile` | Tool calling fiável. Alt.: `qwen/qwen3-32b` (+ `GROQ_REASONING_EFFORT=none`) |
| `GROQ_REASONING_EFFORT` | não | `""` (vazio) | Vazio para Llama. Para Qwen3 põe `none` (respostas diretas, sem `<think>`) |
| `CACHE_TTL` | não | `45` | Segundos de cache das chamadas GitLab |
| `GITLAB_PAGE_LIMIT` | não | `5` | Máx. páginas (×100 issues) |
| `RATE_LIMIT` | não | `20` | Pedidos/min por IP nos endpoints que usam o Groq (0 = desligado) |
| `README_MAX` | não | `1500` | Caracteres do excerto do README no contexto do LLM |

> O `GROQ_REASONING_EFFORT` só se aplica a modelos *reasoning* (ex. Qwen3). Com o
> default Llama deixa-o vazio — senão o Groq rejeita o parâmetro.

## Correr localmente (dev)

```powershell
$env:GROQ_API_KEY="gsk_..."
$env:GITLAB_TOKEN="glpat-..."
$env:PORT="8080"
python server.py
# abre http://localhost:8080
```

## Notas

- Limites do free tier Groq são **por modelo** (cada modelo tem a sua quota de
  req/min e tokens/min) — se um modelo atingir o limite, troca de modelo nas
  definições (⚙️) e continuas com quota fresca. O servidor também aplica um
  rate-limit por IP (`RATE_LIMIT`) para um único cliente não esgotar a quota.
- O Space adormece após ~48h sem tráfego; o primeiro pedido a seguir tem um arranque curto.
- Segredos nunca ficam no repositório — só nos Secrets do Space.
- Workspaces e conversas vivem no `localStorage` do browser; usa os botões
  **Backup / Repor** (rodapé da barra lateral) para os levar para outro browser.

## Testes

```bash
python -m pip install -r requirements-dev.txt
python -m pytest        # 142 testes: unitários + integração HTTP (sem rede)
```
