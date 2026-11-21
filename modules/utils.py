# -*- coding: utf-8 -*-
"""Utilidades compartidas: colores, detección de herramientas, carpetas de salida e input."""
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=True)
except ImportError:
    # Sin colorama el programa debe seguir funcionando, solo sin colores
    class _Vacio:
        def __getattr__(self, _):
            return ""
    Fore = Style = _Vacio()


# ---------------------------------------------------------------------------
# Salida de consola
# ---------------------------------------------------------------------------
def info(msg):
    print(f"{Fore.CYAN}[i]{Style.RESET_ALL} {msg}")


def ok(msg):
    print(f"{Fore.GREEN}[+]{Style.RESET_ALL} {msg}")


def warn(msg):
    print(f"{Fore.YELLOW}[!]{Style.RESET_ALL} {msg}")


def err(msg):
    print(f"{Fore.RED}[-]{Style.RESET_ALL} {msg}")


def title(msg):
    linea = "=" * (len(msg) + 6)
    print(f"\n{Fore.MAGENTA}{linea}\n   {msg}\n{linea}{Style.RESET_ALL}\n")


# ---------------------------------------------------------------------------
# Herramientas externas
# ---------------------------------------------------------------------------
# nombre -> (ejecutable, cómo instalarla)
TOOLS = {
    "maigret":   ("maigret",   "pipx install maigret"),
    "sherlock":  ("sherlock",  "pipx install sherlock-project"),
    "holehe":    ("holehe",    "pipx install holehe"),
    "ignorant":  ("ignorant",  "pipx install ignorant"),
    "blackbird": ("blackbird", "git clone https://github.com/p1ngul1n0/blackbird y definir BLACKBIRD_PATH"),
}


def tool_command(name: str):
    """
    Devuelve el comando base (lista) para lanzar la herramienta, o None si no está.
    Blackbird se instala clonando el repo, por eso se acepta la variable de entorno
    BLACKBIRD_PATH apuntando a blackbird.py además de un ejecutable en PATH.
    """
    if name == "blackbird":
        ruta = os.environ.get("BLACKBIRD_PATH", "")
        if ruta and Path(ruta).is_file():
            return [sys.executable, ruta]
    exe = TOOLS[name][0]
    return [exe] if shutil.which(exe) else None


def tool_available(name: str) -> bool:
    """Indica si la herramienta se puede ejecutar."""
    return tool_command(name) is not None


def check_tools() -> dict:
    """Revisa todas las herramientas, avisa las que faltan y devuelve {nombre: bool}."""
    estado = {}
    info("Comprobando herramientas instaladas...")
    for name in TOOLS:
        disponible = tool_available(name)
        estado[name] = disponible
        if disponible:
            ok(f"{name:10s} -> disponible")
        else:
            warn(f"{name:10s} -> NO instalada ({TOOLS[name][1]})")
    return estado


def run_command(cmd: list, cwd: str = None, timeout: int = 900) -> tuple:
    """
    Ejecuta un comando y devuelve (returncode, stdout, stderr).
    Nunca lanza excepción: el que llama decide qué hacer con el código de error.
    """
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"Comando no encontrado: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "Tiempo máximo alcanzado"
    except Exception as e:  # noqa: BLE001 - se reporta tal cual al usuario
        return 1, "", str(e)


# ---------------------------------------------------------------------------
# Carpetas de salida
# ---------------------------------------------------------------------------
OUTPUT_ROOT = Path(__file__).resolve().parent.parent / "output"


def make_output_dir(target: str) -> Path:
    """Crea output/<fecha_hora>_<objetivo>/ y la devuelve (el objetivo se sanea para el sistema de archivos)."""
    seguro = "".join(c if c.isalnum() or c in "-_." else "_" for c in target)[:60]
    sello = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    carpeta = OUTPUT_ROOT / f"{sello}_{seguro}"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def list_reports() -> list:
    """Lista las carpetas de resultados, de la más nueva a la más vieja."""
    if not OUTPUT_ROOT.exists():
        return []
    dirs = [d for d in OUTPUT_ROOT.iterdir() if d.is_dir()]
    return sorted(dirs, key=lambda d: d.name, reverse=True)


# ---------------------------------------------------------------------------
# Entrada de usuario
# ---------------------------------------------------------------------------
def ask(prompt: str, default: str = "") -> str:
    sufijo = f" [{default}]" if default else ""
    raw = input(f"{Fore.BLUE}?> {Style.RESET_ALL}{prompt}{sufijo}: ").strip()
    return raw or default


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    d = "S/n" if default else "s/N"
    r = input(f"{Fore.BLUE}?> {Style.RESET_ALL}{prompt} ({d}): ").strip().lower()
    if not r:
        return default
    return r in ("s", "si", "sí", "y", "yes")


def ask_int(prompt: str, default: int, options: list) -> int:
    """Pide un entero que esté dentro de `options`; repite hasta que sea válido."""
    while True:
        r = ask(f"{prompt} {options}", str(default))
        try:
            v = int(r)
            if v in options:
                return v
        except ValueError:
            pass
        warn(f"Valor inválido. Opciones: {options}")
