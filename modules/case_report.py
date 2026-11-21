# -*- coding: utf-8 -*-
"""
Fase 6 :: Reporte Integral del Caso (HTML + PDF + JSON), Evaluación de Exposición/Privacidad,
Anonimización Opcional de Terceros e Higiene de Almacenamiento.
"""
import copy
import html
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from modules import reports, storage, utils
from modules.text_analysis import analyze_case_nlp


# ---------------------------------------------------------------------------
# 1. Evaluación defensiva de exposición / privacidad integral del caso
# ---------------------------------------------------------------------------
def compute_case_exposure(
    detail: dict,
    vision: Optional[dict] = None,
    nlp: Optional[dict] = None,
    sna: Optional[dict] = None,
) -> dict:
    """
    Calcula qué tan fácil resulta perfilar a la entidad del caso usando únicamente
    información pública consolidada (perfiles, fotos, texto y círculo cercano),
    devolviendo un puntaje defensivo (0–100), hallazgos y recomendaciones prácticas.
    """
    profiles = [p for p in (detail.get("profiles") or []) if p.get("manual_status") != "rejected"]
    verified = [p for p in profiles if p.get("resolution_level") == "VERIFICADO"]
    res_meta = detail.get("resolution") or {}
    cross_links = res_meta.get("cross_links") or []

    media_items = (vision or {}).get("media_items") or (detail.get("media_items") or [])
    gps_media = [m for m in media_items if m.get("gps_lat") is not None and m.get("gps_lon") is not None]
    similar_pairs = (vision or {}).get("similar_pairs") or []

    interests = (nlp or {}).get("unified_interests") or []
    high_interests = [i for i in interests if i.get("confidence") == "ALTA"]
    places = ((nlp or {}).get("entities") or {}).get("places") or []
    timeline = (nlp or {}).get("timeline") or {}

    sna_stats = (sna or {}).get("stats") or {}
    intimo_n = int(sna_stats.get("intimo_count") or 0)
    recip_n = int(sna_stats.get("reciprocal_count") or 0)
    comm_n = int(sna_stats.get("communities_count") or 0)

    score = 0
    findings = []
    recommendations = []
    breakdown = {}

    # Dimensión A: Superficie de identidad y correlación entre redes (hasta 30 pts)
    id_pts = min(15, len(verified) * 4) + min(15, len(cross_links) * 5)
    score += id_pts
    breakdown["Identidad y correlación"] = id_pts
    if len(verified) >= 2:
        findings.append(f"{len(verified)} perfiles verificados son correlacionables entre sí ({len(cross_links)} enlaces/señales cruzadas).")
        recommendations.append("Evita reutilizar el mismo username, avatar o enlaces cruzados entre cuentas personales y profesionales.")
    elif len(profiles) >= 1:
        findings.append(f"{len(profiles)} perfiles públicos asociados al caso.")

    # Dimensión B: Exposición multimedia (GPS EXIF y fotos repetidas, hasta 20 pts)
    media_pts = min(14, len(gps_media) * 10) + min(6, len(similar_pairs) * 3)
    score += media_pts
    breakdown["Multimedia y metadatos"] = media_pts
    if gps_media:
        findings.append(f"ALERTA: {len(gps_media)} foto(s) contienen coordenadas GPS exactas en sus metadatos EXIF.")
        recommendations.append("Elimina metadatos EXIF/GPS antes de publicar imágenes (puedes usar el sanitizador integrado en Metadatos de Media).")
    if similar_pairs:
        findings.append(f"Se detectaron {len(similar_pairs)} par(es) de imágenes o avatares visualmente duplicados (dHash).")
        recommendations.append("Usa fotos de perfil distintas en cada red para dificultar la búsqueda inversa y la correlación visual.")

    # Dimensión C: Perfilabilidad de intereses y ubicación (hasta 25 pts)
    nlp_pts = min(15, len(high_interests) * 4 + len(interests)) + min(5, len(places) * 2) + (5 if (timeline.get("dated_items") or 0) >= 5 else 0)
    score += nlp_pts
    breakdown["Intereses y hábitos"] = nlp_pts
    if high_interests:
        top_names = ", ".join(i["label"] for i in high_interests[:3])
        findings.append(f"Perfil temático altamente inferible ({len(high_interests)} intereses con confianza ALTA: {top_names}).")
        recommendations.append("Revisa qué biografías, hashtags y publicaciones públicas revelan ubicaciones frecuentes, horarios o rutinas.")
    if places:
        findings.append(f"Ubicaciones/lugares visibles en contenido público: {', '.join(p['name'] for p in places[:4])}.")
    if (timeline.get("dated_items") or 0) >= 5 and timeline.get("peak_slot"):
        findings.append(f"Patrón horario reconstruible a partir de fechas públicas (pico observable: {timeline['peak_slot']}).")

    # Dimensión D: Exposición del círculo cercano (SNA, hasta 25 pts)
    sna_pts = min(15, intimo_n * 5 + recip_n * 3) + min(10, comm_n * 3)
    score += sna_pts
    breakdown["Círculo cercano (SNA)"] = sna_pts
    if recip_n > 0:
        findings.append(f"Círculo social reconstruible desde comentarios públicos: {intimo_n} vínculo(s) íntimo(s), {recip_n} recíproco(s) y {comm_n} comunidad(es).")
        recommendations.append("Restringe quién puede ver o comentar públicamente tus publicaciones si deseas proteger la privacidad de tu círculo cercano.")

    score = max(0, min(100, int(score)))
    if score >= 75:
        level = "CRÍTICA"
    elif score >= 50:
        level = "ALTA"
    elif score >= 25:
        level = "MODERADA"
    else:
        level = "BAJA"

    if not findings:
        findings.append("Exposición pública mínima según los datos actuales del caso.")
    if not recommendations:
        recommendations.append("Mantén la separación de identificadores y revisa periódicamente tu configuración de privacidad.")

    return {
        "score": score,
        "level": level,
        "breakdown": breakdown,
        "findings": findings,
        "recommendations": list(dict.fromkeys(recommendations)),
    }


# ---------------------------------------------------------------------------
# 2. Anonimización consistente de terceros al exportar
# ---------------------------------------------------------------------------
def anonymize_third_parties(
    sna: Optional[dict],
    nlp: Optional[dict],
    vision: Optional[dict],
    subject_handles: set,
) -> tuple:
    """
    Reemplaza de forma determinista los handles de terceros por seudónimos (`Tercero_01`, ...)
    en SNA (círculo cercano, comunidades, citas), NLP (menciones/citas/keywords) y Visión
    (alt_text/captions). Devuelve `(sna_anon, nlp_anon, vision_anon, anonymized_count)`.
    """
    sna_copy = copy.deepcopy(sna) if sna else None
    nlp_copy = copy.deepcopy(nlp) if nlp else None
    vis_copy = copy.deepcopy(vision) if vision else None
    subj_norm = {(h or "").strip().lstrip("@").lower() for h in (subject_handles or set()) if h}

    mapping = {}  # handle_lower -> "Tercero_01"
    aliases_set = set()

    def alias_for(raw_handle: str) -> str:
        raw = (raw_handle or "").strip()
        if raw in aliases_set:
            return raw
        clean = raw.lstrip("@").lower()
        if not clean or clean in subj_norm:
            return raw_handle
        if clean not in mapping:
            pseudo = f"Tercero_{len(mapping) + 1:02d}"
            mapping[clean] = pseudo
            aliases_set.add(pseudo)
        return mapping[clean]

    # Registrar primero handles del círculo cercano ordenados por cercanía para numeración limpia
    if sna_copy:
        for u in sna_copy.get("close_circle") or []:
            alias_for(u.get("id") or "")

    def mask_text(text: str) -> str:
        if not text:
            return text
        out = str(text)

        def _rep_at(m):
            h = m.group(1)
            if h in aliases_set or h.lower() in subj_norm:
                return m.group(0)
            return f"@{alias_for(h)}"

        out = re.sub(r"@([A-Za-z0-9_.-]{2,60})", _rep_at, out)
        out = re.sub(
            r"\bu/([A-Za-z0-9_-]{2,40})",
            lambda m: m.group(0) if (m.group(1) in aliases_set or m.group(1).lower() in subj_norm) else f"u/{alias_for(m.group(1))}",
            out,
            flags=re.I,
        )
        for real_h, pseudo in list(mapping.items()):
            if len(real_h) >= 3:
                out = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(real_h)}(?![A-Za-z0-9_])", pseudo, out, flags=re.I)
        return out

    def _anonymize_node_dict(node: dict, seen_objs: set) -> None:
        oid = id(node)
        if oid in seen_objs:
            return
        seen_objs.add(oid)
        if not node.get("is_subject"):
            pseudo = alias_for(node.get("id") or "")
            node["id"] = pseudo
            node["label"] = pseudo
            for cm in node.get("sample_comments") or []:
                cm["author"] = alias_for(cm.get("author") or "")
                cm["target"] = alias_for(cm.get("target") or "")
                cm["content"] = mask_text(cm.get("content") or "")
            rcues = node.get("relational_cues") or {}
            for cue in rcues.get("cues") or []:
                cue["context_excerpt"] = mask_text(cue.get("context_excerpt") or "")
                if cue.get("evidence_url"):
                    cue["evidence_url"] = mask_text(cue["evidence_url"])

    if sna_copy:
        seen_objs = set()
        for node in sna_copy.get("nodes") or []:
            _anonymize_node_dict(node, seen_objs)
        for u in sna_copy.get("close_circle") or []:
            _anonymize_node_dict(u, seen_objs)
        for e in sna_copy.get("edges") or []:
            if (e.get("source") or "").lower() not in subj_norm:
                e["source"] = alias_for(e.get("source") or "")
            if (e.get("target") or "").lower() not in subj_norm:
                e["target"] = alias_for(e.get("target") or "")
        for comm in sna_copy.get("communities") or []:
            comm["members"] = [alias_for(m) for m in (comm.get("members") or [])]

    if nlp_copy:
        ent = nlp_copy.get("entities") or {}
        if ent.get("mentions"):
            ent["mentions"] = [
                {"name": alias_for(m["name"]), "count": m["count"]}
                for m in ent["mentions"]
            ]
        if ent.get("keywords"):
            ent["keywords"] = [
                {"word": mask_text(k["word"]), "count": k["count"]}
                for k in ent["keywords"]
            ]
        for it in nlp_copy.get("unified_interests") or []:
            for q in it.get("evidence_quotes") or []:
                q["source"] = mask_text(q.get("source") or "")
                q["quote"] = mask_text(q.get("quote") or "")

    if vis_copy:
        for m in vis_copy.get("media_items") or []:
            if m.get("alt_text"):
                m["alt_text"] = mask_text(m["alt_text"])
            if m.get("caption"):
                m["caption"] = mask_text(m["caption"])

    return sna_copy, nlp_copy, vis_copy, len(mapping)


def _anonymize_extended_sections(
    interactants: list,
    friend_evidence: dict,
    geo_hints_data: dict,
    subject_handles: set,
    sna_orig: Optional[dict] = None,
) -> tuple:
    """
    Aplica la misma asignación determinista `Tercero_NN` sobre interactuantes,
    evidencia de cuentas amigas y pistas de ubicación visual.
    """
    subj_norm = {s.strip().lstrip("@").lower() for s in (subject_handles or set()) if s}
    mapping = {}
    aliases_set = set()
    counter = 0

    def alias_for(handle: str) -> str:
        nonlocal counter
        clean = (handle or "").strip().lstrip("@")
        if not clean:
            return "Tercero"
        low = clean.lower()
        if low in subj_norm or clean in aliases_set:
            return clean
        if low not in mapping:
            counter += 1
            alias = f"Tercero_{counter:02d}"
            mapping[low] = alias
            aliases_set.add(alias)
        return mapping[low]

    if sna_orig:
        for u in sna_orig.get("close_circle") or []:
            alias_for(u.get("id") or "")

    int_copy = copy.deepcopy(interactants or [])
    for it in int_copy:
        h = it.get("handle") or ""
        dn = it.get("display_name") or ""
        pseudo = alias_for(h or dn)
        if dn:
            mapping.setdefault(dn.strip().lower(), pseudo)
        it["handle"] = pseudo
        it["display_name"] = pseudo
        if it.get("profile_url") and (h or "").lower() not in subj_norm:
            it["profile_url"] = "[perfil_anonimizado]"

    fe_copy = copy.deepcopy(friend_evidence or {})
    for c in fe_copy.get("candidates") or []:
        pseudo = alias_for(c.get("handle") or c.get("display_name") or "")
        c["handle"] = pseudo
        c["display_name"] = pseudo
        if c.get("profile_url"):
            c["profile_url"] = "[perfil_anonimizado]"
    for vm in fe_copy.get("visual_matches") or []:
        if vm.get("friend_handle"):
            vm["friend_handle"] = alias_for(vm["friend_handle"])
    for ch in fe_copy.get("consolidated_history") or []:
        pseudo = alias_for(ch.get("handle") or ch.get("display_name") or "")
        ch["handle"] = pseudo
        ch["display_name"] = pseudo
        if ch.get("profile_url"):
            ch["profile_url"] = "[perfil_anonimizado]"
        for item in ch.get("history") or []:
            for fld in ("text", "source_url", "thumbnail_url"):
                val = item.get(fld) or ""
                if val:
                    for real_h, al in list(mapping.items()):
                        if len(real_h) >= 3:
                            val = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(real_h)}(?![A-Za-z0-9_])", al, val, flags=re.I)
                    item[fld] = val

    geo_copy = copy.deepcopy(geo_hints_data or {})
    return int_copy, fe_copy, geo_copy, len(mapping)


# ---------------------------------------------------------------------------
# 3. Generador de Reporte Integral (HTML + PDF + JSON)
# ---------------------------------------------------------------------------
def _render_integral_html(payload: dict) -> str:
    c = payload["case"]
    exp = payload["exposure"]
    res_stats = (payload.get("resolution") or {}).get("stats") or {}
    profiles = payload.get("profiles") or []
    nlp = payload.get("nlp") or {}
    vision = payload.get("vision") or {}
    sna = payload.get("sna") or {}
    gov = payload["governance"]

    exp_color = (
        "#f43f5e" if exp["level"] in ("CRÍTICA", "ALTA")
        else ("#f59e0b" if exp["level"] == "MODERADA" else "#10b981")
    )

    def _badge(txt: str, cls: str = "b-info") -> str:
        return f'<span class="badge {cls}">{html.escape(str(txt))}</span>'

    # Sección 1: Perfiles
    prof_rows = ""
    for p in profiles:
        lv = p.get("resolution_level") or "REVISAR"
        bcls = "b-ok" if lv == "VERIFICADO" else ("b-warn" if lv in ("PROBABLE", "REVISAR") else "b-err")
        reasons = "; ".join((p.get("resolution_reasons") or [])[:3])
        prof_rows += (
            f"<tr>"
            f"<td><strong>{html.escape(p.get('site') or '')}</strong><br>"
            f"<a href=\"{html.escape(p.get('url') or '')}\" target=\"_blank\" rel=\"noopener\">{html.escape(p.get('url') or '')}</a></td>"
            f"<td>{_badge(lv, bcls)} <code>{p.get('resolution_score', 0)}/100</code></td>"
            f"<td>{html.escape((p.get('bio') or p.get('location') or '—')[:160])}</td>"
            f"<td><small>{html.escape(reasons or '—')}</small></td>"
            f"</tr>"
        )

    # Sección 2: Intereses unificados
    interests_html = ""
    for it in (nlp.get("unified_interests") or []):
        quotes_li = ""
        if gov.get("include_quotes", True):
            quotes_li = "".join(
                f"<li><small>{html.escape(q.get('source') or '')}:</small> “{html.escape(q.get('quote') or '')}”</li>"
                for q in (it.get("evidence_quotes") or [])[:3]
            )
        interests_html += (
            f'<div class="subcard">'
            f'<div><strong>{html.escape(it.get("label") or "")}</strong> '
            f'{_badge(it.get("modality") or "Texto", "b-info")} '
            f'{_badge(it.get("confidence") or "MEDIA", "b-ok" if it.get("confidence") == "ALTA" else "b-warn")} '
            f'<small>Tono: {html.escape(it.get("dominant_tone") or "NEUTRO")}</small></div>'
            f'{f"<ul>{quotes_li}</ul>" if quotes_li else ""}'
            f"</div>"
        )

    # Sección 3: Galería visual / dHash
    vis_themes = ", ".join(f"{t['label']} ({t['count']})" for t in (vision.get("theme_summary") or [])) or "Sin etiquetas visuales"
    vis_dups = "".join(
        f"<li>{html.escape(pr['relation'])} ({pr['similarity_pct']}%): <code>{html.escape(pr['filename_a'])}</code> ↔ <code>{html.escape(pr['filename_b'])}</code></li>"
        for pr in (vision.get("similar_pairs") or [])
    )

    # Sección 4: Círculo cercano y comunidades (SNA)
    sna_rows = ""
    for u in (sna.get("close_circle") or [])[:15]:
        themes_s = ", ".join(t["label"] for t in (u.get("shared_themes") or [])) or "—"
        tone = (u.get("relationship_sentiment") or {}).get("dominant") or "NEUTRO"
        recip = "↔ Recíproco" if u.get("is_reciprocal") else "→ Unidir."
        sna_rows += (
            f"<tr>"
            f"<td><strong>{html.escape(u.get('label') or '')}</strong><br><small>{html.escape(u.get('community_label') or '')}</small></td>"
            f"<td>{_badge(u.get('tier') or 'PERIFERICO', 'b-ok' if u.get('tier') == 'INTIMO' else 'b-info')}</td>"
            f"<td><code>{u.get('closeness_score', 0)}/100</code></td>"
            f"<td>{html.escape(recip)} ({u.get('sent_to_them', 0)}→ / {u.get('received_from_them', 0)}←)</td>"
            f"<td>{html.escape(themes_s)} · <small>{html.escape(tone)}</small></td>"
            f"</tr>"
        )

    comm_items = "".join(
        f"<li><strong>{html.escape(cm['label'])}</strong> ({cm['size']} miembros · Tono {html.escape(cm['dominant_tone'])}): "
        f"{html.escape(', '.join(cm.get('members') or []))}</li>"
        for cm in (sna.get("communities") or [])
    )

    anon_banner = (
        f'<div class="banner-ok">Modo Privacidad activo: <strong>{gov["anonymized_count"]}</strong> cuenta(s) de terceros fueron anonimizadas en este reporte.</div>'
        if gov.get("anonymize_third_parties")
        else '<div class="banner-warn">Exportación sin anonimización de terceros. Maneja este archivo de forma responsable y confidencial.</div>'
    )

    findings_li = "".join(f"<li>{html.escape(f)}</li>" for f in exp.get("findings") or [])
    recs_li = "".join(f"<li>{html.escape(r)}</li>" for r in exp.get("recommendations") or [])
    caveats_li = "".join(f"<li>{html.escape(cv)}</li>" for cv in (nlp.get("caveats") or []))

    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <title>Reporte Integral · {html.escape(c.get('title') or 'Caso')}</title>
  <style>
    body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:1040px;margin:24px auto;padding:0 18px;background:#080d1a;color:#e2e8f0;line-height:1.55}}
    h1{{color:#f8fafc;margin:0 0 6px}} h2{{color:#38bdf8;border-bottom:1px solid #1e293b;padding-bottom:6px;margin-top:26px}}
    .card{{background:#131d33;border:1px solid rgba(148,163,184,.18);border-radius:12px;padding:18px;margin:16px 0}}
    .subcard{{background:#0f172a;border:1px solid rgba(148,163,184,.15);border-radius:8px;padding:12px;margin:10px 0}}
    .kpi-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0}}
    .kpi{{background:#0f172a;border:1px solid rgba(148,163,184,.15);border-radius:10px;padding:12px}}
    .kpi-lbl{{font-size:11.5px;color:#94a3b8}} .kpi-val{{font-size:22px;font-weight:700;color:#f8fafc;margin-top:2px}}
    table{{width:100%;border-collapse:collapse;font-size:13px}} th,td{{text-align:left;padding:9px 10px;border-bottom:1px solid rgba(148,163,184,.14);vertical-align:top}}
    th{{color:#94a3b8;font-size:11.5px;text-transform:uppercase}} a{{color:#7dd3fc;word-break:break-all}}
    code{{font-family:ui-monospace,Consolas,monospace;background:#0b1222;padding:1px 5px;border-radius:4px}}
    .badge{{display:inline-block;padding:2px 7px;border-radius:999px;font-size:11px;font-weight:600}}
    .b-ok{{background:rgba(16,185,129,.18);color:#10b981}} .b-warn{{background:rgba(245,158,11,.18);color:#f59e0b}}
    .b-err{{background:rgba(244,63,94,.18);color:#f43f5e}} .b-info{{background:rgba(56,189,248,.18);color:#38bdf8}}
    .meter-bg{{height:12px;background:#0f172a;border-radius:999px;overflow:hidden;margin:8px 0}}
    .meter-fill{{height:100%;background:{exp_color};width:{exp['score']}%}}
    .banner-ok{{background:rgba(16,185,129,.12);border:1px solid rgba(16,185,129,.35);padding:10px 14px;border-radius:8px;margin:12px 0;font-size:13px}}
    .banner-warn{{background:rgba(245,158,11,.12);border:1px solid rgba(245,158,11,.35);padding:10px 14px;border-radius:8px;margin:12px 0;font-size:13px}}
    small{{color:#94a3b8}}
    @media print{{body{{background:#fff;color:#0f172a}} .card,.subcard,.kpi{{background:#f8fafc;border-color:#cbd5e1}} a{{color:#0369a1}}}}
  </style>
</head>
<body>
  <div class="card">
    <h1>OSINT-Auto · Reporte Integral del Caso</h1>
    <div><strong>Caso:</strong> {html.escape(c.get('title') or '')} · <strong>Entidad:</strong> {html.escape(c.get('subject_name') or '')} · <strong>Propósito:</strong> {html.escape(c.get('purpose') or '')}</div>
    <small>Generado el {html.escape(payload['generated_at'])} · Solo fuentes públicas abiertas y datos autorizados.</small>
    {anon_banner}
    <div class="kpi-grid">
      <div class="kpi"><div class="kpi-lbl">Exposición pública</div><div class="kpi-val" style="color:{exp_color}">{exp['score']}/100 ({html.escape(exp['level'])})</div></div>
      <div class="kpi"><div class="kpi-lbl">Perfiles verificados</div><div class="kpi-val">{res_stats.get('verified', 0)}/{len(profiles)}</div></div>
      <div class="kpi"><div class="kpi-lbl">Intereses detectados</div><div class="kpi-val">{len(nlp.get('unified_interests') or [])}</div></div>
      <div class="kpi"><div class="kpi-lbl">Círculo cercano (SNA)</div><div class="kpi-val">{(sna.get('stats') or {}).get('alters_count', 0)} vínculos</div></div>
    </div>
  </div>

  <div class="card">
    <h2>1. Evaluación Defensiva de Exposición y Privacidad</h2>
    <div>Nivel de exposición: <strong style="color:{exp_color}">{html.escape(exp['level'])} ({exp['score']}/100)</strong></div>
    <div class="meter-bg"><div class="meter-fill"></div></div>
    <h3>Hallazgos clave</h3><ul>{findings_li}</ul>
    <h3>Acciones recomendadas para reducir exposición</h3><ul>{recs_li}</ul>
  </div>

  <div class="card">
    <h2>2. Identidad y Perfiles Consolidados (Resolución de Entidades)</h2>
    {f'<table><thead><tr><th>Plataforma / URL</th><th>Resolución</th><th>Bio / Ubicación</th><th>Evidencia</th></tr></thead><tbody>{prof_rows}</tbody></table>' if prof_rows else '<p><small>Sin perfiles registrados en el caso.</small></p>'}
  </div>

  <div class="card">
    <h2>3. Intereses Unificados (Texto + Fotos) y Resumen NLP</h2>
    <p>{html.escape((nlp.get('summary') or {}).get('executive_summary') or 'Sin análisis textual disponible.')}</p>
    {interests_html or '<p><small>Sin intereses detectados todavía.</small></p>'}
    {f'<h3>Cautelas metodológicas</h3><ul>{caveats_li}</ul>' if caveats_li else ''}
  </div>

  {f'''<div class="card">
    <h2>4. Galería Visual y Duplicados Perceptuales (dHash)</h2>
    <p><strong>Fotos analizadas:</strong> {(vision.get('stats') or {}).get('analyzed', 0)}/{(vision.get('stats') or {}).get('total_media', 0)} · <strong>Temas visuales:</strong> {html.escape(vis_themes)}</p>
    {f"<h3>Pares de imágenes similares</h3><ul>{vis_dups}</ul>" if vis_dups else "<p><small>No se detectaron imágenes duplicadas por dHash.</small></p>"}
  </div>''' if gov.get("include_gallery", True) else ""}

  <div class="card">
    <h2>5. Círculo Cercano y Comunidades (SNA)</h2>
    {f'<table><thead><tr><th>Cuenta / Comunidad</th><th>Anillo</th><th>Cercanía</th><th>Reciprocidad</th><th>Temas y Tono</th></tr></thead><tbody>{sna_rows}</tbody></table>' if sna_rows else '<p><small>Sin interacciones suficientes para construir el círculo cercano.</small></p>'}
    {f'<h3>Comunidades detectadas</h3><ul>{comm_items}</ul>' if comm_items else ''}
  </div>

  <div class="card">
    <h2>6. Interactuantes por publicación</h2>
    {''.join([
        '<table><thead><tr><th>Interactuante</th><th>Handle / Plataforma</th><th>Comentarios</th><th>Primera / Última</th><th>Sentimiento</th></tr></thead><tbody>'
        + ''.join(
            f"<tr><td><strong>{html.escape(it.get('display_name') or it.get('handle') or '')}</strong></td>"
            f"<td><code>{html.escape(it.get('handle') or '')}</code> ({html.escape(it.get('platform') or '')})"
            f"{'<br><small>' + html.escape(it.get('profile_url') or '') + '</small>' if it.get('profile_url') else ''}</td>"
            f"<td>{int(it.get('comments_count') or 0)}</td>"
            f"<td><small>{html.escape((it.get('first_seen') or '—')[:16])} / {html.escape((it.get('last_seen') or '—')[:16])}</small></td>"
            f"<td>{_badge(it.get('dominant_sentiment') or 'NEUTRAL', 'b-ok' if it.get('dominant_sentiment') == 'POSITIVO' else ('b-err' if it.get('dominant_sentiment') == 'NEGATIVO' else 'b-info'))}</td></tr>"
            for it in (payload.get('interactants') or [])[:30]
        )
        + '</tbody></table>'
    ]) if (payload.get('interactants') or []) else '<p><small>Sin interactuantes nominales registrados.</small></p>'}
  </div>

  <div class="card">
    <h2>7. Cuentas amigas, evidencia visual e historial compartido</h2>
    {''.join([
        '<table><thead><tr><th>Cuenta amiga</th><th>Proximidad visual</th><th>Coincidencias dHash</th><th>Eventos</th></tr></thead><tbody>'
        + ''.join(
            f"<tr><td><strong>{html.escape(ch.get('display_name') or ch.get('handle') or '')}</strong></td>"
            f"<td><code>{ch.get('visual_proximity_score', 0)}/100</code></td>"
            f"<td>{int(ch.get('visual_matches_count') or 0)}</td>"
            f"<td>{len(ch.get('history') or [])} evento(s)</td></tr>"
            for ch in ((payload.get('friend_evidence') or {}).get('consolidated_history') or [])[:20]
        )
        + '</tbody></table>'
    ]) if ((payload.get('friend_evidence') or {}).get('consolidated_history') or []) else '<p><small>Sin cuentas amigas candidatas o evidencia visual registrada.</small></p>'}
  </div>

  <div class="card">
    <h2>8. Pistas de Ubicación Visual (Probabilístico)</h2>
    <p><small>Estimación probabilística basada en pistas visuales, EXIF y menciones; nunca afirma ubicación exacta sin evidencia explícita.</small></p>
    {''.join([
        f'<div class="subcard"><div><strong>{html.escape(cand.get("label") or "")}</strong> '
        f'{_badge(cand.get("source") or "local", "b-ok" if cand.get("has_exif") else "b-info")} '
        f'<code>{int(round(float(cand.get("confidence") or 0)*100))}%</code></div>'
        f'<div class="meter-bg"><div class="meter-fill" style="width:{int(round(float(cand.get("confidence") or 0)*100))}%"></div></div>'
        f'<div><small>{html.escape(cand.get("probabilistic_statement") or "")}</small></div>'
        f'<ul>{"".join(f"<li><small>{html.escape(ev)}</small></li>" for ev in (cand.get("evidence") or [])[:3])}</ul></div>'
        for cand in ((payload.get('geo_hints') or {}).get('candidates') or [])[:12]
    ]) if ((payload.get('geo_hints') or {}).get('candidates') or []) else '<p><small>Sin pistas de ubicación visual calculadas todavía.</small></p>'}
  </div>

  <div class="card">
    <h2>9. Galería de Fotos del Caso e Historial de Ubicaciones</h2>
    {''.join([
        '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">'
        '<input id="galFilterAuthor" placeholder="Filtrar autor..." style="padding:5px 8px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#e2e8f0" />'
        '<input id="galFilterLoc" placeholder="Filtrar ubicación..." style="padding:5px 8px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#e2e8f0" />'
        '<input id="galFilterConf" type="number" min="0" max="100" value="0" placeholder="Conf. mín %" style="width:110px;padding:5px 8px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#e2e8f0" />'
        '</div>'
        '<div id="repGalleryGrid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px">'
        + ''.join(
            f'<div class="subcard gal-item" data-author="{html.escape((mg.get("author") or "subject").lower())}" '
            f'data-loc="{html.escape((mg.get("possible_location") or "").lower())}" '
            f'data-conf="{int(round(float(mg.get("confidence") or 0)*100))}">'
            f"""{f'<img src="{html.escape(mg.get("source_url") or "")}" alt="{html.escape(mg.get("filename") or "foto")}" style="width:100%;height:120px;object-fit:cover;border-radius:6px;margin-bottom:6px" />' if mg.get("source_url") else ""}"""
            f'<div><strong>{html.escape(mg.get("filename") or "foto")}</strong> · <small>{html.escape(mg.get("author") or "subject")}</small></div>'
            f'<div><small>Fecha: {html.escape((mg.get("published_at") or "—")[:19])}</small></div>'
            f'<div><strong>Ubicación:</strong> {html.escape(mg.get("possible_location") or "No determinada")} '
            f'{_badge(("Confirmada analista" if mg.get("is_manual_override") else (mg.get("source") or "—")), "b-ok" if mg.get("is_manual_override") else "b-info")} '
            f'<code>{int(round(float(mg.get("confidence") or 0)*100))}%</code></div>'
            f'<div><small>{html.escape((mg.get("evidence_quote") or "")[:140])}</small></div>'
            f"""{f'<a href="{html.escape(mg.get("post_url") or "")}" target="_blank">Ver publicación</a>' if mg.get("post_url") else ""}"""
            f'</div>'
            for mg in (payload.get("media_gallery") or [])[:24]
        )
        + '</div>'
        + '<script>(function(){const fa=document.getElementById("galFilterAuthor"),fl=document.getElementById("galFilterLoc"),fc=document.getElementById("galFilterConf");function f(){const a=(fa.value||"").toLowerCase(),l=(fl.value||"").toLowerCase(),c=Number(fc.value||0);document.querySelectorAll("#repGalleryGrid .gal-item").forEach(el=>{const okA=!a||(el.dataset.author||"").includes(a);const okL=!l||(el.dataset.loc||"").includes(l);const okC=Number(el.dataset.conf||0)>=c;el.style.display=(okA&&okL&&okC)?"":"none";});}[fa,fl,fc].forEach(x=>x&&x.addEventListener("input",f));})();</script>'
    ]) if (payload.get("media_gallery") or []) else '<p><small>Sin fotos registradas en la galería del caso.</small></p>'}
  </div>

  <div class="card">
    <h2>10. Círculo de Amigos Relacionados</h2>
    {('<div class="banner-warn">Nombres de amigos confirmados revelados por decisión del analista.</div>' if (payload.get("governance") or {}).get("reveal_confirmed_friends") else '')}
    {''.join([
        '<table><thead><tr><th>Amigo</th><th>Plataforma</th><th>Interacciones (1.ª / Últ.)</th><th>Prox. Visual</th><th>Establecimiento</th></tr></thead><tbody>'
        + ''.join(
            f"<tr><td><strong>{html.escape(fc.get('display_name') or fc.get('handle') or '')}</strong><br/><code>@{html.escape(fc.get('handle') or '')}</code>"
            f"""{f'<br/><a href="{html.escape(fc.get("profile_url") or "")}" target="_blank"><small>{html.escape(fc.get("profile_url") or "")}</small></a>' if fc.get("profile_url") else ""}</td>"""
            f"<td>{html.escape(fc.get('platform') or '')}</td>"
            f"<td>{int(fc.get('interactions_count') or 0)}<br/><small>{html.escape((fc.get('first_seen') or '—')[:10])} / {html.escape((fc.get('last_seen') or '—')[:10])}</small></td>"
            f"<td><code>{fc.get('visual_proximity_score', 0)}/100</code></td>"
            f"<td>{_badge(fc.get('epistemic_label') or 'ambiguous', 'b-ok' if fc.get('epistemic_label') == 'explicit' else 'b-info')}</td></tr>"
            for fc in (payload.get("friends_circle") or [])[:25]
        )
        + '</tbody></table>'
    ]) if (payload.get("friends_circle") or []) else '<p><small>Sin amigos relacionados registrados.</small></p>'}
  </div>

  <div class="card">
    <h2>11. Perfil Enriquecido del Sujeto (7 Categorías)</h2>
    {''.join([
        f'<div class="subcard"><div><strong>{html.escape(sp.get("category") or "")}</strong>: {html.escape(sp.get("value") or "")} '
        f'{_badge(sp.get("epistemic_status") or "ambiguous", "b-ok" if sp.get("epistemic_status") == "explicit" else "b-info")} '
        f'<code>{int(round(float(sp.get("confidence") or 0)*100))}%</code> '
        f'<small>({html.escape(sp.get("human_review") or "pending")})</small></div>'
        + (
            f'<div><small>Evidencia: {html.escape("; ".join(str(rf.get("quote") or "") for rf in (sp.get("evidence_refs") or [])[:2] if isinstance(rf, dict) and rf.get("quote")))}</small></div>'
            if gov.get("include_quotes", True) and any(isinstance(rf, dict) and rf.get("quote") for rf in (sp.get("evidence_refs") or []))
            else ""
        )
        + '</div>'
        for sp in (payload.get("subject_profile") or [])[:35]
    ]) if (payload.get("subject_profile") or []) else '<p><small>Sin entradas de perfil del sujeto calculadas todavía.</small></p>'}
  </div>
</body>
</html>"""


def _build_pdf_sections(payload: dict) -> dict:
    c = payload["case"]
    exp = payload["exposure"]
    nlp = payload.get("nlp") or {}
    sna = payload.get("sna") or {}
    vision = payload.get("vision") or {}
    gov = payload["governance"]

    sections = {
        "Resumen del Caso": [
            f"Titulo: {c.get('title') or ''}",
            f"Entidad principal: {c.get('subject_name') or ''}",
            f"Proposito: {c.get('purpose') or ''}",
            f"Anonimizacion de terceros: {'SI (' + str(gov['anonymized_count']) + ' protegidos)' if gov.get('anonymize_third_parties') else 'NO'}",
        ],
        "1. Evaluacion de Exposicion y Privacidad": [
            f"Nivel de exposicion: {exp['level']} ({exp['score']}/100)",
            *(f"Hallazgo: {f}" for f in exp.get("findings") or []),
            *(f"Recomendacion: {r}" for r in exp.get("recommendations") or []),
        ],
        "2. Perfiles Consolidados (Resolucion de Entidades)": [
            f"[{p.get('resolution_level', 'REVISAR')} {p.get('resolution_score', 0)}/100] {p.get('site', '')}: {p.get('url', '')}"
            for p in (payload.get("profiles") or [])[:40]
        ] or ["(sin perfiles registrados)"],
        "3. Intereses Unificados (Texto + Fotos)": [
            (nlp.get("summary") or {}).get("executive_summary") or "Sin resumen NLP.",
            *[
                f"[{it.get('confidence', 'MEDIA')} | {it.get('modality', 'Texto')}] {it.get('label', '')} (tono: {it.get('dominant_tone', 'NEUTRO')})"
                for it in (nlp.get("unified_interests") or [])[:15]
            ],
        ],
        "4. Galeria Visual y dHash": [
            f"Fotos analizadas: {(vision.get('stats') or {}).get('analyzed', 0)}/{(vision.get('stats') or {}).get('total_media', 0)}",
            *[f"Tema visual: {t['label']} ({t['count']} foto/s)" for t in (vision.get("theme_summary") or [])[:10]],
            *[f"Similar ({pr['similarity_pct']}%): {pr['filename_a']} <-> {pr['filename_b']}" for pr in (vision.get("similar_pairs") or [])[:10]],
        ],
        "5. Circulo Cercano y Comunidades (SNA)": [
            f"[{u.get('tier', '')} {u.get('closeness_score', 0)}/100] {u.get('label', '')} "
            f"({'reciproco' if u.get('is_reciprocal') else 'unidir.'}, comunidad: {u.get('community_label', '')}, "
            f"tono: {(u.get('relationship_sentiment') or {}).get('dominant', 'NEUTRO')})"
            for u in (sna.get("close_circle") or [])[:20]
        ] or ["(sin interacciones suficientes)"],
        "6. Interactuantes por publicacion": [
            f"{it.get('display_name') or it.get('handle', '')} ({it.get('platform', '')}): "
            f"{it.get('comments_count', 0)} comentario(s), tono {it.get('dominant_sentiment', 'NEUTRAL')}"
            for it in (payload.get("interactants") or [])[:25]
        ] or ["(sin interactuantes registrados)"],
        "7. Cuentas amigas y evidencia visual": [
            f"{ch.get('display_name') or ch.get('handle', '')}: proximidad visual {ch.get('visual_proximity_score', 0)}/100, "
            f"{ch.get('visual_matches_count', 0)} coincidencia(s) dHash, {len(ch.get('history') or [])} evento(s)"
            for ch in ((payload.get("friend_evidence") or {}).get("consolidated_history") or [])[:20]
        ] or ["(sin cuentas amigas candidatas)"],
        "8. Pistas de Ubicacion Visual (Probabilistico)": [
            f"[{int(round(float(cand.get('confidence') or 0)*100))}% | {cand.get('source', '')}] "
            f"{cand.get('label', '')} - {cand.get('probabilistic_statement', '')}"
            for cand in ((payload.get("geo_hints") or {}).get("candidates") or [])[:12]
        ] or ["(sin pistas de ubicacion visual)"],
        "9. Galeria de Fotos y Ubicaciones": [
            f"{mg.get('filename', 'foto')} ({mg.get('author', 'subject')} | {(mg.get('published_at') or '')[:10]}): "
            f"{mg.get('possible_location') or 'No determinada'} [{int(round(float(mg.get('confidence') or 0)*100))}% | {mg.get('source', '—')}]"
            for mg in (payload.get("media_gallery") or [])[:24]
        ] or ["(sin fotos en galeria)"],
        "10. Circulo de Amigos Relacionados": [
            f"{fc.get('display_name') or fc.get('handle', '')} ({fc.get('platform', '')}): "
            f"{fc.get('interactions_count', 0)} int., prox. visual {fc.get('visual_proximity_score', 0)}/100 [{fc.get('epistemic_label', 'ambiguous')}]"
            for fc in (payload.get("friends_circle") or [])[:25]
        ] or ["(sin amigos relacionados)"],
        "11. Perfil Enriquecido del Sujeto": [
            f"[{sp.get('category', '')} | {sp.get('epistemic_status', '')}] {sp.get('value', '')} ({int(round(float(sp.get('confidence') or 0)*100))}%)"
            for sp in (payload.get("subject_profile") or [])[:30]
        ] or ["(sin perfil enriquecido)"],
    }
    if nlp.get("caveats"):
        sections["Cautelas Metodologicas"] = nlp["caveats"]
    return sections


def generate_case_report(
    case_id: str,
    anonymize: bool = True,
    include_quotes: bool = True,
    include_gallery: bool = True,
    reveal_confirmed_friends: bool = False,
) -> Optional[dict]:
    """
    Genera el Reporte Integral del Caso (HTML + PDF + JSON) en `output/<fecha>_case_<titulo>/`
    aplicando anonimización opcional de terceros y registrando la exportación en `case_runs`.
    """
    detail = storage.get_case_detail(case_id)
    if not detail:
        return None

    vision = storage.get_case_vision_summary(case_id) or {}
    nlp = storage.get_case_nlp_analysis(case_id) or {}
    if not nlp.get("analyzed"):
        corpus = storage.get_case_corpus_for_nlp(case_id)
        if corpus:
            nlp_fresh = analyze_case_nlp(corpus, provider="local")
            storage.save_case_nlp_analysis(case_id, nlp_fresh)
            nlp = storage.get_case_nlp_analysis(case_id) or {}

    sna = storage.compute_and_save_case_sna(case_id, record_run=False) or {}
    exposure = compute_case_exposure(detail, vision=vision, nlp=nlp, sna=sna)

    subject_handles = {
        (i["value"] or "").strip().lstrip("@").lower()
        for i in (detail.get("identifiers") or [])
        if i.get("kind") == "username"
    }
    for p in detail.get("profiles") or []:
        if p.get("username") and p.get("manual_status") != "rejected":
            subject_handles.add(p["username"].strip().lstrip("@").lower())

    export_sna, export_nlp, export_vision, anon_count = (
        anonymize_third_parties(sna, nlp, vision, subject_handles)
        if anonymize
        else (sna, nlp, vision, 0)
    )

    from modules.browser_agent import get_case_friend_evidence
    from modules.geo_inference import infer_case_locations

    raw_interactants = storage.list_case_interactants(case_id)
    raw_friend_ev = get_case_friend_evidence(case_id)
    raw_content = storage.get_case_content(case_id)
    raw_geo_hints = storage.list_case_geo_hints(case_id)
    geo_data = infer_case_locations(
        geo_hints=raw_geo_hints,
        media_items=raw_content.get("media_items") or [],
        posts=raw_content.get("posts") or [],
        nlp_data=nlp,
    )
    geo_data["hints"] = raw_geo_hints

    if anonymize:
        export_interactants, export_friend_ev, export_geo, ext_anon_count = _anonymize_extended_sections(
            raw_interactants, raw_friend_ev, geo_data, subject_handles, sna_orig=sna
        )
        anon_count = max(anon_count, ext_anon_count)
    else:
        export_interactants, export_friend_ev, export_geo = raw_interactants, raw_friend_ev, geo_data

    # Fase 1, 3, 4: location_history, media_gallery, friends_circle y subject_profile
    loc_history = storage.list_location_history(case_id, owner_type="subject")
    loc_by_media = {lh.get("media_id"): lh for lh in loc_history if lh.get("media_id")}
    post_url_by_id = {p.get("id"): (p.get("url") or "") for p in (raw_content.get("posts") or [])}
    media_gallery = []
    for m in raw_content.get("media_items") or []:
        lh = loc_by_media.get(m.get("id")) or {}
        loc_str = ", ".join(x for x in (lh.get("city"), lh.get("region"), lh.get("country")) if x)
        media_gallery.append({
            "media_id": m.get("id"),
            "filename": m.get("filename") or "",
            "source_url": m.get("source_url") or "",
            "local_path": m.get("local_path") or "",
            "published_at": m.get("published_at") or m.get("created_at") or "",
            "author": "subject",
            "possible_location": loc_str,
            "confidence": float(lh.get("confidence") or 0.0),
            "source": lh.get("source") or "",
            "evidence_quote": lh.get("evidence_quote") or "",
            "is_manual_override": bool(lh.get("is_manual_override")),
            "post_url": post_url_by_id.get(m.get("post_id"), ""),
        })

    # Círculo de amigos relacionados (respeta reveal_confirmed_friends opt-in)
    if reveal_confirmed_friends:
        storage.record_case_run(case_id, kind="audit:reveal_friends", target=case_id, status="done", summary={"revealed": True})

    friends_circle = []
    for raw_it, anon_it in zip(raw_interactants, export_interactants):
        if not (raw_it.get("is_friend_candidate") or raw_it.get("owner_type") == "friend"):
            continue
        is_confirmed = bool(raw_it.get("reciprocal") or raw_it.get("owner_type") == "friend")
        use_real = (not anonymize) or (reveal_confirmed_friends and is_confirmed)
        chosen = raw_it if use_real else anon_it
        disp = chosen.get("display_name") or chosen.get("handle") or "Tercero"
        hand = chosen.get("handle") or "Tercero"
        friends_circle.append({
            "handle": hand,
            "display_name": disp,
            "profile_url": (raw_it.get("profile_url") or "") if use_real else "",
            "platform": raw_it.get("platform") or "",
            "interactions_count": int(raw_it.get("interactions_count") or raw_it.get("comments_count") or 0),
            "first_seen": raw_it.get("first_seen") or "",
            "last_seen": raw_it.get("last_seen") or "",
            "visual_proximity_score": float(raw_it.get("visual_proximity_score") or 0.0),
            "epistemic_label": "explicit" if raw_it.get("reciprocal") else "ambiguous",
        })

    subj_prof = storage.list_subject_profile(case_id)
    if not subj_prof:
        from modules.subject_profile import compute_subject_profile
        subj_prof = compute_subject_profile(case_id, persist=False).get("entries") or []
    if anonymize:
        # Enmascarar referencias a terceros dentro de `friends` e `ideas`
        subj_prof = copy.deepcopy(subj_prof)
        anon_map = {}
        c_idx = 0
        for it in raw_interactants:
            h = (it.get("handle") or "").strip().lstrip("@").lower()
            if h and h not in subject_handles and h not in anon_map:
                c_idx += 1
                anon_map[h] = f"Tercero_{c_idx:02d}"
        for sp in subj_prof:
            if sp.get("category") in ("friends", "ideas"):
                val = sp.get("value") or ""
                for real_h, al in anon_map.items():
                    if len(real_h) >= 3:
                        val = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(real_h)}(?![A-Za-z0-9_])", al, val, flags=re.I)
                sp["value"] = val
            for rf in sp.get("evidence_refs") or []:
                if isinstance(rf, dict):
                    for key in ("id", "quote"):
                        rv = str(rf.get(key) or "")
                        if rv:
                            for real_h, al in anon_map.items():
                                if len(real_h) >= 3:
                                    rv = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(real_h)}(?![A-Za-z0-9_])", al, rv, flags=re.I)
                            rf[key] = rv

    clean_case = {k: v for k, v in detail["case"].items() if k not in ("nlp_json", "sna_json", "resolution_json")}

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "case": clean_case,
        "identifiers": detail.get("identifiers") or [],
        "profiles": [p for p in (detail.get("profiles") or []) if p.get("manual_status") != "rejected"],
        "resolution": detail.get("resolution") or {},
        "exposure": exposure,
        "vision": export_vision if include_gallery else {"stats": export_vision.get("stats") or {}, "theme_summary": [], "similar_pairs": []},
        "nlp": export_nlp,
        "sna": export_sna,
        "interactants": export_interactants,
        "friend_evidence": export_friend_ev,
        "geo_hints": export_geo,
        "location_history": loc_history,
        "media_gallery": media_gallery if include_gallery else [],
        "friends_circle": friends_circle,
        "subject_profile": subj_prof,
        "governance": {
            "anonymize_third_parties": bool(anonymize),
            "reveal_confirmed_friends": bool(reveal_confirmed_friends),
            "anonymized_count": anon_count,
            "include_quotes": bool(include_quotes),
            "include_gallery": bool(include_gallery),
        },
    }

    title_slug = (detail["case"].get("title") or case_id)[:30]
    out_dir = utils.make_output_dir(f"case_{title_slug}")

    html_path = out_dir / "resumen.html"
    html_path.write_text(_render_integral_html(payload), encoding="utf-8")

    reports.write_pdf(
        out_dir,
        f"Caso: {detail['case'].get('title') or case_id}",
        _build_pdf_sections(payload),
        gallery_items=payload.get("media_gallery") or [],
    )
    pdf_exists = (out_dir / "reporte_final.pdf").is_file()

    # Limpiar rutas locales internas antes de exportar caso_completo.json
    json_payload = copy.deepcopy(payload)
    for mg in json_payload.get("media_gallery") or []:
        mg.pop("local_path", None)
    json_path = out_dir / "caso_completo.json"
    json_path.write_text(json.dumps(json_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    storage.record_case_run(
        case_id=case_id,
        kind="report:integral",
        target=detail["case"].get("title") or case_id,
        status="done",
        folder=out_dir.name,
        summary={
            "exposure_score": exposure["score"],
            "exposure_level": exposure["level"],
            "anonymized_third_parties": anon_count,
        },
    )

    return {
        "case_id": case_id,
        "folder": out_dir.name,
        "html_url": f"/api/reports/{out_dir.name}/resumen.html",
        "pdf_url": f"/api/reports/{out_dir.name}/reporte_final.pdf" if pdf_exists else None,
        "json_url": f"/api/reports/{out_dir.name}/caso_completo.json",
        "exposure": exposure,
        "governance": payload["governance"],
    }


# ---------------------------------------------------------------------------
# 4. Gobernanza e Higiene de Almacenamiento (output/ y SQLite)
# ---------------------------------------------------------------------------
def _dir_size_bytes(path: Path) -> int:
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def storage_maintenance_summary() -> dict:
    """Informa uso de espacio de `output/` y `osint_cases.db`, identificando carpetas huérfanas."""
    referenced_folders = storage.get_all_referenced_folders()
    output_dirs = utils.list_reports()
    total_output_bytes = 0
    orphan_folders = []

    for d in output_dirs:
        sz = _dir_size_bytes(d)
        total_output_bytes += sz
        if d.name not in referenced_folders:
            orphan_folders.append({"folder": d.name, "size_bytes": sz})

    db_path = storage.get_db_path()
    db_size = db_path.stat().st_size if db_path.is_file() else 0

    browser_cache_mb = 0.0
    browser_profiles_mb = 0.0
    try:
        from modules.browser_agent import get_browser_cache_stats
        bstats = get_browser_cache_stats()
        browser_cache_mb = float(bstats.get("cache_size_mb", 0.0))
        browser_profiles_mb = float(bstats.get("total_size_mb", 0.0))
    except Exception:
        pass

    return {
        "output_folders_count": len(output_dirs),
        "referenced_folders_count": len(output_dirs) - len(orphan_folders),
        "orphan_folders_count": len(orphan_folders),
        "orphan_folders": orphan_folders[:50],
        "output_size_mb": round(total_output_bytes / (1024 * 1024), 2),
        "orphan_size_mb": round(sum(x["size_bytes"] for x in orphan_folders) / (1024 * 1024), 2),
        "db_size_mb": round(db_size / (1024 * 1024), 2),
        "browser_cache_mb": browser_cache_mb,
        "browser_profiles_mb": browser_profiles_mb,
    }


def cleanup_orphan_outputs(dry_run: bool = True) -> dict:
    """
    Elimina carpetas de `output/` que no están vinculadas a ningún caso en SQLite.
    Valida estrictamente que cada objetivo sea hijo directo de `OUTPUT_ROOT`.
    """
    summary = storage_maintenance_summary()
    base = utils.OUTPUT_ROOT.resolve()
    candidates = [x["folder"] for x in summary["orphan_folders"]]

    if dry_run:
        return {
            "dry_run": True,
            "would_delete_count": len(candidates),
            "would_free_mb": summary["orphan_size_mb"],
            "folders": candidates,
        }

    deleted = []
    freed_bytes = 0
    for item in summary["orphan_folders"]:
        target = (base / item["folder"]).resolve()
        if target.parent == base and target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            deleted.append(item["folder"])
            freed_bytes += item["size_bytes"]

    return {
        "dry_run": False,
        "deleted_count": len(deleted),
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "folders": deleted,
    }
