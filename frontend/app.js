/* Support Desk — @mention docs, collapsible agent context, session tracking */
const API = "";
const $ = (s) => document.querySelector(s);
const LS_SID = "q2_sid";
let SID = localStorage.getItem(LS_SID) || null;
let LIBRARY = [];
let MAX_UPLOAD_MB = 15;
let sending = false;
let stepCounter = 0;

const pages = ["chat", "kb", "memory", "about"];
function route() {
  const page = location.hash.replace("#/", "") || "chat";
  pages.forEach((p) => {
    const el = document.getElementById("page-" + p);
    const link = document.querySelector(`[data-page="${p}"]`);
    if (el) el.classList.toggle("active", p === page);
    if (link) link.classList.toggle("active", p === page);
  });
  if (page === "kb") loadDocs();
  if (page === "memory") loadMemoryPage();
  if (page === "chat") renderDocChips();
}
window.addEventListener("hashchange", route);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}
function md(s) {
  let t = esc(s ?? "");
  t = t.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  t = t.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>");
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  t = t.replace(/\n/g, "<br/>");
  return t;
}
function topicLabel(t) {
  if (typeof t === "string") return t;
  return t?.topic || "";
}
function fmtTime(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}
function mentionOf(d) {
  return d.mention || (d.filename || "doc").replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_-]+/g, "_");
}

async function api(path, opts = {}) {
  const r = await fetch(API + path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = data.detail;
    const msg = typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
        : data.message || r.statusText || "request failed";
    throw new Error(msg);
  }
  return data;
}

/* ---------- library / @mentions ---------- */
async function loadLibrary() {
  LIBRARY = await api("/documents");
  renderDocChips();
}

function renderDocChips() {
  const box = $("#docChips");
  if (!LIBRARY.length) {
    box.innerHTML = '<span class="muted">No docs yet — upload on Knowledge Base.</span>';
    return;
  }
  box.innerHTML = LIBRARY.map((d) =>
    `<button type="button" class="docchip" data-mention="${esc(mentionOf(d))}" title="${esc(d.filename)}">@${esc(mentionOf(d))}</button>`
  ).join("");
  box.querySelectorAll(".docchip").forEach((btn) => {
    btn.onclick = () => insertMention(btn.dataset.mention);
  });
}

function insertMention(name) {
  const input = $("#askInput");
  const v = input.value;
  const needsSpace = v && !/\s$/.test(v);
  input.value = v + (needsSpace ? " @" : "@") + name + " ";
  input.focus();
  hideMentions();
}

function hideMentions() {
  $("#mentionMenu").hidden = true;
  $("#mentionMenu").innerHTML = "";
}

function showMentions(filter) {
  const q = (filter || "").toLowerCase();
  const hits = LIBRARY.filter((d) => {
    const m = mentionOf(d).toLowerCase();
    const f = (d.filename || "").toLowerCase();
    return !q || m.includes(q) || f.includes(q);
  });
  const menu = $("#mentionMenu");
  if (!hits.length) { hideMentions(); return; }
  menu.hidden = false;
  menu.innerHTML = hits.slice(0, 8).map((d) =>
    `<button type="button" class="mentionitem" data-name="${esc(mentionOf(d))}">
      <b>@${esc(mentionOf(d))}</b><span class="muted">${esc(d.filename)}</span>
     </button>`
  ).join("");
  menu.querySelectorAll(".mentionitem").forEach((btn) => {
    btn.onclick = () => {
      const input = $("#askInput");
      const v = input.value;
      const at = v.lastIndexOf("@");
      input.value = v.slice(0, at) + "@" + btn.dataset.name + " ";
      hideMentions();
      input.focus();
    };
  });
}

$("#askInput").addEventListener("input", () => {
  const v = $("#askInput").value;
  const m = v.match(/(?:^|\s)@([A-Za-z0-9_.-]*)$/);
  if (m) showMentions(m[1]);
  else hideMentions();
});
$("#askInput").addEventListener("keydown", (e) => {
  if (e.key === "Escape") hideMentions();
});

/* ---------- sessions ---------- */
async function refreshSessions() {
  const list = $("#sessionList");
  try {
    const sessions = await api("/sessions");
    if (!sessions.length) {
      list.innerHTML = '<div class="muted">No sessions yet.</div>';
      return;
    }
    list.innerHTML = sessions.map((s) => {
      const preview = (s.preview || "New conversation").trim();
      const active = s.id === SID ? " active" : "";
      const when = fmtTime(s.last_active || s.created_at);
      return `<button type="button" class="sessionitem${active}" data-sid="${esc(s.id)}">
        <span class="sprev">${esc(preview)}</span>
        <span class="smeta">${esc(when)} · ${s.n_messages || 0} msgs</span>
      </button>`;
    }).join("");
    list.querySelectorAll(".sessionitem").forEach((btn) => {
      btn.onclick = () => openSession(btn.dataset.sid);
    });
  } catch (e) {
    list.innerHTML = `<div class="errmsg">${esc(e.message)}</div>`;
  }
}

async function newSession() {
  const info = await api("/sessions", { method: "POST" });
  SID = info.session_id;
  localStorage.setItem(LS_SID, SID);
  $("#chat").innerHTML = "";
  $("#memoryBox").textContent = "Nothing discussed yet.";
  $("#turnTrace").textContent = "Tool calls and memory flags appear here.";
  $("#sessionHint").textContent = "session " + SID.slice(0, 8) + "…";
  addMsg(
    "bot",
    `Ask about the knowledge base. Type <b>@</b> to focus one document`
    + ` (e.g. <code>@${esc(LIBRARY[0] ? mentionOf(LIBRARY[0]) : "policy_wording")} free look?</code>);`
    + ` without @ I search all documents. Each turn shows collapsible agent context, then one final answer.`
  );
  await refreshSessions();
  await loadMemory();
}

async function openSession(id) {
  if (!id) return;
  SID = id;
  localStorage.setItem(LS_SID, SID);
  $("#sessionHint").textContent = "session " + SID.slice(0, 8) + "…";
  $("#chat").innerHTML = "";
  $("#turnTrace").textContent = "Tool calls and memory flags appear here.";
  try {
    const msgs = await api(`/sessions/${SID}/messages`);
    if (!msgs.length) {
      addMsg("bot", "This session is empty — ask a question, or type <b>@</b> to focus a document.");
    } else {
      for (const m of msgs) {
        if (m.role === "user") addMsg("user", esc(m.content));
        else if (m.role === "assistant") {
          let meta = {};
          try { meta = typeof m.meta === "string" ? JSON.parse(m.meta || "{}") : (m.meta || {}); }
          catch { meta = {}; }
          const bot = addMsg("bot", "");
          renderAnswer({
            answer: m.content,
            citations: meta.citations || [],
            not_in_kb: !!meta.not_in_kb,
            compressed: !!meta.compressed,
            topic_switch_ack: !!meta.topic_switch_ack,
            web_sources: meta.web_sources || [],
            usage: meta.usage || {},
            tool_runs: meta.tool_runs || [],
          }, bot, { fromHistory: true });
        }
      }
    }
  } catch {
    addMsg("bot", "Could not load history for this session.");
  }
  await loadMemory();
  await refreshSessions();
  await refreshUsage();
}

$("#newSession").onclick = () => newSession().catch((e) => alert(e.message));

/* ---------- chat + collapsible agent context ---------- */
function addMsg(kind, html) {
  const div = document.createElement("div");
  div.className = "msg " + kind;
  div.innerHTML = html;
  $("#chat").appendChild(div);
  $("#chat").scrollTop = $("#chat").scrollHeight;
  return div;
}

function ensureAgentShell(bot) {
  let ctx = bot.querySelector(".agentctx");
  if (ctx) return ctx;
  bot.innerHTML = `
    <details class="agentctx" open>
      <summary><span class="chev">▸</span> <span class="ctxlabel">Agent context</span>
        <span class="chip mem ctxcount">0 steps</span></summary>
      <div class="ctxbody">
        <div class="agentsummary">Starting…</div>
        <div class="toolstack"></div>
      </div>
    </details>
    <div class="finalanswer"></div>`;
  return bot.querySelector(".agentctx");
}

function setAgentSummary(bot, text) {
  const el = bot.querySelector(".agentsummary");
  if (el) el.textContent = text;
}

function bumpStepCount(bot) {
  const el = bot.querySelector(".ctxcount");
  if (!el) return;
  const n = bot.querySelectorAll(".toolblock").length;
  el.textContent = n + (n === 1 ? " step" : " steps");
}

function addToolBlock(bot, name, label, preview) {
  ensureAgentShell(bot);
  const stack = bot.querySelector(".toolstack");
  stepCounter += 1;
  const id = "t" + stepCounter;
  const details = document.createElement("details");
  details.className = "toolblock running";
  details.dataset.toolId = id;
  details.dataset.name = name;
  details.open = false;
  details.innerHTML = `
    <summary>
      <span class="tname">${esc(label || name)}</span>
      <span class="tprev">${esc(preview || "")}</span>
      <span class="tmeta"><span class="spin"></span></span>
    </summary>
    <div class="toolbody muted">Running…</div>`;
  stack.appendChild(details);
  bumpStepCount(bot);
  $("#chat").scrollTop = $("#chat").scrollHeight;
  return details;
}

function finishToolBlock(bot, name, ok, latency, summary, detail) {
  const blocks = [...bot.querySelectorAll(".toolblock")];
  let block = null;
  for (let i = blocks.length - 1; i >= 0; i--) {
    if (blocks[i].dataset.name === name && blocks[i].classList.contains("running")) {
      block = blocks[i];
      break;
    }
  }
  if (!block) {
    for (let i = blocks.length - 1; i >= 0; i--) {
      if (blocks[i].dataset.name === name) { block = blocks[i]; break; }
    }
  }
  if (!block) return;
  block.classList.remove("running");
  block.classList.add(ok ? "ok" : "err");
  const meta = block.querySelector(".tmeta");
  if (meta) meta.innerHTML = `${ok ? "ok" : "err"}${latency != null ? " · " + Math.round(latency) + "ms" : ""}`;
  const body = block.querySelector(".toolbody");
  if (body) body.textContent = detail || summary || (ok ? "done" : "failed");
}

$("#askForm").onsubmit = async (e) => {
  e.preventDefault();
  hideMentions();
  const q = $("#askInput").value.trim();
  if (!q || !SID || sending) return;
  $("#askInput").value = "";
  addMsg("user", esc(q));
  const bot = addMsg("bot", "");
  ensureAgentShell(bot);
  setAgentSummary(bot, "Memory pre-pass…");
  const btn = $("#askBtn");
  sending = true;
  btn.disabled = true;
  $("#turnTrace").innerHTML = '<span class="muted">Waiting for memory pass…</span>';
  try {
    const resp = await fetch(`${API}/sessions/${SID}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || resp.statusText || "chat failed");
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let ev = null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of chunk.split("\n")) {
          if (line.startsWith("event: ")) ev = line.slice(7).trim();
          else if (line.startsWith("data: ")) {
            try { handleEvent(ev, JSON.parse(line.slice(6)), bot); }
            catch { /* ignore malformed SSE */ }
          }
        }
      }
    }
    // Collapse agent context after the final answer so the answer is primary
    const ctx = bot.querySelector(".agentctx");
    if (ctx && bot.querySelector(".finalanswer")?.textContent) ctx.open = false;
    await loadMemory();
    await refreshSessions();
    await refreshUsage();
  } catch (err) {
    setAgentSummary(bot, "Failed");
    const fin = bot.querySelector(".finalanswer") || bot;
    fin.innerHTML = `<span style="color:var(--red)">${esc(err.message)}</span>`;
  }
  sending = false;
  btn.disabled = false;
  $("#askInput").focus();
};

function handleEvent(ev, d, bot) {
  if (ev === "memory") {
    ensureAgentShell(bot);
    const flags = [];
    const focus = (d.focus_docs || []).map((x) => "@" + (x.mention || x.filename)).join(", ");
    if (d.scope === "document" && focus) {
      flags.push("Scoped to " + focus);
      setAgentSummary(bot, "Searching only " + focus + "…");
    } else if (d.mention_unresolved?.length) {
      flags.push("Unknown @" + d.mention_unresolved.join(", @"));
      setAgentSummary(bot, "Mention not found — falling back to whole KB search…");
    } else {
      flags.push("Whole KB search");
      setAgentSummary(bot, "Semantic + keyword search across all documents…");
    }
    if (d.likely_repeat) flags.push("repeat detected");
    if (d.topic_switch) flags.push("topic switch");
    if (d.intent) flags.push(d.intent);
    if (d.rewritten_query) flags.push("query → " + String(d.rewritten_query).slice(0, 72));
    $("#turnTrace").innerHTML = flags.map((f) => `<span class="chip mem">${esc(f)}</span>`).join(" ");
  } else if (ev === "tool_call") {
    addToolBlock(bot, d.name, d.label || d.name, d.preview || "");
    setAgentSummary(bot, (d.label || d.name) + (d.preview ? ": " + d.preview : ""));
  } else if (ev === "tool_result") {
    finishToolBlock(bot, d.name, d.ok, d.latency_ms, d.summary, d.detail || d.summary);
    setAgentSummary(bot, d.summary || ((d.ok ? "Finished " : "Failed ") + (d.label || d.name)));
  } else if (ev === "answer") {
    setAgentSummary(bot, "Done — final answer below.");
    renderAnswer(d, bot);
  } else if (ev === "error") {
    setAgentSummary(bot, "Error");
    const fin = bot.querySelector(".finalanswer") || bot;
    fin.innerHTML = `<span style="color:var(--red)">${esc(d.message)}</span>`;
  }
}

function renderAnswer(a, bot, opts = {}) {
  ensureAgentShell(bot);
  if (opts.fromHistory && a.tool_runs?.length) {
    const stack = bot.querySelector(".toolstack");
    stack.innerHTML = "";
    a.tool_runs.forEach((tr) => {
      const details = document.createElement("details");
      details.className = "toolblock " + (tr.ok ? "ok" : "err");
      details.innerHTML = `
        <summary>
          <span class="tname">${esc(tr.tool || tr.name || "tool")}</span>
          <span class="tprev">${esc(String(tr.args || "").slice(0, 80))}</span>
          <span class="tmeta">${tr.ok ? "ok" : "err"}${tr.latency_ms != null ? " · " + Math.round(tr.latency_ms) + "ms" : ""}</span>
        </summary>
        <div class="toolbody">${esc(String(tr.result || "").slice(0, 800))}</div>`;
      stack.appendChild(details);
    });
    bumpStepCount(bot);
    setAgentSummary(bot, "Restored from session history.");
    bot.querySelector(".agentctx").open = false;
  }

  const parts = [];
  if (a.not_in_kb) parts.push('<span class="warn">Not covered by the knowledge base</span>');
  if (a.compressed) parts.push('<span class="warn">Compressed — covered earlier in this session</span>');
  if (a.topic_switch_ack) parts.push('<span class="warn">Topic switch acknowledged</span>');
  parts.push(`<div class="answerbody">${md(a.answer)}</div>`);
  if (a.citations?.length) {
    parts.push(
      '<div class="cites">' +
        a.citations
          .map(
            (c) => `<details class="cite"><summary>${c.valid === false ? "Unverified · " : ""}${esc(c.doc)}${c.section ? " › " + esc(c.section) : ""}</summary>
             <blockquote>${esc(c.span_text || "")}</blockquote></details>`
          )
          .join("") +
        "</div>"
    );
  }
  if (a.web_sources?.length) {
    parts.push(
      '<div class="websources muted">Web (not KB): ' +
        a.web_sources
          .map((s) => `<a href="${esc(s.uri)}" target="_blank" rel="noopener">${esc(s.title || s.uri)}</a>`)
          .join(" · ") +
        "</div>"
    );
  }
  if (a.usage && (a.usage.prompt_tokens || a.usage.completion_tokens)) {
    parts.push(
      `<div class="muted" style="margin-top:8px">${a.usage.prompt_tokens || 0} + ${a.usage.completion_tokens || 0} tokens · ${a.usage.llm_calls || 0} calls</div>`
    );
  }
  const fin = bot.querySelector(".finalanswer");
  if (fin) fin.innerHTML = parts.join("");
  else bot.insertAdjacentHTML("beforeend", `<div class="finalanswer">${parts.join("")}</div>`);
  $("#chat").scrollTop = $("#chat").scrollHeight;
}

/* ---------- memory ---------- */
async function loadMemory() {
  if (!SID) return;
  try {
    const m = await api(`/sessions/${SID}/memory`);
    const box = $("#memoryBox");
    const topics = (m.topics || [])
      .map((t) => topicLabel(t))
      .filter(Boolean)
      .map((t) => `<span class="topic">${esc(t)}</span>`)
      .join("");
    const qs = (m.asked_questions || [])
      .slice(-5)
      .map((a) => `<div class="qrow">${esc(a.question || "")}</div>`)
      .join("");
    box.innerHTML =
      (topics ? `<div>${topics}</div>` : "") +
      (qs ? `<div style="margin-top:8px">${qs}</div>` : topics ? "" : "Nothing discussed yet.");
    if (m.usage) {
      $("#usageTotals").textContent =
        `${m.usage.prompt_tokens || 0}/${m.usage.completion_tokens || 0} tok`;
    }
  } catch { /* ignore while session boots */ }
}

function loadMemoryPage() {
  if (!SID) {
    $("#memoryPage").innerHTML = '<div class="empty">Create or select a session first.</div>';
    return;
  }
  api(`/sessions/${SID}/memory`)
    .then((m) => {
      const topics = (m.topics || [])
        .map((t) => topicLabel(t))
        .filter(Boolean)
        .map((t) => `<span class="topic">${esc(t)}</span>`)
        .join("") || '<span class="muted">none yet</span>';
      const qs =
        (m.asked_questions || [])
          .slice(-12)
          .map(
            (a) =>
              `<div class="qrow"><b>T${a.turn ?? "?"}</b> · ${esc(a.question || "")}${
                a.intent ? ` <span class="muted">(${esc(a.intent)})</span>` : ""
              }</div>`
          )
          .join("") || '<span class="muted">none yet</span>';
      $("#memoryPage").innerHTML = `
        <div class="panel"><h3>Topics seen</h3>${topics}</div>
        <div class="panel"><h3>Questions asked</h3>${qs}</div>
        <div class="panel"><h3>Rolling summary</h3><p class="muted">${esc(
          m.summary || "Builds up as the conversation progresses."
        )}</p></div>`;
    })
    .catch((e) => {
      $("#memoryPage").innerHTML = `<div class="errmsg">${esc(e.message)}</div>`;
    });
}

async function refreshUsage() {
  if (!SID) return;
  try {
    const u = await api(`/sessions/${SID}/usage`);
    $("#usageTotals").textContent = `${u.prompt_tokens || 0}/${u.completion_tokens || 0} tok`;
  } catch { /* ignore */ }
}

/* ---------- KB ---------- */
async function loadDocs() {
  try {
    const docs = await api("/documents");
    LIBRARY = docs;
    renderDocChips();
    $("#docList").innerHTML =
      docs
        .map(
          (d) => `<div class="docrow">
            <div><b>@${esc(mentionOf(d))}</b><small>${esc(d.filename)} · ${esc(d.fmt)} · ${d.n_chunks} chunks · ${d.n_sections} sections</small></div>
            <button class="del" type="button" data-id="${esc(d.doc_id)}">Delete</button>
          </div>`
        )
        .join("") || '<div class="empty">No documents yet — upload one to get started.</div>';
    $("#docList").querySelectorAll(".del").forEach((btn) => {
      btn.onclick = () => delDoc(btn.dataset.id);
    });
  } catch (e) {
    $("#docList").innerHTML = `<span class="errmsg">${esc(e.message)}</span>`;
  }
}

async function delDoc(id) {
  if (!confirm("Delete this document from the knowledge base?")) return;
  await api(`/documents/${id}`, { method: "DELETE" });
  loadDocs();
  refreshHealth();
}

async function ingestFile(file) {
  const msg = $("#ingestMsg");
  if (!file) return;
  $("#ingestBtn").disabled = true;
  msg.className = "muted";
  msg.textContent = "Parsing → chunking → embedding → indexing…";
  try {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const info = await api("/documents", { method: "POST", body: fd });
    msg.className = "okmsg";
    msg.textContent = `Ingested @${mentionOf(info)} · ${info.filename} (${info.n_chunks} chunks)`;
    $("#fileInput").value = "";
    loadDocs();
    refreshHealth();
  } catch (err) {
    msg.className = "errmsg";
    msg.textContent = err.message;
  }
  $("#ingestBtn").disabled = false;
}

$("#ingestBtn").onclick = () => ingestFile($("#fileInput").files[0]);
$("#refreshDocs").onclick = () => loadDocs();

const drop = $("#dropZone");
["dragenter", "dragover"].forEach((ev) => {
  drop.addEventListener(ev, (e) => {
    e.preventDefault();
    drop.classList.add("drag");
  });
});
["dragleave", "drop"].forEach((ev) => {
  drop.addEventListener(ev, (e) => {
    e.preventDefault();
    drop.classList.remove("drag");
  });
});
drop.addEventListener("drop", (e) => {
  const f = e.dataTransfer?.files?.[0];
  if (f) {
    $("#fileInput").files = e.dataTransfer.files;
    ingestFile(f);
  }
});

/* ---------- boot ---------- */
async function refreshHealth() {
  try {
    const h = await api("/health");
    MAX_UPLOAD_MB = h.max_upload_mb || 15;
    $("#modelBadge").textContent = `${h.model} · ${h.documents} docs · ${h.chunks} chunks`;
    const hint = drop.querySelector("p.muted");
    if (hint) hint.textContent = `Drag & drop here, or choose a file. Max ${MAX_UPLOAD_MB} MB · PDF / DOCX / MD.`;
  } catch {
    $("#modelBadge").textContent = "backend offline";
  }
}

(async () => {
  await refreshHealth();
  try { await loadLibrary(); } catch { LIBRARY = []; }
  if (SID) {
    try {
      await api(`/sessions/${SID}/messages`);
      await openSession(SID);
    } catch {
      localStorage.removeItem(LS_SID);
      SID = null;
      await newSession();
    }
  } else {
    await newSession();
  }
  await refreshSessions();
  route();
})();
