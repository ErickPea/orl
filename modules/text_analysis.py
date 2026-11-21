# -*- coding: utf-8 -*-
"""
Motor de Análisis de Texto, Intereses, Entidades y Sentimiento (NLP) para OSINT-Auto (Fase 4).

Principios de diseño:
  - Separa estrictamente el contenido del SUJETO (bios, publicaciones, comentarios propios)
    de los comentarios de TERCEROS (audiencia / círculo).
  - Muestra evidencia literal (citas directas) y nivel de confianza para cada tema/interés,
    evitando presentar inferencias automáticas como certezas absolutas.
  - Combina señales de texto con los temas visuales detectados en Fase 3 (fusión multimodal).
  - Funciona 100% offline/local y admite síntesis opcional con LLM (Ollama, Gemini, OpenAI).
"""
import json
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import requests

from modules.vision_analysis import VISUAL_THEME_TAXONOMY

# Categorías adicionales orientadas a texto/opinión/biografías
EXTRA_TEXT_THEMES = {
    "Economía y Finanzas": [
        "economia", "finanzas", "inversion", "bolsa", "cripto", "bitcoin", "trading",
        "emprendimiento", "startup", "negocios", "business", "mercado", "ahorro", "dinero",
        "impuestos", "inflacion", "fintech",
    ],
    "Política y Sociedad": [
        "politica", "gobierno", "democracia", "elecciones", "derechos", "justicia",
        "constitucion", "congreso", "senado", "debate", "sociedad", "ciudadania", "publico",
        "reforma", "leyes", "activismo", "policy",
    ],
    "Salud y Bienestar": [
        "salud", "bienestar", "meditacion", "yoga", "nutricion", "psicologia",
        "terapia", "sueno", "mente", "habitos", "wellness", "mindfulness", "medico",
    ],
    "Medio Ambiente y Sostenibilidad": [
        "clima", "ecologia", "sostenibilidad", "reciclaje", "energia", "solar",
        "biodiversidad", "medioambiente", "planeta", "conservacion", "climate", "green",
    ],
    "Ciberseguridad y Privacidad": [
        "ciberseguridad", "cybersecurity", "seguridad", "privacidad", "privacy", "osint",
        "pentesting", "cifrado", "vulnerabilidad", "malware", "infosec", "hacking",
    ],
}

FULL_THEME_TAXONOMY = {**VISUAL_THEME_TAXONOMY, **EXTRA_TEXT_THEMES}

# Léxicos de sentimiento bilingües (ES/EN)
_POS_WORDS = {
    "excelente", "genial", "increible", "maravilloso", "feliz", "alegre", "encanta",
    "amo", "adoro", "bueno", "buena", "buenos", "buenas", "mejor", "mejores", "gracias",
    "agradecido", "agradecida", "orgulloso", "orgullosa", "exito", "exitoso", "brillante",
    "espectacular", "recomiendo", "recomendado", "fantastico", "perfecto", "apoyo",
    "aplaudo", "positiva", "positivo", "inspirador", "disfrutando", "divertido", "logro",
    "gran", "buen", "aprobado", "util", "bonito", "hermoso", "impresionante",
    "great", "awesome", "amazing", "love", "loved", "happy", "thanks", "thank",
    "excellent", "wonderful", "best", "good", "nice", "helpful", "proud", "fantastic",
    "enjoy", "enjoying", "brilliant", "perfect", "support", "cool", "win",
}

_NEG_WORDS = {
    "malo", "mala", "malos", "malas", "pesimo", "terrible", "horrible", "odio",
    "detesto", "triste", "enojado", "molesto", "frustrado", "frustrante", "decepcion",
    "decepcionado", "basura", "estafa", "fraude", "error", "fallo", "falla", "peor",
    "problema", "problemas", "inaceptable", "verguenza", "ridiculo", "mentira", "corrupcion",
    "injusto", "injusticia", "aburrido", "desastre", "lamentable", "queja", "critica",
    "peligro", "peligroso", "toxico", "malisimo", "pesima", "preocupante", "grave",
    "bad", "terrible", "horrible", "hate", "sad", "angry", "worst", "broken", "fail",
    "failed", "failure", "scam", "fraud", "disappointed", "awful", "trash", "boring",
    "wrong", "issue", "problem", "useless", "toxic", "annoying", "unacceptable",
}

_NEGATORS = {
    "no", "nunca", "jamas", "sin", "tampoco", "nada", "ni",
    "not", "never", "dont", "doesn", "doesnt", "didnt", "cant", "cannot", "without", "none",
}

_STOPWORDS = {
    "que", "con", "por", "para", "como", "pero", "mas", "este", "esta", "esto", "estos",
    "estas", "ese", "esa", "eso", "esos", "esas", "del", "las", "los", "una", "uno", "unos",
    "unas", "sobre", "entre", "desde", "hasta", "todo", "toda", "todos", "todas", "tambien",
    "solo", "muy", "hay", "ser", "fue", "son", "esta", "estan", "tiene", "tienen", "hacer",
    "hace", "puede", "pueden", "aqui", "alli", "donde", "cuando", "quien", "cual", "porque",
    "sino", "aunque", "mientras", "despues", "antes", "ahora", "siempre", "cada", "otro",
    "otra", "otros", "otras", "mismo", "misma", "bien", "mal", "asi", "tan", "tanto",
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "had", "are", "was",
    "were", "will", "would", "could", "should", "about", "into", "more", "some", "what",
    "when", "where", "which", "their", "they", "them", "your", "you", "our", "out", "just",
    "like", "been", "being", "than", "then", "also", "very", "here", "there", "http", "https",
    "www", "com", "net", "org", "repositorio",
}

_KNOWN_PLACES = {
    "bogota": "Bogotá", "medellin": "Medellín", "cali": "Cali", "barranquilla": "Barranquilla",
    "cartagena": "Cartagena", "bucaramanga": "Bucaramanga", "pereira": "Pereira",
    "colombia": "Colombia", "mexico": "México", "cdmx": "Ciudad de México", "guadalajara": "Guadalajara",
    "monterrey": "Monterrey", "espana": "España", "madrid": "Madrid", "barcelona": "Barcelona",
    "valencia": "Valencia", "sevilla": "Sevilla", "argentina": "Argentina", "buenos aires": "Buenos Aires",
    "chile": "Chile", "santiago": "Santiago", "peru": "Perú", "lima": "Lima", "ecuador": "Ecuador",
    "quito": "Quito", "guayaquil": "Guayaquil", "venezuela": "Venezuela", "caracas": "Caracas",
    "uruguay": "Uruguay", "montevideo": "Montevideo", "panama": "Panamá", "costa rica": "Costa Rica",
    "brasil": "Brasil", "estados unidos": "Estados Unidos", "miami": "Miami", "nueva york": "Nueva York",
    "new york": "New York", "london": "Londres", "londres": "Londres", "paris": "París",
    "berlin": "Berlín", "canada": "Canadá", "toronto": "Toronto", "latam": "Latinoamérica",
}

_ORG_PATTERN = re.compile(
    r"\b((?:Universidad|Instituto|Fundaci[oó]n|Corporaci[oó]n|Asociaci[oó]n|Ministerio|Colegio|Academia|Laboratorio|Grupo|Centro)\s+[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ0-9&.\-]*(?:\s+(?:de|del|la|las|los|y|en)?\s*[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ0-9&.\-]*){0,4})"
)
_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)
_HASHTAG_RE = re.compile(r"(?<![A-Za-z0-9_])#([A-Za-z0-9_\u00C0-\u024F]{2,50})")
_MENTION_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z0-9_.-]{2,60})")
_WEEKDAYS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _norm(text: str) -> str:
    if not text:
        return ""
    nfd = unicodedata.normalize("NFD", str(text))
    clean = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    return " ".join(clean.lower().split())


def _tokens(text: str) -> list:
    return re.findall(r"[a-z0-9]+", _norm(text))


# ---------------------------------------------------------------------------
# Estado de motores NLP
# ---------------------------------------------------------------------------
def _check_ollama_text(timeout: float = 1.5) -> dict:
    base_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    preferred = os.environ.get("OLLAMA_TEXT_MODEL", "").strip()
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=timeout)
        if r.status_code != 200:
            return {"available": False, "url": base_url, "models": [], "selected_model": preferred}
        models = [m.get("name", "") for m in (r.json() or {}).get("models", []) if m.get("name")]
        selected = preferred or (models[0] if models else "")
        return {
            "available": bool(selected),
            "server_up": True,
            "url": base_url,
            "models": models[:10],
            "selected_model": selected,
        }
    except Exception:
        return {"available": False, "server_up": False, "url": base_url, "models": [], "selected_model": preferred}


def nlp_capabilities(probe_ollama: bool = True) -> dict:
    """Informa qué motores NLP/LLM están disponibles."""
    oll = _check_ollama_text() if probe_ollama else {"available": False, "models": [], "selected_model": ""}
    gemini_ok = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    openai_ok = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    default_engine = "local"
    if oll.get("available"):
        default_engine = f"ollama:{oll['selected_model']}"
    elif gemini_ok:
        default_engine = "gemini"
    elif openai_ok:
        default_engine = "openai"

    return {
        "local_lexicon": True,
        "ollama": oll,
        "gemini": gemini_ok,
        "openai": openai_ok,
        "default_engine": default_engine,
    }


# ---------------------------------------------------------------------------
# Análisis de Sentimiento con ventana de negación
# ---------------------------------------------------------------------------
def analyze_sentiment(text: str) -> dict:
    """Evalúa polaridad (`POSITIVO`, `NEUTRO`, `CRÍTICO`) con inversión por negaciones."""
    toks = _tokens(text)
    if not toks:
        return {"label": "NEUTRO", "score": 0.0, "pos_hits": [], "neg_hits": []}

    pos_hits = []
    neg_hits = []
    score_raw = 0.0

    for idx, tok in enumerate(toks):
        prev_window = set(toks[max(0, idx - 2) : idx])
        negated = bool(prev_window & _NEGATORS)

        if tok in _POS_WORDS:
            if negated:
                score_raw -= 1.0
                neg_hits.append(f"no {tok}")
            else:
                score_raw += 1.0
                pos_hits.append(tok)
        elif tok in _NEG_WORDS:
            if negated:
                score_raw += 0.6
                pos_hits.append(f"no {tok}")
            else:
                score_raw -= 1.0
                neg_hits.append(tok)

    total_hits = len(pos_hits) + len(neg_hits)
    norm_score = round(max(-1.0, min(1.0, score_raw / max(1, total_hits))), 2) if total_hits else 0.0
    if norm_score >= 0.25:
        label = "POSITIVO"
    elif norm_score <= -0.25:
        label = "CRÍTICO"
    else:
        label = "NEUTRO"

    return {
        "label": label,
        "score": norm_score,
        "pos_hits": pos_hits[:5],
        "neg_hits": neg_hits[:5],
    }


def _aggregate_sentiments(sent_list: list) -> dict:
    total = len(sent_list)
    pos = sum(1 for s in sent_list if s["label"] == "POSITIVO")
    neu = sum(1 for s in sent_list if s["label"] == "NEUTRO")
    neg = sum(1 for s in sent_list if s["label"] == "CRÍTICO")
    avg = round(sum(s["score"] for s in sent_list) / total, 2) if total else 0.0
    dominant = "NEUTRO"
    if total:
        if pos > neg and pos >= neu * 0.6:
            dominant = "POSITIVO"
        elif neg > pos and neg >= neu * 0.6:
            dominant = "CRÍTICO"
    return {
        "total": total,
        "positive": pos,
        "neutral": neu,
        "critical": neg,
        "positive_pct": round((pos / total) * 100, 1) if total else 0.0,
        "neutral_pct": round((neu / total) * 100, 1) if total else 0.0,
        "critical_pct": round((neg / total) * 100, 1) if total else 0.0,
        "avg_score": avg,
        "dominant": dominant,
    }


# ---------------------------------------------------------------------------
# Extracción de Entidades (NER ligero) y Línea de Tiempo
# ---------------------------------------------------------------------------
def extract_entities(texts: list, profile_locations: Optional[list] = None) -> dict:
    places = Counter()
    orgs = Counter()
    domains = Counter()
    hashtags = Counter()
    mentions = Counter()
    keywords = Counter()

    for loc in profile_locations or []:
        nloc = _norm(loc)
        if nloc:
            matched_known = False
            for k, pretty in _KNOWN_PLACES.items():
                if k in nloc:
                    places[pretty] += 1
                    matched_known = True
            if not matched_known and len(loc.strip()) >= 3:
                places[loc.strip()] += 1

    for raw in texts or []:
        if not raw:
            continue
        # Organizaciones por patrón institucional
        for m in _ORG_PATTERN.findall(raw):
            orgs[m.strip()] += 1
        # Dominios externos
        for u in _URL_RE.findall(raw):
            host = (urlparse(u).netloc or "").lower().removeprefix("www.")
            if host and "." in host:
                domains[host] += 1
        # Hashtags y menciones
        for h in _HASHTAG_RE.findall(raw):
            hashtags[h.lower()] += 1
        for mn in _MENTION_RE.findall(raw):
            mentions[mn.strip(".,;:!?").lower()] += 1
        # Lugares conocidos
        nt = _norm(raw)
        for k, pretty in _KNOWN_PLACES.items():
            if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", nt):
                places[pretty] += 1
        # Palabras clave limpias (quitando URLs)
        sin_urls = _URL_RE.sub(" ", raw)
        for tok in _tokens(sin_urls):
            if len(tok) >= 4 and not tok.isdigit() and tok not in _STOPWORDS:
                keywords[tok] += 1

    return {
        "places": [{"name": k, "count": v} for k, v in places.most_common(12)],
        "organizations": [{"name": k, "count": v} for k, v in orgs.most_common(12)],
        "domains": [{"name": k, "count": v} for k, v in domains.most_common(12)],
        "hashtags": [{"name": k, "count": v} for k, v in hashtags.most_common(15)],
        "mentions": [{"name": k, "count": v} for k, v in mentions.most_common(15)],
        "keywords": [{"word": k, "count": v} for k, v in keywords.most_common(20)],
    }


def _parse_dt(raw: str) -> Optional[datetime]:
    s = (raw or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def build_activity_timeline(dated_items: list) -> dict:
    """Calcula distribución mensual, por día de la semana y por franja horaria."""
    by_month = Counter()
    by_weekday = Counter()
    buckets = {"Madrugada (00-05)": 0, "Mañana (06-11)": 0, "Tarde (12-17)": 0, "Noche (18-23)": 0}
    parsed_count = 0

    for raw_date in dated_items or []:
        dt = _parse_dt(raw_date)
        if not dt:
            continue
        parsed_count += 1
        by_month[dt.strftime("%Y-%m")] += 1
        by_weekday[dt.weekday()] += 1
        # Solo contar hora si la cadena incluía componente horario
        if ":" in (raw_date or ""):
            h = dt.hour
            if h < 6:
                buckets["Madrugada (00-05)"] += 1
            elif h < 12:
                buckets["Mañana (06-11)"] += 1
            elif h < 18:
                buckets["Tarde (12-17)"] += 1
            else:
                buckets["Noche (18-23)"] += 1

    months_sorted = [{"month": m, "count": by_month[m]} for m in sorted(by_month.keys())[-12:]]
    weekdays = [{"day": _WEEKDAYS_ES[i], "count": by_weekday.get(i, 0)} for i in range(7)]
    time_buckets = [{"slot": k, "count": v} for k, v in buckets.items()]
    peak_slot = max(time_buckets, key=lambda x: x["count"])["slot"] if any(x["count"] > 0 for x in time_buckets) else "Sin datos horarios"

    return {
        "dated_items": parsed_count,
        "by_month": months_sorted,
        "by_weekday": weekdays,
        "by_time_slot": time_buckets,
        "peak_slot": peak_slot,
    }


# ---------------------------------------------------------------------------
# Síntesis opcional con LLM (Ollama / Gemini / OpenAI)
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


def _call_llm_synthesis(prompt: str, provider: str) -> tuple:
    """Devuelve (dict_json_o_None, engine_name, note)."""
    prov = (provider or "local").strip().lower()
    if prov == "local":
        return None, "local", ""

    try:
        if prov in ("auto", "ollama"):
            oll = _check_ollama_text()
            if oll.get("available"):
                r = requests.post(
                    f"{oll['url']}/api/generate",
                    json={"model": oll["selected_model"], "prompt": prompt, "stream": False, "format": "json"},
                    timeout=45,
                )
                if r.status_code == 200:
                    parsed = _extract_json((r.json() or {}).get("response") or "")
                    if parsed:
                        return parsed, f"ollama:{oll['selected_model']}", ""
            if prov == "ollama":
                return None, "local", "Ollama no disponible; se generó síntesis local determinista."

        if prov in ("auto", "gemini") and os.environ.get("GEMINI_API_KEY", "").strip():
            api_key = os.environ["GEMINI_API_KEY"].strip()
            model = os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
            payload = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
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
                try:
                    r = requests.post(url, params={"key": api_key}, json=payload, timeout=35)
                    if r.status_code != 200:
                        continue
                    cands = (r.json() or {}).get("candidates") or []
                    parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
                    txt = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
                    parsed = _extract_json(txt)
                    if parsed:
                        return parsed, "gemini", ""
                except Exception:
                    continue
            if prov == "gemini":
                return None, "local", "Gemini API sin respuesta; se generó síntesis local."

        if prov in ("auto", "openai") and os.environ.get("OPENAI_API_KEY", "").strip():
            model = os.environ.get("OPENAI_TEXT_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY'].strip()}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=35,
            )
            if r.status_code == 200:
                choices = (r.json() or {}).get("choices") or []
                txt = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
                parsed = _extract_json(txt)
                if parsed:
                    return parsed, "openai", ""
            if prov == "openai":
                return None, "local", "OpenAI API sin respuesta; se generó síntesis local."
    except Exception as exc:  # noqa: BLE001
        return None, "local", f"Fallback local tras error en LLM: {exc}"

    return None, "local", ""


# ---------------------------------------------------------------------------
# Pipeline principal NLP sobre un Caso
# ---------------------------------------------------------------------------
def analyze_case_nlp(corpus: dict, provider: str = "local") -> dict:
    """
    Analiza el corpus completo de un caso:
      - `subject_handles`: set de usernames del sujeto
      - `bios`: lista de `{site, text, location}`
      - `posts`: lista de posts del caso
      - `comments`: lista de comentarios del caso (cada uno con `id`, `author_handle`, `target_handle`, `content`, `published_at`)
      - `vision_themes`: temas visuales agregados en Fase 3 (`theme_summary`)
    """
    subject_handles = {h.strip().lstrip("@").lower() for h in (corpus.get("subject_handles") or []) if h}
    bios = corpus.get("bios") or []
    posts = corpus.get("posts") or []
    comments = corpus.get("comments") or []
    vision_themes = corpus.get("vision_themes") or []

    # 1) Clasificar textos en SUJETO vs. TERCEROS
    subject_docs = []  # {source_type, label, text, date, sentiment}
    others_docs = []
    comment_sentiments = {}  # comment_id -> label

    profile_locations = []
    for b in bios:
        if b.get("location"):
            profile_locations.append(b["location"])
        txt = (b.get("text") or "").strip()
        if txt:
            sent = analyze_sentiment(txt)
            subject_docs.append({
                "source_type": "bio",
                "label": f"Bio ({b.get('site') or 'perfil'})",
                "text": txt,
                "date": "",
                "sentiment": sent,
            })

    for p in posts:
        txt = (p.get("content") or "").strip()
        if not txt:
            continue
        author = (p.get("author") or "").strip().lstrip("@").lower()
        sent = analyze_sentiment(txt)
        doc = {
            "source_type": "post",
            "label": f"Post ({p.get('platform') or 'red'})",
            "text": txt,
            "date": p.get("published_at") or "",
            "sentiment": sent,
        }
        # Si no hay handles registrados asumimos posts ingeridos como propios del sujeto
        if not subject_handles or not author or author in subject_handles:
            subject_docs.append(doc)
        else:
            others_docs.append(doc)

    for c in comments:
        txt = (c.get("content") or "").strip()
        if not txt:
            continue
        author = (c.get("author_handle") or "").strip().lstrip("@").lower()
        sent = analyze_sentiment(txt)
        if c.get("id") is not None:
            comment_sentiments[c["id"]] = sent["label"]
        doc = {
            "source_type": "comment",
            "label": f"Comentario de @{author or 'usuario'}",
            "text": txt,
            "date": c.get("published_at") or "",
            "sentiment": sent,
        }
        if subject_handles and author in subject_handles:
            subject_docs.append(doc)
        else:
            others_docs.append(doc)

    # 2) Mapa Unificado de Intereses (Texto del sujeto + Temas Visuales de Fase 3)
    interests_map = {}

    for doc in subject_docs:
        nt = _norm(doc["text"])
        for category, kws in FULL_THEME_TAXONOMY.items():
            matched = [kw for kw in kws if re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", nt)]
            if not matched:
                continue
            entry = interests_map.setdefault(
                category,
                {
                    "label": category,
                    "text_hits": 0,
                    "bio_hits": 0,
                    "vision_hits": 0,
                    "sentiments": [],
                    "keywords": Counter(),
                    "evidence_quotes": [],
                },
            )
            entry["text_hits"] += 1
            if doc["source_type"] == "bio":
                entry["bio_hits"] += 1
            entry["sentiments"].append(doc["sentiment"])
            for kw in matched:
                entry["keywords"][kw] += 1
            if len(entry["evidence_quotes"]) < 3:
                entry["evidence_quotes"].append({
                    "source": doc["label"],
                    "quote": doc["text"][:180],
                    "sentiment": doc["sentiment"]["label"],
                })

    # Fusionar temas visuales de Fase 3
    for vt in vision_themes:
        label = (vt.get("label") or "").strip()
        if not label:
            continue
        entry = interests_map.setdefault(
            label,
            {
                "label": label,
                "text_hits": 0,
                "bio_hits": 0,
                "vision_hits": 0,
                "sentiments": [],
                "keywords": Counter(),
                "evidence_quotes": [],
            },
        )
        entry["vision_hits"] += int(vt.get("count") or 0)
        for ev in (vt.get("evidences") or [])[:2]:
            if len(entry["evidence_quotes"]) < 4:
                entry["evidence_quotes"].append({
                    "source": "Galería visual (Fase 3)",
                    "quote": str(ev)[:180],
                    "sentiment": "NEUTRO",
                })

    unified_interests = []
    for entry in interests_map.values():
        total_ev = entry["text_hits"] + entry["vision_hits"]
        if entry["text_hits"] > 0 and entry["vision_hits"] > 0:
            modality = "Texto + Fotos"
        elif entry["vision_hits"] > 0:
            modality = "Fotos"
        else:
            modality = "Texto"

        if (entry["text_hits"] >= 1 and entry["vision_hits"] >= 1) or total_ev >= 3 or entry["bio_hits"] >= 1:
            conf = "ALTA"
        elif total_ev >= 2:
            conf = "MEDIA"
        else:
            conf = "BAJA"

        tone = _aggregate_sentiments(entry["sentiments"])["dominant"] if entry["sentiments"] else "NEUTRO"
        unified_interests.append({
            "label": entry["label"],
            "modality": modality,
            "confidence": conf,
            "total_evidence": total_ev,
            "text_hits": entry["text_hits"],
            "vision_hits": entry["vision_hits"],
            "dominant_tone": tone,
            "top_keywords": [k for k, _ in entry["keywords"].most_common(6)],
            "evidence_quotes": entry["evidence_quotes"],
        })
    unified_interests.sort(key=lambda x: (-x["total_evidence"], x["label"]))

    # 3) Sentimiento separado (Sujeto vs. Terceros)
    subject_sentiment = _aggregate_sentiments([d["sentiment"] for d in subject_docs])
    others_sentiment = _aggregate_sentiments([d["sentiment"] for d in others_docs])

    # 4) Entidades (NER) y Línea de Tiempo
    subject_texts = [d["text"] for d in subject_docs]
    all_texts = subject_texts + [d["text"] for d in others_docs]
    entities = extract_entities(all_texts, profile_locations=profile_locations)
    timeline = build_activity_timeline([d["date"] for d in subject_docs if d.get("date")])

    # 5) Cautelas metodológicas automáticas
    caveats = []
    if len(subject_docs) < 5:
        caveats.append(
            f"Muestra textual reducida ({len(subject_docs)} textos del sujeto). Las categorías detectadas son indicativas y requieren más publicaciones o contexto."
        )
    caveats.append(
        "El análisis automático de sentimiento puede confundir sarcasmo, humor o citas de terceros; revisa siempre las citas de evidencia."
    )

    # 6) Síntesis ejecutiva (opcional con LLM o determinista local)
    llm_synthesis = None
    engine_used = "local"
    note = ""
    if provider and provider.lower() != "local" and (subject_docs or others_docs):
        sample_bios = "\n".join(f"- {d['text'][:200]}" for d in subject_docs if d["source_type"] == "bio")[:600]
        sample_posts = "\n".join(f"- {d['text'][:220]}" for d in subject_docs if d["source_type"] == "post")[:1800]
        sample_others = "\n".join(f"- {d['text'][:180]}" for d in others_docs[:10])[:1000]
        prompt = (
            "Eres un analista OSINT riguroso. Resume de forma objetiva y en español EXCLUSIVAMENTE lo respaldado por estos textos públicos.\n"
            "Devuelve SOLO un JSON válido con claves:\n"
            '{"executive_summary": "resumen neutral en 2-3 frases", '
            '"communication_style": "tono y estilo observable en 1 frase", '
            '"key_topics": ["hasta 5 temas principales observables"]}\n'
            f"Biografías:\n{sample_bios or '(ninguna)'}\n\n"
            f"Publicaciones del sujeto:\n{sample_posts or '(ninguna)'}\n\n"
            f"Comentarios de terceros:\n{sample_others or '(ninguno)'}\n"
        )
        llm_synthesis, engine_used, note = _call_llm_synthesis(prompt, provider)

    if llm_synthesis:
        summary_block = {
            "executive_summary": str(llm_synthesis.get("executive_summary") or "").strip(),
            "communication_style": str(llm_synthesis.get("communication_style") or "").strip(),
            "key_topics": [str(x) for x in (llm_synthesis.get("key_topics") or [])[:6]],
        }
    else:
        top_topic_names = [i["label"] for i in unified_interests[:4]]
        if top_topic_names:
            exec_txt = (
                f"Se analizaron {len(subject_docs)} textos del sujeto y {len(others_docs)} comentarios de terceros. "
                f"Los temas con mayor respaldo verificable son: {', '.join(top_topic_names)}. "
                f"El tono general observable del sujeto es {subject_sentiment['dominant']} "
                f"y el de los comentarios recibidos es {others_sentiment['dominant']}."
            )
        else:
            exec_txt = (
                f"Se analizaron {len(subject_docs)} textos del sujeto y {len(others_docs)} comentarios de terceros, "
                "sin coincidencias fuertes en categorías temáticas todavía. Ingiere más publicaciones o biografías para ampliar el perfilado."
            )
        summary_block = {
            "executive_summary": exec_txt,
            "communication_style": f"Tono predominante {subject_sentiment['dominant']} (puntaje medio {subject_sentiment['avg_score']:+.2f}).",
            "key_topics": top_topic_names,
        }

    return {
        "engine": engine_used,
        "note": note,
        "analyzed_at": _now_iso(),
        "counts": {
            "subject_docs": len(subject_docs),
            "others_docs": len(others_docs),
            "bios": sum(1 for d in subject_docs if d["source_type"] == "bio"),
            "posts": sum(1 for d in subject_docs if d["source_type"] == "post"),
            "comments_analyzed": len(comment_sentiments),
            "interests_count": len(unified_interests),
        },
        "summary": summary_block,
        "unified_interests": unified_interests,
        "sentiment": {
            "subject": subject_sentiment,
            "others": others_sentiment,
        },
        "entities": entities,
        "timeline": timeline,
        "caveats": caveats,
        "comment_sentiments": comment_sentiments,
    }
