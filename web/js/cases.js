// OSINT-Auto :: Gestión de casos, contenido, galería/visión, mapa geo-hints, NLP, relacional y reportes.

import { state, getActiveCaseId, setActiveCaseId, esc, toast, api, apiFormData, requireConsent } from "./state.js";
import { loadStatus, loadReports, startJobPolling } from "./core_ui.js";
import { renderCaseSnaPreview } from "./sna.js";
// ---------------------------------------------------------------------------
// Fase 1 :: Casos, Entidades y Resolución de Entidades (SQLite)
// ---------------------------------------------------------------------------
/** Obtiene la lista de casos desde `/api/cases` y sincroniza el selector global. */
export async function loadCases() {
  try {
    const data = await api("/api/cases");
    state.cases = data.cases || [];
    const navCases = document.getElementById("navCasesCount");
    const countLbl = document.getElementById("casesCountLabel");
    if (navCases) navCases.textContent = state.cases.length;
    if (countLbl) countLbl.textContent = `${state.cases.length} caso(s)`;

    // Si el caso activo ya no existe, limpiar selección
    if (state.activeCaseId && !state.cases.some((c) => c.id === state.activeCaseId)) {
      setActiveCaseId("");
    }
    syncActiveCaseSelect();
    renderCasesList();

    if (state.selectedCaseId && !state.cases.some((c) => c.id === state.selectedCaseId)) {
      state.selectedCaseId = "";
    }
    if (!state.selectedCaseId && state.activeCaseId) {
      selectCase(state.activeCaseId);
    } else if (!state.selectedCaseId && state.cases.length) {
      selectCase(state.cases[0].id);
    }
  } catch (err) {
    toast(`Error cargando casos: ${err.message}`, "err");
  }
}

/** Sincroniza el selector superior del caso activo y los selectores secundarios SNA/Agente. */
export function syncActiveCaseSelect() {
  const sel = document.getElementById("activeCaseSelect");
  if (!sel) return;
  const opts = [
    `<option value="">Ninguno (solo output/)</option>`,
    ...state.cases.map(
      (c) => `<option value="${esc(c.id)}">${esc(c.title)} (${c.profiles_count || 0} perfiles)</option>`
    ),
  ];
  sel.innerHTML = opts.join("");
  sel.value = state.activeCaseId || "";

  const snaSel = document.getElementById("snaCaseSelect");
  if (snaSel) {
    snaSel.innerHTML = [
      `<option value="">Selecciona un caso...</option>`,
      ...state.cases.map(
        (c) => `<option value="${esc(c.id)}">${esc(c.title)} (${c.interactions_count || 0} interac. · ${c.comments_count || 0} coment.)</option>`
      ),
    ].join("");
    if (state.sna.caseId && state.cases.some((c) => c.id === state.sna.caseId)) {
      snaSel.value = state.sna.caseId;
    }
  }

  const agSel = document.getElementById("agCaseId");
  if (agSel) {
    agSel.innerHTML = state.cases.length
      ? state.cases.map((c) => `<option value="${esc(c.id)}">${esc(c.title)} (${esc(c.subject_name || c.id)})</option>`).join("")
      : `<option value="">Crea un caso primero en Casos y Entidades</option>`;
    if (state.activeCaseId && state.cases.some((c) => c.id === state.activeCaseId)) {
      agSel.value = state.activeCaseId;
    }
  }
}

/** Renderiza el listado lateral de casos con acciones de selección, activación y borrado. */
export function renderCasesList() {
  const listEl = document.getElementById("casesList");
  if (!listEl) return;
  if (!state.cases.length) {
    listEl.innerHTML = `<div class="muted">Aún no hay casos guardados. Crea el primero arriba.</div>`;
    return;
  }
  listEl.innerHTML = state.cases
    .map((c) => {
      const isSelected = c.id === state.selectedCaseId;
      const isActive = c.id === state.activeCaseId;
      const statusBadge = c.status === "closed"
        ? `<span class="badge badge-baja">CERRADO</span>`
        : isActive
        ? `<span class="badge badge-verificado">ACTIVO</span>`
        : `<span class="badge badge-info">ABIERTO</span>`;
      return `
        <div class="case-item ${isSelected ? "selected" : ""}" data-case-id="${esc(c.id)}">
          <div class="case-item-head">
            <span class="case-item-title">${esc(c.title)}</span>
            ${statusBadge}
          </div>
          <div class="muted" style="font-size:12px">${esc(c.subject_name || "Sin alias")} · ${esc(c.purpose || "")}</div>
          <div class="case-item-meta">
            <span>${c.profiles_count || 0} perfiles</span>
            <span>${c.verified_count || 0} verif.</span>
            <span>${c.identifiers_count || 0} semillas</span>
            <span>${c.runs_count || 0} ejec.</span>
          </div>
        </div>
      `;
    })
    .join("");

  listEl.querySelectorAll(".case-item").forEach((el) => {
    el.addEventListener("click", () => selectCase(el.dataset.caseId));
  });
}

/** Carga el catálogo de conectores oficiales disponibles desde `/api/connectors`. */
export async function loadConnectors() {
  try {
    const data = await api("/api/connectors");
    state.connectors = data.connectors || [];
  } catch (err) {
    // Silencioso si no hay conectores cargados aún
  }
}

/** Carga la matriz de capacidades por plataforma y conexiones OAuth2 activas. */
export async function loadPlatformCapabilities() {
  try {
    const data = await api("/api/platforms/capabilities");
    state.platformCaps = data.platforms || [];
    state.oauthProviders = data.oauth_providers || {};
  } catch (err) {
    state.platformCaps = [];
  }
}

/** Consulta disponibilidad de motores de visión (Pillow/dHash, Ollama, Gemini, OpenAI, Tesseract). */
export async function loadVisionStatus() {
  try {
    state.visionStatus = await api("/api/vision/status");
  } catch (err) {
    state.visionStatus = null;
  }
}

/** Consulta disponibilidad de motores NLP locales y LLM opcionales. */
export async function loadNlpStatus() {
  try {
    state.nlpStatus = await api("/api/nlp/status");
  } catch (err) {
    state.nlpStatus = null;
  }
}

/** Selecciona un caso y carga en paralelo detalle, contenido, visión, NLP, relacional y evidencias. */
export async function selectCase(caseId) {
  if (!caseId) return;
  state.selectedCaseId = caseId;
  state.visionThemeFilter = "";
  renderCasesList();
  const pane = document.getElementById("caseDetailPane");
  if (pane) pane.innerHTML = `<div class="panel-card"><div class="muted">Cargando caso...</div></div>`;
  try {
    const [detail, content, vision, nlp, relational, evidences] = await Promise.all([
      api(`/api/cases/${encodeURIComponent(caseId)}`),
      api(`/api/cases/${encodeURIComponent(caseId)}/content`),
      api(`/api/cases/${encodeURIComponent(caseId)}/vision`),
      api(`/api/cases/${encodeURIComponent(caseId)}/nlp`),
      api(`/api/cases/${encodeURIComponent(caseId)}/relational`),
      api(`/api/cases/${encodeURIComponent(caseId)}/evidences`),
    ]);
    renderCaseDetail(detail, content, vision, nlp, relational, evidences);
  } catch (err) {
    toast(`No se pudo cargar el caso: ${err.message}`, "err");
  }
}

export function _levelBadge(level) {
  const lv = (level || "REVISAR").toUpperCase();
  const cls =
    lv === "VERIFICADO" ? "badge-verificado" :
    lv === "PROBABLE" ? "badge-probable" :
    lv === "DESCARTADO" ? "badge-descartado" : "badge-revisar";
  return `<span class="badge ${cls}">${esc(lv)}</span>`;
}

export function _scoreColor(score, level) {
  if (level === "DESCARTADO") return "var(--rose)";
  if (score >= 65) return "var(--emerald)";
  if (score >= 40) return "var(--accent)";
  return "var(--amber)";
}

/** Renderiza pestañas de contenido ingerido (posts, comentarios, perfil enriquecido, historial de ubicaciones). */
export function renderCaseContentViewer(containerEl, content, evidences = null) {
  if (!containerEl) return;
  const counts = (content && content.counts) || { posts: 0, comments: 0, media: 0, interactions: 0 };
  const posts = (content && content.posts) || [];
  const comments = (content && content.comments) || [];
  const media = (content && content.media_items) || [];
  const interactions = (content && content.interactions) || [];
  const topTags = (content && content.top_hashtags) || [];
  const topMentions = (content && content.top_mentions) || [];
  const evList = (evidences && evidences.evidences) || [];
  const snaps = (evidences && evidences.profile_snapshots) || [];
  const reactions = (evidences && evidences.reactions) || [];
  const tab = state.caseContentTab || "posts";

  const summaryChips = (topTags.length || topMentions.length)
    ? `<div style="margin-bottom:12px;display:flex;flex-direction:column;gap:6px">
        ${topTags.length ? `<div class="tags-wrap"><span class="muted" style="font-size:12px">Temas / Hashtags:</span>${topTags.slice(0, 12).map((t) => `<span class="tag-chip">#${esc(t.tag)} (${t.count})</span>`).join("")}</div>` : ""}
        ${topMentions.length ? `<div class="tags-wrap"><span class="muted" style="font-size:12px">Menciones frecuentes:</span>${topMentions.slice(0, 12).map((m) => `<span class="tag-chip">@${esc(m.handle)} (${m.count})</span>`).join("")}</div>` : ""}
      </div>`
    : "";

  let bodyHtml = "";
  if (tab === "posts") {
    bodyHtml = posts.length
      ? `<div class="list-stack">
          ${posts
            .map(
              (p) => `<div class="post-card">
                <div class="post-card-head">
                  <div><span class="badge badge-info">${esc(p.platform)}</span> <strong>@${esc(p.author || "autor")}</strong> <span class="muted">${esc(p.published_at || "")}</span></div>
                  ${p.url ? `<a class="btn btn-xs btn-ghost" href="${esc(p.url)}" target="_blank" rel="noopener">Abrir</a>` : ""}
                </div>
                <div class="post-body">${esc((p.content || "").slice(0, 600))}</div>
                ${(p.hashtags || []).length || (p.mentions || []).length
                  ? `<div class="tags-wrap">
                      ${(p.hashtags || []).slice(0, 8).map((h) => `<span class="tag-chip">#${esc(h)}</span>`).join("")}
                      ${(p.mentions || []).slice(0, 6).map((m) => `<span class="tag-chip">@${esc(m)}</span>`).join("")}
                    </div>`
                  : ""}
              </div>`
            )
            .join("")}
        </div>`
      : `<div class="muted">Sin publicaciones ingeridas todavía. Usa un conector de API abierta o importa un archivo JSON/CSV arriba.</div>`;
  } else if (tab === "media") {
    bodyHtml = media.length
      ? `<div class="media-gallery">
          ${media
            .map((m) => {
              const imgUrl = m.source_url || m.clean_copy_url || "";
              const isImg = (m.media_type || "image") === "image" && imgUrl;
              return `<div class="media-tile">
                ${isImg
                  ? `<a href="${esc(imgUrl)}" target="_blank" rel="noopener"><img class="media-tile-img" src="${esc(imgUrl)}" alt="${esc(m.alt_text || m.filename)}" loading="lazy" /></a>`
                  : `<div class="media-tile-placeholder">${esc((m.media_type || "MEDIA").toUpperCase())}</div>`}
                <div class="media-tile-meta">
                  <strong>${esc((m.filename || "").slice(0, 26))}</strong>
                  ${m.alt_text ? `<div class="muted">${esc(m.alt_text.slice(0, 90))}</div>` : ""}
                  ${m.gps_lat != null ? `<span class="badge badge-baja">GPS</span>` : ""}
                  ${m.captured_date ? `<div class="muted">${esc(m.captured_date.slice(0, 19))}</div>` : ""}
                </div>
              </div>`;
            })
            .join("")}
        </div>`
      : `<div class="muted">Sin fotos o archivos multimedia en este caso todavía.</div>`;
  } else if (tab === "comments") {
    bodyHtml = comments.length
      ? `<div class="list-stack">
          ${comments
            .map(
              (cm) => `<div class="post-card">
                <div class="post-card-head">
                  <div><strong>@${esc(cm.author_handle)}</strong> <span class="muted">→</span> <strong>@${esc(cm.target_handle || "hilo")}</strong></div>
                  <span class="muted">${esc(cm.published_at || "")}</span>
                </div>
                <div class="post-body">${esc((cm.content || "").slice(0, 500))}</div>
              </div>`
            )
            .join("")}
        </div>`
      : `<div class="muted">Sin comentarios públicos ingeridos todavía.</div>`;
  } else if (tab === "interactions") {
    bodyHtml = interactions.length
      ? `<div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr><th>Origen</th><th>Destino</th><th>Tipo</th><th>Frecuencia</th><th>Peso</th></tr>
            </thead>
            <tbody>
              ${interactions
                .map(
                  (ix) => `<tr>
                    <td><strong>@${esc(ix.source_handle)}</strong></td>
                    <td><strong>@${esc(ix.target_handle)}</strong></td>
                    <td><span class="badge badge-info">${esc(ix.kind)}</span></td>
                    <td>${Number(ix.evidence_count || 1)}</td>
                    <td><code>${Number(ix.weight || 1).toFixed(1)}</code></td>
                  </tr>`
                )
                .join("")}
            </tbody>
          </table>
        </div>`
      : `<div class="muted">Sin interacciones detectadas todavía. Al ingerir comentarios o menciones aparecerán aquí (base del Grafo SNA de Fase 5).</div>`;
  } else if (tab === "evidences") {
    const snapHtml = snaps.length
      ? `<div style="margin-bottom:14px">
          <div class="muted" style="font-size:12px;margin-bottom:6px"><strong>Snapshots históricos del perfil (${snaps.length})</strong></div>
          <div class="list-stack">
            ${snaps
              .map(
                (s) => `<div class="post-card">
                  <div class="post-card-head">
                    <div><span class="badge badge-info">${esc(s.platform)}</span> <strong>${esc(s.display_name || s.username)}</strong> <span class="badge badge-verificado">${esc(s.access_method)}</span></div>
                    <span class="muted">${esc(s.captured_at)}</span>
                  </div>
                  ${s.bio ? `<div class="post-body">${esc(s.bio.slice(0, 300))}</div>` : ""}
                  <div class="muted" style="font-size:11.5px">Origen: <a href="${esc(s.source_url)}" target="_blank" rel="noopener">${esc(s.source_url)}</a></div>
                  ${(s.public_links || []).length ? `<div class="tags-wrap">${s.public_links.map((u) => `<a class="tag-chip" href="${esc(u)}" target="_blank" rel="noopener">${esc(u.slice(0, 48))}</a>`).join("")}</div>` : ""}
                </div>`
              )
              .join("")}
          </div>
        </div>`
      : "";
    const rxHtml = reactions.length
      ? `<div style="margin-bottom:14px">
          <div class="muted" style="font-size:12px;margin-bottom:6px"><strong>Reacciones capturadas (${reactions.length})</strong></div>
          <div class="tags-wrap">${reactions.slice(0, 20).map((r) => `<span class="tag-chip">${esc(r.platform)} · ${esc(r.reaction_type)}: ${r.count}${r.actor_handle ? ` (@${esc(r.actor_handle)})` : ""}</span>`).join("")}</div>
        </div>`
      : "";
    const evRows = evList.length
      ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Tipo</th><th>Plataforma / Acceso</th><th>Extracto</th><th>Fuente y Captura</th></tr></thead><tbody>
          ${evList
            .slice(0, 40)
            .map(
              (ev) => `<tr>
                <td><span class="badge badge-info">${esc(ev.record_kind)}</span></td>
                <td><strong>${esc(ev.platform)}</strong><div class="muted" style="font-size:11px">${esc(ev.access_method)}</div></td>
                <td style="font-size:12px">${esc((ev.excerpt || "—").slice(0, 130))}</td>
                <td style="font-size:11.5px">
                  ${ev.source_url ? `<a href="${esc(ev.source_url)}" target="_blank" rel="noopener">Abrir fuente</a><br/>` : ""}
                  <span class="muted">${esc(ev.captured_at)}</span>
                </td>
              </tr>`
            )
            .join("")}
        </tbody></table></div>`
      : `<div class="muted">Sin registros formales de evidencia todavía.</div>`;
    bodyHtml = snapHtml + rxHtml + evRows;
  } else if (tab === "interactants") {
    bodyHtml = `<div id="caseInteractantsHost"><div class="muted">Cargando interactuantes nominales...</div></div>`;
  } else if (tab === "subject_profile") {
    bodyHtml = `<div id="caseSubjectProfileHost"><div class="muted">Cargando perfil enriquecido del sujeto...</div></div>`;
  }

  containerEl.innerHTML = `
    <div class="content-tabs">
      <button type="button" class="content-tab ${tab === "posts" ? "active" : ""}" data-ctab="posts">Publicaciones (${counts.posts})</button>
      <button type="button" class="content-tab ${tab === "media" ? "active" : ""}" data-ctab="media">Fotos / Media (${counts.media})</button>
      <button type="button" class="content-tab ${tab === "comments" ? "active" : ""}" data-ctab="comments">Comentarios (${counts.comments})</button>
      <button type="button" class="content-tab ${tab === "interactions" ? "active" : ""}" data-ctab="interactions">Interacciones (${counts.interactions})</button>
      <button type="button" class="content-tab ${tab === "interactants" ? "active" : ""}" data-ctab="interactants">Interactuantes</button>
      <button type="button" class="content-tab ${tab === "subject_profile" ? "active" : ""}" data-ctab="subject_profile">Perfil del Sujeto</button>
      <button type="button" class="content-tab ${tab === "evidences" ? "active" : ""}" data-ctab="evidences">Evidencias y Snapshots (${evList.length})</button>
    </div>
    ${summaryChips}
    ${bodyHtml}
  `;

  if (tab === "subject_profile") {
    const host = document.getElementById("caseSubjectProfileHost");
    const cid = getActiveCaseId();
    const loadSubjProf = () => {
      if (!host || !cid) return;
      api(`/api/cases/${encodeURIComponent(cid)}/subject-profile`)
        .then((res) => {
          const entries = res.entries || [];
          const catMeta = [
            ["hobbies", "Hobbies y Gustos"],
            ["places", "Lugares Visitados / Mencionados"],
            ["friends", "Círculo de Amigos (SNA)"],
            ["ideas", "Ideas y Temas Afines"],
            ["personality", "Personalidad y Patrones"],
            ["occupation", "Ocupación / Actividad"],
            ["relationship_status", "Estado Civil / Vínculos (Conservador)"],
          ];
          const byCat = {};
          entries.forEach((e) => {
            byCat[e.category] = byCat[e.category] || [];
            byCat[e.category].push(e);
          });
          const header = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
            <span class="muted">7 categorías con separación epistemológica y revisión humana</span>
            <button type="button" class="btn btn-xs btn-primary" id="btnRecomputeSubjProf">Recalcular Perfil</button>
          </div>`;
          if (!entries.length) {
            host.innerHTML = `${header}<div class="muted">Sin entradas calculadas todavía. Pulsa <strong>Recalcular Perfil</strong>.</div>`;
          } else {
            const cardsHtml = catMeta.map(([catKey, catTitle]) => {
              const items = byCat[catKey] || [];
              if (!items.length) {
                return `<div class="panel-card" style="padding:12px"><strong>${esc(catTitle)}</strong><div class="muted" style="font-size:12px;margin-top:4px">Sin indicios detectados todavía.</div></div>`;
              }
              const itemsHtml = items.map((e) => {
                const pct = Math.round(Number(e.confidence || 0) * 100);
                const refs = e.evidence_refs || [];
                const avatar = catKey === "friends" ? `<span class="tag-chip" style="font-weight:700">${esc((e.value || "?").replace("@", "").slice(0, 2).toUpperCase())}</span> ` : "";
                const bar = catKey === "personality" ? `<div class="score-bar" style="margin:4px 0"><div class="score-bar-fill" style="width:${pct}%"></div></div>` : "";
                const evBlock = refs.length
                  ? `<div id="spEv_${e.id}" style="display:none;margin-top:6px;padding:6px 8px;background:#0a1120;border-radius:6px;font-size:11.5px">${refs.map((rf) => `<div><code>${esc(rf.kind || "ref")}</code> ${rf.id ? `#${esc(rf.id)}` : ""} ${rf.quote ? `· "${esc(rf.quote)}"` : ""}</div>`).join("")}</div>`
                  : "";
                return `
                  <div class="interest-card" style="margin-top:6px">
                    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
                      <div>${avatar}<strong>${esc(e.value)}</strong></div>
                      <div style="display:flex;gap:4px;align-items:center;flex-wrap:wrap">
                        <span class="badge ${e.epistemic_status === "explicit" ? "badge-alta" : "badge-media"}">${esc(e.epistemic_status)} · ${pct}%</span>
                        <span class="badge badge-info">${esc(e.human_review)}</span>
                        ${refs.length ? `<button type="button" class="btn btn-xs btn-ghost" data-sp-ev="${e.id}">Ver evidencia</button>` : ""}
                        <button type="button" class="btn btn-xs btn-secondary" data-sp-rev="${e.id}:validated">Validar</button>
                        <button type="button" class="btn btn-xs btn-ghost" data-sp-rev="${e.id}:ambiguous">Ambiguo</button>
                        <button type="button" class="btn btn-xs btn-ghost" data-sp-rev="${e.id}:discarded">Descartar</button>
                      </div>
                    </div>
                    ${bar}
                    ${evBlock}
                  </div>
                `;
              }).join("");
              return `<div class="panel-card" style="padding:12px"><strong>${esc(catTitle)}</strong> (${items.length})${itemsHtml}</div>`;
            }).join("");
            host.innerHTML = `${header}<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px">${cardsHtml}</div>`;
          }
          document.getElementById("btnRecomputeSubjProf")?.addEventListener("click", async () => {
            await api(`/api/cases/${encodeURIComponent(cid)}/subject-profile/recompute`, { method: "POST" });
            loadSubjProf();
            toast("Perfil del sujeto actualizado.", "ok");
          });
          host.querySelectorAll("[data-sp-ev]").forEach((btn) => {
            btn.addEventListener("click", () => {
              const box = document.getElementById(`spEv_${btn.dataset.spEv}`);
              if (box) box.style.display = box.style.display === "none" ? "block" : "none";
            });
          });
          host.querySelectorAll("[data-sp-rev]").forEach((btn) => {
            btn.addEventListener("click", async () => {
              const [eid, st] = btn.dataset.spRev.split(":");
              await api(`/api/cases/${encodeURIComponent(cid)}/subject-profile/${encodeURIComponent(eid)}`, {
                method: "PATCH",
                body: JSON.stringify({ human_review: st }),
              });
              loadSubjProf();
            });
          });
        })
        .catch(() => {});
    };
    loadSubjProf();
  }

  if (tab === "interactants") {
    const host = document.getElementById("caseInteractantsHost");
    const cid = getActiveCaseId();
    if (host && cid) {
      api(`/api/cases/${encodeURIComponent(cid)}/interactants`)
        .then((res) => {
          const rows = res.interactants || [];
          if (!rows.length) {
            host.innerHTML = `<div class="muted">Sin interactuantes nominales registrados todavía.</div>`;
            return;
          }
          const renderRows = (q = "") => {
            const ql = q.trim().toLowerCase();
            const filtered = ql
              ? rows.filter((r) => (`${r.display_name} ${r.handle} ${r.platform}`).toLowerCase().includes(ql))
              : rows;
            return filtered.map((r) => `
              <tr>
                <td><strong>${esc(r.display_name || r.handle)}</strong><br/><code>@${esc(r.handle)}</code></td>
                <td>${esc(r.platform)}</td>
                <td>${Number(r.comments_count || 0)}</td>
                <td><span class="badge ${r.dominant_sentiment === "POSITIVO" ? "badge-alta" : r.dominant_sentiment === "NEGATIVO" ? "badge-baja" : "badge-info"}">${esc(r.dominant_sentiment || "NEUTRAL")}</span></td>
                <td>${r.is_friend_candidate ? '<span class="badge badge-media">Candidato amigo</span>' : '<span class="muted">—</span>'}</td>
                <td>${r.profile_url ? `<a href="${esc(r.profile_url)}" target="_blank" rel="noopener">Ver perfil</a>` : "—"}</td>
              </tr>
            `).join("");
          };
          host.innerHTML = `
            <div style="margin-bottom:8px"><input id="inpInteractantFilter" type="text" placeholder="Filtrar por nombre, handle o plataforma..." style="max-width:340px" /></div>
            <div class="table-wrap"><table>
              <thead><tr><th>Interactuante</th><th>Plataforma</th><th>Comentarios</th><th>Sentimiento</th><th>Vínculo</th><th>Perfil</th></tr></thead>
              <tbody id="tbodyInteractants">${renderRows()}</tbody>
            </table></div>
          `;
          document.getElementById("inpInteractantFilter")?.addEventListener("input", (ev) => {
            const tb = document.getElementById("tbodyInteractants");
            if (tb) tb.innerHTML = renderRows(ev.target.value);
          });
        })
        .catch((err) => {
          host.innerHTML = `<div class="muted">Error cargando interactuantes: ${esc(err.message)}</div>`;
        });
    }
  }

  containerEl.querySelectorAll("[data-ctab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.caseContentTab = btn.dataset.ctab;
      renderCaseContentViewer(containerEl, content, evidences);
    });
  });
}

/** Renderiza galería visual del caso, duplicados dHash, investigación de foto y mapa geo-hints. */
export function renderCaseVisionPanel(containerEl, caseId, vision) {
  if (!containerEl) return;
  const v = vision || { stats: {}, theme_summary: [], similar_pairs: [], ocr_snippets: [], media_items: [] };
  const stats = v.stats || { total_media: 0, analyzed: 0, pending: 0, themes_detected: 0, similar_pairs: 0 };
  const themes = v.theme_summary || [];
  const pairs = v.similar_pairs || [];
  const allItems = v.media_items || [];
  const activeTheme = state.visionThemeFilter || "";
  const vs = state.visionStatus || {};

  const engineLabel = vs.default_engine || "local_context";
  const engineBadges = `
    <div class="tags-wrap" style="margin-bottom:12px">
      <span class="badge badge-verificado">dHash: ${vs.dhash !== false ? "listo" : "off"}</span>
      <span class="badge ${vs.ollama && vs.ollama.available ? "badge-verificado" : "badge-info"}">Ollama VLM: ${vs.ollama && vs.ollama.available ? esc(vs.ollama.selected_model) : "no detectado"}</span>
      <span class="badge ${vs.gemini ? "badge-verificado" : "badge-info"}">Gemini: ${vs.gemini ? "activo" : "opcional (.env)"}</span>
      <span class="badge ${vs.openai ? "badge-verificado" : "badge-info"}">OpenAI: ${vs.openai ? "activo" : "opcional (.env)"}</span>
      <span class="badge ${vs.tesseract_ocr ? "badge-verificado" : "badge-info"}">OCR local: ${vs.tesseract_ocr ? "Tesseract OK" : "vía VLM"}</span>
    </div>
  `;

  const themesHtml = themes.length
    ? `<div style="margin:14px 0">
        <div class="muted" style="font-size:12px;margin-bottom:8px"><strong>Intereses visuales detectados</strong> (haz clic en un tema para ver las fotos que lo respaldan):</div>
        <div class="tags-wrap">
          <button type="button" class="theme-pill ${!activeTheme ? "active" : ""}" data-vtheme="">Todas (${allItems.length})</button>
          ${themes
            .map(
              (t) => `<button type="button" class="theme-pill ${activeTheme === t.label ? "active" : ""}" data-vtheme="${esc(t.label)}" title="${esc((t.evidences || []).join(" | "))}">
                <span>${esc(t.label)}</span>
                <span class="badge ${t.confidence === "ALTA" ? "badge-verificado" : "badge-probable"}">${t.count}</span>
              </button>`
            )
            .join("")}
        </div>
      </div>`
    : "";

  const pairsHtml = pairs.length
    ? `<div style="margin:14px 0">
        <div class="muted" style="font-size:12px;margin-bottom:6px"><strong>Imágenes duplicadas o muy similares (dHash perceptual):</strong></div>
        <div class="list-stack">
          ${pairs
            .map(
              (pr) => `<div class="alert-box alert-warn">
                <strong>${esc(pr.relation)} (${pr.similarity_pct}%)</strong>:
                <code>${esc(pr.filename_a)}</code> ↔ <code>${esc(pr.filename_b)}</code>
                <span class="muted">(distancia Hamming: ${pr.hamming_distance}/64)</span>
              </div>`
            )
            .join("")}
        </div>
      </div>`
    : "";

  const filteredItems = activeTheme
    ? allItems.filter((m) => (m.tags || []).includes(activeTheme))
    : allItems;

  const galleryHtml = filteredItems.length
    ? `<div class="media-gallery">
        ${filteredItems
          .map((m) => {
            const imgUrl = m.source_url || m.clean_copy_url || "";
            const vis = m.vision || {};
            const colors = vis.dominant_colors || [];
            const tags = m.tags || [];
            const ocr = m.ocr_text || vis.ocr_text || "";
            const analyzed = Boolean(vis.analyzed_at);
            return `
              <div class="media-tile">
                ${imgUrl
                  ? `<a href="${esc(imgUrl)}" target="_blank" rel="noopener"><img class="media-tile-img" src="${esc(imgUrl)}" alt="${esc(m.alt_text || m.filename)}" loading="lazy" /></a>`
                  : `<div class="media-tile-placeholder">IMG</div>`}
                <div class="media-tile-meta">
                  <div style="display:flex;justify-content:space-between;align-items:center;gap:4px">
                    <strong title="${esc(m.filename)}">${esc((m.filename || "").slice(0, 22))}</strong>
                    ${colors.length ? `<span class="color-swatches">${colors.map((hex) => `<span class="color-swatch" style="background:${esc(hex)}" title="${esc(hex)}"></span>`).join("")}</span>` : ""}
                  </div>
                  ${analyzed
                    ? `<div><span class="badge ${vis.confidence === "ALTA" ? "badge-verificado" : vis.confidence === "MEDIA" ? "badge-probable" : "badge-revisar"}">${esc(vis.engine || "local")} · ${esc(vis.confidence || "BAJA")}</span></div>
                       ${vis.description ? `<div class="muted">${esc(vis.description.slice(0, 120))}</div>` : ""}
                       ${tags.length ? `<div class="tags-wrap">${tags.map((tg) => `<span class="tag-chip">${esc(tg)}</span>`).join("")}</div>` : ""}
                       ${ocr ? `<div class="ocr-snippet">OCR: ${esc(ocr.slice(0, 90))}</div>` : ""}
                       ${m.dhash ? `<div class="muted" style="font-family:var(--mono);font-size:10.5px">dHash: ${esc(m.dhash)}</div>` : ""}`
                    : `<div class="muted">Pendiente de análisis visual</div>`}
                  <div style="margin-top:6px">
                    <button type="button" class="btn btn-xs btn-secondary" data-geo-photo="${m.id}">Investigar ubicación</button>
                  </div>
                </div>
              </div>
            `;
          })
          .join("")}
      </div>`
    : `<div class="muted">${activeTheme ? "Ninguna foto coincide con el tema seleccionado." : "Aún no hay fotos en este caso. Sube imágenes arriba o ingiere desde un conector abierto."}</div>`;

  containerEl.innerHTML = `
    <div class="panel-card">
      <div class="panel-head">
        <h2>Galería y Análisis Visual con IA (Fase 3)</h2>
        <span class="muted">${stats.analyzed || 0}/${stats.total_media || 0} analizadas · Motor por defecto: <code>${esc(engineLabel)}</code></span>
      </div>
      ${engineBadges}
      <div class="two-col" style="margin-bottom:14px">
        <form id="formGalleryUpload" style="display:flex;flex-direction:column;gap:8px">
          <div class="muted" style="font-size:12px"><strong>1. Subir lote de fotos locales a la galería del caso</strong></div>
          <input id="galFiles" type="file" accept="image/*" multiple required />
          <input id="galAlt" type="text" placeholder="Contexto o descripción opcional para este lote..." />
          <div><button type="submit" class="btn btn-sm btn-secondary" id="btnGalUpload">Subir foto(s) a galería</button></div>
        </form>

        <form id="formVisionAnalyze" style="display:flex;flex-direction:column;gap:8px">
          <div class="muted" style="font-size:12px"><strong>2. Ejecutar análisis visual (dHash + OCR + Temas VLM/Local)</strong></div>
          <div class="inline-actions">
            <select id="visProvider" style="flex:1">
              <option value="auto">Auto (VLM si disponible, si no Local)</option>
              <option value="local_context">Solo Local (dHash + Paleta + Contexto/OCR)</option>
              <option value="ollama">Ollama Local (qwen2.5-vl / llava)</option>
              <option value="gemini">Gemini Vision API (.env)</option>
              <option value="openai">OpenAI Vision API (.env)</option>
            </select>
            <label class="checkbox-label" style="font-size:12px">
              <input type="checkbox" id="visOnlyPending" />
              <span>Solo pendientes</span>
            </label>
          </div>
          <div><button type="submit" class="btn btn-sm btn-primary">Analizar galería (${stats.total_media || 0} fotos)</button></div>
          <div id="progress-casevision"></div>
          <div id="results-casevision"></div>
        </form>
      </div>

      ${pairsHtml}
      ${themesHtml}
      ${galleryHtml}

      <div class="panel-card" style="margin-top:16px">
        <div class="panel-head">
          <h2>Pistas de Ubicación Visual (Probabilístico)</h2>
          <button type="button" class="btn btn-xs btn-secondary" id="btnRecomputeGeoHints">Recalcular pistas</button>
        </div>
        <div id="singlePhotoGeoHost" style="margin-bottom:10px"></div>
        <div id="geoHintsHost"><div class="muted">Cargando pistas de ubicación...</div></div>
      </div>

      <div class="panel-card" style="margin-top:16px">
        <div class="panel-head">
          <h2>Historial de Ubicaciones (Cronológico)</h2>
          <a class="btn btn-xs btn-secondary" href="/api/cases/${encodeURIComponent(caseId)}/location-history?format=csv" download="location_history_${esc(caseId)}.csv">Exportar CSV</a>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;align-items:center">
          <input id="lhFrom" type="date" title="Desde" style="max-width:140px" />
          <input id="lhTo" type="date" title="Hasta" style="max-width:140px" />
          <select id="lhSource" style="max-width:140px">
            <option value="">Todas las fuentes</option>
            <option value="manual">Manual (analista)</option>
            <option value="exif">EXIF GPS</option>
            <option value="vlm">VLM</option>
            <option value="ocr">OCR</option>
            <option value="local_context">Contexto local</option>
            <option value="ner">NER</option>
            <option value="nominatim">Nominatim</option>
          </select>
          <input id="lhMinConf" type="number" min="0" max="100" step="5" value="0" placeholder="Conf. mín %" style="max-width:110px" />
          <button type="button" class="btn btn-xs btn-primary" id="btnFilterLocHist">Filtrar</button>
        </div>
        <div id="locationHistoryHost"><div class="muted">Cargando historial de ubicaciones...</div></div>
      </div>
    </div>
  `;

  const loadGeoHints = () => {
    const host = document.getElementById("geoHintsHost");
    if (!host) return;
    api(`/api/cases/${encodeURIComponent(caseId)}/geo-hints`)
      .then((res) => {
        const hints = res.hints || [];
        const cands = res.candidates || [];
        if (!hints.length && !cands.length) {
          host.innerHTML = `<div class="muted">Sin pistas de ubicación aún. Pulsa <strong>Recalcular pistas</strong> tras analizar fotos.</div>`;
          return;
        }
        const chips = hints.slice(0, 18).map((h) => `<span class="tag-chip">${esc(h.hint_type)}: <strong>${esc(h.value)}</strong> (${Math.round(Number(h.confidence || 0) * 100)}%)</span>`).join("");
        const candsHtml = cands.slice(0, 8).map((c) => {
          const pct = Math.round(Number(c.confidence || 0) * 100);
          return `
            <div class="interest-card" style="margin-top:8px">
              <div style="display:flex;justify-content:space-between;align-items:center">
                <strong>${esc(c.label)}</strong>
                <span class="badge ${c.has_exif ? "badge-alta" : "badge-info"}">${esc(c.source)} · ${pct}%</span>
              </div>
              <div class="score-bar" style="margin:6px 0"><div class="score-bar-fill" style="width:${pct}%"></div></div>
              <div class="muted" style="font-size:12px">${esc(c.probabilistic_statement || "")}</div>
              ${(c.evidence || []).length ? `<div style="font-size:11.5px;margin-top:4px">${c.evidence.slice(0, 2).map((ev) => `• ${esc(ev)}`).join("<br/>")}</div>` : ""}
            </div>
          `;
        }).join("");
        const mapped = cands.filter((c) => typeof c.lat === "number" && typeof c.lon === "number");
        const mapHtml = mapped.length
          ? `<div style="margin-bottom:12px;border:1px solid var(--border);border-radius:10px;padding:10px;background:#0a1120">
               <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:8px">
                 <div style="font-size:12px;color:var(--text-muted)">
                   <strong>Mapa probabilístico (${mapped.length} punto/s)</strong> ·
                   <span style="color:#10b981">● EXIF exacto</span> ·
                   <span style="color:#38bdf8">● Pista visual / NER (aprox.)</span>
                 </div>
                 <button type="button" class="btn btn-xs btn-ghost" id="btnGeoMapToggle">Cambiar a vista mundial</button>
               </div>
               <canvas id="geoHintsMapCanvas" style="width:100%;height:260px;display:block;border-radius:8px;cursor:pointer"></canvas>
               <div id="geoMapSelectedInfo" class="muted" style="font-size:12px;margin-top:6px">Haz clic en un punto del mapa para ver su evidencia.</div>
             </div>`
          : `<div class="muted" style="font-size:12px;margin-bottom:8px">Sin coordenadas aproximadas disponibles para graficar en el mapa.</div>`;

        host.innerHTML = `${mapHtml}<div class="tags-wrap" style="margin-bottom:8px">${chips}</div>${candsHtml}`;
        if (mapped.length) {
          const canvas = document.getElementById("geoHintsMapCanvas");
          let focusPoints = true;
          const draw = () => renderGeoHintsMap(canvas, mapped, focusPoints);
          draw();
          document.getElementById("btnGeoMapToggle")?.addEventListener("click", (ev) => {
            focusPoints = !focusPoints;
            ev.target.textContent = focusPoints ? "Cambiar a vista mundial" : "Enfocar en puntos";
            draw();
          });
        }
      })
      .catch(() => {});
  };
  loadGeoHints();

  const loadLocationHistory = () => {
    const host = document.getElementById("locationHistoryHost");
    if (!host) return;
    const fromVal = document.getElementById("lhFrom")?.value || "";
    const toVal = document.getElementById("lhTo")?.value || "";
    const srcVal = document.getElementById("lhSource")?.value || "";
    const minPct = Number(document.getElementById("lhMinConf")?.value || 0);
    const qs = new URLSearchParams();
    if (fromVal) qs.set("from", fromVal);
    if (toVal) qs.set("to", toVal);
    if (srcVal) qs.set("source", srcVal);
    if (minPct > 0) qs.set("min_confidence", String(minPct / 100));
    api(`/api/cases/${encodeURIComponent(caseId)}/location-history?${qs.toString()}`)
      .then((res) => {
        const rows = res.history || [];
        if (!rows.length) {
          host.innerHTML = `<div class="muted">Sin registros de historial que coincidan con el filtro. Pulsa <strong>Investigar ubicación</strong> en una foto.</div>`;
          return;
        }
        host.innerHTML = `<div class="table-wrap"><table>
          <thead><tr><th>Fecha</th><th>Foto</th><th>Ubicación</th><th>Fuente</th><th>Confianza</th><th>Evidencia</th></tr></thead>
          <tbody>${rows.map((r) => {
            const loc = [r.city, r.region, r.country].filter(Boolean).join(", ") || "—";
            const thumb = r.source_url ? `<img src="${esc(r.source_url)}" style="width:42px;height:42px;object-fit:cover;border-radius:6px;vertical-align:middle;margin-right:6px" />` : "";
            const coord = (typeof r.lat === "number" && typeof r.lon === "number") ? `<br/><small><code>${Number(r.lat).toFixed(3)}, ${Number(r.lon).toFixed(3)}</code></small>` : "";
            return `<tr>
              <td><small>${esc((r.sort_date || r.published_at || "—").slice(0, 19))}</small></td>
              <td>${thumb}<code>${esc(r.filename || ("media#" + r.media_id))}</code></td>
              <td><strong>${esc(loc)}</strong>${coord}</td>
              <td><span class="badge ${r.is_manual_override ? "badge-alta" : "badge-info"}">${r.is_manual_override ? "Confirmada analista" : esc(r.source)}</span></td>
              <td>${Math.round(Number(r.confidence || 0) * 100)}%</td>
              <td><small>${esc((r.evidence_quote || "").slice(0, 140))}</small></td>
            </tr>`;
          }).join("")}</tbody>
        </table></div>`;
      })
      .catch(() => {});
  };
  loadLocationHistory();
  document.getElementById("btnFilterLocHist")?.addEventListener("click", loadLocationHistory);

  containerEl.querySelectorAll("[data-geo-photo]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const mid = btn.dataset.geoPhoto;
      const singleHost = document.getElementById("singlePhotoGeoHost");
      const mediaObj = allItems.find((x) => String(x.id) === String(mid)) || {};
      const imgPreview = mediaObj.source_url || mediaObj.clean_copy_url || "";
      btn.disabled = true;
      if (singleHost) {
        singleHost.innerHTML = `<div class="muted">Analizando pistas visuales/VLM de la foto #${esc(mid)}...</div>`;
      }
      try {
        const out = await api(`/api/cases/${encodeURIComponent(caseId)}/media/${encodeURIComponent(mid)}/geo-investigate`, {
          method: "POST",
          body: JSON.stringify({ use_vlm: true }),
        });
        const cands = out.candidates || [];
        const rawHints = (out.geo_hints && out.geo_hints.hints) || [];
        const hintsHtml = rawHints.length
          ? `<div class="tags-wrap" style="margin:6px 0">${rawHints.map((h) => `<span class="tag-chip">${esc(h.hint_type)}: <strong>${esc(h.value)}</strong> (${Math.round(Number(h.confidence || 0) * 100)}%)</span>`).join("")}</div>`
          : `<div class="muted" style="font-size:12px">Sin pistas visuales explícitas detectadas en esta imagen.</div>`;
        const candsList = cands.length
          ? cands.slice(0, 4).map((c) => `
              <div style="font-size:12px;margin-top:4px">
                • <strong>${esc(c.label)}</strong> (${Math.round(Number(c.confidence || 0) * 100)}% · <code>${esc(c.source)}</code>)
                ${(c.evidence || []).length ? `<br/><small class="muted">${esc(c.evidence[0])}</small>` : ""}
              </div>
            `).join("")
          : `<div class="muted" style="font-size:12px">Sin ubicación candidata sugerida; puedes fijarla manualmente abajo.</div>`;
        const top = cands[0] || {};
        if (singleHost) {
          singleHost.innerHTML = `
            <div class="alert-box alert-info" style="margin-bottom:8px">
              <div style="display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap">
                ${imgPreview ? `<img src="${esc(imgPreview)}" style="width:76px;height:76px;object-fit:cover;border-radius:8px;border:1px solid var(--border)" />` : ""}
                <div style="flex:1;min-width:220px">
                  <strong>Análisis de ubicación · ${esc(mediaObj.filename || ("Foto #" + mid))}</strong>
                  ${hintsHtml}
                  ${candsList}
                </div>
              </div>
              <form id="formManualGeoOverride" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;padding-top:8px;border-top:1px solid rgba(148,163,184,0.2)">
                <input id="inpManCity" type="text" value="${esc(top.city || "")}" placeholder="Ciudad" style="max-width:130px" />
                <input id="inpManRegion" type="text" value="${esc(top.region || "")}" placeholder="Región" style="max-width:130px" />
                <input id="inpManCountry" type="text" value="${esc(top.country && !String(top.country).includes("Compatible") ? top.country : "")}" placeholder="País" style="max-width:130px" />
                <input id="inpManNote" type="text" placeholder="Nota / evidencia del analista" style="flex:1;min-width:160px" />
                <button type="submit" class="btn btn-xs btn-primary">Fijar ubicación manual</button>
              </form>
            </div>
          `;
          document.getElementById("formManualGeoOverride")?.addEventListener("submit", async (ev) => {
            ev.preventDefault();
            await api(`/api/cases/${encodeURIComponent(caseId)}/media/${encodeURIComponent(mid)}/geo-investigate`, {
              method: "POST",
              body: JSON.stringify({
                manual_override: true,
                city: document.getElementById("inpManCity")?.value.trim() || "",
                region: document.getElementById("inpManRegion")?.value.trim() || "",
                country: document.getElementById("inpManCountry")?.value.trim() || "",
                evidence_quote: document.getElementById("inpManNote")?.value.trim() || "Ubicación confirmada manualmente por el analista",
              }),
            });
            toast("Ubicación manual confirmada.", "ok");
            loadLocationHistory();
            loadGeoHints();
          });
        }
        loadGeoHints();
        loadLocationHistory();
      } catch (err) {
        toast(err.message, "err");
      } finally {
        btn.disabled = false;
      }
    });
  });
  document.getElementById("btnRecomputeGeoHints")?.addEventListener("click", async () => {
    try {
      await api(`/api/cases/${encodeURIComponent(caseId)}/geo-hints/recompute`, { method: "POST", body: JSON.stringify({}) });
      loadGeoHints();
      loadLocationHistory();
      toast("Pistas de ubicación recalculadas.", "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  });

  // Filtros interactivos por tema visual
  containerEl.querySelectorAll("[data-vtheme]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.visionThemeFilter = btn.dataset.vtheme || "";
      renderCaseVisionPanel(containerEl, caseId, vision);
    });
  });

  // Subir lote de fotos
  const upForm = document.getElementById("formGalleryUpload");
  if (upForm) {
    upForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const inp = document.getElementById("galFiles");
      if (!inp.files || !inp.files.length) return;
      const btn = document.getElementById("btnGalUpload");
      btn.disabled = true;
      try {
        const fd = new FormData();
        Array.from(inp.files).forEach((f) => fd.append("files", f));
        fd.append("alt_text", document.getElementById("galAlt").value.trim());
        const data = await apiFormData(`/api/cases/${encodeURIComponent(caseId)}/media/upload`, fd);
        toast(`Subidas ${(data.uploaded || []).length} foto(s) a la galería del caso.`, "ok");
        selectCase(caseId);
      } catch (err) {
        toast(err.message, "err");
      } finally {
        btn.disabled = false;
      }
    });
  }

  // Analizar galería (job asíncrono con progreso)
  const visForm = document.getElementById("formVisionAnalyze");
  if (visForm) {
    visForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const data = await api(`/api/cases/${encodeURIComponent(caseId)}/vision/analyze`, {
          method: "POST",
          body: JSON.stringify({
            provider: document.getElementById("visProvider").value,
            only_pending: document.getElementById("visOnlyPending").checked,
            max_images: 40,
          }),
        });
        startJobPolling(data.job_id, "casevision", (job, container) => {
          const r = job.result || {};
          container.innerHTML = `<div class="alert-box alert-safe">Análisis visual terminado: <strong>${r.analyzed || 0}</strong> fotos procesadas.</div>`;
        });
      } catch (err) {
        toast(err.message, "err");
      }
    });
  }
}

/** Dibuja en `<canvas>` el mapa proyectado con los puntos de ubicación candidatos y sus pesos. */
export function renderGeoHintsMap(canvas, candidates, focusPoints = true) {
  if (!canvas || !candidates || !candidates.length) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 720;
  const h = 260;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const continents = [
    [[-168,65],[-140,70],[-125,70],[-95,72],[-80,62],[-55,52],[-65,44],[-75,35],[-81,25],[-90,21],[-84,10],[-78,8],[-88,14],[-105,20],[-117,32],[-125,48],[-150,60]],
    [[-78,9],[-72,12],[-62,10],[-50,0],[-35,-6],[-38,-14],[-42,-23],[-52,-33],[-65,-42],[-68,-54],[-74,-50],[-72,-35],[-71,-18],[-81,-5]],
    [[-10,36],[0,38],[5,43],[2,51],[10,55],[18,58],[28,70],[50,68],[60,55],[40,42],[28,40],[15,38],[10,44],[0,42]],
    [[-17,15],[-6,36],[12,37],[32,31],[43,12],[51,12],[41,-2],[35,-25],[20,-35],[15,-22],[9,4],[-8,5]],
    [[35,42],[60,55],[105,72],[140,68],[170,65],[142,45],[128,35],[120,24],[108,10],[100,2],[96,16],[80,8],[68,24],[55,25],[40,20]],
    [[114,-22],[130,-12],[142,-11],[153,-28],[148,-38],[136,-35],[115,-34]],
  ];

  let minLon = -180, maxLon = 180, minLat = -60, maxLat = 78;
  if (focusPoints && candidates.length) {
    const lons = candidates.map((c) => c.lon);
    const lats = candidates.map((c) => c.lat);
    const padLon = Math.max(12, (Math.max(...lons) - Math.min(...lons)) * 0.45);
    const padLat = Math.max(8, (Math.max(...lats) - Math.min(...lats)) * 0.45);
    minLon = Math.max(-180, Math.min(...lons) - padLon);
    maxLon = Math.min(180, Math.max(...lons) + padLon);
    minLat = Math.max(-70, Math.min(...lats) - padLat);
    maxLat = Math.min(82, Math.max(...lats) + padLat);
    const spanLon = maxLon - minLon;
    const spanLat = maxLat - minLat;
    const targetRatio = w / h;
    if (spanLon / spanLat < targetRatio) {
      const extra = (spanLat * targetRatio - spanLon) / 2;
      minLon = Math.max(-180, minLon - extra);
      maxLon = Math.min(180, maxLon + extra);
    }
  }

  const projX = (lon) => ((lon - minLon) / Math.max(0.001, maxLon - minLon)) * w;
  const projY = (lat) => h - ((lat - minLat) / Math.max(0.001, maxLat - minLat)) * h;

  ctx.fillStyle = "#08101e";
  ctx.fillRect(0, 0, w, h);

  ctx.strokeStyle = "rgba(148, 163, 184, 0.12)";
  ctx.lineWidth = 1;
  for (let lon = -180; lon <= 180; lon += 30) {
    const x = projX(lon);
    if (x >= 0 && x <= w) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
  }
  for (let lat = -60; lat <= 80; lat += 20) {
    const y = projY(lat);
    if (y >= 0 && y <= h) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
  }

  ctx.fillStyle = "#16243d";
  ctx.strokeStyle = "rgba(100, 116, 139, 0.35)";
  ctx.lineWidth = 1;
  continents.forEach((poly) => {
    ctx.beginPath();
    poly.forEach(([lon, lat], i) => {
      const x = projX(lon), y = projY(lat);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  });

  const hitTargets = [];
  candidates.forEach((c) => {
    const x = projX(c.lon);
    const y = projY(c.lat);
    const conf = Number(c.confidence || 0.5);
    const color = c.has_exif ? "#10b981" : "#38bdf8";
    const haloR = Math.max(12, Math.round((1.15 - conf) * 38));

    ctx.beginPath();
    ctx.arc(x, y, haloR, 0, Math.PI * 2);
    ctx.fillStyle = c.has_exif ? "rgba(16, 185, 129, 0.18)" : "rgba(56, 189, 248, 0.16)";
    ctx.fill();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(x, y, 5.5, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = "#08101e";
    ctx.lineWidth = 1.5;
    ctx.stroke();

    const pct = Math.round(conf * 100);
    const lbl = `${c.city || c.region || c.country || c.label} (${pct}%)`;
    ctx.font = "600 11px system-ui, sans-serif";
    ctx.fillStyle = "#e2e8f0";
    ctx.fillText(lbl, Math.min(w - 130, x + 9), Math.max(14, y + 4));

    hitTargets.push({ x, y, r: Math.max(14, haloR), cand: c });
  });

  canvas.onclick = (ev) => {
    const rect = canvas.getBoundingClientRect();
    const mx = ev.clientX - rect.left;
    const my = ev.clientY - rect.top;
    const hit = hitTargets.find((t) => Math.hypot(mx - t.x, my - t.y) <= t.r);
    const infoEl = document.getElementById("geoMapSelectedInfo");
    if (!infoEl) return;
    if (!hit) {
      infoEl.innerHTML = `<span class="muted">Haz clic en un punto del mapa para ver su evidencia.</span>`;
      return;
    }
    const c = hit.cand;
    const pct = Math.round(Number(c.confidence || 0) * 100);
    infoEl.innerHTML = `
      <strong>${esc(c.label)}</strong> · <code>${Number(c.lat).toFixed(4)}, ${Number(c.lon).toFixed(4)}</code>
      (${c.coord_precision === "exif" ? "EXIF exacto" : "centroide aproximado"} · ${pct}% confianza)<br/>
      <span class="muted">${esc(c.probabilistic_statement || "")}</span>
    `;
  };
}

export function _renderSentimentCard(title, s) {
  const data = s || { total: 0, positive_pct: 0, neutral_pct: 0, critical_pct: 0, dominant: "NEUTRO", avg_score: 0 };
  const toneBadge =
    data.dominant === "POSITIVO" ? "badge-verificado" :
    data.dominant === "CRÍTICO" ? "badge-descartado" : "badge-info";
  return `
    <div class="interest-card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <strong>${esc(title)}</strong>
        <span class="badge ${toneBadge}">${esc(data.dominant)} (${data.total} textos)</span>
      </div>
      <div class="sent-bar">
        <div class="sent-pos" style="width:${data.positive_pct || 0}%" title="Positivo ${data.positive_pct}%"></div>
        <div class="sent-neu" style="width:${data.neutral_pct || 0}%" title="Neutro ${data.neutral_pct}%"></div>
        <div class="sent-neg" style="width:${data.critical_pct || 0}%" title="Crítico ${data.critical_pct}%"></div>
      </div>
      <div class="case-item-meta">
        <span style="color:var(--emerald)">Positivo: ${data.positive_pct || 0}%</span>
        <span>Neutro: ${data.neutral_pct || 0}%</span>
        <span style="color:var(--rose)">Crítico: ${data.critical_pct || 0}%</span>
        <span>Score: ${Number(data.avg_score || 0).toFixed(2)}</span>
      </div>
    </div>
  `;
}

/** Renderiza análisis NLP del caso (intereses, NER, sentimiento sujeto vs. terceros y patrones horarios). */
export function renderCaseNlpPanel(containerEl, caseId, nlp) {
  if (!containerEl) return;
  const ns = state.nlpStatus || {};
  const analyzed = Boolean(nlp && nlp.analyzed);

  let bodyHtml = `<div class="muted">Todavía no has ejecutado el análisis NLP sobre este caso. Haz clic en <strong>Analizar bios, posts y comentarios</strong> arriba.</div>`;

  if (analyzed) {
    const sum = nlp.summary || {};
    const interests = nlp.unified_interests || [];
    const sent = nlp.sentiment || {};
    const ent = nlp.entities || {};
    const tl = nlp.timeline || {};
    const caveats = nlp.caveats || [];

    const interestsHtml = interests.length
      ? `<div class="social-grid">
          ${interests
            .map((it) => {
              const modBadge = it.modality === "Texto + Fotos" ? "badge-verificado" : it.modality === "Fotos" ? "badge-probable" : "badge-info";
              const confBadge = it.confidence === "ALTA" ? "badge-verificado" : it.confidence === "MEDIA" ? "badge-probable" : "badge-revisar";
              const quotes = (it.evidence_quotes || []).map(
                (q) => `<div class="quote-item"><span class="muted">${esc(q.source)}:</span> “${esc(q.quote)}”</div>`
              ).join("");
              return `
                <div class="interest-card">
                  <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;flex-wrap:wrap">
                    <strong>${esc(it.label)}</strong>
                    <div class="inline-actions">
                      <span class="badge ${modBadge}">${esc(it.modality)}</span>
                      <span class="badge ${confBadge}">${esc(it.confidence)}</span>
                    </div>
                  </div>
                  <div class="case-item-meta">
                    <span>Texto: ${it.text_hits || 0}</span>
                    <span>Fotos: ${it.vision_hits || 0}</span>
                    <span>Tono: ${esc(it.dominant_tone || "NEUTRO")}</span>
                  </div>
                  ${(it.top_keywords || []).length ? `<div class="tags-wrap">${it.top_keywords.map((k) => `<span class="tag-chip">${esc(k)}</span>`).join("")}</div>` : ""}
                  ${quotes ? `<div class="list-stack" style="gap:5px">${quotes}</div>` : ""}
                </div>
              `;
            })
            .join("")}
        </div>`
      : `<div class="muted">Sin coincidencias temáticas destacadas todavía.</div>`;

    const slots = tl.by_time_slot || [];
    const maxSlot = Math.max(1, ...slots.map((x) => x.count || 0));
    const slotsHtml = slots
      .map(
        (x) => `<div class="mini-bar-row">
          <span class="muted">${esc(x.slot)}</span>
          <div class="mini-bar-track"><div class="mini-bar-fill" style="width:${Math.round(((x.count || 0) / maxSlot) * 100)}%"></div></div>
          <span style="font-family:var(--mono);text-align:right">${x.count || 0}</span>
        </div>`
      )
      .join("");

    const days = tl.by_weekday || [];
    const maxDay = Math.max(1, ...days.map((x) => x.count || 0));
    const daysHtml = days
      .map(
        (x) => `<div class="mini-bar-row">
          <span class="muted">${esc(x.day)}</span>
          <div class="mini-bar-track"><div class="mini-bar-fill" style="width:${Math.round(((x.count || 0) / maxDay) * 100)}%"></div></div>
          <span style="font-family:var(--mono);text-align:right">${x.count || 0}</span>
        </div>`
      )
      .join("");

    const entityBlock = (label, arr, key) =>
      arr && arr.length
        ? `<div style="margin-bottom:8px"><span class="muted" style="font-size:12px">${esc(label)}: </span><span class="tags-wrap" style="display:inline-flex">${arr.slice(0, 10).map((x) => `<span class="tag-chip">${esc(x[key])} (${x.count})</span>`).join("")}</span></div>`
        : "";

    bodyHtml = `
      <div class="alert-box alert-safe" style="margin-bottom:14px">
        <div><strong>Resumen observable (${esc(nlp.engine || "local")}):</strong> ${esc(sum.executive_summary || "")}</div>
        ${sum.communication_style ? `<div class="muted" style="margin-top:4px">${esc(sum.communication_style)}</div>` : ""}
      </div>

      <div class="two-col" style="margin-bottom:14px">
        ${_renderSentimentCard("Tono del Sujeto (bios, posts y comentarios propios)", sent.subject)}
        ${_renderSentimentCard("Tono de Terceros (comentarios recibidos)", sent.others)}
      </div>

      <div style="margin-bottom:16px">
        <div class="muted" style="font-size:12px;margin-bottom:8px"><strong>Mapa Unificado de Intereses (Fusión Texto + Fotos con citas literales):</strong></div>
        ${interestsHtml}
      </div>

      <div class="two-col" style="margin-bottom:14px">
        <div class="interest-card">
          <strong>Entidades Detectadas (NER) y Términos Clave</strong>
          ${entityBlock("Lugares", ent.places, "name")}
          ${entityBlock("Organizaciones", ent.organizations, "name")}
          ${entityBlock("Dominios enlazados", ent.domains, "name")}
          ${entityBlock("Hashtags", ent.hashtags, "name")}
          ${entityBlock("Palabras clave", ent.keywords, "word")}
          ${!((ent.places || []).length || (ent.organizations || []).length || (ent.keywords || []).length) ? `<div class="muted">Sin entidades suficientes todavía.</div>` : ""}
        </div>

        <div class="interest-card">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <strong>Patrones de Actividad (Línea de Tiempo)</strong>
            <span class="muted" style="font-size:11.5px">Pico: ${esc(tl.peak_slot || "—")}</span>
          </div>
          <div class="muted" style="font-size:11.5px">Por franja horaria (${tl.dated_items || 0} eventos fechados):</div>
          <div class="mini-bars">${slotsHtml}</div>
          <div class="muted" style="font-size:11.5px;margin-top:6px">Por día de la semana:</div>
          <div class="mini-bars">${daysHtml}</div>
        </div>
      </div>

      ${caveats.length ? `<div class="alert-box alert-warn" style="font-size:12px"><strong>Cautelas metodológicas:</strong> ${esc(caveats.join(" "))}</div>` : ""}
    `;
  }

  containerEl.innerHTML = `
    <div class="panel-card">
      <div class="panel-head">
        <h2>Análisis de Texto, Intereses y Sentimiento (Fase 4 · NLP)</h2>
        <span class="muted">${analyzed ? `Último análisis: ${esc(nlp.analyzed_at || "")}` : "Pendiente"}</span>
      </div>
      <div class="tags-wrap" style="margin-bottom:10px">
        <span class="badge badge-verificado">Léxico ES/EN + NER: listo</span>
        <span class="badge ${ns.ollama && ns.ollama.available ? "badge-verificado" : "badge-info"}">Ollama LLM: ${ns.ollama && ns.ollama.available ? esc(ns.ollama.selected_model) : "opcional"}</span>
        <span class="badge ${ns.gemini ? "badge-verificado" : "badge-info"}">Gemini: ${ns.gemini ? "activo" : "opcional (.env)"}</span>
        <span class="badge ${ns.openai ? "badge-verificado" : "badge-info"}">OpenAI: ${ns.openai ? "activo" : "opcional (.env)"}</span>
      </div>
      <form id="formNlpAnalyze" class="inline-actions" style="margin-bottom:14px;gap:10px">
        <select id="nlpProvider" style="max-width:290px">
          <option value="local">Solo Local (Rápido · Léxico + NER + Fusión Fotos)</option>
          <option value="auto">Auto (LLM si disponible, si no Local)</option>
          <option value="ollama">Ollama Local</option>
          <option value="gemini">Gemini API (.env)</option>
          <option value="openai">OpenAI API (.env)</option>
        </select>
        <button type="submit" class="btn btn-sm btn-primary">Analizar bios, posts y comentarios</button>
      </form>
      <div id="progress-casenlp"></div>
      <div id="results-casenlp"></div>
      ${bodyHtml}
    </div>
  `;

  const nlpForm = document.getElementById("formNlpAnalyze");
  if (nlpForm) {
    nlpForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const data = await api(`/api/cases/${encodeURIComponent(caseId)}/nlp/analyze`, {
          method: "POST",
          body: JSON.stringify({ provider: document.getElementById("nlpProvider").value }),
        });
        startJobPolling(data.job_id, "casenlp", (job, container) => {
          const r = job.result || {};
          if (r.nlp) {
            renderCaseNlpPanel(containerEl, caseId, r.nlp);
          } else {
            container.innerHTML = `<div class="alert-box alert-safe">Análisis NLP completado.</div>`;
          }
        });
      } catch (err) {
        toast(err.message, "err");
      }
    });
  }
}

export function _epistemicBadge(ep) {
  if (ep === "explicit") return `<span class="badge badge-verificado">EXPLÍCITA</span>`;
  if (ep === "model_inference") return `<span class="badge badge-probable">INFERENCIA IA</span>`;
  return `<span class="badge badge-revisar">AMBIGUA / COLOQUIAL</span>`;
}

export function _reviewBadge(st) {
  if (st === "validated") return `<span class="badge badge-verificado">Validada</span>`;
  if (st === "dismissed") return `<span class="badge badge-descartado">Descartada</span>`;
  if (st === "ambiguous") return `<span class="badge badge-revisar">Ambigua</span>`;
  return `<span class="badge badge-info">Pendiente</span>`;
}

/** Renderiza señales de lenguaje relacional con filtros y revisión humana (validar/ambiguo/descartar). */
export function renderCaseRelationalPanel(containerEl, caseId, relational) {
  if (!containerEl) return;
  const data = relational || { stats: {}, signals: [] };
  const st = data.stats || { total: 0, explicit: 0, ambiguous: 0, model_inference: 0, validated: 0 };
  const allSigs = data.signals || [];

  const filtered = allSigs.filter((s) => {
    if (state.relCatFilter && s.category !== state.relCatFilter) return false;
    if (state.relEpiFilter && s.epistemic_type !== state.relEpiFilter) return false;
    return true;
  });

  const rowsHtml = filtered.length
    ? `<div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>Interlocutores / Categoría</th>
              <th>Expresión y Contexto Original</th>
              <th>Clasificación Epistemológica</th>
              <th>Revisión Humana</th>
            </tr>
          </thead>
          <tbody>
            ${filtered
              .slice(0, 40)
              .map((s) => {
                const flagsHtml = (s.ambiguity_flags || []).length
                  ? `<div class="tags-wrap" style="margin-top:4px">${s.ambiguity_flags.map((fl) => `<span class="tag-chip">${esc(fl)}</span>`).join("")}</div>`
                  : "";
                return `<tr>
                  <td>
                    <div><strong>@${esc(s.source_handle || "?")}</strong> <span class="muted">→</span> <strong>${s.target_handle ? `@${esc(s.target_handle)}` : "general"}</strong></div>
                    <div style="margin-top:4px"><span class="badge badge-info">${esc(s.category)}</span> <span class="muted" style="font-size:11px">${esc(s.subcategory || "")} · ${esc(s.platform)}</span></div>
                  </td>
                  <td>
                    <div>Expresión: <code>${esc(s.matched_expression)}</code></div>
                    <div class="quote-item" style="margin-top:4px">“${esc((s.context_excerpt || "").slice(0, 220))}”</div>
                    ${s.evidence_url ? `<div style="margin-top:4px;font-size:11.5px"><a href="${esc(s.evidence_url)}" target="_blank" rel="noopener">Ver fuente original</a></div>` : ""}
                  </td>
                  <td>
                    <div>${_epistemicBadge(s.epistemic_type)}</div>
                    <div class="muted" style="font-size:11px;margin-top:4px">Confianza: ${esc(s.confidence)} · Motor: ${esc(s.engine)}</div>
                    ${flagsHtml}
                  </td>
                  <td>
                    <div style="margin-bottom:6px">${_reviewBadge(s.review_status)}</div>
                    <div class="inline-actions">
                      <button type="button" class="btn btn-xs ${s.review_status === "validated" ? "btn-primary" : "btn-ghost"}" data-rel-rev="validated" data-rel-id="${s.id}">Validar</button>
                      <button type="button" class="btn btn-xs ${s.review_status === "ambiguous" ? "btn-secondary" : "btn-ghost"}" data-rel-rev="ambiguous" data-rel-id="${s.id}">Ambiguo</button>
                      <button type="button" class="btn btn-xs ${s.review_status === "dismissed" ? "btn-danger" : "btn-ghost"}" data-rel-rev="dismissed" data-rel-id="${s.id}">Descartar</button>
                    </div>
                  </td>
                </tr>`;
              })
              .join("")}
          </tbody>
        </table>
      </div>`
    : `<div class="muted">No hay expresiones relacionales que coincidan con el filtro actual. Ejecuta el análisis arriba tras ingerir comentarios o publicaciones.</div>`;

  containerEl.innerHTML = `
    <div class="panel-card">
      <div class="panel-head">
        <div>
          <h2>Lenguaje Relacional Contextual (Familia, Amistad, Pareja)</h2>
          <span class="muted">Distingue evidencia explícita, expresiones coloquiales/ambiguas e inferencias del modelo · Nunca afirma vínculos sin revisión humana</span>
        </div>
        <div class="inline-actions">
          <span class="badge badge-verificado">${st.explicit || 0} explícitas</span>
          <span class="badge badge-revisar">${st.ambiguous || 0} ambiguas</span>
          <span class="badge badge-probable">${st.model_inference || 0} inferencias IA</span>
        </div>
      </div>

      <div class="inline-actions" style="justify-content:space-between;margin-bottom:12px;gap:10px">
        <form id="formRelAnalyze" class="inline-actions" style="gap:8px">
          <select id="relProvider" style="max-width:280px">
            <option value="local">Solo Reglas Locales (Regionalismos + Sarcasmo)</option>
            <option value="gemini">Híbrido · Revisar ambiguos con Gemini</option>
            <option value="auto">Híbrido Auto (LLM disponible)</option>
            <option value="ollama">Híbrido · Ollama local</option>
          </select>
          <button type="submit" class="btn btn-sm btn-primary">Analizar lenguaje relacional</button>
        </form>
        <div class="inline-actions" style="gap:8px">
          <select id="relFilterCat" style="max-width:155px">
            <option value="" ${!state.relCatFilter ? "selected" : ""}>Todas categorías</option>
            <option value="familia" ${state.relCatFilter === "familia" ? "selected" : ""}>Familia</option>
            <option value="amistad" ${state.relCatFilter === "amistad" ? "selected" : ""}>Amistad</option>
            <option value="pareja" ${state.relCatFilter === "pareja" ? "selected" : ""}>Pareja</option>
            <option value="laboral_academico" ${state.relCatFilter === "laboral_academico" ? "selected" : ""}>Laboral / Académico</option>
            <option value="afectivo_general" ${state.relCatFilter === "afectivo_general" ? "selected" : ""}>Afectivo general</option>
          </select>
          <select id="relFilterEpi" style="max-width:165px">
            <option value="" ${!state.relEpiFilter ? "selected" : ""}>Todos los niveles</option>
            <option value="explicit" ${state.relEpiFilter === "explicit" ? "selected" : ""}>Solo Explícitas</option>
            <option value="ambiguous" ${state.relEpiFilter === "ambiguous" ? "selected" : ""}>Solo Ambiguas</option>
            <option value="model_inference" ${state.relEpiFilter === "model_inference" ? "selected" : ""}>Solo Inferencia IA</option>
          </select>
        </div>
      </div>

      <div id="progress-caserel"></div>
      <div id="results-caserel"></div>
      ${rowsHtml}
    </div>
  `;

  document.getElementById("relFilterCat").addEventListener("change", (e) => {
    state.relCatFilter = e.target.value;
    renderCaseRelationalPanel(containerEl, caseId, relational);
  });
  document.getElementById("relFilterEpi").addEventListener("change", (e) => {
    state.relEpiFilter = e.target.value;
    renderCaseRelationalPanel(containerEl, caseId, relational);
  });
  document.getElementById("formRelAnalyze").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const out = await api(`/api/cases/${encodeURIComponent(caseId)}/relational/analyze`, {
        method: "POST",
        body: JSON.stringify({ provider: document.getElementById("relProvider").value }),
      });
      startJobPolling(out.job_id, "caserel", (job) => {
        if (job.result && job.result.signals) {
          renderCaseRelationalPanel(containerEl, caseId, { stats: job.result.stats, signals: job.result.signals });
        }
      });
    } catch (err) {
      toast(err.message, "err");
    }
  });
  containerEl.querySelectorAll("[data-rel-rev]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        const res = await api(`/api/cases/${encodeURIComponent(caseId)}/relational/${btn.dataset.relId}`, {
          method: "PATCH",
          body: JSON.stringify({ review_status: btn.dataset.relRev }),
        });
        renderCaseRelationalPanel(containerEl, caseId, res.relational);
        toast(`Señal marcada como: ${btn.dataset.relRev}`, "ok");
      } catch (err) {
        toast(err.message, "err");
      }
    });
  });
}

/** Orquesta el panel completo de detalle del caso (semillas, perfiles, ingestas, análisis y acciones). */
export function renderCaseDetail(detail, content = null, vision = null, nlp = null, relational = null, evidences = null) {
  const pane = document.getElementById("caseDetailPane");
  if (!pane || !detail || !detail.case) return;

  const c = detail.case;
  const idents = detail.identifiers || [];
  const profiles = detail.profiles || [];
  const mediaItems = detail.media_items || [];
  const runs = detail.runs || [];
  const res = detail.resolution || {};
  const stats = res.stats || { total: profiles.length, verified: 0, probable: 0, review: 0, rejected: 0, avg_score: 0 };
  const warnings = res.warnings || [];
  const crossLinks = res.cross_links || [];
  const isActive = state.activeCaseId === c.id;

  const warningsHtml = warnings.length
    ? `<div class="panel-card">
        <div class="panel-head"><h2>Alertas de homónimos / consistencia</h2><span class="badge badge-revisar">${warnings.length}</span></div>
        <div class="list-stack">${warnings.map((w) => `<div class="alert-box alert-warn">${esc(w)}</div>`).join("")}</div>
      </div>`
    : "";

  const crossLinksHtml = crossLinks.length
    ? `<div class="panel-card">
        <div class="panel-head"><h2>Señales cruzadas entre perfiles</h2><span class="badge badge-verificado">${crossLinks.length}</span></div>
        <div class="list-stack">
          ${crossLinks
            .map(
              (lk) => `<div class="report-row">
                <div><span class="badge ${lk.strength === "ALTA" ? "badge-verificado" : "badge-probable"}">${esc(lk.type)}</span> <strong>${esc(lk.from_site)} ↔ ${esc(lk.to_site)}</strong></div>
                <span class="muted">${esc(lk.detail)}</span>
              </div>`
            )
            .join("")}
        </div>
      </div>`
    : "";

  const identsHtml = idents.length
    ? `<div class="tags-wrap" style="margin-bottom:12px">
        ${idents
          .map(
            (i) => `<span class="ident-chip">
              <span class="ident-kind">${esc(i.kind)}</span>
              <span>${esc(i.value)}</span>
              <button type="button" data-del-ident="${i.id}" title="Eliminar semilla">&times;</button>
            </span>`
          )
          .join("")}
      </div>`
    : `<div class="muted" style="margin-bottom:12px">Sin semillas registradas todavía.</div>`;

  const profilesHtml = profiles.length
    ? `<div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>Sitio / Perfil</th>
              <th>Resolución</th>
              <th>Evidencia explicable</th>
              <th>Control analista</th>
            </tr>
          </thead>
          <tbody>
            ${profiles
              .map((p) => {
                const color = _scoreColor(p.resolution_score || 0, p.resolution_level);
                const reasons = (p.resolution_reasons || []).slice(0, 4);
                return `
                  <tr>
                    <td>
                      <div><strong>${esc(p.site)}</strong> ${p.username ? `<span class="muted">@${esc(p.username)}</span>` : ""}</div>
                      <div style="font-size:12px"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.url)}</a></div>
                      ${p.display_name ? `<div class="muted" style="font-size:11.5px">Nombre: ${esc(p.display_name)}</div>` : ""}
                      ${p.location ? `<div class="muted" style="font-size:11.5px">Ubicación: ${esc(p.location)}</div>` : ""}
                      ${p.bio ? `<div class="muted" style="font-size:11.5px">Bio: ${esc(p.bio.slice(0, 120))}</div>` : ""}
                    </td>
                    <td>
                      <div style="margin-bottom:6px">${_levelBadge(p.resolution_level)}</div>
                      <div class="score-bar-wrap">
                        <div class="score-bar"><div class="score-bar-fill" style="width:${Number(p.resolution_score || 0)}%;background:${color}"></div></div>
                        <span class="score-num" style="color:${color}">${Number(p.resolution_score || 0)}</span>
                      </div>
                      <div class="muted" style="font-size:11px;margin-top:4px">${esc(p.sources || "OSINT")}</div>
                    </td>
                    <td>
                      ${reasons.length
                        ? `<ul class="reasons-list">${reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`
                        : `<span class="muted">Sin señales adicionales</span>`}
                    </td>
                    <td>
                      <div class="inline-actions">
                        <button type="button" class="btn btn-xs ${p.manual_status === "confirmed" ? "btn-primary" : "btn-ghost"}" data-prof-status="confirmed" data-prof-id="${p.id}">Confirmar</button>
                        <button type="button" class="btn btn-xs ${p.manual_status === "rejected" ? "btn-danger" : "btn-ghost"}" data-prof-status="rejected" data-prof-id="${p.id}">Descartar</button>
                        ${p.manual_status !== "auto" ? `<button type="button" class="btn btn-xs btn-ghost" data-prof-status="auto" data-prof-id="${p.id}">Auto</button>` : ""}
                      </div>
                    </td>
                  </tr>
                `;
              })
              .join("")}
          </tbody>
        </table>
      </div>`
    : `<div class="muted">Aún no hay perfiles en este caso. Activa este caso y ejecuta una búsqueda, o añade un perfil manualmente abajo.</div>`;

  const runsHtml = runs.length
    ? runs
        .map(
          (r) => `<div class="report-row">
            <div>
              <span class="badge badge-info">${esc(r.kind)}</span>
              <strong>${esc(r.target)}</strong>
              <span class="muted">${esc(r.created_at)}</span>
            </div>
            <div class="inline-actions">
              ${r.folder ? `<a class="btn btn-xs btn-ghost" href="/api/reports/${encodeURIComponent(r.folder)}/resumen.html" target="_blank" rel="noopener">Ver HTML</a>` : ""}
            </div>
          </div>`
        )
        .join("")
    : `<div class="muted">Sin ejecuciones registradas en este caso todavía.</div>`;

  const mediaHtml = mediaItems.length
    ? `<div class="panel-card">
        <div class="panel-head"><h2>Media analizada (${mediaItems.length})</h2></div>
        <div class="list-stack">
          ${mediaItems
            .map(
              (m) => `<div class="report-row">
                <div>
                  <span class="badge badge-info">${esc(m.media_type)}</span>
                  <strong>${esc(m.filename)}</strong>
                  ${m.gps_lat != null ? `<span class="badge badge-baja">GPS: ${m.gps_lat}, ${m.gps_lon}</span>` : ""}
                  <div class="muted" style="font-size:11.5px">SHA-256: <code>${esc((m.sha256 || "").slice(0, 18))}…</code> ${m.device ? `· ${esc(m.device)}` : ""}</div>
                </div>
                <div class="inline-actions">
                  ${m.clean_copy_url ? `<a class="btn btn-xs btn-primary" href="${esc(m.clean_copy_url)}" download>Copia limpia</a>` : ""}
                </div>
              </div>`
            )
            .join("")}
        </div>
      </div>`
    : "";

  const platformsList = state.platformCaps.length
    ? state.platformCaps
    : [
        { id: "bluesky", name: "Bluesky (ATProto)", auth_mode: "public_api", ready: true, placeholder: "ej: usuario.bsky.social", supports_profile: true, supports_posts: true, supports_comments: true, supports_reactions: true, supports_third_party_public: true, limitations: "Acceso público abierto.", active_connections: [] },
        { id: "mastodon", name: "Mastodon / Fediverso", auth_mode: "public_api", ready: true, placeholder: "ej: @usuario@mastodon.social", supports_profile: true, supports_posts: true, supports_comments: true, supports_reactions: true, supports_third_party_public: true, limitations: "Acceso público federado.", active_connections: [] },
        { id: "reddit", name: "Reddit (API pública / OAuth2)", auth_mode: "public_api", ready: true, placeholder: "ej: u/usuario", supports_profile: true, supports_posts: true, supports_comments: true, supports_reactions: true, supports_third_party_public: true, limitations: "Soporta JSON público y OAuth2 oficial.", active_connections: [] },
        { id: "github", name: "GitHub (REST API)", auth_mode: "public_api", ready: true, placeholder: "ej: usuario", supports_profile: true, supports_posts: true, supports_comments: true, supports_reactions: false, supports_third_party_public: true, limitations: "Actividad pública reciente.", active_connections: [] },
        { id: "youtube", name: "YouTube Data API v3", auth_mode: "api_key", ready: false, placeholder: "ej: @canal", supports_profile: true, supports_posts: true, supports_comments: true, supports_reactions: true, supports_third_party_public: true, limitations: "Requiere YOUTUBE_API_KEY en .env.", active_connections: [] },
        { id: "instagram_pro", name: "Instagram Professional (Meta OAuth2)", auth_mode: "oauth2", ready: false, placeholder: "ej: @mi_cuenta_business", supports_profile: true, supports_posts: true, supports_comments: true, supports_reactions: true, supports_third_party_public: false, limitations: "Solo cuentas Profesionales autorizadas vía Meta OAuth2.", active_connections: [] },
        { id: "facebook_page", name: "Facebook Page (Meta OAuth2)", auth_mode: "oauth2", ready: false, placeholder: "ej: nombre o ID de Página", supports_profile: true, supports_posts: true, supports_comments: true, supports_reactions: true, supports_third_party_public: false, limitations: "Solo Páginas administradas vía Meta OAuth2.", active_connections: [] },
      ];
  const defaultSeedUser = (idents.find((i) => i.kind === "username") || {}).value || "";

  const ingestPhase2Html = `
    <div class="panel-card">
      <div class="panel-head">
        <h2>Investigación Autorizada de Cuenta e Importador</h2>
        <span class="badge badge-verificado">APIs Oficiales + OAuth2 · Sin contraseñas</span>
      </div>
      <div class="two-col">
        <form id="formCaseConnector" style="display:flex;flex-direction:column;gap:10px">
          <div class="muted" style="font-size:12px"><strong>1. Investigar cuenta automáticamente</strong> (perfil, posts, comentarios, reacciones, lenguaje relacional, NLP y SNA)</div>
          <div class="field">
            <label for="ingPlatform">Plataforma autorizada</label>
            <select id="ingPlatform">
              ${platformsList
                .map(
                  (cn) => `<option value="${esc(cn.id)}" data-ph="${esc(cn.placeholder || "")}">${esc(cn.name)}${cn.ready ? "" : ` (${cn.auth_mode === "oauth2" ? "requiere OAuth2" : "requiere API key"})`}</option>`
                )
                .join("")}
            </select>
          </div>
          <div id="ingPlatformCapsBox" class="interest-card" style="padding:9px 11px;font-size:11.5px"></div>
          <div class="field">
            <label for="ingHandle">Handle / Usuario / Canal / Página *</label>
            <input id="ingHandle" type="text" value="${esc(defaultSeedUser)}" placeholder="${esc(platformsList[0].placeholder || "")}" required />
          </div>
          <div class="inline-actions">
            <select id="ingLimit" style="max-width:125px">
              <option value="10">10 posts</option>
              <option value="25" selected>25 posts</option>
              <option value="40">40 posts</option>
            </select>
            <label class="checkbox-label" style="font-size:12px"><input type="checkbox" id="ingReplies" checked /><span>Comentarios</span></label>
            <label class="checkbox-label" style="font-size:12px"><input type="checkbox" id="ingReactions" checked /><span>Reacciones</span></label>
            <label class="checkbox-label" style="font-size:12px"><input type="checkbox" id="ingRunRel" checked /><span>Lenguaje relacional</span></label>
          </div>
          <div class="inline-actions">
            <button type="submit" class="btn btn-sm btn-primary">Investigar cuenta automáticamente</button>
            <button type="button" class="btn btn-xs btn-ghost hidden" id="btnStartOAuth">Conectar OAuth2 oficial</button>
          </div>
          <div id="progress-caseingest"></div>
          <div id="results-caseingest"></div>
        </form>

        <form id="formCaseImport" style="display:flex;flex-direction:column;gap:10px">
          <div class="muted" style="font-size:12px"><strong>2. Alternativa para redes personales cerradas: Importar exportación oficial (Meta JSON/ZIP, WhatsApp TXT o CSV)</strong></div>
          <div class="field">
            <label for="impFile">Archivo .json, .zip, .txt (WhatsApp) o .csv *</label>
            <input id="impFile" type="file" accept=".json,.csv,.zip,.txt" required />
          </div>
          <div class="form-grid" style="grid-template-columns:1fr 1fr;gap:8px">
            <div class="field">
              <label for="impPlatform">Etiqueta de fuente</label>
              <input id="impPlatform" type="text" value="exportacion_propia" />
            </div>
            <div class="field">
              <label for="impSubject">Usuario sujeto (opcional)</label>
              <input id="impSubject" type="text" value="${esc(defaultSeedUser)}" placeholder="ej: mi_usuario" />
            </div>
          </div>
          <div class="inline-actions">
            <button type="submit" class="btn btn-sm btn-secondary" id="btnRunImport">Importar al caso</button>
            <a class="btn btn-xs btn-ghost" href="/api/ingest/template" download="plantilla_osint_import.json">Descargar plantilla JSON</a>
          </div>
        </form>
      </div>
    </div>

    <div id="caseVisionSection"></div>
    <div id="caseNlpSection"></div>
    <div id="caseRelationalSection"></div>
    <div id="caseSnaPreviewSection"></div>
    <div id="caseReportSection"></div>

    <div class="panel-card">
      <div class="panel-head">
        <h2>Contenido Ingerido y Evidencias del Caso</h2>
        <span class="muted">Publicaciones, galería, comentarios, interacciones, snapshots de perfil y trazabilidad</span>
      </div>
      <div id="caseContentViewer"></div>
    </div>
  `;

  pane.innerHTML = `
    <div class="panel-card">
      <div class="panel-head">
        <div>
          <h2 style="margin-bottom:2px">${esc(c.title)}</h2>
          <div class="muted">Entidad principal: <strong>${esc(c.subject_name || c.title)}</strong> · Propósito: ${esc(c.purpose)} · Creado: ${esc(c.created_at)}</div>
          ${c.notes ? `<div class="muted" style="margin-top:4px">${esc(c.notes)}</div>` : ""}
        </div>
        <div class="inline-actions">
          <button type="button" class="btn btn-sm btn-primary" id="btnScanCaseSeeds" title="Ejecuta Maigret + Holehe + Ignorant + Brechas usando las semillas registradas en este caso">
            Rastrear OSINT con semillas
          </button>
          <button type="button" class="btn btn-sm ${isActive ? "btn-secondary" : "btn-ghost"}" id="btnActivateCase">
            ${isActive ? "Caso activo ✓" : "Activar para búsquedas"}
          </button>
          <button type="button" class="btn btn-sm btn-ghost" id="btnRecalcCase">Recalcular resolución</button>
          <button type="button" class="btn btn-sm btn-ghost" id="btnToggleCaseStatus">${c.status === "open" ? "Cerrar caso" : "Reabrir caso"}</button>
          <button type="button" class="btn btn-sm btn-danger" id="btnDeleteCase">Eliminar</button>
        </div>
      </div>

      <div id="progress-casescan" style="margin-top:10px"></div>
      <div id="results-casescan"></div>

      <div class="kpi-grid" style="margin-top:14px">
        <div class="kpi-card"><div class="kpi-label">Perfiles totales</div><div class="kpi-value">${stats.total || 0}</div><div class="kpi-meta">${stats.verified || 0} verificados</div></div>
        <div class="kpi-card"><div class="kpi-label">Publicaciones</div><div class="kpi-value" style="color:var(--accent)">${(detail.content_counts || {}).posts || 0}</div><div class="kpi-meta">${(detail.content_counts || {}).media || 0} fotos/media</div></div>
        <div class="kpi-card"><div class="kpi-label">Comentarios</div><div class="kpi-value" style="color:var(--violet)">${(detail.content_counts || {}).comments || 0}</div><div class="kpi-meta">${(detail.content_counts || {}).interactions || 0} interacciones</div></div>
        <div class="kpi-card"><div class="kpi-label">Score promedio</div><div class="kpi-value" style="color:var(--emerald)">${stats.avg_score || 0}</div><div class="kpi-meta">${stats.cross_links_count || 0} señales cruzadas</div></div>
      </div>
    </div>

    ${ingestPhase2Html}
    ${warningsHtml}
    ${crossLinksHtml}

    <div class="panel-card">
      <div class="panel-head">
        <h2>Semillas / Identificadores de la entidad</h2>
        <span class="muted">${idents.length} registrados</span>
      </div>
      ${identsHtml}
      <form id="formAddIdent" class="inline-actions" style="gap:8px">
        <select id="addIdentKind" style="max-width:150px">
          <option value="username">Username</option>
          <option value="full_name">Nombre completo</option>
          <option value="email">Correo</option>
          <option value="phone">Teléfono</option>
          <option value="location">Ubicación</option>
          <option value="domain">Dominio</option>
        </select>
        <input id="addIdentVal" type="text" placeholder="Valor de la semilla..." required style="flex:1;min-width:180px" />
        <button type="submit" class="btn btn-sm btn-primary">Añadir semilla</button>
      </form>
    </div>

    <div class="panel-card">
      <div class="panel-head">
        <h2>Perfiles y Resolución de Entidades</h2>
        <span class="muted">Ordenados por puntaje de coincidencia</span>
      </div>
      ${profilesHtml}

      <details style="margin-top:14px">
        <summary class="muted" style="cursor:pointer">+ Añadir perfil público manualmente al caso</summary>
        <form id="formAddProfile" class="form-grid" style="margin-top:10px">
          <div class="field"><label>Plataforma *</label><input id="apSite" type="text" placeholder="ej: GitHub, Reddit, Bluesky" required /></div>
          <div class="field"><label>URL pública *</label><input id="apUrl" type="url" placeholder="https://..." required /></div>
          <div class="field"><label>Username</label><input id="apUser" type="text" placeholder="opcional" /></div>
          <div class="field"><label>Nombre visible</label><input id="apName" type="text" placeholder="opcional" /></div>
          <div class="field"><label>Ubicación</label><input id="apLoc" type="text" placeholder="ej: Bogotá" /></div>
          <div class="field"><label>Sitio web / enlace en bio</label><input id="apWeb" type="text" placeholder="https://..." /></div>
          <div class="field span-2"><label>Biografía pública</label><input id="apBio" type="text" placeholder="Texto de la bio pública..." /></div>
          <div class="form-actions span-2"><button type="submit" class="btn btn-sm btn-primary">Guardar perfil y recalcular</button></div>
        </form>
      </details>
    </div>

    ${mediaHtml}

    <div class="panel-card">
      <div class="panel-head">
        <h2>Bitácora de ejecuciones del caso</h2>
        <span class="muted">${runs.length} registros</span>
      </div>
      <div class="list-stack">${runsHtml}</div>
    </div>
  `;

  // Render de Galería y Análisis Visual (Fase 3)
  const visionSec = document.getElementById("caseVisionSection");
  if (vision) {
    renderCaseVisionPanel(visionSec, c.id, vision);
  } else {
    api(`/api/cases/${encodeURIComponent(c.id)}/vision`)
      .then((freshVision) => renderCaseVisionPanel(visionSec, c.id, freshVision))
      .catch(() => {});
  }

  // Render de Análisis NLP (Fase 4)
  const nlpSec = document.getElementById("caseNlpSection");
  if (nlp) {
    renderCaseNlpPanel(nlpSec, c.id, nlp);
  } else {
    api(`/api/cases/${encodeURIComponent(c.id)}/nlp`)
      .then((freshNlp) => renderCaseNlpPanel(nlpSec, c.id, freshNlp))
      .catch(() => {});
  }

  // Render de Lenguaje Relacional Contextual
  const relSec = document.getElementById("caseRelationalSection");
  if (relational) {
    renderCaseRelationalPanel(relSec, c.id, relational);
  } else {
    api(`/api/cases/${encodeURIComponent(c.id)}/relational`)
      .then((freshRel) => renderCaseRelationalPanel(relSec, c.id, freshRel))
      .catch(() => {});
  }

  // Render de Vista Previa SNA (Fase 5) con acceso directo a la Vista Secundaria
  const snaPrev = document.getElementById("caseSnaPreviewSection");
  if (snaPrev) {
    renderCaseSnaPreview(snaPrev, c.id);
  }

  // Render de Reporte Integral y Evaluación de Exposición (Fase 6)
  const repSec = document.getElementById("caseReportSection");
  if (repSec) {
    renderCaseReportPanel(repSec, c.id);
  }

  // Render del Visor de Contenido y Evidencias
  const contentViewer = document.getElementById("caseContentViewer");
  if (content) {
    renderCaseContentViewer(contentViewer, content, evidences);
  } else {
    Promise.all([
      api(`/api/cases/${encodeURIComponent(c.id)}/content`),
      api(`/api/cases/${encodeURIComponent(c.id)}/evidences`),
    ])
      .then(([freshContent, freshEv]) => renderCaseContentViewer(contentViewer, freshContent, freshEv))
      .catch(() => {});
  }

  // Matriz interactiva de capacidades por plataforma + botón OAuth2 oficial
  const ingPlatformSel = document.getElementById("ingPlatform");
  const ingHandleInp = document.getElementById("ingHandle");
  const capsBox = document.getElementById("ingPlatformCapsBox");
  const btnOAuth = document.getElementById("btnStartOAuth");

  const renderSelectedPlatformCaps = () => {
    if (!ingPlatformSel || !capsBox) return;
    const platId = ingPlatformSel.value;
    const cap = platformsList.find((x) => x.id === platId) || platformsList[0];
    if (!cap) return;
    if (ingHandleInp && cap.placeholder) ingHandleInp.placeholder = cap.placeholder;
    const conns = cap.active_connections || [];
    const pill = (ok, lbl) => `<span class="badge ${ok ? "badge-verificado" : "badge-revisar"}">${esc(lbl)} ${ok ? "✓" : "✗"}</span>`;
    capsBox.innerHTML = `
      <div class="tags-wrap">
        ${pill(cap.supports_profile, "Perfil")}
        ${pill(cap.supports_posts, "Posts")}
        ${pill(cap.supports_comments, "Comentarios")}
        ${pill(cap.supports_reactions, "Reacciones")}
        ${pill(cap.supports_third_party_public, "Terceros públicos")}
      </div>
      <div class="muted" style="margin-top:4px">${esc(cap.limitations || "")}</div>
      ${conns.length ? `<div style="margin-top:4px;color:var(--emerald)">Conexión OAuth2 activa: <strong>${esc(conns[0].account_label)}</strong></div>` : ""}
    `;
    const supportsOAuth = ["instagram_pro", "facebook_page", "reddit"].includes(platId);
    if (btnOAuth) {
      btnOAuth.classList.toggle("hidden", !supportsOAuth);
      btnOAuth.textContent = conns.length ? `Reconectar OAuth2 (${platId})` : `Conectar OAuth2 (${platId})`;
    }
  };
  if (ingPlatformSel) {
    ingPlatformSel.addEventListener("change", renderSelectedPlatformCaps);
    renderSelectedPlatformCaps();
  }
  if (btnOAuth) {
    btnOAuth.addEventListener("click", async () => {
      const platId = ingPlatformSel.value;
      try {
        const res = await api(`/api/auth/${encodeURIComponent(platId)}/start`);
        window.open(res.auth_url, "_blank", "noopener");
        toast(`Abriendo autorización OAuth2 oficial para ${platId}...`, "ok");
      } catch (err) {
        toast(err.message, "err");
      }
    });
  }

  // Submit: Investigación integral autorizada de cuenta
  const formConn = document.getElementById("formCaseConnector");
  if (formConn) {
    formConn.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!requireConsent()) return;
      try {
        const data = await api(`/api/cases/${encodeURIComponent(c.id)}/investigate/account`, {
          method: "POST",
          body: JSON.stringify({
            platform: document.getElementById("ingPlatform").value,
            handle: document.getElementById("ingHandle").value.trim(),
            limit: Number(document.getElementById("ingLimit").value),
            include_replies: document.getElementById("ingReplies").checked,
            include_reactions: document.getElementById("ingReactions").checked,
            run_relational: document.getElementById("ingRunRel").checked,
            relational_provider: "local",
            run_nlp: true,
            nlp_provider: "local",
            run_sna: true,
            confirm_legitimate: true,
          }),
        });
        startJobPolling(data.job_id, "caseingest", (job, container) => {
          const r = job.result || {};
          const ing = r.ingest || {};
          const rel = r.relational || {};
          const warns = r.warnings || [];
          container.innerHTML =
            `<div class="alert-box alert-safe">Investigación completada: <strong>${ing.posts_inserted || 0}</strong> posts, <strong>${ing.comments_inserted || 0}</strong> comentarios, <strong>${ing.reactions_inserted || 0}</strong> reacciones, <strong>${rel.total || 0}</strong> señales relacionales.</div>` +
            (warns.length ? `<div class="alert-box alert-warn mt-12">${esc(warns.join(" · "))}</div>` : "");
        });
      } catch (err) {
        toast(err.message, "err");
      }
    });
  }

  // Submit: Importador JSON / CSV (Fase 2)
  const formImp = document.getElementById("formCaseImport");
  if (formImp) {
    formImp.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fInp = document.getElementById("impFile");
      if (!fInp.files || !fInp.files[0]) return;
      const btn = document.getElementById("btnRunImport");
      btn.disabled = true;
      try {
        const fd = new FormData();
        fd.append("file", fInp.files[0]);
        fd.append("platform_label", document.getElementById("impPlatform").value.trim() || "exportacion_propia");
        fd.append("subject_handle", document.getElementById("impSubject").value.trim());
        const data = await apiFormData(`/api/cases/${encodeURIComponent(c.id)}/ingest/import`, fd);
        const s = data.summary || {};
        toast(`Importado: ${s.posts_inserted || 0} posts, ${s.comments_inserted || 0} comentarios, ${s.media_inserted || 0} media.`, "ok");
        await loadCases();
        renderCaseDetail(data.detail, data.content);
      } catch (err) {
        toast(err.message, "err");
      } finally {
        btn.disabled = false;
      }
    });
  }

  // Eventos del panel de detalle
  const btnScanSeeds = document.getElementById("btnScanCaseSeeds");
  if (btnScanSeeds) {
    btnScanSeeds.addEventListener("click", async () => {
      if (!requireConsent()) return;
      const seedUser = (idents.find((i) => i.kind === "username") || {}).value || "";
      const seedEmail = (idents.find((i) => i.kind === "email") || {}).value || "";
      const seedPhone = (idents.find((i) => i.kind === "phone") || {}).value || "";
      const seedName = (idents.find((i) => i.kind === "full_name") || {}).value || c.subject_name || "";
      const seedLoc = (idents.find((i) => i.kind === "location") || {}).value || "";

      try {
        let data = null;
        if (seedUser) {
          data = await api("/api/search/full", {
            method: "POST",
            body: JSON.stringify({
              username: seedUser,
              known_email: seedEmail || null,
              phone: seedPhone || null,
              case_id: c.id,
              confirm_legitimate: true,
            }),
          });
        } else if (seedEmail) {
          data = await api("/api/search/email", {
            method: "POST",
            body: JSON.stringify({ target: seedEmail, mode: "osint", case_id: c.id, confirm_legitimate: true }),
          });
        } else if (seedPhone) {
          data = await api("/api/search/phone", {
            method: "POST",
            body: JSON.stringify({ phone: seedPhone, case_id: c.id, confirm_legitimate: true }),
          });
        } else if (seedName) {
          data = await api("/api/search/name", {
            method: "POST",
            body: JSON.stringify({ full_name: seedName, city: seedLoc || null, case_id: c.id, confirm_legitimate: true }),
          });
        } else {
          toast("Añade al menos una semilla (username, correo, teléfono o nombre) para rastrear.", "err");
          return;
        }
        toast("Rastreo OSINT lanzado para este caso...", "ok");
        startJobPolling(data.job_id, "casescan", (job, container) => {
          const ing = job.case_ingestion || {};
          container.innerHTML = `<div class="alert-box alert-safe">Rastreo OSINT finalizado y guardado en el caso: <strong>${ing.profiles_ingested || 0}</strong> perfiles y <strong>${ing.identifiers_ingested || 0}</strong> identificadores consolidados.</div>`;
        });
      } catch (err) {
        toast(err.message, "err");
      }
    });
  }

  document.getElementById("btnActivateCase").addEventListener("click", () => {
    setActiveCaseId(isActive ? "" : c.id);
    toast(isActive ? "Caso desactivado para nuevas búsquedas." : `Caso activo: ${c.title}`, "ok");
    renderCasesList();
    renderCaseDetail(detail, content);
  });

  document.getElementById("btnRecalcCase").addEventListener("click", async () => {
    try {
      const out = await api(`/api/cases/${encodeURIComponent(c.id)}/resolve`, { method: "POST" });
      renderCaseDetail(out.detail);
      loadCases();
      toast("Resolución de entidades recalculada.", "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  });

  document.getElementById("btnToggleCaseStatus").addEventListener("click", async () => {
    const nextStatus = c.status === "open" ? "closed" : "open";
    try {
      const updated = await api(`/api/cases/${encodeURIComponent(c.id)}`, {
        method: "PATCH",
        body: JSON.stringify({ status: nextStatus }),
      });
      renderCaseDetail(updated);
      loadCases();
      toast(nextStatus === "closed" ? "Caso cerrado." : "Caso reabierto.", "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  });

  document.getElementById("btnDeleteCase").addEventListener("click", async () => {
    if (!confirm(`¿Eliminar el caso "${c.title}" y todos sus registros en SQLite?`)) return;
    const alsoFiles = confirm("¿Quieres borrar también las carpetas de reportes en output/ asociadas a este caso?");
    try {
      await api(`/api/cases/${encodeURIComponent(c.id)}?delete_files=${alsoFiles ? "true" : "false"}`, {
        method: "DELETE",
      });
      if (state.activeCaseId === c.id) setActiveCaseId("");
      state.selectedCaseId = "";
      pane.innerHTML = `<div class="panel-card"><div class="muted">Caso eliminado correctamente.</div></div>`;
      loadCases();
      loadReports();
      loadStatus();
      toast("Caso eliminado.", "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  });

  document.getElementById("formAddIdent").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const out = await api(`/api/cases/${encodeURIComponent(c.id)}/identifiers`, {
        method: "POST",
        body: JSON.stringify({
          kind: document.getElementById("addIdentKind").value,
          value: document.getElementById("addIdentVal").value.trim(),
          source: "manual",
          confidence: "ALTA",
        }),
      });
      renderCaseDetail(out.detail);
      loadCases();
      toast("Semilla añadida y resolución actualizada.", "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  });

  pane.querySelectorAll("[data-del-ident]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        const out = await api(`/api/cases/${encodeURIComponent(c.id)}/identifiers/${btn.dataset.delIdent}`, {
          method: "DELETE",
        });
        renderCaseDetail(out.detail);
        loadCases();
      } catch (err) {
        toast(err.message, "err");
      }
    });
  });

  pane.querySelectorAll("[data-prof-status]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        const out = await api(`/api/cases/${encodeURIComponent(c.id)}/profiles/${btn.dataset.profId}`, {
          method: "PATCH",
          body: JSON.stringify({ manual_status: btn.dataset.profStatus }),
        });
        renderCaseDetail(out.detail);
        loadCases();
      } catch (err) {
        toast(err.message, "err");
      }
    });
  });

  document.getElementById("formAddProfile").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const out = await api(`/api/cases/${encodeURIComponent(c.id)}/profiles`, {
        method: "POST",
        body: JSON.stringify({
          site: document.getElementById("apSite").value.trim(),
          url: document.getElementById("apUrl").value.trim(),
          username: document.getElementById("apUser").value.trim(),
          display_name: document.getElementById("apName").value.trim(),
          location: document.getElementById("apLoc").value.trim(),
          website: document.getElementById("apWeb").value.trim(),
          bio: document.getElementById("apBio").value.trim(),
          sources: "manual",
          osint_confidence: "ALTA",
        }),
      });
      renderCaseDetail(out.detail);
      loadCases();
      toast("Perfil añadido al caso.", "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  });
}


// ---------------------------------------------------------------------------
// Fase 6 :: Reporte Integral, Evaluación de Exposición y Gobernanza de Temporales
// ---------------------------------------------------------------------------
/** Carga el puntaje de exposición y genera el Reporte Integral del Caso (HTML/PDF/JSON). */
export async function renderCaseReportPanel(containerEl, caseId) {
  if (!containerEl) return;
  containerEl.innerHTML = `<div class="panel-card"><div class="muted">Calculando evaluación defensiva de exposición...</div></div>`;
  try {
    const exp = await api(`/api/cases/${encodeURIComponent(caseId)}/exposure`);
    const expColor =
      exp.level === "CRÍTICA" || exp.level === "ALTA" ? "var(--rose)" :
      exp.level === "MODERADA" ? "var(--amber)" : "var(--emerald)";
    const breakdownChips = Object.entries(exp.breakdown || {})
      .map(([k, v]) => `<span class="tag-chip">${esc(k)}: ${v} pts</span>`)
      .join("");

    containerEl.innerHTML = `
      <div class="panel-card">
        <div class="panel-head">
          <div>
            <h2>Reporte Integral y Evaluación de Exposición (Fase 6)</h2>
            <span class="muted">Consolida Fases 1–5 en HTML + PDF + JSON con anonimización opcional de terceros</span>
          </div>
          <span class="badge ${exp.level === "CRÍTICA" || exp.level === "ALTA" ? "badge-descartado" : exp.level === "MODERADA" ? "badge-revisar" : "badge-verificado"}">
            Exposición ${esc(exp.level)} (${exp.score}/100)
          </span>
        </div>

        <div class="privacy-meter-bg">
          <div class="privacy-meter-fill" style="width:${exp.score}%;background:${expColor}"></div>
        </div>
        <div class="tags-wrap" style="margin-bottom:12px">${breakdownChips}</div>

        <div class="two-col" style="margin-bottom:14px">
          <div class="interest-card">
            <strong>Factores de exposición detectados</strong>
            <ul class="reasons-list">${(exp.findings || []).map((f) => `<li>${esc(f)}</li>`).join("")}</ul>
          </div>
          <div class="interest-card">
            <strong>Recomendaciones defensivas de privacidad</strong>
            <ul class="reasons-list">${(exp.recommendations || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
          </div>
        </div>

        <form id="formGenerateCaseReport" class="interest-card" style="gap:10px">
          <div style="font-weight:600;color:var(--text-strong)">Exportar Reporte Integral del Caso</div>
          <div class="inline-actions" style="gap:16px">
            <label class="checkbox-label" style="font-size:12.5px">
              <input type="checkbox" id="repAnonThird" checked />
              <span><strong>Anonimizar terceros</strong> (reemplaza cuentas ajenas por Tercero_01, Tercero_02...)</span>
            </label>
            <label class="checkbox-label" style="font-size:12.5px">
              <input type="checkbox" id="repRevealFriends" />
              <span>Revelar nombres solo de amigos confirmados (opt-in auditado)</span>
            </label>
            <label class="checkbox-label" style="font-size:12.5px">
              <input type="checkbox" id="repIncludeQuotes" checked />
              <span>Incluir citas literales de evidencia</span>
            </label>
            <label class="checkbox-label" style="font-size:12.5px">
              <input type="checkbox" id="repIncludeGallery" checked />
              <span>Incluir resumen visual y galería</span>
            </label>
          </div>
          <div class="inline-actions">
            <button type="submit" class="btn btn-sm btn-primary" id="btnGenIntegralReport">Generar Reporte Integral (HTML + PDF + JSON)</button>
          </div>
          <div id="caseReportExportLinks"></div>
        </form>
      </div>
    `;

    document.getElementById("formGenerateCaseReport").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = document.getElementById("btnGenIntegralReport");
      const out = document.getElementById("caseReportExportLinks");
      btn.disabled = true;
      out.innerHTML = `<div class="muted">Generando reporte integral HTML, PDF y JSON...</div>`;
      try {
        const res = await api(`/api/cases/${encodeURIComponent(caseId)}/report`, {
          method: "POST",
          body: JSON.stringify({
            anonymize_third_parties: document.getElementById("repAnonThird").checked,
            reveal_confirmed_friends: Boolean(document.getElementById("repRevealFriends")?.checked),
            include_quotes: document.getElementById("repIncludeQuotes").checked,
            include_gallery: document.getElementById("repIncludeGallery").checked,
          }),
        });
        const anonMsg = res.governance && res.governance.anonymize_third_parties
          ? ` (${res.governance.anonymized_count || 0} terceros anonimizados)`
          : " (sin anonimización)";
        out.innerHTML = `
          <div class="alert-box alert-safe">
            <div style="margin-bottom:8px"><strong>Reporte integral listo</strong>${esc(anonMsg)} · Carpeta: <code>${esc(res.folder)}</code></div>
            <div class="inline-actions">
              <a class="btn btn-xs btn-primary" href="${esc(res.html_url)}" target="_blank" rel="noopener">Abrir HTML</a>
              ${res.pdf_url ? `<a class="btn btn-xs btn-secondary" href="${esc(res.pdf_url)}" target="_blank" rel="noopener">Descargar PDF</a>` : ""}
              <a class="btn btn-xs btn-ghost" href="${esc(res.json_url)}" download>Descargar JSON</a>
            </div>
          </div>
        `;
        loadReports();
        toast("Reporte integral generado correctamente.", "ok");
      } catch (err) {
        out.innerHTML = `<div class="alert-box alert-danger">${esc(err.message)}</div>`;
        toast(err.message, "err");
      } finally {
        btn.disabled = false;
      }
    });
  } catch (err) {
    containerEl.innerHTML = "";
  }
}

/** Consulta y muestra métricas de almacenamiento (`output/`, SQLite y cachés de navegador). */
export async function loadStorageMaintenance() {
  const body = document.getElementById("storageGovernanceBody");
  if (!body) return;
  try {
    const s = await api("/api/maintenance/storage");
    body.innerHTML = `
      <div class="case-item-meta" style="font-size:12.5px">
        <span>Carpetas en <code>output/</code>: <strong>${s.output_folders_count}</strong> (${s.output_size_mb} MB)</span>
        <span>Vinculadas a casos: <strong>${s.referenced_folders_count}</strong></span>
        <span>Temporales huérfanos: <strong style="color:${s.orphan_folders_count ? "var(--amber)" : "var(--emerald)"}">${s.orphan_folders_count}</strong> (${s.orphan_size_mb} MB)</span>
        <span>Base SQLite: <strong>${s.db_size_mb} MB</strong></span>
        <span>Caché navegador: <strong>${s.browser_cache_mb ?? 0} MB</strong> (perfiles: ${s.browser_profiles_mb ?? 0} MB)</span>
      </div>
    `;
  } catch (err) {
    body.innerHTML = `<div class="muted">No se pudo consultar el almacenamiento: ${esc(err.message)}</div>`;
  }
}

