# SprintLab — TFC Chatbox

Assistente IA local integrado com GitLab e Microsoft Teams, desenvolvido por **Bernardo Gouveia** para o TFC da Universidade Lusófona (LEI).

---

## Arquitetura

```
Microsoft Teams (tab)
        ↓
  ngrok tunnel
        ↓
  chatbox (porta 8080)   ←→   Ollama (porta 11434)
        ↓
  GitLab API (gitlab.com)
```

---

## Pré-requisitos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [Ollama](https://ollama.com/) instalado e a correr
- [ngrok](https://ngrok.com/) instalado
- Conta GitLab com token de acesso (`glpat-...`)

---

## Instalação rápida

### 1. Instalar o modelo Ollama

```powershell
ollama pull qwen2.5:7b
```

### 2. Clonar o repositório

```powershell
git clone https://gitlab.com/BernardoGouveia/bernardogouveia-tfc.git
cd bernardogouveia-tfc
```

### 3. Configurar variáveis de ambiente

Cria um ficheiro `.env` na pasta raiz:

```env
GITLAB_TOKEN=glpat-SEU_TOKEN_AQUI
GITLAB_PROJECT_ID=80767095
DATABASE_URL=postgresql://...
```

### 4. Correr com Docker

```powershell
# Construir e iniciar todos os serviços
docker-compose up --build -d

# Ver logs
docker-compose logs -f chatbox

# Parar
docker-compose down
```

### 5. Expor com ngrok

```powershell
ngrok http 8080
```

Copia o URL público (ex: `https://xxxx.ngrok-free.app`).

---

## Correr sem Docker (desenvolvimento)

```powershell
# Terminal 1 — Ollama
ollama serve

# Terminal 2 — Chatbox
cd chatbox
python server.py

# Terminal 3 — ngrok
ngrok http 8080
```

---

## Funcionalidades do Chatbox

| Comando | Resultado |
|---|---|
| `O que é o SprintLab?` | Responde com base no relatório TFC |
| `Quantas issues estão abertas?` | Consulta o GitLab em tempo real |
| `Quem tem mais issues atribuídas?` | Análise por assignee |
| `Qual o progresso do sprint?` | % de issues fechadas |
| `Issues em atraso?` | Lista issues com due date ultrapassada |
| `Cria uma issue com o título 'X'` | Cria issue no GitLab |
| `Fecha a issue #5` | Fecha issue no GitLab |
| `Muda o título da issue #2 para 'X'` | Atualiza issue no GitLab |
| `Exporta todas as issues para CSV` | Descarrega ficheiro CSV |

---

## Carregar no Microsoft Teams

1. Vai a **Teams → Aplicações → Carregar uma aplicação**
2. Seleciona `sprintlab-chatbox.zip`
3. Adiciona como tab no canal desejado

O ficheiro `sprintlab-chatbox.zip` contém:
- `manifest.json`
- `icon-color.png`
- `icon-outline.png`

---

## Estrutura do projeto

```
bernardogouveia-tfc/
├── chatbox/
│   ├── chatbox.html          # Interface do chatbox
│   ├── server.py             # Servidor Python (proxy + GitLab)
│   └── Dockerfile.chatbox    # Docker do chatbox
├── tabs/
│   ├── board.html            # Kanban board
│   ├── dashboard.html        # Dashboard/Gantt
│   └── main.html             # Shell principal
├── routes/
│   ├── gitlab.js             # API GitLab
│   ├── gitlab_dashboard.js   # Dados Gantt
│   ├── teams.js              # Verificação de roles
│   └── webhooks.js           # Webhooks GitLab
├── services/
│   └── db.js                 # PostgreSQL
├── server.js                 # Middleware Express principal
├── Dockerfile                # Docker do middleware
├── docker-compose.yml        # Stack completo
└── README.md                 # Este ficheiro
```

---

## Tecnologias

| Tecnologia | Função |
|---|---|
| Express.js | Middleware backend |
| Microsoft Teams API | Plugin Teams (Kanban, Gantt, Chatbox) |
| GitLab API | Issues, webhooks, milestones |
| Ollama + qwen2.5:7b | Modelo IA local |
| PostgreSQL | Base de dados de configurações |
| Docker | Containerização |
| ngrok | Tunnel para exposição local |

---

## Autor

**Bernardo Gouveia** — Universidade Lusófona, LEI, 2025/2026  
Orientador: Daniel Silveira
