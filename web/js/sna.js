// OSINT-Auto :: Laboratorio SNA (Círculo Cercano): vista orbital/física, ranking, comunidades e inspector.

import { state, esc, toast, api, apiFormData } from "./state.js";
import { switchView } from "./core_ui.js";
import { loadCases, _epistemicBadge, _reviewBadge } from "./cases.js";
// ---------------------------------------------------------------------------
// Fase 5 :: Vista Secundaria Laboratorio SNA (Grafo Orbital/Físico, Ranking y Comunidades)
// ---------------------------------------------------------------------------
export function _tierBadgeHtml(tier) {
  const t = (tier || "PERIFERICO").toUpperCase();
  if (t === "INTIMO") return `<span class="badge sna-ring-badge-intimo">ANILLO 1 · ÍNTIMO</span>`;
  if (t === "CERCANO") return `<span class="badge sna-ring-badge-cercano">ANILLO 2 · CERCANO</span>`;
  if (t === "SUJETO") return `<span class="badge badge-verificado">SUJETO</span>`;
  return `<span class="badge sna-ring-badge-periferico">ANILLO 3 · PERIFERIA</span>`;
}

export async function renderCaseSnaPreview(containerEl, caseId) {
  if (!containerEl) return;
  containerEl.innerHTML = `<div class="panel-card"><div class="muted">Calculando resumen de Círculo Cercano (SNA)...</div></div>`;
  try {
    const sna = await api(`/api/cases/${encodeURIComponent(caseId)}/sna`);
    const top5 = (sna.close_circle || []).slice(0, 5);
    const st = sna.stats || {};
    const topHtml = top5.length
      ? `<div class="list-stack">
          ${top5
            .map(
              (u) => `<div class="report-row">
                <div>
                  <strong>${esc(u.label)}</strong>
                  ${_tierBadgeHtml(u.tier)}
                  ${u.is_reciprocal ? `<span class="badge badge-verificado">↔ Recíproco</span>` : `<span class="badge badge-info">Unidireccional</span>`}
                  <span class="muted" style="font-size:12px"> · Comunidad: ${esc(u.community_label)}</span>
                </div>
                <div class="inline-actions">
                  <span style="font-family:var(--mono);font-weight:700;color:var(--accent)">${u.closeness_score}/100</span>
                  <button type="button" class="btn btn-xs btn-ghost" data-jump-sna-node="${esc(u.id)}">Inspeccionar</button>
                </div>
              </div>`
            )
            .join("")}
        </div>`
      : `<div class="muted">Sin interacciones o comentarios suficientes aún. Ingiere desde un conector abierto o importa un JSON/CSV para construir el círculo cercano.</div>`;

    containerEl.innerHTML = `
      <div class="panel-card">
        <div class="panel-head">
          <div>
            <h2>Círculo Cercano y Comunidades (Fase 5 · SNA)</h2>
            <span class="muted">${st.alters_count || 0} cuentas · ${st.intimo_count || 0} íntimos · ${st.reciprocal_count || 0} recíprocos · ${st.communities_count || 0} comunidades</span>
          </div>
          <button type="button" class="btn btn-sm btn-primary" id="btnOpenSnaLab">Abrir Laboratorio SNA (Vista Secundaria) →</button>
        </div>
        ${topHtml}
      </div>
    `;
    const btnOpen = document.getElementById("btnOpenSnaLab");
    if (btnOpen) {
      btnOpen.addEventListener("click", () => {
        state.sna.caseId = caseId;
        state.sna.selectedNodeId = "";
        switchView("sna");
      });
    }
    containerEl.querySelectorAll("[data-jump-sna-node]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.sna.caseId = caseId;
        state.sna.selectedNodeId = btn.dataset.jumpSnaNode;
        state.sna.mode = "graph";
        switchView("sna");
      });
    });
  } catch (err) {
    containerEl.innerHTML = "";
  }
}

export async function openSnaView(preferredCaseId = "") {
  if (!state.cases.length) {
    await loadCases();
  }
  const targetId = preferredCaseId || state.sna.caseId || state.selectedCaseId || state.activeCaseId || (state.cases[0] && state.cases[0].id) || "";
  const sel = document.getElementById("snaCaseSelect");
  if (sel && targetId) sel.value = targetId;
  if (targetId) {
    await loadSnaCase(targetId);
  } else {
    document.getElementById("snaKpiRow").innerHTML = "";
    document.getElementById("snaWorkspace").innerHTML = `<div class="panel-card"><div class="muted">Crea primero un caso en <strong>Casos y Entidades</strong> e ingiere comentarios o interacciones para visualizar el Grafo SNA.</div></div>`;
  }
}

export async function loadSnaCase(caseId, forceRecompute = false) {
  if (!caseId) return;
  state.sna.caseId = caseId;
  const ws = document.getElementById("snaWorkspace");
  if (ws) ws.innerHTML = `<div class="panel-card"><div class="muted">Construyendo Grafo SNA y detectando comunidades...</div></div>`;
  try {
    const data = forceRecompute
      ? await api(`/api/cases/${encodeURIComponent(caseId)}/sna/recompute`, { method: "POST" })
      : await api(`/api/cases/${encodeURIComponent(caseId)}/sna`);
    state.sna.data = data;
    if (!state.sna.selectedNodeId && (data.close_circle || []).length) {
      state.sna.selectedNodeId = data.close_circle[0].id;
    }
    renderSnaView();
  } catch (err) {
    toast(`Error cargando SNA: ${err.message}`, "err");
  }
}

export function renderSnaView() {
  if (state.sna.rafId) {
    cancelAnimationFrame(state.sna.rafId);
    state.sna.rafId = null;
  }
  const data = state.sna.data;
  const kpiRow = document.getElementById("snaKpiRow");
  const ws = document.getElementById("snaWorkspace");
  if (!data || !ws) return;

  // Sincronizar tabs del switcher
  document.querySelectorAll("#snaViewSwitcher .seg-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.snaMode === state.sna.mode);
  });

  const st = data.stats || {};
  kpiRow.innerHTML = `
    <div class="kpi-grid">
      <div class="kpi-card"><div class="kpi-label">Cuentas conectadas</div><div class="kpi-value">${st.alters_count || 0}</div><div class="kpi-meta">${st.edges_count || 0} relaciones totales</div></div>
      <div class="kpi-card"><div class="kpi-label">Círculo Íntimo</div><div class="kpi-value" style="color:var(--emerald)">${st.intimo_count || 0}</div><div class="kpi-meta">${st.cercano_count || 0} cercanos · ${st.periferico_count || 0} periferia</div></div>
      <div class="kpi-card"><div class="kpi-label">Vínculos Recíprocos</div><div class="kpi-value" style="color:var(--accent)">${st.reciprocal_count || 0}</div><div class="kpi-meta">Interacción en doble vía</div></div>
      <div class="kpi-card"><div class="kpi-label">Comunidades</div><div class="kpi-value" style="color:var(--violet)">${st.communities_count || 0}</div><div class="kpi-meta">Detectadas por grafo + temas</div></div>
    </div>
  `;

  const quickFeederHtml = `
    <details class="panel-card" ${!(data.close_circle || []).length ? "open" : ""}>
      <summary style="cursor:pointer;font-weight:600;color:var(--text-strong)">
        + Alimentar Círculo Cercano (Subir exportación Facebook/Instagram/Threads/WhatsApp o registrar interacción manual)
      </summary>
      <div class="two-col" style="margin-top:12px">
        <form id="formSnaQuickImport" style="display:flex;flex-direction:column;gap:8px">
          <div class="muted" style="font-size:12px"><strong>Opción A · Importar exportación real</strong> (Meta JSON/ZIP, chat WhatsApp .txt o CSV)</div>
          <input id="snaImpFile" type="file" accept=".json,.csv,.zip,.txt" required />
          <div class="inline-actions">
            <select id="snaImpPlat" style="max-width:170px">
              <option value="facebook">Facebook</option>
              <option value="instagram">Instagram</option>
              <option value="threads">Threads</option>
              <option value="whatsapp">WhatsApp</option>
              <option value="exportacion_propia">Otro / JSON</option>
            </select>
            <button type="submit" class="btn btn-sm btn-primary" id="btnSnaQuickImport">Importar y actualizar grafo</button>
          </div>
        </form>

        <form id="formSnaManualIx" style="display:flex;flex-direction:column;gap:8px">
          <div class="muted" style="font-size:12px"><strong>Opción B · Registrar comentario o vínculo observado</strong> (ej. de tu revisión en Facebook/Threads)</div>
          <div class="form-grid" style="grid-template-columns:1fr 1fr;gap:8px">
            <input id="snaManPeer" type="text" placeholder="Usuario o nombre del contacto *" required />
            <select id="snaManDir">
              <option value="mutual">Interacción mutua (↔)</option>
              <option value="incoming">Esa persona te comentó (←)</option>
              <option value="outgoing">Tú le comentaste (→)</option>
            </select>
            <select id="snaManPlat">
              <option value="facebook">Facebook</option>
              <option value="threads">Threads</option>
              <option value="instagram">Instagram</option>
              <option value="whatsapp">WhatsApp</option>
              <option value="otro">Otra red</option>
            </select>
            <select id="snaManKind">
              <option value="comment">Comentario</option>
              <option value="reply">Respuesta</option>
              <option value="mention">Mención</option>
              <option value="friend">Amigo cercano</option>
            </select>
          </div>
          <input id="snaManContent" type="text" placeholder="Texto del comentario (opcional, ayuda a detectar temas y tono)..." />
          <div><button type="submit" class="btn btn-sm btn-secondary">Añadir al Círculo Cercano</button></div>
        </form>
      </div>
    </details>
  `;

  if (!(data.close_circle || []).length) {
    ws.innerHTML = `
      <div class="panel-card">
        <div class="panel-head"><h2>Sin interacciones todavía en este caso</h2></div>
        <p class="helper-text">
          <strong>¿Por qué está vacío aunque ya encontraste perfiles en Facebook / Threads?</strong><br/>
          Maigret solo detecta que la cuenta existe, y redes cerradas como <strong>Facebook, Instagram y Threads</strong> bloquean la lectura automática de amigos y comentarios detrás de login.
          Para poblar el Círculo Cercano al instante, usa cualquiera de las dos opciones aquí abajo: sube tu exportación oficial de Meta (<code>.json</code> o <code>.zip</code>), un chat de WhatsApp (<code>.txt</code>), o registra manualmente las cuentas que interactúan contigo.
        </p>
      </div>
      ${quickFeederHtml}
    `;
    bindSnaQuickFeeders(state.sna.caseId);
    return;
  }

  ws.innerHTML = `${quickFeederHtml}<div id="snaMainModeSlot"></div>`;
  bindSnaQuickFeeders(state.sna.caseId);
  const slot = document.getElementById("snaMainModeSlot");

  if (state.sna.mode === "graph") {
    renderSnaGraphMode(slot, data);
  } else if (state.sna.mode === "ranking") {
    renderSnaRankingMode(slot, data);
  } else {
    renderSnaCommunitiesMode(slot, data);
  }
}

export function bindSnaQuickFeeders(caseId) {
  const fImp = document.getElementById("formSnaQuickImport");
  if (fImp) {
    fImp.addEventListener("submit", async (e) => {
      e.preventDefault();
      const inp = document.getElementById("snaImpFile");
      if (!inp.files || !inp.files[0]) return;
      const btn = document.getElementById("btnSnaQuickImport");
      btn.disabled = true;
      try {
        const fd = new FormData();
        fd.append("file", inp.files[0]);
        fd.append("platform_label", document.getElementById("snaImpPlat").value);
        fd.append("subject_handle", (state.sna.data && state.sna.data.ego_id) || "");
        const out = await apiFormData(`/api/cases/${encodeURIComponent(caseId)}/ingest/import`, fd);
        const s = out.summary || {};
        toast(`Importado: ${s.comments_inserted || 0} comentarios y ${s.interactions_upserted || 0} interacciones.`, "ok");
        await loadCases();
        await loadSnaCase(caseId, false);
      } catch (err) {
        toast(err.message, "err");
      } finally {
        btn.disabled = false;
      }
    });
  }

  const fMan = document.getElementById("formSnaManualIx");
  if (fMan) {
    fMan.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const updated = await api(`/api/cases/${encodeURIComponent(caseId)}/sna/interaction`, {
          method: "POST",
          body: JSON.stringify({
            peer_handle: document.getElementById("snaManPeer").value.trim(),
            direction: document.getElementById("snaManDir").value,
            platform: document.getElementById("snaManPlat").value,
            kind: document.getElementById("snaManKind").value,
            content: document.getElementById("snaManContent").value.trim(),
          }),
        });
        state.sna.data = updated;
        state.sna.selectedNodeId = document.getElementById("snaManPeer").value.trim().replace(/^@/, "").toLowerCase();
        await loadCases();
        renderSnaView();
        toast("Interacción añadida al Círculo Cercano.", "ok");
      } catch (err) {
        toast(err.message, "err");
      }
    });
  }
}

export function renderSnaInspector(containerEl, data, nodeId) {
  if (!containerEl || !data) return;
  const node = (data.nodes || []).find((n) => n.id === nodeId) || (data.close_circle || [])[0];
  if (!node) {
    containerEl.innerHTML = `<div class="muted">Haz clic en cualquier nodo del grafo para inspeccionar su relación, temas y comentarios.</div>`;
    return;
  }

  const sent = node.relationship_sentiment || { dominant: "NEUTRO", positive: 0, neutral: 0, critical: 0, avg_score: 0 };
  const sentTotal = Math.max(1, (sent.positive || 0) + (sent.neutral || 0) + (sent.critical || 0));
  const posPct = Math.round(((sent.positive || 0) / sentTotal) * 100);
  const neuPct = Math.round(((sent.neutral || 0) / sentTotal) * 100);
  const negPct = Math.round(((sent.critical || 0) / sentTotal) * 100);

  const themesHtml = (node.shared_themes || []).length
    ? `<div class="tags-wrap">${node.shared_themes.map((t) => `<span class="tag-chip">${esc(t.label)} (${t.count})</span>`).join("")}</div>`
    : `<div class="muted" style="font-size:12px">Sin categorías temáticas detectadas en comentarios mutuos.</div>`;

  const kindsHtml = Object.keys(node.interaction_types || {}).length
    ? `<div class="tags-wrap">${Object.entries(node.interaction_types).map(([k, v]) => `<span class="tag-chip">${esc(k)}: ${v}</span>`).join("")}</div>`
    : "";

  const chats = node.sample_comments || [];
  const chatsHtml = chats.length
    ? `<div class="list-stack" style="gap:7px">
        ${chats
          .map((cm) => {
            const isOut = (cm.direction || "").startsWith("Sujeto");
            const sBadge = cm.sentiment === "POSITIVO" ? "badge-verificado" : cm.sentiment === "CRÍTICO" ? "badge-descartado" : "badge-info";
            return `
              <div class="sna-chat-bubble ${isOut ? "out" : "in"}">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:6px">
                  <strong>${esc(cm.direction)}</strong>
                  <span class="badge ${sBadge}">${esc(cm.sentiment || "NEUTRO")}</span>
                </div>
                <div>${esc(cm.content)}</div>
                ${cm.published_at ? `<div class="muted" style="font-size:10.5px">${esc(cm.published_at)}</div>` : ""}
              </div>
            `;
          })
          .join("")}
      </div>`
    : `<div class="muted" style="font-size:12px">Relación detectada vía menciones/colaboraciones sin texto de comentario almacenado.</div>`;

  containerEl.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
      <div>
        <div style="font-size:16px;font-weight:700;color:var(--text-strong)">${esc(node.label)}</div>
        <div class="muted" style="font-size:12px">Comunidad: <span style="color:${esc(node.color)};font-weight:600">${esc(node.community_label)}</span></div>
      </div>
      ${_tierBadgeHtml(node.tier)}
    </div>

    ${!node.is_subject ? `
      <div class="interest-card" style="padding:10px 12px">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span class="muted" style="font-size:12px">Índice de Cercanía</span>
          <strong style="font-family:var(--mono);font-size:18px;color:var(--accent)">${node.closeness_score}/100</strong>
        </div>
        <div class="score-bar"><div class="score-bar-fill" style="width:${node.closeness_score}%;background:${esc(node.color)}"></div></div>
        <div class="case-item-meta" style="margin-top:4px">
          <span>${node.is_reciprocal ? "↔ Recíproco" : "→ Unidireccional"} (${Math.round((node.reciprocity || 0) * 100)}%)</span>
          <span>Sujeto→Ellos: ${node.sent_to_them}</span>
          <span>Ellos→Sujeto: ${node.received_from_them}</span>
        </div>
        <div class="case-item-meta">
          <span>Días activos: ${node.active_days || 0}</span>
          <span>Grado: ${node.degree || 0}</span>
          <span>Puente (Betweenness): ${Number(node.betweenness || 0).toFixed(2)}</span>
        </div>
        <div style="margin-top:8px;padding-top:6px;border-top:1px solid var(--border)">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <span class="muted" style="font-size:12px">Proximidad Visual (dHash + co-ocurrencia)</span>
            <strong style="font-family:var(--mono);font-size:13px;color:var(--emerald)">${Number(node.visual_proximity_score || 0).toFixed(1)}/100</strong>
          </div>
          <div id="snaVisualMatchesHost" class="tags-wrap" style="margin-top:6px"></div>
        </div>
      </div>

      <div>
        <div class="muted" style="font-size:12px;margin-bottom:5px"><strong>Tipo de interacciones</strong></div>
        ${kindsHtml || `<span class="muted" style="font-size:12px">—</span>`}
      </div>

      <div>
        <div class="muted" style="font-size:12px;margin-bottom:5px"><strong>Temas compartidos en conversación</strong></div>
        ${themesHtml}
      </div>

      <div>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span class="muted" style="font-size:12px"><strong>Tono de la relación</strong></span>
          <span class="badge ${sent.dominant === "POSITIVO" ? "badge-verificado" : sent.dominant === "CRÍTICO" ? "badge-descartado" : "badge-info"}">${esc(sent.dominant)}</span>
        </div>
        <div class="sent-bar">
          <div class="sent-pos" style="width:${posPct}%"></div>
          <div class="sent-neu" style="width:${neuPct}%"></div>
          <div class="sent-neg" style="width:${negPct}%"></div>
        </div>
      </div>

      <div>
        <div class="muted" style="font-size:12px;margin-bottom:5px"><strong>Expresiones relacionales detectadas (no concluyentes)</strong></div>
        ${(() => {
          const rc = node.relational_cues || { total_signals: 0, cues: [] };
          if (!rc.total_signals) return `<div class="muted" style="font-size:12px">Sin expresiones relacionales detectadas en sus comentarios.</div>`;
          return `
            <div class="tags-wrap" style="margin-bottom:6px">
              ${rc.explicit_count ? `<span class="badge badge-verificado">${rc.explicit_count} explícita(s)</span>` : ""}
              ${rc.ambiguous_count ? `<span class="badge badge-revisar">${rc.ambiguous_count} ambigua(s)</span>` : ""}
              ${rc.inferred_count ? `<span class="badge badge-probable">${rc.inferred_count} inferencia IA</span>` : ""}
              ${rc.validated_count ? `<span class="badge badge-verificado">${rc.validated_count} validada(s)</span>` : ""}
            </div>
            <div class="list-stack" style="gap:6px">
              ${(rc.cues || []).slice(0, 5).map((q) => `
                <div class="quote-item">
                  <div style="display:flex;justify-content:space-between;align-items:center;gap:4px;flex-wrap:wrap">
                    <span><span class="badge badge-info">${esc(q.category)}</span> <code>${esc(q.matched_expression)}</code></span>
                    ${_epistemicBadge(q.epistemic_type)}
                  </div>
                  <div style="margin-top:4px">“${esc((q.context_excerpt || "").slice(0, 180))}”</div>
                  <div class="inline-actions" style="margin-top:5px">
                    ${_reviewBadge(q.review_status)}
                    ${q.evidence_url ? `<a class="btn btn-xs btn-ghost" href="${esc(q.evidence_url)}" target="_blank" rel="noopener">Fuente</a>` : ""}
                    ${q.id ? `
                      <button type="button" class="btn btn-xs btn-ghost" data-sna-rel-rev="validated" data-sna-rel-id="${q.id}">Validar</button>
                      <button type="button" class="btn btn-xs btn-ghost" data-sna-rel-rev="dismissed" data-sna-rel-id="${q.id}">Descartar</button>
                    ` : ""}
                  </div>
                </div>
              `).join("")}
            </div>
          `;
        })()}
      </div>

      <div>
        <div class="muted" style="font-size:12px;margin-bottom:6px"><strong>Comentarios intercambiados (${chats.length})</strong></div>
        ${chatsHtml}
      </div>
    ` : `
      <div class="interest-card">
        <div class="muted" style="font-size:12px">Nodo central (Entidad Principal del caso).</div>
        <div class="case-item-meta">
          <span>Conexiones directas: ${node.degree}</span>
          <span>Interacciones totales: ${node.total_interactions}</span>
        </div>
      </div>
    `}
  `;

  // Permitir validar o descartar señales relacionales directo desde el Inspector SNA
  containerEl.querySelectorAll("[data-sna-rel-rev]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!state.sna.caseId) return;
      try {
        await api(`/api/cases/${encodeURIComponent(state.sna.caseId)}/relational/${btn.dataset.snaRelId}`, {
          method: "PATCH",
          body: JSON.stringify({ review_status: btn.dataset.snaRelRev }),
        });
        await loadSnaCase(state.sna.caseId, false);
        toast(`Señal relacional actualizada (${btn.dataset.snaRelRev}).`, "ok");
      } catch (err) {
        toast(err.message, "err");
      }
    });
  });
}

export function renderSnaGraphMode(ws, data) {
  const comms = data.communities || [];
  ws.innerHTML = `
    <div class="sna-split">
      <div class="panel-card sna-stage-card">
        <div class="sna-controls">
          <div class="segmented" id="snaLayoutSwitcher" style="margin:0">
            <button type="button" class="seg-btn ${state.sna.layout === "orbital" ? "active" : ""}" data-sna-layout="orbital">Anillos de Cercanía</button>
            <button type="button" class="seg-btn ${state.sna.layout === "force" ? "active" : ""}" data-sna-layout="force">Red Física</button>
          </div>
          <label>
            Cercanía mín: <strong id="snaMinScoreVal" style="color:var(--accent);font-family:var(--mono)">${state.sna.minScore}</strong>
            <input type="range" id="snaMinScore" min="0" max="85" step="5" value="${state.sna.minScore}" style="width:95px" />
          </label>
          <label>
            Comunidad:
            <select id="snaCommFilter" style="max-width:170px">
              <option value="all">Todas (${comms.length})</option>
              ${comms.map((cm) => `<option value="${cm.id}" ${String(state.sna.communityFilter) === String(cm.id) ? "selected" : ""}>${esc(cm.label)} (${cm.size})</option>`).join("")}
            </select>
          </label>
          <label class="checkbox-label" style="font-size:12px">
            <input type="checkbox" id="snaOnlyRecip" ${state.sna.onlyReciprocal ? "checked" : ""} />
            <span>Solo recíprocos</span>
          </label>
        </div>

        <div class="sna-canvas-wrap" id="snaCanvasWrap">
          <canvas class="sna-canvas" id="snaCanvas"></canvas>
          <div class="sna-legend-hud">
            <span class="legend-item"><span class="legend-dot" style="background:var(--emerald)"></span>Arista recíproca (↔)</span>
            <span class="legend-item"><span class="legend-dot" style="background:var(--accent)"></span>Arista directa (→)</span>
            <span class="legend-item"><span class="legend-dot" style="background:var(--violet)"></span>Co-hilo terceros</span>
            <span class="muted">Arrastra nodos · Rueda = zoom</span>
          </div>
          <div class="sna-zoom-hud">
            <button type="button" class="btn btn-xs btn-ghost" id="btnSnaZoomIn" title="Acercar">+</button>
            <button type="button" class="btn btn-xs btn-ghost" id="btnSnaZoomOut" title="Alejar">−</button>
            <button type="button" class="btn btn-xs btn-ghost" id="btnSnaReset">Centrar</button>
          </div>
        </div>
      </div>

      <div class="panel-card sna-inspector-card" id="snaInspector"></div>
    </div>
  `;

  // Bind controles
  ws.querySelectorAll("[data-sna-layout]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.sna.layout = btn.dataset.snaLayout;
      renderSnaGraphMode(ws, data);
    });
  });
  document.getElementById("snaMinScore").addEventListener("input", (e) => {
    state.sna.minScore = Number(e.target.value);
    document.getElementById("snaMinScoreVal").textContent = state.sna.minScore;
    mountSnaCanvas(data);
  });
  document.getElementById("snaCommFilter").addEventListener("change", (e) => {
    state.sna.communityFilter = e.target.value;
    mountSnaCanvas(data);
  });
  document.getElementById("snaOnlyRecip").addEventListener("change", (e) => {
    state.sna.onlyReciprocal = e.target.checked;
    mountSnaCanvas(data);
  });

  renderSnaInspector(document.getElementById("snaInspector"), data, state.sna.selectedNodeId);
  mountSnaCanvas(data);
}

export function mountSnaCanvas(data) {
  if (state.sna.rafId) {
    cancelAnimationFrame(state.sna.rafId);
    state.sna.rafId = null;
  }
  const canvas = document.getElementById("snaCanvas");
  const wrap = document.getElementById("snaCanvasWrap");
  if (!canvas || !wrap) return;

  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = wrap.clientWidth || 720;
  const height = wrap.clientHeight || 540;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  const ctx = canvas.getContext("2d");

  // Filtrar nodos y aristas según controles activos
  const visibleNodes = (data.nodes || []).filter((n) => {
    if (n.is_subject) return true;
    if ((n.closeness_score || 0) < state.sna.minScore) return false;
    if (state.sna.onlyReciprocal && !n.is_reciprocal) return false;
    if (state.sna.communityFilter !== "all" && String(n.community_id) !== String(state.sna.communityFilter)) return false;
    return true;
  });
  const visSet = new Set(visibleNodes.map((n) => n.id));
  const visibleEdges = (data.edges || []).filter((e) => visSet.has(e.source) && visSet.has(e.target));

  // Posicionamiento inicial (Orbital por anillos y comunidad)
  const cx = width / 2;
  const cy = height / 2;
  const maxR = Math.min(width, height) * 0.43;
  const alters = visibleNodes.filter((n) => !n.is_subject);
  // Ordenar por comunidad para agrupar angularmente
  alters.sort((a, b) => (a.community_id - b.community_id) || (b.closeness_score - a.closeness_score));

  const simNodes = visibleNodes.map((n) => {
    if (n.is_subject) {
      return { ...n, x: cx, y: cy, vx: 0, vy: 0, r: 22 };
    }
    const idx = alters.findIndex((a) => a.id === n.id);
    const angle = (idx / Math.max(1, alters.length)) * Math.PI * 2 - Math.PI / 2;
    const normDist = 1.0 - (Math.max(0, Math.min(100, n.closeness_score || 0)) / 100.0);
    const radius = maxR * (0.28 + 0.68 * normDist);
    const nodeR = Math.max(8, Math.min(17, 7 + Math.sqrt(n.total_interactions || 1) * 2.5));
    return {
      ...n,
      x: cx + Math.cos(angle) * radius,
      y: cy + Math.sin(angle) * radius,
      vx: 0,
      vy: 0,
      r: nodeR,
    };
  });
  const byId = Object.fromEntries(simNodes.map((n) => [n.id, n]));

  let zoom = 1.0;
  let panX = 0;
  let panY = 0;
  let hoverId = null;
  let dragNode = null;
  let isPanning = false;
  let panStart = { x: 0, y: 0 };
  let simEnergy = state.sna.layout === "force" ? 180 : 0;

  const toWorld = (mx, my) => ({
    x: (mx - width / 2 - panX) / zoom + width / 2,
    y: (my - height / 2 - panY) / zoom + height / 2,
  });

  const hitTest = (mx, my) => {
    const w = toWorld(mx, my);
    for (let i = simNodes.length - 1; i >= 0; i--) {
      const n = simNodes[i];
      const dx = w.x - n.x;
      const dy = w.y - n.y;
      if (dx * dx + dy * dy <= (n.r + 6) * (n.r + 6)) return n;
    }
    return null;
  };

  function stepForce() {
    if (simEnergy <= 0) return;
    simEnergy -= 1;
    const kRepel = 4600;
    const kSpring = 0.018;
    for (let i = 0; i < simNodes.length; i++) {
      const a = simNodes[i];
      if (a.is_subject || a === dragNode) continue;
      // Gravedad suave al centro
      a.vx += (cx - a.x) * 0.004;
      a.vy += (cy - a.y) * 0.004;
      for (let j = 0; j < simNodes.length; j++) {
        if (i === j) continue;
        const b = simNodes[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const d2 = Math.max(400, dx * dx + dy * dy);
        const f = kRepel / d2;
        const dist = Math.sqrt(d2);
        a.vx += (dx / dist) * f;
        a.vy += (dy / dist) * f;
      }
    }
    for (const e of visibleEdges) {
      const a = byId[e.source];
      const b = byId[e.target];
      if (!a || !b) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      const ideal = e.edge_type === "co_thread" ? 130 : Math.max(68, 175 - Math.min(95, (e.weight || 1) * 14));
      const force = (dist - ideal) * kSpring;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      if (!a.is_subject && a !== dragNode) { a.vx += fx; a.vy += fy; }
      if (!b.is_subject && b !== dragNode) { b.vx -= fx; b.vy -= fy; }
    }
    for (const n of simNodes) {
      if (n.is_subject || n === dragNode) continue;
      n.vx *= 0.82;
      n.vy *= 0.82;
      n.x += Math.max(-14, Math.min(14, n.vx));
      n.y += Math.max(-14, Math.min(14, n.vy));
    }
  }

  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    ctx.save();
    ctx.translate(width / 2 + panX, height / 2 + panY);
    ctx.scale(zoom, zoom);
    ctx.translate(-width / 2, -height / 2);

    // Anillos concéntricos en modo Orbital
    if (state.sna.layout === "orbital") {
      const rings = [
        { r: maxR * 0.44, label: "ANILLO 1 · ÍNTIMO", color: "rgba(16, 185, 129, 0.22)" },
        { r: maxR * 0.72, label: "ANILLO 2 · CERCANO", color: "rgba(56, 189, 248, 0.18)" },
        { r: maxR * 0.98, label: "ANILLO 3 · PERIFERIA", color: "rgba(148, 163, 184, 0.14)" },
      ];
      for (const rg of rings) {
        ctx.beginPath();
        ctx.arc(cx, cy, rg.r, 0, Math.PI * 2);
        ctx.strokeStyle = rg.color;
        ctx.lineWidth = 1.2;
        ctx.setLineDash([5, 5]);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "rgba(148, 163, 184, 0.52)";
        ctx.font = "600 9.5px 'JetBrains Mono', monospace";
        ctx.textAlign = "center";
        ctx.fillText(rg.label, cx, cy - rg.r - 5);
      }
    }

    const focusId = hoverId || state.sna.selectedNodeId;
    const neighborSet = new Set();
    if (focusId) {
      neighborSet.add(focusId);
      for (const e of visibleEdges) {
        if (e.source === focusId) neighborSet.add(e.target);
        if (e.target === focusId) neighborSet.add(e.source);
      }
    }

    // Dibujar aristas
    for (const e of visibleEdges) {
      const a = byId[e.source];
      const b = byId[e.target];
      if (!a || !b) continue;
      const isHighlight = !focusId || e.source === focusId || e.target === focusId;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      if (e.edge_type === "co_thread") {
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = isHighlight ? "rgba(167, 139, 250, 0.45)" : "rgba(167, 139, 250, 0.10)";
        ctx.lineWidth = 1.1;
      } else if (e.bidirectional) {
        ctx.setLineDash([]);
        ctx.strokeStyle = isHighlight ? "rgba(16, 185, 129, 0.78)" : "rgba(16, 185, 129, 0.16)";
        ctx.lineWidth = Math.min(5, 1.5 + Math.log1p(e.weight || 1) * 1.1);
      } else {
        ctx.setLineDash([]);
        ctx.strokeStyle = isHighlight ? "rgba(56, 189, 248, 0.52)" : "rgba(56, 189, 248, 0.12)";
        ctx.lineWidth = Math.min(3.5, 1.0 + Math.log1p(e.weight || 1) * 0.7);
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Dibujar nodos
    for (const n of simNodes) {
      const isSelected = n.id === state.sna.selectedNodeId;
      const isHovered = n.id === hoverId;
      const dimmed = focusId && !neighborSet.has(n.id) && !n.is_subject;

      ctx.globalAlpha = dimmed ? 0.26 : 1.0;

      // Halo para Ego o seleccionado
      if (n.is_subject || isSelected || isHovered) {
        const grad = ctx.createRadialGradient(n.x, n.y, n.r * 0.4, n.x, n.y, n.r * 2.1);
        grad.addColorStop(0, n.is_subject ? "rgba(56, 189, 248, 0.45)" : "rgba(248, 250, 252, 0.35)");
        grad.addColorStop(1, "rgba(0,0,0,0)");
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r * 2.1, 0, Math.PI * 2);
        ctx.fillStyle = grad;
        ctx.fill();
      }

      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fillStyle = n.is_subject ? "#0ea5e9" : (n.color || "#38bdf8");
      ctx.fill();
      ctx.lineWidth = isSelected || isHovered ? 3 : (n.is_reciprocal && !n.is_subject ? 2 : 1.2);
      ctx.strokeStyle = isSelected || isHovered ? "#f8fafc" : (n.is_reciprocal && !n.is_subject ? "#10b981" : "#0b1222");
      ctx.stroke();

      // Aro punteado discreto si el nodo tiene señales de lenguaje relacional detectadas/validadas
      if (!n.is_subject && n.relational_cues && n.relational_cues.total_signals > 0) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 4.5, 0, Math.PI * 2);
        ctx.setLineDash([2.5, 2.5]);
        ctx.strokeStyle = n.relational_cues.validated_count > 0 ? "#f59e0b" : "rgba(245, 158, 11, 0.55)";
        ctx.lineWidth = 1.4;
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // Etiqueta
      ctx.font = n.is_subject ? "700 12px Inter, sans-serif" : "600 11px Inter, sans-serif";
      ctx.fillStyle = "#f8fafc";
      ctx.textAlign = "center";
      const lbl = n.label.length > 20 ? `${n.label.slice(0, 18)}…` : n.label;
      ctx.fillText(lbl, n.x, n.y + n.r + 14);

      if (!n.is_subject && (n.closeness_score >= 45 || isSelected || isHovered)) {
        ctx.font = "500 9.5px 'JetBrains Mono', monospace";
        ctx.fillStyle = "#94a3b8";
        ctx.fillText(`${n.closeness_score}`, n.x, n.y + 3.5);
      }
      ctx.globalAlpha = 1.0;
    }

    ctx.restore();
  }

  function loop() {
    if (simEnergy > 0) {
      stepForce();
      draw();
      state.sna.rafId = requestAnimationFrame(loop);
    } else {
      state.sna.rafId = null;
    }
  }

  draw();
  if (simEnergy > 0) state.sna.rafId = requestAnimationFrame(loop);

  // Interacción mouse + zoom
  const getLocal = (ev) => {
    const rect = canvas.getBoundingClientRect();
    return { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
  };

  canvas.onmousedown = (ev) => {
    const p = getLocal(ev);
    const hit = hitTest(p.x, p.y);
    if (hit) {
      dragNode = hit;
      state.sna.selectedNodeId = hit.id;
      renderSnaInspector(document.getElementById("snaInspector"), data, hit.id);
      draw();
    } else {
      isPanning = true;
      panStart = { x: p.x - panX, y: p.y - panY };
    }
  };
  canvas.onmousemove = (ev) => {
    const p = getLocal(ev);
    if (dragNode) {
      const w = toWorld(p.x, p.y);
      dragNode.x = w.x;
      dragNode.y = w.y;
      if (state.sna.layout === "force") {
        simEnergy = Math.max(simEnergy, 25);
        if (!state.sna.rafId) state.sna.rafId = requestAnimationFrame(loop);
      } else {
        draw();
      }
      return;
    }
    if (isPanning) {
      panX = p.x - panStart.x;
      panY = p.y - panStart.y;
      draw();
      return;
    }
    const hit = hitTest(p.x, p.y);
    const nextHover = hit ? hit.id : null;
    if (nextHover !== hoverId) {
      hoverId = nextHover;
      canvas.style.cursor = hit ? "pointer" : "grab";
      draw();
    }
  };
  const endDrag = () => {
    dragNode = null;
    isPanning = false;
  };
  canvas.onmouseup = endDrag;
  canvas.onmouseleave = () => { endDrag(); hoverId = null; draw(); };
  canvas.onwheel = (ev) => {
    ev.preventDefault();
    const factor = ev.deltaY < 0 ? 1.12 : 0.89;
    zoom = Math.max(0.45, Math.min(2.8, zoom * factor));
    draw();
  };

  const zIn = document.getElementById("btnSnaZoomIn");
  const zOut = document.getElementById("btnSnaZoomOut");
  const zRes = document.getElementById("btnSnaReset");
  if (zIn) zIn.onclick = () => { zoom = Math.min(2.8, zoom * 1.2); draw(); };
  if (zOut) zOut.onclick = () => { zoom = Math.max(0.45, zoom / 1.2); draw(); };
  if (zRes) zRes.onclick = () => { zoom = 1.0; panX = 0; panY = 0; draw(); };
}

export function renderSnaRankingMode(ws, data) {
  const all = data.close_circle || [];
  const filtered = all.filter((u) => {
    if (state.sna.rankingTier !== "ALL" && u.tier !== state.sna.rankingTier) return false;
    if (state.sna.rankingQuery && !`${u.label} ${u.community_label}`.toLowerCase().includes(state.sna.rankingQuery.toLowerCase())) return false;
    return true;
  });

  ws.innerHTML = `
    <div class="sna-split">
      <div class="panel-card">
        <div class="panel-head">
          <h2>Ranking del Círculo Cercano (${filtered.length}/${all.length})</h2>
          <div class="inline-actions">
            <select id="snaRankTier" style="max-width:155px">
              <option value="ALL" ${state.sna.rankingTier === "ALL" ? "selected" : ""}>Todos los anillos</option>
              <option value="INTIMO" ${state.sna.rankingTier === "INTIMO" ? "selected" : ""}>Solo Íntimo</option>
              <option value="CERCANO" ${state.sna.rankingTier === "CERCANO" ? "selected" : ""}>Solo Cercano</option>
              <option value="PERIFERICO" ${state.sna.rankingTier === "PERIFERICO" ? "selected" : ""}>Solo Periferia</option>
            </select>
            <input id="snaRankQuery" class="search-input" type="search" value="${esc(state.sna.rankingQuery)}" placeholder="Filtrar cuenta o comunidad..." style="max-width:210px" />
          </div>
        </div>
        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Cuenta / Comunidad</th>
                <th>Cercanía</th>
                <th>Reciprocidad</th>
                <th>Temas y Tono</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              ${filtered
                .map(
                  (u, i) => `<tr>
                    <td style="font-family:var(--mono)">${i + 1}</td>
                    <td>
                      <div><strong>${esc(u.label)}</strong> ${_tierBadgeHtml(u.tier)}</div>
                      <div class="muted" style="font-size:11.5px"><span style="color:${esc(u.color)}">●</span> ${esc(u.community_label)}</div>
                    </td>
                    <td>
                      <div class="score-bar-wrap">
                        <div class="score-bar"><div class="score-bar-fill" style="width:${u.closeness_score}%;background:${esc(u.color)}"></div></div>
                        <span class="score-num" style="color:${esc(u.color)}">${u.closeness_score}</span>
                      </div>
                      <div class="muted" style="font-size:11px">${u.total_interactions} interac. (${u.sent_to_them}→ / ${u.received_from_them}←)</div>
                    </td>
                    <td>
                      ${u.is_reciprocal
                        ? `<span class="badge badge-verificado">↔ ${Math.round((u.reciprocity || 0) * 100)}%</span>`
                        : `<span class="badge badge-info">Unidir.</span>`}
                    </td>
                    <td>
                      <span class="badge ${(u.relationship_sentiment || {}).dominant === "POSITIVO" ? "badge-verificado" : (u.relationship_sentiment || {}).dominant === "CRÍTICO" ? "badge-descartado" : "badge-info"}">${esc((u.relationship_sentiment || {}).dominant || "NEUTRO")}</span>
                      <div class="muted" style="font-size:11.5px;margin-top:3px">${(u.shared_themes || []).map((t) => esc(t.label)).join(", ") || "—"}</div>
                    </td>
                    <td>
                      <div class="inline-actions">
                        <button type="button" class="btn btn-xs btn-primary" data-rank-inspect="${esc(u.id)}">Detalle</button>
                        <button type="button" class="btn btn-xs btn-ghost" data-rank-graph="${esc(u.id)}">En grafo</button>
                      </div>
                    </td>
                  </tr>`
                )
                .join("")}
            </tbody>
          </table>
        </div>
      </div>

      <div class="panel-card sna-inspector-card" id="snaInspector"></div>
    </div>
  `;

  document.getElementById("snaRankTier").addEventListener("change", (e) => {
    state.sna.rankingTier = e.target.value;
    renderSnaRankingMode(ws, data);
  });
  document.getElementById("snaRankQuery").addEventListener("input", (e) => {
    state.sna.rankingQuery = e.target.value;
    renderSnaRankingMode(ws, data);
  });
  ws.querySelectorAll("[data-rank-inspect]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.sna.selectedNodeId = btn.dataset.rankInspect;
      renderSnaInspector(document.getElementById("snaInspector"), data, state.sna.selectedNodeId);
    });
  });
  ws.querySelectorAll("[data-rank-graph]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.sna.selectedNodeId = btn.dataset.rankGraph;
      state.sna.mode = "graph";
      renderSnaView();
    });
  });

  renderSnaInspector(document.getElementById("snaInspector"), data, state.sna.selectedNodeId);
}

export function renderSnaCommunitiesMode(ws, data) {
  const comms = data.communities || [];
  ws.innerHTML = `
    <div class="sna-split">
      <div class="panel-card">
        <div class="panel-head">
          <h2>Comunidades Detectadas (${comms.length})</h2>
          <span class="muted">Agrupadas por co-participación en hilos, interacciones mutuas y afinidad temática</span>
        </div>
        <div class="sna-comm-grid">
          ${comms
            .map(
              (cm) => `
              <div class="sna-comm-card" style="border-top-color:${esc(cm.color)}">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
                  <strong style="font-size:14.5px;color:var(--text-strong)">${esc(cm.label)}</strong>
                  <span class="badge badge-info">${cm.size} miembros</span>
                </div>
                <div class="inline-actions">
                  <span class="badge ${cm.dominant_tone === "POSITIVO" ? "badge-verificado" : cm.dominant_tone === "CRÍTICO" ? "badge-descartado" : "badge-info"}">Tono: ${esc(cm.dominant_tone)}</span>
                  ${(cm.top_themes || []).slice(0, 2).map((t) => `<span class="tag-chip">${esc(t.label)}</span>`).join("")}
                </div>
                <div class="tags-wrap">
                  ${(cm.members || []).map((m) => `<button type="button" class="sna-member-btn" data-comm-member="${esc(m)}">@${esc(m)}</button>`).join("")}
                </div>
                <div>
                  <button type="button" class="btn btn-xs btn-ghost" data-comm-open-graph="${cm.id}">Ver esta comunidad en el Grafo →</button>
                </div>
              </div>
            `
            )
            .join("")}
        </div>
      </div>

      <div class="panel-card sna-inspector-card" id="snaInspector"></div>
    </div>
  `;

  ws.querySelectorAll("[data-comm-member]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.sna.selectedNodeId = btn.dataset.commMember;
      renderSnaInspector(document.getElementById("snaInspector"), data, state.sna.selectedNodeId);
    });
  });
  ws.querySelectorAll("[data-comm-open-graph]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.sna.communityFilter = btn.dataset.commOpenGraph;
      state.sna.mode = "graph";
      renderSnaView();
    });
  });

  renderSnaInspector(document.getElementById("snaInspector"), data, state.sna.selectedNodeId);
}

