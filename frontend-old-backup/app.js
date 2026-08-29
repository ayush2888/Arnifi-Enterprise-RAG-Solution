const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("question-input");
const sendBtn = document.getElementById("send-btn");
const statusEl = document.getElementById("status");
const newChatBtn = document.getElementById("new-chat-btn");
const searchChatsBtn = document.getElementById("search-chats-btn");
const searchDialog = document.getElementById("search-dialog");
const searchChatsInput = document.getElementById("search-chats-input");
const searchChatsList = document.getElementById("search-chats-list");

const CHAT_STORAGE_KEY = "arnifi.chatHistory";
const MAX_STORED_CHATS = 50;

let isStreaming = false;
let lastQuestion = "";
/** @type {Array<{source_index:number, doc_title:string, heading_path:string, source_url:string, snippet?:string}>} */
let latestSources = [];
/** @type {ReturnType<typeof setInterval> | null} */
let loaderInterval = null;
/** @type {number} */
let lastScrollAt = 0;

/** @type {string | null} */
let currentChatId = null;
/** @type {Array<{role:string, content:string, sources?:Array}>} */
let currentMessages = [];

const LOADER_STATUS_LINES = [
  "Searching knowledge…",
  "Matching relevant passages…",
  "Preparing answer…",
];

const WELCOME_HTML = `
  <div class="message assistant welcome" id="welcome-message">
    <div class="avatar">AI</div>
    <div class="bubble">
      <p>Hello. Ask about Arnifi services, company setup, visas, funds, pricing, and compliance. Answers are grounded in the indexed knowledge base.</p>
      <div class="suggestions">
        <button type="button" class="suggestion" data-q="What is a UAE golden visa and who qualifies?">UAE golden visa</button>
        <button type="button" class="suggestion" data-q="What KYC documents are required for Cayman Island company setup?">Cayman company KYC</button>
        <button type="button" class="suggestion" data-q="How do I set up a business in Malaysia?">Malaysia business setup</button>
        <button type="button" class="suggestion" data-q="Explain Cayman tokenised fund structure">Tokenised funds</button>
      </div>
    </div>
  </div>
`;

const SEARCHING_LOADER_HTML = `
  <div class="searching-loader" aria-live="polite" aria-busy="true">
    <div class="loader-icons" aria-hidden="true">
      <span class="loader-icon is-active" data-icon="globe">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="9"/>
          <path d="M3 12h18"/>
          <path d="M12 3a14 14 0 0 1 0 18"/>
          <path d="M12 3a14 14 0 0 0 0 18"/>
        </svg>
      </span>
      <span class="loader-icon" data-icon="doc">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>
          <path d="M14 3v5h5"/>
          <path d="M9 13h6"/>
          <path d="M9 17h4"/>
        </svg>
      </span>
      <span class="loader-icon" data-icon="sparkles">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 3l1.2 3.6L17 8l-3.8 1.4L12 13l-1.2-3.6L7 8l3.8-1.4L12 3z"/>
          <path d="M18.5 14l.7 2.1L21.5 17l-2.3.8-.7 2.2-.7-2.2L15.5 17l2.3-.9.7-2.1z"/>
          <path d="M5.5 15.5l.55 1.65L7.8 18l-1.75.6-.55 1.7-.55-1.7L3.2 18l1.75-.85.55-1.65z"/>
        </svg>
      </span>
    </div>
    <div class="loader-shimmer" aria-hidden="true"></div>
    <p class="loader-status">${LOADER_STATUS_LINES[0]}</p>
  </div>
`;

function setStatus(text, thinking = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("thinking", thinking);
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function citeChipMax() {
  return window.matchMedia("(max-width: 640px)").matches ? 22 : 36;
}

function shortTitle(title, max = citeChipMax()) {
  const t = (title || "Reference").trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max - 1)}…`;
}

function truncateTitle(text, max = 72) {
  const t = (text || "").trim().replace(/\s+/g, " ");
  if (t.length <= max) return t;
  return `${t.slice(0, max - 1)}…`;
}

function newChatId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `chat-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function loadChats() {
  try {
    const raw = localStorage.getItem(CHAT_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveChats(chats) {
  const capped = chats
    .slice()
    .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
    .slice(0, MAX_STORED_CHATS);
  localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(capped));
  return capped;
}

function getChatById(id) {
  return loadChats().find((c) => c.id === id) || null;
}

function upsertCurrentChat() {
  if (!currentChatId || !currentMessages.length) return;

  const firstUser = currentMessages.find((m) => m.role === "user");
  const title = truncateTitle(firstUser?.content || "New chat");
  const chats = loadChats().filter((c) => c.id !== currentChatId);
  chats.unshift({
    id: currentChatId,
    title,
    updatedAt: Date.now(),
    messages: currentMessages.map((m) => ({
      role: m.role,
      content: m.content,
      ...(m.sources ? { sources: m.sources } : {}),
    })),
  });
  saveChats(chats);
}

function relativeTime(ts) {
  const diff = Date.now() - (ts || 0);
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(ts).toLocaleDateString();
}

function scrollToBottom(force = false) {
  const now = Date.now();
  if (!force && now - lastScrollAt < 80) return;
  lastScrollAt = now;
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function createMessage(role, html = "") {
  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;
  wrap.innerHTML = `
    <div class="avatar">${role === "user" ? "You" : "AI"}</div>
    <div class="bubble-stack">
      <div class="bubble">${html}</div>
    </div>
  `;
  messagesEl.appendChild(wrap);
  scrollToBottom(true);
  return wrap;
}

function startSearchingLoader(bubble) {
  stopSearchingLoader();
  bubble.classList.add("is-searching");
  bubble.innerHTML = SEARCHING_LOADER_HTML;

  const icons = bubble.querySelectorAll(".loader-icon");
  const statusLine = bubble.querySelector(".loader-status");
  let tick = 0;

  loaderInterval = setInterval(() => {
    tick += 1;
    icons.forEach((icon, i) => {
      icon.classList.toggle("is-active", i === tick % icons.length);
    });
    if (statusLine) {
      statusLine.textContent = LOADER_STATUS_LINES[tick % LOADER_STATUS_LINES.length];
    }
  }, 1400);
}

function stopSearchingLoader(bubble) {
  if (loaderInterval) {
    clearInterval(loaderInterval);
    loaderInterval = null;
  }
  if (bubble) {
    bubble.classList.remove("is-searching");
    const loader = bubble.querySelector(".searching-loader");
    if (loader) loader.remove();
  }
}

function bindSuggestionClicks(root = document) {
  root.querySelectorAll(".suggestion").forEach((btn) => {
    btn.addEventListener("click", () => handleSubmit(btn.dataset.q));
  });
}

function renderAnswerHtml(answerText, sources) {
  const byIndex = new Map(
    (sources || []).map((s) => [Number(s.source_index), s])
  );

  const withPlaceholders = answerText.replace(
    /\[Source\s+(\d+)\]/gi,
    (_, n) => `%%CITE_${n}%%`
  );
  let html = marked.parse(withPlaceholders);

  html = html.replace(/%%CITE_(\d+)%%/g, (_, n) => {
    const idx = Number(n);
    const src = byIndex.get(idx);
    const title = shortTitle(src?.doc_title || `Reference ${idx}`);
    const full = escapeHtml(src?.doc_title || `Reference ${idx}`);
    return `<button type="button" class="cite-chip" data-source-index="${idx}" title="${full}">${escapeHtml(title)}</button>`;
  });

  return html;
}

function buildReferencesBlock(sources, answerWrap) {
  const refs = document.createElement("div");
  refs.className = "answer-refs";

  if (!sources.length) {
    refs.innerHTML = `<p class="refs-empty">No matching references for this answer.</p>`;
    return refs;
  }

  const list = document.createElement("div");
  list.className = "refs-list";
  list.innerHTML = `<h3 class="refs-heading">References</h3>`;

  sources.forEach((source) => {
    const card = document.createElement("article");
    card.className = "ref-card";
    card.dataset.sourceIndex = String(source.source_index);

    const title = escapeHtml(source.doc_title || `Reference ${source.source_index}`);
    const section = escapeHtml(source.heading_path || "");
    const snippet = escapeHtml(source.snippet || "");
    const url = source.source_url || "";
    const canOpen = /^https?:\/\//i.test(url) && !url.startsWith("whatsapp://");

    card.innerHTML = `
      <h4>${title}</h4>
      ${section ? `<p class="ref-section">${section}</p>` : ""}
      ${snippet ? `<p class="ref-snippet">${snippet}</p>` : ""}
      ${canOpen ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">Open</a>` : ""}
    `;
    list.appendChild(card);
  });

  refs.appendChild(list);

  answerWrap.addEventListener("click", (e) => {
    const chip = e.target.closest(".cite-chip");
    if (!chip) return;
    const idx = chip.dataset.sourceIndex;
    const card = list.querySelector(`.ref-card[data-source-index="${idx}"]`);
    if (!card) return;
    list.querySelectorAll(".ref-card").forEach((c) => c.classList.remove("active"));
    card.classList.add("active");
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });

  return refs;
}

function renderStoredMessages(messages) {
  messagesEl.innerHTML = "";
  messages.forEach((msg) => {
    if (msg.role === "user") {
      createMessage("user", `<p>${escapeHtml(msg.content)}</p>`);
      return;
    }
    const wrap = createMessage("assistant");
    const stack = wrap.querySelector(".bubble-stack");
    const bubble = stack.querySelector(".bubble");
    const sources = msg.sources || [];
    bubble.innerHTML = renderAnswerHtml(msg.content, sources);
    stack.appendChild(buildReferencesBlock(sources, bubble));
  });
  scrollToBottom(true);
}

function openChatById(id) {
  const chat = getChatById(id);
  if (!chat) return;

  if (currentChatId && currentMessages.length && currentChatId !== id) {
    upsertCurrentChat();
  }

  currentChatId = chat.id;
  currentMessages = (chat.messages || []).map((m) => ({
    role: m.role,
    content: m.content,
    ...(m.sources ? { sources: m.sources } : {}),
  }));
  latestSources = [];
  lastQuestion = "";
  stopSearchingLoader();
  renderStoredMessages(currentMessages);
  setStatus("Ready");
  inputEl.focus();
}

async function streamAnswer(question, msgWrap) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, source: "all" }),
  });

  if (!response.ok) {
    throw new Error(`Server error (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answerText = "";
  let sawToken = false;
  latestSources = [];

  const stack = msgWrap.querySelector(".bubble-stack");
  const answerBubble = stack.querySelector(".bubble");

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";

    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data: ")) continue;

      const event = JSON.parse(line.slice(6));
      if (event.type === "sources") {
        latestSources = event.sources || [];
        const statusLine = answerBubble.querySelector(".loader-status");
        if (statusLine) statusLine.textContent = "Matching relevant passages…";
        setStatus("Answering…", true);
      } else if (event.type === "token") {
        if (!sawToken) {
          sawToken = true;
          stopSearchingLoader(answerBubble);
          answerBubble.classList.add("streaming");
          setStatus("Answering…", true);
        }
        answerText += event.content;
        answerBubble.innerHTML = renderAnswerHtml(answerText, latestSources);
        scrollToBottom();
      } else if (event.type === "done") {
        stopSearchingLoader(answerBubble);
        answerBubble.classList.remove("streaming");
        answerBubble.innerHTML = renderAnswerHtml(answerText, latestSources);
        const existing = stack.querySelector(".answer-refs");
        if (existing) existing.remove();
        stack.appendChild(buildReferencesBlock(latestSources, answerBubble));
        scrollToBottom(true);
      }
    }
  }

  if (!stack.querySelector(".answer-refs")) {
    stopSearchingLoader(answerBubble);
    answerBubble.classList.remove("streaming");
    answerBubble.innerHTML = renderAnswerHtml(answerText, latestSources);
    stack.appendChild(buildReferencesBlock(latestSources, answerBubble));
    scrollToBottom(true);
  }

  return { answerText, sources: latestSources };
}

async function handleSubmit(question) {
  const text = (question || "").trim();
  if (!text || isStreaming) return;

  const welcome = document.getElementById("welcome-message");
  if (welcome) welcome.remove();

  if (!currentChatId) {
    currentChatId = newChatId();
    currentMessages = [];
  }

  isStreaming = true;
  sendBtn.disabled = true;
  lastQuestion = text;
  setStatus("Searching…", true);

  currentMessages.push({ role: "user", content: text });
  createMessage("user", `<p>${escapeHtml(text)}</p>`);
  inputEl.value = "";
  inputEl.style.height = "auto";

  const assistantWrap = createMessage("assistant");
  const answerBubble = assistantWrap.querySelector(".bubble");
  startSearchingLoader(answerBubble);

  try {
    const { answerText, sources } = await streamAnswer(text, assistantWrap);
    currentMessages.push({
      role: "assistant",
      content: answerText,
      sources: sources || [],
    });
    upsertCurrentChat();
    setStatus("Ready");
  } catch (err) {
    stopSearchingLoader(answerBubble);
    answerBubble.classList.remove("streaming");
    answerBubble.innerHTML = `
      <p>Couldn't reach the assistant. ${escapeHtml(err.message)}</p>
      <button type="button" class="retry-btn">Retry</button>
    `;
    const retry = answerBubble.querySelector(".retry-btn");
    if (retry) {
      retry.addEventListener("click", () => handleSubmit(lastQuestion));
    }
    // Drop the user turn if the assistant failed, so a retry doesn't double-save
    if (
      currentMessages.length &&
      currentMessages[currentMessages.length - 1].role === "user" &&
      currentMessages[currentMessages.length - 1].content === text
    ) {
      currentMessages.pop();
    }
    setStatus("Error");
  } finally {
    stopSearchingLoader();
    isStreaming = false;
    sendBtn.disabled = false;
    inputEl.focus();
  }
}

function resetChat() {
  if (isStreaming) return;
  upsertCurrentChat();
  stopSearchingLoader();
  currentChatId = null;
  currentMessages = [];
  messagesEl.innerHTML = WELCOME_HTML;
  latestSources = [];
  lastQuestion = "";
  setStatus("Ready");
  bindSuggestionClicks(messagesEl);
  inputEl.focus();
}

function filterChats(query) {
  const q = (query || "").trim().toLowerCase();
  const chats = loadChats();
  if (!q) return chats;
  return chats.filter((chat) => {
    if ((chat.title || "").toLowerCase().includes(q)) return true;
    return (chat.messages || []).some((m) =>
      String(m.content || "").toLowerCase().includes(q)
    );
  });
}

function renderSearchList(query = "") {
  const chats = filterChats(query);
  searchChatsList.innerHTML = "";

  if (!chats.length) {
    const empty = document.createElement("p");
    empty.className = "search-chats-empty";
    empty.textContent = query.trim()
      ? "No matches"
      : "No chats yet. Ask a question to start.";
    searchChatsList.appendChild(empty);
    return;
  }

  chats.forEach((chat) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "search-chat-row";
    row.setAttribute("role", "option");
    if (chat.id === currentChatId) row.classList.add("is-current");
    row.innerHTML = `
      <span class="search-chat-title">${escapeHtml(chat.title || "Untitled chat")}</span>
      <span class="search-chat-meta">${escapeHtml(relativeTime(chat.updatedAt))}</span>
    `;
    row.addEventListener("click", () => {
      openChatById(chat.id);
      closeSearchDialog();
    });
    searchChatsList.appendChild(row);
  });
}

function openSearchDialog() {
  upsertCurrentChat();
  searchDialog.hidden = false;
  searchChatsInput.value = "";
  renderSearchList("");
  document.body.style.overflow = "hidden";
  searchChatsInput.focus();
}

function closeSearchDialog() {
  searchDialog.hidden = true;
  document.body.style.overflow = "";
  searchChatsInput.value = "";
}

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  handleSubmit(inputEl.value);
});

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    handleSubmit(inputEl.value);
  }
});

inputEl.addEventListener("input", () => {
  inputEl.style.height = "auto";
  inputEl.style.height = `${Math.min(inputEl.scrollHeight, 140)}px`;
});

newChatBtn.addEventListener("click", resetChat);

searchChatsBtn.addEventListener("click", openSearchDialog);

searchDialog.querySelectorAll("[data-close-search]").forEach((el) => {
  el.addEventListener("click", closeSearchDialog);
});

searchChatsInput.addEventListener("input", () => {
  renderSearchList(searchChatsInput.value);
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !searchDialog.hidden) {
    e.preventDefault();
    closeSearchDialog();
  }
});

bindSuggestionClicks();
inputEl.focus();
