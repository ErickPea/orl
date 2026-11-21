# -*- coding: utf-8 -*-
"""Clase base común para los adaptadores del Agente de Navegación Supervisada."""
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from modules.logger import get_logger

_LOGGER = get_logger("browser_adapters")
_DIAG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "browser_diagnostics"


class BaseBrowserAdapter:
    """
    Define el contrato común de navegación supervisada:
      - `detect_security_or_login_gate(page)` -> detecta muros de login, CAPTCHAs o checkpoints.
      - `run(page, target_url, limits, ctrl, emit, check_gate, update_status)` -> extrae
        perfil, publicaciones, media y comentarios visibles respetando pausas y cancelación.
    """

    platform: str = "generic"

    _SECURITY_URL_MARKERS = (
        "/login",
        "/accounts/login",
        "/checkpoint",
        "/two_factor",
        "/challenge",
        "captcha",
        "/authwall",
    )

    _SECURITY_TEXT_MARKERS = (
        "confirm you are human",
        "confirma que eres humano",
        "demuestra que no eres un robot",
        "security check",
        "verificación de seguridad",
        "suspicious activity",
        "actividad sospechosa",
        "enter the code",
        "código de confirmación",
    )

    # Para qué sirve: detecta muros de inicio de sesión, 2FA, checkpoints o CAPTCHAs para pausar con seguridad.
    # Cómo funciona: revisa la URL contra `_SECURITY_URL_MARKERS`, busca iframes CAPTCHA y escanea `_SECURITY_TEXT_MARKERS` en el `body`.
    def detect_security_or_login_gate(self, page) -> str:
        """
        Inspecciona URL y texto visible en busca de login-wall, 2FA, checkpoint o CAPTCHA.
        NUNCA intenta resolver ni evadir el bloqueo; devuelve una descripción para pausar.
        """
        try:
            url = (page.url or "").lower()
            for mk in self._SECURITY_URL_MARKERS:
                if mk in url:
                    return f"Muro de acceso o verificación ({mk})"

            # Comprobar iframes conocidos de CAPTCHA
            if page.locator("iframe[src*='captcha'], iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='turnstile']").count() > 0:
                return "CAPTCHA / verificación humana"

            body_txt = (page.locator("body").inner_text(timeout=1500) or "")[:1500].lower()
            for txt_mk in self._SECURITY_TEXT_MARKERS:
                if txt_mk in body_txt:
                    return f"Control de seguridad ({txt_mk})"
        except Exception:
            pass
        return ""

    # Para qué sirve: unifica comentarios y respuestas extraídos del DOM bajo un mismo diccionario nominal.
    # Cómo funciona: normaliza el handle, rellena `author_display_name` y conserva URL y flags `is_reply`/`parent_comment_id`.
    @staticmethod
    def normalize_comment_record(
        author_handle: str,
        text: str,
        author_display_name: str = "",
        author_profile_url: str = "",
        is_reply: bool = False,
        parent_comment_id: str = "",
    ) -> dict:
        """Normaliza un comentario/respuesta con metadatos nominales del interactuante."""
        h = (author_handle or "").strip().lstrip("@").lower()
        dn = (author_display_name or "").strip() or h
        return {
            "author": h,
            "author_handle": h,
            "author_display_name": dn,
            "author_profile_url": (author_profile_url or "").strip(),
            "text": (text or "").strip(),
            "is_reply": bool(is_reply),
            "parent_comment_id": str(parent_comment_id or ""),
        }

    # Para qué sirve: deja evidencia diagnóstica cuando un selector DOM falla o cae a un selector fallback.
    # Cómo funciona: toma un screenshot en `data/browser_diagnostics/<plat>`, registra advertencia en log y llama a `update_status`.
    def record_selector_telemetry(
        self,
        page,
        update_status: Optional[Callable],
        step_name: str,
        failed_selector: str,
        fallback_used: str = "",
        detail: str = "",
        diag_dir: Optional[Path] = None,
    ) -> str:
        """
        Registra telemetría cuando un selector primario falla o cae a fallback:
        guarda screenshot diagnóstico (si `page` lo soporta) y escribe en log/run.
        """
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
        safe_step = re.sub(r"[^a-zA-Z0-9_-]", "_", step_name or "step")[:40]
        target_dir = (diag_dir or _DIAG_DIR) / self.platform
        shot_path_str = ""
        try:
            if page is not None and hasattr(page, "screenshot"):
                target_dir.mkdir(parents=True, exist_ok=True)
                shot_file = target_dir / f"{ts}_{safe_step}.png"
                page.screenshot(path=str(shot_file), full_page=False)
                if shot_file.is_file():
                    shot_path_str = str(shot_file)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("No se pudo capturar screenshot diagnóstico (%s/%s): %s", self.platform, step_name, exc)

        fb_txt = f" -> fallback={fallback_used}" if fallback_used else " (sin fallback)"
        det_txt = f" | {detail}" if detail else ""
        shot_txt = f" | screenshot={shot_path_str}" if shot_path_str else ""
        msg = f"[Telemetría DOM:{self.platform}:{step_name}] falló='{failed_selector}'{fb_txt}{det_txt}{shot_txt}"
        _LOGGER.warning(msg)
        if update_status is not None:
            try:
                update_status(None, None, msg)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("No se pudo escribir telemetría en el log del run: %s", exc)
        return shot_path_str

    # Para qué sirve: define la firma obligatoria que todo adaptador de plataforma debe implementar.
    # Cómo funciona: método abstracto que lanza `NotImplementedError` si la subclase no lo sobrescribe.
    def run(
        self,
        page,
        target_url: str,
        limits: dict,
        ctrl,
        emit: Callable,
        check_gate: Callable,
        update_status: Callable,
    ) -> None:
        raise NotImplementedError
