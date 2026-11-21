# -*- coding: utf-8 -*-
"""
Reportes consolidados y motor de precisión:
  - Parsea resultados de Maigret (CSV + JSON simple con metadatos), Sherlock y Blackbird.
  - Normaliza URLs para deduplicar hallazgos entre herramientas.
  - Asigna nivel de confianza (ALTA / MEDIA / BAJA) mediante validación cruzada y metadatos.
  - Extrae señales de correlación de identidad (nombres, ubicaciones, bios).
  - Genera CSV consolidado, HTML con insignias de confianza y PDF (fpdf2).
"""
import csv
import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from . import utils
from .blackbird import parse_blackbird_output

try:
    from fpdf import FPDF
    FPDF_OK = True
except ImportError:
    FPDF_OK = False

CATEGORIAS = {
    "Redes sociales": ["facebook", "instagram", "twitter", "x.com", "tiktok", "linkedin",
                       "youtube", "snapchat", "telegram", "pinterest", "reddit", "threads"],
    "Empleo y profesional": ["linkedin", "computrabajo", "elempleo", "indeed", "glassdoor",
                             "bumeran", "infojobs", "github", "gitlab", "behance", "dribbble"],
    "Tecnología y foros": ["github", "gitlab", "stackoverflow", "hackernews", "medium",
                           "dev.to", "kaggle", "codepen", "bitbucket", "npm", "pypi"],
}

# Se ocultan del reporte consolidado (los archivos crudos de cada herramienta no se borran)
SENSIBLES = ["porn", "xxx", "adult", "dating", "casino", "bet", "gambl", "crypto"]

# Dominios o patrones que suelen generar falsos positivos con un solo hit sin metadatos
_SITIOS_RUIDOSOS = {"pastebin", "gravatar", "gravatar.com", "about.me"}

# Orden de prioridad al ordenar resultados por confianza
_ORDEN_CONFIANZA = {"ALTA": 0, "MEDIA": 1, "BAJA": 2}

# Regex para URLs en la salida de texto plano de Sherlock
_URL_LINE_RE = re.compile(r"(https?://[^\s'\"<>)\]]+)", re.I)


# ---------------------------------------------------------------------------
# Normalización y filtrado
# ---------------------------------------------------------------------------
def _normalize_url(url: str) -> str:
    """
    Normaliza una URL para deduplicar hallazgos equivalentes entre herramientas
    (ej. 'https://www.github.com/User/' y 'https://github.com/User').
    """
    raw = (url or "").strip().rstrip("/")
    if not raw:
        return ""
    p = urlparse(raw)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    # En rutas de perfiles conservamos path en minúsculas para evitar duplicados por mayúsculas
    path = p.path.rstrip("/").lower()
    return f"{host}{path}"


def _es_sensible(sitio: str, url: str, extra_excludes: list = None) -> bool:
    """Indica si el sitio/URL coincide con palabras excluidas (sensibles o exclusiones extra)."""
    texto = f"{sitio} {url}".lower()
    palabras = list(SENSIBLES) + [x.lower() for x in (extra_excludes or [])]
    return any(p in texto for p in palabras)


# ---------------------------------------------------------------------------
# Parsers por herramienta
# ---------------------------------------------------------------------------
def _load_maigret_json_metadata(out_dir: Path) -> dict:
    """
    Carga metadatos enriquecidos desde los JSON de Maigret (`report_*_simple.json`).
    Devuelve un mapa {url_normalizada: {"fullname": ..., "bio": ..., "location": ..., "avatar": ...}}.
    """
    meta_por_url = {}
    for jf in out_dir.rglob("report_*.json"):
        try:
            data = json.loads(jf.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue

        for _site_name, entry in data.items():
            if not isinstance(entry, dict):
                continue
            url = (entry.get("url_user") or "").strip()
            status = entry.get("status") or {}
            # En formato simple de Maigret, `status.ids` guarda los datos extraídos del perfil
            ids = status.get("ids") if isinstance(status, dict) else {}
            if not isinstance(ids, dict):
                ids = {}
            if not url:
                continue

            extra = {}
            for clave_out, posibles in (
                ("fullname", ("fullname", "name", "display_name", "username_real")),
                ("bio", ("bio", "about", "description")),
                ("location", ("location", "country", "city")),
                ("avatar", ("image", "avatar", "avatar_url")),
            ):
                for k in posibles:
                    val = ids.get(k)
                    if val and str(val).strip():
                        extra[clave_out] = str(val).strip()[:200]
                        break
            if extra:
                meta_por_url[_normalize_url(url)] = extra
    return meta_por_url


def parse_maigret_found(out_dir: Path, ocultar_sensibles: bool = True,
                        extra_excludes: list = None) -> list:
    """
    Lee los CSV de Maigret (`report_*.csv`) y los enriquece con metadatos JSON si existen.
    Devuelve lista de dicts: [{sitio, url, fuente, http_status, metadata}].
    """
    meta_json = _load_maigret_json_metadata(out_dir)
    filas, vistas = [], set()
    for f in out_dir.rglob("report_*.csv"):
        try:
            with f.open(encoding="utf-8", errors="ignore", newline="") as fh:
                for r in csv.DictReader(fh):
                    if (r.get("exists") or "").strip().lower() != "claimed":
                        continue
                    url = (r.get("url_user") or "").strip()
                    sitio = (r.get("name") or urlparse(url).netloc).strip()
                    if not url:
                        continue
                    norm = _normalize_url(url)
                    if norm in vistas:
                        continue
                    if ocultar_sensibles and _es_sensible(sitio, url, extra_excludes):
                        continue
                    vistas.add(norm)
                    http_status = (r.get("http_status") or "").strip()
                    filas.append({
                        "sitio": sitio,
                        "url": url,
                        "fuente": "maigret",
                        "http_status": http_status,
                        "metadata": meta_json.get(norm, {}),
                    })
        except OSError:
            continue
    return filas


def parse_sherlock_found(out_dir: Path, ocultar_sensibles: bool = True,
                         extra_excludes: list = None) -> list:
    """
    Lee las salidas de Sherlock (`sherlock_*.txt`) donde cada línea positiva contiene una URL.
    Devuelve lista de dicts: [{sitio, url, fuente, http_status, metadata}].
    """
    filas, vistas = [], set()
    for f in out_dir.rglob("sherlock_*.txt"):
        try:
            texto = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for raw in texto.splitlines():
            linea = raw.strip()
            if not linea or linea.lower().startswith("total websites"):
                continue
            m = _URL_LINE_RE.search(linea)
            if not m:
                continue
            url = m.group(1).rstrip(".,;:")
            norm = _normalize_url(url)
            if not norm or norm in vistas:
                continue
            # Sherlock a veces imprime "[+] Sitio: https://..." o directamente la URL
            sitio = urlparse(url).netloc.replace("www.", "")
            if ": http" in linea:
                prefijo = linea.split(": http", 1)[0].replace("[+]", "").strip()
                if prefijo:
                    sitio = prefijo
            if ocultar_sensibles and _es_sensible(sitio, url, extra_excludes):
                continue
            vistas.add(norm)
            filas.append({
                "sitio": sitio,
                "url": url,
                "fuente": "sherlock",
                "http_status": "",
                "metadata": {},
            })
    return filas


def parse_blackbird_found(out_dir: Path, ocultar_sensibles: bool = True,
                          extra_excludes: list = None) -> list:
    """
    Lee las salidas guardadas de Blackbird (`blackbird_*.txt`) usando el parser del módulo.
    """
    filas, vistas = [], set()
    for f in out_dir.rglob("blackbird_*.txt"):
        try:
            texto = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for item in parse_blackbird_output(texto):
            url = item["url"]
            sitio = item["sitio"]
            norm = _normalize_url(url)
            if not norm or norm in vistas:
                continue
            if ocultar_sensibles and _es_sensible(sitio, url, extra_excludes):
                continue
            vistas.add(norm)
            filas.append({
                "sitio": sitio,
                "url": url,
                "fuente": "blackbird",
                "http_status": "",
                "metadata": {},
            })
    return filas


# ---------------------------------------------------------------------------
# Consolidación, validación cruzada y scoring de confianza
# ---------------------------------------------------------------------------
def _calcular_confianza(fuentes: list, http_status: str, metadata: dict, sitio: str) -> str:
    """
    Calcula el nivel de confianza del perfil encontrado:
      - ALTA:  confirmado por >= 2 herramientas distintas, O tiene metadatos reales extraídos
               (nombre/bio/ubicación/avatar) con estado HTTP 200 o sin error.
      - MEDIA: 1 herramienta con HTTP 200 (o sin código anómalo) en sitio no ruidoso.
      - BAJA:  1 herramienta con código HTTP distinto de 200 (ej. 301/302/403/429) o sitio ruidoso.
    """
    codigo = str(http_status or "").strip()
    es_http_ok = (codigo == "" or codigo == "200")
    es_ruidoso = any(r in (sitio or "").lower() for r in _SITIOS_RUIDOSOS)

    if len(fuentes) >= 2:
        return "ALTA"
    if metadata and es_http_ok:
        return "ALTA"
    if not es_http_ok or es_ruidoso:
        return "BAJA"
    return "MEDIA"


def consolidate_profiles(out_dir: Path, ocultar_sensibles: bool = True,
                         extra_excludes: list = None) -> list:
    """
    Unifica los hallazgos de Maigret, Sherlock y Blackbird en una sola lista deduplicada
    y ordenada por nivel de confianza (`ALTA` -> `MEDIA` -> `BAJA`).

    Cada elemento tiene:
      {sitio, url, confianza, fuentes, http_status, metadata_resumen, metadata}
    """
    todos = (
        parse_maigret_found(out_dir, ocultar_sensibles, extra_excludes)
        + parse_sherlock_found(out_dir, ocultar_sensibles, extra_excludes)
        + parse_blackbird_found(out_dir, ocultar_sensibles, extra_excludes)
    )

    agrupados = {}
    for item in todos:
        norm = _normalize_url(item["url"])
        if not norm:
            continue
        if norm not in agrupados:
            agrupados[norm] = {
                "sitio": item["sitio"],
                "url": item["url"],
                "fuentes": [item["fuente"]],
                "http_status": item.get("http_status", ""),
                "metadata": dict(item.get("metadata") or {}),
            }
        else:
            reg = agrupados[norm]
            if item["fuente"] not in reg["fuentes"]:
                reg["fuentes"].append(item["fuente"])
            # Si Maigret aporta código HTTP o metadatos que otra herramienta no tenía, los conservamos
            if not reg["http_status"] and item.get("http_status"):
                reg["http_status"] = item["http_status"]
            if item.get("metadata"):
                reg["metadata"].update(item["metadata"])

    consolidados = []
    for reg in agrupados.values():
        conf = _calcular_confianza(
            reg["fuentes"], reg["http_status"], reg["metadata"], reg["sitio"]
        )
        # Resumen corto de metadatos para CSV / HTML / PDF
        partes_meta = []
        for k in ("fullname", "location", "bio"):
            if reg["metadata"].get(k):
                partes_meta.append(f"{k}={reg['metadata'][k]}")
        consolidados.append({
            "sitio": reg["sitio"],
            "url": reg["url"],
            "confianza": conf,
            "fuentes": "+".join(sorted(reg["fuentes"])),
            "http_status": reg["http_status"] or "—",
            "metadata_resumen": " | ".join(partes_meta),
            "metadata": reg["metadata"],
        })

    consolidados.sort(key=lambda r: (_ORDEN_CONFIANZA.get(r["confianza"], 9), r["sitio"].lower()))
    return consolidados


def extract_identity_signals(profiles: list) -> dict:
    """
    Agrega señales de correlación de identidad detectadas en metadatos de perfiles
    (nombres, ubicaciones, bios y avatares) para ayudar a verificar si las cuentas
    pertenecen realmente a la misma persona.
    """
    nombres, ubicaciones, bios, avatares = set(), set(), set(), set()
    for p in profiles:
        m = p.get("metadata") or {}
        if m.get("fullname"):
            nombres.add(m["fullname"])
        if m.get("location"):
            ubicaciones.add(m["location"])
        if m.get("bio"):
            bios.add(m["bio"][:140])
        if m.get("avatar"):
            avatares.add(m["avatar"])

    signals = {}
    if nombres:
        signals["Nombres detectados"] = sorted(nombres)
    if ubicaciones:
        signals["Ubicaciones detectadas"] = sorted(ubicaciones)
    if bios:
        signals["Biografías / descripciones"] = sorted(bios)[:8]
    if avatares:
        signals["URLs de avatar"] = sorted(avatares)[:8]
    return signals


def build_insights(found: list) -> dict:
    """
    Agrupa los perfiles consolidados por categoría incluyendo etiqueta de confianza y fuente(s),
    y añade al principio un resumen cuantitativo de confianza.
    """
    ins = {}
    if found:
        alta = sum(1 for r in found if r.get("confianza") == "ALTA")
        media = sum(1 for r in found if r.get("confianza") == "MEDIA")
        baja = sum(1 for r in found if r.get("confianza") == "BAJA")
        ins["Resumen de precisión"] = [
            f"Total perfiles únicos (deduplicados): {len(found)}",
            f"Confianza ALTA (validación cruzada o con metadatos): {alta}",
            f"Confianza MEDIA (1 herramienta, HTTP OK): {media}",
            f"Confianza BAJA (revisar manualmente): {baja}",
        ]

    for k in CATEGORIAS:
        ins[k] = []
    ins["Otros sitios"] = []

    for r in found:
        texto = (r["sitio"] + r["url"]).lower()
        conf = r.get("confianza", "MEDIA")
        fuentes = r.get("fuentes", "maigret")
        meta = f" — {r['metadata_resumen']}" if r.get("metadata_resumen") else ""
        # Formato compatible con _item_html(): "[CONFIANZA] Sitio (fuentes): https://... — meta"
        linea = f"[{conf}] {r['sitio']} ({fuentes}): {r['url']}{meta}"

        puesto = False
        for cat, palabras in CATEGORIAS.items():
            if any(p in texto for p in palabras):
                ins[cat].append(linea)
                puesto = True
        if not puesto:
            ins["Otros sitios"].append(linea)

    # Si hubo metadatos de identidad en Maigret JSON, se muestran como sección aparte
    signals = extract_identity_signals(found)
    for k, v in signals.items():
        ins[f"Correlación · {k}"] = v

    return ins


def rows_for_csv(found: list) -> list:
    """Convierte los perfiles consolidados a filas planas aptas para CSV (sin dicts anidados)."""
    return [
        {
            "confianza": r.get("confianza", ""),
            "sitio": r.get("sitio", ""),
            "url": r.get("url", ""),
            "fuentes": r.get("fuentes", ""),
            "http_status": r.get("http_status", ""),
            "metadatos": r.get("metadata_resumen", ""),
        }
        for r in found
    ]


# ---------------------------------------------------------------------------
# Escritura de CSV, PDF y HTML
# ---------------------------------------------------------------------------
def write_csv(out_dir: Path, rows: list, filename: str = "resumen_consolidado.csv"):
    """
    Escribe un archivo CSV en out_dir a partir de una lista de dicts.
    Acepta `filename` opcional para poder guardar CSVs separados (ej. perfiles vs correos)
    sin sobrescribir resumen_consolidado.csv.
    """
    if not rows:
        return
    path = out_dir / filename
    campos = sorted({k for r in rows for k in r})
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=campos)
        w.writeheader()
        w.writerows(rows)
    utils.ok(f"CSV guardado -> {path}")


def _latin1(texto) -> str:
    """Helvetica de fpdf2 solo soporta latin-1; se reemplaza lo demás para no romper el PDF."""
    return str(texto).encode("latin-1", "replace").decode("latin-1")


def write_pdf(out_dir: Path, target: str, sections: dict, gallery_items: Optional[list] = None):
    """Escribe reporte_final.pdf con las secciones {titulo: dict|list|str} y miniaturas locales opcionales."""
    if not FPDF_OK:
        utils.warn("fpdf2 no instalado; se omite el PDF.")
        return
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _latin1(f"OSINT-Auto - Reporte: {target}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 8, f"Generado: {datetime.now():%Y-%m-%d %H:%M:%S}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    def linea(txt):
        pdf.multi_cell(0, 5, _latin1(txt), new_x="LMARGIN", new_y="NEXT")

    for nombre, contenido in sections.items():
        if not contenido:
            continue
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, _latin1(nombre), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        if isinstance(contenido, dict):
            for k, v in contenido.items():
                linea(f"- {k}: {v}"[:300])
        elif isinstance(contenido, list):
            for item in contenido or ["(sin hallazgos)"]:
                linea(f"- {item}"[:300])
        else:
            linea(str(contenido)[:1000])
        pdf.ln(3)

    # Miniaturas embebidas para fotos locales existentes en disco (Fase 3)
    local_thumbs = [g for g in (gallery_items or []) if g.get("local_path") and Path(str(g["local_path"])).is_file()]
    if local_thumbs:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, _latin1("Miniaturas de Galeria (Evidencia Local)"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        for g in local_thumbs[:12]:
            lp = Path(str(g["local_path"]))
            try:
                if pdf.get_y() > 245:
                    pdf.add_page()
                y0 = pdf.get_y()
                pdf.image(str(lp), x=12, y=y0, w=24)
                pdf.set_xy(40, y0)
                loc = g.get("possible_location") or "No determinada"
                conf_pct = int(round(float(g.get("confidence") or 0) * 100))
                pdf.multi_cell(
                    0,
                    4.5,
                    _latin1(
                        f"{g.get('filename', 'foto')} | {g.get('author', 'subject')} | {(g.get('published_at') or '')[:10]}\n"
                        f"Ubicacion: {loc} ({conf_pct}% | {g.get('source') or '-'})\n"
                        f"{(g.get('evidence_quote') or '')[:140]}"
                    ),
                    new_x="LMARGIN",
                    new_y="NEXT",
                )
                pdf.set_y(max(pdf.get_y(), y0 + 26))
            except Exception:
                continue

    path = out_dir / "reporte_final.pdf"
    pdf.output(str(path))
    utils.ok(f"PDF -> {path}")


HTML_TEMPLATE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>OSINT-Auto - {target}</title>
<style>
 body{{font-family:system-ui,Arial,sans-serif;max-width:980px;margin:2em auto;padding:1em;background:#0f172a;color:#e2e8f0}}
 h1{{color:#38bdf8}} h2{{color:#fbbf24;border-bottom:1px solid #334155;padding-bottom:.3em}}
 .card{{background:#1e293b;border-radius:8px;padding:1em;margin:1em 0}}
 li{{margin:.35em 0;word-break:break-all}} a{{color:#7dd3fc}} small{{color:#94a3b8}}
 .badge{{display:inline-block;padding:1px 6px;border-radius:4px;font-size:.78em;font-weight:700;margin-right:6px}}
 .badge-alta{{background:#166534;color:#dcfce7}}
 .badge-media{{background:#854d0e;color:#fef9c3}}
 .badge-baja{{background:#7f1d1d;color:#fee2e2}}
</style></head><body>
<h1>Reporte OSINT-Auto</h1>
<p><strong>Objetivo:</strong> {target}<br><small>Generado: {ts}</small></p>
{sections}
</body></html>"""


def _formatear_badge(texto_escapado: str) -> str:
    """Reemplaza prefijos [ALTA]/[MEDIA]/[BAJA] por insignias visuales en HTML."""
    for nivel, clase in (("ALTA", "badge-alta"), ("MEDIA", "badge-media"), ("BAJA", "badge-baja")):
        pref = f"[{nivel}] "
        if texto_escapado.startswith(pref):
            return f'<span class="badge {clase}">{nivel}</span>' + texto_escapado[len(pref):]
    return texto_escapado


def _item_html(item: str) -> str:
    """Convierte 'etiqueta: https://...' en enlace clicable (con badge de confianza si aplica)."""
    texto = str(item)
    if ": http" in texto:
        etiqueta, resto = texto.split(": ", 1)
        # Si después de la URL vienen metadatos (" — fullname=..."), los separamos del href
        if " — " in resto:
            url, cola = resto.split(" — ", 1)
            cola_html = f" <small>— {html.escape(cola)}</small>"
        else:
            url, cola_html = resto, ""
        u = html.escape(url, quote=True)
        etiqueta_html = _formatear_badge(html.escape(etiqueta))
        return f'<li>{etiqueta_html}: <a href="{u}" target="_blank" rel="noopener">{html.escape(url)}</a>{cola_html}</li>'
    return f"<li>{_formatear_badge(html.escape(texto))}</li>"


def _normalizar_items_html(items) -> list:
    """
    Normaliza el valor de una sección de insights a una lista de strings.
    Evita que un string (ej. un correo) se itere carácter por carácter en HTML
    y permite mostrar diccionarios como pares 'clave: valor'.
    """
    if isinstance(items, dict):
        salida = []
        for k, v in items.items():
            if isinstance(v, (list, tuple, set)):
                valor = ", ".join(str(x) for x in v) or "(sin datos)"
            else:
                valor = str(v)
            salida.append(f"{k}: {valor}")
        return salida
    if isinstance(items, (list, tuple, set)):
        return [str(x) for x in items]
    return [str(items)]


def write_html(out_dir: Path, target: str, insights: dict, extra: dict = None):
    """Escribe resumen.html. Todo se escapa porque el contenido viene de fuentes externas."""
    bloques = []
    for cat, items in insights.items():
        if items:
            normalizados = _normalizar_items_html(items)
            lis = "".join(_item_html(i) for i in normalizados)
            bloques.append(f'<div class="card"><h2>{html.escape(cat)}</h2><ul>{lis}</ul></div>')
    for nombre, data in (extra or {}).items():
        if isinstance(data, (list, dict)):
            normalizados = _normalizar_items_html(data)
            lis = "".join(_item_html(i) for i in normalizados)
            bloques.append(f'<div class="card"><h2>{html.escape(nombre)}</h2><ul>{lis}</ul></div>')
        else:
            bloques.append(f'<div class="card"><h2>{html.escape(nombre)}</h2>'
                           f'<pre>{html.escape(str(data)[:2000])}</pre></div>')
    cuerpo = HTML_TEMPLATE.format(
        target=html.escape(target), ts=f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        sections="\n".join(bloques) or "<p>Sin hallazgos destacados.</p>")
    path = out_dir / "resumen.html"
    path.write_text(cuerpo, encoding="utf-8")
    utils.ok(f"HTML resumen -> {path}")
