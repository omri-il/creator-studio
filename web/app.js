"use strict";
/* Creator Studio front-end. Talks to the Flask backend over fetch; polls jobs
   and camera detection. Vanilla JS, no build step. */

const $ = (s) => document.querySelector(s);
const api = (p) => fetch(p).then((r) => r.json());
const post = (p, body) =>
  fetch(p, { method: "POST", headers: { "Content-Type": "application/json" },
             body: JSON.stringify(body || {}) }).then((r) => r.json());

const fmtSize = (b) => {
  if (!b) return "";
  const u = ["B", "KB", "MB", "GB"]; let i = 0; b = +b;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return b.toFixed(b < 10 && i > 0 ? 1 : 0) + " " + u[i];
};
const fmtDur = (s) => {
  s = Math.round(+s || 0); const m = Math.floor(s / 60);
  return m + ":" + String(s % 60).padStart(2, "0");
};

let state = { source: "", lastDrive: null, toastDismissed: false, features: {}, txBackend: "vps" };

/* transcription backend chooser (☁️ שרת whisper-agent / 💻 מקומי GPU) */
function setTxBackend(b, save) {
  state.txBackend = b === "local" ? "local" : "vps";
  document.querySelectorAll("#txSeg .seg-btn").forEach((el) =>
    el.classList.toggle("on", el.dataset.backend === state.txBackend));
  if (save) post("/api/osmo/config", { transcribe_backend: state.txBackend });
}
const txLabel = (b) => (b === "local" ? " · 💻 מקומי" : b === "vps" ? " · ☁️ שרת" : "");

/* ── views ──────────────────────────────────────────────────────────────── */
function showHome() {
  $("#view-home").classList.remove("hidden");
  $("#view-import").classList.add("hidden");
}
function showImport() {
  $("#view-home").classList.add("hidden");
  $("#view-import").classList.remove("hidden");
  $("#toast").classList.add("hidden");
  detectDrives();
  loadDest();
}

/* ── mic ────────────────────────────────────────────────────────────────── */
function renderMic(m) {
  if (!m) return;
  const v = m.volume == null ? 0 : m.volume;
  $("#micBar").style.width = v + "%";
  $("#micVal").textContent = (m.volume == null ? "--" : m.volume) + "%";
  $("#micLock").textContent = m.locked ? "🔒" : "🔓";
  $("#micLock").style.opacity = m.locked ? "1" : ".4";
  const btn = $("#micToggleBtn");
  if (btn) {
    btn.textContent = m.locked ? `נעול ב-${m.target}% · שחרר` : "נעל עוצמה";
    btn.dataset.locked = m.locked ? "1" : "0";
  }
  const sub = $("#micTileSub");
  if (sub) sub.textContent = m.locked
    ? `העוצמה ננעלה על ${m.target}% ונשמרת ברקע.`
    : "המיקרופון חופשי כרגע. נעל כדי לשמור עוצמה קבועה.";
}
async function pollMic() {
  try { renderMic(await api("/api/mic")); } catch (e) {}
  setTimeout(pollMic, 2000);
}
async function toggleMicLock() {
  const btn = $("#micToggleBtn");
  const locked = btn.dataset.locked === "1";
  renderMic(await post("/api/mic/lock", { locked: !locked }));
}

/* ── camera detection ───────────────────────────────────────────────────── */
async function detectDrives() {
  let drives = [];
  try { drives = (await api("/api/osmo/detect")).drives || []; } catch (e) {}
  const cam = $("#camStat");
  if (drives.length) {
    const d = drives[0];
    cam.classList.add("live");
    $("#camText").textContent = d.is_dji ? "Osmo מחוברת" : "מצלמה מחוברת";
    // import-view banner + autofill
    const banner = $("#detectBanner");
    if (banner) {
      banner.classList.add("found");
      $("#detectText").innerHTML =
        `נמצאה מצלמה בכונן <span class="mono">${d.root}</span>${d.label ? " · " + d.label : ""}`;
      if (!$("#sourcePath").value) $("#sourcePath").value = d.dcim || d.root;
    }
    // toast (once per connect)
    if (d.root !== state.lastDrive && !state.toastDismissed &&
        $("#view-home").classList.contains("hidden") === false) {
      $("#toastSub").textContent = (d.is_dji ? "DJI Osmo" : "מצלמה") + " · מוכן לייבוא";
      $("#toast").classList.remove("hidden");
    }
    state.lastDrive = d.root;
  } else {
    cam.classList.remove("live");
    $("#camText").textContent = "אין מצלמה";
    state.lastDrive = null; state.toastDismissed = false;
    const banner = $("#detectBanner");
    if (banner) { banner.classList.remove("found"); $("#detectText").textContent = "מחפש מצלמה מחוברת…"; }
  }
}
async function pollDetect() {
  try { await detectDrives(); } catch (e) {}
  setTimeout(pollDetect, 5000);
}

/* ── destination root ───────────────────────────────────────────────────── */
async function loadDest() {
  try {
    const c = await api("/api/osmo/config");
    $("#destPath").textContent = c.backup_root + "\\<תאריך>";
    $("#destPath").dataset.root = c.backup_root;
    if (c.transcribe_backend) setTxBackend(c.transcribe_backend, false);
  } catch (e) {}
}
async function editRoot() {
  let pick = await post("/api/pick", { kind: "folder" });
  let root = pick && pick.ok && pick.path ? pick.path
    : prompt("תיקיית יעד לגיבוי:", $("#destPath").dataset.root || "");
  if (root) { await post("/api/osmo/config", { backup_root: root }); loadDest(); }
}

/* ── scan + sessions ────────────────────────────────────────────────────── */
async function scan() {
  const source = $("#sourcePath").value.trim();
  if (!source) { alert("בחר תיקיית מקור"); return; }
  state.source = source;
  const btn = $("#scanBtn"); btn.disabled = true; btn.textContent = "סורק…";
  let res;
  try { res = await post("/api/osmo/scan", { source }); }
  catch (e) { res = { ok: false, error: String(e) }; }
  btn.disabled = false; btn.textContent = "סרוק 🔍";
  if (!res.ok) { alert(res.error || "הסריקה נכשלה"); return; }
  renderSessions(res);
}

function renderSessions(res) {
  const c = res.counts;
  const box = $("#scanResult");
  let html = `<div class="sessions-head">
      <h2>${c.sessions} אירועים · ${c.clips} קליפים</h2>
      <span class="sum">${c.new} חדשים לייבוא</span></div>`;
  if (!res.sessions.length) html += `<div class="panel">לא נמצאו קטעי וידאו בתיקייה הזו.</div>`;
  for (const s of res.sessions) {
    const multi = s.clips.length > 1;
    html += `<div class="session${s.already ? " done" : ""}">
      <div class="session-top">
        <div class="reel">${multi ? "🎞️" : "🎬"}</div>
        <h4>${s.label}</h4>
        ${multi ? `<span class="chip merge">מיזוג ${s.clips.length} קליפים</span>` : ""}
        ${s.already ? `<span class="chip done">כבר יובא</span>` : ""}
        <span class="chip">${fmtDur(s.total_duration)}</span>
      </div><div class="clips">`;
    for (const cl of s.clips) {
      html += `<div class="clip${cl.already ? " skip" : ""}">
        <span class="cname">${cl.name}</span>
        <span class="cmeta">${cl.width}×${cl.height} · ${fmtSize(cl.size)}${cl.already ? " · דולג" : ""}</span>
      </div>`;
    }
    html += `</div></div>`;
  }
  box.innerHTML = html;
  box.classList.remove("hidden");
  $("#optionsPanel").classList.toggle("hidden", c.new === 0);
  if (c.new === 0) {
    box.innerHTML += `<div class="panel">כל הקליפים כבר יובאו בעבר — אין מה להעתיק. ✅</div>`;
  }
}

/* ── import job ─────────────────────────────────────────────────────────── */
async function startImport() {
  const body = {
    source: state.source,
    merge: $("#optMerge").checked,
    transcribe: $("#optTranscribe").checked,
    transcribe_backend: state.txBackend,
    keep_originals: $("#optKeep").checked,
    backup_root: $("#destPath").dataset.root,
  };
  $("#optionsPanel").classList.add("hidden");
  $("#jobPanel").classList.remove("hidden");
  $("#donePanel").classList.add("hidden");
  const r = await post("/api/osmo/import", body);
  if (!r.ok) { alert(r.error || "הייבוא נכשל להתחיל"); return; }
  pollJob(r.id, (j) => {
    $("#jobBar").style.width = (j.progress || 0) + "%";
    $("#jobPct").textContent = Math.round(j.progress || 0) + "%";
    $("#jobMsg").textContent = j.message || "";
  }, (j) => renderDone(j.result), (j) => {
    $("#jobMsg").textContent = j.message || "שגיאה";
  });
}

function renderDone(r) {
  $("#jobPanel").classList.add("hidden");
  if (!r) return;
  const box = $("#donePanel");
  // Which transcription targets never got a transcript (skipped or failed)?
  const done = new Set(r.transcribed || []);
  const failed = (r.transcribe_targets || []).filter((p) => !done.has(p));
  const canTx = state.features.transcribe && failed.length;
  let html = `<div class="done"><h2>✅ הייבוא הושלם</h2>
    <ul>
      <li><b>${r.copied.length}</b> קבצים הועתקו · <b>${r.skipped}</b> דולגו (כבר יובאו)</li>
      ${r.merged.length ? `<li><b>${r.merged.length}</b> אירועים מוזגו ללא איבוד איכות</li>` : ""}
      ${r.transcribed.length ? `<li><b>${r.transcribed.length}</b> קבצים תומללו${txLabel(r.transcribe_backend)}</li>` : ""}
      ${canTx ? `<li class="warn"><b>${failed.length}</b> קבצים ממתינים לתמלול</li>` : ""}
    </ul>`;
  if (r.errors && r.errors.length)
    html += `<div class="errs">שגיאות: ${r.errors.slice(0, 4).join(" · ")}</div>`;
  html += `<div class="done-actions">
      ${canTx ? `<button class="btn" id="retryTxBtn">🎙️ תמלל (${failed.length})</button>` : ""}
      <button class="btn" onclick="reveal('${r.dest_dir.replace(/\\/g, "\\\\")}')">📁 פתח תיקיית יעד</button>
      <button class="btn ghost" onclick="location.reload()">ייבוא נוסף</button>
    </div></div>`;
  box.innerHTML = html;
  box.classList.remove("hidden");
  const rt = $("#retryTxBtn");
  if (rt) rt.onclick = () => retranscribe(failed, r.dest_dir);
}
window.reveal = (p) => post("/api/reveal", { path: p });

/* (Re)transcribe already-imported files that were skipped or failed. */
async function retranscribe(paths, destDir) {
  const rt = $("#retryTxBtn");
  if (rt) { rt.disabled = true; rt.textContent = "מתמלל…"; }
  const r = await post("/api/osmo/transcribe", { paths, dest_dir: destDir, transcribe_backend: state.txBackend });
  if (!r.ok) { alert(r.error || "התמלול נכשל להתחיל"); if (rt) { rt.disabled = false; rt.textContent = `🎙️ תמלל (${paths.length})`; } return; }
  $("#donePanel").classList.add("hidden");
  $("#jobPanel").classList.remove("hidden");
  pollJob(r.id, (j) => {
    $("#jobBar").style.width = (j.progress || 0) + "%";
    $("#jobPct").textContent = Math.round(j.progress || 0) + "%";
    $("#jobMsg").textContent = j.message || "";
  }, (j) => renderTxDone(j.result, paths), (j) => { $("#jobMsg").textContent = j.message || "שגיאה"; });
}

function renderTxDone(res, attempted) {
  $("#jobPanel").classList.add("hidden");
  const box = $("#donePanel");
  if (!res) { box.classList.remove("hidden"); return; }
  const ok = (res.transcribed || []).length;
  const errs = res.errors || [];
  const done = new Set(res.transcribed || []);
  const remaining = (attempted || []).filter((p) => !done.has(p));
  let html = `<div class="done"><h2>${errs.length ? "⚠️" : "✅"} תמלול</h2>
    <ul><li><b>${ok}</b> קבצים תומללו${remaining.length ? ` · <b>${remaining.length}</b> עדיין ממתינים` : ""}</li></ul>`;
  if (errs.length) html += `<div class="errs">שגיאות: ${errs.slice(0, 4).join(" · ")}</div>`;
  html += `<div class="done-actions">
      ${remaining.length ? `<button class="btn" id="retryTxBtn">🔁 נסה שוב (${remaining.length})</button>` : ""}
      <button class="btn" onclick="reveal('${(res.dest_dir || "").replace(/\\/g, "\\\\")}')">📁 פתח תיקיית יעד</button>
      <button class="btn ghost" onclick="location.reload()">חזרה</button>
    </div></div>`;
  box.innerHTML = html;
  box.classList.remove("hidden");
  const rt = $("#retryTxBtn");
  if (rt) rt.onclick = () => retranscribe(remaining, res.dest_dir);
}

/* generic job poller */
function pollJob(id, onProgress, onDone, onError) {
  const tick = async () => {
    let j; try { j = await api("/api/job/" + id); } catch (e) { return setTimeout(tick, 1200); }
    if (!j || j.error) return;
    onProgress && onProgress(j);
    if (j.state === "done") return onDone && onDone(j);
    if (j.state === "error") return onError && onError(j);
    setTimeout(tick, 1000);
  };
  tick();
}

/* ── tool actions ───────────────────────────────────────────────────────── */
async function toolAction(act) {
  if (act === "davinci-launch") { const r = await post("/api/davinci/launch"); flash(r.ok ? "Resolve מופעל" : r.error); }
  else if (act === "davinci-dashboard") { await post("/api/davinci/dashboard"); flash("פותח לוח בקרה…"); }
  else if (act === "map-drive") { const r = await post("/api/davinci/map-drive"); flash(r.ok ? "כונן מופה" : (r.error || r.output || "נכשל")); }
  else if (act === "audio-pick") { audioPick(); }
}
function flash(msg) {
  const t = $("#toast");
  $(".toast-ico", ).textContent; // no-op keep
  $("#toastSub").textContent = "";
  t.querySelector("b").textContent = msg || "";
  $("#toastSub").textContent = "";
  $("#toastOpen").classList.add("hidden");
  t.querySelector(".toast-ico").textContent = "✓";
  t.classList.remove("hidden");
  setTimeout(() => { t.classList.add("hidden"); $("#toastOpen").classList.remove("hidden");
    t.querySelector(".toast-ico").textContent = "📷"; }, 2600);
}

/* ── audio analyze ──────────────────────────────────────────────────────── */
async function audioPick() {
  const pick = await post("/api/pick", { kind: "file" });
  if (!pick || !pick.ok || !pick.path) return;
  const r = await post("/api/audio/analyze", { path: pick.path });
  if (!r.ok) { alert(r.error); return; }
  showAudioLoading();
  pollJob(r.id, null, (j) => showAudio(j.result), (j) => alert(j.message));
}
function showAudioLoading() {
  $("#audioModalCard").innerHTML = `<h2>מנתח אודיו…</h2><div class="progress"><i style="width:60%"></i></div>`;
  $("#audioModal").classList.remove("hidden");
}
function showAudio(a) {
  if (!a) { $("#audioModal").classList.add("hidden"); return; }
  const card = $("#audioModalCard");
  card.innerHTML = `<h2>בדיקת אודיו ליוטיוב</h2>
    <div class="verdict" style="background:${a.color}">${a.verdict}</div>
    <div class="rows">
      <div class="mrow"><span>עוצמה משולבת</span><span class="mono">${a.integrated_lufs.toFixed(1)} LUFS</span></div>
      <div class="mrow"><span>שיא אמיתי</span><span class="mono">${a.true_peak_dbfs.toFixed(1)} dBTP</span></div>
    </div>
    <div class="obs">${a.observations.map((o) => `<div>${o}</div>`).join("")}</div>
    ${a.recommendation ? `<div class="rec">💡 ${a.recommendation}</div>` : ""}
    <div class="done-actions">
      ${!a.verdict.startsWith("✅") ? `<button class="btn" id="normBtn">🎛️ נרמל ל-14- LUFS</button>` : ""}
      <button class="btn ghost" onclick="document.getElementById('audioModal').classList.add('hidden')">סגור</button>
    </div>`;
  const nb = $("#normBtn");
  if (nb) nb.onclick = async () => {
    nb.disabled = true; nb.textContent = "מנרמל…";
    const r = await post("/api/audio/normalize", { path: a.path });
    if (!r.ok) { alert(r.error); return; }
    pollJob(r.id, null, (j) => { alert("נשמר: " + j.result.output_path); reveal(j.result.output_path); $("#audioModal").classList.add("hidden"); }, (j) => alert(j.message));
  };
}

/* ── wiring ─────────────────────────────────────────────────────────────── */
async function init() {
  try {
    const st = await api("/api/status");
    state.features = st.features || {};
    renderMic(st.mic);
    if (st.transcribe_backend) setTxBackend(st.transcribe_backend, false);
    if (!state.features.davinci) $("#tileDavinci")?.classList.add("hidden");
    if (!state.features.map_drive) $("#tileDrive")?.classList.add("hidden");
    // Only offer the backend chooser when both backends are actually available.
    const f = state.features;
    if (f.transcribe && !(f.transcribe_local && f.transcribe_vps))
      $("#txBackendRow")?.classList.add("hidden");
  } catch (e) {}

  $("#heroOsmo").onclick = showImport;
  $("#brandHome").onclick = showHome;
  $("#backHome").onclick = showHome;
  $("#micStat").onclick = toggleMicLock;
  $("#micToggleBtn").onclick = toggleMicLock;
  $("#scanBtn").onclick = scan;
  $("#rescanDrivesBtn").onclick = detectDrives;
  $("#pickFolderBtn").onclick = async () => {
    const p = await post("/api/pick", { kind: "folder" });
    if (p && p.ok && p.path) $("#sourcePath").value = p.path;
  };
  $("#editRootBtn").onclick = editRoot;
  $("#startImportBtn").onclick = startImport;
  document.querySelectorAll("#txSeg .seg-btn").forEach((b) =>
    b.addEventListener("click", () => setTxBackend(b.dataset.backend, true)));
  $("#optTranscribe").onchange = (e) =>
    $("#txBackendRow")?.classList.toggle("dim", !e.target.checked);
  $("#toastOpen").onclick = () => { state.toastDismissed = true; showImport(); scan(); };
  document.querySelectorAll("[data-act]").forEach((b) =>
    b.addEventListener("click", () => toolAction(b.dataset.act)));

  pollMic();
  pollDetect();
}
document.addEventListener("DOMContentLoaded", init);
