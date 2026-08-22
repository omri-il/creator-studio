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
const VIEWS = ["view-home", "view-import", "view-vsl"];
function setView(id) {
  VIEWS.forEach((v) => $("#" + v).classList.toggle("hidden", v !== id));
}
function showHome() { setView("view-home"); }
function showImport() {
  setView("view-import");
  $("#toast").classList.add("hidden");
  detectDrives();
  loadDest();
}
function showVsl() {
  setView("view-vsl");
  $("#toast").classList.add("hidden");
  loadVslConfig();
  loadVslEvents();
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
  else if (act === "audio-open") { audioOpen(); }
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

/* ── audio: hand off to video-prep ──────────────────────────────────────── */
/* Creator Studio used to analyze and normalize here, through its own
   audio_tools.py. video-prep's נרמול אודיו tab does the same job better
   (voice chain, optional denoise, before/after preview), so this now just
   makes sure that app is up and opens its tab — one implementation, not two. */
async function audioOpen() {
  flash("פותח נרמול אודיו…");
  const r = await post("/api/audio/open");
  if (!r || !r.ok) { alert((r && r.error) || "לא הצלחתי לפתוח את video-prep"); return; }
  window.open(r.url, "_blank");
}

/* ── VSL publishing (Wistia → Event-Engine) ─────────────────────────────── */
let vslFile = null;

async function loadVslConfig() {
  let c;
  try { c = await api("/api/wistia/config"); } catch (e) { return; }
  $("#vslExportsDir").value = c.exports_dir || "";
  $("#vslEeUrl").value = c.event_engine_url || "";
  $("#vslProjectId").value = c.wistia_project_id || "";
  $("#vslSubdomain").value = c.wistia_subdomain || "";
  /* A missing token is the one setup step the panel cannot do for you, so it
     says so up front rather than failing at upload time. */
  const missing = [];
  if (!c.has_wistia_token) missing.push("WISTIA_API_TOKEN");
  if (!c.has_event_engine_token) missing.push("EVENT_ENGINE_TOKEN");
  const banner = $("#vslConfigBanner");
  if (missing.length) {
    $("#vslConfigText").innerHTML =
      'חסר בקובץ <span class="mono">.env</span>: <span class="mono">' +
      missing.join(", ") + "</span>";
    banner.classList.remove("hidden");
    $("#vslSettings").open = true;
  } else {
    banner.classList.add("hidden");
  }
  setVslReady();
}

async function saveVslConfig() {
  await post("/api/wistia/config", {
    exports_dir: $("#vslExportsDir").value.trim(),
    event_engine_url: $("#vslEeUrl").value.trim(),
    wistia_project_id: $("#vslProjectId").value.trim(),
    wistia_subdomain: $("#vslSubdomain").value.trim(),
  });
  loadVslConfig();
  loadVslEvents();
}

async function loadVslEvents() {
  const sel = $("#vslEvent");
  const note = $("#vslEventNote");
  let r;
  try { r = await api("/api/wistia/events"); } catch (e) { r = { ok: false, error: "" }; }
  sel.innerHTML = '<option value="">— רק להעלות, בלי לשייך —</option>';
  if (!r.ok) {
    /* Not fatal: the upload still works and the link can be pasted by hand. */
    note.textContent = "לא ניתן לטעון אירועים: " + (r.error || "");
    return;
  }
  for (const ev of r.events || []) {
    const o = document.createElement("option");
    o.value = ev.id;
    o.textContent = ev.name + " · " +
      (ev.starts_at || "").slice(0, 16).replace("T", " ") +
      (ev.vsl_url ? "  (יש כבר סרטון)" : "");
    sel.appendChild(o);
  }
  note.textContent = (r.events || []).length ? "" : "אין אירועים פעילים.";
}

function renderVslFile(info) {
  vslFile = info;
  $("#vslPath").value = info.path;
  $("#vslFileInfo").innerHTML =
    info.name + ' · <span class="mono">' + info.size_human + "</span> · כ-" +
    info.minutes + " דקות העלאה";
  const warn = $("#vslWarn");
  warn.textContent = info.warn_text || "";
  warn.classList.toggle("hidden", !info.warn);
  setVslReady();
}

function setVslReady() {
  $("#vslUploadBtn").disabled = !vslFile;
}

async function inspectVslPath(path) {
  if (!path) return;
  const r = await post("/api/wistia/inspect", { path });
  if (!r.ok) {
    vslFile = null;
    setVslReady();
    $("#vslFileInfo").textContent = r.error || "קובץ לא קיים";
    return;
  }
  renderVslFile(r);
}

async function pickVslFile() {
  const p = await post("/api/pick", { kind: "file" });
  if (p && p.ok && p.path) inspectVslPath(p.path);
}

async function useLatestVideo() {
  const r = await post("/api/wistia/latest", { folder: $("#vslExportsDir").value.trim() });
  if (!r.ok) { alert(r.error || "לא נמצא קובץ"); return; }
  renderVslFile(r);
}

async function startVslUpload() {
  if (!vslFile) return;
  const eventId = $("#vslEvent").value || null;
  $("#vslUploadBtn").disabled = true;
  $("#vslJobPanel").classList.remove("hidden");
  $("#vslDonePanel").classList.add("hidden");
  const r = await post("/api/wistia/upload", { path: vslFile.path, event_id: eventId });
  if (!r.ok) {
    alert(r.error || "ההעלאה נכשלה להתחיל");
    $("#vslJobPanel").classList.add("hidden");
    setVslReady();
    return;
  }
  pollJob(r.id, (j) => {
    $("#vslJobBar").style.width = (j.progress || 0) + "%";
    $("#vslJobPct").textContent = Math.round(j.progress || 0) + "%";
    $("#vslJobMsg").textContent = j.message || "";
  }, (j) => renderVslDone(j.result), (j) => {
    $("#vslJobPanel").classList.add("hidden");
    const box = $("#vslDonePanel");
    box.innerHTML = '<div class="done"><h2>⚠️ ההעלאה נכשלה</h2><div class="errs">' +
      (j.message || "שגיאה") +
      '</div><div class="done-actions"><button class="btn ghost" onclick="location.reload()">נסה שוב</button></div></div>';
    box.classList.remove("hidden");
    setVslReady();
  });
}

function renderVslDone(r) {
  $("#vslJobPanel").classList.add("hidden");
  const box = $("#vslDonePanel");
  if (!r) { box.classList.remove("hidden"); return; }
  const url = r.video_url;
  /* An upload that worked followed by a failed event update is a PARTIAL
     success — the video is on Wistia either way, so the link is always shown
     rather than the whole thing reading as a failure. */
  let html = '<div class="done"><h2>' + (r.event_error ? "⚠️" : "✅") +
    ' הועלה ל-Wistia</h2>' +
    '<div class="row"><input type="text" class="ltr" id="vslResultUrl" readonly value="' +
    url + '"><button class="btn" id="vslCopyBtn">העתק</button></div>';
  if (r.event_error) {
    html += '<div class="errs">הסרטון עלה, אבל האירוע לא עודכן: ' + r.event_error +
      "<br>הדביקו את הקישור שלמעלה בשדה הווידאו של האירוע.</div>";
  } else if (r.event && r.event.public_url) {
    html += "<ul><li>דף ההרשמה כבר מציג את הסרטון.</li></ul>";
  }
  html += '<div class="done-actions">' +
    (r.event && r.event.public_url
      ? '<button class="btn" id="vslOpenPageBtn">🌐 פתח את דף ההרשמה</button>' : "") +
    '<button class="btn ghost" onclick="location.reload()">פרסום נוסף</button></div></div>';
  box.innerHTML = html;
  box.classList.remove("hidden");
  $("#vslCopyBtn").onclick = () => {
    const el = $("#vslResultUrl");
    el.select();
    if (navigator.clipboard) navigator.clipboard.writeText(url);
    else document.execCommand("copy");
    $("#vslCopyBtn").textContent = "הועתק ✓";
  };
  const open = $("#vslOpenPageBtn");
  if (open) open.onclick = () => window.open(r.event.public_url, "_blank");
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
  $("#openVslBtn").onclick = showVsl;
  $("#backHomeVsl").onclick = showHome;
  $("#vslPickBtn").onclick = pickVslFile;
  $("#vslLatestBtn").onclick = useLatestVideo;
  $("#vslReloadEventsBtn").onclick = loadVslEvents;
  $("#vslUploadBtn").onclick = startVslUpload;
  $("#vslSaveCfgBtn").onclick = saveVslConfig;
  $("#vslPath").onchange = (e) => inspectVslPath(e.target.value.trim());
  $("#vslPickExportsBtn").onclick = async () => {
    const p = await post("/api/pick", { kind: "folder" });
    if (p && p.ok && p.path) $("#vslExportsDir").value = p.path;
  };
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
