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
        # Para qué sirve: stub sin colores cuando `colorama` no está instalado.
        # Cómo funciona: intercepta cualquier atributo de color/estilo devolviendo cadena vacía.
        def __getattr__(self, _):
            return ""
    Fore = Style = _Vacio()


# ---------------------------------------------------------------------------
# Salida de consola
# ---------------------------------------------------------------------------
# Para qué sirve: imprime un mensaje informativo en consola CLI con prefijo `[i]`.
# Cómo funciona: formatea el texto usando `Fore.CYAN` y `Style.RESET_ALL`.
def info(msg):
    print(f"{Fore.CYAN}[i]{Style.RESET_ALL} {msg}")


# Para qué sirve: imprime un mensaje de éxito en consola CLI con prefijo `[+]`.
# Cómo funciona: formatea el texto usando `Fore.GREEN` y `Style.RESET_ALL`.
def ok(msg):
    print(f"{Fore.GREEN}[+]{Style.RESET_ALL} {msg}")


# Para qué sirve: imprime una advertencia en consola CLI con prefijo `[!]`.
# Cómo funciona: formatea el texto usando `Fore.YELLOW` y `Style.RESET_ALL`.
def warn(msg):
    print(f"{Fore.YELLOW}[!]{Style.RESET_ALL} {msg}")


# Para qué sirve: imprime un mensaje de error en consola CLI con prefijo `[-]`.
# Cómo funciona: formatea el texto usando `Fore.RED` y `Style.RESET_ALL`.
def err(msg):
    print(f"{Fore.RED}[-]{Style.RESET_ALL} {msg}")


# Para qué sirve: muestra un encabezado visual destacado para secciones del CLI.
# Cómo funciona: rodea el título con líneas `=` en color magenta.
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


# Para qué sirve: resuelve el comando base ejecutable para lanzar una herramienta OSINT externa.
# Cómo funciona: revisa `BLACKBIRD_PATH` para Blackbird y usa `shutil.which` sobre `TOOLS[name]` para el resto.
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


# Para qué sirve: comprueba si una herramienta CLI externa está lista para ejecutarse.
# Cómo funciona: evalúa si `tool_command(name)` devuelve un comando distinto de `None`.
def tool_available(name: str) -> bool:
    """Indica si la herramienta se puede ejecutar."""
    return tool_command(name) is not None


# Para qué sirve: audita qué binarios OSINT están disponibles en el sistema e informa los faltantes.
# Cómo funciona: itera `TOOLS`, consulta `tool_available` e imprime estado + comando de instalación sugerido.
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


# Para qué sirve: ejecuta comandos externos de forma segura capturando salida sin lanzar excepciones.
# Cómo funciona: envuelve `subprocess.run` con timeout UTF-8 y mapea `FileNotFoundError`/`TimeoutExpired` a códigos `127`/`124`.
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


# Para qué sirve: crea un directorio único y seguro para almacenar los artefactos de un reporte o corrida.
# Cómo funciona: sanea `target` (máx. 60 chars alfanuméricos/`-_.`), antepone fecha-hora y crea el directorio en `OUTPUT_ROOT`.
def make_output_dir(target: str) -> Path:
    """Crea output/<fecha_hora>_<objetivo>/ y la devuelve (el objetivo se sanea para el sistema de archivos)."""
    seguro = "".join(c if c.isalnum() or c in "-_." else "_" for c in target)[:60]
    sello = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    carpeta = OUTPUT_ROOT / f"{sello}_{seguro}"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


# Para qué sirve: enumera las carpetas de reportes existentes en `output/`.
# Cómo funciona: filtra subdirectorios directos de `OUTPUT_ROOT` y los ordena alfabéticamente en orden inverso (más reciente primero).
def list_reports() -> list:
    """Lista las carpetas de resultados, de la más nueva a la más vieja."""
    if not OUTPUT_ROOT.exists():
        return []
    dirs = [d for d in OUTPUT_ROOT.iterdir() if d.is_dir()]
    return sorted(dirs, key=lambda d: d.name, reverse=True)


# ---------------------------------------------------------------------------
# Entrada de usuario
# ---------------------------------------------------------------------------
# Para qué sirve: solicita una entrada de texto en consola con valor por defecto opcional.
# Cómo funciona: muestra `prompt` estilizado vía `input()` y retorna el texto ingresado o `default`.
def ask(prompt: str, default: str = "") -> str:
    sufijo = f" [{default}]" if default else ""
    raw = input(f"{Fore.BLUE}?> {Style.RESET_ALL}{prompt}{sufijo}: ").strip()
    return raw or default


# Para qué sirve: solicita una confirmación booleana Sí/No al usuario en consola.
# Cómo funciona: interpreta `s/si/sí/y/yes` como `True` y retorna `default` si se presiona Enter vacío.
def ask_yes_no(prompt: str, default: bool = False) -> bool:
    d = "S/n" if default else "s/N"
    r = input(f"{Fore.BLUE}?> {Style.RESET_ALL}{prompt} ({d}): ").strip().lower()
    if not r:
        return default
    return r in ("s", "si", "sí", "y", "yes")


# Para qué sirve: solicita al usuario un número entero validado dentro de una lista permitida.
# Cómo funciona: repite `ask` en bucle hasta recibir un entero válido contenido en `options`.
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
