// Soundwave frontend — vanilla JS, no build step.

const CUE_COLORS = {
  INTRO: 0xFFFFFF, DROP: 0xFF0000, BUILD: 0x00FF00, BREAK: 0xFFFF00,
  VOCAL: 0x0000FF, OUTRO32: 0xFF6400, OUTRO16: 0xFF0080,
};

const state = {
  currentPath: null,
  analyze: {
    path: null,
    duration: 0,
    bpm: 0,
    cues: [],       // [{label, time_sec, color}]
    peaks: [],
    dragging: null,      // index of cue being dragged
    dragMoved: false,    // did the pointer move past the click threshold
    dragStartX: 0,
    lyricsSynced: false,
    lastActiveLyricIdx: -1,
    scrollSuspendedUntil: 0,
  },
  // Drives the Home dashboard's pipeline cards & nav status jacks — a
  // lightweight, session-scoped picture of "how far along" each stage is.
  stats: {
    library: null,          // {path, trackCount}
    analyzedCount: 0,
    similarityCount: 0,     // playlists built this session
    lastOutput: null,       // resolved path of the most recently written XML
    playlists: null,        // {path, count}
  },
};

(function restoreStats() {
  try {
    const saved = JSON.parse(sessionStorage.getItem("soundwave-stats") || "null");
    if (saved) Object.assign(state.stats, saved);
  } catch (e) { /* ignore corrupt storage */ }
})();

function saveStats() {
  sessionStorage.setItem("soundwave-stats", JSON.stringify(state.stats));
}

// ---------------------------------------------------------------------------
// Boot splash — spinning vinyl intro, shown once per session
// ---------------------------------------------------------------------------

(function () {
  const splash = document.getElementById("splash");
  if (sessionStorage.getItem("soundwave-intro-seen")) {
    splash.remove();
    return;
  }
  sessionStorage.setItem("soundwave-intro-seen", "1");

  let dismissed = false;
  function dismiss() {
    if (dismissed) return;
    dismissed = true;
    splash.classList.add("hidden");
    setTimeout(() => splash.remove(), 550);
  }
  splash.addEventListener("click", dismiss);
  setTimeout(dismiss, 2600);
})();

// ---------------------------------------------------------------------------
// Nav
// ---------------------------------------------------------------------------

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`view-${btn.dataset.view}`).classList.add("active");

    // Arriving at Playlists with a known output XML but an empty field —
    // prefill it so "Export & Verify" doesn't require re-typing the path.
    if (btn.dataset.view === "playlists" && state.stats.lastOutput) {
      const plPath = document.getElementById("pl-path");
      if (plPath && !plPath.value.trim()) plPath.value = state.stats.lastOutput;
      const checkOutput = document.getElementById("check-output-xml");
      if (checkOutput && !checkOutput.value.trim()) checkOutput.value = state.stats.lastOutput;
    }
  });
});

function goToView(name) {
  document.querySelector(`.nav-item[data-view="${name}"]`).click();
}

document.querySelectorAll(".pipeline-step").forEach((btn) => {
  btn.addEventListener("click", () => goToView(btn.dataset.view));
});

document.querySelectorAll(".flow-next").forEach((el) => {
  el.addEventListener("click", () => goToView(el.dataset.view));
});

// --- Toggle cards: driven by a class rather than :has(), which some
// embedded browser builds fail to repaint on programmatic/real checkbox
// state changes even though selector matching itself is correct. ---
document.querySelectorAll(".toggle-card").forEach((card) => {
  const input = card.querySelector("input");
  if (!input) return;
  const sync = () => card.classList.toggle("checked", input.checked);
  input.addEventListener("change", sync);
  sync();
});

// ---------------------------------------------------------------------------
// Toasts — transient confirmation, in addition to the inline status text.
// ---------------------------------------------------------------------------

function showToast(message, kind = "info", ms = 3800) {
  const stack = document.getElementById("toast-stack");
  const toast = document.createElement("div");
  toast.className = `toast ${kind}`;
  toast.innerHTML = `<span class="toast-dot"></span><span>${escapeHtml(message)}</span>`;
  stack.appendChild(toast);
  setTimeout(() => {
    toast.classList.add("leaving");
    setTimeout(() => toast.remove(), 320);
  }, ms);
}

// ---------------------------------------------------------------------------
// Button ripple — tactile click feedback on every .btn press.
// ---------------------------------------------------------------------------

document.addEventListener("click", (e) => {
  const btn = e.target.closest("button.btn");
  if (!btn || btn.disabled) return;
  const rect = btn.getBoundingClientRect();
  const size = Math.max(rect.width, rect.height);
  const ripple = document.createElement("span");
  ripple.className = "btn-ripple";
  ripple.style.width = ripple.style.height = `${size}px`;
  ripple.style.left = `${e.clientX - rect.left - size / 2}px`;
  ripple.style.top = `${e.clientY - rect.top - size / 2}px`;
  btn.appendChild(ripple);
  setTimeout(() => ripple.remove(), 600);
});

// ---------------------------------------------------------------------------
// Home dashboard — reflects real pipeline state, not placeholder numbers.
// ---------------------------------------------------------------------------

function truncatePath(p, max) {
  if (!p || p.length <= max) return p || "";
  return "…" + p.slice(p.length - max + 1);
}

function setNavStatus(view, status) {
  const nav = document.querySelector(`.nav-item[data-view="${view}"]`);
  if (nav && !nav.classList.contains("active")) nav.dataset.status = status;
}

function renderDashboard() {
  const s = state.stats;

  const libStatus = document.getElementById("pipeline-status-library");
  const libCount = document.getElementById("stat-library-count");
  if (s.library) {
    libStatus.textContent = `${s.library.trackCount} track${s.library.trackCount === 1 ? "" : "s"} — ${truncatePath(s.library.path, 34)}`;
    libStatus.title = s.library.path;
    libStatus.classList.add("is-ready");
    libCount.textContent = s.library.trackCount;
    setNavStatus("library", "done");
  } else {
    libStatus.textContent = "Not set up yet";
    libStatus.classList.remove("is-ready");
    libCount.textContent = "—";
    setNavStatus("library", "pending");
  }

  const anStatus = document.getElementById("pipeline-status-analyze");
  const anCount = document.getElementById("stat-analyzed-count");
  anCount.textContent = s.analyzedCount;
  if (s.analyzedCount > 0) {
    anStatus.textContent = `${s.analyzedCount} track${s.analyzedCount === 1 ? "" : "s"} analyzed this session`;
    anStatus.classList.add("is-ready");
    setNavStatus("analyze", "done");
  } else {
    anStatus.textContent = "No tracks analyzed yet";
    anStatus.classList.remove("is-ready");
    setNavStatus("analyze", "pending");
  }

  const simStatus = document.getElementById("pipeline-status-similarity");
  const simCount = document.getElementById("stat-similarity-count");
  if (s.similarityCount > 0) {
    simStatus.textContent = `${s.similarityCount} playlist${s.similarityCount === 1 ? "" : "s"} built this session`;
    simStatus.classList.add("is-ready");
    simCount.textContent = s.similarityCount;
    setNavStatus("similarity", "done");
  } else {
    simStatus.textContent = "Not run yet";
    simStatus.classList.remove("is-ready");
    simCount.textContent = "—";
    setNavStatus("similarity", "pending");
  }

  const plStatus = document.getElementById("pipeline-status-playlists");
  const plOutput = document.getElementById("stat-output-path");
  if (s.playlists) {
    plStatus.textContent = `${s.playlists.count} playlist${s.playlists.count === 1 ? "" : "s"} — ${truncatePath(s.playlists.path, 30)}`;
    plStatus.title = s.playlists.path;
    plStatus.classList.add("is-ready");
    setNavStatus("playlists", "done");
  } else if (s.lastOutput) {
    plStatus.textContent = `Written to ${truncatePath(s.lastOutput, 30)} — open to verify`;
    plStatus.classList.remove("is-ready");
    setNavStatus("playlists", "pending");
  } else {
    plStatus.textContent = "Not checked yet";
    plStatus.classList.remove("is-ready");
    setNavStatus("playlists", "pending");
  }
  plOutput.textContent = s.lastOutput ? truncatePath(basename(s.lastOutput), 18) : "—";

  saveStats();
}

function basename(p) {
  return p.split(/[\\/]/).pop();
}

renderDashboard();

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function colorToHex(intColor) {
  return "#" + (intColor >>> 0).toString(16).padStart(6, "0");
}

function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

async function getJSON(url) {
  const r = await fetch(url);
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function qs(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === false) continue;
    if (Array.isArray(v)) {
      v.forEach((item) => p.append(k, item));
    } else if (v === true) {
      p.append(k, "true");
    } else {
      p.append(k, v);
    }
  }
  return p.toString();
}

// ---------------------------------------------------------------------------
// Library browsing
// ---------------------------------------------------------------------------

const libPathInput = document.getElementById("lib-path");

async function browseLibrary(path) {
  const wrap = document.getElementById("lib-table-wrap");
  wrap.innerHTML = `<div class="empty-state">Loading…</div>`;
  try {
    const data = await getJSON("/api/browse?" + qs({ path }));
    state.currentPath = data.path;
    libPathInput.value = data.path;

    const trackCount = data.folders.reduce((sum, f) => sum + f.track_count, 0) + data.files.length;
    state.stats.library = { path: data.path, trackCount };
    renderDashboard();

    const crumb = document.getElementById("lib-crumb");
    crumb.textContent = data.path;

    let rows = "";
    if (data.parent) {
      rows += `<tr class="folder-row" data-path="${escapeAttr(data.parent)}"><td colspan="4"><span class="folder-icon">↑</span> ..</td></tr>`;
    }
    for (const f of data.folders) {
      rows += `<tr class="folder-row" data-path="${escapeAttr(f.path)}">
        <td colspan="3"><span class="folder-icon">📁</span>${escapeHtml(f.name)}</td>
        <td>${f.track_count} track${f.track_count === 1 ? "" : "s"}</td>
      </tr>`;
    }
    for (const f of data.files) {
      rows += `<tr class="file-row" data-path="${escapeAttr(f.path)}">
        <td>${escapeHtml(f.title || f.name)}</td>
        <td>${escapeHtml(f.artist || "")}</td>
        <td>${f.genre ? `<span class="tag-genre">${escapeHtml(f.genre)}</span>` : `<span class="tag-missing">no genre</span>`}</td>
        <td>${f.bpm ? escapeHtml(f.bpm) : ""}</td>
      </tr>`;
    }

    wrap.innerHTML = `
      <table class="browser">
        <thead><tr><th>Title</th><th>Artist</th><th>Genre</th><th>BPM</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="4" class="empty-state">Empty folder.</td></tr>`}</tbody>
      </table>`;

    wrap.querySelectorAll(".folder-row").forEach((row) => {
      row.addEventListener("click", () => browseLibrary(row.dataset.path));
    });
    wrap.querySelectorAll(".file-row").forEach((row) => {
      row.addEventListener("click", () => {
        document.getElementById("an-path").value = row.dataset.path;
        goToView("analyze");
        loadAnalyze();
      });
    });
  } catch (e) {
    wrap.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function escapeAttr(s) { return escapeHtml(s); }

document.getElementById("lib-browse-btn").addEventListener("click", () => browseLibrary(libPathInput.value));
libPathInput.addEventListener("keydown", (e) => { if (e.key === "Enter") browseLibrary(libPathInput.value); });

const pickFolderBtn = document.getElementById("lib-pick-folder-btn");
pickFolderBtn.addEventListener("click", async () => {
  pickFolderBtn.disabled = true;
  try {
    const data = await getJSON("/api/pick-folder?" + qs({ initial: libPathInput.value.trim() }));
    if (data.path) {
      libPathInput.value = data.path;
      browseLibrary(data.path);
    }
  } catch (e) {
    showToast("Couldn't open the folder picker: " + e.message, "error");
  } finally {
    pickFolderBtn.disabled = false;
  }
});

// Prefill from remembered config
getJSON("/api/config").then((cfg) => {
  if (cfg.last_library_path) {
    libPathInput.value = cfg.last_library_path;
    browseLibrary(cfg.last_library_path);
  }
  if (cfg.acoustid_key) document.getElementById("acoustid-key").value = cfg.acoustid_key;
}).catch(() => {});

// --- Tidy operation detail toggles ---

document.getElementById("op-clear-genre-toggle").addEventListener("change", (e) => {
  document.getElementById("op-clear-genre-detail").style.display = e.target.checked ? "block" : "none";
});
document.getElementById("op-fill-tags").addEventListener("change", (e) => {
  document.getElementById("op-fill-tags-detail").style.display = e.target.checked ? "block" : "none";
});
document.getElementById("op-lookup").addEventListener("change", (e) => {
  document.getElementById("op-lookup-detail").style.display = e.target.checked ? "block" : "none";
});

// --- Batch analyze cues (SSE) ---

document.getElementById("batch-run-btn").addEventListener("click", () => {
  const path = libPathInput.value.trim();
  if (!path) return;

  const params = {
    path,
    output: document.getElementById("batch-output-xml").value.trim() || "soundwave_output.xml",
    use_demucs: document.getElementById("batch-use-demucs").checked,
    recompute: document.getElementById("batch-recompute").checked,
  };

  const log = document.getElementById("batch-log");
  const progressWrap = document.getElementById("batch-progress-wrap");
  const progressFill = document.getElementById("batch-progress-fill");
  log.innerHTML = "";
  progressWrap.style.display = "block";
  progressFill.style.width = "0%";
  const btn = document.getElementById("batch-run-btn");
  btn.disabled = true;

  const es = new EventSource("/api/analyze/stream?" + qs(params));
  es.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.type === "step") {
      log.innerHTML += `<div class="log-step">${escapeHtml(data.label)}</div>`;
    } else if (data.type === "progress") {
      progressFill.style.width = `${(data.index / data.total) * 100}%`;
      log.innerHTML += `<div>[${data.index}/${data.total}] ${escapeHtml(data.name)} — ${data.cueCount} cue(s)</div>`;
    } else if (data.type === "line") {
      log.innerHTML += `<div class="log-error">${escapeHtml(data.text)}</div>`;
    } else if (data.type === "error") {
      log.innerHTML += `<div class="log-error">ERROR: ${escapeHtml(data.message)}</div>`;
      es.close();
      btn.disabled = false;
      showToast(data.message || "Batch analyze failed", "error");
    } else if (data.type === "done") {
      progressFill.style.width = "100%";
      log.innerHTML += `<div class="log-done">Done. Analyzed ${data.analyzed} track(s), ${data.totalCues} total cue(s)`
        + (data.failed.length ? `, ${data.failed.length} failed` : "")
        + ` → ${escapeHtml(data.output)}</div>`;
      es.close();
      btn.disabled = false;
      state.stats.analyzedCount += data.analyzed;
      state.stats.lastOutput = data.output;
      state.stats.playlists = null; // stale until re-checked in Playlists view
      renderDashboard();
      showToast(`Analyzed ${data.analyzed} track${data.analyzed === 1 ? "" : "s"} — ${data.totalCues} cues total`, "success");
    }
    log.scrollTop = log.scrollHeight;
  };
  es.onerror = () => { es.close(); btn.disabled = false; };
});

// --- Run tidy (SSE) ---

document.getElementById("tidy-run-btn").addEventListener("click", () => {
  const path = libPathInput.value.trim();
  if (!path) return;

  const clearGenreEnabled = document.getElementById("op-clear-genre-toggle").checked;
  const clearGenreFolders = document.getElementById("clear-genre-folders").value
    .split(",").map((s) => s.trim()).filter(Boolean);
  const ignoreFolders = document.getElementById("ignore-folders").value
    .split(",").map((s) => s.trim()).filter(Boolean);
  const acoustidKey = document.getElementById("acoustid-key").value.trim();

  if (acoustidKey) fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ acoustid_key: acoustidKey }) });

  const params = {
    path,
    clear_genre: clearGenreEnabled ? clearGenreFolders : [],
    fill_tags: document.getElementById("op-fill-tags").checked,
    fill_bpm: document.getElementById("op-fill-bpm").checked,
    lookup: document.getElementById("op-lookup").checked,
    acoustid_key: acoustidKey,
    overwrite: document.getElementById("op-overwrite").checked,
    no_art: document.getElementById("op-no-art").checked,
    dedupe: document.getElementById("op-dedupe").checked,
    sort_genres: document.getElementById("op-sort-genres").checked,
    rename: document.getElementById("op-rename").checked,
    ignore: ignoreFolders,
    dry_run: document.getElementById("op-dry-run").checked,
  };

  const log = document.getElementById("tidy-log");
  log.innerHTML = "";
  const btn = document.getElementById("tidy-run-btn");
  btn.disabled = true;

  const es = new EventSource("/api/tidy/stream?" + qs(params));
  es.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.type === "step") {
      log.innerHTML += `<div class="log-step">${escapeHtml(data.label)}</div>`;
    } else if (data.type === "line") {
      log.innerHTML += `<div>${escapeHtml(data.text)}</div>`;
    } else if (data.type === "error") {
      log.innerHTML += `<div class="log-error">ERROR: ${escapeHtml(data.message)}</div>`;
      es.close();
      btn.disabled = false;
      showToast(data.message || "Tidy run failed", "error");
    } else if (data.type === "done") {
      log.innerHTML += `<div class="log-done">Done.</div>`;
      es.close();
      btn.disabled = false;
      browseLibrary(path);
      showToast("Tidy operations complete", "success");
    }
    log.scrollTop = log.scrollHeight;
  };
  es.onerror = () => { es.close(); btn.disabled = false; };
});

// ---------------------------------------------------------------------------
// Analyze & Cues
// ---------------------------------------------------------------------------

const CUE_LABEL_ORDER = ["INTRO", "DROP", "BUILD", "BREAK", "VOCAL", "OUTRO32", "OUTRO16"];

const audioEl = document.getElementById("an-audio");
const canvas = document.getElementById("waveform-canvas");
const ctx = canvas.getContext("2d");

async function loadAnalyze() {
  const path = document.getElementById("an-path").value.trim();
  if (!path) return;
  const useDemucs = document.getElementById("an-use-demucs").checked;

  const panel = document.getElementById("an-panel");
  panel.style.display = "block";
  document.getElementById("an-track-title").textContent = "Analyzing…";
  document.getElementById("an-cue-legend").innerHTML = "";
  document.getElementById("cue-markers").innerHTML = "";
  document.getElementById("an-save-status").textContent = "";

  try {
    const [analysis, waveform] = await Promise.all([
      getJSON("/api/track/analyze?" + qs({ path, use_demucs: useDemucs })),
      getJSON("/api/track/waveform?" + qs({ path })),
    ]);

    state.analyze.path = path;
    state.analyze.duration = analysis.duration_sec;
    state.analyze.bpm = analysis.bpm;
    state.analyze.lastActiveLyricIdx = -1;
    state.analyze.scrollSuspendedUntil = 0;
    state.analyze.cues = analysis.cues.slice().sort((a, b) => a.time_sec - b.time_sec);
    state.analyze.peaks = waveform.peaks;

    document.getElementById("an-track-title").textContent =
      `${analysis.artist || "Unknown"} — ${analysis.title}  ·  ${analysis.bpm.toFixed(1)} BPM`;

    showMiniPlayer(analysis.artist ? `${analysis.artist} — ${analysis.title}` : analysis.title);

    audioEl.src = "/api/track/audio?" + qs({ path });
    document.getElementById("an-time-total").textContent = fmtTime(analysis.duration_sec);
    document.getElementById("an-time-cur").textContent = "0:00";
    updateMiniPlayerTime();

    drawWaveform();
    updatePlayhead();
    renderCueMarkers();
    renderCueLegend();
    loadLyrics(analysis.artist, analysis.title, analysis.duration_sec);
  } catch (e) {
    document.getElementById("an-track-title").textContent = "Error: " + e.message;
  }
}

document.getElementById("an-load-btn").addEventListener("click", loadAnalyze);

function drawWaveform() {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = 140 * dpr;
  ctx.scale(dpr, dpr);

  const w = rect.width, h = 140;
  ctx.clearRect(0, 0, w, h);
  const peaks = state.analyze.peaks;
  if (!peaks || !peaks.length) return;

  const mid = h / 2;
  const barW = w / peaks.length;
  const styles = getComputedStyle(document.documentElement);
  const unplayedColor = styles.getPropertyValue("--waveform").trim() || "#6b7690";
  const playedColor = styles.getPropertyValue("--waveform-played").trim() || "#9aa6c2";
  const duration = state.analyze.duration || 1;
  const playedFrac = Math.min(1, (audioEl.currentTime || 0) / duration);
  const playedBars = Math.floor(playedFrac * peaks.length);

  for (let i = 0; i < peaks.length; i++) {
    const amp = Math.max(1.5, peaks[i] * (h * 0.44));
    ctx.fillStyle = i < playedBars ? playedColor : unplayedColor;
    ctx.fillRect(i * barW, mid - amp, Math.max(1, barW - 0.5), amp * 2);
  }
}
window.addEventListener("resize", () => { if (state.analyze.peaks.length) drawWaveform(); });

function renderCueLegend() {
  const seen = new Set();
  const legend = document.getElementById("an-cue-legend");
  legend.innerHTML = "";
  for (const cue of state.analyze.cues) {
    if (seen.has(cue.label)) continue;
    seen.add(cue.label);
    const chip = document.createElement("span");
    chip.className = "cue-chip";
    chip.style.background = colorToHex(cue.color);
    chip.textContent = cue.label;
    legend.appendChild(chip);
  }
}

function renderCueMarkers() {
  const container = document.getElementById("cue-markers");
  container.innerHTML = "";
  const duration = state.analyze.duration || 1;

  state.analyze.cues.forEach((cue, idx) => {
    const marker = document.createElement("div");
    marker.className = "cue-marker";
    marker.style.left = `${(cue.time_sec / duration) * 100}%`;
    marker.dataset.idx = idx;
    marker.title = `${cue.label} @ ${fmtTime(cue.time_sec)} — click to jump, drag to move`;

    const line = document.createElement("div");
    line.className = "cue-line";
    line.style.background = colorToHex(cue.color);
    marker.appendChild(line);

    const flag = document.createElement("div");
    flag.className = "cue-flag";
    flag.style.background = colorToHex(cue.color);
    flag.textContent = cue.label;
    marker.appendChild(flag);

    const del = document.createElement("button");
    del.className = "cue-delete";
    del.textContent = "×";
    del.title = "Delete this cue";
    del.addEventListener("mousedown", (e) => e.stopPropagation());
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      state.analyze.cues.splice(idx, 1);
      renderCueMarkers();
      renderCueLegend();
    });
    marker.appendChild(del);

    marker.addEventListener("mousedown", (e) => {
      if (e.target.closest(".cue-delete")) return;
      e.preventDefault();
      state.analyze.dragging = idx;
      state.analyze.dragMoved = false;
      state.analyze.dragStartX = e.clientX;
    });

    container.appendChild(marker);
  });
}

document.getElementById("cue-markers").parentElement.addEventListener("mousemove", (e) => {
  if (state.analyze.dragging === null) return;
  if (Math.abs(e.clientX - state.analyze.dragStartX) > 4) state.analyze.dragMoved = true;
  if (!state.analyze.dragMoved) return;

  const wrap = document.querySelector(".waveform-wrap");
  const rect = wrap.getBoundingClientRect();
  let frac = (e.clientX - rect.left) / rect.width;
  frac = Math.min(1, Math.max(0, frac));
  const idx = state.analyze.dragging;
  state.analyze.cues[idx].time_sec = frac * state.analyze.duration;
  const marker = document.querySelector(`.cue-marker[data-idx="${idx}"]`);
  if (marker) marker.style.left = `${frac * 100}%`;
});

window.addEventListener("mouseup", () => {
  const idx = state.analyze.dragging;
  if (idx !== null && !state.analyze.dragMoved) {
    // A click, not a drag: jump playback there so you can audition the cue.
    audioEl.currentTime = state.analyze.cues[idx].time_sec;
    audioEl.play();
  } else if (idx !== null) {
    // Finished a drag: re-sort and refresh so markers/legend stay tidy.
    state.analyze.cues.sort((a, b) => a.time_sec - b.time_sec);
    renderCueMarkers();
    renderCueLegend();
  }
  state.analyze.dragging = null;
  state.analyze.dragMoved = false;
});

// Click waveform to seek
document.querySelector(".waveform-wrap").addEventListener("click", (e) => {
  if (e.target.closest(".cue-marker")) return;
  const rect = e.currentTarget.getBoundingClientRect();
  const frac = (e.clientX - rect.left) / rect.width;
  audioEl.currentTime = frac * state.analyze.duration;
});

// Transport
const playIcon = document.getElementById("an-play-icon");
document.getElementById("an-play-btn").addEventListener("click", () => {
  if (audioEl.paused) audioEl.play(); else audioEl.pause();
});
audioEl.addEventListener("play", () => { playIcon.innerHTML = '<path d="M6 5h4v14H6zM14 5h4v14h-4z"/>'; });
audioEl.addEventListener("pause", () => { playIcon.innerHTML = '<path d="M8 5v14l11-7z"/>'; });
audioEl.addEventListener("seeked", () => {
  document.getElementById("an-time-cur").textContent = fmtTime(audioEl.currentTime);
  updatePlayhead();
  drawWaveform();
  updateActiveLyricLine(audioEl.currentTime, true);
});
audioEl.addEventListener("timeupdate", () => {
  document.getElementById("an-time-cur").textContent = fmtTime(audioEl.currentTime);
  updateActiveLyricLine(audioEl.currentTime);
  updatePlayhead();
  drawWaveform();
});

function updatePlayhead() {
  const duration = state.analyze.duration || 1;
  const frac = Math.min(1, Math.max(0, audioEl.currentTime / duration));
  document.getElementById("playhead").style.left = `${frac * 100}%`;
}

// Save cues
document.getElementById("an-save-cues-btn").addEventListener("click", async () => {
  const status = document.getElementById("an-save-status");
  status.textContent = "Saving…";
  try {
    const body = {
      path: state.analyze.path,
      cues: state.analyze.cues.map((c) => ({ label: c.label, time_sec: c.time_sec, color: c.color })),
      output: document.getElementById("an-output-xml").value.trim() || "soundwave_output.xml",
      bpm: state.analyze.bpm,
      duration_sec: state.analyze.duration,
    };
    const r = await fetch("/api/track/cues", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "Save failed");
    status.textContent = `Saved to ${data.output}`;
    state.stats.analyzedCount += 1;
    state.stats.lastOutput = data.output;
    state.stats.playlists = null;
    renderDashboard();
    showToast(`Cues saved to ${basename(data.output)}`, "success");
  } catch (e) {
    status.textContent = "Error: " + e.message;
    showToast(e.message, "error");
  }
});

// ---------------------------------------------------------------------------
// Mini-player — keeps playback visible & controllable from any view
// ---------------------------------------------------------------------------

const miniPlayer = document.getElementById("mini-player");
const miniPlayIcon = document.getElementById("mini-play-icon");
const miniVinyl = document.getElementById("mini-vinyl");
const navVinyl = document.getElementById("nav-vinyl");
const miniTitle = document.getElementById("mini-player-title");
const miniSub = document.getElementById("mini-player-sub");
const miniScrub = document.getElementById("mini-player-scrub");
const miniScrubFill = document.getElementById("mini-player-scrub-fill");

function showMiniPlayer(label) {
  miniTitle.textContent = label;
  miniPlayer.classList.add("visible");
  document.querySelector(".main").classList.add("has-mini-player");
}

document.getElementById("mini-play-btn").addEventListener("click", () => {
  if (audioEl.paused) audioEl.play(); else audioEl.pause();
});
miniTitle.addEventListener("click", () => goToView("analyze"));

miniScrub.addEventListener("click", (e) => {
  if (!state.analyze.duration) return;
  const rect = miniScrub.getBoundingClientRect();
  const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
  audioEl.currentTime = frac * state.analyze.duration;
});

audioEl.addEventListener("play", () => {
  miniPlayIcon.innerHTML = '<path d="M6 5h4v14H6zM14 5h4v14h-4z"/>';
  miniVinyl.classList.add("spinning");
  navVinyl.classList.add("spinning");
});
audioEl.addEventListener("pause", () => {
  miniPlayIcon.innerHTML = '<path d="M8 5v14l11-7z"/>';
  miniVinyl.classList.remove("spinning");
  navVinyl.classList.remove("spinning");
});
function updateMiniPlayerTime() {
  const duration = state.analyze.duration || 0;
  const frac = duration ? Math.min(1, Math.max(0, audioEl.currentTime / duration)) : 0;
  miniScrubFill.style.width = `${frac * 100}%`;
  miniSub.textContent = `${fmtTime(audioEl.currentTime)} / ${fmtTime(duration)}`;
}
audioEl.addEventListener("timeupdate", updateMiniPlayerTime);
audioEl.addEventListener("seeked", updateMiniPlayerTime);

// --- Manual add cue ---

const newCueLabelSelect = document.getElementById("new-cue-label");
const newCueCustomLabel = document.getElementById("new-cue-custom-label");
const newCueColor = document.getElementById("new-cue-color");

newCueLabelSelect.addEventListener("change", () => {
  const isCustom = newCueLabelSelect.value === "__custom";
  newCueCustomLabel.style.display = isCustom ? "inline-block" : "none";
  newCueColor.style.display = isCustom ? "inline-block" : "none";
});

document.getElementById("add-cue-btn").addEventListener("click", () => {
  if (!state.analyze.path) return;
  const isCustom = newCueLabelSelect.value === "__custom";
  const label = isCustom ? (newCueCustomLabel.value.trim() || "CUE") : newCueLabelSelect.value;
  const color = isCustom
    ? parseInt(newCueColor.value.replace("#", ""), 16)
    : (CUE_COLORS[label] ?? 0xFF6400);

  state.analyze.cues.push({ label, time_sec: audioEl.currentTime, color });
  state.analyze.cues.sort((a, b) => a.time_sec - b.time_sec);
  renderCueMarkers();
  renderCueLegend();
});

// --- Lyrics ---

let lyricLines = [];

async function loadLyrics(artist, title, duration) {
  const panel = document.getElementById("lyrics-panel");
  panel.innerHTML = `<div class="empty-state">Loading lyrics…</div>`;
  lyricLines = [];
  state.analyze.lyricsSynced = false;
  state.analyze.lastActiveLyricIdx = -1;
  try {
    const data = await getJSON("/api/lyrics?" + qs({ artist: artist || "", title, duration }));
    if (!data.lines || !data.lines.length) {
      panel.innerHTML = `<div class="empty-state">No lyrics found.</div>`;
      return;
    }
    lyricLines = data.lines;
    state.analyze.lyricsSynced = !!data.synced;

    const note = data.synced
      ? ""
      : `<div class="lyrics-note">Not time-synced (no line-by-line timing available) — showing plain lyrics.</div>`;
    panel.innerHTML = note + lyricLines.map((l, i) =>
      `<div class="lyric-line" data-idx="${i}">${escapeHtml(l.text)}</div>`
    ).join("");
  } catch (e) {
    panel.innerHTML = `<div class="empty-state">Lyrics lookup failed.</div>`;
  }
}

// Manual scrolling in the lyrics panel suspends auto-follow for a few
// seconds, so trying to read ahead/behind doesn't get yanked back mid-scroll.
const lyricsPanelEl = document.getElementById("lyrics-panel");
lyricsPanelEl.addEventListener("wheel", () => {
  state.analyze.scrollSuspendedUntil = Date.now() + 4000;
});

function updateActiveLyricLine(t, force) {
  if (!lyricLines.length || !state.analyze.lyricsSynced) return;

  let active = -1;
  for (let i = 0; i < lyricLines.length; i++) {
    if (lyricLines[i].time_sec <= t) active = i; else break;
  }
  if (active === state.analyze.lastActiveLyricIdx) return;
  state.analyze.lastActiveLyricIdx = active;

  document.querySelectorAll(".lyric-line").forEach((el) => el.classList.remove("active"));
  if (active < 0) return;

  const el = document.querySelector(`.lyric-line[data-idx="${active}"]`);
  if (!el) return;
  el.classList.add("active");
  if (force || Date.now() > state.analyze.scrollSuspendedUntil) {
    el.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

// ---------------------------------------------------------------------------
// Similarity playlists
// ---------------------------------------------------------------------------

document.getElementById("sim-run-btn").addEventListener("click", () => {
  const path = document.getElementById("sim-path").value.trim();
  if (!path) return;

  const params = {
    path,
    clusters: document.getElementById("sim-clusters").value,
    mode: document.getElementById("sim-mode").value,
    analysis_sec: document.getElementById("sim-analysis-sec").value,
    output: document.getElementById("sim-output").value.trim() || "soundwave_output.xml",
    recompute: document.getElementById("sim-recompute").checked,
  };

  const log = document.getElementById("sim-log");
  const progressWrap = document.getElementById("sim-progress-wrap");
  const progressFill = document.getElementById("sim-progress-fill");
  log.innerHTML = "";
  progressWrap.style.display = "block";
  progressFill.style.width = "0%";
  const btn = document.getElementById("sim-run-btn");
  btn.disabled = true;

  const es = new EventSource("/api/similarity/stream?" + qs(params));
  es.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.type === "step") {
      log.innerHTML += `<div class="log-step">${escapeHtml(data.label)}</div>`;
    } else if (data.type === "progress") {
      progressFill.style.width = `${(data.index / data.total) * 100}%`;
    } else if (data.type === "line") {
      log.innerHTML += `<div>${escapeHtml(data.text)}</div>`;
    } else if (data.type === "error") {
      log.innerHTML += `<div class="log-error">ERROR: ${escapeHtml(data.message)}</div>`;
      es.close();
      btn.disabled = false;
      showToast(data.message || "Similarity run failed", "error");
    } else if (data.type === "done") {
      progressFill.style.width = "100%";
      log.innerHTML += `<div class="log-done">Done. Written to ${escapeHtml(data.output)}</div>`;
      es.close();
      btn.disabled = false;
      state.stats.similarityCount += (data.summary?.clusters?.length || 0) + (data.summary?.path ? 1 : 0);
      state.stats.lastOutput = data.output;
      state.stats.playlists = null;
      renderDashboard();
      showToast(`Playlists written to ${basename(data.output)}`, "success");
    }
    log.scrollTop = log.scrollHeight;
  };
  es.onerror = () => { es.close(); btn.disabled = false; };
});

// ---------------------------------------------------------------------------
// Playlist viewer
// ---------------------------------------------------------------------------

function countPlaylistTracks(nodes) {
  let playlistCount = 0;
  for (const node of nodes) {
    if (node.type === "folder") playlistCount += countPlaylistTracks(node.children);
    else playlistCount += 1;
  }
  return playlistCount;
}

document.getElementById("pl-load-btn").addEventListener("click", async () => {
  const xmlPath = document.getElementById("pl-path").value.trim();
  const tree = document.getElementById("pl-tree");
  tree.innerHTML = `<div class="empty-state">Loading…</div>`;
  try {
    const data = await getJSON("/api/playlists?" + qs({ xml_path: xmlPath }));
    tree.innerHTML = renderPlaylistNodes(data.nodes) || `<div class="empty-state">No playlists found.</div>`;
    state.stats.playlists = { path: xmlPath, count: countPlaylistTracks(data.nodes) };
    renderDashboard();
  } catch (e) {
    tree.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
  }
});

// ---------------------------------------------------------------------------
// Rekordbox re-import checklist
// ---------------------------------------------------------------------------

function updateCheckSourceUI() {
  const useXml = document.getElementById("check-source-xml").checked;
  document.getElementById("check-xml-field").style.display = useXml ? "flex" : "none";
  document.getElementById("check-xml-hint").style.display = useXml ? "block" : "none";
}
document.getElementById("check-source-live").addEventListener("change", updateCheckSourceUI);
document.getElementById("check-source-xml").addEventListener("change", updateCheckSourceUI);
updateCheckSourceUI();

document.getElementById("check-run-btn").addEventListener("click", async () => {
  const output = document.getElementById("check-output-xml").value.trim();
  const useLive = document.getElementById("check-source-live").checked;
  const rekordboxXml = document.getElementById("check-rekordbox-xml").value.trim();
  const results = document.getElementById("check-results");

  if (!output) {
    results.innerHTML = `<div class="empty-state">Enter your Soundwave output XML path above.</div>`;
    return;
  }
  if (!useLive && !rekordboxXml) {
    results.innerHTML = `<div class="empty-state">Enter a Rekordbox collection export XML path, or switch to auto-detect.</div>`;
    return;
  }

  results.innerHTML = `<div class="empty-state">Checking…</div>`;
  try {
    const params = useLive ? { output, live: true } : { output, rekordbox_xml: rekordboxXml };
    const data = await getJSON("/api/reimport-check?" + qs(params));
    results.innerHTML = renderChecklist(data);
  } catch (e) {
    results.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
  }
});

function renderChecklist(data) {
  const section = (title, items, note) => {
    const header = `<h2 style="margin-top:20px;">${escapeHtml(title)} (${items.length})</h2>`;
    if (!items.length) {
      return header + `<div class="empty-state">None.</div>`;
    }
    const rows = items.map((t) => `
      <label class="checkbox-row">
        <input type="checkbox" />
        ${escapeHtml(t.artist ? `${t.artist} — ${t.title}` : (t.title || "Untitled"))}
        <span style="color:var(--text-faint); margin-left:6px;">(${t.cue_count} cues)</span>
      </label>`).join("");
    return header + `<div class="view-subtitle" style="margin-bottom:6px;">${note}</div>${rows}`;
  };

  return section(
    "Need remove + reimport", data.existing,
    "Already in your Rekordbox library — right-click each (multi-select works) &rarr; Remove from Collection, then re-import the XML and Import to Collection again."
  ) + section(
    "Will import cleanly", data.new,
    "Not in your library yet — a normal import (File &rarr; Import Collection in xml format &rarr; right-click &rarr; Import to Collection) is all you need."
  );
}

function renderPlaylistNodes(nodes) {
  return nodes.map((node) => {
    if (node.type === "folder") {
      return `<div class="node"><span class="folder-icon">📁</span><span class="folder-name">${escapeHtml(node.name)}</span>${renderPlaylistNodes(node.children)}</div>`;
    }
    const tracks = node.tracks.map((t) =>
      `<div class="track-row">${escapeHtml(t.artist ? `${t.artist} — ${t.title}` : (t.title || "Untitled"))}${t.bpm ? `<span class="bpm">${escapeHtml(t.bpm)}</span>` : ""}</div>`
    ).join("");
    return `<div class="node"><div class="playlist-name">${escapeHtml(node.name)} <span style="color:var(--text-faint); font-weight:400;">(${node.tracks.length})</span></div>${tracks}</div>`;
  }).join("");
}
