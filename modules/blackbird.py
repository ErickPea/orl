# -*- coding: utf-8 -*-
"""
Wrapper y parser de Blackbird (cubre sitios que Maigret/Sherlock no tienen).
Además de guardar el log crudo, extrae URLs encontradas para poder cruzarlas
con Maigret y Sherlock en el reporte consolidado.
"""
import re
from pathlib import Path
from urllib.parse import urlparse
from . import utils

# Extrae URLs de líneas positivas de Blackbird (ej: "[+] [GitHub] https://github.com/user" o "FOUND: https://...")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_URL_RE = re.compile(r"https?://[^\s'\"<>)\]]+", re.I)
_SITE_BRACKET_RE = re.compile(r"\[\+\]\s*\[([^\]]+)\]")

# Dominios del propio proyecto Blackbird que pueden aparecer en el banner y NO son perfiles
_IGNORAR_DOMINIOS = {"github.com/p1ngul1n0/blackbird"}


def parse_blackbird_output(text: str) -> list:
    """
    Parsea la salida de texto de Blackbird y devuelve una lista de dicts:
      [{"sitio": str, "url": str, "fuente": "blackbird"}]
    """
    encontrados, vistas = [], set()
    for raw in (text or "").splitlines():
        # Limpiamos códigos de color ANSI que Blackbird suele imprimir
        linea = _ANSI_RE.sub("", raw).strip()
        if not linea:
            continue
        # Solo consideramos líneas que indiquen hallazgo positivo
        es_positivo = (
            linea.startswith("[+]")
            or "FOUND" in linea.upper()
            or "✔" in linea
        )
        if not es_positivo:
            continue
        urls = _URL_RE.findall(linea)
        if not urls:
            continue

        m_site = _SITE_BRACKET_RE.search(linea)
        for url in urls:
            url_limpia = url.rstrip(".,;:")
            if any(ig in url_limpia.lower() for ig in _IGNORAR_DOMINIOS):
                continue
            if url_limpia in vistas:
                continue
            vistas.add(url_limpia)
            sitio = m_site.group(1).strip() if m_site else urlparse(url_limpia).netloc
            encontrados.append({"sitio": sitio, "url": url_limpia, "fuente": "blackbird"})
    return encontrados


def run_blackbird(username: str, out_dir: Path, email: str = None) -> dict:
    """Corre Blackbird por username (y correo si se da); guarda el log en out_dir y devuelve hallazgos."""
    base = utils.tool_command("blackbird")
    if not base:
        utils.warn("Blackbird no instalado; se omite.")
        return {"error": "blackbird no instalado", "found": []}

    cmd = base + ["-u", username]
    if email:
        cmd += ["-e", email]

    utils.info(f"Ejecutando Blackbird para '{username}'...")
    rc, out, e = utils.run_command(cmd, timeout=600)
    log = out_dir / f"blackbird_{username}.txt"
    log.write_text(out + "\n" + e, encoding="utf-8")

    found = parse_blackbird_output(out)
    if rc == 0:
        utils.ok(f"Blackbird terminó ({len(found)} perfiles detectados en log).")
    else:
        utils.err(f"Blackbird devolvió código {rc}: {e[:200]}")

    return {"rc": rc, "stdout": out, "log": str(log), "found": found}
