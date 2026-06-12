# Testes automatizados

142 testes em dois níveis, **sem rede** (o GitLab e o Groq nunca são chamados):

1. **Unitários** — lógica pura dos módulos (sanitização, agregações, CSV,
   relatório, blame, repo overview).
2. **Integração** (`test_endpoints.py`) — um servidor HTTP **real** arranca numa
   thread e todas as rotas são exercitadas ponta-a-ponta; só as duas funções de
   rede (`_gitlab_request` / `_groq_complete`) são substituídas por fakes.

## Correr

```bash
python -m pip install -r requirements-dev.txt
python -m pytest            # a partir da pasta chatbox-cloud/
```

## O que é coberto

| Ficheiro | Foco |
|---|---|
| `test_helpers.py`      | `_norm_iid`, `_clean_labels`, `_valid_due_date`, `_int_ids`, `_words`, `_strip_think`, `_parse_suggestions`, `_pick_model`, `_action_summary` |
| `test_stats.py`        | `_issue_stats` — abertas/fechadas, progresso, em atraso, sem assignee |
| `test_sprint_report.py`| `build_sprint_report` + `_due_status` — números exatos, destaques, markdown, resiliência |
| `test_csv.py`          | `issues_to_csv` / `commits_to_csv` — BOM para Excel, cabeçalhos, linhas |
| `test_blame.py`        | investigação de código — `_path_candidates`, `_blame_owner`, `_blame_snippet`, `_diff_excerpt` |
| `test_repo_overview.py`| visão geral do repo — limpeza do README, índice, linhas do contexto |
| `test_endpoints.py`    | **integração HTTP**: estáticos, stats, report, exports, charts, multi-tenant (X-GL-*), confirm-action, SSE do chat, rate-limit |

As datas são injetadas (`today=...`) para os testes de "em atraso" serem
determinísticos. `conftest.py` define `GITLAB_TOKEN`/`GROQ_API_KEY` falsos antes
de importar o `server`, que de outra forma sai logo por falta de Secrets.
