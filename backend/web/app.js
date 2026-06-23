// Aether — orb-centric client with a proper microphone-permission flow.
// Tap the orb to toggle recording, or press-and-hold to talk; then Send or Cancel.
// Phases stream live over WebSocket and drive the orb's animation.

const $ = (id) => document.getElementById(id);
const orbWrap = () => document.querySelector(".orb-wrap");
const state = {
  token: localStorage.getItem("aether_token") || null,
  ws: null, mode: "idle", recMode: null, pressAt: 0,
  recorder: null, chunks: [], blob: null, micGranted: false, pending: null, choice: null,
};

// ───────── auth ─────────
// Sent on every request so ngrok's free-tier browser-warning page doesn't intercept
// our fetch calls when accessing remotely over the tunnel. Harmless otherwise.
const NGROK = { "ngrok-skip-browser-warning": "true" };
async function login(u, p) {
  const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json", ...NGROK }, body: JSON.stringify({ username: u, password: p }) });
  if (!r.ok) throw new Error("Invalid username or password");
  state.token = (await r.json()).access_token;
  localStorage.setItem("aether_token", state.token);
}
function logout() { state.token = null; localStorage.removeItem("aether_token"); if (state.ws) state.ws.close(); $("app").classList.add("hidden"); $("login").classList.remove("hidden"); }
const authH = () => ({ ...NGROK, Authorization: `Bearer ${state.token}` });

// ───────── orb state ─────────
function setMode(mode) {
  state.mode = mode;
  const w = orbWrap();
  w.classList.remove("rec", "busy", "speaking");
  $("orb").classList.remove("recording");
  $("rec-actions").classList.toggle("hidden", mode !== "review");
  $("hint").classList.toggle("hidden", mode !== "idle");
  if (mode === "recording") { w.classList.add("rec"); $("orb").classList.add("recording"); setPhase("Listening… tap again or release to stop", true); }
  else if (mode === "busy") { w.classList.add("busy"); }
  else if (mode === "review") setPhase("Send it, or cancel");
  else setPhase("Tap to speak · hold to talk");
}
function setPhase(t, active = false) { const p = $("phase"); p.textContent = t; p.classList.toggle("active", active); }
function setReply(t, kind = "") {
  // kind: ""|"error" — error styles the reply as a calm flash, with optional detail.
  const r = $("reply");
  r.textContent = t;
  r.classList.toggle("flash-error", kind === "error");
}
function flashError(summary, detail) {
  // Detailed, silent flash for failures the host intentionally won't voice. Both summary
  // and (if present) detail show, so the user has actionable context — not just "oops".
  setReply(summary + (detail ? "\n\n" + detail : ""), "error");
}

// ───────── microphone permission (professional flow) ─────────
async function micState() {
  try { return (await navigator.permissions.query({ name: "microphone" })).state; } // granted|denied|prompt
  catch { return "prompt"; }
}
function showMicSheet(denied = false) {
  $("mic-title").textContent = denied ? "Microphone is blocked" : "Enable your microphone";
  $("mic-msg").textContent = denied
    ? "Your browser blocked mic access for this site. Allow it in the address-bar/site settings, then try again. (Voice also needs HTTPS on remote devices — typing always works.)"
    : "Aether needs microphone access so you can talk to it. Audio is sent only to your own server for transcription.";
  $("mic-allow").classList.toggle("hidden", denied);
  $("mic-sheet").classList.remove("hidden");
}
async function ensureMic() {
  if (state.micGranted) return true;
  if (!navigator.mediaDevices || !window.MediaRecorder) { showMicSheet(true); return false; }
  const st = await micState();
  if (st === "denied") { showMicSheet(true); return false; }
  if (st === "granted") { state.micGranted = true; return true; }
  showMicSheet(false);              // explain first, then the browser prompt on "Allow"
  return false;
}
async function requestMic() {
  $("mic-sheet").classList.add("hidden");
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach((t) => t.stop());
    state.micGranted = true;
    setPhase("Microphone ready — tap the orb to talk", true);
  } catch { showMicSheet(true); }
}

// ───────── recording ─────────
async function startRecording() {
  if (!(await ensureMic())) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.recorder = new MediaRecorder(stream); state.chunks = [];
    state.recorder.ondataavailable = (e) => e.data.size && state.chunks.push(e.data);
    state.recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      state.blob = state.chunks.length ? new Blob(state.chunks, { type: "audio/webm" }) : null;
      if (state.blob) { setMode("review"); startAutoSend(); } else setMode("idle");
    };
    state.recorder.start(); setMode("recording");
  } catch (e) { setPhase("Microphone error: " + e.message); }
}
function stopRecording() { if (state.recorder && state.recorder.state !== "inactive") state.recorder.stop(); }
function discardRecording() { clearAutoSend(); state.blob = null; setMode("idle"); }

// Auto-send the recording after a short countdown (user can Cancel or Send now).
const AUTO_SECONDS = 2;
function startAutoSend() {
  let left = AUTO_SECONDS;
  const btn = $("rec-send");
  btn.textContent = `Send now (${left})`;
  btn.classList.add("countdown");
  btn.style.setProperty("--count", AUTO_SECONDS + "s");
  setPhase("Sending automatically… or cancel", true);
  state.autoInt = setInterval(() => { left -= 1; if (left > 0) btn.textContent = `Send now (${left})`; }, 1000);
  state.autoTimer = setTimeout(submitVoice, AUTO_SECONDS * 1000);
}
function clearAutoSend() {
  clearTimeout(state.autoTimer); clearInterval(state.autoInt);
  state.autoTimer = state.autoInt = null;
  const btn = $("rec-send"); btn.classList.remove("countdown"); btn.textContent = "Send ↑";
}

// ───────── sending ─────────
function beginBusy(label) { setMode("busy"); setReply("…"); setPhase(label, true); }
async function submitVoice() {
  clearAutoSend();
  if (!state.blob) return;
  beginBusy("📤 Sending your voice…");
  const fd = new FormData(); fd.append("audio", state.blob, "speech.webm"); state.blob = null;
  await handle(fetch("/api/command/voice", { method: "POST", headers: authH(), body: fd }));
}
async function submitText(text) {
  if (!text.trim()) return;
  beginBusy("📤 Sending…");
  await handle(fetch("/api/command/text", { method: "POST", headers: { "Content-Type": "application/json", ...authH() }, body: JSON.stringify({ text }) }));
}
async function submitApprove(a) {
  beginBusy("📤 Sending approval…");
  await handle(fetch("/api/command/approve", { method: "POST", headers: { "Content-Type": "application/json", ...authH() }, body: JSON.stringify(a) }));
}
async function submitChoice(answer) {
  const c = state.choice; state.choice = null;
  $("choice").classList.add("hidden");
  if (!c) return;
  beginBusy("📤 " + answer);
  // Resend the original request plus the answer so the agent continues from the fork.
  await handle(fetch("/api/command/text", { method: "POST", headers: { "Content-Type": "application/json", ...authH() }, body: JSON.stringify({ text: c.transcript || c.question, clarify: { question: c.question, answer } }) }));
}
async function handle(promise) {
  try {
    const r = await promise;
    if (r.status === 401) return logout();
    const text = await r.text();                 // read once, parse defensively
    let data;
    try { data = JSON.parse(text); }
    catch { throw new Error(`Server returned ${r.status} (${text.slice(0, 120) || "empty response"})`); }
    if (!r.ok) throw new Error(data.detail || data.summary || `Request failed (${r.status})`);
    renderResult(data);
  } catch (e) {
    console.error("Aether request failed:", e);
    // Client-side / transport failure (network, server unreachable, malformed response).
    // Flash it silently; don't let the browser speak this either — the rule applies to
    // anything error-shaped, not just backend statuses.
    flashError("That request didn't reach Aether.", (e && e.message) ? e.message : String(e));
  } finally {
    if (state.mode === "busy") setMode("idle");
  }
}

function renderResult(res) {
  if (res.transcript) addHistory("user", res.transcript);
  addHistory("bot", res.summary || "(no response)", res);
  // Errors/blocks: silent flash with detail. Anything else: normal reply.
  if (res.status === "error" || res.status === "blocked") {
    flashError(res.summary || "Something didn't work.", res.detail);
  } else {
    setReply(res.summary || "Done.");
  }
  maybeSpeak(res);
  if (res.status === "needs_choice") {
    state.choice = { question: res.question, transcript: res.transcript };
    $("choice-q").textContent = res.question || "Which one?";
    const box = $("choice-options"); box.innerHTML = "";
    (res.options || []).forEach((opt) => {
      const b = document.createElement("button");
      b.className = "btn-soft choice-opt"; b.textContent = opt;
      b.addEventListener("click", () => submitChoice(opt));
      box.appendChild(b);
    });
    $("choice").classList.remove("hidden");
    setMode("idle");
    return;
  }
  if (res.status === "needs_confirmation") {
    state.pending = { skill: res.skill, params: res.params, transcript: res.transcript };
    const root = /needs root|sudo/i.test((res.summary || "") + (res.detail || ""));
    $("approve-badge").classList.toggle("hidden", !root);
    $("approve-reason").textContent = res.detail || "This action is powerful — review it before it runs.";
    $("approve-cmd").textContent = (res.params && res.params.command) || res.summary;
    $("approve").classList.remove("hidden");
    setMode("idle");
    return;
  }
  if (res.status === "done") loadSuggestions();  // keep the "most-asked" chips current
}

// The host plays Aether's own voice; res.spoken says whether that succeeded. If it didn't
// (host audio down or speaking disabled), speak the reply in the browser so a response is
// never delivered silently. Gated on !res.spoken so we never talk over the host voice —
// and gated on the status so error/blocked outcomes stay silent everywhere (the rule:
// failures flash on the web, they do NOT take over the speakers).
function maybeSpeak(res) {
  if (res.status === "error" || res.status === "blocked") return;
  if (res.spoken || !res.summary || !("speechSynthesis" in window)) return;
  try {
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(res.summary));
  } catch (e) { console.warn("browser speech failed:", e); }
}

// ───────── history ─────────
function addHistory(role, text, res) {
  const list = $("history-list");
  if (role === "user") {
    const el = document.createElement("div"); el.className = "h-item " + (res ? res.status : "");
    el.innerHTML = `<div class="h-user">🗣 ${esc(text)}</div>`; list.prepend(el); state._u = el;
  } else {
    const el = state._u || (() => { const d = document.createElement("div"); d.className = "h-item"; list.prepend(d); return d; })();
    el.className = "h-item " + (res ? res.status : "");
    const meta = res ? `${res.skill || "agent"} · ${res.status}${res.spoken ? " · 🔊" : ""}` : "";
    el.innerHTML += `<div class="h-bot">${esc(text)}</div><div class="h-meta">${esc(meta)}</div>`; state._u = null;
  }
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ───────── live phases ─────────
function setConn(live) { $("conn").classList.toggle("live", live); $("conn").querySelector("em").textContent = live ? "live" : "offline"; }
function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  state.ws = new WebSocket(`${proto}://${location.host}/ws?token=${state.token}`);
  state.ws.onopen = () => setConn(true);
  state.ws.onclose = () => { setConn(false); if (state.token) setTimeout(connectWs, 3000); };
  state.ws.onmessage = (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === "progress" && state.mode === "busy") {
      const sp = m.step === "speaking";
      orbWrap().classList.toggle("speaking", sp);
      setPhase(m.label, true);
    } else if (m.type === "task_done" && document.hidden && Notification.permission === "granted") {
      new Notification("Aether", { body: m.summary });
    } else if (m.type === "notification" && Notification.permission === "granted") {
      // A host desktop notification, relayed live by the backend.
      const title = m.app ? `Aether • ${m.app}` : "Aether";
      new Notification(title, { body: [m.summary, m.body].filter(Boolean).join(" — ") });
    }
  };
}

// ───────── orb gestures ─────────
const orb = $("orb");
orb.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  if (state.mode === "idle") { state.pressAt = Date.now(); state.recMode = null; startRecording(); }
  else if (state.mode === "recording" && state.recMode === "tap") stopRecording();
});
orb.addEventListener("pointerup", (e) => {
  e.preventDefault();
  if (state.mode !== "recording" || state.recMode === "tap") return;
  const held = Date.now() - state.pressAt;
  if (held < 250) state.recMode = "tap"; else { state.recMode = "hold"; stopRecording(); }
});

// ───────── wiring ─────────
$("rec-send").addEventListener("click", submitVoice);
$("rec-cancel").addEventListener("click", discardRecording);
$("mic-allow").addEventListener("click", requestMic);
$("mic-dismiss").addEventListener("click", () => $("mic-sheet").classList.add("hidden"));
$("approve-yes").addEventListener("click", () => { $("approve").classList.add("hidden"); if (state.pending) submitApprove(state.pending); state.pending = null; });
$("approve-no").addEventListener("click", () => { $("approve").classList.add("hidden"); state.pending = null; setReply("Cancelled."); });
$("choice-cancel").addEventListener("click", () => { $("choice").classList.add("hidden"); state.choice = null; setReply("Okay, never mind."); });
$("send").addEventListener("click", () => { const t = $("text").value; $("text").value = ""; submitText(t); });
$("text").addEventListener("keydown", (e) => { if (e.key === "Enter") { const t = $("text").value; $("text").value = ""; submitText(t); } });
// Suggestion chips: clicking one sends it (event delegation, so dynamically-loaded chips work).
$("hint").addEventListener("click", (e) => { const c = e.target.closest(".chip"); if (c) submitText(c.textContent); });

// Populate the chips with the user's most-RECENT requests (then most-asked, then defaults —
// all server-side). The label reads "Recent" once there's real history, else a generic prompt.
async function loadSuggestions() {
  try {
    const r = await fetch("/api/suggestions", { headers: authH() });
    if (!r.ok) return;
    const { suggestions, from_history } = await r.json();
    if (!Array.isArray(suggestions) || !suggestions.length) return;
    const hint = $("hint");
    hint.innerHTML = "";
    suggestions.forEach((s) => {
      const b = document.createElement("button");
      b.className = "chip"; b.textContent = s;
      hint.appendChild(b);
    });
    const label = $("hint-label");
    if (label) { label.textContent = from_history ? "Recent" : "Try saying"; label.classList.remove("hidden"); }
  } catch (e) { console.warn("suggestions load failed:", e); }
}
$("logout").addEventListener("click", logout);
$("history-btn").addEventListener("click", () => $("history").classList.remove("hidden"));
$("history-close").addEventListener("click", () => $("history").classList.add("hidden"));
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault(); $("login-error").textContent = "";
  try { await login($("username").value, $("password").value); showApp(); }
  catch (err) { $("login-error").textContent = err.message; }
});

function showApp() {
  $("login").classList.add("hidden"); $("app").classList.remove("hidden");
  setMode("idle"); connectWs(); loadSuggestions();
  micState().then((s) => { if (s === "granted") state.micGranted = true; });
}
if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
if (state.token) showApp(); else { $("login").classList.remove("hidden"); $("app").classList.add("hidden"); }

// Make Aether installable as an app (Install / Add to Home Screen). The service worker only
// runs on a secure context (https or localhost), so it quietly does nothing on plain http.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch((e) => console.warn("SW register failed:", e));
  });
}
