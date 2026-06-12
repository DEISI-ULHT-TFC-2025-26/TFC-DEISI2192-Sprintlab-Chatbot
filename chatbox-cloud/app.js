// ============================================================
// SprintLab TFC Chatbox — logica do frontend
// Separada de chatbox.html (organizacao HTML / CSS / JS).
// Servida por server.py em  GET /app.js  (carregada com <script defer>).
// ============================================================

    // BASE_URL is derived from the page itself — no more hardcoded ngrok URL.
    const BASE_URL = window.location.origin;
    const MODEL = "Llama 3.3 70B";  // display label only — server uses GROQ_MODEL
    const HISTORY_CAP = 20;

    const history = [];
    let loading = false;
    let activeController = null; // AbortController for in-flight stream

    // ── Utils ─────────────────────────────────────────────────────────────────
    function resize(el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 100) + "px";
    }
    function handleKey(e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    }
    function useSuggestion(el) {
      document.getElementById("input").value = el.textContent;
      send();
    }
    function removeWelcome() {
      const w = document.getElementById("welcome");
      if (w) w.remove();
    }
    function escHtml(s) {
      return s.replace(/&/g,"&amp;").replace(/</g,"&lt;")
              .replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
    }
    // Só permite http(s) num href — bloqueia javascript:/data: (web_url vem de um
    // GitLab arbitrário que o utilizador liga, logo é dado não-confiável).
    function safeUrl(u) {
      try {
        const url = new URL(u, BASE_URL);
        return (url.protocol === "https:" || url.protocol === "http:") ? url.href : "";
      } catch (e) { return ""; }
    }
    // Render markdown SAFELY: escape first, then apply formatting on the
    // already-escaped string. The previous version replaced **/* before
    // escaping, which let the model inject raw HTML into the page.
    function renderMd(text) {
      let s = escHtml(text);
      s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
      s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
      let html = "";
      for (const line of s.split("\n")) {
        const t = line.trim();
        if (!t) continue;
        if (/^[\-\*] /.test(t))      html += `<li>${t.slice(2)}</li>`;
        else if (/^\d+\. /.test(t))  html += `<li>${t.replace(/^\d+\.\s*/,"")}</li>`;
        else                          html += `<p>${t}</p>`;
      }
      return html.replace(/(<li>[\s\S]*?<\/li>)+/g, m => `<ul>${m}</ul>`);
    }
    function setStatus(thinking) {
      const dot = document.getElementById("status-dot");
      const label = document.getElementById("model-label");
      dot.className = "status-dot" + (thinking ? " thinking" : "");
      label.textContent = thinking ? "a pensar..." : modelLabel();
    }
    let scrollPending = false;
    function scrollBottom() {
      if (scrollPending) return;
      scrollPending = true;
      requestAnimationFrame(() => {
        scrollPending = false;
        const m = document.getElementById("messages");
        m.scrollTop = m.scrollHeight;
      });
    }

    // ── Static (non-stream) message ───────────────────────────────────────────
    function addMsg(role, text, badge) {
      removeWelcome();
      const msgs = document.getElementById("messages");
      const wrap = document.createElement("div");
      wrap.className = `msg ${role}`;
      const av = document.createElement("div");
      av.className = "avatar";
      av.textContent = role === "user" ? "Tu" : "AI";
      const bub = document.createElement("div");
      bub.className = "bubble";
      if (badge) {
        const b = document.createElement("div");
        b.className = "action-badge";
        b.textContent = badge;
        bub.appendChild(b);
      }
      const content = document.createElement("div");
      content.className = "bubble-content";
      if (role === "user") {
        content.textContent = text; // pre-wrap CSS preserves newlines
      } else {
        content.innerHTML = renderMd(text);
      }
      bub.appendChild(content);
      wrap.appendChild(av);
      wrap.appendChild(bub);
      msgs.appendChild(wrap);
      scrollBottom();
      return content;
    }

    // ── Streaming bubble (perf-critical path) ─────────────────────────────────
    // While streaming we append plain text to a <span> and only re-parse the
    // markdown when streaming completes. This avoids the O(n²) re-parse-on-
    // every-token cost the previous version had.
    function createStreamBubble() {
      removeWelcome();
      const msgs = document.getElementById("messages");
      const wrap = document.createElement("div");
      wrap.className = "msg ai";
      wrap.id = "stream-wrap";

      const av = document.createElement("div");
      av.className = "avatar";
      av.textContent = "AI";

      const bub = document.createElement("div");
      bub.className = "bubble";

      const typing = document.createElement("div");
      typing.className = "typing";
      typing.id = "typing-indicator";
      typing.innerHTML = `<div class="dot"></div><div class="dot"></div><div class="dot"></div>`;
      bub.appendChild(typing);

      wrap.appendChild(av);
      wrap.appendChild(bub);
      msgs.appendChild(wrap);
      scrollBottom();
      return bub;
    }

    function ensureStreamContent(bub) {
      let raw = bub.querySelector(".stream-raw");
      if (raw) return raw;
      const typing = document.getElementById("typing-indicator");
      if (typing) typing.remove();
      raw = document.createElement("span");
      raw.className = "stream-raw";
      const cursor = document.createElement("span");
      cursor.className = "cursor";
      bub.appendChild(raw);
      bub.appendChild(cursor);
      return raw;
    }

    function appendStreamChunk(bub, chunk) {
      const raw = ensureStreamContent(bub);
      // textContent append is cheap; pre-wrap CSS keeps newlines.
      raw.appendChild(document.createTextNode(chunk));
      scrollBottom();
    }

    function finalizeStreamBubble(bub, fullText) {
      const cursor = bub.querySelector(".cursor");
      if (cursor) cursor.remove();
      const raw = bub.querySelector(".stream-raw");
      if (!raw) {
        bub.innerHTML = renderMd(fullText);
        return;
      }
      // One markdown re-parse at the end, no more.
      raw.outerHTML = `<div class="bubble-content">${renderMd(fullText)}</div>`;
    }

    function showError(msg) {
      const msgs = document.getElementById("messages");
      const e = document.createElement("div");
      e.className = "error-msg";
      e.textContent = msg;
      msgs.appendChild(e);
      scrollBottom();
    }

    // ── Intent detection ──────────────────────────────────────────────────────
    function normaliseQuotes(s) {
      // Collapse smart quotes / pt-PT angle quotes into ASCII so the regex below
      // doesn't miss "criar issue 'X'" written with curly quotes.
      return s.replace(/[‘’‚‛‹›]/g, "'")
              .replace(/[“”„«»]/g, "\"");
    }
    function detectChartIntent(t) {
      // Trigger word must be present — otherwise plain Q&A goes to the chat model.
      const hasTrigger = /\b(?:gr[áa]fico|chart|visualiza[r]?|mostra(?:r|me)?|pie|doughnut|barras?|burndown)\b/i.test(t);
      if (!hasTrigger) return null;

      const daysMatch = t.match(/(\d+)\s*dias?/i);
      const params = daysMatch ? { days: daysMatch[1] } : {};

      if (/burndown|fechadas?.*dia|progresso (?:ao longo|temporal|di[áa]rio)/i.test(t))
        return { chart: "burndown", params };
      if (/cycle ?time|tempo (?:de |para )?ciclo|tempo para fechar|tempo de fecho/i.test(t))
        return { chart: "cycle-time", params: {} };
      if (/assignee|atribu[ií]d|quem tem/i.test(t))
        return { chart: "by-assignee", params: {} };
      if (/label|etiqueta/i.test(t))
        return { chart: "by-label", params: {} };
      if (/milestone|\bsprint\b(?!lab)/i.test(t))
        return { chart: "by-milestone", params: {} };
      if (/merge request|\bmr\b|pull request/i.test(t))
        return { chart: "contributors-mr", params: {} };
      if (/commit/i.test(t))
        // com "N dias" → janela temporal; sem dias → histórico completo
        return daysMatch ? { chart: "contributors-commits", params }
                         : { chart: "contributors-all", params: {} };
      if (/estado|aberta?s? vs|opened vs|status/i.test(t))
        return { chart: "state-pie", params: {} };
      // Generic "mostra um gráfico" with no specifier → state pie
      return { chart: "state-pie", params: {} };
    }

    // ── Investigação de código (blame): "quem alterou X?", "analisa o erro em
    // Y linha N", ou um stack trace colado. Devolve {file, line} | {help} | null.
    function detectBlameIntent(raw) {
      // 1) stack traces colados → ficheiro+linha extraídos diretamente
      let m = raw.match(/File "([^"]+)", line (\d+)/);                    // Python
      if (m) return { file: m[1], line: Number(m[2]) };
      m = raw.match(/\bat [^\n(]*\(?([\w./\\-]+\.[A-Za-z]\w{0,7}):(\d+)(?::\d+)?\)?/); // JS
      if (m) return { file: m[1], line: Number(m[2]) };

      // 2) frase natural: palavras-chave de blame/erro + um token de ficheiro
      const kw =
        /\b(?:quem|qual|que)\b[^.?!\n]*\b(?:alterou|alteraram|mudou|mudaram|mexeu|mexeram|modificou|modificaram|escreveu|introduziu)\b/i.test(raw)
        || /\bblame\b/i.test(raw)
        || (/\b(?:analisa|investiga|descobre|encontra|explica|origem|veio)\b/i.test(raw)
            && /\b(?:erro|bug|problema|falha)\b/i.test(raw));
      if (!kw) return null;

      const noUrl = raw.replace(/https?:\/\/\S+/g, " ");  // não confundir URLs com ficheiros
      const f = noUrl.match(/([\w\-./\\]*[\w\-]\.[A-Za-z]\w{0,7})(?::(\d+))?/);
      if (!f) {
        // pediu blame mas sem ficheiro → bolha de ajuda (0 tokens)
        return /\b(?:ficheiro|linha|c[óo]digo|file)\b/i.test(raw) ? { help: true } : null;
      }
      let line = f[2] ? Number(f[2]) : null;
      if (!line) {
        const l = raw.match(/\b(?:linha|line)\s*#?(\d+)/i);
        if (l) line = Number(l[1]);
      }
      return { file: f[1], line };
    }

    function detectIntent(text) {
      const raw = normaliseQuotes(text);
      const t = raw.toLowerCase();

      // Blame antes dos charts: "mostra quem alterou o app.js" tem trigger de
      // chart ("mostra") mas o token de ficheiro torna-o investigação de código.
      const blame = detectBlameIntent(raw);
      if (blame) {
        return blame.help ? { type: "blame_help" }
                          : { type: "blame_analysis", file: blame.file, line: blame.line };
      }

      // Commit por IA — exige o VERBO ("faz commit", "commita"), por isso não
      // dispara em "gráfico de commits", "exporta commits" ou "quantos commits".
      if (/\bfa(?:z|ça|zer)\s+(?:o\s+|um\s+)?commit\b/i.test(raw)
          || /\bcommita(?:r)?\b/i.test(raw)) {
        return { type: "code_commit" };
      }

      const chart = detectChartIntent(t);
      if (chart) return { type: "chart", ...chart };

      // Create intent → open the structured form (the model never auto-creates).
      // Broad detection; a title is only pre-filled when clearly given.
      if (/\b(cria|criar)\b[^?]*\b(issue|tarefa)\b/i.test(t)
          || /\bnov[ao]\s+issue\b/i.test(t)) {
        let title = "";
        const q = raw.match(/['"]([^'"]+)['"]/);
        if (q) {
          title = q[1].trim();
        } else {
          const m = raw.match(/\b(?:t[ií]tulo|chamad[oa])\s+(.+)$/i);
          if (m) title = m[1].trim();
        }
        return { type: "create_form", title };
      }

      // Delete (destructive) — explicit number → always goes through a red confirm card.
      const deleteMatch = raw.match(/\b(?:apaga[r]?|elimina[r]?|delete|remove[r]?)\b[^?]*\bissue\s+#?(\d+)/i);
      if (deleteMatch) return { type: "delete_action", iid: deleteMatch[1] };

      const closeMatch = raw.match(/fecha[r]?\s+(?:a\s+)?issue\s+#?(\d+)/i)
                      || raw.match(/close\s+issue\s+#?(\d+)/i);
      if (closeMatch) return { type: "close_issue", iid: closeMatch[1] };

      // Edit/update intent → open the pre-filled edit form for that issue.
      const editMatch = raw.match(/\b(?:edita[r]?|atualiz[ae][r]?|altera[r]?|modifica[r]?|muda[r]?)\b[^?]*\bissue\s+#?(\d+)/i);
      if (editMatch) return { type: "edit_form", iid: editMatch[1] };

      if (/export[ae][r]?|download|csv|transfere|descarrega|ficheiro de (?:issues|commits)/i.test(t)) {
        if (/commit/i.test(t)) return { type: "export_commits" };
        const state = /fecha[ds]|closed/i.test(t) ? "closed"
                    : /abert[as]|opened/i.test(t) ? "opened"
                    : "all";
        return { type: "export_issues", state };
      }

      // Relatório automático de sprint → card determinístico (números exatos).
      if (/relat[óo]rio|resumo\s+(?:d[oae]\s+)?(?:sprint|projeto|projecto)|sprint\s+report/i.test(t))
        return { type: "sprint_report" };

      return { type: "chat" };
    }

    // ── GitLab actions ────────────────────────────────────────────────────────
    // All issue writes (create/close/update/delete) go through the confirmation
    // card -> POST /api/confirm-action. Only export stays a direct GET download.
    const headers = { "Content-Type": "application/json", "ngrok-skip-browser-warning": "true" };

    // ── Definições globais (modelo + tema) — guardadas no browser ──────────────
    const SETTINGS_KEY = "sprintlab_settings";
    const MODEL_LABELS = {
      "llama-3.3-70b-versatile": "Llama 3.3 70B",
      "llama-3.1-8b-instant": "Llama 3.1 8B",
      "qwen/qwen3-32b": "Qwen3 32B",
      "openai/gpt-oss-120b": "GPT-OSS 120B",
      "moonshotai/kimi-k2-instruct-0905": "Kimi K2",
    };
    const settings = (() => {
      let s = {};
      try { s = JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {}; } catch (e) {}
      return {
        // gl* mantidos só para migração p/ workspaces (ver loadWorkspaces)
        glBase: s.glBase || "", glToken: s.glToken || "", glProject: s.glProject || "",
        model: MODEL_LABELS[s.model] ? s.model : "llama-3.3-70b-versatile",
        // sem escolha guardada → segue o tema do sistema operativo
        theme: ["dark", "light", "black"].includes(s.theme) ? s.theme
             : (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
                ? "light" : "dark"),
      };
    })();
    function modelLabel() { return MODEL_LABELS[settings.model] || settings.model; }
    function saveSettingsToStorage() {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify({ model: settings.model, theme: settings.theme }));
    }
    function applyTheme() { document.documentElement.dataset.theme = settings.theme; }
    function applyModelBadge() {
      const el = document.getElementById("model-label");
      if (el && !loading) el.textContent = modelLabel();
    }

    // ── Workspaces: cada GitLab é um "chat" próprio na sidebar esquerda ───────
    // O workspace `default` usa as credenciais do servidor (Secrets) — sem token
    // no browser. Os personalizados guardam base/token/project em localStorage.
    const WS_KEY = "sprintlab_workspaces";
    const HIST_PREFIX = "sprintlab_hist_";
    const workspaces = loadWorkspaces();

    function loadWorkspaces() {
      let w = null;
      try { w = JSON.parse(localStorage.getItem(WS_KEY)); } catch (e) {}
      if (!w || !Array.isArray(w.list) || !w.list.length) {
        w = { active: "default", list: [] };
      }
      if (!w.list.some(x => x.builtin)) {
        w.list.unshift({ id: "default", name: "SprintLab", builtin: true });
      }
      // migração: config GitLab antiga (painel ⚙️ de antes) vira um workspace
      if (settings.glToken || settings.glProject) {
        if (!w.list.some(x => x.id === "ws_migrated")) {
          w.list.push({
            id: "ws_migrated", name: "O meu GitLab",
            glBase: settings.glBase, glToken: settings.glToken, glProject: settings.glProject,
          });
          w.active = "ws_migrated";
        }
        // PERSISTE o workspace migrado ANTES de limpar a config antiga, senão as
        // credenciais perdiam-se (o `w` aqui ainda está em TDZ p/ saveWorkspaces).
        try { localStorage.setItem(WS_KEY, JSON.stringify(w)); } catch (e) {}
        settings.glBase = settings.glToken = settings.glProject = "";
        saveSettingsToStorage();
      }
      if (!w.list.some(x => x.id === w.active)) w.active = "default";
      return w;
    }
    function saveWorkspaces() {
      try { localStorage.setItem(WS_KEY, JSON.stringify(workspaces)); } catch (e) { /* quota */ }
    }
    function activeWs() { return workspaces.list.find(x => x.id === workspaces.active) || workspaces.list[0]; }

    function applyGitlabHeaders() {
      // mutam o objeto `headers` partilhado → todos os fetch usam o ws ativo
      const ws = activeWs();
      if (!ws || ws.builtin) {
        delete headers["X-GL-Base"]; delete headers["X-GL-Token"]; delete headers["X-GL-Project"];
        return;
      }
      if (ws.glBase)    headers["X-GL-Base"] = ws.glBase;       else delete headers["X-GL-Base"];
      if (ws.glToken)   headers["X-GL-Token"] = ws.glToken;     else delete headers["X-GL-Token"];
      if (ws.glProject) headers["X-GL-Project"] = ws.glProject; else delete headers["X-GL-Project"];
    }

    // Histórico por workspace (persistido no browser)
    function persistHistory() {
      try {
        localStorage.setItem(HIST_PREFIX + workspaces.active,
          JSON.stringify(history.slice(-HISTORY_CAP)));
      } catch (e) { /* storage cheio — ignora */ }
    }
    function restoreHistory() {
      let saved = [];
      try { saved = JSON.parse(localStorage.getItem(HIST_PREFIX + workspaces.active)) || []; } catch (e) {}
      history.splice(0, history.length, ...saved);
      const msgs = document.getElementById("messages");
      if (!history.length) {
        msgs.innerHTML = WELCOME_HTML;   // ecrã de boas-vindas do projeto
        return;
      }
      msgs.innerHTML = "";
      for (const m of history) addMsg(m.role === "user" ? "user" : "ai", m.content);
      scrollBottom();
    }

    function updateHeaderForWs() {
      const ws = activeWs();
      document.getElementById("ws-title").textContent = ws.name || "GitLab";
      const host = ws.builtin ? "GitLab predefinido"
        : `${(ws.glBase || "gitlab.com").replace(/^https?:\/\//, "")} · #${ws.glProject}`;
      document.getElementById("ws-sub").textContent = host;
    }

    function renderWorkspaceList() {
      const box = document.getElementById("ws-list");
      box.innerHTML = "";
      for (const ws of workspaces.list) {
        const item = document.createElement("div");
        item.className = "ws-item" + (ws.id === workspaces.active ? " active" : "");
        item.onclick = () => { switchWorkspace(ws.id); document.getElementById("sidebar-left").classList.remove("open"); };

        const ico = document.createElement("div");
        ico.className = "ws-ico";
        ico.innerHTML = ws.builtin ? '<i class="fas fa-rocket"></i>' : '<i class="fab fa-gitlab"></i>';

        const meta = document.createElement("div");
        meta.className = "ws-meta";
        const nm = document.createElement("div");
        nm.className = "ws-name"; nm.textContent = ws.name || "GitLab";
        const host = document.createElement("div");
        host.className = "ws-host";
        host.textContent = ws.builtin ? "Predefinição"
          : (ws.glBase || "gitlab.com").replace(/^https?:\/\//, "");
        meta.appendChild(nm); meta.appendChild(host);

        item.appendChild(ico); item.appendChild(meta);
        if (!ws.builtin) {
          const edit = document.createElement("button");
          edit.type = "button"; edit.className = "ws-edit";
          edit.innerHTML = '<i class="fas fa-pen"></i>';
          edit.setAttribute("aria-label", "Editar " + (ws.name || "GitLab"));
          edit.onclick = (e) => { e.stopPropagation(); openWsEditor(ws.id); };
          item.appendChild(edit);
        }
        box.appendChild(item);
      }
    }

    function switchWorkspace(id) {
      if (loading) return;                  // não trocar a meio de uma resposta
      if (id === workspaces.active) return;
      persistHistory();
      workspaces.active = id;
      saveWorkspaces();
      applyGitlabHeaders();
      renderWorkspaceList();
      updateHeaderForWs();
      clearFollowups();
      restoreHistory();
      loadStats();
    }

    // ── Editor de workspace (adicionar / editar um GitLab) ────────────────────
    let wsEditing = null;        // id do ws em edição, ou null = novo
    let wsTestName = "";         // nome do projeto devolvido pelo último teste

    function openWsEditor(id) {
      wsEditing = id || null;
      const ws = id ? workspaces.list.find(x => x.id === id) : null;
      document.getElementById("ws-editor-title").textContent = ws ? "Editar GitLab" : "Adicionar GitLab";
      document.getElementById("ws-name").value = ws?.name || "";
      document.getElementById("ws-base").value = ws?.glBase || "";
      document.getElementById("ws-token").value = ws?.glToken || "";
      document.getElementById("ws-project").value = ws?.glProject || "";
      document.getElementById("ws-test-result").textContent = "";
      document.getElementById("ws-delete").hidden = !ws;
      wsTestName = "";
      document.getElementById("ws-overlay").hidden = false;
      document.getElementById("ws-name").focus();
    }
    function closeWsEditor() { document.getElementById("ws-overlay").hidden = true; }

    async function wsTest() {
      const r = document.getElementById("ws-test-result");
      r.textContent = "A testar…"; r.className = "settings-test-result";
      const h = { "Content-Type": "application/json", "ngrok-skip-browser-warning": "true" };
      const base = document.getElementById("ws-base").value.trim();
      const token = document.getElementById("ws-token").value.trim();
      const project = document.getElementById("ws-project").value.trim();
      if (base) h["X-GL-Base"] = base;
      if (token) h["X-GL-Token"] = token;
      if (project) h["X-GL-Project"] = project;
      try {
        const res = await fetch(`${BASE_URL}/gitlab/test`, { headers: h });
        const d = await res.json();
        if (d.ok) {
          wsTestName = d.name || "";
          r.textContent = `✓ ${d.name} · ${d.open_issues ?? "?"} issues abertas`;
          r.className = "settings-test-result ok";
        } else { r.textContent = `✗ ${d.error}`; r.className = "settings-test-result err"; }
      } catch (e) { r.textContent = "✗ Erro de ligação ao servidor"; r.className = "settings-test-result err"; }
    }

    function wsSave() {
      const r = document.getElementById("ws-test-result");
      const name = document.getElementById("ws-name").value.trim();
      const base = document.getElementById("ws-base").value.trim();
      const token = document.getElementById("ws-token").value.trim();
      const project = document.getElementById("ws-project").value.trim();
      if (!token || !project) {
        r.textContent = "✗ Token e Project ID são obrigatórios.";
        r.className = "settings-test-result err";
        return;
      }
      // nome final: o que escreveste > último teste > parte final do namespace
      const finalName = name || (wsTestName ? wsTestName.split("/").pop().trim() : "") || "GitLab";
      if (wsEditing) {
        const ws = workspaces.list.find(x => x.id === wsEditing);
        if (ws) Object.assign(ws, { name: finalName, glBase: base, glToken: token, glProject: project });
        saveWorkspaces();
        if (wsEditing === workspaces.active) { applyGitlabHeaders(); updateHeaderForWs(); loadStats(); }
        renderWorkspaceList();
        closeWsEditor();
      } else {
        const id = "ws_" + Math.random().toString(36).slice(2, 10);
        workspaces.list.push({ id, name: finalName, glBase: base, glToken: token, glProject: project });
        saveWorkspaces();
        renderWorkspaceList();   // mostra-o já na lista (mesmo se não puder trocar agora)
        closeWsEditor();
        if (!loading) switchWorkspace(id);   // entra no projeto novo (se não houver resposta a decorrer)
      }
    }

    function wsDelete() {
      if (!wsEditing) return;
      const ws = workspaces.list.find(x => x.id === wsEditing);
      // Não apagar o workspace ATIVO a meio de uma resposta (corromperia o histórico).
      if (loading && wsEditing === workspaces.active) {
        const r = document.getElementById("ws-test-result");
        r.textContent = "✗ Espera a resposta terminar antes de apagar este projeto.";
        r.className = "settings-test-result err";
        return;
      }
      if (!confirm(`Apagar o projeto «${ws?.name || "GitLab"}» da lista? (não apaga nada no GitLab)`)) return;
      workspaces.list = workspaces.list.filter(x => x.id !== wsEditing);
      try { localStorage.removeItem(HIST_PREFIX + wsEditing); } catch (e) {}
      if (workspaces.active === wsEditing) {
        workspaces.active = "default";
        saveWorkspaces();
        applyGitlabHeaders(); updateHeaderForWs(); restoreHistory(); loadStats();
      } else {
        saveWorkspaces();
      }
      renderWorkspaceList();
      closeWsEditor();
    }

    // ── Painel de estatísticas (sidebar direita): issues + commits + MRs ──────
    let statsSeq = 0;
    async function loadStats() {
      const seq = ++statsSeq;                 // só a chamada mais recente pode renderizar
      const wsAtStart = workspaces.active;
      const body = document.getElementById("stats-body");
      body.innerHTML = '<div class="stats-empty">A carregar…</div>';
      try {
        const res = await fetch(`${BASE_URL}/gitlab/stats`, { headers });
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        if (seq !== statsSeq || wsAtStart !== workspaces.active) return;  // resposta obsoleta
        renderStats(data);
      } catch (e) {
        if (seq === statsSeq) body.innerHTML = '<div class="stats-empty">Não foi possível carregar as estatísticas.</div>';
      }
    }
    function statRow(label, value, ellip) {
      return `<div class="stat-row"><span class="sr-label${ellip ? " ellip" : ""}">${escHtml(String(label))}</span>` +
             `<span class="sr-value">${escHtml(String(value))}</span></div>`;
    }
    function renderStats(d) {
      const body = document.getElementById("stats-body");
      let html = "";
      if (d.project) {
        html += `<div class="stats-sec sec-proj"><h4><i class="fas fa-folder"></i> Projeto</h4>` +
                statRow("Nome", (d.project.name || "?").split("/").pop().trim()) +
                (d.project.commit_count != null ? statRow("Commits (total)", d.project.commit_count) : "") +
                (safeUrl(d.project.web_url) ? `<a class="stats-proj-link" href="${escHtml(safeUrl(d.project.web_url))}" target="_blank" rel="noopener">Abrir no GitLab ↗</a>` : "") +
                `</div>`;
      }
      if (d.issues) {
        html += `<div class="stats-sec sec-issues"><h4><i class="fas fa-circle-dot"></i> Issues</h4>` +
                `<div class="progress-track"><div class="progress-fill" style="width:${Math.min(100, d.issues.progress)}%"></div></div>` +
                statRow("Progresso", d.issues.progress + "%") +
                statRow("Abertas", d.issues.open) +
                statRow("Fechadas", d.issues.closed) +
                statRow("Em atraso", d.issues.overdue) +
                statRow("Sem assignee", d.issues.no_assignee) +
                `</div>`;
      }
      if (d.contributors && d.contributors.top && d.contributors.top.length) {
        html += `<div class="stats-sec sec-contrib"><h4><i class="fas fa-users"></i> Contribuidores</h4>` +
                statRow("Autores", d.contributors.authors);
        for (const t of d.contributors.top) {
          html += statRow(t.name, t.commits, true);
        }
        html += `</div>`;
      }
      if (d.commits && d.commits.last90d > 0) {
        html += `<div class="stats-sec sec-activity"><h4><i class="fas fa-code-commit"></i> Atividade (90 dias)</h4>` +
                statRow("Commits", d.commits.last90d) +
                statRow("Autores", d.commits.authors);
        for (const t of (d.commits.top || []).slice(0, 3)) {
          html += statRow(t.name, t.count, true);
        }
        html += `</div>`;
      }
      if (d.mrs && d.mrs.total > 0) {
        html += `<div class="stats-sec sec-mrs"><h4><i class="fas fa-code-pull-request"></i> Merge Requests</h4>` +
                statRow("Abertos", d.mrs.open) +
                statRow("Total", d.mrs.total) +
                `</div>`;
      }
      if (d.milestones && d.milestones.length) {
        html += `<div class="stats-sec sec-ms"><h4><i class="fas fa-flag"></i> Milestones</h4>`;
        for (const m of d.milestones) {
          html += statRow(m.title, m.due_date || "—", true);
        }
        html += `</div>`;
      }
      body.innerHTML = html || '<div class="stats-empty">Sem dados para mostrar.</div>';
    }

    // ── Definições globais (gear): modelo + tema ──────────────────────────────
    function refreshModelDesc() {
      const sel = document.getElementById("set-model");
      const opt = sel.options[sel.selectedIndex];
      const raw = opt?.dataset.desc || "";
      const esc = raw.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
      document.getElementById("set-model-desc").innerHTML =
        esc.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    }
    function openSettings() {
      document.getElementById("set-model").value = settings.model;
      document.getElementById("set-theme").value = settings.theme;
      refreshModelDesc();
      document.getElementById("settings-overlay").hidden = false;
    }
    function closeSettings() {
      applyTheme();   // reverte pré-visualização de tema não guardada
      document.getElementById("settings-overlay").hidden = true;
    }
    function saveSettings() {
      settings.model = document.getElementById("set-model").value;
      settings.theme = document.getElementById("set-theme").value;
      saveSettingsToStorage();
      applyTheme(); applyModelBadge();
      document.getElementById("settings-overlay").hidden = true;
    }

    // ── Init + wiring (corre no load; DOM pronto por causa do defer) ──────────
    const WELCOME_HTML = document.getElementById("welcome").outerHTML;
    applyTheme(); applyModelBadge(); applyGitlabHeaders();
    renderWorkspaceList(); updateHeaderForWs(); restoreHistory(); loadStats();

    document.getElementById("gear-btn").onclick = openSettings;
    document.getElementById("settings-close").onclick = closeSettings;
    document.getElementById("set-cancel").onclick = closeSettings;
    document.getElementById("set-save").onclick = saveSettings;
    document.getElementById("set-theme").onchange = (e) => { document.documentElement.dataset.theme = e.target.value; };
    document.getElementById("set-model").onchange = refreshModelDesc;
    document.getElementById("settings-overlay").onclick = (e) => {
      if (e.target.id === "settings-overlay") closeSettings();
    };

    document.getElementById("ws-add").onclick = () => openWsEditor(null);
    document.getElementById("ws-close").onclick = closeWsEditor;
    document.getElementById("ws-cancel").onclick = closeWsEditor;
    document.getElementById("ws-save").onclick = wsSave;
    document.getElementById("ws-test").onclick = wsTest;
    document.getElementById("ws-delete").onclick = wsDelete;
    document.getElementById("ws-overlay").onclick = (e) => {
      if (e.target.id === "ws-overlay") closeWsEditor();
    };

    // ── Cópia de segurança (workspaces + conversas + definições) ──────────────
    // Tudo vive no localStorage — trocar de browser/máquina perdia os dados.
    // O backup é um JSON local: o utilizador guarda e repõe onde quiser.
    function backupExport() {
      const hasTokens = workspaces.list.some(w => w.glToken);
      if (hasTokens && !confirm(
        "O ficheiro de backup inclui os TOKENS dos teus GitLabs (em claro).\n" +
        "Guarda-o num local seguro. Continuar?")) return;
      const data = {
        app: "sprintlab-chatbox", version: 1,
        exported_at: new Date().toISOString(),
        settings: { model: settings.model, theme: settings.theme },
        workspaces: { active: workspaces.active, list: workspaces.list },
        histories: {},
      };
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith(HIST_PREFIX)) {
          try { data.histories[k] = JSON.parse(localStorage.getItem(k)); }
          catch (e) { /* histórico corrompido — não trava o backup */ }
        }
      }
      const blob = new Blob([JSON.stringify(data, null, 2)],
                            { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "sprintlab-backup.json";
      a.click();
      URL.revokeObjectURL(a.href);
    }

    function backupRestore(file) {
      const reader = new FileReader();
      reader.onload = () => {
        let data = null;
        try { data = JSON.parse(reader.result); } catch (e) {}
        if (!data || data.app !== "sprintlab-chatbox"
            || !data.workspaces || !Array.isArray(data.workspaces.list)) {
          alert("Ficheiro de backup inválido."); return;
        }
        if (!confirm("Repor o backup substitui os GitLabs e conversas atuais. Continuar?"))
          return;
        try {
          localStorage.setItem(WS_KEY, JSON.stringify(data.workspaces));
          if (data.settings) {
            localStorage.setItem(SETTINGS_KEY, JSON.stringify(data.settings));
          }
          // limpa históricos atuais e repõe os do backup
          for (let i = localStorage.length - 1; i >= 0; i--) {
            const k = localStorage.key(i);
            if (k && k.startsWith(HIST_PREFIX)) localStorage.removeItem(k);
          }
          for (const [k, v] of Object.entries(data.histories || {})) {
            if (k.startsWith(HIST_PREFIX)) localStorage.setItem(k, JSON.stringify(v));
          }
        } catch (e) {
          alert("Não foi possível repor o backup (armazenamento cheio?)."); return;
        }
        location.reload();   // re-arranca com o estado reposto
      };
      reader.readAsText(file);
    }

    document.getElementById("ws-backup").onclick = backupExport;
    document.getElementById("ws-restore").onclick = () =>
      document.getElementById("ws-restore-file").click();
    document.getElementById("ws-restore-file").onchange = (e) => {
      if (e.target.files && e.target.files[0]) backupRestore(e.target.files[0]);
      e.target.value = "";   // permite re-importar o mesmo ficheiro
    };

    document.getElementById("stats-refresh").onclick = loadStats;
    document.getElementById("toggle-left").onclick = () =>
      document.getElementById("sidebar-left").classList.toggle("open");
    document.getElementById("toggle-right").onclick = () =>
      document.getElementById("sidebar-right").classList.toggle("open");

    // Download via fetch (NÃO <a href>) para os headers X-GL-* irem com o pedido —
    // senão o servidor cai nas Secrets (SprintLab) e ignora o workspace ativo.
    async function downloadCsv(url, filename) {
      const res = await fetch(url, { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      URL.revokeObjectURL(a.href);
    }
    function exportIssues(state) {
      downloadCsv(`${BASE_URL}/gitlab/export?state=${encodeURIComponent(state)}`,
                  "gitlab_issues.csv")
        .catch(e => { showError("Não foi possível exportar as issues."); console.error(e); });
    }
    function exportCommits() {
      downloadCsv(`${BASE_URL}/gitlab/export?type=commits`, "gitlab_commits.csv")
        .catch(e => { showError("Não foi possível exportar os commits."); console.error(e); });
    }

    // ── Relatório automático de sprint ────────────────────────────────────────
    // Card determinístico a partir de /gitlab/report (números exatos, sem o
    // modelo a "contar"). Botões para exportar .md e copiar.
    function addAiCard(html) {
      removeWelcome();
      const msgs = document.getElementById("messages");
      const wrap = document.createElement("div");
      wrap.className = "msg ai";
      const av = document.createElement("div");
      av.className = "avatar"; av.textContent = "AI";
      const bub = document.createElement("div");
      bub.className = "bubble";
      bub.innerHTML = html;
      wrap.appendChild(av); wrap.appendChild(bub);
      msgs.appendChild(wrap);
      scrollBottom();
      return bub;
    }

    function repKpi(label, value, cls) {
      return `<div class="rep-kpi ${cls || ""}"><div class="rep-kpi-v">${escHtml(String(value))}</div>` +
             `<div class="rep-kpi-l">${escHtml(label)}</div></div>`;
    }

    function sprintReportHtml(d) {
      const s = d.summary || {}, a = d.activity90d || { top: [] };
      const c = d.contributors || { top: [] }, mrs = d.mrs || {};
      const hasIssues = d.has_issues, hasRecent = d.has_recent_activity;
      const badge = { ativo: ["Ativo", "st-active"], concluido: ["Concluído", "st-done"],
                      vazio: ["Vazio", "st-empty"] }[d.status];

      let h = `<div class="report-card">`;
      h += `<div class="rep-head"><i class="fas fa-file-lines"></i>` +
           `<div><div class="rep-title">Relatório do projeto` +
           (badge ? ` <span class="rep-status ${badge[1]}">${escHtml(badge[0])}</span>` : "") +
           `</div>` +
           `<div class="rep-sub">${escHtml(d.project || "?")} · ${escHtml(d.generated_at || "")}</div></div></div>`;

      // Sobre — o que o projeto é (descrição, linguagens, estrutura)
      if (d.about && d.about.length) {
        h += `<div class="rep-sec"><h5><i class="fas fa-circle-info sec-i-blue"></i> Sobre</h5><ul class="rep-list">`;
        for (const x of d.about) h += `<li>${escHtml(x)}</li>`;
        h += `</ul></div>`;
      }

      if (d.highlights && d.highlights.length) {
        h += `<div class="rep-sec"><h5><i class="fas fa-star sec-i-amber"></i> Destaques</h5><ul class="rep-list">`;
        for (const x of d.highlights) h += `<li>${escHtml(x)}</li>`;
        h += `</ul></div>`;
      }

      // Issues — progresso/KPIs só quando o projeto as usa (0% ≠ "fase inicial")
      if (hasIssues) {
        h += `<div class="rep-progress"><div class="progress-track">` +
             `<div class="progress-fill" style="width:${Math.min(100, Number(s.progress) || 0)}%"></div></div>` +
             `<span class="rep-pct">${escHtml(String(s.progress || 0))}%</span></div>`;
        h += `<div class="rep-kpis">` +
             repKpi("Abertas", s.open || 0, "k-blue") +
             repKpi("Fechadas", s.closed || 0, "k-green") +
             repKpi("Em atraso", s.overdue || 0, "k-amber") + `</div>`;
        if (d.overdue_issues && d.overdue_issues.length) {
          h += `<div class="rep-sec"><h5><i class="fas fa-triangle-exclamation sec-i-amber"></i> Issues em atraso</h5><ul class="rep-list">`;
          for (const i of d.overdue_issues)
            h += `<li>#${escHtml(String(i.iid))} ${escHtml(i.title)} <span class="rep-due">(${escHtml(i.due_date || "")})</span></li>`;
          h += `</ul></div>`;
        }
      } else {
        h += `<div class="rep-note"><i class="fas fa-circle-info"></i> Este projeto não usa issues do GitLab.</div>`;
      }

      // Commits (sempre); Atividade (90d) só em projetos ativos
      const repoTot = (d.repo_commits != null)
        ? `${escHtml(String(d.repo_commits))} commits no repo · ` : "";
      let commitsSec = `<div class="rep-sec"><h5><i class="fas fa-users sec-i-blue"></i> Commits</h5>` +
        `<div class="rep-line">${repoTot}${escHtml(String(c.authors || 0))} autores <span class="rep-dim">(por autor)</span></div>`;
      for (const t of (c.top || []).slice(0, 3))
        commitsSec += `<div class="rep-row"><span>${escHtml(t.name)}</span><b>${escHtml(String(t.commits))}</b></div>`;
      commitsSec += `</div>`;
      if (hasRecent) {
        let actSec = `<div class="rep-sec"><h5><i class="fas fa-code-commit sec-i-green"></i> Atividade (90d)</h5>` +
          `<div class="rep-line">${escHtml(String(a.commits || 0))} commits · ${escHtml(String(a.authors || 0))} autores</div>`;
        for (const t of (a.top || []).slice(0, 3))
          actSec += `<div class="rep-row"><span>${escHtml(t.name)}</span><b>${escHtml(String(t.count))}</b></div>`;
        actSec += `</div>`;
        h += `<div class="rep-cols">${commitsSec}${actSec}</div>`;
      } else {
        h += commitsSec;   // largura total — sem atividade recente a omitir
      }

      if (mrs.total) {
        h += `<div class="rep-sec"><h5><i class="fas fa-code-pull-request sec-i-teal"></i> Merge Requests</h5>` +
             `<div class="rep-line">${escHtml(String(mrs.open || 0))} abertos · ${escHtml(String(mrs.total))} no total</div></div>`;
      }
      if (d.milestones && d.milestones.length) {
        h += `<div class="rep-sec"><h5><i class="fas fa-flag sec-i-pink"></i> Milestones</h5><ul class="rep-list">`;
        for (const m of d.milestones) {
          const tag = m.status === "overdue" ? ' <span class="rep-tag late">atrasado</span>'
                    : m.status === "soon" ? ' <span class="rep-tag soon">em breve</span>' : "";
          h += `<li>${escHtml(m.title || "?")} <span class="rep-due">(${escHtml(m.due_date || "—")})</span>${tag}</li>`;
        }
        h += `</ul></div>`;
      }

      // Proveniência (anti-"workslop"): o relatório não tem nada para rever —
      // todos os valores vêm do GitLab, calculados em código, sem a IA a contar.
      h += `<div class="prov-foot"><i class="fas fa-shield-halved"></i> ` +
           `Relatório 100% determinístico — todos os valores vêm diretamente do GitLab. ` +
           `Nenhum número é gerado por IA.</div>`;
      h += `<div class="rep-actions">` +
           `<button class="rep-btn" data-rep="md"><i class="fas fa-download"></i> Exportar .md</button>` +
           `<button class="rep-btn ghost" data-rep="copy"><i class="fas fa-copy"></i> Copiar</button></div>`;
      return h + `</div>`;
    }

    function wireReportActions(bub, d) {
      const md = bub.querySelector('[data-rep="md"]');
      const cp = bub.querySelector('[data-rep="copy"]');
      if (md) md.onclick = () => {
        const blob = new Blob([d.markdown || ""], { type: "text/markdown;charset=utf-8" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "relatorio-projeto.md";
        a.click();
        URL.revokeObjectURL(a.href);
      };
      if (cp) cp.onclick = async () => {
        try { await navigator.clipboard.writeText(d.markdown || ""); cp.innerHTML = '<i class="fas fa-check"></i> Copiado'; }
        catch (e) { cp.innerHTML = "Falhou"; }
        setTimeout(() => { cp.innerHTML = '<i class="fas fa-copy"></i> Copiar'; }, 1800);
      };
    }

    async function renderSprintReport() {
      const bub = addAiCard('<div class="confirm-msg"><i class="fas fa-spinner fa-spin"></i> A gerar o relatório do projeto…</div>');
      try {
        const res = await fetch(`${BASE_URL}/gitlab/report`, { headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const d = await res.json();
        bub.innerHTML = sprintReportHtml(d);
        wireReportActions(bub, d);
        scrollBottom();
      } catch (e) {
        bub.innerHTML = '<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> Não foi possível gerar o relatório.</div>';
        console.error(e);
      }
    }

    // ── Investigação de código (blame + diff + análise IA) ────────────────────
    function blameShaHtml(shortSha, url, small) {
      const u = safeUrl(url);
      const cls = "bl-sha" + (small ? " sm" : "");
      return u ? `<a class="${cls}" href="${escHtml(u)}" target="_blank" rel="noopener">${escHtml(shortSha)}</a>`
               : `<span class="${cls}">${escHtml(shortSha)}</span>`;
    }

    function blameCardHtml(d) {
      let h = `<div class="blame-card">`;
      h += `<div class="rep-head"><i class="fas fa-magnifying-glass bl-ico"></i>` +
           `<div><div class="rep-title">Investigação de código</div>` +
           `<div class="rep-sub">${escHtml(d.file)}${d.line ? ":" + escHtml(String(d.line)) : ""} · ref ${escHtml(d.ref || "?")}</div></div></div>`;

      if (d.note) h += `<div class="bl-note"><i class="fas fa-circle-info"></i> ${escHtml(d.note)}</div>`;

      if (d.facts) {
        const f = d.facts;
        h += `<div class="bl-facts">` +
             `<div class="bl-facts-t"><i class="fas fa-fingerprint"></i> Última alteração desta linha (git blame)</div>` +
             `<div class="bl-fact-row">${blameShaHtml(f.short_sha, f.commit_url)}` +
             `<span><i class="fas fa-user"></i> ${escHtml(f.author)}</span>` +
             `<span><i class="fas fa-calendar"></i> ${escHtml(f.date)}</span></div>` +
             `<div class="bl-msg">“${escHtml(f.message)}”</div>`;
        if (d.line_text)
          h += `<pre class="bl-line"><code>${escHtml(String(d.line))} | ${escHtml(d.line_text)}</code></pre>`;
        h += `</div>`;
      }

      if (d.analysis) {
        h += `<div class="rep-sec"><h5><i class="fas fa-wand-magic-sparkles sec-i-amber"></i> Análise da IA</h5>` +
             `<div class="bl-analysis">${renderMd(d.analysis)}</div></div>`;
      } else if (d.line && d.facts) {
        h += `<div class="bl-note"><i class="fas fa-circle-info"></i> Análise IA indisponível neste momento — os factos acima vêm diretamente do GitLab.</div>`;
      }

      if (d.authors && d.authors.length) {
        h += `<div class="rep-sec"><h5><i class="fas fa-users sec-i-blue"></i> Quem mais alterou este ficheiro</h5>`;
        for (const a of d.authors)
          h += `<div class="rep-row"><span>${escHtml(a.name)}</span><b>${escHtml(String(a.count))}</b></div>`;
        h += `</div>`;
      }

      if (d.history && d.history.length) {
        h += `<div class="rep-sec"><h5><i class="fas fa-clock-rotate-left sec-i-teal"></i> Últimos commits neste ficheiro</h5><div class="bl-hist">`;
        for (const c of d.history) {
          h += `<div class="bl-hist-row">${blameShaHtml(c.short_sha, c.commit_url, true)}` +
               `<span class="bl-hist-title">${escHtml(c.title)}</span>` +
               `<span class="bl-hist-meta">${escHtml(c.author)} · ${escHtml(c.date)}</span></div>`;
        }
        h += `</div></div>`;
      }

      h += `<div class="prov-foot"><i class="fas fa-shield-halved"></i> Factos (commit, autor, data) via git blame do GitLab — determinísticos. A análise do erro é uma hipótese gerada por IA.</div>`;
      return h + `</div>`;
    }

    async function renderBlameAnalysis(intent, originalText) {
      const bub = addAiCard('<div class="confirm-msg"><i class="fas fa-spinner fa-spin"></i> A investigar… (git blame + diff' + (intent.line ? " + análise IA" : "") + ')</div>');
      try {
        const res = await fetch(`${BASE_URL}/api/analyze-code`, {
          method: "POST",
          headers,   // leva os X-GL-* do workspace ativo (multi-tenant)
          body: JSON.stringify({
            file: intent.file, line: intent.line,
            question: originalText, model: settings.model,
          }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const d = await res.json();
        if (!d.ok) {
          bub.innerHTML = `<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> ${escHtml(d.error || "Não foi possível investigar.")}</div>`;
          return;
        }
        bub.innerHTML = blameCardHtml(d);
        scrollBottom();
      } catch (e) {
        bub.innerHTML = '<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> Erro ao investigar o código.</div>';
        console.error(e);
      }
    }

    // ── Commit por IA (gera plano → confirmação → branch ai/* + commit + MR) ──
    function commitPreviewHtml(p) {
      let h = `<div class="commit-card">`;
      h += `<div class="rep-head"><i class="fas fa-code-commit"></i><div>` +
           `<div class="rep-title">Commit por IA — pré-visualização</div>` +
           `<div class="rep-sub">branch <code>${escHtml(p.branch)}</code> · com Merge Request para revisão</div></div></div>`;
      if (p.summary) h += `<div class="cc-summary">${escHtml(p.summary)}</div>`;
      h += `<div class="cc-msg"><i class="fas fa-message"></i> ${escHtml(p.commit_message)}</div>`;
      for (const f of p.files || []) {
        h += `<div class="cc-file"><div class="cc-path"><i class="fas fa-file-code"></i> ${escHtml(f.path)}</div>` +
             `<pre class="cc-code"><code>${escHtml(f.content)}</code></pre></div>`;
      }
      h += `<div class="cc-note"><i class="fas fa-shield-halved"></i> Nada foi escrito ainda. ` +
           `Ao confirmar é criada a branch <b>${escHtml(p.branch)}</b> + commit + Merge Request — ` +
           `a branch principal não é alterada.</div>`;
      h += `<div class="rep-actions">` +
           `<button class="rep-btn cc-approve"><i class="fas fa-check"></i> Criar commit + MR</button>` +
           `<button class="rep-btn ghost cc-cancel">Cancelar</button></div>`;
      return h + `</div>`;
    }

    function commitResultHtml(r) {
      const cu = safeUrl(r.commit_url), mu = safeUrl(r.mr_url);
      let h = `<div class="commit-card">` +
        `<div class="confirm-msg ok"><i class="fas fa-circle-check"></i> Commit criado com sucesso!</div>` +
        `<div class="cc-result">` +
        `<div class="rep-row"><span>Branch</span><b>${escHtml(r.branch || "")}</b></div>` +
        `<div class="rep-row"><span>Commit</span><b>${escHtml(r.commit_sha || "")}</b></div>` +
        `<div class="rep-row"><span>Ficheiros</span><b>${escHtml((r.files || []).join(", "))}</b></div>` +
        `</div><div class="rep-actions">`;
      if (mu) h += `<a class="rep-btn" href="${escHtml(mu)}" target="_blank" rel="noopener"><i class="fas fa-code-pull-request"></i> Abrir o Merge Request</a>`;
      if (cu) h += `<a class="rep-btn ghost" href="${escHtml(cu)}" target="_blank" rel="noopener">Ver o commit</a>`;
      h += `</div>`;
      if (!mu) h += `<div class="cc-note"><i class="fas fa-circle-info"></i> O commit foi criado, mas o Merge Request falhou — podes abri-lo manualmente no GitLab.</div>`;
      return h + `</div>`;
    }

    async function renderCommitFlow(requestText) {
      const bub = addAiCard('<div class="confirm-msg"><i class="fas fa-spinner fa-spin"></i> A gerar o código e o plano de commit…</div>');
      let plan = null;
      try {
        const res = await fetch(`${BASE_URL}/api/generate-commit`, {
          method: "POST",
          headers,   // leva os X-GL-* do workspace ativo (multi-tenant)
          body: JSON.stringify({ request: requestText, model: settings.model }),
        });
        const d = await res.json();
        if (!d.ok) {
          bub.innerHTML = `<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> ${escHtml(d.error || "Não consegui gerar o plano.")}</div>`;
          return;
        }
        plan = d.plan;
      } catch (e) {
        bub.innerHTML = '<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> Erro ao contactar o servidor.</div>';
        console.error(e);
        return;
      }

      bub.innerHTML = commitPreviewHtml(plan);
      scrollBottom();
      const ok = bub.querySelector(".cc-approve");
      const no = bub.querySelector(".cc-cancel");
      no.onclick = () => {
        bub.innerHTML = '<div class="confirm-msg">Commit cancelado — nada foi escrito no GitLab.</div>';
      };
      ok.onclick = async () => {
        ok.disabled = no.disabled = true;
        ok.innerHTML = '<i class="fas fa-spinner fa-spin"></i> A criar branch + commit + MR…';
        try {
          const res = await fetch(`${BASE_URL}/api/confirm-commit`, {
            method: "POST", headers, body: JSON.stringify({ plan }),
          });
          const r = await res.json();
          if (r.ok) {
            bub.innerHTML = commitResultHtml(r);
            history.push({ role: "assistant", content: `[Commit criado na branch ${r.branch}]` });
          } else {
            bub.innerHTML = `<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> ${escHtml(r.error || "Não foi possível criar o commit.")}</div>`;
          }
        } catch (e) {
          bub.innerHTML = '<div class="confirm-msg err">Erro ao executar o commit.</div>';
          console.error(e);
        }
        scrollBottom();
      };
    }

    // ── Streaming chat ────────────────────────────────────────────────────────
    async function streamChat() {
      const bub = createStreamBubble();
      let fullText = "";
      let pendingAction = null;

      activeController = new AbortController();

      const res = await fetch(`${BASE_URL}/api/chat`, {
        method: "POST",
        headers,
        body: JSON.stringify({ model: settings.model, messages: [...history] }),
        signal: activeController.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop();

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            try {
              const chunk = JSON.parse(line.slice(6));
              if (chunk.action) pendingAction = chunk.action;
              if (chunk.content) {
                fullText += chunk.content;
                appendStreamChunk(bub, chunk.content);
              }
              if (chunk.done && !pendingAction) {
                finalizeStreamBubble(bub, fullText);
              }
            } catch(e) { /* ignore malformed line */ }
          }
        }
        if (pendingAction) {
          if (fullText.trim()) {
            // The model said something before proposing the action — keep it
            // (render it) and show the card/form in a new bubble.
            finalizeStreamBubble(bub, fullText);
            showActionCard(pendingAction);
          } else if (pendingAction.tool === "update_issue" && pendingAction.args && pendingAction.args.iid) {
            // Updates open the PRE-FILLED form (ignore any fields the model invented).
            bub.closest(".msg")?.remove();
            openEditForm(pendingAction.args.iid);
          } else {
            renderConfirmCard(bub, pendingAction);
          }
          return { action: pendingAction };
        }
        // Safety: finalise even if server forgets to send done.
        finalizeStreamBubble(bub, fullText);
      } catch (e) {
        if (e.name === "AbortError") {
          finalizeStreamBubble(bub, fullText + "\n\n_(cancelado)_");
        } else {
          throw e;
        }
      }

      return { text: fullText.trim() || "Sem resposta." };
    }

    // ── Confirmation card for write actions (created only after you approve) ──
    // Used both by the model path (streamChat) and by explicit commands (showActionCard).
    function showActionCard(action) {
      // Updates go through the pre-filled edit form (never the model's invented
      // fields); only close/delete use a plain confirmation card.
      if (action.tool === "update_issue" && action.args && action.args.iid) {
        openEditForm(action.args.iid);
        return;
      }
      removeWelcome();
      const msgs = document.getElementById("messages");
      const wrap = document.createElement("div");
      wrap.className = "msg ai";
      const av = document.createElement("div");
      av.className = "avatar"; av.textContent = "AI";
      const bub = document.createElement("div");
      bub.className = "bubble";
      wrap.appendChild(av); wrap.appendChild(bub);
      msgs.appendChild(wrap);
      renderConfirmCard(bub, action);
    }

    function renderConfirmCard(bub, action) {
      const typing = bub.querySelector(".typing"); if (typing) typing.remove();
      const cursor = bub.querySelector(".cursor"); if (cursor) cursor.remove();
      const raw = bub.querySelector(".stream-raw"); if (raw) raw.remove();

      const danger = action.tool === "delete_issue";
      const card = document.createElement("div");
      card.className = "confirm-card" + (danger ? " danger" : "");
      bub.appendChild(card);

      const icon = danger ? "fa-triangle-exclamation" : "fa-circle-question";
      const build = (summaryText) => {
        card.innerHTML =
          `<div class="confirm-summary"><i class="fas ${icon}"></i> ${escHtml(summaryText)}</div>`;
        const btns = document.createElement("div");
        btns.className = "confirm-btns";
        const yes = document.createElement("button");
        yes.type = "button";
        yes.className = "confirm-yes" + (danger ? " danger" : "");
        yes.textContent = danger ? "Apagar" : "Confirmar";
        yes.onclick = () => doConfirm(card, action);
        const no = document.createElement("button");
        no.type = "button"; no.className = "confirm-no"; no.textContent = "Cancelar";
        no.onclick = () => { card.innerHTML = '<div class="confirm-msg">Ação cancelada.</div>'; };
        btns.appendChild(yes); btns.appendChild(no);
        card.appendChild(btns);
        scrollBottom();
      };

      // For issue actions, VERIFY the issue exists and show its title first —
      // so a hallucinated/wrong number can't be confirmed blindly (esp. delete).
      const iid = action.args && action.args.iid;
      if (["close_issue", "update_issue", "delete_issue"].includes(action.tool) && iid) {
        card.innerHTML = '<div class="confirm-msg">A verificar a issue…</div>';
        fetch(`${BASE_URL}/gitlab/issue/${iid}`, { headers })
          .then(r => r.ok ? r.json() : Promise.reject(r.status))
          .then(d => build(`${action.summary} — «${d.title}»`))
          .catch(() => {
            card.innerHTML = `<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> Não encontrei a issue #${escHtml(String(iid))}. Confirma o número.</div>`;
            scrollBottom();
          });
      } else {
        build(action.summary || "Confirmar esta ação?");
      }
      scrollBottom();
    }

    async function doConfirm(card, action) {
      const btns = card.querySelector(".confirm-btns");
      if (btns) btns.innerHTML = '<span class="confirm-msg">A executar…</span>';
      try {
        const res = await fetch(`${BASE_URL}/api/confirm-action`, {
          method: "POST", headers, body: JSON.stringify(action),
        });
        const r = await res.json();
        if (r.ok) {
          const verb = r.action === "create" ? "criada"
                     : r.action === "close" ? "fechada"
                     : r.action === "delete" ? "apagada"
                     : "atualizada";
          let html = `<div class="confirm-msg ok"><i class="fas fa-check"></i> Issue #${r.iid} ${verb} com sucesso.</div>`;
          if (safeUrl(r.web_url)) html += `<a class="confirm-link" href="${escHtml(safeUrl(r.web_url))}" target="_blank" rel="noopener">Abrir no GitLab</a>`;
          card.innerHTML = html;
          loadStats();   // a escrita mudou os números → refresca o painel
        } else {
          card.innerHTML = `<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> ${escHtml(r.error || "Não foi possível executar.")}</div>`;
        }
      } catch (e) {
        card.innerHTML = '<div class="confirm-msg err">Erro ao executar a ação.</div>';
        console.error(e);
      }
      scrollBottom();
    }

    // ── Issue form — create OR edit (edit pre-fills every field) ─────────────
    function renderIssueForm(opts) {
      const isEdit = (opts.mode || "create") === "edit";
      removeWelcome();
      const msgs = document.getElementById("messages");
      const wrap = document.createElement("div");
      wrap.className = "msg ai";
      const av = document.createElement("div");
      av.className = "avatar"; av.textContent = "AI";
      const bub = document.createElement("div");
      bub.className = "bubble issue-form";
      bub.innerHTML = `
        <div class="cf-head">${isEdit ? "Editar issue #" + opts.iid : "Nova issue"}</div>
        ${isEdit ? "" : `<div class="cf-templates">
          <span class="cf-tlabel">Template:</span>
          <button type="button" class="cf-tmpl active" data-tmpl="">Vazio</button>
          <button type="button" class="cf-tmpl" data-tmpl="bug">Bug</button>
          <button type="button" class="cf-tmpl" data-tmpl="task">Tarefa</button>
        </div>`}
        <div class="cf-field">
          <span class="cf-tlabel">Título *</span>
          <input type="text" class="cf-input cf-titlein" placeholder="Título da issue" />
          <div class="cf-dup"></div>
        </div>
        <div class="cf-field">
          <span class="cf-tlabel">Descrição
            <button type="button" class="cf-ai"><i class="fas fa-wand-magic-sparkles"></i> Gerar com IA</button>
          </span>
          <textarea class="cf-input cf-desc" rows="4" placeholder="Descrição (opcional)"></textarea>
        </div>
        <div class="cf-field">
          <span class="cf-tlabel">Labels</span>
          <div class="cf-labels"><span class="cf-muted">a carregar…</span></div>
          <input type="text" class="cf-input cf-customlabel" placeholder="escrever label e Enter (opcional)" />
        </div>
        <div class="cf-row">
          <div class="cf-field cf-half">
            <span class="cf-tlabel">Milestone</span>
            <select class="cf-input cf-milestone"><option value="">Nenhuma</option></select>
          </div>
          <div class="cf-field cf-half">
            <span class="cf-tlabel">Data limite</span>
            <input type="date" class="cf-input cf-due" />
          </div>
        </div>
        <div class="cf-row">
          <div class="cf-field cf-half">
            <span class="cf-tlabel">Assignee</span>
            <select class="cf-input cf-assignee"><option value="">Ninguém</option></select>
          </div>
          <div class="cf-field cf-half cf-conf-field">
            <label class="cf-checkrow"><input type="checkbox" class="cf-confidential" /> Confidencial</label>
          </div>
        </div>
        <div class="cf-actions">
          <button type="button" class="cf-create">${isEdit ? "Guardar alterações" : "Criar issue"}</button>
          <button type="button" class="cf-cancel">Cancelar</button>
          <span class="cf-status"></span>
        </div>`;
      wrap.appendChild(av); wrap.appendChild(bub);
      msgs.appendChild(wrap);
      scrollBottom();

      const selectedLabels = new Set(opts.labels || []);
      let currentTemplate = "";
      const $ = (sel) => bub.querySelector(sel);
      const titleIn = $(".cf-titlein"), descIn = $(".cf-desc"), dupBox = $(".cf-dup");
      const labelsBox = $(".cf-labels"), customLabel = $(".cf-customlabel");
      const msSelect = $(".cf-milestone"), dueIn = $(".cf-due"), statusEl = $(".cf-status");
      const asSelect = $(".cf-assignee"), confIn = $(".cf-confidential");

      titleIn.value = opts.title || "";
      descIn.value = opts.description || "";
      dueIn.value = opts.dueDate || "";
      confIn.checked = !!opts.confidential;
      titleIn.focus();

      // Templates pre-fill the description scaffold
      bub.querySelectorAll(".cf-tmpl").forEach(b => {
        b.onclick = () => {
          currentTemplate = b.dataset.tmpl;
          bub.querySelectorAll(".cf-tmpl").forEach(x => x.classList.remove("active"));
          b.classList.add("active");
          if (currentTemplate === "bug")
            descIn.value = "**O que acontece:**\n\n**Passos para reproduzir:**\n1. \n2. \n\n**Resultado esperado:**\n";
          else if (currentTemplate === "task")
            descIn.value = "**Objetivo:**\n\n**Critérios de aceitação:**\n- [ ] ";
        };
      });

      // AI-generated description from the title
      $(".cf-ai").onclick = async () => {
        const title = titleIn.value.trim();
        if (!title) { titleIn.focus(); return; }
        const btn = $(".cf-ai"); const old = btn.innerHTML;
        btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> A gerar…';
        try {
          const res = await fetch(`${BASE_URL}/api/generate-description`, {
            method: "POST", headers,
            body: JSON.stringify({ title, template: currentTemplate, model: settings.model }),
          });
          const d = await res.json();
          if (d.description) descIn.value = d.description;
        } catch (e) { /* ignore */ }
        btn.disabled = false; btn.innerHTML = old;
      };

      // Duplicate-title warning (debounced) — only when creating a new issue
      let dupTimer = null;
      if (!isEdit) titleIn.oninput = () => {
        clearTimeout(dupTimer);
        const title = titleIn.value.trim();
        if (title.length < 3) { dupBox.innerHTML = ""; return; }
        dupTimer = setTimeout(async () => {
          try {
            const res = await fetch(`${BASE_URL}/gitlab/duplicate?title=${encodeURIComponent(title)}`, { headers });
            const d = await res.json();
            if (d.matches && d.matches.length)
              dupBox.innerHTML = `<i class="fas fa-triangle-exclamation"></i> Parecido com: ` +
                d.matches.map(m => `#${m.iid} ${escHtml(m.title)}`).join(" · ");
            else dupBox.innerHTML = "";
          } catch (e) { dupBox.innerHTML = ""; }
        }, 450);
      };

      // Load real project labels as toggle chips, pre-selecting the issue's current ones
      const addLabelChip = (name, on) => {
        const chip = document.createElement("button");
        chip.type = "button"; chip.className = "cf-labelchip" + (on ? " on" : ""); chip.textContent = name;
        chip.onclick = () => {
          if (selectedLabels.has(name)) { selectedLabels.delete(name); chip.classList.remove("on"); }
          else { selectedLabels.add(name); chip.classList.add("on"); }
        };
        labelsBox.appendChild(chip);
      };
      (async () => {
        try {
          const res = await fetch(`${BASE_URL}/gitlab/labels`, { headers });
          const labels = (await res.json()).labels || [];
          labelsBox.innerHTML = "";
          const seen = new Set();
          labels.slice(0, 40).forEach(l => { seen.add(l.name); addLabelChip(l.name, selectedLabels.has(l.name)); });
          (opts.labels || []).forEach(n => { if (!seen.has(n)) addLabelChip(n, true); });
          if (!labelsBox.children.length) labelsBox.innerHTML = '<span class="cf-muted">sem labels no projeto</span>';
        } catch (e) { labelsBox.innerHTML = '<span class="cf-muted">erro ao carregar labels</span>'; }
      })();

      // Custom label on Enter
      customLabel.onkeydown = (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        const v = customLabel.value.trim();
        if (!v || selectedLabels.has(v)) { customLabel.value = ""; return; }
        selectedLabels.add(v); customLabel.value = "";
        const chip = document.createElement("button");
        chip.type = "button"; chip.className = "cf-labelchip on"; chip.textContent = v;
        chip.onclick = () => { selectedLabels.delete(v); chip.remove(); };
        labelsBox.appendChild(chip);
      };

      // Garante que o valor atual existe como <option> antes de o selecionar —
      // senão um valor sem option faz o <select> cair para "" e, ao guardar,
      // desassociava a milestone/assignee em silêncio.
      const ensureOption = (sel, value, label) => {
        const v = String(value);
        if (![...sel.options].some(o => o.value === v)) {
          const opt = document.createElement("option");
          opt.value = v; opt.textContent = label;
          sel.appendChild(opt);
        }
      };

      // Load milestones (pre-select the issue's current one, even se estiver fechada)
      (async () => {
        try {
          const res = await fetch(`${BASE_URL}/gitlab/milestones`, { headers });
          (await res.json()).milestones?.forEach(m => {
            const opt = document.createElement("option");
            opt.value = m.id; opt.textContent = m.title;
            msSelect.appendChild(opt);
          });
        } catch (e) { /* ignore */ }
        if (opts.milestoneId) {
          ensureOption(msSelect, opts.milestoneId, opts.milestoneTitle || `#${opts.milestoneId}`);
          msSelect.value = String(opts.milestoneId);
        }
      })();

      // Load project members for the assignee dropdown (pre-select current)
      (async () => {
        try {
          const res = await fetch(`${BASE_URL}/gitlab/members`, { headers });
          (await res.json()).members?.forEach(m => {
            const opt = document.createElement("option");
            opt.value = m.id; opt.textContent = m.name;
            asSelect.appendChild(opt);
          });
        } catch (e) { /* ignore */ }
        if (opts.assigneeId) {
          ensureOption(asSelect, opts.assigneeId, opts.assigneeName || `#${opts.assigneeId}`);
          asSelect.value = String(opts.assigneeId);
        }
      })();

      $(".cf-cancel").onclick = () => { bub.innerHTML = `<div class="confirm-msg">${isEdit ? "Edição" : "Criação"} cancelada.</div>`; };

      $(".cf-create").onclick = async () => {
        const title = titleIn.value.trim();
        if (!title) { statusEl.className = "cf-status err"; statusEl.textContent = "Indica um título."; titleIn.focus(); return; }
        const custom = customLabel.value.trim();
        if (custom) selectedLabels.add(custom);
        const args = {
          title,
          description: descIn.value,
          labels: Array.from(selectedLabels).join(","),
          due_date: dueIn.value || "",
          milestone_id: msSelect.value || "",
          assignee_ids: asSelect.value ? [Number(asSelect.value)] : [],
          confidential: confIn.checked,
        };
        if (isEdit) args.iid = opts.iid;
        const btn = $(".cf-create"); btn.disabled = true;
        statusEl.className = "cf-status"; statusEl.textContent = isEdit ? "A guardar…" : "A criar…";
        try {
          const res = await fetch(`${BASE_URL}/api/confirm-action`, {
            method: "POST", headers,
            body: JSON.stringify({ tool: isEdit ? "update_issue" : "create_issue", args }),
          });
          const r = await res.json();
          if (r.ok) {
            const verb = isEdit ? "atualizada" : "criada";
            let html = `<div class="confirm-msg ok"><i class="fas fa-check"></i> Issue #${r.iid} ${verb} com sucesso.</div>`;
            if (safeUrl(r.web_url)) html += `<a class="confirm-link" href="${escHtml(safeUrl(r.web_url))}" target="_blank" rel="noopener">Abrir no GitLab</a>`;
            bub.innerHTML = html;
            loadStats();   // refresca o painel de estatísticas
          } else {
            btn.disabled = false;
            statusEl.className = "cf-status err"; statusEl.textContent = r.error || "Não foi possível guardar.";
          }
        } catch (e) {
          btn.disabled = false;
          statusEl.className = "cf-status err"; statusEl.textContent = "Erro ao guardar.";
        }
        scrollBottom();
      };
    }

    function renderCreateForm(prefillTitle) {
      renderIssueForm({ mode: "create", title: prefillTitle || "" });
    }
    async function openEditForm(iid) {
      removeWelcome();
      try {
        const res = await fetch(`${BASE_URL}/gitlab/issue/${iid}`, { headers });
        if (!res.ok) throw new Error("HTTP " + res.status);
        const d = await res.json();
        renderIssueForm({
          mode: "edit", iid: d.iid, title: d.title, description: d.description,
          labels: d.labels || [], milestoneId: d.milestone_id, milestoneTitle: d.milestone_title,
          dueDate: d.due_date, assigneeId: d.assignee_id, assigneeName: d.assignee_name,
          confidential: d.confidential,
        });
      } catch (e) {
        showError(`Não consegui carregar a issue #${iid}.`);
      }
    }

    // ── Chart rendering (inline in a chat bubble) ─────────────────────────────
    async function renderChartIntent(chartName, params = {}) {
      removeWelcome();
      const msgs = document.getElementById("messages");

      const wrap = document.createElement("div");
      wrap.className = "msg ai";
      const av = document.createElement("div");
      av.className = "avatar";
      av.textContent = "AI";
      const bub = document.createElement("div");
      bub.className = "bubble";

      const typing = document.createElement("div");
      typing.className = "typing";
      typing.innerHTML = `<div class="dot"></div><div class="dot"></div><div class="dot"></div>`;
      bub.appendChild(typing);
      wrap.appendChild(av);
      wrap.appendChild(bub);
      msgs.appendChild(wrap);
      scrollBottom();

      try {
        const qs = new URLSearchParams(params).toString();
        const url = `${BASE_URL}/gitlab/chart/${chartName}${qs ? "?" + qs : ""}`;
        const res = await fetch(url, { headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const config = await res.json();

        typing.remove();

        const title = document.createElement("div");
        title.className = "chart-title";
        title.textContent = config.title;
        bub.appendChild(title);

        const container = document.createElement("div");
        container.className = "chart-container";
        const canvas = document.createElement("canvas");
        container.appendChild(canvas);
        bub.appendChild(container);

        const isCircular = config.type === "pie" || config.type === "doughnut";
        new Chart(canvas, {
          type: config.type,
          data: { labels: config.labels, datasets: config.datasets },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: {
                display: isCircular,
                position: "bottom",
                labels: { color: "#f0f0f0", font: { size: 10 }, padding: 8, boxWidth: 12 },
              },
              tooltip: {
                backgroundColor: "#2e2f31",
                borderColor: "#8B88F8",
                borderWidth: 1,
                titleColor: "#f0f0f0",
                bodyColor: "#f0f0f0",
              },
            },
            scales: isCircular ? {} : {
              x: { ticks: { color: "#aaa", font: { size: 10 } }, grid: { color: "#3a3b3d" } },
              y: {
                ticks: { color: "#aaa", font: { size: 10 }, precision: 0 },
                grid: { color: "#3a3b3d" },
                beginAtZero: true,
              },
            },
          },
        });

        return config.title;
      } catch (e) {
        typing.remove();
        const err = document.createElement("div");
        err.style.color = "#ff8080";
        err.style.fontSize = "12px";
        err.textContent = `Não consegui gerar o gráfico: ${e.message}`;
        bub.appendChild(err);
        console.error(e);
        return null;
      } finally {
        scrollBottom();
      }
    }

    // ── Send button toggles to a Cancel button while streaming ────────────────
    function setSendButton(state) {
      const btn = document.getElementById("send-btn");
      btn.disabled = false;
      if (state === "loading") {
        btn.classList.add("cancel");
        btn.innerHTML = '<i class="fas fa-stop"></i>';
        btn.setAttribute("aria-label", "Cancelar");
        btn.onclick = cancelStream;
      } else {
        btn.classList.remove("cancel");
        btn.innerHTML = '<i class="fas fa-paper-plane"></i>';
        btn.setAttribute("aria-label", "Enviar");
        btn.onclick = send;
      }
    }
    function cancelStream() {
      if (activeController) activeController.abort();
    }

    // ── Main send ─────────────────────────────────────────────────────────────
    // ── Contextual follow-up chips (rendered above the input after a reply) ───
    async function updateFollowups() {
      try {
        const res = await fetch(`${BASE_URL}/api/suggestions`, {
          method: "POST",
          headers,
          body: JSON.stringify({ messages: history.slice(-6), model: settings.model }),
        });
        if (!res.ok) return;
        const data = await res.json();
        renderFollowups(data.suggestions || []);
      } catch (e) {
        /* silent — follow-up chips are a nice-to-have, never block the chat */
      }
    }
    function renderFollowups(items) {
      const bar = document.getElementById("followups");
      bar.innerHTML = "";
      for (const s of (items || []).slice(0, 5)) {
        if (!s) continue;
        const b = document.createElement("button");
        b.type = "button";
        b.className = "chip";
        b.textContent = s;
        b.onclick = () => { document.getElementById("input").value = s; send(); };
        bar.appendChild(b);
      }
    }
    function clearFollowups() {
      document.getElementById("followups").innerHTML = "";
    }

    async function send() {
      if (loading) return;
      const input = document.getElementById("input");
      const text = input.value.trim();
      if (!text) return;

      input.value = "";
      input.style.height = "auto";
      loading = true;
      setStatus(true);
      setSendButton("loading");
      clearFollowups();

      addMsg("user", text);
      history.push({ role: "user", content: text });

      let qaReply = false;  // only fetch follow-up chips after a real Q&A turn
      try {
        const intent = detectIntent(text);

        if (intent.type === "chart") {
          const title = await renderChartIntent(intent.chart, intent.params || {});
          history.push({
            role: "assistant",
            content: title ? `[Gráfico apresentado: ${title}]` : "[Erro ao gerar gráfico]",
          });
          qaReply = true;   // mostra chips de seguimento depois de um gráfico

        } else if (intent.type === "create_form") {
          renderCreateForm(intent.title || "");
          history.push({ role: "assistant",
            content: "[Formulário de criação de issue aberto]" });

        } else if (intent.type === "delete_action") {
          showActionCard({
            tool: "delete_issue",
            args: { iid: Number(intent.iid) },
            summary: `Apagar DEFINITIVAMENTE a issue #${intent.iid} — irreversível`,
          });
          history.push({ role: "assistant",
            content: `[Confirmação de apagar a issue #${intent.iid}]` });

        } else if (intent.type === "close_issue") {
          showActionCard({
            tool: "close_issue",
            args: { iid: Number(intent.iid) },
            summary: `Fechar a issue #${intent.iid}`,
          });
          history.push({ role: "assistant",
            content: `[Confirmação de fechar a issue #${intent.iid}]` });

        } else if (intent.type === "edit_form") {
          await openEditForm(intent.iid);
          history.push({ role: "assistant",
            content: `[Formulário de edição da issue #${intent.iid} aberto]` });

        } else if (intent.type === "export_issues") {
          exportIssues(intent.state);
          const label = intent.state === "all" ? "todas"
                      : intent.state === "opened" ? "abertas" : "fechadas";
          const streamBub = createStreamBubble();
          const reply = `A exportar issues (${label}) para CSV...\n\nO ficheiro \`gitlab_issues.csv\` vai ser descarregado automaticamente.`;
          finalizeStreamBubble(streamBub, reply);
          history.push({ role: "assistant", content: reply });
          qaReply = true;   // chips de seguimento depois de exportar

        } else if (intent.type === "export_commits") {
          exportCommits();
          const streamBub = createStreamBubble();
          const reply = "A exportar commits para CSV...\n\nO ficheiro `gitlab_commits.csv` vai ser descarregado automaticamente (história completa do projeto).";
          finalizeStreamBubble(streamBub, reply);
          history.push({ role: "assistant", content: reply });
          qaReply = true;   // chips de seguimento depois de exportar

        } else if (intent.type === "sprint_report") {
          await renderSprintReport();
          history.push({ role: "assistant", content: "[Relatório do projeto gerado]" });
          qaReply = true;   // chips de seguimento depois do relatório

        } else if (intent.type === "code_commit") {
          await renderCommitFlow(text);
          history.push({ role: "assistant",
            content: "[Proposta de commit por IA apresentada para confirmação]" });

        } else if (intent.type === "blame_analysis") {
          await renderBlameAnalysis(intent, text);
          history.push({ role: "assistant",
            content: `[Investigação de código: ${intent.file}${intent.line ? ":" + intent.line : ""}]` });
          qaReply = true;

        } else if (intent.type === "blame_help") {
          const bub = createStreamBubble();
          const reply = "Para investigar código diz-me o ficheiro (e a linha, se quiseres a análise do erro):\n\n" +
            "- `quem alterou o src/app.js?` — histórico e autores do ficheiro\n" +
            "- `analisa o erro em server.py linha 120` — blame + diff + análise IA\n" +
            "- ou cola um *stack trace* — eu extraio o ficheiro e a linha.";
          finalizeStreamBubble(bub, reply);
          history.push({ role: "assistant", content: reply });
          qaReply = true;

        } else {
          const result = await streamChat();
          if (result.action) {
            history.push({
              role: "assistant",
              content: `[Proposta de ação a aguardar confirmação: ${result.action.summary}]`,
            });
          } else {
            history.push({ role: "assistant", content: result.text });
            qaReply = true;   // plain answer → contextual chips make sense
          }

          // Token-budget-aware history trim: keep the most recent CAP messages.
          if (history.length > HISTORY_CAP) {
            history.splice(0, history.length - HISTORY_CAP);
          }
        }
      } catch (err) {
        const w = document.getElementById("stream-wrap");
        if (w) w.remove();
        showError("Não foi possível obter resposta. Verifica a ligação e tenta novamente.");
        console.error(err);
        history.pop();
      }

      activeController = null;
      loading = false;
      setSendButton("idle");
      setStatus(false);
      document.getElementById("input").focus();
      persistHistory();                // guarda a conversa deste workspace
      if (qaReply) updateFollowups();  // chips only after a Q&A (saves a Groq call/turn)
    }
