const $ = (id) => document.getElementById(id);

const FILTER_VISIBILITY = {
  home: ["agent"],
  agents: ["agent"],
  calls: ["agent", "direction", "callstatus", "search"],
  sales: ["agent", "pipeline", "oppstatus", "search"],
  appointments: ["agent", "apptstatus"],
};

const lastRows = {}; // tableId -> array of row objects, for CSV export
let charts = {};
let currentView = "home";

// ---------- helpers ----------
function toDateInputValue(d) {
  return d.toISOString().slice(0, 10);
}

function setDefaultRange(daysBack) {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - daysBack);
  $("startDate").value = toDateInputValue(start);
  $("endDate").value = toDateInputValue(end);
}

function fmtDuration(totalSeconds) {
  const s = Math.round(totalSeconds || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m ${sec}s`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

function fmtMoney(v) {
  return new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP" }).format(v || 0);
}

function fmtDateTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  return d.toLocaleString();
}

function showError(message) {
  const el = $("errorBanner");
  el.textContent = message;
  el.hidden = false;
}

function clearError() {
  const el = $("errorBanner");
  el.hidden = true;
  el.textContent = "";
}

async function apiGet(path, params) {
  const url = new URL(path, window.location.origin);
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  });
  const resp = await fetch(url);
  const data = await resp.json();
  if (!resp.ok) {
    const detail = data.reason ? `${data.reason} (HTTP ${data.status_code} on ${data.endpoint})` : (data.message || "Request failed");
    throw new Error(detail);
  }
  return data;
}

function currentFilters() {
  return {
    start: $("startDate").value,
    end: $("endDate").value,
    agentId: $("agentFilter").value,
    direction: $("directionFilter").value,
    status: $("statusFilter").value,
    pipelineId: $("pipelineFilter").value,
    oppStatus: $("oppStatusFilter").value,
    apptStatus: $("apptStatusFilter").value,
    search: $("searchBox").value,
  };
}

function csvEscape(v) {
  const s = v === null || v === undefined ? "" : String(v);
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function exportCsv(tableId) {
  const rows = lastRows[tableId];
  if (!rows || !rows.length) return;
  const headers = Object.keys(rows[0]);
  const lines = [headers.join(",")];
  rows.forEach((r) => lines.push(headers.map((h) => csvEscape(r[h])).join(",")));
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${tableId}_${Date.now()}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ---------- routing ----------
function applyFilterVisibility(view) {
  const visible = new Set(FILTER_VISIBILITY[view] || []);
  document.querySelectorAll("[data-filter]").forEach((el) => {
    el.hidden = !visible.has(el.dataset.filter);
  });
}

function setActiveNav(view) {
  document.querySelectorAll(".nav-link").forEach((a) => {
    a.classList.toggle("active", a.dataset.view === view);
  });
}

function showView(view) {
  document.querySelectorAll(".view").forEach((v) => { v.hidden = true; });
  const el = $(`view-${view}`);
  if (el) el.hidden = false;
  applyFilterVisibility(view);
  setActiveNav(view);
  currentView = view;
  closeSidebar();
}

function routeFromHash() {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const view = hash || "home";
  showView(view);
  loadView(view);
}

function openSidebar() {
  $(".sidebar") && null;
  document.querySelector(".sidebar").classList.add("open");
  $("sidebarOverlay").classList.add("open");
}

function closeSidebar() {
  document.querySelector(".sidebar").classList.remove("open");
  $("sidebarOverlay").classList.remove("open");
}

// ---------- renderers ----------
function statCard(label, value, note) {
  return `<div class="stat-card">
    <div class="label">${label}</div>
    <div class="value">${value}</div>
    ${note ? `<div class="note">${note}</div>` : ""}
  </div>`;
}

function renderOverview(data) {
  const combined = data.calls.combined;
  const cards = [
    statCard("Total Calls", combined.total_calls.toLocaleString()),
    statCard("Answered Calls", combined.answered_calls.toLocaleString()),
    statCard("Missed Calls", combined.missed_calls.toLocaleString()),
    statCard("Inbound Calls", combined.inbound_calls.toLocaleString()),
    statCard("Outbound Calls", combined.outbound_calls.toLocaleString()),
    statCard("Total Call Duration", fmtDuration(combined.total_duration_seconds)),
    statCard("Total Contacts", data.contacts.total.toLocaleString(), data.contacts.note),
    statCard("Total Opportunities", data.opportunities.total.toLocaleString(), data.opportunities.note),
    statCard("Sales", fmtMoney(data.sales.total_value), `${data.sales.won_count} won`),
    statCard("Qualified Appointments", data.qualified_appointments.total.toLocaleString(), data.qualified_appointments.note),
  ];
  $("overviewCards").innerHTML = cards.join("");

  const groups = ["native", "justcall", "voice_ai"];
  const titles = { native: "Native GHL Calls", justcall: "JustCall Calls", voice_ai: "Voice AI Calls" };
  $("callGroups").innerHTML = groups.map((key) => {
    const c = data.calls[key];
    return `<div class="call-group">
      <h4>${titles[key]}</h4>
      <div class="row"><span>Total calls</span><span class="n">${c.total_calls}</span></div>
      <div class="row answered"><span>Answered</span><span class="n">${c.answered_calls}</span></div>
      <div class="row missed"><span>Missed</span><span class="n">${c.missed_calls}</span></div>
      <div class="row"><span>Total duration</span><span class="n">${fmtDuration(c.total_duration_seconds)}</span></div>
      <div class="note">${c.note}</div>
    </div>`;
  }).join("");

  if (data.calls.scan_truncated) {
    showError("Warning: this date range had more call activity than could be fully scanned in one request, so call figures may be a partial undercount. Try a narrower date range for exact numbers.");
  }
}

function destroyChart(key) {
  if (charts[key]) { charts[key].destroy(); delete charts[key]; }
}

const CHART_COLORS = ["#2f6fed", "#1f9d55", "#d64545", "#e8a53b", "#8858d6", "#0aa3a3", "#c85fa0", "#6b7688"];

function renderCharts(data) {
  destroyChart("agent");
  destroyChart("status");
  destroyChart("time");
  destroyChart("stage");

  charts.agent = new Chart($("chartCallsByAgent"), {
    type: "bar",
    data: {
      labels: data.calls_by_agent.map((r) => r.agent_name),
      datasets: [{ label: "Total Calls", data: data.calls_by_agent.map((r) => r.total_calls), backgroundColor: CHART_COLORS[0] }],
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } },
  });

  charts.status = new Chart($("chartCallsByStatus"), {
    type: "doughnut",
    data: {
      labels: data.calls_by_status.map((r) => r.status),
      datasets: [{ data: data.calls_by_status.map((r) => r.count), backgroundColor: CHART_COLORS }],
    },
    options: { responsive: true, maintainAspectRatio: false },
  });

  charts.time = new Chart($("chartCallsOverTime"), {
    type: "line",
    data: {
      labels: data.calls_over_time.map((r) => r.date),
      datasets: [
        { label: "Total", data: data.calls_over_time.map((r) => r.total), borderColor: CHART_COLORS[0], tension: 0.25 },
        { label: "Answered", data: data.calls_over_time.map((r) => r.answered), borderColor: CHART_COLORS[1], tension: 0.25 },
        { label: "Missed", data: data.calls_over_time.map((r) => r.missed), borderColor: CHART_COLORS[2], tension: 0.25 },
      ],
    },
    options: { responsive: true, maintainAspectRatio: false },
  });

  charts.stage = new Chart($("chartSalesByStage"), {
    type: "bar",
    data: {
      labels: data.sales_by_stage.map((r) => `${r.pipeline_name} / ${r.stage_name}`),
      datasets: [{ label: "Opportunities", data: data.sales_by_stage.map((r) => r.count), backgroundColor: CHART_COLORS[3] }],
    },
    options: { responsive: true, maintainAspectRatio: false, indexAxis: "y", plugins: { legend: { display: false } } },
  });
}

function renderAgentTable(data) {
  const tbody = $("agentTableBody");
  const empty = $("agentTableEmpty");
  lastRows.agentTable = data.rows.map((r) => ({
    agent_name: r.agent_name, source: r.source, total_calls: r.total_calls, answered_calls: r.answered_calls,
    missed_calls: r.missed_calls, inbound_calls: r.inbound_calls, outbound_calls: r.outbound_calls,
    total_duration_seconds: r.total_duration_seconds, avg_call_duration_seconds: r.avg_call_duration_seconds,
  }));
  if (!data.rows.length) { tbody.innerHTML = ""; empty.hidden = false; return; }
  empty.hidden = true;
  tbody.innerHTML = data.rows.map((r) => `
    <tr>
      <td>${r.agent_name}</td>
      <td><span class="badge ${r.source === "native" ? "badge-native" : "badge-justcall"}">${r.source === "native" ? "Native" : "JustCall"}</span></td>
      <td>${r.total_calls}</td><td>${r.answered_calls}</td><td>${r.missed_calls}</td>
      <td>${r.inbound_calls}</td><td>${r.outbound_calls}</td>
      <td>${fmtDuration(r.total_duration_seconds)}</td><td>${fmtDuration(r.avg_call_duration_seconds)}</td>
    </tr>`).join("");
}

function escapeAttr(s) {
  return String(s || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

function recordingProxyUrl(r) {
  if (r.recording_kind === "native") {
    return `/api/recording-proxy?messageId=${encodeURIComponent(r.recording_ref)}`;
  }
  if (r.recording_kind === "external") {
    return `/api/recording-proxy?url=${encodeURIComponent(r.recording_ref)}`;
  }
  return null;
}

function recordingCell(r) {
  if (!r.has_recording) {
    return `<span class="badge badge-no">None</span>`;
  }
  const proxyUrl = escapeAttr(recordingProxyUrl(r));
  const label = escapeAttr(`${r.contact_name || "Unknown"} — ${fmtDateTime(r.date_added)} — ${r.agent_name || "Unassigned"}`);
  return `<div class="recording-cell">
    <button type="button" class="btn btn-primary btn-play" data-play-url="${proxyUrl}" data-play-label="${label}">&#9654; Play</button>
    <a href="${proxyUrl}" target="_blank" rel="noopener" class="rec-link">Open in tab ↗</a>
  </div>`;
}

const MEDIA_ERROR_MESSAGES = {
  1: "Loading was aborted.",
  2: "Network error while fetching the recording.",
  3: "The browser couldn't decode this audio file.",
  4: "Couldn't load this recording - it may not exist for this call (some completed calls aren't recorded), or the link has expired. Try 'Open in new tab', or re-apply the filters for a fresh link.",
};

function setPlayerStatus(text, isError) {
  const el = $("playerStatus");
  if (!text) { el.hidden = true; el.textContent = ""; return; }
  el.hidden = false;
  el.textContent = text;
  el.classList.toggle("player-status-error", !!isError);
}

function playRecording(proxyUrl, label) {
  const audio = $("playerAudio");
  $("playerOpenLink").href = proxyUrl;
  $("playerSubtitle").textContent = label || "";
  $("playerOverlay").hidden = false;
  setPlayerStatus("Loading...", false);
  // proxyUrl already points at our own /api/recording-proxy (same-origin) -
  // needed either because the external host blocks embedded <audio> requests
  // (JustCall), or because the recording requires our server-side GHL token
  // to fetch at all (native GHL calls).
  audio.src = proxyUrl;
  audio.play().catch(() => { /* surfaced via the error/loadeddata listeners below */ });
}

function closePlayer() {
  const audio = $("playerAudio");
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  $("playerOverlay").hidden = true;
  setPlayerStatus(null);
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-play-url]");
  if (btn) playRecording(btn.dataset.playUrl, btn.dataset.playLabel);
});
$("playerClose").addEventListener("click", closePlayer);
$("playerAudio").addEventListener("loadeddata", () => setPlayerStatus(null));
$("playerAudio").addEventListener("playing", () => setPlayerStatus(null));
$("playerAudio").addEventListener("error", () => {
  const err = $("playerAudio").error;
  setPlayerStatus(MEDIA_ERROR_MESSAGES[err && err.code] || "Couldn't play this recording.", true);
});

function renderCallLogsTable(data) {
  const tbody = $("callLogsTableBody");
  const empty = $("callLogsEmpty");
  lastRows.callLogsTable = data.rows.map((r) => ({
    date_time: fmtDateTime(r.date_added), agent: r.agent_name, customer: r.contact_name,
    phone: r.phone_number, direction: r.direction, status: r.status,
    duration_seconds: r.duration_seconds, has_recording: r.has_recording, recording_kind: r.recording_kind || "", source: r.source,
  }));
  if (!data.rows.length) { tbody.innerHTML = ""; empty.hidden = false; return; }
  empty.hidden = true;
  tbody.innerHTML = data.rows.map((r) => `
    <tr>
      <td>${fmtDateTime(r.date_added)}</td><td>${r.agent_name}</td><td>${r.contact_name}</td>
      <td>${r.phone_number || "-"}</td><td>${r.direction}</td><td>${r.status}</td>
      <td>${fmtDuration(r.duration_seconds)}</td>
      <td>${recordingCell(r)}</td>
    </tr>`).join("");
  if (data.scan_truncated) showError("Warning: call log scan was partially truncated for this range/filters - results may be incomplete. Try a narrower date range.");
}

function renderSalesTable(data) {
  const tbody = $("salesTableBody");
  const empty = $("salesEmpty");
  const note = $("salesTruncatedNote");
  lastRows.salesTable = data.rows.map((r) => ({
    customer: r.customer_name, agent: r.agent_name, pipeline: r.pipeline_name, stage: r.stage_name,
    status: r.status, value: r.value, created_at: r.created_at, updated_at: r.updated_at,
  }));
  if (data.truncated) {
    note.hidden = false;
    note.textContent = `Showing ${data.returned} of ${data.total} matching opportunities (result set capped). Narrow the date range or add filters to see the rest.`;
  } else {
    note.hidden = true;
  }
  if (!data.rows.length) { tbody.innerHTML = ""; empty.hidden = false; return; }
  empty.hidden = true;
  tbody.innerHTML = data.rows.map((r) => `
    <tr>
      <td>${r.customer_name || "Unknown"}</td><td>${r.agent_name}</td><td>${r.pipeline_name}</td>
      <td>${r.stage_name}</td><td>${r.status}</td><td>${fmtMoney(r.value)}</td>
      <td>${fmtDateTime(r.created_at)}</td><td>${fmtDateTime(r.updated_at)}</td>
    </tr>`).join("");
}

function renderAppointmentsTable(data) {
  const tbody = $("appointmentsTableBody");
  const empty = $("appointmentsEmpty");
  lastRows.appointmentsTable = data.rows.map((r) => ({
    customer: r.customer_name, agent: r.agent_name, start_time: r.start_time, status: r.status,
  }));
  if (!data.rows.length) { tbody.innerHTML = ""; empty.hidden = false; return; }
  empty.hidden = true;
  tbody.innerHTML = data.rows.map((r) => `
    <tr><td>${r.customer_name}</td><td>${r.agent_name}</td><td>${fmtDateTime(r.start_time)}</td><td>${r.status}</td></tr>
  `).join("");
}

// ---------- per-view loaders ----------
async function loadHome(f) {
  const [overview, chartData] = await Promise.all([
    apiGet("/api/overview", { start: f.start, end: f.end }),
    apiGet("/api/charts/overview", { start: f.start, end: f.end }),
  ]);
  renderOverview(overview);
  renderCharts(chartData);
}

async function loadAgents(f) {
  const data = await apiGet("/api/agent-performance", { start: f.start, end: f.end, agentId: f.agentId });
  renderAgentTable(data);
}

async function loadCalls(f) {
  const data = await apiGet("/api/call-logs", {
    start: f.start, end: f.end, agentId: f.agentId, direction: f.direction, status: f.status, search: f.search,
  });
  renderCallLogsTable(data);
}

async function loadSales(f) {
  const data = await apiGet("/api/opportunities", {
    start: f.start, end: f.end, agentId: f.agentId, pipelineId: f.pipelineId, status: f.oppStatus, search: f.search,
  });
  renderSalesTable(data);
}

async function loadAppointments(f) {
  const data = await apiGet("/api/appointments", { start: f.start, end: f.end, agentId: f.agentId, status: f.apptStatus });
  renderAppointmentsTable(data);
}

const VIEW_LOADERS = { home: loadHome, agents: loadAgents, calls: loadCalls, sales: loadSales, appointments: loadAppointments };

async function loadView(view) {
  clearError();
  const f = currentFilters();
  $("refreshBtn").disabled = true;
  $("refreshBtn").textContent = "Loading...";
  try {
    await VIEW_LOADERS[view](f);
    $("lastUpdated").textContent = `Last updated ${new Date().toLocaleTimeString()}`;
  } catch (e) {
    showError(`Could not load data: ${e.message}`);
  } finally {
    $("refreshBtn").disabled = false;
    $("refreshBtn").textContent = "Refresh";
  }
}

// ---------- filter option loaders ----------
async function loadAgentOptions() {
  try {
    const data = await apiGet("/api/agents");
    const select = $("agentFilter");
    data.agents.sort((a, b) => a.name.localeCompare(b.name)).forEach((a) => {
      const opt = document.createElement("option");
      opt.value = a.id; opt.textContent = a.name;
      select.appendChild(opt);
    });
  } catch (e) { console.error("Failed to load agents", e); }
}

async function loadPipelineOptions() {
  try {
    const data = await apiGet("/api/pipelines");
    const pSelect = $("pipelineFilter");
    data.pipelines.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.id; opt.textContent = p.name;
      pSelect.appendChild(opt);
    });
    const oSelect = $("oppStatusFilter");
    data.opportunity_statuses.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s; opt.textContent = s.charAt(0).toUpperCase() + s.slice(1);
      oSelect.appendChild(opt);
    });
  } catch (e) { console.error("Failed to load pipelines", e); }
}

// ---------- wiring ----------
document.querySelectorAll("[data-range]").forEach((btn) => {
  btn.addEventListener("click", () => { setDefaultRange(parseInt(btn.dataset.range, 10)); loadView(currentView); });
});

document.querySelectorAll("[data-export]").forEach((btn) => {
  btn.addEventListener("click", () => exportCsv(btn.dataset.export));
});

$("applyBtn").addEventListener("click", () => loadView(currentView));
$("refreshBtn").addEventListener("click", () => loadView(currentView));
$("menuBtn").addEventListener("click", openSidebar);
$("sidebarClose").addEventListener("click", closeSidebar);
$("sidebarOverlay").addEventListener("click", closeSidebar);

window.addEventListener("hashchange", routeFromHash);

setDefaultRange(0);
loadAgentOptions();
loadPipelineOptions();
routeFromHash();
