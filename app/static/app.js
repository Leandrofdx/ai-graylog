const transcript = document.getElementById("transcript");
const composer = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const convList = document.getElementById("convList");
const chatTitle = document.getElementById("chatTitle");
const metaBox = document.getElementById("metaBox");
const btnNew = document.getElementById("btnNew");

let conversationId = null;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function clearWelcome() {
  const w = transcript.querySelector(".welcome");
  if (w) w.remove();
}

function addBubble(role, content, traceId) {
  clearWelcome();
  const box = el("article", `bubble ${role}`);
  box.appendChild(el("div", "role", role));
  box.appendChild(document.createTextNode(content));
  if (traceId) {
    const t = el("div", "trace", `trace_id ${traceId}`);
    box.appendChild(t);
  }
  transcript.appendChild(box);
  transcript.scrollTop = transcript.scrollHeight;
}

async function refreshConversations() {
  const res = await fetch("/api/conversations");
  if (!res.ok) return;
  const data = await res.json();
  convList.innerHTML = "";
  for (const c of data.conversations || []) {
    const b = el("button", "conv-item" + (c.id === conversationId ? " active" : ""), c.title);
    b.type = "button";
    b.addEventListener("click", () => openConversation(c.id));
    convList.appendChild(b);
  }
}

async function openConversation(id) {
  const res = await fetch(`/api/conversations/${id}`);
  if (!res.ok) return;
  const data = await res.json();
  conversationId = id;
  chatTitle.textContent = data.conversation.title || "Investigação";
  transcript.innerHTML = "";
  for (const m of data.messages || []) {
    addBubble(m.role, m.content, m.trace_id);
  }
  refreshConversations();
}

function newChat() {
  conversationId = null;
  chatTitle.textContent = "Agent Space";
  transcript.innerHTML = "";
  const welcome = document.createElement("div");
  welcome.className = "welcome";
  welcome.innerHTML = `
    <h2>Como posso ajudar na investigação?</h2>
    <p>Pergunte sobre erros, status do Graylog, pool do Postgres ou traces.</p>
    <div class="suggestions">
      <button type="button" data-q="Qual o status do sistema e do Graylog?">Status do sistema</button>
      <button type="button" data-q="Mostre erros e timeouts recentes nos logs">Erros recentes</button>
      <button type="button" data-q="Como está o banco e o pool sob carga?">Stats do banco</button>
    </div>`;
  transcript.appendChild(welcome);
  welcome.querySelectorAll("[data-q]").forEach((b) =>
    b.addEventListener("click", () => {
      input.value = b.getAttribute("data-q");
      composer.requestSubmit();
    })
  );
  refreshConversations();
}

composer.addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  addBubble("user", message);
  input.value = "";
  sendBtn.disabled = true;
  metaBox.innerHTML = '<span class="chip">investigando…</span>';

  const testRunId = window.__TEST_RUN_ID || `ui-${Date.now()}`;
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Request-Id": crypto.randomUUID(),
        "X-Test-Run-Id": testRunId,
      },
      body: JSON.stringify({ message, conversation_id: conversationId }),
    });
    const data = await res.json();
    if (!res.ok) {
      addBubble("assistant", `Falha: ${data.error || res.status}`);
      metaBox.innerHTML = '<span class="chip">erro</span>';
      return;
    }
    conversationId = data.conversation_id;
    for (const t of data.tools || []) {
      addBubble("tool", JSON.stringify(t, null, 2), data.trace_id);
    }
    addBubble("assistant", data.reply, data.trace_id);
    metaBox.innerHTML = `<span class="chip">trace ${ (data.trace_id || "").slice(0, 12) }…</span>`;
    chatTitle.textContent = message.length > 48 ? message.slice(0, 45) + "…" : message;
    refreshConversations();
  } catch (err) {
    addBubble("assistant", `Erro de rede: ${err}`);
    metaBox.innerHTML = '<span class="chip">offline</span>';
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
});

btnNew.addEventListener("click", newChat);
document.querySelectorAll("[data-q]").forEach((b) =>
  b.addEventListener("click", () => {
    input.value = b.getAttribute("data-q");
    composer.requestSubmit();
  })
);

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    composer.requestSubmit();
  }
});

refreshConversations();
