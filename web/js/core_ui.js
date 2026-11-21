// OSINT-Auto :: Navegación entre vistas, estado general del servidor, reportes y polling de jobs.

import { VIEW_META, state, esc, toast, api } from "./state.js";
import { loadCases, selectCase, loadStorageMaintenance } from "./cases.js";
import { openSnaView } from "./sna.js";
import { openAgentView } from "./agent.js";
// ---------------------------------------------------------------------------
// Navegación entre vistas
// ---------------------------------------------------------------------------
export function switchView(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));

  const view = document.getElementById(`view-${name}`);
  const btn = document.querySelector(`.nav-item[data-view="${name}"]`);
  if (view) view.classList.add("active");
  if (btn) btn.classList.add("active");

  const [title, sub] = VIEW_META[name] || ["OSINT-Auto", ""];
  document.getElementById("viewTitle").textContent = title;
  document.getElementById("viewSubtitle").textContent = sub;

  if (name === "history" || name === "dashboard") {
    loadReports();
  }
  if (name === "history") {
    loadStorageMaintenance();
  }
  if (name === "cases") {
    loadCases();
  }
  if (name === "sna") {
    openSnaView(state.sna.caseId || state.selectedCaseId || state.activeCaseId);
  }
  if (name === "agent") {
    openAgentView();
  }
}

// ---------------------------------------------------------------------------
// Carga de estado e historial
// ---------------------------------------------------------------------------
export async function loadStatus() {
  try {
    const data = await api("/api/status");
    const tools = data.tools || {};
    const totalTools = Object.keys(tools).length;
    const activeTools = Object.values(tools).filter(Boolean).length;

    document.getElementById("kpiTools").textContent = `${activeTools}/${totalTools}`;
    document.getElementById("kpiToolsSub").textContent = activeTools === totalTools
      ? "Todas las herramientas listas"
      : "Algunas herramientas opcionales faltan";
    document.getElementById("kpiReports").textContent = data.reports_count ?? 0;
    document.getElementById("navReportsCount").textContent = data.reports_count ?? 0;
    const navCases = document.getElementById("navCasesCount");
    if (navCases) navCases.textContent = data.cases_count ?? 0;

    const caps = data.capabilities || {};
    document.getElementById("kpiBreachEngine").textContent = caps.hibp_api_key ? "HIBP + XON" : "XposedOrNot";
    document.getElementById("kpiHibpMeta").textContent = caps.hibp_api_key
      ? "HIBP_API_KEY activa"
      : "Gratis (agrega HIBP_API_KEY en .env para HIBP)";
    document.getElementById("kpiLibs").textContent =
      `${caps.phonenumbers ? "OK" : "—"} / ${caps.dnspython ? "OK" : "—"}`;

    // Pills de estado de herramientas
    const pills = document.getElementById("toolPills");
    pills.innerHTML = Object.entries(tools)
      .map(([name, ok]) =>
        `<span class="tool-pill ${ok ? "ok" : "off"}">
          <span class="dot ${ok ? "idle" : ""}" style="${ok ? "" : "background:var(--rose)"}"></span>
          ${esc(name)}: ${ok ? "disponible" : "no instalada"}
        </span>`
      )
      .join("");

    updateActiveJobsBadge(data.active_jobs || 0);
  } catch (err) {
    toast(`No se pudo cargar el estado: ${err.message}`, "err");
  }
}

export function updateActiveJobsBadge(count) {
  const pill = document.getElementById("activeJobsPill");
  const dot = pill.querySelector(".dot");
  const txt = document.getElementById("activeJobsText");
  if (count > 0) {
    dot.className = "dot busy";
    txt.textContent = `${count} tarea(s) en curso`;
  } else {
    dot.className = "dot idle";
    txt.textContent = "Sin tareas en curso";
  }
}

export async function loadReports() {
  try {
    const data = await api("/api/reports");
    state.reports = data.reports || [];
    document.getElementById("kpiReports").textContent = state.reports.length;
    document.getElementById("navReportsCount").textContent = state.reports.length;
    renderReportsList("recentReportsList", state.reports.slice(0, 5));
    renderReportsList("historyList", state.reports);
  } catch (err) {
    toast(`Error cargando historial: ${err.message}`, "err");
  }
}

export function renderReportsList(containerId, items) {
  const el = document.getElementById(containerId);
  if (!el) return;
  if (!items.length) {
    el.innerHTML = `<div class="muted">Todavía no hay reportes guardados en <code>output/</code>.</div>`;
    return;
  }
  el.innerHTML = items
    .map((r) => {
      const htmlFile = r.files.find((f) => f.name === "resumen.html");
      const pdfFile = r.files.find((f) => f.name === "reporte_final.pdf");
      const csvFiles = r.files.filter((f) => f.name.endsWith(".csv"));
      return `
        <div class="report-row">
          <div class="report-meta">
            <span class="badge badge-info">${esc(r.type)}</span>
            <strong>${esc(r.target)}</strong>
            <span class="muted">${esc(r.date)}</span>
            <span class="muted">(${r.files.length} archivos)</span>
          </div>
          <div class="report-actions">
            ${htmlFile ? `<a class="btn btn-sm btn-primary" href="${esc(htmlFile.url)}" target="_blank" rel="noopener">Ver HTML</a>` : ""}
            ${pdfFile ? `<a class="btn btn-sm btn-ghost" href="${esc(pdfFile.url)}" target="_blank" rel="noopener">PDF</a>` : ""}
            ${csvFiles.map((c) => `<a class="btn btn-sm btn-ghost" href="${esc(c.url)}" download>${esc(c.name)}</a>`).join("")}
          </div>
        </div>
      `;
    })
    .join("");
}

// ---------------------------------------------------------------------------
// Polling de jobs asíncronos + barra de progreso en vivo
// ---------------------------------------------------------------------------
export function startJobPolling(jobId, viewKey, onDone) {
  const progEl = document.getElementById(`progress-${viewKey}`);
  const resEl = document.getElementById(`results-${viewKey}`);
  resEl.innerHTML = "";

  if (state.activePollers[viewKey]) {
    clearInterval(state.activePollers[viewKey]);
  }

  const poll = async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      renderProgress(progEl, job);

      if (job.status === "done") {
        clearInterval(state.activePollers[viewKey]);
        delete state.activePollers[viewKey];
        toast(`Investigación completada: ${job.target}`, "ok");
        onDone(job, resEl);
        loadStatus();
        loadReports();
        if (job.case_id) {
          loadCases();
          if (state.selectedCaseId === job.case_id) {
            selectCase(job.case_id);
          }
        }
      } else if (job.status === "error") {
        clearInterval(state.activePollers[viewKey]);
        delete state.activePollers[viewKey];
        toast(`Error en el trabajo: ${job.error}`, "err");
        loadStatus();
      }
    } catch (err) {
      clearInterval(state.activePollers[viewKey]);
      delete state.activePollers[viewKey];
      toast(`Error consultando estado: ${err.message}`, "err");
    }
  };

  poll();
  state.activePollers[viewKey] = setInterval(poll, 1600);
}

export function renderProgress(container, job) {
  const badgeClass = job.status === "done" ? "badge-alta" : job.status === "error" ? "badge-baja" : "badge-info";
  const label = job.status === "done" ? "COMPLETADO" : job.status === "error" ? "ERROR" : "EN CURSO";
  container.innerHTML = `
    <div class="progress-card">
      <div class="progress-header">
        <div>
          <span class="badge ${badgeClass}">${label}</span>
          <strong style="margin-left:8px">${esc(job.step)}</strong>
        </div>
        <span class="muted">${esc(job.progress)}%</span>
      </div>
      <div class="progress-bar-bg">
        <div class="progress-bar-fill" style="width:${Number(job.progress) || 0}%"></div>
      </div>
      <div class="log-box">${esc((job.logs || []).join("\n"))}</div>
      ${job.files && job.files.length ? renderDownloadBar(job.files) : ""}
    </div>
  `;
}

export function renderDownloadBar(files) {
  const destacados = files.filter((f) =>
    ["resumen.html", "reporte_final.pdf", "resumen_consolidado.csv", "redes_sociales.csv", "busqueda_nombre.csv", "correos_encontrados.csv", "brechas_detectadas.csv", "telefono_servicios.csv", "metadatos.json"].includes(f.name)
  );
  if (!destacados.length) return "";
  return `
    <div class="download-bar">
      ${destacados
        .map((f) => {
          const isHtml = f.name.endsWith(".html");
          return `<a class="btn btn-sm ${isHtml ? "btn-primary" : "btn-ghost"}" href="${esc(f.url)}" ${isHtml ? 'target="_blank" rel="noopener"' : "download"}>${isHtml ? "Abrir " : "Descargar "}${esc(f.name)}</a>`;
        })
        .join("")}
    </div>
  `;
}

