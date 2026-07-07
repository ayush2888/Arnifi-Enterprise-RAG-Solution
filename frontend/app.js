const messagesEl = document.getElementById("messages");
const sourcesListEl = document.getElementById("sources-list");
const sourcesHintEl = document.querySelector(".sources-hint");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("question-input");
const sendBtn = document.getElementById("send-btn");
const statusEl = document.getElementById("status");

let isStreaming = false;

function setStatus(text, thinking = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("thinking", thinking);
}

function createMessage(role, html = "") {
  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;
  wrap.innerHTML = `
    <div class="avatar">${role === "user" ? "You" : "AI"}</div>
    <div class="bubble">${html}</div>
  `;
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return wrap.querySelector(".bubble");
}

function renderSources(sources) {
  sourcesListEl.innerHTML = "";
  if (!sources.length) {
    sourcesHintEl.textContent = "No matching sources found.";
    return;
  }
  sourcesHintEl.textContent = `${sources.length} source(s) used for this answer.`;
  sources.forEach((source) => {
    const card = document.createElement("article");
    card.className = "source-card";
    card.innerHTML = `
      <div class="index">Source ${source.source_index}</div>
      <h3>${escapeHtml(source.doc_title)}</h3>
      <p>${escapeHtml(source.heading_path)}</p>
      <a href="${escapeHtml(source.source_url)}" target="_blank" rel="noopener">View article</a>
    `;
    sourcesListEl.appendChild(card);
  });
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function streamAnswer(question) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });

  if (!response.ok) {
    throw new Error(`Server error (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answerText = "";
  const answerBubble = createMessage("assistant");
  answerBubble.classList.add("streaming");

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
        renderSources(event.sources || []);
        setStatus("Generating answer...", true);
      } else if (event.type === "token") {
        answerText += event.content;
        answerBubble.innerHTML = marked.parse(answerText);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      } else if (event.type === "done") {
        answerBubble.classList.remove("streaming");
        answerBubble.innerHTML = marked.parse(answerText);
      }
    }
  }
}

async function handleSubmit(question) {
  const text = question.trim();
  if (!text || isStreaming) return;

  isStreaming = true;
  sendBtn.disabled = true;
  setStatus("Searching knowledge base...", true);

  createMessage("user", `<p>${escapeHtml(text)}</p>`);
  inputEl.value = "";

  try {
    await streamAnswer(text);
    setStatus("Ready");
  } catch (err) {
    createMessage("assistant", `<p>Sorry, something went wrong. ${escapeHtml(err.message)}</p>`);
    setStatus("Error");
  } finally {
    isStreaming = false;
    sendBtn.disabled = false;
    inputEl.focus();
  }
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

document.querySelectorAll(".suggestion").forEach((btn) => {
  btn.addEventListener("click", () => handleSubmit(btn.dataset.q));
});

inputEl.focus();
