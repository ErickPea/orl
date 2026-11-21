// OSINT-Auto :: Punto de entrada ES module: inicialización de eventos DOMContentLoaded y formularios.

import { state, getActiveCaseId, setActiveCaseId, esc, toast, api, apiFormData, requireConsent } from "./state.js";
import { switchView, loadStatus, loadReports, renderReportsList, startJobPolling } from "./core_ui.js";
import { renderPrivacyCard, renderGraphCard, bindFootprintGraph, renderSocialCards, renderProfilesSection, bindProfileFilters, renderBreachBlock, renderEmailResult, renderPhoneResult, renderNameLocationResult, renderMediaResult } from "./search_results.js";
import { loadCases, renderCasesList, loadConnectors, loadPlatformCapabilities, loadVisionStatus, loadNlpStatus, selectCase, renderCaseDetail, loadStorageMaintenance } from "./cases.js";
import { loadSnaCase, renderSnaView } from "./sna.js";
import { loadAgentCaseRuns, startAgentPolling, refreshFriendScanPanel } from "./agent.js";
// ---------------------------------------------------------------------------
// Inicialización de eventos
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  // Navegación sidebar y accesos rápidos
  document.querySelectorAll("[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });
  document.querySelectorAll("[data-jump]").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.jump));
  });
  document.getElementById("btnRefreshAll").addEventListener("click", () => {
    loadStatus();
    loadReports();
    loadCases();
    refreshFriendScanPanel();
    toast("Estado, casos e historial actualizados.");
  });

  document.getElementById("btnRefreshFriendScan")?.addEventListener("click", () => {
    refreshFriendScanPanel();
  });

  document.getElementById("btnStartFriendScan")?.addEventListener("click", async () => {
    const cid = getActiveCaseId();
    if (!cid) return toast("Selecciona un caso activo primero.", "err");
    const authorized = Boolean(document.getElementById("chkFriendScanAuth")?.checked);
    if (!authorized) {
      return toast("Debes marcar la casilla de autorización explícita antes de iniciar.", "err");
    }
    const plat = document.getElementById("agPlatform")?.value || "instagram";
    try {
      const out = await api(`/api/cases/${encodeURIComponent(cid)}/agent/friend-scan`, {
        method: "POST",
        body: JSON.stringify({ authorized: true, platform: plat, parent_run_id: state.agent?.activeRunId || "" }),
      });
      toast(`Cola de cuentas amigas iniciada (${out.queued_count || 0} perfil/es).`, "ok");
      refreshFriendScanPanel();
    } catch (err) {
      toast(err.message, "err");
    }
  });

  document.getElementById("formAddFriendByLink")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const cid = getActiveCaseId();
    if (!cid) return toast("Selecciona un caso activo primero.", "err");
    const url = document.getElementById("inpFriendUrl")?.value.trim() || "";
    const platform = document.getElementById("selFriendPlatform")?.value || "instagram";
    const label = document.getElementById("inpFriendLabel")?.value.trim() || "";
    try {
      const out = await api(`/api/cases/${encodeURIComponent(cid)}/friends`, {
        method: "POST",
        body: JSON.stringify({ url, platform, label }),
      });
      toast(`Amigo añadido (${out.friend?.status || "active"}) y enlazado al Círculo Cercano (SNA).`, "ok");
      document.getElementById("inpFriendUrl").value = "";
      document.getElementById("inpFriendLabel").value = "";
      await refreshFriendScanPanel();
      if (typeof loadCases === "function") loadCases();
      if (typeof loadSnaCase === "function" && state.sna?.caseId === cid) {
        loadSnaCase(cid, false);
      }
    } catch (err) {
      toast(err.message, "err");
    }
  });

  // Selector global de caso activo (topbar)
  const activeSel = document.getElementById("activeCaseSelect");
  if (activeSel) {
    activeSel.addEventListener("change", () => {
      setActiveCaseId(activeSel.value);
      renderCasesList();
      if (state.activeCaseId) {
        const found = state.cases.find((c) => c.id === state.activeCaseId);
        toast(`Caso activo: ${found ? found.title : state.activeCaseId}`, "ok");
      } else {
        toast("Sin caso activo (solo guardado en output/).", "ok");
      }
    });
  }

  // Controles de la Vista Secundaria SNA (Fase 5)
  const snaCaseSel = document.getElementById("snaCaseSelect");
  if (snaCaseSel) {
    snaCaseSel.addEventListener("change", () => {
      state.sna.selectedNodeId = "";
      state.sna.communityFilter = "all";
      if (snaCaseSel.value) loadSnaCase(snaCaseSel.value);
    });
  }
  document.querySelectorAll("#snaViewSwitcher .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.sna.mode = btn.dataset.snaMode;
      renderSnaView();
    });
  });
  const btnSnaRecalc = document.getElementById("btnSnaRecompute");
  if (btnSnaRecalc) {
    btnSnaRecalc.addEventListener("click", () => {
      if (!state.sna.caseId) {
        toast("Selecciona primero un caso para recalcular el grafo SNA.", "err");
        return;
      }
      loadSnaCase(state.sna.caseId, true).then(() => toast("Grafo SNA y comunidades recalculados.", "ok"));
    });
  }
  const btnSnaBack = document.getElementById("btnSnaBackToCase");
  if (btnSnaBack) {
    btnSnaBack.addEventListener("click", () => {
      if (state.sna.caseId) {
        state.selectedCaseId = state.sna.caseId;
      }
      switchView("cases");
    });
  }

  // Controles de Gobernanza / Higiene de Temporales (Fase 6)
  const btnRefSto = document.getElementById("btnRefreshStorage");
  if (btnRefSto) btnRefSto.addEventListener("click", loadStorageMaintenance);
  const btnClean = document.getElementById("btnCleanOrphans");
  if (btnClean) {
    btnClean.addEventListener("click", async () => {
      try {
        const preview = await api("/api/maintenance/cleanup", {
          method: "POST",
          body: JSON.stringify({ dry_run: true }),
        });
        if (!preview.would_delete_count) {
          toast("No hay carpetas temporales huérfanas por limpiar.", "ok");
          return;
        }
        if (!confirm(`Se eliminarán ${preview.would_delete_count} carpeta(s) huérfana(s) en output/ (${preview.would_free_mb} MB) no asociadas a ningún caso activo. ¿Continuar?`)) {
          return;
        }
        const done = await api("/api/maintenance/cleanup", {
          method: "POST",
          body: JSON.stringify({ dry_run: false }),
        });
        toast(`Limpieza completada: ${done.deleted_count} carpetas borradas (${done.freed_mb} MB liberados).`, "ok");
        loadStorageMaintenance();
        loadReports();
        loadStatus();
      } catch (err) {
        toast(err.message, "err");
      }
    });
  }

  // ---- Submit: Crear nuevo caso (Fase 1) ----
  const formCase = document.getElementById("formCreateCase");
  if (formCase) {
    formCase.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const detail = await api("/api/cases", {
          method: "POST",
          body: JSON.stringify({
            title: document.getElementById("cTitle").value.trim(),
            subject_name: document.getElementById("cSubject").value.trim(),
            purpose: document.getElementById("cPurpose").value,
            notes: document.getElementById("cNotes").value.trim(),
            seed_username: document.getElementById("cSeedUser").value.trim(),
            seed_email: document.getElementById("cSeedEmail").value.trim(),
            seed_phone: document.getElementById("cSeedPhone").value.trim(),
            seed_location: document.getElementById("cSeedLoc").value.trim(),
          }),
        });
        formCase.reset();
        const cid = detail.case.id;
        setActiveCaseId(cid);
        state.selectedCaseId = cid;
        await loadCases();
        renderCaseDetail(detail);
        loadStatus();
        toast(`Caso creado y activado: ${detail.case.title}`, "ok");
      } catch (err) {
        toast(err.message, "err");
      }
    });
  }

  // Cambio de modo en vista Correo
  document.querySelectorAll("#emailModeTabs .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#emailModeTabs .seg-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.emailMode = btn.dataset.mode;
      const isGen = state.emailMode === "generate";
      document.getElementById("eTargetLabel").textContent = isGen ? "Username base" : "Correo electrónico";
      document.getElementById("eTarget").placeholder = isGen ? "ej: juanperez" : "ej: usuario@gmail.com";
      document.getElementById("eLimitWrap").classList.toggle("hidden", !isGen);
    });
  });

  // Prefijos rápidos de teléfono
  document.querySelectorAll("#phonePrefixChips .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const inp = document.getElementById("pInput");
      inp.value = chip.dataset.prefix;
      inp.focus();
    });
  });

  // Ver/ocultar contraseña en auditoría k-Anonymity
  document.getElementById("btnTogglePwd").addEventListener("click", () => {
    const inp = document.getElementById("bPwdInput");
    const btn = document.getElementById("btnTogglePwd");
    inp.type = inp.type === "password" ? "text" : "password";
    btn.textContent = inp.type === "password" ? "Ver" : "Ocultar";
  });

  // Filtro de historial
  document.getElementById("historyFilter").addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    const filtered = state.reports.filter((r) =>
      `${r.folder} ${r.type} ${r.target} ${r.date}`.toLowerCase().includes(q)
    );
    renderReportsList("historyList", filtered);
  });

  // ---- Submit: Username ----
  document.getElementById("formUsername").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!requireConsent()) return;
    try {
      const data = await api("/api/search/username", {
        method: "POST",
        body: JSON.stringify({
          username: document.getElementById("uInput").value.trim(),
          country: document.getElementById("uCountry").value || null,
          top: Number(document.getElementById("uTop").value),
          case_id: getActiveCaseId(),
          confirm_legitimate: true,
        }),
      });
      startJobPolling(data.job_id, "username", (job, container) => {
        const r = job.result;
        const gid = `g-${job.id}`;
        container.innerHTML =
          renderGraphCard(gid, r.graph) +
          renderPrivacyCard(r.privacy) +
          renderProfilesSection(job.id, r.summary, r.profiles, r.signals);
        bindFootprintGraph(gid);
        bindProfileFilters(job.id);
      });
    } catch (err) {
      toast(err.message, "err");
    }
  });

  // ---- Submit: Redes Sociales + Metadatos Públicos ----
  document.getElementById("formSocial").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!requireConsent()) return;
    try {
      const data = await api("/api/search/social", {
        method: "POST",
        body: JSON.stringify({
          username: document.getElementById("sInput").value.trim(),
          country: document.getElementById("sCountry").value || null,
          max_enrich: Number(document.getElementById("sMaxEnrich").value),
          fetch_public_meta: document.getElementById("sFetchMeta").checked,
          case_id: getActiveCaseId(),
          confirm_legitimate: true,
        }),
      });
      startJobPolling(data.job_id, "social", (job, container) => {
        const r = job.result;
        const s = r.summary;
        const gid = `g-${job.id}`;
        const kpis = `
          <div class="kpi-grid">
            <div class="kpi-card"><div class="kpi-label">Redes detectadas</div><div class="kpi-value">${s.total}</div><div class="kpi-meta">Plataformas sociales</div></div>
            <div class="kpi-card"><div class="kpi-label">Confianza ALTA</div><div class="kpi-value" style="color:var(--emerald)">${s.alta}</div><div class="kpi-meta">Cruce o metadatos</div></div>
            <div class="kpi-card"><div class="kpi-label">Metadatos públicos</div><div class="kpi-value" style="color:var(--accent)">${s.public_ok}</div><div class="kpi-meta">Sin muro de login</div></div>
            <div class="kpi-card"><div class="kpi-label">Muro de login</div><div class="kpi-value" style="color:var(--amber)">${s.login_wall}</div><div class="kpi-meta">Revisión manual</div></div>
          </div>
        `;
        container.innerHTML =
          kpis +
          renderGraphCard(gid, r.graph) +
          renderPrivacyCard(r.privacy) +
          renderSocialCards(r.social_profiles);
        bindFootprintGraph(gid);
      });
    } catch (err) {
      toast(err.message, "err");
    }
  });

  // ---- Autocompletado de ubicación (Ciudad/Zona y País) ----
  function attachLocationAutocomplete({ inputId, listId, kind, getCountry, onSelect }) {
    const input = document.getElementById(inputId);
    const list = document.getElementById(listId);
    if (!input || !list) return;

    let items = [];
    let activeIdx = -1;
    let timer = null;

    const close = () => {
      list.classList.add("hidden");
      activeIdx = -1;
    };

    const pick = (item) => {
      if (!item) return;
      input.value = item.value;
      if (onSelect) onSelect(item);
      close();
    };

    const render = () => {
      if (!items.length) {
        close();
        return;
      }
      list.innerHTML = items
        .map(
          (it, idx) => `
        <div class="autocomplete-item ${idx === activeIdx ? "active" : ""}" data-ac-idx="${idx}" role="option">
          <span>${esc(it.label)}</span>
          <span class="ac-sub">${esc(it.sub || "")}</span>
        </div>`
        )
        .join("");
      list.classList.remove("hidden");
      list.querySelectorAll(".autocomplete-item").forEach((el) => {
        el.addEventListener("mousedown", (ev) => {
          // mousedown dispara antes que blur del input
          ev.preventDefault();
          pick(items[Number(el.dataset.acIdx)]);
        });
      });
    };

    const querySuggestions = async () => {
      const q = input.value.trim();
      const country = getCountry ? getCountry() : "";
      try {
        const params = new URLSearchParams({ q, kind, country });
        const data = await api(`/api/locations/suggest?${params.toString()}`);
        items = data.suggestions || [];
        activeIdx = items.length ? 0 : -1;
        render();
      } catch {
        close();
      }
    };

    input.addEventListener("input", () => {
      clearTimeout(timer);
      // Debounce corto: instantáneo en catálogo local y suave para consultas remotas a OSM
      timer = setTimeout(querySuggestions, 220);
    });
    input.addEventListener("focus", () => {
      clearTimeout(timer);
      querySuggestions();
    });
    input.addEventListener("blur", () => setTimeout(close, 150));
    input.addEventListener("keydown", (ev) => {
      if (list.classList.contains("hidden") || !items.length) return;
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        activeIdx = (activeIdx + 1) % items.length;
        render();
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        activeIdx = (activeIdx - 1 + items.length) % items.length;
        render();
      } else if (ev.key === "Enter" && activeIdx >= 0) {
        ev.preventDefault();
        pick(items[activeIdx]);
      } else if (ev.key === "Escape") {
        close();
      }
    });
  }

  // Submit: Agente de Navegación Supervisada
  const formAg = document.getElementById("formAgentStart");
  if (formAg) {
    formAg.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!requireConsent()) return;
      const cid = document.getElementById("agCaseId").value;
      if (!cid) {
        toast("Crea y selecciona un caso primero en Casos y Entidades.", "err");
        return;
      }
      try {
        const run = await api(`/api/cases/${encodeURIComponent(cid)}/agent/start`, {
          method: "POST",
          body: JSON.stringify({
            platform: document.getElementById("agPlatform").value,
            target_url: document.getElementById("agTargetUrl").value.trim(),
            max_posts: Number(document.getElementById("agMaxPosts").value),
            delay_seconds: Number(document.getElementById("agDelay").value),
            confirm_legitimate: true,
          }),
        });
        toast("Agente de navegación iniciado.", "ok");
        startAgentPolling(run.id);
      } catch (err) {
        toast(err.message, "err");
      }
    });
  }
  const agCaseSel = document.getElementById("agCaseId");
  if (agCaseSel) {
    agCaseSel.addEventListener("change", () => loadAgentCaseRuns(agCaseSel.value));
  }

  attachLocationAutocomplete({
    inputId: "nCity",
    listId: "nCitySuggestions",
    kind: "city",
    getCountry: () => document.getElementById("nCountry").value.trim(),
    onSelect: (item) => {
      // Al elegir una ciudad, autocompleta también el país automáticamente
      const countryInp = document.getElementById("nCountry");
      if (item.country && countryInp) {
        countryInp.value = item.country;
      }
    },
  });

  attachLocationAutocomplete({
    inputId: "nCountry",
    listId: "nCountrySuggestions",
    kind: "country",
  });

  // ---- Submit: Nombre y Ubicación ----
  document.getElementById("formName").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!requireConsent()) return;
    const platforms = Array.from(
      document.querySelectorAll("#namePlatformsPicker input[type='checkbox']:checked")
    ).map((el) => el.value);
    try {
      const data = await api("/api/search/name", {
        method: "POST",
        body: JSON.stringify({
          full_name: document.getElementById("nFullName").value.trim(),
          city: document.getElementById("nCity").value.trim() || null,
          country: document.getElementById("nCountry").value.trim() || null,
          keyword: document.getElementById("nKeyword").value.trim() || null,
          platforms,
          case_id: getActiveCaseId(),
          confirm_legitimate: true,
        }),
      });
      startJobPolling(data.job_id, "name", renderNameLocationResult);
    } catch (err) {
      toast(err.message, "err");
    }
  });

  // ---- Submit: Correo ----
  document.getElementById("formEmail").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!requireConsent()) return;
    try {
      const data = await api("/api/search/email", {
        method: "POST",
        body: JSON.stringify({
          target: document.getElementById("eTarget").value.trim(),
          mode: state.emailMode,
          limit: Number(document.getElementById("eLimit").value),
          case_id: getActiveCaseId(),
          confirm_legitimate: true,
        }),
      });
      startJobPolling(data.job_id, "email", renderEmailResult);
    } catch (err) {
      toast(err.message, "err");
    }
  });

  // ---- Submit: Teléfono ----
  document.getElementById("formPhone").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!requireConsent()) return;
    try {
      const data = await api("/api/search/phone", {
        method: "POST",
        body: JSON.stringify({
          phone: document.getElementById("pInput").value.trim(),
          case_id: getActiveCaseId(),
          confirm_legitimate: true,
        }),
      });
      startJobPolling(data.job_id, "phone", renderPhoneResult);
    } catch (err) {
      toast(err.message, "err");
    }
  });

  // ---- Submit: Brechas de correo ----
  document.getElementById("formBreachEmail").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!requireConsent()) return;
    try {
      const data = await api("/api/breach/email", {
        method: "POST",
        body: JSON.stringify({
          email: document.getElementById("bEmailInput").value.trim(),
          case_id: getActiveCaseId(),
          confirm_legitimate: true,
        }),
      });
      startJobPolling(data.job_id, "breach", (job, container) => {
        container.innerHTML = renderBreachBlock(job.result.breaches);
      });
    } catch (err) {
      toast(err.message, "err");
    }
  });

  // ---- Submit: Auditoría contraseña propia (k-Anonymity) ----
  document.getElementById("formBreachPwd").addEventListener("submit", async (e) => {
    e.preventDefault();
    const inp = document.getElementById("bPwdInput");
    const out = document.getElementById("results-breach-pwd");
    out.innerHTML = `<div class="muted">Consultando prefijo SHA-1 por k-Anonymity...</div>`;
    try {
      const res = await api("/api/breach/password", {
        method: "POST",
        body: JSON.stringify({ password: inp.value }),
      });
      // Limpiamos el input inmediatamente por higiene de seguridad
      inp.value = "";
      if (res.error) {
        out.innerHTML = `<div class="alert-box alert-warn">${esc(res.error)}</div>`;
      } else if (res.compromised) {
        out.innerHTML = `<div class="alert-box alert-danger"><strong>COMPROMETIDA:</strong> apareció <strong>${Number(res.count).toLocaleString()}</strong> veces en filtraciones públicas. Cámbiala de inmediato.</div>`;
      } else {
        out.innerHTML = `<div class="alert-box alert-safe"><strong>NO ENCONTRADA:</strong> no aparece en la base pública de Pwned Passwords (prefijo enviado: <code>${esc(res.prefix_sent)}</code>).</div>`;
      }
    } catch (err) {
      out.innerHTML = `<div class="alert-box alert-danger">${esc(err.message)}</div>`;
    }
  });

  // ---- Vista Metadatos de Media (Drag & Drop + Upload) ----
  const dropzone = document.getElementById("mediaDropzone");
  const fileInput = document.getElementById("mediaFileInput");
  const btnMedia = document.getElementById("btnInspectMedia");
  const dropTitle = document.getElementById("mediaDropTitle");
  const dropSub = document.getElementById("mediaDropSub");
  const mediaStatus = document.getElementById("mediaStatusMsg");
  let selectedMediaFile = null;

  const setMediaFile = (file) => {
    selectedMediaFile = file || null;
    if (selectedMediaFile) {
      const mb = (selectedMediaFile.size / (1024 * 1024)).toFixed(2);
      dropTitle.textContent = selectedMediaFile.name;
      dropSub.textContent = `${selectedMediaFile.type || "archivo multimedia"} · ${mb} MB`;
      btnMedia.disabled = false;
    } else {
      dropTitle.textContent = "Arrastra aquí tu imagen o video, o haz clic para seleccionarlo";
      dropSub.textContent = "JPG, PNG, WebP, TIFF, HEIC, MP4, MOV, MKV, AVI, WebM (máx. 150 MB)";
      btnMedia.disabled = true;
    }
  };

  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => setMediaFile(fileInput.files[0]));
  dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("dragover"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setMediaFile(e.dataTransfer.files[0]);
    }
  });

  document.getElementById("formMedia").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!selectedMediaFile) return;
    btnMedia.disabled = true;
    mediaStatus.textContent = "Analizando archivo y extrayendo metadatos...";
    const out = document.getElementById("results-media");
    out.innerHTML = "";

    try {
      const fd = new FormData();
      fd.append("file", selectedMediaFile);
      const cid = getActiveCaseId();
      if (cid) fd.append("case_id", cid);
      const data = await apiFormData("/api/media/inspect", fd);

      renderMediaResult(data, out);
      toast(`Metadatos analizados: ${selectedMediaFile.name}`, "ok");
      loadReports();
      if (cid) {
        loadCases();
        if (state.selectedCaseId === cid) selectCase(cid);
      }
    } catch (err) {
      toast(err.message, "err");
      out.innerHTML = `<div class="alert-box alert-danger">${esc(err.message)}</div>`;
    } finally {
      btnMedia.disabled = false;
      mediaStatus.textContent = "";
    }
  });

  // ---- Submit: Pipeline Completo ----
  document.getElementById("formFull").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!requireConsent()) return;
    try {
      const data = await api("/api/search/full", {
        method: "POST",
        body: JSON.stringify({
          username: document.getElementById("fUsername").value.trim(),
          country: document.getElementById("fCountry").value || null,
          known_email: document.getElementById("fEmail").value.trim() || null,
          phone: document.getElementById("fPhone").value.trim() || null,
          case_id: getActiveCaseId(),
          confirm_legitimate: true,
        }),
      });
      startJobPolling(data.job_id, "full", (job, container) => {
        const r = job.result;
        const gid = `g-${job.id}`;
        const emailsHtml = Object.keys(r.emails || {}).length
          ? `<div class="panel-card"><div class="panel-head"><h2>Correos confirmados</h2></div><div class="list-stack">${Object.entries(r.emails)
              .map(([m, s]) => `<div class="report-row"><strong>${esc(m)}</strong><div class="tags-wrap">${s.map((x) => `<span class="tag-chip">${esc(x)}</span>`).join("")}</div></div>`)
              .join("")}</div></div>`
          : "";
        container.innerHTML =
          renderGraphCard(gid, r.graph) +
          renderPrivacyCard(r.privacy) +
          renderProfilesSection(job.id, r.summary, r.profiles, r.signals) +
          emailsHtml +
          renderBreachBlock(r.breaches);
        bindFootprintGraph(gid);
        bindProfileFilters(job.id);
      });
    } catch (err) {
      toast(err.message, "err");
    }
  });

  // Carga inicial
  loadStatus();
  loadReports();
  loadConnectors();
  loadPlatformCapabilities();
  loadVisionStatus();
  loadNlpStatus();
  loadCases();
});

