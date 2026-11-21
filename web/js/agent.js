// OSINT-Auto :: Agente de navegación supervisada (Playwright), bandeja staging y panel aislado de amigos.

import { state, getActiveCaseId, esc, toast, api } from "./state.js";
import { loadCases, syncActiveCaseSelect } from "./cases.js";
import { loadSnaCase } from "./sna.js";
// ---------------------------------------------------------------------------
// Agente de Navegación Supervisada (Playwright Headed + Pause/Resume/Cancel + Staging)
// ---------------------------------------------------------------------------
/** Inicializa la vista del Agente de Navegación Supervisada, sesiones e historial. */
export async function openAgentView() {
  if (!state.cases.length) {
    await loadCases();
  } else {
    syncActiveCaseSelect();
  }
  await loadAgentSessions();
  const cid = document.getElementById("agCaseId")?.value || state.activeCaseId;
  if (cid) {
    await loadAgentCaseRuns(cid);
  }
}

/** Consulta estado de Playwright, sesiones locales y acciones de login/limpieza de caché. */
export async function loadAgentSessions() {
  const badge = document.getElementById("agentPlaywrightBadge");
  const box = document.getElementById("agentSessionsBox");
  if (!box) return;
  try {
    const data = await api("/api/agent/sessions");
    if (badge) {
      badge.className = `badge ${data.playwright_ready ? "badge-verificado" : "badge-descartado"}`;
      badge.textContent = data.playwright_ready ? "Playwright Chromium listo" : "Playwright no disponible";
    }
    box.innerHTML = (data.sessions || [])
      .map(
        (s) => `<div class="report-row">
          <div>
            <strong>${esc(s.platform.toUpperCase())}</strong>
            <span class="badge ${s.has_saved_session ? "badge-verificado" : "badge-revisar"}">
              ${s.has_saved_session ? "Sesión local guardada" : "Sin sesión"}
            </span>
            ${s.has_saved_session ? `<span class="muted" style="font-size:11.5px;margin-left:6px">Caché: ${Number(s.cache_size_mb || 0).toFixed(1)} MB / Perfil: ${Number(s.profile_size_mb || 0).toFixed(1)} MB</span>` : ""}
          </div>
          <div class="inline-actions">
            <button type="button" class="btn btn-xs btn-primary" data-ag-login="${esc(s.platform)}">Abrir login</button>
            ${s.has_saved_session ? `<button type="button" class="btn btn-xs btn-secondary" data-ag-clean-cache="${esc(s.platform)}" title="Elimina cachés temporales conservando cookies y sesión">Limpiar caché</button>` : ""}
            ${s.has_saved_session ? `<button type="button" class="btn btn-xs btn-danger" data-ag-clear="${esc(s.platform)}">Cerrar sesión</button>` : ""}
          </div>
        </div>`
      )
      .join("");

    box.querySelectorAll("[data-ag-login]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          const out = await api("/api/agent/session/open", {
            method: "POST",
            body: JSON.stringify({ platform: btn.dataset.agLogin }),
          });
          toast(out.message || "Ventana de login abierta.", "ok");
          setTimeout(loadAgentSessions, 2500);
        } catch (err) {
          toast(err.message, "err");
        }
      });
    });

    box.querySelectorAll("[data-ag-clean-cache]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          const out = await api("/api/agent/session/clean-cache", {
            method: "POST",
            body: JSON.stringify({ platform: btn.dataset.agCleanCache, dry_run: false }),
          });
          toast(`Caché de ${btn.dataset.agCleanCache} liberada: ${out.freed_mb ?? 0} MB (sesión y cookies conservadas).`, "ok");
          loadAgentSessions();
        } catch (err) {
          toast(err.message, "err");
        } finally {
          btn.disabled = false;
        }
      });
    });

    box.querySelectorAll("[data-ag-clear]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(`¿Eliminar cookies y sesión local de ${btn.dataset.agClear}?`)) return;
        try {
          await api(`/api/agent/session/${encodeURIComponent(btn.dataset.agClear)}`, { method: "DELETE" });
          toast(`Sesión local de ${btn.dataset.agClear} eliminada.`, "ok");
          loadAgentSessions();
        } catch (err) {
          toast(err.message, "err");
        }
      });
    });
  } catch (err) {
    box.innerHTML = `<div class="muted">No se pudo consultar el estado del agente: ${esc(err.message)}</div>`;
  }
}

/** Lista las ejecuciones pasadas y recientes del agente para el caso seleccionado. */
export async function loadAgentCaseRuns(caseId) {
  const histEl = document.getElementById("agentHistoryRuns");
  if (!histEl || !caseId) return;
  try {
    const data = await api(`/api/cases/${encodeURIComponent(caseId)}/agent/runs`);
    const runs = data.runs || [];
    if (!runs.length) {
      histEl.innerHTML = "";
      return;
    }
    histEl.innerHTML = `
      <div class="panel-card">
        <div class="panel-head"><h2>Sesiones recientes del agente en este caso</h2></div>
        <div class="list-stack">
          ${runs
            .map(
              (r) => `<div class="report-row">
                <div>
                  <span class="badge badge-info">${esc(r.platform)}</span>
                  <strong>${esc(r.target_url)}</strong>
                  <span class="badge ${r.status === "completed" ? "badge-verificado" : r.status === "cancelled" || r.status === "error" ? "badge-descartado" : "badge-revisar"}">${esc(r.status)}</span>
                  ${r.committed ? `<span class="badge badge-verificado">Integrado ✓</span>` : ""}
                  <div class="muted" style="font-size:11.5px">${esc(r.started_at)} · ${esc(r.step)}</div>
                </div>
                <button type="button" class="btn btn-xs btn-ghost" data-ag-open-run="${esc(r.id)}">Ver hallazgos</button>
              </div>`
            )
            .join("")}
        </div>
      </div>
    `;
    histEl.querySelectorAll("[data-ag-open-run]").forEach((btn) => {
      btn.addEventListener("click", () => startAgentPolling(btn.dataset.agOpenRun));
    });
  } catch (err) {
    histEl.innerHTML = "";
  }
}

/** Activa el polling en vivo sobre una corrida de navegador supervisado. */
export function startAgentPolling(runId) {
  state.agentRunId = runId;
  if (state.agentPoller) {
    clearInterval(state.agentPoller);
    state.agentPoller = null;
  }
  const tick = async () => {
    try {
      const run = await api(`/api/agent/runs/${encodeURIComponent(runId)}`);
      renderAgentLiveMonitor(run);
      if (["completed", "cancelled", "error"].includes(run.status)) {
        clearInterval(state.agentPoller);
        state.agentPoller = null;
        loadAgentCaseRuns(run.case_id);
      }
    } catch (err) {
      clearInterval(state.agentPoller);
      state.agentPoller = null;
    }
  };
  tick();
  state.agentPoller = setInterval(tick, 1400);
}

/** Renderiza el monitor en vivo (Pausa/Reanudar/Cancelar, logs y bandeja de staging). */
export function renderAgentLiveMonitor(run) {
  const mon = document.getElementById("agentLiveMonitor");
  if (!mon || !run) return;
  const st = run.stats || {};
  const items = run.items || [];
  const isActive = ["running", "paused", "paused_checkpoint", "waiting_login"].includes(run.status);
  const isPaused = ["paused", "paused_checkpoint"].includes(run.status);

  const itemsHtml = items.length
    ? `<div class="table-wrap" style="margin-top:12px">
        <table class="data-table">
          <thead>
            <tr>
              <th>Incluir</th>
              <th>Tipo</th>
              <th>Autor / Contenido</th>
              <th>Señal previa / Fuente</th>
              <th>Eliminar</th>
            </tr>
          </thead>
          <tbody>
            ${items
              .map((it) => {
                const pl = it.payload || {};
                const relPrev = (pl.relational_preview || [])
                  .map((rp) => `<span class="tag-chip">${esc(rp.category)}: ${esc(rp.expression)} (${esc(rp.epistemic)})</span>`)
                  .join("");
                return `<tr>
                  <td><input type="checkbox" data-ag-chk="${it.id}" ${it.selected ? "checked" : ""} /></td>
                  <td><span class="badge badge-info">${esc(it.item_kind)}</span></td>
                  <td>
                    <div style="font-size:12px"><strong>@${esc(it.author_handle || "perfil")}</strong> ${it.target_handle ? `→ @${esc(it.target_handle)}` : ""} <span class="muted">${esc(it.published_at || "")}</span></div>
                    <div style="font-size:12.5px">${esc((it.content || "").slice(0, 220))}</div>
                  </td>
                  <td>
                    ${pl.sentiment ? `<span class="badge badge-info">${esc(pl.sentiment)}</span>` : ""}
                    ${relPrev ? `<div class="tags-wrap" style="margin-top:4px">${relPrev}</div>` : ""}
                    ${it.source_url ? `<div style="margin-top:4px;font-size:11.5px"><a href="${esc(it.source_url)}" target="_blank" rel="noopener">Fuente</a></div>` : ""}
                  </td>
                  <td><button type="button" class="btn btn-xs btn-danger" data-ag-del="${it.id}">✕</button></td>
                </tr>`;
              })
              .join("")}
          </tbody>
        </table>
      </div>`
    : `<div class="muted" style="margin-top:10px">Esperando hallazgos del navegador...</div>`;

  mon.innerHTML = `
    <div class="panel-card">
      <div class="panel-head">
        <div>
          <h2>Monitor en vivo · ${esc(run.platform.toUpperCase())}</h2>
          <div class="muted">${esc(run.step)} (${run.progress}%)</div>
        </div>
        <div class="inline-actions">
          ${isActive && !isPaused ? `<button type="button" class="btn btn-xs btn-secondary" id="btnAgPause">Pausar</button>` : ""}
          ${isPaused ? `<button type="button" class="btn btn-xs btn-primary" id="btnAgResume">Reanudar</button>` : ""}
          ${isActive ? `<button type="button" class="btn btn-xs btn-danger" id="btnAgCancel">Cancelar</button>` : ""}
        </div>
      </div>

      <div class="progress-track"><div class="progress-fill" style="width:${run.progress}%"></div></div>
      <div class="case-item-meta" style="margin-top:8px">
        <span>Estado: <strong>${esc(run.status)}</strong></span>
        <span>Perfiles: ${st.profiles || 0}</span>
        <span>Posts: ${st.posts || 0}</span>
        <span>Comentarios: ${st.comments || 0}</span>
        <span>Media: ${st.media || 0}</span>
        <span>Señales relacionales: ${st.relational_hits || 0}</span>
      </div>

      ${run.status === "paused_checkpoint" ? `<div class="alert-box alert-warn mt-12"><strong>Atención requerida:</strong> la plataforma mostró una verificación o muro de acceso. Resuélvelo manualmente en la ventana de Chromium abierta y luego pulsa <strong>Reanudar</strong>.</div>` : ""}

      <div class="log-box mt-12">${(run.logs || []).map((l) => `<div>${esc(l)}</div>`).join("")}</div>

      <div class="inline-actions" style="justify-content:space-between;margin-top:14px">
        <strong>Bandeja de revisión (${items.filter((x) => x.selected).length}/${items.length} seleccionados)</strong>
        <button type="button" class="btn btn-sm btn-primary" id="btnAgCommit" ${items.length && !run.committed ? "" : "disabled"}>
          ${run.committed ? "Ya integrado al caso ✓" : "Integrar seleccionados al caso (NLP + Relacional + SNA)"}
        </button>
      </div>
      ${itemsHtml}
    </div>
  `;

  document.getElementById("btnAgPause")?.addEventListener("click", async () => {
    const r = await api(`/api/agent/runs/${encodeURIComponent(run.id)}/pause`, { method: "POST" });
    renderAgentLiveMonitor(r);
  });
  document.getElementById("btnAgResume")?.addEventListener("click", async () => {
    const r = await api(`/api/agent/runs/${encodeURIComponent(run.id)}/resume`, { method: "POST" });
    renderAgentLiveMonitor(r);
  });
  document.getElementById("btnAgCancel")?.addEventListener("click", async () => {
    const r = await api(`/api/agent/runs/${encodeURIComponent(run.id)}/cancel`, { method: "POST" });
    renderAgentLiveMonitor(r);
  });

  mon.querySelectorAll("[data-ag-chk]").forEach((chk) => {
    chk.addEventListener("change", async () => {
      await api(`/api/agent/runs/${encodeURIComponent(run.id)}/items/${chk.dataset.agChk}`, {
        method: "PATCH",
        body: JSON.stringify({ selected: chk.checked }),
      });
    });
  });

  mon.querySelectorAll("[data-ag-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/agent/runs/${encodeURIComponent(run.id)}/items/${btn.dataset.agDel}`, { method: "DELETE" });
      const fresh = await api(`/api/agent/runs/${encodeURIComponent(run.id)}`);
      renderAgentLiveMonitor(fresh);
    });
  });

  document.getElementById("btnAgCommit")?.addEventListener("click", async () => {
    const btn = document.getElementById("btnAgCommit");
    btn.disabled = true;
    try {
      const out = await api(`/api/agent/runs/${encodeURIComponent(run.id)}/commit`, {
        method: "POST",
        body: JSON.stringify({ run_relational: true, run_nlp: true, run_sna: true }),
      });
      toast(`Integrados ${out.committed_items} elementos al caso (NLP + Relacional + SNA actualizados).`, "ok");
      const fresh = await api(`/api/agent/runs/${encodeURIComponent(run.id)}`);
      renderAgentLiveMonitor(fresh);
      loadCases();
      loadAgentCaseRuns(run.case_id);
      refreshFriendScanPanel();
    } catch (err) {
      btn.disabled = false;
      toast(err.message, "err");
    }
  });
}

/** Actualiza el panel de cuentas amigas aisladas, cola secundaria y comparación dHash opt-in. */
export async function refreshFriendScanPanel() {
  const host = document.getElementById("agentFriendScanBody");
  const cid = getActiveCaseId();
  if (!host) return;
  if (!cid) {
    host.innerHTML = `<div class="muted">Selecciona un caso activo para ver candidatos amigos y estado de cola.</div>`;
    return;
  }
  try {
    const [ev, frRes] = await Promise.all([
      api(`/api/cases/${encodeURIComponent(cid)}/friend-evidence`),
      api(`/api/cases/${encodeURIComponent(cid)}/friends`),
    ]);
    const cands = ev.candidates || [];
    const scans = ev.scan_runs || [];
    const matches = ev.visual_matches || [];
    const manualFriends = frRes.friends || [];
    const candsHtml = cands.length
      ? cands.map((c) => `<span class="tag-chip"><strong>@${esc(c.handle)}</strong> (${c.comments_count || c.interactions_count || 0} int · ${Number(c.visual_proximity_score || 0).toFixed(0)}/100)</span>`).join("")
      : `<span class="muted">Sin cuentas amigas candidatas todavía (requiere ≥2 interacciones o reciprocidad).</span>`;
    const manualHtml = manualFriends.length
      ? `<div class="table-wrap" style="margin-top:10px"><table>
          <thead><tr><th>Amigo aislado (link manual)</th><th>Estado</th><th>Contenido</th><th>Acciones</th></tr></thead>
          <tbody>${manualFriends.map((f) => `
            <tr>
              <td><strong>${esc(f.label || f.display_name || f.handle)}</strong><br/><code>@${esc(f.handle)}</code> · ${esc(f.platform)}</td>
              <td><span class="badge ${f.status === "pending_manual" ? "badge-media" : "badge-info"}">${esc(f.status || "active")}</span></td>
              <td><small>${f.posts_count || 0} posts · ${f.media_count || 0} fotos · ${f.locations_count || 0} ubic.</small></td>
              <td style="display:flex;gap:4px;flex-wrap:wrap">
                <button type="button" class="btn btn-xs btn-secondary" data-fr-open="${f.id}">Ver panel</button>
                <button type="button" class="btn btn-xs btn-secondary" data-fr-crawl="${f.id}">Capturar URL</button>
                <button type="button" class="btn btn-xs btn-secondary" data-fr-inv="${f.id}">Investigar</button>
                <button type="button" class="btn btn-xs btn-primary" data-fr-cmp="${f.id}">Comparar con sujeto</button>
                <button type="button" class="btn btn-xs btn-ghost" data-fr-del="${f.id}">Eliminar</button>
              </td>
            </tr>`).join("")}
          </tbody></table></div>`
      : "";
    const scansHtml = scans.length
      ? `<div class="table-wrap" style="margin-top:8px"><table>
          <thead><tr><th>Cuenta</th><th>Estado</th><th>Ítems</th><th>Coincidencias dHash</th></tr></thead>
          <tbody>${scans.map((s) => `
            <tr>
              <td><code>@${esc(s.target_handle)}</code></td>
              <td><span class="badge ${s.status === "done" ? "badge-alta" : s.status === "paused_checkpoint" ? "badge-media" : "badge-info"}">${esc(s.status)}</span></td>
              <td>${Number(s.items_scanned || 0)}</td>
              <td>${Number(s.visual_matches || 0)}</td>
            </tr>`).join("")}
          </tbody></table></div>`
      : "";
    host.innerHTML = `
      <div class="tags-wrap">${candsHtml}</div>
      ${manualHtml}
      <div id="friendIsolatedDetailHost" style="margin-top:12px"></div>
      ${matches.length ? `<div class="alert-box alert-safe" style="margin-top:8px"><strong>${matches.length}</strong> par(es) de imágenes coincidentes por dHash detectados.</div>` : ""}
      ${scansHtml}
    `;

    const openFriendIsolatedPanel = async (fid, compareOut = null) => {
      const box = document.getElementById("friendIsolatedDetailHost");
      if (!box) return;
      box.innerHTML = `<div class="muted">Cargando panel independiente del amigo...</div>`;
      const detail = await api(`/api/cases/${encodeURIComponent(cid)}/friends/${encodeURIComponent(fid)}`);
      const fr = detail.friend || {};
      const ps = detail.public_summary || {};
      const fMedia = detail.media_items || [];
      const fLocs = detail.location_history || [];
      const fPosts = detail.posts || [];
      const gallery = fMedia.length
        ? `<div class="tags-wrap" style="margin-top:6px">${fMedia.slice(0, 12).map((m) => `
            <div style="border:1px solid var(--border);border-radius:8px;padding:6px;width:120px">
              ${m.source_url ? `<img src="${esc(m.source_url)}" style="width:100%;height:78px;object-fit:cover;border-radius:6px" />` : `<div class="muted">IMG</div>`}
              <div style="font-size:10.5px;margin-top:3px;word-break:break-word">${esc((m.filename || "").slice(0, 18))}</div>
            </div>`).join("")}</div>`
        : `<div class="muted" style="font-size:12px">Sin fotos capturadas para este amigo todavía.</div>`;
      const locsHtml = fLocs.length
        ? fLocs.map((l) => `<li><small>${esc((l.sort_date || l.published_at || "—").slice(0, 16))}: <strong>${esc([l.city, l.region, l.country].filter(Boolean).join(", "))}</strong> (${Math.round(Number(l.confidence || 0) * 100)}% · ${esc(l.source)})</small></li>`).join("")
        : `<li><small class="muted">Sin ubicaciones propias inferidas.</small></li>`;
      const cmpHtml = compareOut
        ? `<div class="alert-box ${compareOut.visual_matches?.length ? "alert-safe" : "alert-info"}" style="margin-top:10px">
             <strong>Comparación visual opt-in:</strong> ${(compareOut.visual_matches || []).length} coincidencia(s) dHash · Score: <code>${compareOut.visual_proximity_score || 0}/100</code>
             ${compareOut.visual_matches?.length ? `<div style="margin-top:6px"><button type="button" class="btn btn-xs btn-primary" id="btnConfirmLinkFriendSna">Confirmar y enlazar al grafo SNA del sujeto</button></div>` : ""}
           </div>`
        : "";
      box.innerHTML = `
        <div class="panel-card" style="background:#0c1424">
          <div class="panel-head">
            <h3>Amigos · Investigación independiente: ${esc(fr.label || fr.display_name || fr.handle)}</h3>
            <span class="badge badge-info">Aislado (owner_id=${esc(fr.id)})</span>
          </div>
          <div style="font-size:12px;margin-bottom:6px">
            <strong>Bio pública:</strong> ${esc(ps.bio || "—")}<br/>
            <strong>Intereses:</strong> ${(ps.interests || []).length ? ps.interests.map((x) => `<span class="tag-chip">${esc(x)}</span>`).join("") : "—"}<br/>
            <strong>Lugares:</strong> ${(ps.places || []).length ? esc(ps.places.join(" · ")) : "—"} · <strong>Posts:</strong> ${fPosts.length}
          </div>
          <div><strong>Galería propia del amigo (${fMedia.length}):</strong>${gallery}</div>
          <div style="margin-top:8px"><strong>Historial de ubicaciones propio:</strong><ul>${locsHtml}</ul></div>
          ${cmpHtml}
        </div>
      `;
      document.getElementById("btnConfirmLinkFriendSna")?.addEventListener("click", async () => {
        await api(`/api/cases/${encodeURIComponent(cid)}/friends/${encodeURIComponent(fid)}/link-sna`, {
          method: "POST",
          body: JSON.stringify({
            confirm: true,
            visual_proximity_score: compareOut?.visual_proximity_score || 0,
            matches_count: (compareOut?.visual_matches || []).length || 1,
          }),
        });
        toast("Amigo enlazado al grafo SNA del sujeto tras confirmación.", "ok");
        loadCases();
      });
    };

    host.querySelectorAll("[data-fr-open]").forEach((b) => {
      b.addEventListener("click", () => openFriendIsolatedPanel(b.dataset.frOpen));
    });
    host.querySelectorAll("[data-fr-crawl]").forEach((b) => {
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          const out = await api(`/api/cases/${encodeURIComponent(cid)}/friends/${b.dataset.frCrawl}/crawl`, { method: "POST" });
          if (out.status === "pending_manual") {
            toast(`Login/CAPTCHA detectado (${out.reason || "muro"}); amigo marcado pending_manual.`, "err");
          } else {
            toast(`Capturados ${out.posts_inserted || 0} posts y ${out.media_inserted || 0} fotos (aislados).`, "ok");
          }
          await refreshFriendScanPanel();
          await openFriendIsolatedPanel(b.dataset.frCrawl);
        } catch (err) {
          toast(err.message, "err");
        } finally {
          b.disabled = false;
        }
      });
    });
    host.querySelectorAll("[data-fr-inv]").forEach((b) => {
      b.addEventListener("click", async () => {
        const out = await api(`/api/cases/${encodeURIComponent(cid)}/friends/${b.dataset.frInv}/investigate`, { method: "POST", body: JSON.stringify({ compare_with_subject: false, use_vlm: true }) });
        toast(`Amigo investigado aisladamente (${out.locations_inferred || 0} ubicaciones).`, "ok");
        await refreshFriendScanPanel();
        await openFriendIsolatedPanel(b.dataset.frInv);
      });
    });
    host.querySelectorAll("[data-fr-cmp]").forEach((b) => {
      b.addEventListener("click", async () => {
        const out = await api(`/api/cases/${encodeURIComponent(cid)}/friends/${b.dataset.frCmp}/investigate`, { method: "POST", body: JSON.stringify({ compare_with_subject: true, use_vlm: false }) });
        toast(`Comparación opt-in: ${(out.visual_matches || []).length} coincidencia(s) dHash (score ${out.visual_proximity_score || 0}/100).`, "ok");
        await refreshFriendScanPanel();
        await openFriendIsolatedPanel(b.dataset.frCmp, out);
      });
    });
    host.querySelectorAll("[data-fr-del]").forEach((b) => {
      b.addEventListener("click", async () => {
        await api(`/api/cases/${encodeURIComponent(cid)}/friends/${b.dataset.frDel}`, { method: "DELETE" });
        toast("Amigo, su contenido aislado y su enlace SNA eliminados.", "ok");
        await refreshFriendScanPanel();
        if (typeof loadCases === "function") loadCases();
        if (typeof loadSnaCase === "function" && state.sna?.caseId === cid) {
          loadSnaCase(cid, false);
        }
      });
    });
  } catch (err) {
    host.innerHTML = `<div class="muted">Error cargando cuentas amigas: ${esc(err.message)}</div>`;
  }
}

