from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.parse
import json
import csv
import io
from datetime import datetime, timezone

NGROK_URL = "https://unglamourously-hypoxic-jaylah.ngrok-free.dev"

GITLAB_TOKEN = "glpat-1DkPD2ghY1LOXmQyxB6nZGM6MQpvOjEKdTppcWU1OA8.01.1707f314u"
GITLAB_PROJECT_ID = "80767095"
GITLAB_BASE = "https://gitlab.com/api/v4"

DOCUMENT_CONTEXT = """=== RELATÓRIO INTERCALAR TFC — SprintLab ===
Autor: Bernardo Gouveia | Orientador: Daniel Silveira | LEI | Universidade Lusófona | 2025/2026

DESCRIÇÃO: O SprintLab é um middleware e plugin para Microsoft Teams que integra o GitLab com sincronização bidirecional de issues, Kanban, Gantt e IA conversacional local.

PROBLEMA: Falta de integração entre GitLab e Microsoft Teams gera processos fragmentados, duplicação de tarefas e perda de eficiência. Parceiro: GMV.

SOLUÇÃO: Middleware Express.js + plugin Teams + chatbox IA com qwen2.5:7b via Ollama (local, sem cloud).

TECNOLOGIAS: Express.js (middleware), Microsoft Teams API (plugin), GitLab API (webhooks/issues), Ollama qwen2.5:7b (IA local), PostgreSQL (configurações), Docker, ngrok.

BENCHMARKING: SprintLab é o único com Chatbox IA + NLP + Sincronização bidirecional GitLab↔Teams + Relatórios automáticos IA.

VIABILIDADE: 85% melhoraria eficiência, 70% Kanban+Gantt essenciais, 90% interesse em automação. Redução de 40% em tarefas administrativas. Modelo SaaS.

FUNCIONALIDADES IA: (1) Chatbox NLP — criar/fechar/atualizar issues, queries analíticas, exportar CSV. (2) Relatórios automáticos de sprint. (3) Motor IA↔GitLab↔Teams.

GLOSSÁRIO: LEI=Licenciatura Eng. Informática, TFC=Trabalho Final de Curso, SaaS=Software as a Service, NLP=Natural Language Processing."""

SYSTEM_PROMPT = """És o assistente do projeto TFC SprintLab da Universidade Lusófona, desenvolvido por Bernardo Gouveia.
Responde SEMPRE em português de Portugal. Sê direto, claro e conciso.
Usa os dados fornecidos. Não inventes informação.
Quando tiveres dados do GitLab, usa-os para responder com precisão.
Para ações (criar/fechar issues), confirma o que foi feito de forma clara."""

GITLAB_KEYWORDS = [
    'issue', 'issues','assignee', 'milestone',
    'progresso', 'atraso', 'fechad', 'abert', 'label',
    'taref', 'gitlab', 'commit', 'merge', 'board',
    'quantas', 'quem', 'lista', 'resumo', 'estado', 'projeto'
]

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

def get_gitlab_context():
    try:
        all_issues = get_all_issues("all")
        opened = [i for i in all_issues if i['state'] == 'opened']
        closed  = [i for i in all_issues if i['state'] == 'closed']
        now = datetime.now(timezone.utc)

        overdue = [i for i in opened if i.get('due_date') and
                   datetime.fromisoformat(i['due_date']).replace(tzinfo=timezone.utc) < now]

        by_assignee = {}
        for i in opened:
            name = i['assignee']['name'] if i.get('assignee') else 'Sem assignee'
            by_assignee[name] = by_assignee.get(name, 0) + 1

        progress = round(len(closed) / len(all_issues) * 100, 1) if all_issues else 0

        issues_list = "\n".join([
            f"  #{i['iid']} {i['title']} | {i['assignee']['name'] if i.get('assignee') else 'Nenhum'} | due:{i.get('due_date') or 'N/A'} | {','.join(i.get('labels',[])[:2]) or 'sem label'}"
            for i in opened[:20]
        ]) or "  Nenhuma issue aberta."

        assignee_rank = "\n".join([f"  {n}: {c}" for n,c in sorted(by_assignee.items(), key=lambda x:-x[1])])
        overdue_list  = "\n".join([f"  #{i['iid']} {i['title']} (due:{i['due_date']})" for i in overdue[:5]]) or "  Nenhuma."

        milestones = gitlab_request("GET", f"/projects/{GITLAB_PROJECT_ID}/milestones", params={"state":"active"})
        ms_list = "\n".join([f"  {m['title']} (due:{m.get('due_date','N/A')})" for m in milestones]) or "  Nenhum."

        return f"""
=== DADOS GITLAB (tempo real) ===
Total: {len(all_issues)} | Abertas: {len(opened)} | Fechadas: {len(closed)} | Progresso: {progress}%
Em atraso: {len(overdue)} | Sem assignee: {sum(1 for i in opened if not i.get('assignee'))}

ISSUES ABERTAS:
{issues_list}

RANKING ASSIGNEES:
{assignee_rank or '  Nenhum.'}

EM ATRASO:
{overdue_list}

MILESTONES:
{ms_list}"""
    except Exception as e:
        return f"\n=== GITLAB: erro ao carregar ({e}) ===\n"

def issues_to_csv(issues):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ID","Título","Estado","Assignee","Labels","Due Date","Criada em","URL"])
    for i in issues:
        w.writerow([f"#{i['iid']}", i['title'], i['state'],
                    i['assignee']['name'] if i.get('assignee') else '',
                    ','.join(i.get('labels',[])), i.get('due_date',''),
                    i['created_at'][:10], i['web_url']])
    return out.getvalue()

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
            print(f"\n📤 EXPORTAR CSV (state={state})")
            try:
                issues = get_all_issues(state)
                csv_data = issues_to_csv(issues)
                print(f"   ✅ {len(issues)} issues exportadas")
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
                print(f"\n🆕 CRIAR ISSUE: {data.get('title')}")
                result = gitlab_request("POST", f"/projects/{GITLAB_PROJECT_ID}/issues", body=data)
                print(f"   ✅ #{result['iid']} criada — {result['web_url']}")
                self._json(result)
            except Exception as e:
                print(f"   ❌ {e}"); self._error(str(e))

        elif self.path.startswith('/gitlab/issues/') and self.path.endswith('/close'):
            try:
                iid = self.path.split('/')[3]
                print(f"\n🔒 FECHAR ISSUE #{iid}")
                result = gitlab_request("PUT", f"/projects/{GITLAB_PROJECT_ID}/issues/{iid}",
                                        body={"state_event": "close"})
                print(f"   ✅ #{iid} fechada")
                self._json(result)
            except Exception as e:
                print(f"   ❌ {e}"); self._error(str(e))

        elif self.path.startswith('/gitlab/issues/') and self.path.endswith('/update'):
            try:
                iid = self.path.split('/')[3]
                data = json.loads(body)
                print(f"\n✏️  ATUALIZAR ISSUE #{iid}: {data}")
                result = gitlab_request("PUT", f"/projects/{GITLAB_PROJECT_ID}/issues/{iid}", body=data)
                print(f"   ✅ #{iid} atualizada")
                self._json(result)
            except Exception as e:
                print(f"   ❌ {e}"); self._error(str(e))

        elif self.path == '/api/chat':
            try:
                data = json.loads(body)
                messages = data.get('messages', [])
                last_user = next((m['content'] for m in reversed(messages) if m['role'] == 'user'), '')

                print(f"\n{'='*50}")
                print(f"💬 PERGUNTA: {last_user[:100]}")

                needs_gitlab = any(kw in last_user.lower() for kw in GITLAB_KEYWORDS)

                if needs_gitlab:
                    print(f"   🦊 A buscar dados do GitLab...")
                    gitlab_ctx = get_gitlab_context()
                    print(f"   🦊 GitLab: {len(gitlab_ctx)} chars")
                    system_content = SYSTEM_PROMPT + "\n\n" + DOCUMENT_CONTEXT + "\n" + gitlab_ctx
                else:
                    gitlab_ctx = ""
                    print(f"   📄 Apenas documento TFC")
                    system_content = SYSTEM_PROMPT + "\n\n" + DOCUMENT_CONTEXT

                ollama_payload = {
                    "model": data.get('model', 'qwen2.5:7b'),
                    "messages": [
                        {"role": "system", "content": system_content},
                        *messages
                    ],
                    "stream": True,
                    "options": {
                        "temperature": 0.3,
                        "num_ctx": 4096,
                        "top_p": 0.9
                    }
                }

                print(f"   📤 Prompt: {len(system_content)} chars → Ollama (stream)")

                req = urllib.request.Request(
                    'http://localhost:11434/api/chat',
                    data=json.dumps(ollama_payload).encode(),
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )

                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('X-Accel-Buffering', 'no')
                self.send_cors_headers()
                self.end_headers()

                total_chars = 0
                with urllib.request.urlopen(req, timeout=120) as r:
                    for line in r:
                        if line:
                            try:
                                chunk = json.loads(line.decode('utf-8'))
                                content = chunk.get('message', {}).get('content', '')
                                if content:
                                    total_chars += len(content)
                                    event = json.dumps({"content": content, "done": False})
                                    self.wfile.write(f"data: {event}\n\n".encode())
                                    self.wfile.flush()
                                if chunk.get('done'):
                                    done_event = json.dumps({"content": "", "done": True,
                                        "duration": chunk.get('total_duration', 0) // 1_000_000})
                                    self.wfile.write(f"data: {done_event}\n\n".encode())
                                    self.wfile.flush()
                                    ms = chunk.get('total_duration', 0) // 1_000_000
                                    print(f"   📥 Resposta: {total_chars} chars em {ms}ms")
                                    print(f"{'='*50}")
                            except:
                                pass

            except Exception as e:
                print(f"   ❌ Erro: {e}")
                try:
                    err = json.dumps({"content": f"Erro: {e}", "done": True})
                    self.wfile.write(f"data: {err}\n\n".encode())
                    self.wfile.flush()
                except:
                    pass
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
    print(f"📄 Documento TFC: {len(DOCUMENT_CONTEXT)} chars")
    print(f"🦊 GitLab project: {GITLAB_PROJECT_ID}")
    print(f"⚡ Streaming activado — /api/chat")
    print(f"{'='*50}\n")
    HTTPServer(('', port), Handler).serve_forever()
