# SprintLab — TFC Chatbox

Assistente IA local integrado com GitLab e Microsoft Teams, desenvolvido por **Bernardo Gouveia** para o TFC da Universidade Lusófona (LEI).

---

## Arquitetura

```
Microsoft Teams (tab chatbox)
        ↓  HTTPS
  ngrok tunnel (:443 → :8080)
        ↓
  server.py  (:8080)
  ├── GET  /chatbox.html          → serve a interface
  ├── POST /api/chat              → proxy streaming → Ollama
  ├── POST /gitlab/issues         → criar issue
  ├── POST /gitlab/issues/:id/close   → fechar issue
  ├── POST /gitlab/issues/:id/update  → atualizar issue
  └── GET  /gitlab/export         → exportar CSV
        ↓                    ↓
  Ollama qwen2.5:7b       GitLab API
     (:11434)             (gitlab.com/api/v4)
```

---

## Pré-requisitos

| Componente | Versão | Notas |
|---|---|---|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | v4.0+ | Obrigatório para setup com Docker |
| [Ollama](https://ollama.com/) | v0.18+ | Deve estar a correr no host |
| [ngrok](https://ngrok.com/) | v3.37+ | Conta gratuita — 1 tunnel por vez |
| Token GitLab | `glpat-...` | Scopes: `api`, `read_api` |
| Python | 3.11+ | Apenas sem Docker |

---

## Instalação — Setup com Docker (recomendado)

### 1. Instalar o modelo Ollama

```powershell
ollama pull qwen2.5:7b
```

> **Nota Windows:** O Ollama deve estar a correr no host antes de iniciar o Docker. Verifica com `ollama list`.

### 2. Clonar o repositório

```powershell
git clone https://github.com/DEISI-ULHT-TFC-2025-26/TFC-DEISI2192-Sprintlab-Chatbot.git
cd TFC-DEISI2192-Sprintlab-Chatbot
```

### 3. Criar o ficheiro `.env`

Cria `.env` na raiz do projeto (nunca commites este ficheiro):

```env
# GitLab
GITLAB_TOKEN=glpat-SEU_TOKEN_AQUI
GITLAB_PROJECT_ID=80767095

# Middleware Express
DATABASE_URL=postgresql://user:pass@host:5432/db
```

> Para obter o token: **GitLab → Foto → Edit Profile → Access Tokens → Add new token** (scopes: `api`, `read_api`)

### 4. Construir e iniciar com Docker

```powershell
# Primeira vez (constrói as imagens)
docker-compose up --build -d

# Ver logs do chatbox em tempo real
docker-compose logs -f chatbox

# Parar
docker-compose down
```

### 5. Expor com ngrok

```powershell
ngrok http 8080
```

Copia o URL público (ex: `https://xxxx.ngrok-free.app`).

> **Importante:** Se o URL do ngrok mudar, tens de atualizar o `manifest.json` e recarregar o zip no Teams.

### 6. Testar no browser

Abre `https://SEU-URL.ngrok-free.app` — deves ver o chatbox.

---

## Instalação — Sem Docker (desenvolvimento local)

Abre **3 terminais separados**:

```powershell
# Terminal 1 — Ollama (se não estiver já a correr)
ollama serve

# Terminal 2 — server.py
cd chatbox
python server.py

# Terminal 3 — ngrok
ngrok http 8080
```

> O `server.py` lê as variáveis de ambiente do `.env` ou podes definir diretamente:
> ```powershell
> $env:GITLAB_TOKEN = "glpat-SEU_TOKEN"
> $env:GITLAB_PROJECT_ID = "80767095"
> python server.py
> ```

---

## Comandos do Chatbox

### Perguntas sobre o documento TFC

| Comando | Resultado |
|---|---|
| `O que é o SprintLab?` | Resposta baseada no relatório intercalar |
| `Que tecnologias são usadas?` | Lista de tecnologias do projeto |
| `O que diz o benchmarking?` | Comparação com concorrentes |
| `Quais os resultados do inquérito de viabilidade?` | Dados do inquérito (85%, 70%, 90%) |

### Queries ao GitLab (tempo real)

| Comando | Resultado |
|---|---|
| `Quantas issues estão abertas?` | Número real do GitLab (todas as páginas) |
| `Qual o progresso do sprint?` | % de issues fechadas vs total |
| `Quem tem mais issues atribuídas?` | Ranking por assignee |
| `Há issues em atraso?` | Lista com due_date ultrapassado |
| `Qual a issue mais antiga?` | Issue com created_at mais antigo |
| `Faz um resumo do projeto` | Métricas completas |

### CRUD de Issues

| Comando | Resultado |
|---|---|
| `Cria uma issue com o título 'X'` | Issue criada no GitLab |
| `Cria issue: X` | Alternativa mais curta |
| `Fecha a issue #5` | Issue fechada no GitLab |
| `Muda o título da issue #2 para 'X'` | Título atualizado |

> Podes criar issues com mais detalhe numa só mensagem:
> ```
> Cria issue: Implementar autenticação OAuth2
> Descrição: Adicionar OAuth2 ao middleware Express
> Due date: 2025-10-31
> ```

### Exportação

| Comando | Resultado |
|---|---|
| `Exporta todas as issues para CSV` | Download `gitlab_issues.csv` (todas) |
| `Exporta as issues abertas para CSV` | Só issues abertas |
| `Exporta as issues fechadas para CSV` | Só issues fechadas |

---

## Comandos Docker úteis

```powershell
# Ver estado dos containers
docker-compose ps

# Reiniciar só o chatbox (após alterar server.py)
docker-compose restart chatbox

# Ver logs em tempo real
docker-compose logs -f chatbox

# Reconstruir após mudanças no Dockerfile
docker-compose up --build -d

# Parar e remover containers
docker-compose down

# Ver uso de recursos
docker stats
```

---

## Resolução de Problemas

| Problema | Solução |
|---|---|
| `Erro ao ligar ao modelo` | Verifica se o Ollama está a correr: `ollama list` |
| `HTTP 403 GitLab` | Token inválido — cria novo em gitlab.com/profile/tokens |
| `ERR_NGROK_334` | Fecha o tunnel em dashboard.ngrok.com/tunnels |
| `port already in use` | `docker-compose down` e depois `up -d` |
| Issues incompletas | Garante que usas o `server.py` mais recente (paginação activa) |
| Chatbox não carrega no Teams | URL do ngrok desatualizado no `manifest.json` |

---

## Estrutura do Projeto com o SprintLab

```
bernardogouveia-tfc/
├── chatbox/
│   ├── chatbox.html          # Interface do chatbox (streaming, detecção de intenções)
│   ├── server.py             # Servidor Python — proxy Ollama + CRUD GitLab + CSV
│   └── Dockerfile.chatbox    # Imagem Docker do chatbox
├── tabs/
│   ├── board.html            # Kanban board sincronizado com GitLab
│   ├── dashboard.html        # Dashboard / Gráfico de Gantt
│   └── main.html             # Shell principal com tabs
├── routes/
│   ├── gitlab.js             # API GitLab — issues, boards, milestones
│   ├── gitlab_dashboard.js   # Dados para o Gantt
│   ├── teams.js              # Verificação de roles Teams
│   └── webhooks.js           # Receção de webhooks GitLab
├── services/
│   └── db.js                 # Pool PostgreSQL
├── server.js                 # Middleware Express principal
├── Dockerfile                # Imagem Docker do middleware Express
├── docker-compose.yml        # Orquestração completa (chatbox + middleware)
├── .env                      # Variáveis de ambiente — NÃO commitar!
├── .gitignore                # Deve incluir .env
└── README.md                 # Este ficheiro
```

---

## Tecnologias

| Tecnologia | Versão | Função |
|---|---|---|
| Express.js | v4 | Middleware backend — APIs RESTful, webhooks |
| Microsoft Teams API | v2.12 | Plugin Teams — Kanban, Gantt, Chatbox |
| GitLab API | v4 | Issues, milestones, labels, webhooks |
| Ollama + qwen2.5:7b | v0.18+ / 4.7GB | Modelo LLM local — NLP em português |
| Python | 3.11 | Servidor do chatbox (server.py) |
| PostgreSQL | — | Base de dados de configurações por canal |
| Docker | v4+ | Containerização do chatbox e middleware |
| ngrok | v3.37+ | Tunnel HTTPS para exposição local |

---

## Autor

**Bernardo Gouveia** — Universidade Lusófona, LEI, 2025/2026  
Orientador: Daniel Silveira  
Repositório: [gitlab.com/BernardoGouveia/bernardogouveia-tfc](https://gitlab.com/BernardoGouveia/bernardogouveia-tfc)
