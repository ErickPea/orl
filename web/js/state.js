// OSINT-Auto :: Estado global compartido, helpers de escape XSS, toast y cliente API.
/**
 * OSINT-Auto :: Lógica Frontend (SPA)
 * Conecta las 7 vistas con los endpoints REST de FastAPI (`web_app.py`),
 * gestiona polling de jobs en segundo plano, filtros interactivos y renderizado seguro.
 */

export const VIEW_META = {
  dashboard: ["Panel principal", "Estado de herramientas, accesos rápidos y reportes recientes"],
  cases:     ["Casos y Entidades (Fases 1–6)", "SQLite, resolución de entidades, conectores abiertos, galería visual, NLP, SNA y reporte integral"],
  sna:       ["Círculo Cercano · Laboratorio SNA (Fase 5)", "Vista secundaria interactiva: grafo orbital/físico, ranking de cercanía, comunidades y conversaciones"],
  agent:     ["Agente de Navegación Supervisada", "Sesión manual segura en Chromium visible, extracción en vivo con Pausa/Reanudación/Cancelación y revisión previa"],
  username:  ["Búsqueda por username", "Maigret + Sherlock con deduplicación, grafo y nivel de confianza"],
  social:    ["Redes sociales y huella", "Enfoque en redes sociales, metadatos públicos abiertos y grafo interactivo"],
  name:      ["Nombre y Ubicación", "Búsqueda legal por nombre real + ciudad/zona en APIs públicas y matriz por red social"],
  email:     ["Correo electrónico", "Validación DNS MX, Gravatar público, Holehe y brechas"],
  phone:     ["Análisis de teléfono", "Validación internacional (phonenumbers) y servicios Ignorant"],
  breach:    ["Brechas y auditoría", "Exposición en filtraciones públicas y k-Anonymity para contraseñas propias"],
  media:     ["Metadatos de Media", "Extracción EXIF, GPS, XMP, streams de video (ffprobe) y sanitización de imágenes"],
  full:      ["Búsqueda completa", "Pipeline automático de 5 pasos con grafo y reporte consolidado"],
  history:   ["Historial de reportes", "Carpetas generadas en output/ listas para abrir o descargar"],
};

export const state = {
  emailMode: "lookup",
  reports: [],
  cases: [],
  connectors: [],
  platformCaps: [],
  oauthProviders: {},
  visionStatus: null,
  nlpStatus: null,
  visionThemeFilter: "",
  relCatFilter: "",
  relEpiFilter: "",
  caseContentTab: "posts",
  agentRunId: "",
  agentPoller: null,
  sna: {
    caseId: "",
    data: null,
    mode: "graph",      // graph | ranking | communities
    layout: "orbital",  // orbital | force
    minScore: 0,
    communityFilter: "all",
    onlyReciprocal: false,
    selectedNodeId: "",
    rankingTier: "ALL",
    rankingQuery: "",
    rafId: null,
  },
  activeCaseId: localStorage.getItem("osint_active_case_id") || "",
  selectedCaseId: "",
  activePollers: {},
  profileCache: {}, // jobId -> { profiles, filter, query }
  graphCache: {},   // graphId -> { graph, onlyAlta }
};

export function getActiveCaseId() {
  return state.activeCaseId || null;
}

export function setActiveCaseId(caseId) {
  state.activeCaseId = caseId || "";
  if (state.activeCaseId) {
    localStorage.setItem("osint_active_case_id", state.activeCaseId);
  } else {
    localStorage.removeItem("osint_active_case_id");
  }
  const sel = document.getElementById("activeCaseSelect");
  if (sel && sel.value !== state.activeCaseId) {
    sel.value = state.activeCaseId;
  }
}

// ---------------------------------------------------------------------------
// Utilidades seguras (escape HTML contra XSS, API helper y notificaciones)
// ---------------------------------------------------------------------------
export function esc(val) {
  return String(val ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function toast(msg, type = "ok") {
  const box = document.getElementById("toastContainer");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

export function getApiToken() {
  if (typeof document === "undefined" || typeof document.querySelector !== "function") return "";
  return document.querySelector('meta[name="osint-api-token"]')?.getAttribute("content") || "";
}

export async function api(url, options = {}) {
  const tok = getApiToken();
  const baseHeaders = { "Content-Type": "application/json" };
  if (tok) baseHeaders["X-OSINT-Token"] = tok;
  const res = await fetch(url, {
    ...options,
    headers: { ...baseHeaders, ...(options.headers || {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `Error HTTP ${res.status}`);
  }
  return data;
}

export async function apiFormData(url, formData) {
  const tok = getApiToken();
  const headers = tok ? { "X-OSINT-Token": tok } : {};
  const res = await fetch(url, { method: "POST", headers, body: formData });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `Error HTTP ${res.status}`);
  }
  return data;
}

export function requireConsent() {
  const chk = document.getElementById("globalConsent");
  const bar = chk.closest(".consent-bar");
  if (!chk.checked) {
    bar.classList.add("warn-shake");
    setTimeout(() => bar.classList.remove("warn-shake"), 1200);
    toast("Marca primero la casilla de confirmación de uso legítimo.", "err");
    chk.focus();
    return false;
  }
  return true;
}

