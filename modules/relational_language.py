# -*- coding: utf-8 -*-
"""
Motor de Análisis del Lenguaje Relacional Contextual para OSINT-Auto.

Analiza comentarios, respuestas y biografías públicas para detectar expresiones que
puedan sugerir vínculos sociales (familia, amistad, pareja, laboral/académico),
distinguiendo estrictamente entre:
  - `explicit`        : expresión directa, dirigida y sin marcadores de ambigüedad/sarcasmo.
  - `ambiguous`       : coloquialismo regional, diminutivo/emoji aislado, 3.ª persona o humor/sarcasmo.
  - `model_inference` : interpretación contextual opcional de un LLM (Gemini / Ollama / OpenAI).

Regla fundamental: una palabra aislada NUNCA se presenta como prueba definitiva de relación.
Siempre se conserva la expresión original, la ventana de contexto, los flags de ambigüedad
y la trazabilidad hacia el comentario/publicación de origen para revisión humana.
"""
import json
import os
import re
import unicodedata
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# 1. Normalización de errores ortográficos, abreviaturas y alargamientos
# ---------------------------------------------------------------------------
_TYPO_MAP = {
    "hno": "hermano", "hrmno": "hermano", "ermano": "hermano", "hemano": "hermano", "herman": "hermano",
    "hna": "hermana", "hrmna": "hermana", "ermana": "hermana", "hemana": "hermana",
    "amg": "amigo", "amix": "amiga", "amigx": "amigo",
    "nvio": "novio", "nvia": "novia",
    "espos": "esposo",
    "tqm": "te quiero mucho", "tq": "te quiero", "tk": "te quiero", "tkm": "te quiero mucho",
    "bb": "bebe", "bby": "bebe",
    "prce": "parce", "prcero": "parcero",
    "comp": "compa", "broder": "brother",
    "abue": "abuelo", "abuel": "abuelo",
}

# Términos con alto uso coloquial/polisémico (por sí solos son `ambiguous` salvo refuerzo claro)
_COLLOQUIAL_POLYSEMIC = {
    "bro", "brother", "sis", "hermano", "hermana", "tio", "tia", "primo", "prima",
    "mami", "papi", "viejo", "vieja", "amor", "carino", "cielo", "corazon", "bebe",
    "parcero", "parce", "pana", "cuate", "carnal", "pata", "llave", "compa", "manito",
    "jefe", "maestro", "profe", "reina", "rey", "vida", "nena", "nene",
}

# Diccionario relacional multi-regional: (categoria, subcategoria, lista_de_raices_normalizadas, requiere_contexto_fuerte)
_RELATIONAL_LEXICON = [
    # --- FAMILIA ---
    ("familia", "madre", ["madre", "mama", "mamita", "mamacita", "ama", "mami", "jefecita", "madrecita"], False),
    ("familia", "padre", ["padre", "papa", "papito", "apa", "papi", "cucho", "papacito", "padrecito"], False),
    ("familia", "hermano_a", ["hermano", "hermana", "hermanito", "hermanita", "hermanote", "nana", "nano", "bro", "brother", "sis"], False),
    ("familia", "hijo_a", ["hijo", "hija", "hijito", "hijita", "mijo", "mija", "primogenito"], False),
    ("familia", "tio_a", ["tio", "tia", "tiito", "tiita"], True),
    ("familia", "sobrino_a", ["sobrino", "sobrina", "sobrinito", "sobrinita"], False),
    ("familia", "primo_a", ["primo", "prima", "primito", "primita"], True),
    ("familia", "abuelo_a", ["abuelo", "abuela", "abuelito", "abuelita", "nono", "nona", "tata"], False),
    ("familia", "nieto_a", ["nieto", "nieta", "nietecito", "nietecita"], False),
    ("familia", "familia_politica", ["cunado", "cunada", "suegro", "suegra", "yerno", "nuera", "padrino", "madrina", "ahijado", "ahijada"], False),

    # --- AMISTAD ---
    ("amistad", "amigo_a", ["amigo", "amiga", "amiguito", "amiguita", "amiguis", "bff", "mejor amigo", "mejor amiga", "bestie"], False),
    ("amistad", "regional_amistad", ["parcero", "parcera", "parce", "pana", "compa", "companero", "companera", "cuate", "carnal", "pata", "llave", "manito", "colega"], True),

    # --- PAREJA Y AFECTIVO ---
    ("pareja", "noviazgo", ["novio", "novia", "noviecito", "noviecita", "pareja", "pololo", "polola", "prometido", "prometida"], False),
    ("pareja", "matrimonio", ["esposo", "esposa", "marido", "esposita", "esposito"], False),
    ("pareja", "expresion_afectiva", ["mi amor", "amorcito", "mi vida", "mi cielo", "carino", "corazon", "bebe", "te amo", "te quiero mucho"], True),

    # --- LABORAL / ACADÉMICO ---
    ("laboral_academico", "entorno_trabajo", ["companero de trabajo", "jefe", "jefa", "socio", "socia", "colega de oficina", "profe", "profesor", "profesora", "mentor"], True),
]

# Emojis con carga relacional/afectiva (siempre `ambiguous` si aparecen solos sin texto explícito)
_RELATIONAL_EMOJIS = [
    ("pareja", "emoji_compromiso", "💍"),
    ("pareja", "emoji_pareja", "💑"),
    ("familia", "emoji_familia", "👨‍👩‍👧"),
    ("familia", "emoji_familia", "👨‍👩‍👦"),
    ("familia", "emoji_familia", "👨‍👩‍👧‍👦"),
    ("afectivo_general", "emoji_afecto", "❤️"),
    ("afectivo_general", "emoji_afecto", "🥰"),
    ("afectivo_general", "emoji_afecto", "😘"),
    ("afectivo_general", "emoji_afecto", "😍"),
]

# Patrones de refuerzo explícito (posesivo + parentesco/pareja o contexto inequívoco)
_EXPLICIT_REINFORCEMENT_RE = re.compile(
    r"\b(mi\s+(?:madre|mama|padre|papa|hermano|hermana|hermanito|hermanita|hijo|hija|hijito|hijita|"
    r"esposo|esposa|marido|novio|novia|pareja|prometido|prometida|sobrino|sobrina|abuelo|abuela|"
    r"abuelito|abuelita|nieto|nieta|cunado|cunada|suegro|suegra|mejor\s+amigo|mejor\s+amiga)|"
    r"feliz\s+(?:dia\s+de\s+la\s+madre|dia\s+del\s+padre|aniversario)|"
    r"te\s+amo\s+(?:mama|papa|esposa|esposo|novia|novio|hijo|hija|hermano|hermana)|"
    r"orgullos[oa]\s+de\s+mi\s+(?:hijo|hija|hermano|hermana|esposo|esposa|madre|padre))\b",
    re.I,
)

# Patrones de tercera persona no dirigida (evita atribuir al interlocutor una relación de un tercero)
_THIRD_PERSON_RE = re.compile(
    r"\b(?:el|la|un|una|su|del|de\s+un|de\s+una)\s+(?:hermano|hermana|primo|prima|tio|tia|novio|novia|"
    r"esposo|esposa|amigo|amiga|madre|mama|padre|papa|hijo|hija)\s+(?:de|del)\b",
    re.I,
)

# Indicadores de humor, broma, ironía o sarcasmo
_SARCASM_HUMOR_RE = re.compile(
    r"(?:jaja+|jeje+|jiji+|haha+|lol\b|lmao\b|\bxd\b|/s\b|😂|🤣|😜|🙃|"
    r"\b(?:broma|chiste|sarcasmo|ironia|jodiendo|mamando\s+gallo|mentira|como\s+si\s+fuera\s+mi)\b|"
    r"\?\?\?)",
    re.I,
)

# Cita literal o letra de canción
_QUOTE_RE = re.compile(r'["“«][^"”»]{6,}["”»]')


def _strip_accents(text: str) -> str:
    nfd = unicodedata.normalize("NFD", str(text or ""))
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


def _collapse_elongations(word: str) -> str:
    """Reduce repeticiones expresivas de letras ('amigoooo' -> 'amigo', 'hermanitooo' -> 'hermanito')."""
    # Conserva 'll', 'rr', 'cc', 'ee' en palabras válidas reduciendo solo 3+ repeticiones (y 2+ al final)
    w = re.sub(r"([a-z])\1{2,}", r"\1", word)
    w = re.sub(r"([aeiou])\1+$", r"\1", w)
    return w


def normalize_for_relational(text: str) -> str:
    """Normaliza tildes, alargamientos y abreviaturas/typos preservando estructura de palabras."""
    clean = _strip_accents(text).lower()
    tokens = re.findall(r"[a-z0-9]+|[^\s\w]", clean)
    norm_tokens = []
    for t in tokens:
        if t.isalpha():
            t2 = _collapse_elongations(t)
            t3 = _TYPO_MAP.get(t2, t2)
            norm_tokens.append(t3)
        else:
            norm_tokens.append(t)
    return " ".join(norm_tokens)


def _strip_diminutive(word: str) -> str:
    """Reduce diminutivos regulares al lema base para emparejar variantes no listadas."""
    for suf, repl in (("cito", ""), ("cita", ""), ("illo", "o"), ("illa", "a"), ("ito", "o"), ("ita", "a"), ("ico", "o"), ("ica", "a")):
        if len(word) >= 6 and word.endswith(suf):
            return word[: -len(suf)] + repl
    return word


def analyze_single_text_relational(
    text: str,
    source_handle: str = "",
    target_handle: str = "",
    platform: str = "open",
    comment_id: Optional[int] = None,
    post_id: Optional[int] = None,
    has_reciprocal_history: bool = False,
) -> list:
    """
    Analiza un único comentario o publicación y devuelve una lista de señales relacionales
    estructuradas con distinción estricta `explicit` vs. `ambiguous`.
    """
    raw = (text or "").strip()
    if not raw:
        return []

    norm_text = normalize_for_relational(raw)
    # También generamos una versión con diminutivos reducidos para capturar formas creativas
    dim_tokens = [_strip_diminutive(w) if w.isalpha() else w for w in norm_text.split()]
    norm_dim = " ".join(dim_tokens)

    has_sarcasm = bool(_SARCASM_HUMOR_RE.search(raw))
    has_third_person = bool(_THIRD_PERSON_RE.search(norm_text))
    has_quote = bool(_QUOTE_RE.search(raw))
    has_explicit_reinf = bool(_EXPLICIT_REINFORCEMENT_RE.search(norm_text))

    signals = []
    seen_cats = set()

    # 1) Búsqueda léxica multi-regional
    for category, subcategory, roots, needs_strong_ctx in _RELATIONAL_LEXICON:
        matched_term = None
        for root in roots:
            pat = rf"(?<![a-z0-9]){re.escape(root)}(?![a-z0-9])"
            if re.search(pat, norm_text) or re.search(pat, norm_dim):
                matched_term = root
                break
        if not matched_term:
            continue

        key = (category, subcategory)
        if key in seen_cats:
            continue
        seen_cats.add(key)

        flags = []
        if has_sarcasm:
            flags.append("posible_sarcasmo_o_humor")
        if has_third_person:
            flags.append("tercera_persona_no_dirigida")
        if has_quote:
            flags.append("posible_cita_o_frase_ajena")
        if matched_term in _COLLOQUIAL_POLYSEMIC or needs_strong_ctx:
            flags.append("termino_coloquial_polisemico")
        if not target_handle:
            flags.append("sin_interlocutor_directo")

        # Decisión epistemológica conservadora (cero falsos positivos como `explicit`)
        is_explicit = (
            not has_sarcasm
            and not has_third_person
            and not has_quote
            and bool(target_handle)
            and (
                has_explicit_reinf
                or (matched_term not in _COLLOQUIAL_POLYSEMIC and not needs_strong_ctx)
            )
        )

        epistemic_type = "explicit" if is_explicit else "ambiguous"
        if epistemic_type == "explicit":
            confidence = "ALTA" if (has_explicit_reinf or has_reciprocal_history) else "MEDIA"
        else:
            confidence = "MEDIA" if (has_reciprocal_history and not has_sarcasm and not has_third_person) else "BAJA"

        signals.append({
            "comment_id": comment_id,
            "post_id": post_id,
            "platform": platform,
            "source_handle": (source_handle or "").strip().lstrip("@").lower(),
            "target_handle": (target_handle or "").strip().lstrip("@").lower(),
            "category": category,
            "subcategory": subcategory,
            "matched_expression": matched_term,
            "context_excerpt": raw[:320],
            "epistemic_type": epistemic_type,
            "ambiguity_flags": flags,
            "confidence": confidence,
            "engine": "local_rules",
        })

    # 2) Emojis relacionales (solo si no hubo ya coincidencia en esa misma categoría; siempre `ambiguous`)
    for category, subcategory, emoji in _RELATIONAL_EMOJIS:
        if emoji in raw and not any(s["category"] == category for s in signals):
            flags = ["emoji_sin_texto_explicito"]
            if has_sarcasm:
                flags.append("posible_sarcasmo_o_humor")
            signals.append({
                "comment_id": comment_id,
                "post_id": post_id,
                "platform": platform,
                "source_handle": (source_handle or "").strip().lstrip("@").lower(),
                "target_handle": (target_handle or "").strip().lstrip("@").lower(),
                "category": category,
                "subcategory": subcategory,
                "matched_expression": emoji,
                "context_excerpt": raw[:320],
                "epistemic_type": "ambiguous",
                "ambiguity_flags": flags,
                "confidence": "BAJA",
                "engine": "local_rules",
            })

    return signals


# ---------------------------------------------------------------------------
# 2. Verificación contextual opcional con LLM (clasificada como `model_inference`)
# ---------------------------------------------------------------------------
def _extract_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except Exception:
        m = re.search(r"\{.*\}", cleaned, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None
    return None


def _call_relational_llm(prompt: str, provider: str) -> tuple:
    """Devuelve `(parsed_json, engine_name)` o `(None, 'local_rules')`."""
    prov = (provider or "local").strip().lower()
    if prov == "local":
        return None, "local_rules"
    try:
        if prov in ("auto", "gemini") and os.environ.get("GEMINI_API_KEY", "").strip():
            api_key = os.environ["GEMINI_API_KEY"].strip()
            model = os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
            payload = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
            }
            endpoints = (
                [
                    f"https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent",
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                ]
                if api_key.startswith("AQ.")
                else [
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    f"https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent",
                ]
            )
            for url in endpoints:
                r = requests.post(url, params={"key": api_key}, json=payload, timeout=30)
                if r.status_code == 200:
                    cands = (r.json() or {}).get("candidates") or []
                    parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
                    txt = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
                    parsed = _extract_json(txt)
                    if parsed:
                        return parsed, "gemini"

        if prov in ("auto", "ollama"):
            base_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
            model = os.environ.get("OLLAMA_TEXT_MODEL", "").strip()
            if not model:
                r_tags = requests.get(f"{base_url}/api/tags", timeout=1.5)
                if r_tags.status_code == 200:
                    models = [m.get("name", "") for m in (r_tags.json() or {}).get("models", []) if m.get("name")]
                    model = models[0] if models else ""
            if model:
                r = requests.post(
                    f"{base_url}/api/generate",
                    json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
                    timeout=40,
                )
                if r.status_code == 200:
                    parsed = _extract_json((r.json() or {}).get("response") or "")
                    if parsed:
                        return parsed, f"ollama:{model}"

        if prov in ("auto", "openai") and os.environ.get("OPENAI_API_KEY", "").strip():
            model = os.environ.get("OPENAI_TEXT_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY'].strip()}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            if r.status_code == 200:
                choices = (r.json() or {}).get("choices") or []
                txt = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
                parsed = _extract_json(txt)
                if parsed:
                    return parsed, "openai"
    except Exception:
        return None, "local_rules"
    return None, "local_rules"


def _refine_ambiguous_signals_with_llm(signals: list, provider: str) -> list:
    """
    Evalúa en lote hasta 10 señales `ambiguous` usando un LLM para distinguir uso coloquial/sarcasmo
    de un posible vínculo real, marcando el resultado estrictamente como `model_inference`.
    """
    candidates = [(idx, s) for idx, s in enumerate(signals) if s["epistemic_type"] == "ambiguous"][:10]
    if not candidates or (provider or "local").lower() == "local":
        return signals

    items_text = "\n".join(
        f'{i}: autor=@{s["source_handle"]}, destino=@{s["target_handle"]}, expresion="{s["matched_expression"]}", texto="{s["context_excerpt"][:200]}"'
        for i, (_, s) in enumerate(candidates)
    )
    prompt = (
        "Eres un analista lingüístico riguroso. Evalúa cada comentario en español/inglés para determinar si "
        "la expresión relacional parece un vínculo plausible o es mero uso coloquial/broma/sarcasmo.\n"
        "Devuelve SOLO un JSON válido con la forma:\n"
        '{"items": [{"index": 0, "verdict": "likely_relation" | "colloquial_or_humor" | "uncertain", '
        '"category": "familia" | "amistad" | "pareja" | "laboral_academico" | "afectivo_general", '
        '"confidence": "MEDIA" | "BAJA", "reason": "explicación breve en español"}]}\n\n'
        f"Casos a revisar:\n{items_text}"
    )
    parsed, engine_name = _call_relational_llm(prompt, provider)
    if not parsed or not isinstance(parsed.get("items"), list):
        return signals

    for item in parsed["items"]:
        if not isinstance(item, dict):
            continue
        pos = item.get("index")
        if not isinstance(pos, int) or pos < 0 or pos >= len(candidates):
            continue
        orig_idx, sig = candidates[pos]
        verdict = str(item.get("verdict") or "uncertain").lower()
        reason = str(item.get("reason") or "").strip()[:140]
        if verdict == "likely_relation":
            sig["epistemic_type"] = "model_inference"
            sig["engine"] = engine_name
            sig["confidence"] = "MEDIA" if str(item.get("confidence", "")).upper() == "MEDIA" else "BAJA"
            if item.get("category") in ("familia", "amistad", "pareja", "laboral_academico", "afectivo_general"):
                sig["category"] = item["category"]
            if reason:
                sig["ambiguity_flags"] = [*sig["ambiguity_flags"], f"ia:{reason}"]
        elif verdict == "colloquial_or_humor":
            sig["epistemic_type"] = "ambiguous"
            sig["engine"] = engine_name
            sig["confidence"] = "BAJA"
            if reason:
                sig["ambiguity_flags"] = [*sig["ambiguity_flags"], f"ia_coloquial:{reason}"]
        signals[orig_idx] = sig

    return signals


# ---------------------------------------------------------------------------
# 3. Pipeline completo sobre un caso
# ---------------------------------------------------------------------------
def analyze_case_relational_language(
    comments: list,
    posts: list,
    reciprocal_pairs: Optional[set] = None,
    provider: str = "local",
) -> list:
    """
    Analiza todos los comentarios y publicaciones de un caso y devuelve la lista
    de señales relacionales normalizadas listas para guardar en `relational_signals`.
    """
    recip = reciprocal_pairs or set()
    all_signals = []

    for c in comments or []:
        src = (c.get("author_handle") or "").strip().lstrip("@").lower()
        tgt = (c.get("target_handle") or "").strip().lstrip("@").lower()
        has_recip = (min(src, tgt), max(src, tgt)) in recip if (src and tgt) else False
        meta = c.get("metadata") or {}
        plat = str(meta.get("platform") or c.get("platform") or "open").lower()
        found = analyze_single_text_relational(
            text=c.get("content") or "",
            source_handle=src,
            target_handle=tgt,
            platform=plat,
            comment_id=c.get("id"),
            post_id=c.get("post_id"),
            has_reciprocal_history=has_recip,
        )
        all_signals.extend(found)

    for p in posts or []:
        src = (p.get("author") or "").strip().lstrip("@").lower()
        mentions = (p.get("mentions") or (p.get("metadata") or {}).get("mentions") or [])
        tgt = mentions[0] if len(mentions) == 1 else ""
        has_recip = (min(src, tgt), max(src, tgt)) in recip if (src and tgt) else False
        found = analyze_single_text_relational(
            text=p.get("content") or "",
            source_handle=src,
            target_handle=tgt,
            platform=str(p.get("platform") or "open").lower(),
            comment_id=None,
            post_id=p.get("id"),
            has_reciprocal_history=has_recip,
        )
        all_signals.extend(found)

    if provider and provider.lower() != "local":
        all_signals = _refine_ambiguous_signals_with_llm(all_signals, provider)

    return all_signals
