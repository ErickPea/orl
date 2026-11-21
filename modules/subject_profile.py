# -*- coding: utf-8 -*-
"""
Motor de Perfil Enriquecido del Sujeto (Fase 4) para OSINT-Auto.

Consolida en 7 categorías:
  - `hobbies`
  - `places`
  - `friends`
  - `ideas`
  - `personality`
  - `occupation`
  - `relationship_status`

Reglas duras:
  - Cada entrada incluye `confidence` (0..1), `evidence_refs`, `epistemic_status`
    (`explicit` | `ambiguous` | `model_inference`) y `human_review`.
  - `relationship_status` e `ideas` NUNCA se marcan `explicit` sin cita literal
    explícita que lo sustente; por defecto son `ambiguous` o `model_inference`.
  - Nunca mezcla contenido aislado de amigos (`owner_type='friend'`).
"""
import re
from typing import Optional

from modules import storage

_HOBBY_KEYWORDS = {
    "fotografía / arte": ["foto", "fotografia", "camara", "arte", "dibujo", "pintura", "diseño"],
    "deportes / fitness": ["gym", "correr", "running", "futbol", "ciclismo", "bici", "entrenar", "natacion"],
    "música / conciertos": ["musica", "concierto", "guitarra", "piano", "spotify", "rock", "salsa"],
    "viajes / naturaleza": ["viaje", "viajar", "montaña", "playa", "senderismo", "camping", "turismo"],
    "tecnología / programación": ["codigo", "python", "software", "ia", "linux", "programacion", "dev"],
    "gastronomía / café": ["cafe", "cocina", "restaurante", "receta", "comida", "postre"],
}

_OCCUPATION_RE = re.compile(
    r"\b(ingenier[oa]|desarrollador[a]?|programador[a]?|abogad[oa]|m[eé]dic[oa]|diseñador[a]?|"
    r"profesor[a]?|docente|periodista|arquitect[oa]|contador[a]?|estudiante|fot[oó]graf[oa]|"
    r"developer|engineer|designer|lawyer|teacher|student|analyst)\b",
    re.I,
)

_EXPLICIT_REL_STATUS_RE = re.compile(
    r"\b(estoy\s+(?:casad[oa]|solter[oa]|comprometid[oa])|mi\s+(?:espos[oa]|novi[oa]|prometid[oa]|pareja)|"
    r"feliz\s+aniversario\s+(?:mi\s+)?(?:espos[oa]|novi[oa]|amor))\b",
    re.I,
)


# Para qué sirve: construye el perfil enriquecido del sujeto en 7 categorías a partir de todas las evidencias del caso.
# Cómo funciona: agrega hobbies, lugares, amigos, ideas, personalidad, ocupación y pareja aplicando regla dura de citas literales y guarda en SQLite.
def compute_subject_profile(case_id: str, persist: bool = True) -> dict:
    """Calcula (y opcionalmente persiste) las 7 categorías del perfil enriquecido del sujeto."""
    detail = storage.get_case_detail(case_id)
    if not detail:
        return {"case_id": case_id, "entries": [], "by_category": {}}

    content = storage.get_case_content(case_id, include_friends=False) or {}
    posts = content.get("posts") or []
    profiles = [p for p in (detail.get("profiles") or []) if p.get("manual_status") != "rejected"]
    nlp = storage.get_case_nlp_analysis(case_id) or {}
    vision = storage.get_case_vision_summary(case_id) or {}
    sna = storage.compute_and_save_case_sna(case_id, record_run=False) or {}
    loc_hist = storage.list_location_history(case_id, owner_type="subject")
    rel_data = storage.list_relational_signals(case_id) or {}

    entries = []
    seen = set()

    # Para qué sirve: agrega una entrada deduplicada al perfil aplicando la regla de cautela epistemológica.
    # Cómo funciona: degrada `explicit` a `ambiguous` en `relationship_status`/`ideas` si falta cita literal y acota `conf` a `[0, 1]`.
    def _add(cat: str, val: str, conf: float, refs: list, epistemic: str):
        v = (val or "").strip()
        if not v:
            return
        key = (cat, v.lower())
        if key in seen:
            return
        seen.add(key)
        # Regla dura: `relationship_status` e `ideas` nunca son `explicit` sin cita literal
        has_quote = any(isinstance(r, dict) and (r.get("quote") or "").strip() for r in (refs or []))
        ep = epistemic
        if cat in ("relationship_status", "ideas") and ep == "explicit" and not has_quote:
            ep = "ambiguous"
        entries.append({
            "category": cat,
            "value": v,
            "confidence": round(max(0.0, min(1.0, float(conf))), 3),
            "evidence_refs": refs or [],
            "epistemic_status": ep,
            "human_review": "pending",
        })

    # 1) HOBBIES (desde intereses unificados + palabras clave en posts/bios)
    for it in (nlp.get("unified_interests") or [])[:8]:
        quotes = it.get("evidence_quotes") or []
        refs = [{"kind": "nlp_interest", "quote": q.get("quote") or ""} for q in quotes[:2]]
        conf = 0.82 if it.get("confidence") == "ALTA" else 0.60
        _add("hobbies", it.get("label") or "", conf, refs, "ambiguous" if not refs else "explicit")

    for vt in (vision.get("theme_summary") or [])[:6]:
        mids = vt.get("media_ids") or []
        evs = vt.get("evidences") or []
        _add(
            "hobbies",
            vt.get("label") or "",
            0.74 if vt.get("confidence") == "ALTA" else 0.56,
            [{"kind": "vision_theme", "media_id": mids[0] if mids else None, "quote": evs[0] if evs else f"Tema visual ({vt.get('count', 1)} fotos)"}],
            "ambiguous",
        )

    for p in posts:
        txt = (p.get("content") or "").lower()
        for h_label, kws in _HOBBY_KEYWORDS.items():
            hit = next((kw for kw in kws if kw in txt), None)
            if hit:
                _add(
                    "hobbies",
                    h_label,
                    0.65,
                    [{"kind": "post", "id": p.get("id"), "quote": (p.get("content") or "")[:140]}],
                    "ambiguous",
                )

    # 2) PLACES (desde location_history + perfil + NER)
    for lh in loc_hist:
        loc_label = ", ".join(x for x in (lh.get("city"), lh.get("region"), lh.get("country")) if x)
        if loc_label:
            is_exp = bool(lh.get("is_manual_override") or lh.get("source") == "exif")
            _add(
                "places",
                loc_label,
                float(lh.get("confidence") or 0.6),
                [{"kind": "location_history", "id": lh.get("id"), "media_id": lh.get("media_id"), "quote": lh.get("evidence_quote") or loc_label}],
                "explicit" if is_exp else "ambiguous",
            )
    for pr in profiles:
        if pr.get("location"):
            _add(
                "places",
                pr["location"],
                0.72,
                [{"kind": "profile", "id": pr.get("id"), "quote": f"Perfil {pr.get('site')}: {pr['location']}"}],
                "explicit",
            )
    for pl in ((nlp.get("entities") or {}).get("places") or [])[:6]:
        pname = pl.get("text") or pl.get("name") or pl.get("label") or ""
        if pname:
            _add(
                "places",
                pname,
                0.55,
                [{"kind": "ner_place", "quote": f"Mención NER en texto ({pl.get('count', 1)}x): {pname}"}],
                "ambiguous",
            )

    # 3) FRIENDS (desde SNA close_circle)
    for u in (sna.get("close_circle") or [])[:10]:
        score = float(u.get("closeness_score") or 0)
        vps = float(u.get("visual_proximity_score") or 0)
        _add(
            "friends",
            f"@{u.get('label') or u.get('id')} ({u.get('tier') or 'CERCANO'})",
            min(0.95, max(0.35, (score + vps * 0.4) / 100.0)),
            [{"kind": "sna_node", "id": u.get("id"), "quote": f"Cercanía {score}/100 · Proximidad visual {vps}/100"}],
            "explicit" if u.get("is_reciprocal") else "ambiguous",
        )

    # 4) IDEAS (temas/causas recurrentes — nunca `explicit` sin cita literal)
    for kw in ((nlp.get("entities") or {}).get("hashtags") or [])[:6]:
        tag = kw.get("tag") or kw.get("name") or ""
        if tag:
            _add(
                "ideas",
                f"Tema recurrente #{tag.lstrip('#')}",
                min(0.75, 0.45 + int(kw.get("count") or 1) * 0.08),
                [{"kind": "hashtag", "quote": f"Hashtag #{tag.lstrip('#')} ({kw.get('count', 1)}x)"}],
                "model_inference",
            )

    # 5) PERSONALITY (tono dominante y patrones de actividad)
    subj_sent = (nlp.get("sentiment") or {}).get("subject") or {}
    if subj_sent.get("total", 0) > 0:
        dom = subj_sent.get("dominant") or "NEUTRO"
        _add(
            "personality",
            f"Tono expresivo predominante: {dom}",
            0.62,
            [{"kind": "nlp_sentiment", "quote": f"{subj_sent.get('positive_pct', 0)}% pos / {subj_sent.get('critical_pct', 0)}% crít"}],
            "model_inference",
        )
    timeline = nlp.get("timeline") or {}
    if timeline.get("peak_hour") is not None:
        _add(
            "personality",
            f"Actividad frecuente alrededor de las {timeline['peak_hour']:02d}:00",
            0.58,
            [{"kind": "timeline", "quote": f"Horario pico observado: {timeline['peak_hour']:02d}:00"}],
            "ambiguous",
        )

    # 6) OCCUPATION (desde bio/posts/NER organizaciones)
    for pr in profiles:
        bio = pr.get("bio") or ""
        m = _OCCUPATION_RE.search(bio)
        if m:
            _add(
                "occupation",
                m.group(1).capitalize(),
                0.78,
                [{"kind": "profile", "id": pr.get("id"), "quote": bio[:180]}],
                "explicit",
            )
    for p in posts:
        txt = p.get("content") or ""
        m = _OCCUPATION_RE.search(txt)
        if m:
            _add(
                "occupation",
                m.group(1).capitalize(),
                0.62,
                [{"kind": "post", "id": p.get("id"), "quote": txt[:160]}],
                "ambiguous",
            )
    for org in ((nlp.get("entities") or {}).get("organizations") or [])[:4]:
        oname = org.get("text") or org.get("name") or ""
        if oname:
            _add(
                "occupation",
                f"Vinculación mencionada con {oname}",
                0.55,
                [{"kind": "ner_org", "quote": f"Mención de organización: {oname}"}],
                "ambiguous",
            )

    # 7) RELATIONSHIP_STATUS (regla dura: solo `explicit` si hay cita literal explícita)
    for p in posts:
        txt = p.get("content") or ""
        m = _EXPLICIT_REL_STATUS_RE.search(txt)
        if m:
            _add(
                "relationship_status",
                f"Indicio de vínculo/estado: '{m.group(0)}'",
                0.76,
                [{"kind": "post", "id": p.get("id"), "quote": txt[:180]}],
                "explicit",
            )
    for sig in (rel_data.get("signals") or []):
        if sig.get("category") == "pareja" and sig.get("review_status") != "dismissed":
            ep = "explicit" if (sig.get("epistemic_status") == "explicit" or sig.get("epistemic_type") == "explicit") and sig.get("context_excerpt") else "ambiguous"
            _add(
                "relationship_status",
                f"Señal de pareja ({sig.get('subcategory') or 'pareja'})",
                0.78 if ep == "explicit" else 0.52,
                [{"kind": "relational_signal", "id": sig.get("id"), "quote": sig.get("context_excerpt") or ""}],
                ep,
            )

    saved = storage.replace_subject_profile_entries(case_id, entries) if persist else entries
    by_cat = {}
    for e in saved:
        by_cat.setdefault(e["category"], []).append(e)
    return {"case_id": case_id, "entries": saved, "by_category": by_cat}
