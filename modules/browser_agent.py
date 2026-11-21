# -*- coding: utf-8 -*-
"""
Gestor del Agente de Navegación Supervisada (Playwright Headed Browser) para OSINT-Auto.

Responsabilidades:
  1. Abrir una ventana real y visible de Chromium (`headless=False`) con perfil aislado por
     plataforma en `data/browser_profiles/<platform>` para que el usuario inicie sesión
     manualmente (OSINT-Auto NUNCA pide ni guarda contraseñas).
  2. Ejecutar investigaciones supervisadas con máquina de estados y control en vivo:
       `running` <-> `paused` / `paused_checkpoint` -> `completed` / `cancelled` / `error`.
  3. Detectar muros de login, CAPTCHAs o checkpoints de seguridad y PAUSAR inmediatamente
     para intervención humana (nunca intenta evadir controles de seguridad).
  4. Guardar todos los hallazgos primero en `browser_staged_items` (bandeja de revisión)
     para que el usuario revise, filtre o elimine elementos antes de consolidarlos en el caso.
"""
import importlib.util
import os
import random
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from modules import storage
from modules.connectors.common import add_interactant, add_interaction, empty_bundle, extract_hashtags, extract_mentions
from modules.relational_language import analyze_single_text_relational
from modules.text_analysis import analyze_sentiment

ROOT_DIR = Path(__file__).resolve().parent.parent
BROWSER_PROFILES_DIR = ROOT_DIR / "data" / "browser_profiles"

PLATFORM_LOGIN_URLS = {
    "instagram": "https://www.instagram.com/accounts/login/",
    "facebook": "https://www.facebook.com/login/",
    "reddit": "https://www.reddit.com/login/",
    "bluesky": "https://bsky.app/",
}


# Para qué sirve: verifica si el entorno virtual tiene instalado Playwright antes de lanzar el agente.
# Cómo funciona: comprueba la existencia del módulo `playwright` mediante `importlib.util.find_spec`.
def playwright_available() -> bool:
    """Indica si el paquete `playwright` está disponible en el entorno."""
    return importlib.util.find_spec("playwright") is not None


# Para qué sirve: obtiene y asegura la carpeta de perfil persistente para una plataforma.
# Cómo funciona: normaliza el nombre de plataforma y crea `data/browser_profiles/<plat>` si no existe.
def get_profile_dir(platform: str) -> Path:
    """Devuelve y crea el directorio de perfil persistente del navegador para `platform`."""
    plat = (platform or "generic").strip().lower()
    p = BROWSER_PROFILES_DIR / plat
    p.mkdir(parents=True, exist_ok=True)
    return p


# Para qué sirve: localiza el navegador real instalado para abrir sesiones de login sin flags de automatización.
# Cómo funciona: revisa rutas estándar de Windows/Linux priorizando Microsoft Edge sobre Google Chrome.
def _find_desktop_browser_exe() -> Optional[Path]:
    """Devuelve el ejecutable de Microsoft Edge (prioritario) o Chrome para login nativo sin flags de automatización."""
    edge_candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path("/usr/bin/microsoft-edge"),
    ]
    for p in edge_candidates:
        if p.exists():
            return p
    chrome_candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path("/usr/bin/google-chrome"),
    ]
    for p in chrome_candidates:
        if p.exists():
            return p
    return None


# Para qué sirve: selecciona el canal de navegador real (`msedge`/`chrome`) que usará Playwright.
# Cómo funciona: lee `OSINT_BROWSER_CHANNEL` si está definida o comprueba qué ejecutables existen en el sistema.
def _detect_browser_channel() -> Optional[str]:
    """
    Detecta un navegador de escritorio real instalado (Chrome o Edge) para evitar
    problemas de renderizado de imágenes/CAPTCHA en el Chromium de pruebas.
    """
    env_ch = (os.getenv("OSINT_BROWSER_CHANNEL") or "").strip().lower()
    if env_ch in ("chrome", "msedge"):
        return env_ch
    if env_ch == "chromium":
        return None
    edge_candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path("/usr/bin/microsoft-edge"),
    ]
    if any(p.exists() for p in edge_candidates):
        return "msedge"
    chrome_candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path("/usr/bin/google-chrome"),
    ]
    if any(p.exists() for p in chrome_candidates):
        return "chrome"
    return None


# Para qué sirve: inicia un contexto persistente de navegador reutilizando cookies y sesión guardada.
# Cómo funciona: lanza Edge/Chrome vía `launch_persistent_context` con `user_data_dir` y hace fallback a Chromium.
def _launch_persistent_context(playwright_obj, profile_dir: Path, headless: bool = False):
    """Lanza el contexto persistente usando Chrome/Edge instalado si existe, o Chromium."""
    channel = _detect_browser_channel()
    kwargs = {
        "user_data_dir": str(profile_dir),
        "headless": bool(headless),
        "locale": "es-ES",
    }
    if headless:
        kwargs["viewport"] = {"width": 1280, "height": 860}
    else:
        kwargs["no_viewport"] = True
        kwargs["args"] = ["--start-maximized"]

    if channel:
        try:
            return playwright_obj.chromium.launch_persistent_context(channel=channel, **kwargs)
        except Exception:
            pass
    return playwright_obj.chromium.launch_persistent_context(**kwargs)


# Para qué sirve: informa a la UI/API el estado actual de la sesión de navegador de una plataforma.
# Cómo funciona: verifica Playwright, canal activo, presencia de archivos en el perfil y tamaño de caché en MB.
def session_status(platform: str) -> dict:
    """Resume disponibilidad de Playwright, sesión guardada y tamaño de caché para `platform`."""
    plat = (platform or "").strip().lower()
    pdir = BROWSER_PROFILES_DIR / plat
    has_cookies = pdir.is_dir() and any(pdir.iterdir())
    cache_stats = get_browser_cache_stats(platform=plat) if has_cookies else {"cache_size_mb": 0.0, "total_size_mb": 0.0}
    return {
        "platform": plat,
        "playwright_ready": playwright_available(),
        "browser_channel": _detect_browser_channel() or "chromium",
        "has_saved_session": bool(has_cookies),
        "cache_size_mb": cache_stats.get("cache_size_mb", 0.0),
        "profile_size_mb": cache_stats.get("total_size_mb", 0.0),
        "profile_dir": str(pdir),
        "login_url": PLATFORM_LOGIN_URLS.get(plat, ""),
    }


# Para qué sirve: cierra la sesión guardada de una plataforma eliminando su perfil local completo.
# Cómo funciona: borra recursivamente `data/browser_profiles/<platform>` con `shutil.rmtree`.
def clear_platform_session(platform: str) -> bool:
    """Elimina el perfil/cookies locales de Chromium para una plataforma."""
    plat = (platform or "").strip().lower()
    pdir = BROWSER_PROFILES_DIR / plat
    if pdir.is_dir():
        shutil.rmtree(pdir, ignore_errors=True)
        return True
    return False


_SAFE_CACHE_DIR_NAMES = {
    "Cache",
    "Cache_Data",
    "Code Cache",
    "GPUCache",
    "GrShaderCache",
    "ShaderCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "component_crx_cache",
    "Edge Entity Extraction",
    "EdgeLanguageDetectionModel",
    "Crashpad",
    "OptimizationHints",
    "WasmTtsEngine",
    "MediaCache",
    "Media Cache",
}

_SAFE_CACHE_SUBPATHS = (
    ("Service Worker", "ScriptCache"),
    ("Service Worker", "CacheStorage"),
    ("Shared Dictionary", "cache"),
)

_PROTECTED_SESSION_NAMES = {
    "Cookies",
    "Cookies-journal",
    "Network",
    "Local Storage",
    "Session Storage",
    "IndexedDB",
    "Extensions",
    "Extension State",
    "Local State",
    "Preferences",
    "Secure Preferences",
    "Login Data",
    "Web Data",
}


# Para qué sirve: calcula cuánto espacio ocupa en disco una carpeta del perfil del navegador.
# Cómo funciona: suma `st_size` de todos los archivos encontrados con `rglob("*")` tolerando archivos bloqueados.
def _safe_dir_size_bytes(path: Path) -> int:
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


# Para qué sirve: localiza solo las subcarpetas de caché eliminables sin tocar datos de sesión.
# Cómo funciona: recorre el perfil recursivamente recolectando `_SAFE_CACHE_DIR_NAMES`/`_SAFE_CACHE_SUBPATHS` y omitiendo `_PROTECTED_SESSION_NAMES`.
def _collect_cache_targets(pdir: Path) -> list:
    """
    Recopila subcarpetas de caché seguras de eliminar dentro de un perfil de navegador,
    protegiendo explícitamente cookies, Local/Session Storage, IndexedDB y preferencias.
    """
    if not pdir.is_dir():
        return []
    targets = []
    seen = set()

    # Para qué sirve: explorador recursivo interno que clasifica cada subdirectorio del perfil.
    # Cómo funciona: agrega cachés seguras al vuelo, corta la bajada en carpetas protegidas y evita duplicados por ruta resuelta.
    def _walk(cur: Path, rel_parts: tuple) -> None:
        try:
            children = sorted(cur.iterdir(), key=lambda x: x.name)
        except OSError:
            return
        for child in children:
            if not child.is_dir():
                continue
            name = child.name
            sub_parts = rel_parts + (name,)
            # Coincidencia por subruta compuesta (p. ej. Service Worker/ScriptCache)
            matched_subpath = any(
                len(sub_parts) >= len(sp) and sub_parts[-len(sp):] == sp
                for sp in _SAFE_CACHE_SUBPATHS
            )
            if name in _SAFE_CACHE_DIR_NAMES or matched_subpath:
                res = str(child.resolve())
                if res not in seen:
                    seen.add(res)
                    targets.append(child)
                continue
            if name in _PROTECTED_SESSION_NAMES:
                continue
            _walk(child, sub_parts)

    _walk(pdir, ())
    return targets


# Para qué sirve: mide el tamaño total del perfil y cuánto espacio corresponde a caché limpiable.
# Cómo funciona: evalúa uno o todos los perfiles sumando `_safe_dir_size_bytes` por cada objetivo de `_collect_cache_targets`.
def get_browser_cache_stats(platform: Optional[str] = None, profiles_dir: Optional[Path] = None) -> dict:
    """Devuelve tamaño total y tamaño de cachés limpiables para uno o todos los perfiles."""
    base = profiles_dir or BROWSER_PROFILES_DIR
    plat = (platform or "").strip().lower()
    if plat:
        pdirs = [base / plat] if (base / plat).is_dir() else []
    else:
        pdirs = sorted([d for d in base.iterdir() if d.is_dir()]) if base.is_dir() else []

    total_bytes = 0
    cache_bytes = 0
    targets_count = 0
    per_platform = {}
    for d in pdirs:
        p_total = _safe_dir_size_bytes(d)
        t_list = _collect_cache_targets(d)
        p_cache = sum(_safe_dir_size_bytes(t) for t in t_list)
        total_bytes += p_total
        cache_bytes += p_cache
        targets_count += len(t_list)
        per_platform[d.name] = {
            "total_size_mb": round(p_total / (1024 * 1024), 2),
            "cache_size_mb": round(p_cache / (1024 * 1024), 2),
            "targets_count": len(t_list),
        }

    return {
        "total_size_mb": round(total_bytes / (1024 * 1024), 2),
        "cache_size_mb": round(cache_bytes / (1024 * 1024), 2),
        "total_bytes": total_bytes,
        "cache_bytes": cache_bytes,
        "targets_count": targets_count,
        "platforms": per_platform,
    }


# Para qué sirve: libera espacio eliminando cachés pesadas de Edge/Chromium sin borrar cookies ni sesiones.
# Cómo funciona: bloquea si hay login/agente activo, verifica rutas dentro de `base` y borra (o simula si `dry_run=True`).
def clean_browser_cache(
    platform: Optional[str] = None,
    dry_run: bool = False,
    profiles_dir: Optional[Path] = None,
) -> dict:
    """
    Limpia selectivamente cachés pesados de Chromium/Edge (`Cache`, `Code Cache`,
    `Service Worker/ScriptCache`, `Edge Entity Extraction`, etc.) sin borrar cookies ni sesiones.
    """
    base = (profiles_dir or BROWSER_PROFILES_DIR).resolve()
    plat = (platform or "").strip().lower()
    if plat and plat not in PLATFORM_LOGIN_URLS and profiles_dir is None:
        raise ValueError(f"Plataforma no soportada: {platform}")

    if profiles_dir is None:
        with _CTRL_LOCK:
            if plat:
                if plat in _LOGIN_SESSIONS:
                    raise RuntimeError(f"Cierra la ventana de login de {plat} antes de limpiar su caché.")
            elif _LOGIN_SESSIONS:
                raise RuntimeError("Cierra las ventanas de login abiertas antes de limpiar la caché.")
            if _ACTIVE_CONTROLS:
                raise RuntimeError("Hay una investigación de navegador activa; detenla o espérala antes de limpiar la caché.")

    pdirs = [base / plat] if plat else (sorted([d for d in base.iterdir() if d.is_dir()]) if base.is_dir() else [])
    targets = []
    for d in pdirs:
        if d.is_dir():
            for t in _collect_cache_targets(d):
                rt = t.resolve()
                if base in rt.parents:
                    targets.append((d.name, rt, _safe_dir_size_bytes(rt)))

    total_bytes = sum(sz for _, _, sz in targets)
    if dry_run:
        return {
            "dry_run": True,
            "platform": plat or "all",
            "would_delete_count": len(targets),
            "would_free_mb": round(total_bytes / (1024 * 1024), 2),
            "targets": [f"{p}:{t.name}" for p, t, _ in targets[:60]],
            "sessions_preserved": True,
        }

    freed_bytes = 0
    deleted = []
    for p_name, rt, sz in targets:
        shutil.rmtree(rt, ignore_errors=True)
        if not rt.exists():
            freed_bytes += sz
            deleted.append(f"{p_name}:{rt.name}")
        else:
            # Si algún archivo quedó bloqueado, contabilizar lo efectivamente liberado
            rem = _safe_dir_size_bytes(rt)
            freed_bytes += max(0, sz - rem)
            deleted.append(f"{p_name}:{rt.name}")

    return {
        "dry_run": False,
        "platform": plat or "all",
        "deleted_count": len(deleted),
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "targets": deleted[:60],
        "sessions_preserved": True,
    }


# ---------------------------------------------------------------------------
# Controladores de ejecución en vivo (Pause / Resume / Cancel)
# ---------------------------------------------------------------------------
class RunControl:
    """Primitivas thread-safe para pausar, reanudar y cancelar un agente en ejecución."""

    # Para qué sirve: inicializa las banderas de control thread-safe para una corrida del agente.
    # Cómo funciona: crea `pause_event` activo (`set()`) y `cancel_event` apagado ligados al `run_id`.
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.pause_event = threading.Event()
        self.pause_event.set()  # set() = activo; clear() = pausado
        self.cancel_event = threading.Event()

    # Para qué sirve: detiene temporalmente la navegación para revisión o intervención manual del usuario.
    # Cómo funciona: limpia `pause_event` y persiste estado `paused` o `paused_checkpoint` en SQLite.
    def pause(self, reason: str = "Pausado por el usuario", checkpoint: bool = False):
        self.pause_event.clear()
        st = "paused_checkpoint" if checkpoint else "paused"
        storage.update_browser_agent_run(self.run_id, status=st, step=reason, log=reason)

    # Para qué sirve: reanuda una corrida pausada tras resolver un checkpoint o solicitud del usuario.
    # Cómo funciona: vuelve a activar `pause_event` y registra estado `running` en SQLite.
    def resume(self):
        self.pause_event.set()
        storage.update_browser_agent_run(
            self.run_id,
            status="running",
            step="Reanudando navegación...",
            log="Ejecución reanudada por el usuario.",
        )

    # Para qué sirve: solicita la detención definitiva de la investigación en curso.
    # Cómo funciona: activa `cancel_event`, libera `pause_event` por si estaba bloqueado y marca `cancelled` en BD.
    def cancel(self):
        self.cancel_event.set()
        self.pause_event.set()  # desbloquear si estaba pausado
        storage.update_browser_agent_run(
            self.run_id,
            status="cancelled",
            step="Cancelado por el usuario",
            log="Cancelación solicitada por el usuario.",
            finished=True,
        )

    # Para qué sirve: guarda el punto de avance para poder retomar el perfil principal tras un friend-scan.
    # Cómo funciona: persiste `cursor_url` en la columna `resume_cursor` del run en SQLite.
    def save_cursor(self, cursor_url: str):
        """Persiste la última URL/cursor procesado para poder reanudar tras una pasada secundaria."""
        storage.update_browser_agent_run(self.run_id, resume_cursor=str(cursor_url or ""))

    # Para qué sirve: mantiene el hilo trabajador en espera mientras el usuario o un checkpoint pausen el agente.
    # Cómo funciona: sondea `pause_event` cada 0.25 s y devuelve `False` inmediatamente si se canceló.
    def wait_if_paused(self) -> bool:
        """Bloquea mientras esté pausado. Devuelve False si se canceló."""
        while not self.pause_event.is_set():
            if self.cancel_event.is_set():
                return False
            time.sleep(0.25)
        return not self.cancel_event.is_set()

    # Para qué sirve: introduce pausas humanas aleatorias entre acciones DOM sin perder capacidad de respuesta.
    # Cómo funciona: divide la espera `random.uniform` en micro-pasos de 0.25 s verificando pausa/cancelación.
    def human_delay(self, min_sec: float = 1.4, max_sec: float = 3.0) -> bool:
        """Espera humana respetuosa con cancelación y pausa inmediatas."""
        total = random.uniform(min_sec, max_sec)
        steps = max(1, int(total / 0.25))
        for _ in range(steps):
            if self.cancel_event.is_set():
                return False
            if not self.wait_if_paused():
                return False
            time.sleep(0.25)
        return not self.cancel_event.is_set()


_CTRL_LOCK = threading.Lock()
_ACTIVE_CONTROLS: dict = {}  # run_id -> RunControl
_LOGIN_SESSIONS: dict = {}   # platform -> {"thread": Thread, "stop": Event}


# Para qué sirve: obtiene el controlador en memoria de una corrida activa para pausarla o cancelarla desde la API.
# Cómo funciona: consulta `_ACTIVE_CONTROLS[run_id]` bajo `_CTRL_LOCK`.
def get_run_control(run_id: str) -> Optional[RunControl]:
    """Devuelve el controlador activo (`RunControl`) de una ejecución en curso."""
    with _CTRL_LOCK:
        return _ACTIVE_CONTROLS.get(run_id)


# Para qué sirve: abre una ventana interactiva del navegador para que el usuario inicie sesión manualmente en la red social.
# Cómo funciona: cierra sesiones de login previas de esa red y lanza Edge/Chrome nativo (sin automatización) o Playwright sobre el perfil persistente.
def open_interactive_login_window(platform: str) -> dict:
    """
    Abre una ventana real de Microsoft Edge (sin flags de automatización) usando
    el perfil persistente de `data/browser_profiles/<platform>` para que el usuario
    inicie sesión manualmente con sus credenciales reales.
    """
    plat = (platform or "").strip().lower()
    login_url = PLATFORM_LOGIN_URLS.get(plat)
    if not login_url:
        raise ValueError(f"Plataforma no soportada para sesión de navegador: {platform}")

    profile_dir = get_profile_dir(plat)
    desktop_exe = _find_desktop_browser_exe()

    # Si ya había una ventana de login abierta para esta plataforma, la cerramos primero
    with _CTRL_LOCK:
        prev = _LOGIN_SESSIONS.pop(plat, None)
        if prev:
            if prev.get("stop"):
                prev["stop"].set()
            if prev.get("proc"):
                try:
                    prev["proc"].terminate()
                    prev["proc"].wait(timeout=3)
                except Exception:
                    pass

        if desktop_exe is not None:
            proc = subprocess.Popen(
                [
                    str(desktop_exe),
                    f"--user-data-dir={profile_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--start-maximized",
                    login_url,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _LOGIN_SESSIONS[plat] = {"proc": proc}
            return {
                "platform": plat,
                "status": "waiting_login",
                "login_url": login_url,
                "browser": desktop_exe.name,
                "message": (
                    f"Se abrió {desktop_exe.name} en modo normal (sin automatización). "
                    "Inicia sesión manualmente en la ventana y ciérrala completamente antes de iniciar la investigación."
                ),
            }

    if not playwright_available():
        raise RuntimeError("Playwright no está instalado en el entorno virtual.")

    stop_ev = threading.Event()

    # Para qué sirve: hilo de respaldo con Playwright cuando no hay Edge/Chrome nativo para abrir el login manual.
    # Cómo funciona: abre el perfil persistente en `login_url` y espera hasta cerrar las pestañas o recibir `stop_ev`.
    def _login_worker():
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            ctx = _launch_persistent_context(p, profile_dir, headless=False)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            while not stop_ev.is_set():
                try:
                    pages = ctx.pages
                    if not pages:
                        break
                    pages[-1].wait_for_timeout(300)
                except Exception:
                    break
            try:
                ctx.close()
            except Exception:
                pass

    with _CTRL_LOCK:
        t = threading.Thread(target=_login_worker, daemon=True)
        _LOGIN_SESSIONS[plat] = {"thread": t, "stop": stop_ev}
        t.start()

    return {
        "platform": plat,
        "status": "waiting_login",
        "login_url": login_url,
        "message": "Se abrió el navegador. Inicia sesión manualmente en la ventana y luego ciérrala antes de iniciar la investigación.",
    }


# Para qué sirve: inicia en segundo plano una investigación supervisada en navegador con control en vivo y staging.
# Cómo funciona: valida caso/límites, cierra ventanas de login abiertas, crea `RunControl` y lanza `_worker` en un hilo daemon.
def start_browser_investigation(
    case_id: str,
    platform: str,
    target_url: str,
    max_posts: int = 12,
    max_comments_per_post: int = 15,
    delay_seconds: float = 2.0,
    headless: bool = False,
) -> dict:
    """Lanza un trabajo supervisado de navegación en segundo plano y devuelve su registro."""
    if not playwright_available():
        raise RuntimeError("Playwright no está instalado en el entorno virtual.")
    if not storage.case_exists(case_id):
        raise ValueError(f"Caso no encontrado: {case_id}")

    from modules.browser_adapters import get_browser_adapter

    plat = (platform or "").strip().lower()
    adapter = get_browser_adapter(plat)
    limits = {
        "max_posts": max(1, min(int(max_posts), 40)),
        "max_comments_per_post": max(1, min(int(max_comments_per_post), 40)),
        "delay_seconds": max(1.0, min(float(delay_seconds), 8.0)),
        "headless": bool(headless),
    }

    run_id = uuid.uuid4().hex[:12]
    run_rec = storage.create_browser_agent_run(
        case_id=case_id,
        platform=plat,
        target_url=target_url,
        limits=limits,
        run_id=run_id,
    )
    ctrl = RunControl(run_id)
    with _CTRL_LOCK:
        _ACTIVE_CONTROLS[run_id] = ctrl

    # Cerrar si quedó abierta la ventana de login de esa misma plataforma para liberar el lock del perfil
    with _CTRL_LOCK:
        prev_login = _LOGIN_SESSIONS.pop(plat, None)
        if prev_login:
            if prev_login.get("stop"):
                prev_login["stop"].set()
            if prev_login.get("proc"):
                try:
                    if prev_login["proc"].poll() is None:
                        prev_login["proc"].terminate()
                        prev_login["proc"].wait(timeout=3)
                except Exception:
                    pass
            time.sleep(0.8)

    # Para qué sirve: ejecuta el adaptador Playwright de forma asíncrona y actualiza estado/errores en SQLite.
    # Cómo funciona: abre el perfil persistente, pasa `_emit_staged` y `_check_security_gate` al adaptador y limpia `_ACTIVE_CONTROLS` al terminar.
    def _worker():
        from playwright.sync_api import sync_playwright

        profile_dir = get_profile_dir(plat)
        stats = {"profiles": 0, "posts": 0, "comments": 0, "media": 0, "relational_hits": 0}
        try:
            with sync_playwright() as p:
                ctx = _launch_persistent_context(p, profile_dir, headless=limits["headless"])
                page = ctx.pages[0] if ctx.pages else ctx.new_page()

                # Para qué sirve: recibe cada hallazgo del adaptador, lo enriquece en vivo y lo guarda en staging.
                # Cómo funciona: evalúa sentimiento y señal relacional preliminar si hay texto y lo inserta en `browser_staged_items`.
                def _emit_staged(kind: str, author: str, target: str, content: str, pub_at: str, src_url: str, payload: dict):
                    # Enriquecimiento en vivo (sentimiento + señal relacional preliminar)
                    enriched = dict(payload or {})
                    if content:
                        enriched["sentiment"] = analyze_sentiment(content)["label"]
                        rel_hits = analyze_single_text_relational(
                            text=content,
                            source_handle=author,
                            target_handle=target,
                            platform=plat,
                        )
                        if rel_hits:
                            enriched["relational_preview"] = [
                                {"category": r["category"], "expression": r["matched_expression"], "epistemic": r["epistemic_type"]}
                                for r in rel_hits
                            ]
                            stats["relational_hits"] += len(rel_hits)

                    storage.add_browser_staged_item(
                        run_id=run_id,
                        case_id=case_id,
                        item_kind=kind,
                        platform=plat,
                        author_handle=author,
                        target_handle=target,
                        content=content,
                        published_at=pub_at,
                        source_url=src_url,
                        payload=enriched,
                    )
                    if kind == "profile":
                        stats["profiles"] += 1
                    elif kind == "post":
                        stats["posts"] += 1
                    elif kind == "comment":
                        stats["comments"] += 1
                    elif kind == "media":
                        stats["media"] += 1

                # Para qué sirve: frena al agente ante muros de login, 2FA o CAPTCHAs sin intentar evadirlos.
                # Cómo funciona: llama a `adapter.detect_security_or_login_gate` y pausa con `checkpoint=True` esperando acción humana.
                def _check_security_gate() -> bool:
                    """Si hay CAPTCHA, checkpoint o login wall, pausa para acción humana (NO evade)."""
                    gate = adapter.detect_security_or_login_gate(page)
                    if gate:
                        ctrl.pause(
                            reason=f"Pausado: la plataforma mostró '{gate}'. Resuélvelo manualmente en la ventana y pulsa Reanudar (o Cancela).",
                            checkpoint=True,
                        )
                        return ctrl.wait_if_paused()
                    return True

                adapter.run(
                    page=page,
                    target_url=target_url,
                    limits=limits,
                    ctrl=ctrl,
                    emit=_emit_staged,
                    check_gate=_check_security_gate,
                    update_status=lambda step, pct, log: storage.update_browser_agent_run(
                        run_id, step=step, progress=pct, log=log, stats=stats
                    ),
                )

                try:
                    ctx.close()
                except Exception:
                    pass

            if ctrl.cancel_event.is_set():
                storage.update_browser_agent_run(
                    run_id,
                    status="cancelled",
                    step="Investigación cancelada",
                    progress=100,
                    stats=stats,
                    log="Proceso detenido por el usuario.",
                    finished=True,
                )
            else:
                storage.update_browser_agent_run(
                    run_id,
                    status="completed",
                    step="Recopilación finalizada · Lista para revisión",
                    progress=100,
                    stats=stats,
                    log=(
                        f"Completado: {stats['profiles']} perfil, {stats['posts']} posts, "
                        f"{stats['comments']} comentarios, {stats['media']} media."
                    ),
                    finished=True,
                )
        except Exception as exc:  # noqa: BLE001
            storage.update_browser_agent_run(
                run_id,
                status="error",
                step="Error durante la navegación",
                progress=100,
                stats=stats,
                error=str(exc),
                log=f"Error: {exc}",
                finished=True,
            )
        finally:
            with _CTRL_LOCK:
                _ACTIVE_CONTROLS.pop(run_id, None)

    threading.Thread(target=_worker, daemon=True).start()
    return run_rec


# Para qué sirve: consolida los ítems aprobados por el analista desde la bandeja temporal (`staging`) hacia el caso en SQLite.
# Cómo funciona: filtra `selected == 1`, construye un `ConnectorBundle` normalizado con interactuantes/aristas, ingesta y dispara Relacional, NLP y SNA.
def commit_staged_run_to_case(
    run_id: str,
    run_relational: bool = True,
    run_nlp: bool = True,
    run_sna: bool = True,
) -> Optional[dict]:
    """
    Toma únicamente los ítems seleccionados (`selected=1`) de `browser_staged_items`,
    los convierte en un `ConnectorBundle` normalizado y los integra al caso
    (evidencias, snapshots, NLP, Lenguaje Relacional y SNA).
    """
    run = storage.get_browser_agent_run(run_id)
    if not run:
        return None
    case_id = run["case_id"]
    platform = run["platform"]
    selected = [it for it in (run.get("items") or []) if int(it.get("selected", 1)) == 1]

    bundle = empty_bundle(platform, run.get("target_url") or platform, access_method="browser_supervised")
    subject_handle = ""

    for idx, it in enumerate(selected, start=1):
        kind = it.get("item_kind")
        pl = it.get("payload") or {}
        if kind == "profile":
            subject_handle = (pl.get("username") or it.get("author_handle") or "").strip().lstrip("@").lower()
            bundle["handle"] = subject_handle or bundle["handle"]
            bundle["profile_links"] = pl.get("public_links") or []
            bundle["profile"] = {
                "site": pl.get("site") or platform.title(),
                "url": it.get("source_url") or run["target_url"],
                "username": subject_handle,
                "display_name": pl.get("display_name") or subject_handle,
                "bio": it.get("content") or pl.get("bio") or "",
                "location": pl.get("location") or "",
                "avatar_url": pl.get("avatar_url") or "",
                "website": pl.get("website") or "",
                "sources": f"BrowserAgent-{platform}",
                "osint_confidence": "ALTA",
                "metadata": {"source": "browser_supervised", "run_id": run_id},
            }
        elif kind == "post":
            txt = it.get("content") or ""
            author = it.get("author_handle") or subject_handle or "sujeto"
            mentions = extract_mentions(txt)
            ext_id = pl.get("external_id") or f"ba_post_{run_id}_{idx}"
            bundle["posts"].append({
                "external_id": ext_id,
                "platform": platform,
                "author": author,
                "content": txt,
                "published_at": it.get("published_at") or "",
                "url": it.get("source_url") or run["target_url"],
                "published_location": pl.get("published_location") or "",
                "media_urls": pl.get("media_urls") or [],
                "mentions": mentions,
                "hashtags": extract_hashtags(txt),
                "metadata": {"source": "browser_supervised", "run_id": run_id},
            })
            for m in mentions:
                add_interaction(bundle["interactions"], author, m, "mention", weight=1.0, metadata={"platform": platform})
        elif kind == "comment":
            author = it.get("author_handle") or "usuario"
            target = it.get("target_handle") or subject_handle or "sujeto"
            txt = it.get("content") or ""
            author_dn = pl.get("author_display_name") or author
            author_url = pl.get("author_profile_url") or ""
            add_interactant(
                bundle["interactants"],
                platform=platform,
                handle=author,
                display_name=author_dn,
                profile_url=author_url,
                seen_at=it.get("published_at") or "",
            )
            bundle["comments"].append({
                "external_id": pl.get("external_id") or f"ba_com_{run_id}_{idx}",
                "post_external_id": pl.get("post_external_id") or "",
                "author_handle": author,
                "author_display_name": author_dn,
                "author_profile_url": author_url,
                "target_handle": target,
                "content": txt,
                "published_at": it.get("published_at") or "",
                "metadata": {
                    "platform": platform,
                    "url": it.get("source_url") or run["target_url"],
                    "source": "browser_supervised",
                    "author_display_name": author_dn,
                    "author_profile_url": author_url,
                    "is_reply": bool(pl.get("is_reply")),
                    "parent_comment_id": pl.get("parent_comment_id") or "",
                },
            })
            if author and target and author != target:
                add_interaction(bundle["interactions"], author, target, "comment", weight=1.6, metadata={"platform": platform})
            for m in extract_mentions(txt):
                add_interaction(bundle["interactions"], author, m, "mention", weight=1.0, metadata={"platform": platform})
        elif kind == "media":
            surl = it.get("source_url") or ""
            if surl:
                bundle["media_items"].append({
                    "filename": pl.get("filename") or f"ba_{platform}_{idx}.jpg",
                    "media_type": pl.get("media_type") or "image",
                    "source_url": surl,
                    "alt_text": it.get("content") or pl.get("alt_text") or "",
                    "post_external_id": pl.get("post_external_id") or "",
                    "published_at": it.get("published_at") or "",
                    "metadata": {"platform": platform, "source": "browser_supervised"},
                })

    ingest_res = storage.ingest_connector_bundle(case_id=case_id, bundle=bundle)
    storage.update_browser_agent_run(
        run_id,
        committed=1,
        log=f"Integrados {len(selected)} elementos validados al caso {case_id}.",
    )

    rel_stats = None
    if run_relational:
        from modules.account_investigator import run_case_relational_analysis
        rel_out = run_case_relational_analysis(case_id, provider="local")
        rel_stats = (rel_out or {}).get("stats")

    nlp_counts = None
    if run_nlp:
        from modules.text_analysis import analyze_case_nlp
        corpus = storage.get_case_corpus_for_nlp(case_id)
        if corpus:
            nlp_res = analyze_case_nlp(corpus, provider="local")
            storage.save_case_nlp_analysis(case_id, nlp_res)
            nlp_counts = nlp_res.get("counts")

    sna_stats = None
    if run_sna:
        sna_res = storage.compute_and_save_case_sna(case_id, record_run=False)
        sna_stats = (sna_res or {}).get("stats")

    return {
        "run_id": run_id,
        "case_id": case_id,
        "committed_items": len(selected),
        "ingest": ingest_res,
        "relational": rel_stats,
        "nlp": nlp_counts,
        "sna": sna_stats,
    }


# ---------------------------------------------------------------------------
# Módulo B :: Máquina de estados Friend Scan + Retorno al Perfil Principal
# ---------------------------------------------------------------------------
class FriendScanStateMachine:
    """
    Orquestador de cola FIFO para investigar cuentas amigas candidatas:
      IDLE -> SCANNING_PRIMARY -> QUEUING_FRIENDS -> SCANNING_FRIEND(n)
           -> (PAUSED_CHECKPOINT) -> RESUMING -> SCANNING_PRIMARY_RESUME -> DONE
    """

    # Para qué sirve: inicializa la máquina de estados y eventos de control para la pasada secundaria de amigos.
    # Cómo funciona: fija `state="IDLE"`, cola vacía, cursor de reanudación y eventos `pause_event`/`cancel_event`.
    def __init__(self, case_id: str, parent_run_id: str = "", platform: str = "instagram"):
        self.case_id = case_id
        self.parent_run_id = parent_run_id
        self.platform = (platform or "instagram").strip().lower()
        self.state = "IDLE"
        self.queue = []
        self.current_index = 0
        self.resume_cursor = ""
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.cancel_event = threading.Event()

    # Para qué sirve: cambia el estado formal actual de la máquina de estados.
    # Cómo funciona: asigna `self.state = new_state`.
    def transition(self, new_state: str):
        self.state = new_state

    # Para qué sirve: detiene la cola de amigos ante solicitud manual o bloqueo de seguridad.
    # Cómo funciona: limpia `pause_event` y cambia a `PAUSED_CHECKPOINT` o `PAUSED`.
    def pause(self, checkpoint: bool = False):
        self.pause_event.clear()
        self.state = "PAUSED_CHECKPOINT" if checkpoint else "PAUSED"

    # Para qué sirve: reanuda la cola tras resolver un checkpoint o pausa.
    # Cómo funciona: reactiva `pause_event` y transiciona a `RESUMING`.
    def resume(self):
        self.pause_event.set()
        self.state = "RESUMING"

    # Para qué sirve: cancela de forma definitiva la cola pendiente de cuentas amigas.
    # Cómo funciona: activa `cancel_event`, desbloquea `pause_event` y fija `state = "CANCELLED"`.
    def cancel(self):
        self.cancel_event.set()
        self.pause_event.set()
        self.state = "CANCELLED"

    # Para qué sirve: arma la cola FIFO de cuentas amigas elegibles en SQLite hasta el tope configurado.
    # Cómo funciona: normaliza handles, deduce URL pública si falta, limita a `MAX_FRIEND_PROFILES` y crea `friend_scan_runs`.
    def build_queue(self, candidates: list, max_profiles: Optional[int] = None) -> list:
        limit = int(max_profiles if max_profiles is not None else (os.getenv("MAX_FRIEND_PROFILES", "10") or 10))
        self.transition("QUEUING_FRIENDS")
        self.queue = []
        for c in (candidates or [])[:max(0, limit)]:
            h = (c.get("handle") or "").strip().lstrip("@").lower()
            if not h:
                continue
            plat = (c.get("platform") or self.platform).strip().lower()
            url = (c.get("profile_url") or "").strip()
            if not url:
                if plat == "instagram":
                    url = f"https://www.instagram.com/{h}/"
                elif plat == "facebook":
                    url = f"https://www.facebook.com/{h}"
            scan_rec = storage.create_friend_scan_run(
                case_id=self.case_id,
                platform=plat,
                target_handle=h,
                target_url=url,
                interactant_id=c.get("id"),
                parent_run_id=self.parent_run_id,
                resume_cursor=self.resume_cursor,
            )
            if scan_rec:
                self.queue.append(scan_rec)
        return self.queue

    # Para qué sirve: marca el retorno al perfil principal tras concluir la cola de amigos.
    # Cómo funciona: registra en `parent_run_id` desde qué `resume_cursor` continúa y termina la FSM en `DONE`.
    def mark_primary_resume(self) -> dict:
        self.transition("SCANNING_PRIMARY_RESUME")
        if self.parent_run_id:
            storage.update_browser_agent_run(
                self.parent_run_id,
                status="running",
                step="Reanudando escaneo del perfil principal...",
                log=f"Cola de cuentas amigas completada; reanudando desde cursor: {self.resume_cursor or 'inicio'}",
            )
        self.transition("DONE")
        return {"state": self.state, "resume_cursor": self.resume_cursor}


_FRIEND_SCANS: dict = {}  # parent_key -> FriendScanStateMachine


# Para qué sirve: consolida coincidencias visuales (`dHash`) e historial cronológico compartido con cuentas amigas.
# Cómo funciona: compara media sujeto vs. amigo, refuerza `visual_proximity_score` (`min(100, pares*28)`) y ordena posts/comentarios/fotos por fecha.
def get_case_friend_evidence(case_id: str) -> dict:
    """
    Devuelve evidencia visual coincidente (dHash entre sujeto y cuentas amigas),
    puntuación `visual_proximity_score` e historial cronológico consolidado por cuenta amiga.
    """
    if not storage.case_exists(case_id):
        return {"case_id": case_id, "candidates": [], "visual_matches": [], "consolidated_history": []}

    from modules.vision_analysis import compare_media_between_profiles

    content = storage.get_case_content(case_id)
    candidates = storage.get_friend_candidate_handles(case_id)
    cand_handles = {(c.get("handle") or "").lower() for c in candidates}

    media_items = content.get("media_items") or []
    posts = content.get("posts") or []
    comments = content.get("comments") or []
    profiles = content.get("profiles") or []
    geo_hints = storage.list_case_geo_hints(case_id)

    subject_handles = {(p.get("username") or "").strip().lstrip("@").lower() for p in profiles if p.get("username")}
    # Media cuyo autor/post pertenece a una cuenta amiga vs. sujeto
    post_author_by_id = {p.get("id"): (p.get("author") or "").strip().lstrip("@").lower() for p in posts}
    geo_by_media_id = {}
    for gh in geo_hints:
        mid = gh.get("media_id")
        if mid and mid not in geo_by_media_id:
            geo_by_media_id[mid] = gh.get("value") or ""

    subject_media = []
    friend_media_map = {}
    for m in media_items:
        meta = m.get("metadata") or {}
        owner = (meta.get("owner_handle") or post_author_by_id.get(m.get("post_id")) or "").strip().lstrip("@").lower()
        if owner and owner in cand_handles and owner not in subject_handles:
            friend_media_map.setdefault(owner, []).append(m)
        else:
            subject_media.append(m)

    all_matches = []
    matches_by_friend = {}
    for fh, fmedia in friend_media_map.items():
        pairs = compare_media_between_profiles(subject_media, fmedia)
        if pairs:
            matches_by_friend[fh] = pairs
            for pr in pairs:
                all_matches.append({"friend_handle": fh, **pr})
            # Reforzar visual_proximity_score en interactions
            score = min(100.0, len(pairs) * 28.0)
            storage.update_interaction_visual_score(case_id, fh, score)

    # Historial consolidado cronológico por cuenta amiga
    consolidated = []
    for c in candidates:
        fh = (c.get("handle") or "").lower()
        hist = []
        for p in posts:
            if (p.get("author") or "").strip().lstrip("@").lower() == fh:
                hist.append({
                    "date": p.get("published_at") or p.get("captured_at") or "",
                    "kind": "post",
                    "text": (p.get("content") or "")[:300],
                    "location": p.get("published_location") or "",
                    "thumbnail_url": "",
                    "source_url": p.get("url") or "",
                })
        for cm in comments:
            if (cm.get("author_handle") or "").strip().lstrip("@").lower() == fh:
                hist.append({
                    "date": cm.get("published_at") or cm.get("captured_at") or "",
                    "kind": "comment",
                    "text": (cm.get("content") or "")[:300],
                    "location": "",
                    "thumbnail_url": "",
                    "source_url": (cm.get("metadata") or {}).get("url") or "",
                })
        for fm in friend_media_map.get(fh, []):
            hist.append({
                "date": fm.get("published_at") or fm.get("captured_at") or "",
                "kind": "media",
                "text": fm.get("alt_text") or fm.get("filename") or "",
                "location": geo_by_media_id.get(fm.get("id"), ""),
                "thumbnail_url": fm.get("source_url") or "",
                "source_url": fm.get("source_url") or "",
            })
        hist.sort(key=lambda x: x.get("date") or "", reverse=True)
        f_matches = matches_by_friend.get(fh, [])
        consolidated.append({
            "handle": fh,
            "display_name": c.get("display_name") or fh,
            "profile_url": c.get("profile_url") or "",
            "visual_proximity_score": c.get("visual_proximity_score") or (round(min(100.0, len(f_matches) * 28.0), 1) if f_matches else 0.0),
            "visual_matches_count": len(f_matches),
            "visual_matches": f_matches,
            "history": hist,
        })

    return {
        "case_id": case_id,
        "candidates": candidates,
        "visual_matches": all_matches,
        "consolidated_history": consolidated,
        "scan_runs": storage.list_friend_scan_runs(case_id),
    }


# Para qué sirve: lanza la pasada secundaria autorizada (`authorized=True`) sobre cuentas amigas candidatas.
# Cómo funciona: construye la cola con `FriendScanStateMachine`, navega cada amigo pausando ante CAPTCHA/login y al final retoma `resume_cursor`.
def start_friend_scan_queue(
    case_id: str,
    authorized: bool = False,
    parent_run_id: str = "",
    platform: str = "instagram",
    delay_seconds: Optional[float] = None,
    headless: bool = False,
) -> dict:
    """
    Inicia la pasada secundaria sobre cuentas amigas candidatas (opt-in obligatorio `authorized=True`).
    Reutiliza `detect_security_or_login_gate` (nunca evade CAPTCHA/login) y al terminar
    reanuda el escaneo principal desde `resume_cursor`.
    """
    if not authorized:
        raise PermissionError("Se requiere confirmación explícita (`authorized=True`) para iniciar friend-scan.")
    if not storage.case_exists(case_id):
        raise ValueError(f"Caso no encontrado: {case_id}")

    parent_run = storage.get_browser_agent_run(parent_run_id) if parent_run_id else None
    resume_cursor = (parent_run or {}).get("resume_cursor") or ""
    candidates = storage.get_friend_candidate_handles(case_id)

    fsm = FriendScanStateMachine(case_id=case_id, parent_run_id=parent_run_id, platform=platform)
    fsm.resume_cursor = resume_cursor
    queue = fsm.build_queue(candidates)

    if not queue:
        fsm.mark_primary_resume()
        return {
            "case_id": case_id,
            "run_id": parent_run_id or "none",
            "state": fsm.state,
            "queued_count": 0,
            "scans": [],
            "resume_cursor": resume_cursor,
        }

    scan_delay = float(delay_seconds if delay_seconds is not None else (os.getenv("FRIEND_SCAN_DELAY_SECONDS", "5") or 5))
    scan_key = str(queue[0]["id"])
    with _CTRL_LOCK:
        _FRIEND_SCANS[scan_key] = fsm

    # Para qué sirve: procesa en segundo plano la cola de cuentas amigas y retoma el perfil principal al acabar.
    # Cómo funciona: recorre `queue` en una sola sesión Playwright, frena ante bloqueos con `fsm.pause(checkpoint=True)` y actualiza cada `friend_scan_run`.
    def _friend_worker():
        from modules.browser_adapters import get_browser_adapter
        from modules.vision_analysis import compare_media_between_profiles

        if not playwright_available():
            for sc in queue:
                storage.update_friend_scan_run(sc["id"], status="failed", error="Playwright no disponible", finished=True)
            fsm.mark_primary_resume()
            return

        from playwright.sync_api import sync_playwright
        plat = (platform or "instagram").strip().lower()
        adapter = get_browser_adapter(plat)
        profile_dir = get_profile_dir(plat)
        ctrl = RunControl(parent_run_id or f"fscan_{scan_key}")

        try:
            with sync_playwright() as p:
                ctx = _launch_persistent_context(p, profile_dir, headless=headless)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                for idx, sc in enumerate(queue, start=1):
                    if fsm.cancel_event.is_set() or ctrl.cancel_event.is_set():
                        storage.update_friend_scan_run(sc["id"], status="cancelled", finished=True)
                        continue
                    fsm.transition(f"SCANNING_FRIEND({idx})")
                    storage.update_friend_scan_run(sc["id"], status="running")

                    gate_hit = {"reason": ""}

                    # Para qué sirve: detiene la cola de amigos si la red muestra CAPTCHA, checkpoint o login-wall.
                    # Cómo funciona: inspecciona la página y pasa la FSM a `paused_checkpoint` devolviendo `False`.
                    def _check_gate():
                        r = adapter.detect_security_or_login_gate(page)
                        if r:
                            gate_hit["reason"] = r
                            fsm.pause(checkpoint=True)
                            storage.update_friend_scan_run(sc["id"], status="paused_checkpoint", error=r)
                            return False
                        return True

                    staged_items = []

                    # Para qué sirve: acumula los hallazgos extraídos del amigo marcando su `owner_handle`.
                    # Cómo funciona: añade el dueño al `payload` y guarda la tupla en `staged_items` para conteo.
                    def _emit(kind, author, target, content, pub_at, src_url, payload):
                        pl = dict(payload or {})
                        pl["owner_handle"] = sc["target_handle"]
                        staged_items.append((kind, author, target, content, pub_at, src_url, pl))

                    adapter.run(
                        page=page,
                        target_url=sc["target_url"],
                        limits={"max_posts": 6, "max_comments_per_post": 8, "delay_seconds": max(1.0, min(scan_delay, 8.0))},
                        ctrl=ctrl,
                        emit=_emit,
                        check_gate=_check_gate,
                        update_status=lambda *args, **kwargs: None,
                    )

                    if gate_hit["reason"]:
                        # No continuar ante CAPTCHA/login
                        break

                    # Comparación visual si hay media con dHash en el caso
                    storage.update_friend_scan_run(
                        sc["id"],
                        status="done",
                        items_scanned=len(staged_items),
                        finished=True,
                    )
                    if idx < len(queue):
                        time.sleep(min(scan_delay, 8.0))

                # Reanudar perfil principal si no hubo bloqueo ni cancelación
                if fsm.state not in ("PAUSED_CHECKPOINT", "CANCELLED"):
                    if parent_run and parent_run.get("target_url"):
                        fsm.mark_primary_resume()
                        adapter.run(
                            page=page,
                            target_url=parent_run["target_url"],
                            limits={"max_posts": 12, "max_comments_per_post": 15, "resume_after_url": resume_cursor},
                            ctrl=ctrl,
                            emit=lambda k, a, t, c, pa, su, pl: storage.add_browser_staged_item(
                                parent_run_id, case_id, k, plat, a, t, c, pa, su, pl
                            ),
                            check_gate=lambda: not adapter.detect_security_or_login_gate(page),
                            update_status=lambda *args, **kwargs: None,
                        )
                    else:
                        fsm.mark_primary_resume()
                try:
                    ctx.close()
                except Exception:
                    pass
        except Exception as exc:  # noqa: BLE001
            for sc in queue:
                if sc.get("status") in ("queued", "running"):
                    storage.update_friend_scan_run(sc["id"], status="failed", error=str(exc), finished=True)

    threading.Thread(target=_friend_worker, daemon=True).start()
    return {
        "case_id": case_id,
        "run_id": scan_key,
        "state": fsm.state,
        "queued_count": len(queue),
        "scans": queue,
        "resume_cursor": resume_cursor,
    }


# Para qué sirve: captura de forma aislada el perfil y contenido público de un amigo agregado manualmente por URL.
# Cómo funciona: abre Playwright con el adaptador, encapsula datos en un bundle `owner_type='friend'` y marca `pending_manual` ante bloqueos.
def crawl_single_friend_profile(
    case_id: str,
    friend_id: int,
    max_posts: int = 8,
    headless: bool = False,
) -> dict:
    """
    Captura el perfil público de UN amigo añadido por URL y almacena su contenido
    de forma ESTRICTAMENTE aislada (`owner_type='friend', owner_id=friend_id`).
    Si encuentra muro de login o CAPTCHA, marca `pending_manual` sin evadir controles.
    """
    f_detail = storage.get_friend_isolated_detail(case_id, friend_id)
    if not f_detail:
        raise ValueError("Amigo no encontrado en el caso.")
    friend = f_detail["friend"]
    plat = (friend.get("platform") or "instagram").strip().lower()
    target_url = (friend.get("profile_url") or "").strip()
    if not target_url:
        raise ValueError("El amigo no tiene URL pública configurada.")
    if not playwright_available():
        raise RuntimeError("Playwright no está instalado.")

    from modules.browser_adapters import get_browser_adapter
    from playwright.sync_api import sync_playwright

    adapter = get_browser_adapter(plat)
    profile_dir = get_profile_dir(plat)
    bundle = empty_bundle(plat, friend.get("handle") or "", access_method="browser_supervised")
    gate_reason = {"text": ""}

    class _SimpleCtrl:
        # Para qué sirve: mantiene compatibilidad con el adaptador indicando que la corrida no está pausada.
        # Cómo funciona: devuelve `True` inmediato en esta captura sincrónica corta.
        def wait_if_paused(self):
            return True

        # Para qué sirve: aplica una pausa breve entre acciones DOM durante la captura del amigo.
        # Cómo funciona: duerme hasta `min(1.5, a)` segundos y devuelve `True`.
        def human_delay(self, a=1.0, b=1.8):
            time.sleep(min(1.5, float(a)))
            return True

    # Para qué sirve: distribuye perfil, posts y media extraídos en el bundle aislado del amigo.
    # Cómo funciona: etiqueta cada entrada con `owner_type='friend'` y `owner_id=friend_id`.
    def _emit(kind, author, target, content, pub_at, src_url, payload):
        pl = dict(payload or {})
        if kind == "profile":
            bundle["profile"] = {
                "username": pl.get("username") or friend.get("handle") or author,
                "display_name": pl.get("display_name") or friend.get("display_name") or author,
                "bio": content or pl.get("bio") or "",
            }
        elif kind == "post":
            bundle["posts"].append({
                "external_id": pl.get("external_id") or f"fr_{friend_id}_{len(bundle['posts']) + 1}",
                "platform": plat,
                "author": friend.get("handle") or author,
                "content": content or "",
                "published_at": pub_at or "",
                "url": src_url or target_url,
                "published_location": pl.get("published_location") or "",
                "metadata": {"owner_type": "friend", "owner_id": friend_id},
            })
        elif kind == "media":
            surl = src_url or ""
            if surl:
                bundle["media_items"].append({
                    "filename": pl.get("filename") or f"fr_{friend_id}_{len(bundle['media_items']) + 1}.jpg",
                    "media_type": pl.get("media_type") or "image",
                    "source_url": surl,
                    "alt_text": content or "",
                    "post_external_id": pl.get("post_external_id") or "",
                    "published_at": pub_at or "",
                    "metadata": {"owner_type": "friend", "owner_id": friend_id},
                })

    with sync_playwright() as p:
        ctx = _launch_persistent_context(p, profile_dir, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # Para qué sirve: detiene la captura del amigo si aparece un muro de login o CAPTCHA.
        # Cómo funciona: guarda el motivo en `gate_reason["text"]` y retorna `False` para cortar el adaptador.
        def _check_gate():
            r = adapter.detect_security_or_login_gate(page)
            if r:
                gate_reason["text"] = r
                return False
            return True

        try:
            adapter.run(
                page=page,
                target_url=target_url,
                limits={"max_posts": max(1, min(int(max_posts), 12)), "max_comments_per_post": 5, "delay_seconds": 1.5},
                ctrl=_SimpleCtrl(),
                emit=_emit,
                check_gate=_check_gate,
                update_status=lambda *args, **kwargs: None,
            )
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    if gate_reason["text"]:
        storage.update_friend_status(case_id, friend_id, "pending_manual")
        return {
            "case_id": case_id,
            "friend_id": friend_id,
            "status": "pending_manual",
            "reason": gate_reason["text"],
            "posts_inserted": 0,
            "media_inserted": 0,
        }

    ing = storage.ingest_friend_bundle(case_id, friend_id, bundle) or {}
    storage.update_friend_status(case_id, friend_id, "active")
    return {
        "case_id": case_id,
        "friend_id": friend_id,
        "status": "active",
        "posts_inserted": ing.get("posts_inserted", 0),
        "media_inserted": ing.get("media_inserted", 0),
    }
