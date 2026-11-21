// OSINT-Auto :: Renderizado de resultados OSINT (username, redes, email, teléfono, brechas, media).

import { state, esc, toast } from "./state.js";
import { switchView, renderDownloadBar } from "./core_ui.js";
// ---------------------------------------------------------------------------
// Grafo interactivo de huella digital (SVG nativo) + Evaluación de privacidad
// ---------------------------------------------------------------------------
export const NODE_COLORS = {
  target:  "#38bdf8",
  social:  "#a78bfa",
  profile: "#10b981",
  email:   "#60a5fa",
  phone:   "#2dd4bf",
  breach:  "#f43f5e",
  signal:  "#f59e0b",
};

export function renderPrivacyCard(privacy) {
  if (!privacy) return "";
  const color = privacy.level === "ALTO" ? "var(--rose)" : privacy.level === "MODERADO" ? "var(--amber)" : "var(--emerald)";
  const badgeCls = privacy.level === "ALTO" ? "badge-baja" : privacy.level === "MODERADO" ? "badge-media" : "badge-alta";
  return `
    <div class="panel-card">
      <div class="panel-head">
        <h2>Resumen de exposición y privacidad</h2>
        <span class="badge ${badgeCls}">EXPOSICIÓN ${esc(privacy.level)} (${Number(privacy.score)}/100)</span>
      </div>
      <div class="privacy-meter-bg">
        <div class="privacy-meter-fill" style="width:${Number(privacy.score)}%;background:${color}"></div>
      </div>
      <div class="two-col">
        <div>
          <strong>Qué se observa públicamente</strong>
          <ul>${(privacy.findings || []).map((f) => `<li>${esc(f)}</li>`).join("")}</ul>
        </div>
        <div>
          <strong>Acciones para reducir exposición</strong>
          <ul>${(privacy.actions || []).map((a) => `<li>${esc(a)}</li>`).join("")}</ul>
        </div>
      </div>
    </div>
  `;
}

export function renderGraphCard(graphId, graph) {
  if (!graph || !graph.nodes || graph.nodes.length <= 1) return "";
  state.graphCache[graphId] = { graph, onlyAlta: false };
  return `
    <div class="panel-card">
      <div class="panel-head">
        <h2>Grafo interactivo de huella digital</h2>
        <div class="segmented" data-graph-filter="${esc(graphId)}" style="margin:0">
          <button type="button" class="seg-btn active" data-only-alta="0">Todos los nodos</button>
          <button type="button" class="seg-btn" data-only-alta="1">Solo confianza ALTA</button>
        </div>
      </div>
      <div class="graph-layout">
        <div class="graph-canvas-wrap" id="graph-svg-wrap-${esc(graphId)}"></div>
        <div class="graph-inspector" id="graph-inspector-${esc(graphId)}">
          <div class="muted">Haz clic en cualquier nodo del grafo para inspeccionar detalles o abrir su enlace.</div>
        </div>
      </div>
      <div class="graph-legend mt-12">
        <span class="legend-item"><span class="legend-dot" style="background:${NODE_COLORS.target}"></span>Objetivo</span>
        <span class="legend-item"><span class="legend-dot" style="background:${NODE_COLORS.social}"></span>Red social</span>
        <span class="legend-item"><span class="legend-dot" style="background:${NODE_COLORS.profile}"></span>Otro perfil</span>
        <span class="legend-item"><span class="legend-dot" style="background:${NODE_COLORS.email}"></span>Correo</span>
        <span class="legend-item"><span class="legend-dot" style="background:${NODE_COLORS.phone}"></span>Teléfono</span>
        <span class="legend-item"><span class="legend-dot" style="background:${NODE_COLORS.breach}"></span>Brecha</span>
        <span class="legend-item"><span class="legend-dot" style="background:${NODE_COLORS.signal}"></span>Metadato</span>
      </div>
    </div>
  `;
}

export function drawFootprintGraph(graphId) {
  const entry = state.graphCache[graphId];
  const wrap = document.getElementById(`graph-svg-wrap-${graphId}`);
  const inspector = document.getElementById(`graph-inspector-${graphId}`);
  if (!entry || !wrap) return;

  const root = entry.graph.nodes.find((n) => n.id === "root");
  const children = entry.graph.nodes.filter((n) => {
    if (n.id === "root") return false;
    if (entry.onlyAlta && (n.type === "social" || n.type === "profile") && n.confidence !== "ALTA") {
      return false;
    }
    return true;
  });

  const W = 680;
  const H = 390;
  const cx = W / 2;
  const cy = H / 2;

  // Distribución radial en 1 o 2 anillos según cantidad de nodos para evitar superposición
  const coords = { root: { x: cx, y: cy, node: root } };
  const total = children.length;
  children.forEach((n, idx) => {
    const ring = total > 14 && idx % 2 === 1 ? 156 : 112;
    const angle = (2 * Math.PI * idx) / Math.max(1, total) - Math.PI / 2;
    coords[n.id] = {
      x: cx + ring * Math.cos(angle) * 1.45,
      y: cy + ring * Math.sin(angle),
      node: n,
    };
  });

  const edgesSvg = children
    .map((n) => {
      const p = coords[n.id];
      const stroke = n.type === "breach" ? "rgba(244,63,94,0.45)" : "rgba(148,163,184,0.28)";
      return `<line x1="${cx}" y1="${cy}" x2="${p.x.toFixed(1)}" y2="${p.y.toFixed(1)}" stroke="${stroke}" stroke-width="1.3" />`;
    })
    .join("");

  const nodesSvg = [root, ...children]
    .map((n) => {
      const p = coords[n.id];
      const r = n.id === "root" ? 24 : n.confidence === "ALTA" ? 13 : 10;
      const fill = NODE_COLORS[n.type] || "#94a3b8";
      const label = n.label.length > 16 ? `${n.label.slice(0, 15)}…` : n.label;
      return `
        <g class="graph-node" data-node-id="${esc(n.id)}" transform="translate(${p.x.toFixed(1)}, ${p.y.toFixed(1)})">
          <circle r="${r}" fill="${fill}" fill-opacity="0.88" stroke="#0f172a" stroke-width="2"></circle>
          <text y="${r + 13}" text-anchor="middle" fill="#e2e8f0" font-size="10.5" font-family="Inter, sans-serif">${esc(label)}</text>
        </g>
      `;
    })
    .join("");

  wrap.innerHTML = `<svg class="graph-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${edgesSvg}${nodesSvg}</svg>`;

  // Interacción al hacer clic sobre cualquier nodo
  wrap.querySelectorAll(".graph-node").forEach((g) => {
    g.addEventListener("click", () => {
      const id = g.dataset.nodeId;
      const item = coords[id]?.node;
      if (!item || !inspector) return;
      inspector.innerHTML = `
        <div><span class="badge badge-info">${esc(item.type.toUpperCase())}</span> ${item.confidence ? badgeForConf(item.confidence) : ""}</div>
        <strong style="font-size:15px;color:var(--text-strong)">${esc(item.label)}</strong>
        <div class="muted">${esc(item.detail || "")}</div>
        ${item.url ? `<a class="btn btn-sm btn-primary" href="${esc(item.url)}" target="_blank" rel="noopener">Abrir perfil</a>` : ""}
      `;
    });
  });
}

export function bindFootprintGraph(graphId) {
  if (!state.graphCache[graphId]) return;
  drawFootprintGraph(graphId);
  const seg = document.querySelector(`[data-graph-filter="${graphId}"]`);
  if (!seg) return;
  seg.querySelectorAll(".seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      seg.querySelectorAll(".seg-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.graphCache[graphId].onlyAlta = btn.dataset.onlyAlta === "1";
      drawFootprintGraph(graphId);
    });
  });
}

export function renderSocialCards(socialProfiles) {
  if (!socialProfiles || !socialProfiles.length) {
    return `<div class="panel-card"><div class="muted">No se detectaron redes sociales para este username.</div></div>`;
  }
  return `
    <div class="panel-card">
      <div class="panel-head">
        <h2>Redes sociales detectadas (${socialProfiles.length})</h2>
        <span class="muted">Metadatos públicos abiertos vs plataformas con muro de inicio de sesión</span>
      </div>
      <div class="social-grid">
        ${socialProfiles
          .map((p) => {
            const pm = p.public_meta || {};
            const st = pm.status || "skipped";
            const statusBadge =
              st === "public_ok"
                ? `<span class="badge badge-alta">METADATOS PÚBLICOS</span>`
                : st === "login_wall"
                  ? `<span class="badge badge-media">MURO DE LOGIN · REVISIÓN MANUAL</span>`
                  : `<span class="badge badge-info">${esc(st.toUpperCase())}</span>`;
            const avatar = pm.avatar
              ? `<img class="social-avatar" src="${esc(pm.avatar)}" alt="${esc(p.sitio)}" referrerpolicy="no-referrer" />`
              : `<div class="social-avatar-fallback">${esc((p.sitio || "?").slice(0, 2).toUpperCase())}</div>`;
            const detEntries = Object.entries(pm.details || {});
            return `
              <div class="social-card">
                <div class="social-card-head">
                  <div class="social-id">
                    ${avatar}
                    <div>
                      <strong>${esc(p.sitio)}</strong>
                      <div class="muted" style="font-size:11.5px">${esc(p.fuentes || "maigret")}</div>
                    </div>
                  </div>
                  ${badgeForConf(p.confianza)}
                </div>
                <div>${statusBadge}</div>
                <div class="social-details">
                  ${
                    detEntries.length
                      ? detEntries.map(([k, v]) => `<div><strong>${esc(k)}:</strong> ${esc(v)}</div>`).join("")
                      : `<div class="muted">${esc(pm.note || "Sin metadatos públicos adicionales.")}</div>`
                  }
                </div>
                <div class="report-actions" style="margin-top:auto">
                  <a class="btn btn-sm btn-ghost" href="${esc(p.url)}" target="_blank" rel="noopener">Abrir perfil</a>
                </div>
              </div>
            `;
          })
          .join("")}
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Renderizadores de resultados estructurados
// ---------------------------------------------------------------------------
export function badgeForConf(conf) {
  const map = { ALTA: "badge-alta", MEDIA: "badge-media", BAJA: "badge-baja" };
  return `<span class="badge ${map[conf] || "badge-info"}">${esc(conf)}</span>`;
}

export function renderProfilesSection(jobId, summary, profiles, signals) {
  state.profileCache[jobId] = { profiles: profiles || [], filter: "ALL", query: "" };

  const signalsHtml = signals && Object.keys(signals).length
    ? `<div class="panel-card">
         <div class="panel-head"><h2>Señales de correlación de identidad</h2><span class="muted">Extraídas de metadatos de Maigret</span></div>
         <div class="kv-grid">
           ${Object.entries(signals)
             .map(([k, vals]) => `<div class="kv-item"><div class="k">${esc(k)}</div><div class="v">${vals.map(esc).join(" · ")}</div></div>`)
             .join("")}
         </div>
       </div>`
    : "";

  return `
    <div class="kpi-grid">
      <div class="kpi-card"><div class="kpi-label">Perfiles únicos</div><div class="kpi-value">${summary.total}</div><div class="kpi-meta">Deduplicados</div></div>
      <div class="kpi-card"><div class="kpi-label">Confianza ALTA</div><div class="kpi-value" style="color:var(--emerald)">${summary.alta}</div><div class="kpi-meta">Cruce o metadatos</div></div>
      <div class="kpi-card"><div class="kpi-label">Confianza MEDIA</div><div class="kpi-value" style="color:var(--amber)">${summary.media}</div><div class="kpi-meta">1 herramienta, HTTP OK</div></div>
      <div class="kpi-card"><div class="kpi-label">Confianza BAJA</div><div class="kpi-value" style="color:var(--rose)">${summary.baja}</div><div class="kpi-meta">Revisar manualmente</div></div>
    </div>
    ${signalsHtml}
    <div class="panel-card">
      <div class="filter-bar">
        <div class="segmented" data-profile-filter="${esc(jobId)}">
          <button type="button" class="seg-btn active" data-level="ALL">Todos (${summary.total})</button>
          <button type="button" class="seg-btn" data-level="ALTA">ALTA (${summary.alta})</button>
          <button type="button" class="seg-btn" data-level="MEDIA">MEDIA (${summary.media})</button>
          <button type="button" class="seg-btn" data-level="BAJA">BAJA (${summary.baja})</button>
        </div>
        <input type="search" class="search-input" data-profile-search="${esc(jobId)}" placeholder="Filtrar sitio, URL o fuente..." />
      </div>
      <div class="table-wrap" id="profiles-table-${esc(jobId)}">
        ${buildProfilesTable(profiles)}
      </div>
    </div>
  `;
}

export function buildProfilesTable(profiles) {
  if (!profiles || !profiles.length) {
    return `<div class="muted">No se encontraron perfiles con los filtros actuales.</div>`;
  }
  return `
    <table>
      <thead>
        <tr><th>Confianza</th><th>Sitio</th><th>URL</th><th>Fuentes</th><th>HTTP</th><th>Metadatos</th></tr>
      </thead>
      <tbody>
        ${profiles
          .map(
            (p) => `
          <tr>
            <td>${badgeForConf(p.confianza)}</td>
            <td><strong>${esc(p.sitio)}</strong></td>
            <td><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.url)}</a></td>
            <td><span class="tag-chip">${esc(p.fuentes)}</span></td>
            <td><code>${esc(p.http_status || "—")}</code></td>
            <td class="muted">${esc(p.metadata_resumen || "—")}</td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>
  `;
}

export function bindProfileFilters(jobId) {
  const seg = document.querySelector(`[data-profile-filter="${jobId}"]`);
  const inp = document.querySelector(`[data-profile-search="${jobId}"]`);
  const redraw = () => {
    const entry = state.profileCache[jobId];
    if (!entry) return;
    const q = entry.query.toLowerCase();
    const filtered = entry.profiles.filter((p) => {
      if (entry.filter !== "ALL" && p.confianza !== entry.filter) return false;
      if (q && !`${p.sitio} ${p.url} ${p.fuentes} ${p.metadata_resumen}`.toLowerCase().includes(q)) return false;
      return true;
    });
    const wrap = document.getElementById(`profiles-table-${jobId}`);
    if (wrap) wrap.innerHTML = buildProfilesTable(filtered);
  };
  if (seg) {
    seg.querySelectorAll(".seg-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        seg.querySelectorAll(".seg-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.profileCache[jobId].filter = btn.dataset.level;
        redraw();
      });
    });
  }
  if (inp) {
    inp.addEventListener("input", () => {
      state.profileCache[jobId].query = inp.value.trim();
      redraw();
    });
  }
}

export function renderBreachBlock(breachRes) {
  if (!breachRes) return "";
  const exposed = breachRes.breached;
  const alertCls = exposed ? "alert-danger" : "alert-safe";
  const headline = exposed
    ? `EXPUESTO en ${breachRes.breach_count} brecha(s) pública(s)`
    : "Sin brechas públicas detectadas en las fuentes consultadas";

  const breachesHtml = (breachRes.breaches || []).length
    ? `<table>
         <thead><tr><th>Brecha</th><th>Fecha</th><th>Fuentes</th><th>Tipos de datos expuestos</th></tr></thead>
         <tbody>
           ${breachRes.breaches
             .map(
               (b) => `
             <tr>
               <td><strong>${esc(b.name)}</strong>${b.domain ? `<div class="muted">${esc(b.domain)}</div>` : ""}</td>
               <td><code>${esc(b.date || "—")}</code></td>
               <td><span class="tag-chip">${esc((b.sources || []).join("+"))}</span></td>
               <td>${(b.data_classes || []).map((c) => `<span class="tag-chip">${esc(c)}</span>`).join(" ") || "—"}</td>
             </tr>`
             )
             .join("")}
         </tbody>
       </table>`
    : `<div class="muted">No hay brechas que mostrar.</div>`;

  return `
    <div class="panel-card">
      <div class="panel-head">
        <h2>Exposición en brechas de datos</h2>
        <span class="muted">Fuentes: ${esc((breachRes.sources_checked || []).join(", ") || "sin respuesta")}</span>
      </div>
      <div class="alert-box ${alertCls}" style="margin-bottom:14px">
        <strong>${esc(headline)}</strong>
        · Contraseñas/hashes en brechas: <strong>${breachRes.passwords_exposed ? "SÍ" : "No indicado"}</strong>
        · Teléfono expuesto: <strong>${breachRes.phone_exposed ? "SÍ" : "No indicado"}</strong>
      </div>
      ${breachRes.errors && breachRes.errors.length ? `<div class="alert-box alert-warn" style="margin-bottom:14px">${esc(breachRes.errors.join(" | "))}</div>` : ""}
      <div class="table-wrap">${breachesHtml}</div>
      ${
        (breachRes.recommendations || []).length
          ? `<div class="mt-12"><strong>Recomendaciones defensivas:</strong><ul>${breachRes.recommendations.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>`
          : ""
      }
    </div>
  `;
}

export function renderEmailResult(job, container) {
  const r = job.result || {};
  if (r.kind === "email_generate") {
    const entries = Object.entries(r.found || {});
    container.innerHTML = `
      <div class="panel-card">
        <div class="panel-head"><h2>Correos confirmados para "${esc(r.username)}"</h2><span class="muted">${entries.length} encontrados</span></div>
        ${
          entries.length
            ? `<div class="list-stack">${entries
                .map(([mail, svcs]) => `<div class="report-row"><strong>${esc(mail)}</strong><div class="tags-wrap">${svcs.map((s) => `<span class="tag-chip">${esc(s)}</span>`).join("")}</div></div>`)
                .join("")}</div>`
            : `<div class="muted">No se detectaron correos registrados entre los candidatos probados.</div>`
        }
      </div>
    `;
    return;
  }

  const mx = r.mx || {};
  const grav = r.gravatar || {};
  const services = r.services || [];
  const rateLimited = r.rate_limited || [];
  const derived = r.profiles_found || {};

  container.innerHTML = `
    <div class="two-col">
      <div class="panel-card">
        <div class="panel-head"><h2>Validación DNS / MX</h2>${mx.has_mx ? '<span class="badge badge-alta">ACTIVO</span>' : '<span class="badge badge-media">REVISAR</span>'}</div>
        <div class="kv-grid" style="grid-template-columns:1fr 1fr">
          <div class="kv-item"><div class="k">Dominio</div><div class="v">${esc(mx.domain || "—")}</div></div>
          <div class="kv-item"><div class="k">Estado</div><div class="v">${esc(mx.note || "—")}</div></div>
          <div class="kv-item" style="grid-column:span 2"><div class="k">Servidores MX</div><div class="v"><code>${esc((mx.mx_records || []).join(", ") || "—")}</code></div></div>
        </div>
      </div>

      <div class="panel-card">
        <div class="panel-head"><h2>Gravatar público</h2>${grav.found ? '<span class="badge badge-alta">ENCONTRADO</span>' : '<span class="badge badge-info">SIN PERFIL</span>'}</div>
        ${
          grav.found
            ? `<div class="kv-grid" style="grid-template-columns:1fr 1fr">
                 <div class="kv-item"><div class="k">Nombre visible</div><div class="v">${esc(grav.display_name || "—")}</div></div>
                 <div class="kv-item"><div class="k">Username</div><div class="v">${esc(grav.preferred_username || "—")}</div></div>
                 <div class="kv-item"><div class="k">Ubicación</div><div class="v">${esc(grav.location || "—")}</div></div>
                 <div class="kv-item"><div class="k">Perfil</div><div class="v">${grav.profile_url ? `<a href="${esc(grav.profile_url)}" target="_blank" rel="noopener">Abrir perfil</a>` : "—"}</div></div>
               </div>`
            : `<div class="muted">No se encontró un perfil público asociado en Gravatar.</div>`
        }
      </div>
    </div>

    <div class="panel-card">
      <div class="panel-head">
        <h2>Servicios detectados (Holehe)</h2>
        <span class="muted">${services.length} confirmados · ${rateLimited.length} con rate-limit</span>
      </div>
      <div class="tags-wrap">
        ${services.length ? services.map((s) => `<span class="badge badge-alta">${esc(s)}</span>`).join("") : `<span class="muted">Sin registros confirmados.</span>`}
      </div>
      ${
        rateLimited.length
          ? `<div class="mt-12"><div class="muted" style="margin-bottom:6px">Servicios con rate-limit (no concluyentes):</div><div class="tags-wrap">${rateLimited.slice(0, 30).map((s) => `<span class="badge badge-media">${esc(s)}</span>`).join("")}</div></div>`
          : ""
      }
    </div>

    ${renderBreachBlock(r.breaches)}

    ${
      Object.keys(derived).length
        ? `<div class="panel-card">
             <div class="panel-head"><h2>Perfiles de usernames derivados</h2></div>
             ${Object.entries(derived)
               .map(([u, list]) => `<div class="mt-12"><strong>${esc(u)}</strong><ul>${list.map((i) => `<li>${esc(i)}</li>`).join("")}</ul></div>`)
               .join("")}
           </div>`
        : ""
    }
  `;
}

export function renderPhoneResult(job, container) {
  const r = job.result || {};
  const an = r.analysis || {};
  const voip = r.voip_assessment || {};
  const variants = r.variants || [];
  const dorks = r.dorks || [];
  const direct = r.direct_links || {};

  const voipBadge = voip.is_voip
    ? '<span class="badge badge-media">LÍNEA VoIP / VIRTUAL</span>'
    : "";

  const variantsHtml = variants.length
    ? `<div class="panel-card">
         <div class="panel-head">
           <h2>Variantes de formato y verificación directa</h2>
           <span class="muted">Útiles para búsquedas manuales exactas</span>
         </div>
         <div class="chip-row" style="margin-bottom:10px">
           ${variants.map((v) => `<span class="tag-chip"><code>${esc(v)}</code></span>`).join("")}
         </div>
         <div style="display:flex;gap:8px;flex-wrap:wrap">
           ${direct.whatsapp ? `<a class="btn btn-xs btn-secondary" href="${esc(direct.whatsapp)}" target="_blank" rel="noopener">Verificar en WhatsApp (wa.me)</a>` : ""}
           ${direct.telegram ? `<a class="btn btn-xs btn-secondary" href="${esc(direct.telegram)}" target="_blank" rel="noopener">Verificar en Telegram (t.me)</a>` : ""}
         </div>
       </div>`
    : "";

  const dorksHtml = dorks.length
    ? `<div class="panel-card">
         <div class="panel-head">
           <h2>Consultas OSINT sugeridas por variantes (1 clic)</h2>
           <span class="muted">Google · DuckDuckGo · Bing</span>
         </div>
         <div style="display:flex;flex-direction:column;gap:8px">
           ${dorks.map((d) => `
             <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border);border-radius:8px;flex-wrap:wrap">
               <div>
                 <div style="font-weight:600;font-size:13px">${esc(d.label)}</div>
                 <code style="font-size:11px;color:var(--text-muted)">${esc(d.query)}</code>
               </div>
               <div style="display:flex;gap:6px">
                 <a class="btn btn-xs btn-primary" href="${esc(d.google)}" target="_blank" rel="noopener">Google</a>
                 <a class="btn btn-xs btn-secondary" href="${esc(d.duckduckgo)}" target="_blank" rel="noopener">DuckDuckGo</a>
                 <a class="btn btn-xs btn-ghost" href="${esc(d.bing)}" target="_blank" rel="noopener">Bing</a>
               </div>
             </div>
           `).join("")}
         </div>
       </div>`
    : "";

  container.innerHTML = `
    <div class="panel-card">
      <div class="panel-head">
        <h2>Análisis técnico del teléfono</h2>
        <div style="display:flex;gap:6px;align-items:center">
          ${voipBadge}
          ${an.valid ? '<span class="badge badge-alta">NÚMERO VÁLIDO</span>' : '<span class="badge badge-baja">NO VÁLIDO / REVISAR</span>'}
        </div>
      </div>
      <div class="kv-grid">
        <div class="kv-item"><div class="k">Formato E.164</div><div class="v"><code>${esc(an.e164 || r.phone)}</code></div></div>
        <div class="kv-item"><div class="k">País / Región</div><div class="v">${esc(an.location || an.region_code || "Desconocido")}</div></div>
        <div class="kv-item"><div class="k">Operador (Carrier)</div><div class="v">${esc(an.carrier || "No determinado")}</div></div>
        <div class="kv-item"><div class="k">Tipo de línea</div><div class="v">${esc(an.line_type || "Desconocido")}</div></div>
        <div class="kv-item"><div class="k">Código país / Nacional</div><div class="v"><code>+${esc(an.country_code || "?")} ${esc(an.national_number || "")}</code></div></div>
        <div class="kv-item"><div class="k">Zonas horarias</div><div class="v">${esc((an.timezones || []).join(", ") || "—")}</div></div>
      </div>
      ${voip.note ? `<div class="muted" style="margin-top:10px;font-size:12px">${esc(voip.note)}</div>` : ""}
    </div>

    ${variantsHtml}
    ${dorksHtml}

    <div class="panel-card">
      <div class="panel-head"><h2>Servicios consultados (Ignorant)</h2></div>
      <div class="kv-grid">
        <div class="kv-item">
          <div class="k">Registrados [+]</div>
          <div class="v tags-wrap mt-12">${(r.registered || []).length ? r.registered.map((s) => `<span class="badge badge-alta">${esc(s)}</span>`).join("") : '<span class="muted">Ninguno</span>'}</div>
        </div>
        <div class="kv-item">
          <div class="k">No usados [-]</div>
          <div class="v tags-wrap mt-12">${(r.not_used || []).length ? r.not_used.map((s) => `<span class="tag-chip">${esc(s)}</span>`).join("") : '<span class="muted">—</span>'}</div>
        </div>
        <div class="kv-item">
          <div class="k">Rate-limit [x]</div>
          <div class="v tags-wrap mt-12">${(r.rate_limited || []).length ? r.rate_limited.map((s) => `<span class="badge badge-media">${esc(s)}</span>`).join("") : '<span class="muted">Ninguno</span>'}</div>
        </div>
      </div>
    </div>
  `;
}

export function renderNameLocationResult(job, container) {
  const r = job.result || {};
  const s = r.summary || {};
  const candidates = r.candidates || [];
  const dorks = r.dorks || [];
  const suggested = r.suggested_usernames || [];
  const gid = `g-${job.id}`;

  const kpis = `
    <div class="kpi-grid">
      <div class="kpi-card"><div class="kpi-label">Candidatos en APIs</div><div class="kpi-value">${s.total || 0}</div><div class="kpi-meta">${esc((r.sources_used || []).length)} fuentes consultadas</div></div>
      <div class="kpi-card"><div class="kpi-label">Coincidencia ALTA</div><div class="kpi-value" style="color:var(--emerald)">${s.alta || 0}</div><div class="kpi-meta">Nombre + zona</div></div>
      <div class="kpi-card"><div class="kpi-label">Coincidencia MEDIA</div><div class="kpi-value" style="color:var(--amber)">${s.media || 0}</div><div class="kpi-meta">Nombre sin zona explícita</div></div>
      <div class="kpi-card"><div class="kpi-label">Consultas por red</div><div class="kpi-value" style="color:var(--accent)">${s.dorks_count || 0}</div><div class="kpi-meta">Google · DuckDuckGo · Bing</div></div>
    </div>
  `;

  const apiTip = !r.search_apis_configured
    ? `<div class="alert-box alert-warn" style="margin-bottom:16px">
         <strong>Tip para resultados automáticos de LinkedIn, Facebook, Instagram, X y TikTok:</strong>
         agrega <code>BRAVE_API_KEY</code> o <code>GOOGLE_CSE_KEY</code> + <code>GOOGLE_CSE_CX</code> en tu archivo <code>.env</code>.
         Mientras tanto, usa la <strong>Matriz de búsqueda directa por red social</strong> de abajo (1 clic por red) y los candidatos obtenidos en GitHub/ORCID/OpenAlex/Wikipedia.
       </div>`
    : "";

  const pivotHtml = suggested.length
    ? `<div class="panel-card">
         <div class="panel-head">
           <h2>Pivotar a búsqueda por Username / Redes Sociales</h2>
           <span class="muted">Haz clic en un username sugerido para llevarlo directo a Redes Sociales</span>
         </div>
         <div class="chip-row" style="margin-bottom:0">
           ${suggested.map((u) => `<button type="button" class="chip" data-pivot-social="${esc(u)}">@${esc(u)} → Rastrear</button>`).join("")}
         </div>
       </div>`
    : "";

  const candTable = candidates.length
    ? `<table>
         <thead>
           <tr><th>Coincidencia</th><th>Nombre encontrado</th><th>Red / Fuente</th><th>Ubicación / Afiliación</th><th>Detalle</th><th>Enlace</th></tr>
         </thead>
         <tbody>
           ${candidates
             .map(
               (c) => `
             <tr>
               <td>${badgeForConf(c.match || "MEDIA")}</td>
               <td><strong>${esc(c.person_name)}</strong>${c.username ? `<div class="muted">@${esc(c.username)}</div>` : ""}</td>
               <td><span class="badge badge-info">${esc(c.network)}</span><div class="muted" style="font-size:11px;margin-top:4px">${esc(c.source)}</div></td>
               <td>${esc(c.location || "—")}</td>
               <td class="muted">${esc(c.snippet || "—")}</td>
               <td>${c.url ? `<a class="btn btn-sm btn-ghost" href="${esc(c.url)}" target="_blank" rel="noopener">Abrir</a>` : "—"}</td>
             </tr>`
             )
             .join("")}
         </tbody>
       </table>`
    : `<div class="muted">No hubo coincidencias automáticas en GitHub/ORCID/OpenAlex/Wikipedia para ese nombre y zona. Usa la matriz por red social de abajo.</div>`;

  const dorksHtml = `
    <div class="panel-card">
      <div class="panel-head">
        <h2>Matriz de búsqueda legal por Red Social (${dorks.length})</h2>
        <span class="muted">Consultas exactas con nombre + ubicación listas para abrir</span>
      </div>
      <div class="list-stack">
        ${dorks
          .map(
            (d) => `
          <div class="report-row">
            <div>
              <span class="badge badge-info">${esc(d.network)}</span>
              <code style="margin-left:8px;font-size:12px;color:var(--text)">${esc(d.query)}</code>
            </div>
            <div class="report-actions">
              <a class="btn btn-sm btn-primary" href="${esc(d.google_url)}" target="_blank" rel="noopener">Google</a>
              <a class="btn btn-sm btn-ghost" href="${esc(d.duckduckgo_url)}" target="_blank" rel="noopener">DuckDuckGo</a>
              <a class="btn btn-sm btn-ghost" href="${esc(d.bing_url)}" target="_blank" rel="noopener">Bing</a>
            </div>
          </div>`
          )
          .join("")}
      </div>
    </div>
  `;

  container.innerHTML = `
    ${kpis}
    <div class="alert-box alert-warn" style="margin-bottom:16px"><strong>Control de homónimos:</strong> ${esc(r.homonym_notice || "")}</div>
    ${apiTip}
    ${renderGraphCard(gid, r.graph)}
    <div class="panel-card">
      <div class="panel-head">
        <h2>Personas / perfiles encontrados automáticamente (${candidates.length})</h2>
        <span class="muted">Fuentes: ${esc((r.sources_used || []).join(", "))}</span>
      </div>
      <div class="table-wrap">${candTable}</div>
    </div>
    ${pivotHtml}
    ${dorksHtml}
  `;

  bindFootprintGraph(gid);
  container.querySelectorAll("[data-pivot-social]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const u = btn.dataset.pivotSocial;
      document.getElementById("sInput").value = u;
      switchView("social");
      toast(`Username "@${u}" cargado en Redes Sociales.`);
    });
  });
}

export function renderMediaResult(payload, container) {
  const r = payload.result || {};
  const priv = r.privacy || {};
  const gps = r.gps || {};
  const metaEntries = Object.entries(r.metadata || {}).sort((a, b) => a[0].localeCompare(b[0]));

  const privCls = priv.level === "ALTO" ? "alert-danger" : priv.level === "MODERADO" ? "alert-warn" : "alert-safe";
  const privBadge = priv.level === "ALTO" ? "badge-baja" : priv.level === "MODERADO" ? "badge-media" : "badge-alta";

  const gpsHtml = gps.latitude !== undefined
    ? `
      <div class="gps-card">
        <div class="panel-head" style="margin-bottom:10px">
          <h2>Ubicación GPS detectada en el archivo</h2>
          <span class="badge badge-baja">COORDENADAS EXACTAS</span>
        </div>
        <div class="kv-grid">
          <div class="kv-item"><div class="k">Latitud / Longitud</div><div class="v"><code>${esc(gps.latitude)}, ${esc(gps.longitude)}</code></div></div>
          <div class="kv-item"><div class="k">Altitud</div><div class="v">${gps.altitude_m !== undefined ? `${esc(gps.altitude_m)} m` : "No indicada"}</div></div>
          <div class="kv-item">
            <div class="k">Ver en mapa</div>
            <div class="v report-actions" style="margin-top:6px">
              <a class="btn btn-sm btn-primary" href="${esc(gps.openstreetmap_url)}" target="_blank" rel="noopener">OpenStreetMap</a>
              <a class="btn btn-sm btn-ghost" href="${esc(gps.google_maps_url)}" target="_blank" rel="noopener">Google Maps</a>
            </div>
          </div>
        </div>
      </div>
    `
    : "";

  const techItems = [
    ["Archivo", r.filename],
    ["Tipo / MIME", `${(r.media_type || "—").toUpperCase()} (${r.mime_type || "—"})`],
    ["Tamaño", r.size_human || "—"],
    ["Motores", (r.engines || []).join(", ") || "—"],
    ...Object.entries(r.technical || {}),
    ["SHA-256", (r.hashes || {}).sha256 || "—"],
    ["MD5", (r.hashes || {}).md5 || "—"],
  ];

  const cleanBtn = payload.clean_copy_url
    ? `<a class="btn btn-sm btn-secondary" href="${esc(payload.clean_copy_url)}" download>Descargar copia limpia (sin EXIF/GPS)</a>`
    : "";

  container.innerHTML = `
    ${gpsHtml}
    <div class="panel-card">
      <div class="panel-head">
        <h2>Privacidad y reportes</h2>
        <span class="badge ${privBadge}">RIESGO EN METADATOS: ${esc(priv.level || "BAJO")}</span>
      </div>
      <div class="alert-box ${privCls}" style="margin-bottom:12px">
        <ul style="margin:0;padding-left:18px">${(priv.findings || []).map((f) => `<li>${esc(f)}</li>`).join("")}</ul>
      </div>
      ${(priv.recommendations || []).length ? `<div class="muted" style="margin-bottom:10px"><strong>Recomendación:</strong> ${esc(priv.recommendations.join(" "))}</div>` : ""}
      <div class="download-bar">
        ${cleanBtn}
        ${renderDownloadBar(payload.files || [])}
      </div>
    </div>

    <div class="panel-card">
      <div class="panel-head"><h2>Información técnica y hashes</h2></div>
      <div class="kv-grid">
        ${techItems.map(([k, v]) => `<div class="kv-item"><div class="k">${esc(k)}</div><div class="v"><code>${esc(v)}</code></div></div>`).join("")}
      </div>
    </div>

    <div class="panel-card">
      <div class="panel-head">
        <h2>Metadatos extraídos (${metaEntries.length})</h2>
        <input type="search" id="mediaMetaFilter" class="search-input" placeholder="Filtrar etiqueta o valor (ej: GPS, Model, Date)..." />
      </div>
      <div class="table-wrap" id="mediaMetaTableWrap"></div>
    </div>
  `;

  const renderTable = (q = "") => {
    const ql = q.toLowerCase();
    const rows = metaEntries.filter(([k, v]) => !ql || `${k} ${v}`.toLowerCase().includes(ql));
    const wrap = document.getElementById("mediaMetaTableWrap");
    if (!rows.length) {
      wrap.innerHTML = `<div class="muted">Sin metadatos embebidos para mostrar.</div>`;
      return;
    }
    wrap.innerHTML = `
      <table>
        <thead><tr><th>Etiqueta</th><th>Valor</th></tr></thead>
        <tbody>
          ${rows.map(([k, v]) => `<tr><td><strong>${esc(k)}</strong></td><td><code>${esc(v)}</code></td></tr>`).join("")}
        </tbody>
      </table>
    `;
  };
  renderTable();
  const filterInp = document.getElementById("mediaMetaFilter");
  if (filterInp) filterInp.addEventListener("input", (ev) => renderTable(ev.target.value.trim()));
}

