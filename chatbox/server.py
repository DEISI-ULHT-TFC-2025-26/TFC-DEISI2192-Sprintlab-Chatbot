from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.parse
import json
import csv
import io
from datetime import datetime, timezone

NGROK_URL = "https://unglamourously-hypoxic-jaylah.ngrok-free.dev"

# GitLab config — substitui SEU_TOKEN_AQUI pelo teu token glpat-...
GITLAB_TOKEN = "glpat-1DkPD2ghY1LOXmQyxB6nZGM6MQpvOjEKdTppcWU1OA8.01.1707f314u"
GITLAB_PROJECT_ID = "80767095"
GITLAB_BASE = "https://gitlab.com/api/v4"

DOCUMENT_CONTEXT = """
=== DOCUMENTO: RELATÓRIO INTERCALAR TFC — SprintLab ===
Autor: Bernardo Gouveia | Orientador: Daniel Silveira | LEI | Universidade Lusófona | 29/11/2025

O SprintLab é um middleware e plugin para Microsoft Teams que integra o GitLab, com sincronização bidirecional de issues, tarefas, labels, gráficos e eventos, com quadros Kanban e gráficos de Gantt diretamente no Teams.

NOVOS OBJETIVOS DE IA:
1. IA Chatbox: interação por linguagem natural para consulta, criação e atualização de tarefas.
2. Geração automática de relatórios via IA: sumários de sprint, métricas, evolução de tarefas.

IDENTIFICAÇÃO DO PROBLEMA:
- Falta de visibilidade: atualizações no GitLab não refletidas em tempo real no Teams.
- Duplicação de esforços: atualização manual em plataformas diferentes.
- Parceiro empresarial: GMV.

TECNOLOGIAS: Express.js, Microsoft Teams API, GitLab API, IA Chatbox (qwen2.5:7b via Ollama).

BENCHMARKING: SprintLab exclusivo: Chatbox IA, Sincronização bidirecional GitLab-Teams, Relatórios automáticos IA, NLP.

VIABILIDADE: 85% melhoraria eficiência, 70% Kanban+Gantt essenciais, 90% interesse em automação. Modelo SaaS.

GLOSSÁRIO: LEI=Licenciatura Eng. Informática, TFC=Trabalho Final de Curso, SaaS=Software as a Service, NLP=Natural Language Processing.
"""

SYSTEM_PROMPT = f"""És o assistente do projeto TFC SprintLab da Universidade Lusófona, desenvolvido por Bernardo Gouveia.
Tens acesso ao documento oficial do projeto e aos dados em tempo real do repositório GitLab, incluindo análises pré-calculadas.
Responde SEMPRE em português de Portugal. Sê direto, claro e útil.
Usa os dados de análise fornecidos para responder com precisão. Não inventes dados.

{DOCUMENT_CONTEXT}
"""

# ── GitLab helpers ────────────────────────────────────────────────────────────
def gitlab_request(method, endpoint, body=None, params=None):
    url = f"{GITLAB_BASE}{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN, "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def get_all_issues(state="all"):
    return gitlab_request("GET", f"/projects/{GITLAB_PROJECT_ID}/issues",
                          params={"state": state, "per_page": 100})

def analyse_issues(issues):
    """Análise pré-calculada dos dados para enriquecer o contexto do modelo."""
    now = datetime.now(timezone.utc)

    # Contagens base
    total = len(issues)
    opened = [i for i in issues if i['state'] == 'opened']
    closed = [i for i in issues if i['state'] == 'closed']

    # Issues em atraso (due_date < hoje e ainda abertas)
    overdue = []
    for i in opened:
        if i.get('due_date'):
            due = datetime.fromisoformat(i['due_date'])
            if due.replace(tzinfo=timezone.utc) < now:
                overdue.append(i)

    # Issues sem assignee
    no_assignee = [i for i in opened if not i.get('assignee')]

    # Issues por assignee
    by_assignee = {}
    for i in opened:
        name = i['assignee']['name'] if i.get('assignee') else 'Sem assignee'
        by_assignee[name] = by_assignee.get(name, 0) + 1
    by_assignee_sorted = sorted(by_assignee.items(), key=lambda x: x[1], reverse=True)

    # Issues por label
    by_label = {}
    for i in opened:
        for label in i.get('labels', []) or ['Sem label']:
            by_label[label] = by_label.get(label, 0) + 1
    by_label_sorted = sorted(by_label.items(), key=lambda x: x[1], reverse=True)

    # Issue mais antiga (aberta)
    oldest = None
    if opened:
        oldest = min(opened, key=lambda i: i['created_at'])

    # Issue mais recente (aberta)
    newest = None
    if opened:
        newest = max(opened, key=lambda i: i['created_at'])

    # Progresso geral
    progress = round((len(closed) / total * 100), 1) if total > 0 else 0

    return {
        "total": total,
        "abertas": len(opened),
        "fechadas": len(closed),
        "progresso": f"{progress}%",
        "em_atraso": len(overdue),
        "sem_assignee": len(no_assignee),
        "por_assignee": by_assignee_sorted,
        "por_label": by_label_sorted[:5],
        "mais_antiga": f"#{oldest['iid']} '{oldest['title']}' (criada em {oldest['created_at'][:10]})" if oldest else "N/A",
        "mais_recente": f"#{newest['iid']} '{newest['title']}' (criada em {newest['created_at'][:10]})" if newest else "N/A",
        "overdue_list": [f"#{i['iid']} {i['title']} (due: {i['due_date']})" for i in overdue[:5]],
        "no_assignee_list": [f"#{i['iid']} {i['title']}" for i in no_assignee[:5]],
    }

def get_gitlab_context():
    try:
        all_issues = get_all_issues("all")
        opened_issues = [i for i in all_issues if i['state'] == 'opened']
        analysis = analyse_issues(all_issues)

        milestones = gitlab_request("GET", f"/projects/{GITLAB_PROJECT_ID}/milestones",
                                    params={"state": "active"})
        milestones_text = "\n".join([
            f"  - {m['title']} (due: {m.get('due_date') or 'Sem data'})"
            for m in milestones
        ]) or "  Nenhum milestone ativo."

        issues_text = "\n".join([
            f"  #{i['iid']} [{i['state']}] {i['title']} | assignee: {i['assignee']['name'] if i.get('assignee') else 'Nenhum'} | due: {i.get('due_date') or 'N/A'} | labels: {', '.join(i.get('labels', [])) or 'Nenhuma'}"
            for i in opened_issues
        ]) or "  Nenhuma issue aberta."

        assignee_text = "\n".join([f"  {name}: {count} issues" for name, count in analysis['por_assignee']])
        label_text = "\n".join([f"  {label}: {count} issues" for label, count in analysis['por_label']])
        overdue_text = "\n".join([f"  {i}" for i in analysis['overdue_list']]) or "  Nenhuma em atraso."
        no_assignee_text = "\n".join([f"  {i}" for i in analysis['no_assignee_list']]) or "  Todas têm assignee."

        return f"""
=== DADOS ATUAIS DO GITLAB (projeto {GITLAB_PROJECT_ID}) ===

RESUMO:
  Total de issues: {analysis['total']}
  Abertas: {analysis['abertas']} | Fechadas: {analysis['fechadas']}
  Progresso geral: {analysis['progresso']}
  Em atraso: {analysis['em_atraso']}
  Sem assignee: {analysis['sem_assignee']}
  Issue mais antiga: {analysis['mais_antiga']}
  Issue mais recente: {analysis['mais_recente']}

ISSUES POR ASSIGNEE:
{assignee_text or '  Nenhum assignee.'}

ISSUES POR LABEL:
{label_text or '  Nenhuma label.'}

ISSUES EM ATRASO:
{overdue_text}

ISSUES SEM ASSIGNEE:
{no_assignee_text}

MILESTONES ATIVOS:
{milestones_text}

LISTA DE ISSUES ABERTAS:
{issues_text}
"""
    except Exception as e:
        return f"\n=== GITLAB: Erro ao carregar dados: {str(e)} ===\n"

def issues_to_csv(issues):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Título", "Estado", "Assignee", "Labels", "Due Date", "Criada em", "URL"])
    for i in issues:
        writer.writerow([
            f"#{i['iid']}", i['title'], i['state'],
            i['assignee']['name'] if i.get('assignee') else '',
            ', '.join(i.get('labels', [])),
            i.get('due_date') or '',
            i['created_at'][:10], i['web_url']
        ])
    return output.getvalue()

# ── HTTP Handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('X-Frame-Options', 'ALLOWALL')
        self.send_header('Content-Security-Policy',
            "frame-ancestors 'self' https://teams.microsoft.com https://*.teams.microsoft.com https://*.skype.com")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path in ['/', '/chatbox.html']:
            try:
                with open('chatbox.html', 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'chatbox.html not found')

        elif self.path.startswith('/gitlab/export'):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            state = params.get('state', ['all'])[0]
            print(f"\n{'='*50}")
            print(f"📤 EXPORTAR ISSUES (state={state})")
            try:
                issues = get_all_issues(state)
                csv_data = issues_to_csv(issues)
                print(f"   ✅ {len(issues)} issues exportadas")
                print(f"{'='*50}")
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv; charset=utf-8')
                self.send_header('Content-Disposition', 'attachment; filename="gitlab_issues.csv"')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(csv_data.encode('utf-8'))
            except Exception as e:
                print(f"   ❌ Erro: {e}")
                self.send_response(500)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers['Content-Length'])
        body = self.rfile.read(length)

        if self.path == '/gitlab/issues':
            try:
                data = json.loads(body)
                print(f"\n{'='*50}")
                print(f"🆕 CRIAR ISSUE: {data.get('title', '?')}")
                result = gitlab_request("POST", f"/projects/{GITLAB_PROJECT_ID}/issues", body=data)
                print(f"   ✅ Issue #{result['iid']} criada: {result['title']}")
                print(f"   🔗 {result['web_url']}")
                print(f"{'='*50}")
                self._json(result)
            except Exception as e:
                print(f"   ❌ Erro: {e}")
                self._error(str(e))

        elif self.path.startswith('/gitlab/issues/') and self.path.endswith('/close'):
            try:
                iid = self.path.split('/')[3]
                print(f"\n{'='*50}")
                print(f"🔒 FECHAR ISSUE #{iid}")
                result = gitlab_request("PUT", f"/projects/{GITLAB_PROJECT_ID}/issues/{iid}",
                                        body={"state_event": "close"})
                print(f"   ✅ Issue #{iid} fechada: {result['title']}")
                print(f"{'='*50}")
                self._json(result)
            except Exception as e:
                print(f"   ❌ Erro: {e}")
                self._error(str(e))

        elif self.path.startswith('/gitlab/issues/') and self.path.endswith('/update'):
            try:
                iid = self.path.split('/')[3]
                data = json.loads(body)
                print(f"\n{'='*50}")
                print(f"✏️  ATUALIZAR ISSUE #{iid}: {data}")
                result = gitlab_request("PUT", f"/projects/{GITLAB_PROJECT_ID}/issues/{iid}", body=data)
                print(f"   ✅ Issue #{iid} atualizada")
                print(f"{'='*50}")
                self._json(result)
            except Exception as e:
                print(f"   ❌ Erro: {e}")
                self._error(str(e))

        elif self.path == '/api/generate':
            try:
                data = json.loads(body)
                user_prompt = data.get('prompt', '').split('Utilizador:')[-1].split('Assistente:')[0].strip()
                print(f"\n{'='*50}")
                print(f"💬 PERGUNTA: {user_prompt[:120]}")
                print(f"   \U0001f4ac Pergunta sobre: {user_prompt[:60]}")

                # Detecao inteligente: so vai ao GitLab se necessario
                gitlab_keywords = [
                    "issue", "issues", "sprint", "assignee", "milestone",
                    "progresso", "atraso", "fechad", "abert", "label",
                    "taref", "gitlab", "commit", "merge", "board",
                    "quantas", "quem", "lista", "resumo", "estado"
                ]
                needs_gitlab = any(kw in user_prompt.lower() for kw in gitlab_keywords)

                if needs_gitlab:
                    print(f"   \U0001f98a Pergunta sobre GitLab - a buscar dados...")
                    gitlab_ctx = get_gitlab_context()
                    print(f"   \U0001f98a GitLab: {len(gitlab_ctx)} chars carregados")
                else:
                    gitlab_ctx = ""
                    print(f"   \U0001f4c4 Pergunta sobre documento TFC - sem GitLab (~{len(SYSTEM_PROMPT)} chars)")

                original_prompt = data.get("prompt", "")
                data["prompt"] = SYSTEM_PROMPT + gitlab_ctx + "\n\n" + original_prompt
                print(f"   \U0001f4e4 Prompt total: {len(data['prompt'])} chars -> Ollama")

                req = urllib.request.Request(
                    'http://localhost:11434/api/generate',
                    data=json.dumps(data).encode(),
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=120) as r:
                    resp = r.read()

                response_data = json.loads(resp)
                reply = response_data.get('response', '')
                print(f"   📥 Resposta ({len(reply)} chars): {reply[:100]}...")
                print(f"   ⏱️  Tempo: {response_data.get('total_duration', 0) // 1_000_000}ms")
                print(f"{'='*50}")

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(resp)

            except Exception as e:
                print(f"   ❌ Erro: {e}")
                self._error(str(e))
        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _error(self, msg):
        self.send_response(500)
        self.send_header('Content-Type', 'application/json')
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps({'error': msg}).encode())

    def log_message(self, format, *args):
        pass

if __name__ == '__main__':
    port = 8080
    print(f"\n{'='*50}")
    print(f"✅ Servidor a correr em http://localhost:{port}")
    print(f"🌍 Público em {NGROK_URL}")
    print(f"📄 Documento carregado ({len(DOCUMENT_CONTEXT)} caracteres)")
    print(f"🦊 GitLab project: {GITLAB_PROJECT_ID}")
    print(f"{'='*50}\n")
    HTTPServer(('', port), Handler).serve_forever()
