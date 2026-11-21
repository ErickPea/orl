# -*- coding: utf-8 -*-
"""
Motor de Análisis Visual y Galería Inteligente para OSINT-Auto (Fase 3).

Capacidades:
  1. Hash perceptual dHash (64-bit en Python puro con Pillow) + distancia de Hamming
     para detectar fotos o avatares repetidos/recomprimidos entre plataformas.
  2. Métricas visuales locales (dimensiones, relación de aspecto, paleta de colores
     dominantes, heurística de captura de pantalla).
  3. Análisis multimodal con modelos de visión (intercambiable y opcional):
       - Ollama local (`OLLAMA_URL`, `OLLAMA_VISION_MODEL`, p. ej. `qwen2.5-vl` / `llava`)
       - Gemini API oficial (`GEMINI_API_KEY`)
       - OpenAI Vision API oficial (`OPENAI_API_KEY`)
  4. Respaldo 100% local (`local_context`): OCR opcional con `tesseract` + clasificación
     temática trazable sobre `alt_text`, pies de foto, nombres de archivo y texto OCR.
  5. Agregación por caso: intereses visuales con conteo de fotos de evidencia y clústeres
     de imágenes visualmente similares.
"""
import base64
import io
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

from modules.logger import get_logger

_LOGGER = get_logger("vision_analysis")

import requests

try:
    from PIL import Image, ImageOps
    PILLOW_OK = True
except ImportError:
    PILLOW_OK = False

USER_AGENT = "OSINT-Auto/2.2 (vision-analysis)"
MAX_REMOTE_IMAGE_BYTES = 12 * 1024 * 1024  # 12 MB


# Taxonomía curada ES/EN para clasificación de intereses visuales con evidencia explícita
VISUAL_THEME_TAXONOMY = {
    "Tecnología y Programación": [
        "codigo", "code", "python", "javascript", "laptop", "computador", "computadora",
        "teclado", "keyboard", "monitor", "servidor", "linux", "github", "terminal",
        "software", "programacion", "hardware", "robot", "inteligencia artificial", "developer",
    ],
    "Naturaleza y Viajes": [
        "montana", "mountain", "playa", "beach", "bosque", "forest", "paisaje", "landscape",
        "senderismo", "hiking", "viaje", "travel", "amanecer", "atardecer", "sunset", "mar",
        "rio", "lago", "camping", "volcan", "avion", "aeropuerto", "turismo", "naturaleza",
    ],
    "Deportes y Fitness": [
        "bicicleta", "ciclismo", "cycling", "futbol", "soccer", "gimnasio", "gym", "running",
        "correr", "maraton", "natacion", "basket", "tenis", "crossfit", "entrenamiento",
        "fitness", "deporte", "sport", "estadio", "balon", "casco",
    ],
    "Fotografía y Arte": [
        "camara", "camera", "lente", "fotografia", "photography", "retrato", "portrait",
        "pintura", "dibujo", "ilustracion", "diseno", "design", "museo", "galeria", "arte",
        "art", "escultura", "cine", "pelicula",
    ],
    "Música y Conciertos": [
        "guitarra", "piano", "bateria", "concierto", "concert", "musica", "music", "festival",
        "vinilo", "audifonos", "headphones", "microfono", "escenario", "banda", "dj", "cancion",
    ],
    "Gastronomía y Café": [
        "cafe", "coffee", "restaurante", "comida", "food", "pizza", "hamburguesa", "cocina",
        "receta", "postre", "vino", "cerveza", "barista", "desayuno", "almuerzo", "cena",
    ],
    "Gaming y Esports": [
        "gaming", "videojuego", "consola", "playstation", "xbox", "nintendo", "steam",
        "esports", "twitch", "control", "joystick", "setup", "gamer", "pc gamer",
    ],
    "Lectura y Educación": [
        "libro", "book", "biblioteca", "universidad", "estudio", "conferencia", "charla",
        "clase", "diploma", "graduacion", "ciencia", "investigacion", "articulo", "paper",
    ],
    "Mascotas y Animales": [
        "perro", "dog", "gato", "cat", "mascota", "pet", "cachorro", "aves", "caballo",
        "animal", "veterinario", "pajaro",
    ],
    "Arquitectura y Vida Urbana": [
        "ciudad", "city", "edificio", "calle", "street", "puente", "arquitectura",
        "architecture", "urbano", "urban", "metro", "plaza", "rascacielos",
    ],
    "Vehículos y Motores": [
        "carro", "auto", "coche", "car", "moto", "motocicleta", "motorcycle", "motor",
        "carreras", "racing", "vehiculo", "camioneta",
    ],
    "Moda y Estilo": [
        "moda", "fashion", "outfit", "ropa", "zapatos", "sneakers", "reloj", "estilo",
        "style", "accesorios", "maquillaje",
    ],
    "Eventos y Vida Social": [
        "fiesta", "party", "amigos", "friends", "familia", "family", "boda", "cumpleanos",
        "reunion", "evento", "celebracion", "grupo", "selfie",
    ],
}


# Para qué sirve: obtiene la fecha y hora actual en formato ISO local legible para sellar análisis visuales.
# Cómo funciona: formatea `datetime.now()` como `YYYY-MM-DD HH:MM:SS`.
def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# Para qué sirve: normaliza textos sin tildes ni mayúsculas para comparar etiquetas OCR y categorías.
# Cómo funciona: descompone en NFD con `unicodedata`, filtra marcas diacríticas (`Mn`) y colapsa espacios.
def _norm(text: str) -> str:
    if not text:
        return ""
    nfd = unicodedata.normalize("NFD", str(text))
    clean = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    return " ".join(clean.lower().split())


# ---------------------------------------------------------------------------
# Capacidades y detección de motores disponibles
# ---------------------------------------------------------------------------
# Para qué sirve: detecta si Ollama local tiene modelos de visión listos para analizar imágenes.
# Cómo funciona: consulta `/api/tags` y filtra nombres con pistas multimodales (`vl`, `llava`, `vision`, `gemma3`, etc.).
def _check_ollama(timeout: float = 1.5) -> dict:
    base_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    preferred = os.environ.get("OLLAMA_VISION_MODEL", "").strip()
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=timeout)
        if r.status_code != 200:
            return {"available": False, "url": base_url, "models": [], "selected_model": preferred}
        models = [m.get("name", "") for m in (r.json() or {}).get("models", []) if m.get("name")]
        vision_hints = ("vl", "llava", "vision", "bakllava", "moondream", "minicpm-v", "gemma3")
        vision_models = [m for m in models if any(h in m.lower() for h in vision_hints)]
        selected = preferred or (vision_models[0] if vision_models else "")
        return {
            "available": bool(selected),
            "server_up": True,
            "url": base_url,
            "models": vision_models,
            "selected_model": selected,
        }
    except Exception:
        return {"available": False, "server_up": False, "url": base_url, "models": [], "selected_model": preferred}


# Para qué sirve: informa qué motores de visión (`dHash`, Tesseract OCR, Ollama, Gemini, OpenAI) están activos.
# Cómo funciona: evalúa `PILLOW_OK`, `shutil.which("tesseract")`, `_check_ollama()` y claves API en el entorno.
def vision_capabilities(probe_ollama: bool = True) -> dict:
    """Informa qué motores de análisis visual están listos en la máquina actual."""
    ollama = _check_ollama() if probe_ollama else {"available": False, "server_up": False, "models": [], "selected_model": ""}
    gemini_ok = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    openai_ok = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    tesseract_ok = bool(shutil.which("tesseract"))

    default_engine = "local_context"
    if ollama.get("available"):
        default_engine = f"ollama:{ollama['selected_model']}"
    elif gemini_ok:
        default_engine = "gemini"
    elif openai_ok:
        default_engine = "openai"

    return {
        "pillow": PILLOW_OK,
        "dhash": PILLOW_OK,
        "tesseract_ocr": tesseract_ok,
        "ollama": ollama,
        "gemini": gemini_ok,
        "openai": openai_ok,
        "default_engine": default_engine,
    }


# ---------------------------------------------------------------------------
# Hash Perceptual (dHash 64-bit) y señales visuales locales con Pillow
# ---------------------------------------------------------------------------
# Para qué sirve: genera una huella visual de 64 bits resistente a redimensionado y recompresión JPEG.
# Cómo funciona: corrige orientación EXIF, escala a `(hash_size+1) x hash_size` en grises y compara gradientes horizontales.
def compute_dhash_from_image(img: "Image.Image", hash_size: int = 8) -> str:
    """
    Calcula un hash perceptual dHash de 64 bits (16 hex chars) usando solo Pillow.
    Robusto ante cambios de tamaño, compresión JPEG y ligeros ajustes de brillo.
    """
    gray = ImageOps.exif_transpose(img).convert("L")
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    resized = gray.resize((hash_size + 1, hash_size), resampling)
    pixels = list(resized.getdata())
    bits = 0
    for row in range(hash_size):
        offset = row * (hash_size + 1)
        for col in range(hash_size):
            bits = (bits << 1) | (1 if pixels[offset + col] > pixels[offset + col + 1] else 0)
    return f"{bits:0{hash_size * hash_size // 4}x}"


# Para qué sirve: mide la similitud visual entre dos hashes dHash contando bits distintos.
# Cómo funciona: aplica XOR entre ambos enteros hexadecimales y devuelve `.bit_count()`.
def hamming_distance(hash_a: str, hash_b: str) -> Optional[int]:
    """Distancia de Hamming entre dos dHashes hexadecimales (0 = idénticas, <= 10 = muy similares)."""
    if not hash_a or not hash_b or len(hash_a) != len(hash_b):
        return None
    try:
        diff = int(hash_a, 16) ^ int(hash_b, 16)
        return diff.bit_count()
    except ValueError:
        return None


# Para qué sirve: obtiene la paleta de colores hexadecimales dominantes de una imagen.
# Cómo funciona: reduce la imagen a 48x48, cuantiza a `count` colores (MEDIANCUT) y ordena por frecuencia.
def _dominant_colors(img: "Image.Image", count: int = 3) -> list:
    """Extrae los colores hex predominantes reduciendo la imagen a una paleta cuantizada."""
    try:
        small = img.convert("RGB").resize((48, 48))
        paletted = small.quantize(colors=count, method=Image.Quantize.MEDIANCUT)
        palette = paletted.getpalette() or []
        color_counts = sorted(paletted.getcolors() or [], key=lambda x: -x[0])
        hexes = []
        for _, idx in color_counts[:count]:
            base = idx * 3
            if base + 2 < len(palette):
                r, g, b = palette[base], palette[base + 1], palette[base + 2]
                hexes.append(f"#{r:02x}{g:02x}{b:02x}")
        return hexes
    except Exception:
        return []


# Para qué sirve: extrae señales visuales locales (dHash, dimensiones, relación de aspecto y paleta) desde bytes.
# Cómo funciona: abre el stream con Pillow, aplica `exif_transpose` y deduce formato visual aproximado por aspect ratio.
def _extract_pillow_signals(image_bytes: bytes) -> dict:
    if not PILLOW_OK or not image_bytes:
        return {"dhash": "", "dimensions": {}, "dominant_colors": [], "visual_hints": []}
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            w, h = img.size
            dhash = compute_dhash_from_image(img)
            colors = _dominant_colors(img, count=3)
            ratio = round(w / h, 2) if h else 0.0
            hints = []
            if w and h:
                if ratio >= 1.65:
                    hints.append("formato panorámico / horizontal")
                elif ratio <= 0.65:
                    hints.append("formato vertical (historia / móvil)")
                elif 0.9 <= ratio <= 1.1:
                    hints.append("formato cuadrado (avatar / feed)")
            return {
                "dhash": dhash,
                "dimensions": {"width": w, "height": h, "aspect_ratio": ratio},
                "dominant_colors": colors,
                "visual_hints": hints,
            }
    except Exception:
        return {"dhash": "", "dimensions": {}, "dominant_colors": [], "visual_hints": []}


# Para qué sirve: extrae texto legible impreso dentro de una imagen de forma local mediante OCR.
# Cómo funciona: escribe un PNG temporal, invoca `tesseract ... stdout --psm 6` (máx. 15 s) y borra el temporal en `finally`.
def _run_tesseract_ocr(image_bytes: bytes) -> str:
    """Ejecuta OCR local con `tesseract` si está instalado en el sistema."""
    if not shutil.which("tesseract") or not image_bytes:
        return ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            tmp_path = Path(tf.name)
            tf.write(image_bytes)
        try:
            proc = subprocess.run(
                ["tesseract", str(tmp_path), "stdout", "--psm", "6"],
                capture_output=True,
                text=True,
                timeout=15,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode == 0:
                return " ".join(proc.stdout.split())[:600]
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception:
        return ""
    return ""


# ---------------------------------------------------------------------------
# Clasificador temático sobre contexto (alt-text, caption, OCR, filename)
# ---------------------------------------------------------------------------
# Para qué sirve: mapea texto contextual de imágenes (OCR, caption, alt-text) a categorías de interés con evidencia literal.
# Cómo funciona: busca coincidencias exactas de palabra contra `VISUAL_THEME_TAXONOMY` y sube confianza a `ALTA` con 2+ hits.
def classify_themes_from_text(text: str, base_confidence: str = "MEDIA") -> list:
    """Clasifica un texto en la taxonomía de intereses visuales devolviendo evidencia literal."""
    norm = _norm(text)
    if not norm:
        return []
    hits = []
    for category, keywords in VISUAL_THEME_TAXONOMY.items():
        matched = []
        for kw in keywords:
            if re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", norm):
                matched.append(kw)
        if matched:
            conf = "ALTA" if len(matched) >= 2 and base_confidence != "BAJA" else base_confidence
            hits.append({
                "label": category,
                "confidence": conf,
                "evidence": f"Palabras clave: {', '.join(matched[:4])}",
                "keywords": matched[:6],
            })
    hits.sort(key=lambda x: (-len(x["keywords"]), x["label"]))
    return hits


# ---------------------------------------------------------------------------
# Proveedores Multimodales (VLM) opcionales: Ollama, Gemini, OpenAI
# ---------------------------------------------------------------------------
_VLM_PROMPT = (
    "Analiza objetivamente esta imagen y devuelve EXCLUSIVAMENTE un objeto JSON válido "
    "(sin markdown ni texto extra) con estas claves:\n"
    '{"description": "descripción breve y objetiva en español (máx 180 caracteres)", '
    '"scene": "entorno principal en español (ej. montaña, oficina, ciudad, interior)", '
    '"objects": ["hasta 6 objetos o elementos claramente visibles"], '
    '"themes": ["1 a 3 temas de interés visibles, ej. Naturaleza y Viajes, Tecnología y Programación, Deportes y Fitness, Fotografía y Arte, Música y Conciertos, Gastronomía y Café, Gaming y Esports, Lectura y Educación, Mascotas y Animales, Arquitectura y Vida Urbana, Vehículos y Motores, Moda y Estilo, Eventos y Vida Social"], '
    '"ocr_text": "texto legible dentro de la imagen si existe, o cadena vacía", '
    '"confidence": "ALTA o MEDIA o BAJA"}\n'
    "Reglas: describe solo lo visible sin especular sobre identidad privada ni salud."
)


# Para qué sirve: extrae un diccionario JSON válido desde la salida cruda de un modelo VLM.
# Cómo funciona: limpia bloques Markdown ```json y hace fallback por regex sobre `{...}`.
def _extract_json_block(raw_text: str) -> Optional[dict]:
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
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


# Para qué sirve: analiza una imagen localmente usando un modelo multimodal en Ollama.
# Cómo funciona: codifica los bytes en base64, llama a `/api/generate` con `format="json"` y parsea la respuesta.
def _call_ollama_vision(image_bytes: bytes, model: str, base_url: str, timeout: int = 45, prompt: Optional[str] = None) -> Optional[dict]:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    r = requests.post(
        f"{base_url.rstrip('/')}/api/generate",
        json={
            "model": model,
            "prompt": prompt or _VLM_PROMPT,
            "images": [b64],
            "stream": False,
            "format": "json",
        },
        timeout=timeout,
    )
    if r.status_code != 200:
        return None
    return _extract_json_block((r.json() or {}).get("response") or "")


# Para qué sirve: analiza una imagen mediante Google Gemini Vision (AI Studio o Vertex Express Mode).
# Cómo funciona: envía el payload base64 + `inline_data` en orden de endpoint según el prefijo de `GEMINI_API_KEY`.
def _call_gemini_vision(image_bytes: bytes, mime_type: str = "image/jpeg", timeout: int = 35, prompt: Optional[str] = None) -> Optional[dict]:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    model = os.environ.get("GEMINI_VISION_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt or _VLM_PROMPT},
                    {"inline_data": {"mime_type": mime_type, "data": b64}},
                ],
            }
        ],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
    }
    # Claves `AQ.` suelen pertenecer a Vertex AI Express Mode; `AIza...` a Google AI Studio
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
            r = requests.post(url, params={"key": api_key}, json=payload, timeout=timeout)
            if r.status_code != 200:
                continue
            candidates = (r.json() or {}).get("candidates") or []
            parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
            parsed = _extract_json_block(text)
            if parsed:
                return parsed
        except Exception:
            continue
    return None


# Para qué sirve: analiza una imagen usando la API de visión de OpenAI (`gpt-4o-mini` por defecto).
# Cómo funciona: incrusta `data:{mime};base64,{b64}` en `/v1/chat/completions` pidiendo `json_object`.
def _call_openai_vision(image_bytes: bytes, mime_type: str = "image/jpeg", timeout: int = 35, prompt: Optional[str] = None) -> Optional[dict]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    model = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt or _VLM_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                    ],
                }
            ],
        },
        timeout=timeout,
    )
    if r.status_code != 200:
        return None
    choices = (r.json() or {}).get("choices") or []
    text = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
    return _extract_json_block(text)


# Para qué sirve: previene ataques SSRF asegurando que toda URL remota apunte únicamente a Internet público.
# Cómo funciona: exige HTTP(S), rechaza dominios `.local`/`.internal`/`localhost` y verifica `ip_obj.is_global` vía IP literal o `getaddrinfo`.
def is_safe_public_http_url(url: str) -> bool:
    """
    Valida contra SSRF que la URL sea HTTP(S) hacia un host público global
    (bloqueando localhost, IPs privadas/link-local/loopback/reservadas y metadatos cloud).
    """
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or host in ("localhost", "localhost.localdomain") or host.endswith((".local", ".internal", ".localhost")):
        return False
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)

    # 1) Si ya es IP literal, validar directamente sin DNS
    try:
        ip_obj = ipaddress.ip_address(host)
        return bool(ip_obj.is_global)
    except ValueError:
        pass

    # 2) Resolver DNS y exigir que todas las IPs resueltas sean globales
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            return False
        ip_raw = str(sockaddr[0]).split("%", 1)[0]
        try:
            ip_obj = ipaddress.ip_address(ip_raw)
        except ValueError:
            return False
        if not ip_obj.is_global:
            return False
    return True


# Para qué sirve: descarga una imagen pública de forma segura evitando SSRF por redirecciones y desbordamiento de memoria.
# Cómo funciona: valida cada salto `Location` con `is_safe_public_http_url`, exige `Content-Type: image/*` y corta en `12 MB`.
def download_public_image(url: str, timeout: int = 12, max_redirects: int = 3) -> tuple:
    """Descarga una imagen pública validando SSRF por salto y límite de tamaño (12 MB). Devuelve (bytes, mime_type)."""
    cur_url = (url or "").strip()
    if not is_safe_public_http_url(cur_url):
        _LOGGER.warning("URL bloqueada por política anti-SSRF en descarga de imagen: %s", cur_url[:160])
        return b"", "image/jpeg"
    try:
        r = None
        for _ in range(max_redirects + 1):
            r = requests.get(
                cur_url,
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
                stream=True,
                allow_redirects=False,
            )
            if r.status_code in (301, 302, 303, 307, 308):
                loc = (r.headers.get("Location") or "").strip()
                r.close()
                if not loc:
                    return b"", "image/jpeg"
                nxt_url = urljoin(cur_url, loc)
                if not is_safe_public_http_url(nxt_url):
                    _LOGGER.warning("Redirección SSRF bloqueada hacia: %s", nxt_url[:160])
                    return b"", "image/jpeg"
                cur_url = nxt_url
                continue
            break

        if r is None or r.status_code != 200:
            return b"", "image/jpeg"
        try:
            ctype = (r.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip().lower()
            if not ctype.startswith("image/"):
                return b"", ctype
            buf = bytearray()
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > MAX_REMOTE_IMAGE_BYTES:
                    return b"", ctype
            return bytes(buf), ctype
        finally:
            r.close()
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Error descargando imagen pública %s: %s", cur_url[:160], exc)
        return b"", "image/jpeg"


# ---------------------------------------------------------------------------
# Función principal de análisis por imagen
# ---------------------------------------------------------------------------
# Para qué sirve: analiza una imagen combinando métricas Pillow, OCR local y opcionalmente un VLM para extraer temas y descripción.
# Cómo funciona: calcula dHash/paleta/dimensiones, consulta Ollama/Gemini/OpenAI según `provider` o usa `_run_tesseract_ocr` + contexto.
def analyze_image_bytes(
    image_bytes: bytes,
    alt_text: str = "",
    caption: str = "",
    filename: str = "",
    provider: str = "auto",
    mime_type: str = "image/jpeg",
) -> dict:
    """
    Analiza una imagen combinando señales visuales locales (dHash, dimensiones, paleta)
    con un VLM (si está disponible y seleccionado) o con el motor local de contexto/OCR.
    """
    pillow_sig = _extract_pillow_signals(image_bytes)
    req_provider = (provider or "auto").strip().lower()

    vlm_data = None
    engine_used = "local_context"
    vlm_note = ""

    if image_bytes and req_provider != "local_context":
        try:
            if req_provider == "ollama":
                oll = _check_ollama()
                if oll.get("available"):
                    vlm_data = _call_ollama_vision(image_bytes, oll["selected_model"], oll["url"])
                    if vlm_data:
                        engine_used = f"ollama:{oll['selected_model']}"
                else:
                    vlm_note = "Ollama sin modelo de visión activo; se usó análisis local."
            elif req_provider == "gemini":
                vlm_data = _call_gemini_vision(image_bytes, mime_type=mime_type)
                if vlm_data:
                    engine_used = "gemini"
                else:
                    vlm_note = "GEMINI_API_KEY no configurada o sin respuesta; se usó análisis local."
            elif req_provider == "openai":
                vlm_data = _call_openai_vision(image_bytes, mime_type=mime_type)
                if vlm_data:
                    engine_used = "openai"
                else:
                    vlm_note = "OPENAI_API_KEY no configurada o sin respuesta; se usó análisis local."
            else:
                # Modo auto: intenta Ollama local -> Gemini -> OpenAI -> local_context
                oll = _check_ollama()
                if oll.get("available"):
                    vlm_data = _call_ollama_vision(image_bytes, oll["selected_model"], oll["url"])
                    if vlm_data:
                        engine_used = f"ollama:{oll['selected_model']}"
                if not vlm_data and os.environ.get("GEMINI_API_KEY", "").strip():
                    vlm_data = _call_gemini_vision(image_bytes, mime_type=mime_type)
                    if vlm_data:
                        engine_used = "gemini"
                if not vlm_data and os.environ.get("OPENAI_API_KEY", "").strip():
                    vlm_data = _call_openai_vision(image_bytes, mime_type=mime_type)
                    if vlm_data:
                        engine_used = "openai"
        except Exception as exc:  # noqa: BLE001
            vlm_note = f"Fallback a análisis local tras error en VLM: {exc}"

    ocr_text = ""
    description = ""
    scene = ""
    objects = []
    themes = []
    confidence = "BAJA"

    if vlm_data:
        description = str(vlm_data.get("description") or "").strip()[:280]
        scene = str(vlm_data.get("scene") or "").strip()[:120]
        objects = [str(o).strip() for o in (vlm_data.get("objects") or []) if str(o).strip()][:8]
        ocr_text = str(vlm_data.get("ocr_text") or "").strip()[:600]
        confidence = str(vlm_data.get("confidence") or "ALTA").upper()
        if confidence not in ("ALTA", "MEDIA", "BAJA"):
            confidence = "ALTA"

        raw_themes = [str(t).strip() for t in (vlm_data.get("themes") or []) if str(t).strip()]
        seen_t = set()
        for t in raw_themes:
            key = t.lower()
            if key not in seen_t:
                seen_t.add(key)
                themes.append({
                    "label": t,
                    "confidence": confidence,
                    "evidence": f"Detectado visualmente ({engine_used}): {scene or description[:80]}",
                })
        # Complementar con taxonomía por si hay contexto en caption/alt_text/OCR
        for extra_t in classify_themes_from_text(f"{alt_text} {caption} {ocr_text}", base_confidence="MEDIA"):
            if extra_t["label"].lower() not in seen_t:
                seen_t.add(extra_t["label"].lower())
                themes.append(extra_t)
    else:
        # Motor 100% local: OCR tesseract (si existe) + contexto textual + señales visuales Pillow
        ocr_text = _run_tesseract_ocr(image_bytes) if image_bytes else ""
        combined_text = " ".join(x for x in (alt_text, caption, ocr_text, filename.replace("_", " ").replace("-", " ")) if x)
        themes = classify_themes_from_text(combined_text, base_confidence="MEDIA" if (alt_text or caption or ocr_text) else "BAJA")
        if alt_text:
            description = alt_text[:240]
            confidence = "MEDIA"
        elif caption:
            description = caption[:240]
            confidence = "MEDIA"
        elif ocr_text:
            description = f"Texto visible (OCR): {ocr_text[:200]}"
            confidence = "MEDIA"
        else:
            hints = ", ".join(pillow_sig.get("visual_hints") or [])
            description = f"Imagen analizada localmente ({hints or 'sin texto contextual'}; configura Ollama/Gemini para descripción VLM)."
            confidence = "BAJA"
        scene = ", ".join(pillow_sig.get("visual_hints") or [])

    return {
        "engine": engine_used,
        "description": description,
        "scene": scene,
        "objects": objects,
        "themes": themes,
        "ocr_text": ocr_text,
        "dhash": pillow_sig["dhash"],
        "dimensions": pillow_sig["dimensions"],
        "dominant_colors": pillow_sig["dominant_colors"],
        "confidence": confidence,
        "note": vlm_note,
        "analyzed_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Agregación de intereses visuales y detección de duplicados por caso
# ---------------------------------------------------------------------------
# Para qué sirve: consolida temas visuales, fragmentos OCR y pares de fotos similares en toda la galería del caso.
# Cómo funciona: agrega conteos/evidencias por tema y compara todos los `dhash` por pares buscando Hamming <= `similarity_threshold`.
def summarize_case_vision(media_items: list, similarity_threshold: int = 10) -> dict:
    """
    Consolida los resultados de visión de toda la galería de un caso:
      - `theme_summary`: intereses visuales ordenados por cantidad de fotos de evidencia.
      - `similar_pairs`: pares de fotos visualmente idénticas o casi iguales (dHash Hamming <= threshold).
      - `ocr_snippets`: textos legibles extraídos de imágenes.
      - `stats`: conteos generales de cobertura visual.
    """
    themes_map = {}
    ocr_snippets = []
    hashed_items = []
    analyzed_count = 0

    for m in media_items or []:
        mid = m.get("id")
        vision = m.get("vision") or {}
        dhash = m.get("dhash") or vision.get("dhash") or ""
        ocr = m.get("ocr_text") or vision.get("ocr_text") or ""

        if vision and vision.get("analyzed_at"):
            analyzed_count += 1
        if dhash:
            hashed_items.append((mid, m.get("filename") or f"img_{mid}", dhash, m.get("source_url") or m.get("clean_copy_url") or ""))
        if ocr:
            ocr_snippets.append({
                "media_id": mid,
                "filename": m.get("filename") or "",
                "text": ocr[:300],
            })

        raw_themes = vision.get("themes") or []
        for t in raw_themes:
            label = (t.get("label") if isinstance(t, dict) else str(t)).strip()
            if not label:
                continue
            conf = (t.get("confidence") if isinstance(t, dict) else "MEDIA") or "MEDIA"
            ev = (t.get("evidence") if isinstance(t, dict) else "") or ""
            entry = themes_map.setdefault(
                label,
                {"label": label, "count": 0, "media_ids": [], "confidences": [], "evidences": []},
            )
            if mid not in entry["media_ids"]:
                entry["media_ids"].append(mid)
                entry["count"] += 1
                entry["confidences"].append(conf)
                if ev and len(entry["evidences"]) < 4:
                    entry["evidences"].append(ev)

    theme_summary = []
    for item in themes_map.values():
        alta_n = sum(1 for c in item["confidences"] if c == "ALTA")
        overall = "ALTA" if (alta_n >= 1 and item["count"] >= 2) or alta_n >= 2 else ("MEDIA" if item["count"] >= 1 else "BAJA")
        theme_summary.append({
            "label": item["label"],
            "count": item["count"],
            "confidence": overall,
            "media_ids": item["media_ids"],
            "evidences": item["evidences"],
        })
    theme_summary.sort(key=lambda x: (-x["count"], x["label"]))

    # Detección de duplicados / imágenes casi idénticas por dHash
    similar_pairs = []
    for i in range(len(hashed_items)):
        id_a, name_a, h_a, url_a = hashed_items[i]
        for j in range(i + 1, len(hashed_items)):
            id_b, name_b, h_b, url_b = hashed_items[j]
            dist = hamming_distance(h_a, h_b)
            if dist is not None and dist <= similarity_threshold:
                sim_pct = round((1.0 - dist / 64.0) * 100.0, 1)
                similar_pairs.append({
                    "media_a_id": id_a,
                    "media_b_id": id_b,
                    "filename_a": name_a,
                    "filename_b": name_b,
                    "url_a": url_a,
                    "url_b": url_b,
                    "hamming_distance": dist,
                    "similarity_pct": sim_pct,
                    "relation": "Idéntica" if dist <= 2 else "Muy similar / recomprimida",
                })
    similar_pairs.sort(key=lambda x: x["hamming_distance"])

    return {
        "stats": {
            "total_media": len(media_items or []),
            "analyzed": analyzed_count,
            "pending": max(0, len(media_items or []) - analyzed_count),
            "themes_detected": len(theme_summary),
            "similar_pairs": len(similar_pairs),
            "ocr_hits": len(ocr_snippets),
        },
        "theme_summary": theme_summary,
        "similar_pairs": similar_pairs,
        "ocr_snippets": ocr_snippets,
    }


# ---------------------------------------------------------------------------
# Módulo B y C :: Comparación visual entre perfiles y Pistas de Ubicación (GeoHints)
# ---------------------------------------------------------------------------
# Para qué sirve: detecta fotos compartidas o re-subidas entre dos perfiles distintos (ej. sujeto vs. cuenta amiga).
# Cómo funciona: calcula `hamming_distance` entre sus `dhash` y devuelve pares dentro de `DHASH_HAMMING_THRESHOLD` ordenados por similitud.
def compare_media_between_profiles(
    media_a: list,
    media_b: list,
    hamming_threshold: Optional[int] = None,
) -> list:
    """
    Compara listas de media de dos perfiles (ej. sujeto vs. cuenta amiga) mediante `dHash`.
    Devuelve pares coincidentes con distancia Hamming <= `hamming_threshold` (default `DHASH_HAMMING_THRESHOLD=10`).
    """
    thr = int(
        hamming_threshold
        if hamming_threshold is not None
        else (os.getenv("DHASH_HAMMING_THRESHOLD", "10") or 10)
    )
    matches = []
    seen = set()
    for a in media_a or []:
        ha = (a.get("dhash") or "").strip()
        if not ha:
            continue
        for b in media_b or []:
            hb = (b.get("dhash") or "").strip()
            if not hb:
                continue
            dist = hamming_distance(ha, hb)
            if dist is not None and dist <= thr:
                key = (a.get("id") or a.get("source_url") or ha, b.get("id") or b.get("source_url") or hb)
                if key in seen:
                    continue
                seen.add(key)
                sim_pct = round((1.0 - dist / 64.0) * 100.0, 1)
                matches.append({
                    "media_a_id": a.get("id"),
                    "media_b_id": b.get("id"),
                    "url_a": a.get("source_url") or a.get("url") or "",
                    "url_b": b.get("source_url") or b.get("url") or "",
                    "filename_a": a.get("filename") or "",
                    "filename_b": b.get("filename") or "",
                    "dhash_a": ha,
                    "dhash_b": hb,
                    "hamming_distance": dist,
                    "similarity_pct": sim_pct,
                    "published_a": a.get("published_at") or "",
                    "published_b": b.get("published_at") or "",
                })
    matches.sort(key=lambda x: (x["hamming_distance"], -x["similarity_pct"]))
    return matches


_GEO_HINT_LEXICON = {
    "flora": [
        ("palmeras", ["palmera", "palmeras", "palm tree", "coco", "cocotero", "playa"], 0.65),
        ("pinos", ["pino", "pinos", "pine", "conifera", "bosque frio"], 0.62),
        ("cactus", ["cactus", "desierto", "desert", "matorral", "suculenta"], 0.68),
        ("selva tropical", ["selva", "jungle", "amazonas", "tropical", "helecho", "monstera"], 0.65),
        ("frailejones / páramo", ["frailejon", "paramo", "neblina andina"], 0.78),
    ],
    "architecture": [
        ("balcones coloniales", ["colonial", "balcon", "cartagena", "centro historico", "adoquin"], 0.72),
        ("techos de teja", ["teja", "tejas", "terracota", "colonial"], 0.60),
        ("ladrillo rojo", ["ladrillo", "brick", "bogota", "chapinero"], 0.62),
        ("rascacielos / zona financiera", ["rascacielos", "skyline", "torre", "edificio alto"], 0.58),
    ],
    "climate": [
        ("soleado / luz diurna", ["soleado", "sunny", "verano", "playa", "mediodia"], 0.50),
        ("nublado / lluvia", ["nublado", "lluvia", "neblina", "rain", "fog"], 0.50),
        ("nieve / invierno", ["nieve", "snow", "invierno", "glaciar"], 0.70),
        ("atardecer / noche", ["atardecer", "sunset", "noche", "nocturno"], 0.45),
    ],
    "object": [
        ("transporte público / taxi", ["transmilenio", "sitp", "metro", "taxi amarillo", "bus", "tuk tuk"], 0.68),
        ("señalética o placa", ["placa", "matricula", "calle", "carrera", "avenida", "highway", "street"], 0.65),
        ("moneda o bandera", ["bandera", "flag", "pesos", "cop", "mxn", "eur", "usd"], 0.66),
    ],
}

_GEO_VLM_PROMPT = (
    "Analiza esta imagen con fines de geolocalización OSINT probabilística. "
    "Responde SOLO en JSON válido con las claves: "
    '{"flora": [{"value": "...", "confidence": 0.0-1.0, "evidence_quote": "..."}], '
    '"architecture": [...], "ocr": [...], "climate_light": [...], "distinctive_objects": [...]}. '
    "No inventes datos; si no hay pistas claras deja las listas vacías."
)


# Para qué sirve: normaliza las pistas geográficas visuales (`flora`, `architecture`, `ocr`, `climate`, `object`) devueltas por un VLM.
# Cómo funciona: parsea JSON (o cadena JSON) y mapea cada categoría al esquema `{hint_type, value, confidence, evidence_quote, source}`.
def _parse_geo_vlm_payload(raw) -> list:
    data = raw
    if isinstance(raw, str):
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except Exception:
            return []
    if not isinstance(data, dict):
        return []
    mapping = {
        "flora": "flora",
        "architecture": "architecture",
        "ocr": "ocr",
        "climate_light": "climate",
        "climate": "climate",
        "distinctive_objects": "object",
        "object": "object",
    }
    hints = []
    for k, htype in mapping.items():
        items = data.get(k) or []
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict) and it.get("value"):
                hints.append({
                    "hint_type": htype,
                    "value": str(it["value"]).strip()[:200],
                    "confidence": round(max(0.0, min(1.0, float(it.get("confidence") or 0.5))), 3),
                    "evidence_quote": str(it.get("evidence_quote") or it["value"]).strip()[:300],
                    "source": "vlm",
                })
            elif isinstance(it, str) and it.strip():
                hints.append({
                    "hint_type": htype,
                    "value": it.strip()[:200],
                    "confidence": 0.55,
                    "evidence_quote": f"VLM detectó: {it.strip()[:200]}",
                    "source": "vlm",
                })
    return hints


# Para qué sirve: extrae y agrupa pistas visuales de ubicación (`GeoHints`) desde EXIF GPS, VLM opcional y contexto OCR/caption.
# Cómo funciona: prioriza EXIF (`conf=0.98`), invoca `vlm_client` si existe, registra OCR literal y busca en `_GEO_HINT_LEXICON`.
def extract_geo_hints(
    image_path: Optional[Path] = None,
    vlm_client=None,
    ocr_text: str = "",
    alt_text: str = "",
    caption: str = "",
    exif_gps: Optional[dict] = None,
) -> dict:
    """
    Extrae pistas visuales de ubicación (`GeoHints`) combinando:
      1) EXIF GPS si existe (`source='exif'`, prioridad máxima).
      2) Cliente VLM opcional (`vlm_client` callable `(image_path, prompt) -> dict|str`).
      3) OCR y reglas léxicas locales (`local_context`) sobre texto asociado.
    """
    hints = []

    if isinstance(exif_gps, dict) and exif_gps.get("lat") is not None and exif_gps.get("lon") is not None:
        lat = exif_gps["lat"]
        lon = exif_gps["lon"]
        hints.append({
            "hint_type": "exif",
            "value": f"{lat},{lon}",
            "confidence": 0.98,
            "evidence_quote": f"EXIF GPS: lat={lat}, lon={lon}",
            "source": "exif",
        })

    if callable(vlm_client):
        try:
            raw_vlm = vlm_client(image_path, _GEO_VLM_PROMPT)
            hints.extend(_parse_geo_vlm_payload(raw_vlm))
        except Exception:
            pass

    if ocr_text and ocr_text.strip():
        hints.append({
            "hint_type": "ocr",
            "value": ocr_text.strip()[:160],
            "confidence": 0.70,
            "evidence_quote": f"OCR literal: '{ocr_text.strip()[:180]}'",
            "source": "ocr",
        })

    combined = " ".join(x for x in (ocr_text, alt_text, caption) if x).lower()
    if combined:
        seen_vals = {(h["hint_type"], h["value"].lower()) for h in hints}
        for htype, entries in _GEO_HINT_LEXICON.items():
            for label, kws, conf in entries:
                hit = next((kw for kw in kws if kw in combined), None)
                if hit and (htype, label.lower()) not in seen_vals:
                    seen_vals.add((htype, label.lower()))
                    hints.append({
                        "hint_type": htype,
                        "value": label,
                        "confidence": conf,
                        "evidence_quote": f"Coincidencia contextual '{hit}' en texto/OCR/alt-text",
                        "source": "local_context",
                    })

    # Estructura agrupada compatible con GeoHints
    grouped = {
        "flora": [h for h in hints if h["hint_type"] == "flora"],
        "architecture": [h for h in hints if h["hint_type"] == "architecture"],
        "ocr": [h for h in hints if h["hint_type"] == "ocr"],
        "climate_light": [h for h in hints if h["hint_type"] == "climate"],
        "distinctive_objects": [h for h in hints if h["hint_type"] == "object"],
        "exif": [h for h in hints if h["hint_type"] == "exif"],
        "hints": hints,
    }
    return grouped


# Para qué sirve: fabrica un cliente VLM listo para inyectar en `extract_geo_hints` usando la foto local o URL pública.
# Cómo funciona: devuelve `_client`, que carga los bytes de la imagen (disco o `download_public_image`) y consulta Gemini -> OpenAI -> Ollama.
def build_geo_vlm_client(local_path: Optional[Path] = None, source_url: str = ""):
    """
    Construye un callable `(image_path, prompt) -> dict` que obtiene bytes reales de la foto
    (disco local o URL pública) y consulta Gemini / OpenAI / Ollama si están configurados.
    """
    # Para qué sirve: ejecuta la consulta VLM sobre la imagen cargada para un prompt geográfico específico.
    # Cómo funciona: lee bytes locales o remotos (anti-SSRF) e intenta en cascada Gemini, OpenAI y Ollama.
    def _client(img_path: Optional[Path], prompt: str):
        img_bytes = b""
        mime = "image/jpeg"
        candidate_path = img_path or local_path
        if candidate_path and Path(candidate_path).is_file():
            try:
                img_bytes = Path(candidate_path).read_bytes()
                ext = Path(candidate_path).suffix.lower()
                if ext == ".png":
                    mime = "image/png"
                elif ext == ".webp":
                    mime = "image/webp"
            except Exception:
                img_bytes = b""
        if not img_bytes and source_url and source_url.lower().startswith(("http://", "https://")):
            img_bytes, mime = download_public_image(source_url)
        if not img_bytes:
            return None

        if os.environ.get("GEMINI_API_KEY", "").strip():
            out = _call_gemini_vision(img_bytes, mime_type=mime, prompt=prompt)
            if out:
                return out
        if os.environ.get("OPENAI_API_KEY", "").strip():
            out = _call_openai_vision(img_bytes, mime_type=mime, prompt=prompt)
            if out:
                return out
        ollama_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").strip()
        ollama_model = os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5-vl").strip() or "qwen2.5-vl"
        if ollama_url:
            try:
                return _call_ollama_vision(img_bytes, model=ollama_model, base_url=ollama_url, prompt=prompt)
            except Exception:
                return None
        return None

    return _client
