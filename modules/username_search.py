# -*- coding: utf-8 -*-
"""Búsqueda por nombre de usuario con Maigret (principal) y Sherlock (complemento)."""
from pathlib import Path
from . import utils


# Para qué sirve: construye la línea de comandos de Maigret con salida múltiple para correlación posterior.
# Cómo funciona: solicita HTML/PDF/CSV y `--json simple`, añadiendo tags de país/redes sociales y tope de sitios.
def _cmd_maigret(username: str, out_dir: Path, country: str = None,
                 top: int = None, social_only: bool = False) -> list:
    """
    Arma el comando de Maigret.
    Además de HTML/PDF/CSV se solicita `--json simple` para extraer metadatos de perfil
    (nombre completo, bio, ubicación, avatar) que permiten correlacionar identidades
    y calcular el nivel de confianza en `modules/reports.py`.
    Si `social_only=True`, añade el tag `social` para enfocarse en redes sociales.
    """
    cmd = utils.tool_command("maigret") + [
        username, "--html", "--pdf", "--csv", "--json", "simple",
        "--folderoutput", str(out_dir),
        "--timeout", "30", "--no-progressbar",
    ]
    tags = []
    if country:
        tags.append(country)
    if social_only:
        tags.append("social")
    if tags:
        cmd += ["--tags", ",".join(tags)]
    if top:
        cmd += ["--top-sites", str(top)]
    return cmd


# Para qué sirve: construye el comando de Sherlock apuntando al archivo de salida del caso.
# Cómo funciona: usa `--print-found` y `--output sherlock_<username>.txt` con timeout de 20 s por sitio.
def _cmd_sherlock(username: str, out_dir: Path) -> list:
    """Arma el comando de Sherlock guardando la salida en la carpeta del caso."""
    return utils.tool_command("sherlock") + [
        username, "--output", str(out_dir / f"sherlock_{username}.txt"),
        "--timeout", "20", "--print-found",
    ]


# Para qué sirve: ejecuta la búsqueda de perfiles por username combinando Maigret y Sherlock.
# Cómo funciona: verifica qué binarios están disponibles, corre cada subproceso en `out_dir` y retorna sus códigos/salidas.
def run_username_search(username: str, out_dir: Path, country: str = None,
                        top: int = None, exclude: list = None,
                        social_only: bool = False) -> dict:
    """
    Ejecuta Maigret y Sherlock si están instalados.
    Recibe el usuario, la carpeta de salida, un código de país opcional, el tope de sitios,
    `exclude` opcional y `social_only` para priorizar redes sociales en Maigret.
    """
    resultado = {
        "maigret": None,
        "sherlock": None,
        "username": username,
        "excluded": list(exclude) if exclude else [],
        "social_only": social_only,
    }

    if utils.tool_available("maigret"):
        etiqueta_tags = ",".join([t for t in (country, "social" if social_only else None) if t])
        utils.info(f"Ejecutando Maigret para '{username}'" + (f" (tags={etiqueta_tags})" if etiqueta_tags else ""))
        rc, out, e = utils.run_command(
            _cmd_maigret(username, out_dir, country, top, social_only=social_only),
            timeout=1200,
        )
        if rc == 0:
            utils.ok("Maigret terminó bien.")
        else:
            utils.err(f"Maigret devolvió código {rc}. {e[:300]}")
        resultado["maigret"] = {"rc": rc, "stdout": out, "stderr": e}
    else:
        utils.warn("Maigret no instalado. Se omite.")

    if utils.tool_available("sherlock"):
        utils.info(f"Ejecutando Sherlock para '{username}'")
        rc, out, e = utils.run_command(_cmd_sherlock(username, out_dir), timeout=600)
        if rc == 0:
            utils.ok("Sherlock terminó bien.")
        else:
            utils.err(f"Sherlock devolvió código {rc}. {e[:300]}")
        resultado["sherlock"] = {"rc": rc, "stdout": out, "stderr": e}
    else:
        utils.warn("Sherlock no instalado. Se omite.")

    return resultado
