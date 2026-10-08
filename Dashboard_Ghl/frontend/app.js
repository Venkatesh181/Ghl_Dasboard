const $ = (id) => document.getElementById(id);

const FILTER_VISIBILITY = {
  home: ["agent", "pipeline", "stage"],
  agents: ["agent"],
  calls: ["agent", "direction", "callstatus", "search"],
  sales: ["agent", "pipeline", "stage", "oppstatus", "search"],
  appointments: ["agent", "apptstatus"],
};

const CHART_COLORS = ["#2563eb", "#10b981", "#ef4444", "#f59e0b", "#8b5cf6", "#06b6d4", "#ec4899", "#64748b"];
const lastRows = {};
let charts = {};
let currentView = "home";

// Client-side cache to make view switches instant
const CLIENT_CACHE = new Map();
const CLIENT_CACHE_TTL = 60000; // 60 seconds

// Interactive Stage Table State
let stageTableState = {
  rawData: [],
  search: "",
  hideZero: false,
  sortCol: "count",
  sortAsc: false,
};

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
  return isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
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

async function apiGet(path, params, bypassCache = false) {
  const url = new URL(path, window.location.origin);
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  });
  const cacheKey = url.toString();

  if (!bypassCache && CLIENT_CACHE.has(cacheKey)) {
    const entry = CLIENT_CACHE.get(cacheKey);
    if (Date.now() - entry.time < CLIENT_CACHE_TTL) {
      return entry.data;
    }
  }

  const resp = await fetch(url);
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(data.message || data.reason || "Request failed");
  }

  CLIENT_CACHE.set(cacheKey, { data, time: Date.now() });
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
    stageId: $("stageFilter").value,
    oppStatus: $("oppStatusFilter").value,
    apptStatus: $("apptStatusFilter").value,
    search: $("searchBox").value,
  };
}

function exportCsv(tableId) {
  const rows = lastRows[tableId];
  if (!rows || !rows.length) return;
  const headers = Object.keys(rows[0]);
  const lines = [headers.join(",")];
  rows.forEach((r) => {
    lines.push(headers.map((h) => {
      const v = r[h] === null || r[h] === undefined ? "" : String(r[h]);
      return `"${v.replace(/"/g, '""')}"`;
    }).join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${tableId}_${Date.now()}.csv`;
  a.click();
}

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
  document.querySelectorAll(".view").forEach((v) => (v.hidden = true));
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
  document.querySelector(".sidebar").classList.add("open");
  $("sidebarOverlay").classList.add("open");
}

function closeSidebar() {
  document.querySelector(".sidebar").classList.remove("open");
  $("sidebarOverlay").classList.remove("open");
}

function statCard(label, value, note) {
  return `<div class="stat-card">
    <div class="label">${label}</div>
    <div class="value">${value}</div>
    ${note ? `<div class="note">${note}</div>` : ""}
  </div>`;
}

function renderOverview(data) {
  const c = data.calls.combined;
  $("overviewCards").innerHTML = [
    statCard("Total Calls", c.total_calls.toLocaleString()),
    statCard("Answered", c.answered_calls.toLocaleString()),
    statCard("Missed", c.missed_calls.toLocaleString()),
    statCard("Inbound / Outbound", `${c.inbound_calls} / ${c.outbound_calls}`),
    statCard("Total Duration", fmtDuration(c.total_duration_seconds)),
    statCard("Total Contacts", data.contacts.total.toLocaleString()),
    statCard("Sales Volume", fmtMoney(data.sales.total_value), `${data.sales.won_count} deals won`),
    statCard("Qualified Deals", data.qualified_appointments.total.toLocaleString()),
  ].join("");

  const groups = ["native", "justcall", "voice_ai"];
  const titles = { native: "Native GHL Calls", justcall: "JustCall Calls", voice_ai: "AI Voice Agent" };
  $("callGroups").innerHTML = groups.map((k) => {
    const item = data.calls[k];
    return `<div class="call-group">
      <h4>${titles[k]}</h4>
      <div class="row"><span>Total calls</span><span class="n">${item.total_calls}</span></div>
      <div class="row answered"><span>Answered</span><span class="n">${item.answered_calls}</span></div>
      <div class="row missed"><span>Missed</span><span class="n">${item.missed_calls}</span></div>
      <div class="row"><span>Talk Time</span><span class="n">${fmtDuration(item.total_duration_seconds)}</span></div>
    </div>`;
  }).join("");
}

function destroyChart(key) {
  if (charts[key]) {
    charts[key].destroy();
    delete charts[key];
  }
}

function renderCharts(data) {
  destroyChart("agent");
  destroyChart("status");
  destroyChart("time");

  charts.agent = new Chart($("chartCallsByAgent"), {
    type: "bar",
    data: {
      labels: data.calls_by_agent.map((r) => r.agent_name),
      datasets: [{ label: "Calls", data: data.calls_by_agent.map((r) => r.total_calls), backgroundColor: CHART_COLORS[0], borderRadius: 4 }],
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
        { label: "Total", data: data.calls_over_time.map((r) => r.total), borderColor: CHART_COLORS[0], tension: 0.3 },
        { label: "Answered", data: data.calls_over_time.map((r) => r.answered), borderColor: CHART_COLORS[1], tension: 0.3 },
        { label: "Missed", data: data.calls_over_time.map((r) => r.missed), borderColor: CHART_COLORS[2], tension: 0.3 },
      ],
    },
    options: { responsive: true, maintainAspectRatio: false },
  });

  renderStageCharts(data.sales_by_stage);
}

function renderStageCharts(byStage) {
  Object.keys(charts).filter((k) => k.startsWith("stage:")).forEach((k) => destroyChart(k));
  const container = $("stageChartsGrid");
  container.innerHTML = "";

  const groups = new Map();
  byStage.forEach((r) => {
    const k = r.pipeline_id || r.pipeline_name;
    if (!groups.has(k)) groups.set(k, { pipeline_name: r.pipeline_name, rows: [] });
    groups.get(k).rows.push(r);
  });

  let i = 0;
  groups.forEach((group) => {
    const card = document.createElement("div");
    card.className = "chart-card";
    card.innerHTML = `<h4>${group.pipeline_name}</h4><canvas></canvas>`;
    container.appendChild(card);
    charts[`stage:${i++}`] = new Chart(card.querySelector("canvas"), {
      type: "bar",
      data: {
        labels: group.rows.map((r) => r.stage_name),
        datasets: [{ label: "Opportunities", data: group.rows.map((r) => r.count), backgroundColor: CHART_COLORS[4], borderRadius: 4 }],
      },
      options: { responsive: true, maintainAspectRatio: false, indexAxis: "y", plugins: { legend: { display: false } } },
    });
  });
}

function renderSalesSummary(summary, cardsElId = "salesSummaryCards") {
  $(cardsElId).innerHTML = [
    statCard("Open Deals", summary.open.count.toLocaleString(), fmtMoney(summary.open.value)),
    statCard("Total Won Revenue", fmtMoney(summary.total_income), `${summary.won.count} Deals Closed Won`),
    statCard("Closed (All)", summary.closed.count.toLocaleString(), fmtMoney(summary.closed.value)),
    statCard("Lost Deals", summary.lost.count.toLocaleString(), fmtMoney(summary.lost.value)),
    statCard("Abandoned", summary.abandoned.count.toLocaleString(), fmtMoney(summary.abandoned.value)),
  ].join("");
}

function renderStageTable(byStage, tableKey = "homeStageTable") {
  if (tableKey === "homeStageTable") {
    stageTableState.rawData = byStage || [];
    updateStageTable();
  } else {
    const tbody = $(`${tableKey}Body`);
    if (!tbody) return;
    lastRows[tableKey] = byStage;
    tbody.innerHTML = byStage.map((r) => `
      <tr><td>${r.pipeline_name}</td><td>${r.stage_name}</td><td><strong>${r.count}</strong></td></tr>
    `).join("");
  }
}

function updateStageTable() {
  const tbody = $("homeStageTableBody");
  const emptyEl = $("stageTableEmpty");
  const summaryBadge = $("stageTableSummary");
  if (!tbody) return;

  let filtered = stageTableState.rawData.filter((r) => {
    const q = stageTableState.search.toLowerCase().trim();
    const matchesSearch = !q ||
      (r.pipeline_name && r.pipeline_name.toLowerCase().includes(q)) ||
      (r.stage_name && r.stage_name.toLowerCase().includes(q));

    const matchesZero = stageTableState.hideZero ? Number(r.count) > 0 : true;
    return matchesSearch && matchesZero;
  });

  filtered.sort((a, b) => {
    let valA = a[stageTableState.sortCol];
    let valB = b[stageTableState.sortCol];

    if (stageTableState.sortCol === "count") {
      valA = Number(valA) || 0;
      valB = Number(valB) || 0;
      return stageTableState.sortAsc ? valA - valB : valB - valA;
    }

    valA = String(valA || "").toLowerCase();
    valB = String(valB || "").toLowerCase();
    return stageTableState.sortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
  });

  lastRows["homeStageTable"] = filtered;

  const totalLeads = filtered.reduce((acc, curr) => acc + (Number(curr.count) || 0), 0);
  if (summaryBadge) {
    summaryBadge.textContent = `${totalLeads.toLocaleString()} Active Leads (${filtered.length} Stages)`;
  }

  if (!filtered.length) {
    tbody.innerHTML = "";
    if (emptyEl) emptyEl.hidden = false;
    return;
  }
  if (emptyEl) emptyEl.hidden = true;

  tbody.innerHTML = filtered.map((r) => {
    const count = Number(r.count) || 0;
    const badgeClass = count > 0 ? "has-leads" : "zero";
    return `
      <tr>
        <td><strong>${r.pipeline_name}</strong></td>
        <td>${r.stage_name}</td>
        <td class="text-right">
          <span class="count-badge ${badgeClass}">${count}</span>
        </td>
      </tr>
    `;
  }).join("");

  ["pipeline_name", "stage_name", "count"].forEach((col) => {
    const icon = $(`sort-icon-${col}`);
    if (!icon) return;
    if (stageTableState.sortCol === col) {
      icon.classList.add("active");
      icon.textContent = stageTableState.sortAsc ? "↑" : "↓";
    } else {
      icon.classList.remove("active");
      icon.textContent = "↕";
    }
  });
}

function renderAgentTable(data) {
  const tbody = $("agentTableBody");
  lastRows.agentTable = data.rows;
  if (!data.rows.length) { tbody.innerHTML = ""; $("agentTableEmpty").hidden = false; return; }
  $("agentTableEmpty").hidden = true;
  tbody.innerHTML = data.rows.map((r) => `
    <tr>
      <td><strong>${r.agent_name}</strong></td>
      <td><span class="badge ${r.source === "native" ? "badge-native" : "badge-justcall"}">${r.source}</span></td>
      <td>${r.total_calls}</td><td>${r.answered_calls}</td><td>${r.missed_calls}</td>
      <td>${r.inbound_calls}</td><td>${r.outbound_calls}</td>
      <td>${fmtDuration(r.total_duration_seconds)}</td><td>${fmtDuration(r.avg_call_duration_seconds)}</td>
    </tr>`).join("");
}

function renderCallLogsTable(data) {
  const tbody = $("callLogsTableBody");
  lastRows.callLogsTable = data.rows;
  if (!data.rows.length) { tbody.innerHTML = ""; $("callLogsEmpty").hidden = false; return; }
  $("callLogsEmpty").hidden = true;
  tbody.innerHTML = data.rows.map((r) => `
    <tr>
      <td>${fmtDateTime(r.date_added)}</td>
      <td>${r.agent_name}</td>
      <td><strong>${r.contact_name}</strong></td>
      <td>${r.phone_number || "-"}</td>
      <td>${r.direction}</td>
      <td>${r.status}</td>
      <td>${fmtDuration(r.duration_seconds)}</td>
      <td>
        ${r.has_recording
          ? `<button class="btn btn-primary btn-play" data-play-url="/api/recording-proxy?${r.recording_kind === "native" ? "messageId" : "url"}=${encodeURIComponent(r.recording_ref)}" data-play-label="${r.contact_name}">▶ Play</button>`
          : `<span class="badge badge-no">None</span>`}
      </td>
    </tr>`).join("");
}

function renderSalesTable(data) {
  const tbody = $("salesTableBody");
  lastRows.salesTable = data.rows;
  if (!data.rows.length) { tbody.innerHTML = ""; $("salesEmpty").hidden = false; return; }
  $("salesEmpty").hidden = true;
  tbody.innerHTML = data.rows.map((r) => `
    <tr>
      <td><strong>${r.customer_name || "Unknown"}</strong></td>
      <td>${r.agent_name}</td><td>${r.pipeline_name}</td><td>${r.stage_name}</td>
      <td><span class="badge ${r.status === "won" ? "badge-native" : "badge-no"}">${r.status}</span></td>
      <td><strong>${fmtMoney(r.value)}</strong></td>
      <td>${fmtDateTime(r.created_at)}</td><td>${fmtDateTime(r.updated_at)}</td>
    </tr>`).join("");
}

function renderAppointmentsTable(data) {
  const tbody = $("appointmentsTableBody");
  lastRows.appointmentsTable = data.rows;
  if (!data.rows.length) { tbody.innerHTML = ""; $("appointmentsEmpty").hidden = false; return; }
  $("appointmentsEmpty").hidden = true;
  tbody.innerHTML = data.rows.map((r) => `
    <tr>
      <td><strong>${r.customer_name}</strong></td>
      <td>${r.agent_name}</td><td>${fmtDateTime(r.start_time)}</td><td>${r.status}</td>
    </tr>`).join("");
}

function playRecording(url, label) {
  const audio = $("playerAudio");
  $("playerOpenLink").href = url;
  $("playerSubtitle").textContent = label || "";
  $("playerOverlay").hidden = false;
  audio.src = url;
  audio.play().catch(() => {});
}

$("playerClose").addEventListener("click", () => {
  const audio = $("playerAudio");
  audio.pause();
  audio.removeAttribute("src");
  $("playerOverlay").hidden = true;
});

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-play-url]");
  if (btn) playRecording(btn.dataset.playUrl, btn.dataset.playLabel);
});

// View Loaders
async function loadHome(f, bypass = false) {
  const [overview, chartData, oppSummary] = await Promise.all([
    apiGet("/api/overview", { start: f.start, end: f.end }, bypass),
    apiGet("/api/charts/overview", { start: f.start, end: f.end, agentId: f.agentId, pipelineId: f.pipelineId }, bypass),
    apiGet("/api/opportunities/summary", { start: f.start, end: f.end, agentId: f.agentId, pipelineId: f.pipelineId, stageId: f.stageId }, bypass),
  ]);
  renderOverview(overview);
  renderCharts(chartData);
  renderSalesSummary(oppSummary, "homeSalesSummaryCards");
  renderStageTable(oppSummary.by_stage, "homeStageTable");
}

async function loadAgents(f, bypass = false) {
  renderAgentTable(await apiGet("/api/agent-performance", { start: f.start, end: f.end, agentId: f.agentId }, bypass));
}

async function loadCalls(f, bypass = false) {
  renderCallLogsTable(await apiGet("/api/call-logs", {
    start: f.start, end: f.end, agentId: f.agentId, direction: f.direction, status: f.status, search: f.search,
  }, bypass));
}

async function loadSales(f, bypass = false) {
  const [data, summary] = await Promise.all([
    apiGet("/api/opportunities", {
      start: f.start, end: f.end, agentId: f.agentId, pipelineId: f.pipelineId, stageId: f.stageId, status: f.oppStatus, search: f.search,
    }, bypass),
    apiGet("/api/opportunities/summary", { start: f.start, end: f.end, agentId: f.agentId, pipelineId: f.pipelineId, stageId: f.stageId }, bypass),
  ]);
  renderSalesSummary(summary);
  renderStageTable(summary.by_stage, "stageTable");
  renderSalesTable(data);
}

async function loadAppointments(f, bypass = false) {
  renderAppointmentsTable(await apiGet("/api/appointments", { start: f.start, end: f.end, agentId: f.agentId, status: f.apptStatus }, bypass));
}

const VIEW_LOADERS = { home: loadHome, agents: loadAgents, calls: loadCalls, sales: loadSales, appointments: loadAppointments };

async function loadView(view, bypassCache = false) {
  clearError();
  const f = currentFilters();
  const btn = $("refreshBtn");
  btn.disabled = true;
  btn.textContent = "Loading...";
  try {
    await VIEW_LOADERS[view](f, bypassCache);
    $("lastUpdated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (e) {
    showError(`Error loading data: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Refresh Data";
  }
}

async function loadFilterOptions() {
  try {
    const [agentsData, pipelinesData] = await Promise.all([apiGet("/api/agents"), apiGet("/api/pipelines")]);
    const aSelect = $("agentFilter");
    agentsData.agents.sort((a, b) => a.name.localeCompare(b.name)).forEach((a) => {
      aSelect.add(new Option(a.name, a.id));
    });

    const pSelect = $("pipelineFilter");
    pipelinesData.pipelines.forEach((p) => pSelect.add(new Option(p.name, p.id)));

    const sSelect = $("stageFilter");
    pSelect.addEventListener("change", () => {
      sSelect.innerHTML = `<option value="">All stages</option>`;
      const pl = pipelinesData.pipelines.find((p) => p.id === pSelect.value);
      if (pl) pl.stages.forEach((s) => sSelect.add(new Option(s.name, s.id)));
      sSelect.disabled = !pl;
    });
    sSelect.disabled = true;

    const oSelect = $("oppStatusFilter");
    pipelinesData.opportunity_statuses.forEach((s) => oSelect.add(new Option(s.toUpperCase(), s)));
  } catch (err) {
    console.error("Filter loading failed", err);
  }
}

// Events
document.querySelectorAll("[data-range]").forEach((b) => {
  b.addEventListener("click", () => { setDefaultRange(parseInt(b.dataset.range, 10)); loadView(currentView, true); });
});
document.querySelectorAll("[data-export]").forEach((b) => b.addEventListener("click", () => exportCsv(b.dataset.export)));
$("applyBtn").addEventListener("click", () => loadView(currentView, true));
$("refreshBtn").addEventListener("click", () => {
  CLIENT_CACHE.clear();
  loadView(currentView, true);
});
$("menuBtn").addEventListener("click", openSidebar);
$("sidebarClose").addEventListener("click", closeSidebar);
$("sidebarOverlay").addEventListener("click", closeSidebar);

// Hook up Table Filtering and Sorting
$("stageTableSearch").addEventListener("input", (e) => {
  stageTableState.search = e.target.value;
  updateStageTable();
});

$("stageTableHideZero").addEventListener("change", (e) => {
  stageTableState.hideZero = e.target.checked;
  updateStageTable();
});

document.querySelectorAll("#homeStageTable th.sortable").forEach((th) => {
  th.addEventListener("click", () => {
    const col = th.dataset.sort;
    if (stageTableState.sortCol === col) {
      stageTableState.sortAsc = !stageTableState.sortAsc;
    } else {
      stageTableState.sortCol = col;
      stageTableState.sortAsc = col !== "count";
    }
    updateStageTable();
  });
});

window.addEventListener("hashchange", routeFromHash);

// Boot
setDefaultRange(0);
loadFilterOptions();
routeFromHash();