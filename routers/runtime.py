# -*- coding: utf-8 -*-
"""Estado de jobs en segundo plano y helpers compartidos entre routers."""
import os
import secrets
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from modules import storage
from modules.logger import get_logger

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"

_LOGGER = get_logger("routers.runtime")
_JOBS_LOCK = threading.Lock()
_JOBS: dict = {}
_MAX_IN_MEMORY_JOBS = 100
_EPHEMERAL_API_TOKEN = secrets.token_urlsafe(32)


def get_active_api_token() -> str:
    """Devuelve `OSINT_API_TOKEN` del entorno si está configurado o el token efímero de sesión."""
    env_tok = os.environ.get("OSINT_API_TOKEN", "").strip()
    return env_tok or _EPHEMERAL_API_TOKEN


def _prune_finished_jobs_locked() -> None:
    """Mantiene acotado `_JOBS` en RAM eliminando los trabajos finalizados más antiguos."""
    if len(_JOBS) <= _MAX_IN_MEMORY_JOBS:
        return
    finished = [
        (j.get("finished_at") or j.get("started_at") or "", jid)
        for jid, j in _JOBS.items()
        if j.get("status") != "running"
    ]
    finished.sort()
    excess = len(_JOBS) - _MAX_IN_MEMORY_JOBS
    for _, jid in finished[:excess]:
        _JOBS.pop(jid, None)


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _validate_case_id(case_id: Optional[str]) -> Optional[str]:
    cid = (case_id or "").strip() or None
    if cid and not storage.case_exists(cid):
        raise HTTPException(status_code=404, detail=f"Caso no encontrado: {cid}")
    return cid


def _create_job(kind: str, target: str, case_id: Optional[str] = None) -> dict:
    """Registra un nuevo job en estado `running` en memoria y en SQLite."""
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "case_id": case_id,
        "kind": kind,
        "target": target,
        "status": "running",
        "step": "Iniciando...",
        "progress": 5,
        "logs": [f"[{_now_iso()}] Trabajo iniciado ({kind}): {target}"],
        "started_at": _now_iso(),
        "finished_at": None,
        "folder": None,
        "files": [],
        "result": None,
        "error": None,
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
        _prune_finished_jobs_locked()
    try:
        storage.save_job(job)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("No se pudo persistir el inicio del job %s: %s", job_id, exc)
    return job


def _update_job(job_id: str, step: str = None, progress: int = None, log: str = None, **fields):
    """Actualiza el progreso, paso actual y logs de un job de forma thread-safe."""
    snapshot = None
    should_ingest = False
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        if step is not None:
            job["step"] = step
        if progress is not None:
            job["progress"] = max(0, min(100, int(progress)))
        if log:
            job["logs"].append(f"[{_now_iso()}] {log}")
        for k, v in fields.items():
            job[k] = v
        if job.get("status") in ("done", "error"):
            snapshot = dict(job)
            should_ingest = (
                job.get("status") == "done"
                and bool(job.get("case_id"))
                and isinstance(job.get("result"), dict)
                and not str(job.get("kind") or "").startswith(("ingest:", "vision:", "nlp:", "investigate:", "relational:"))
            )

    if should_ingest and snapshot:
        try:
            ing = storage.ingest_job_result_to_case(
                case_id=snapshot["case_id"],
                job_id=snapshot["id"],
                kind=snapshot["kind"],
                target=snapshot["target"],
                folder=snapshot.get("folder") or "",
                result=snapshot["result"],
            )
            if ing:
                with _JOBS_LOCK:
                    job = _JOBS.get(job_id)
                    if job:
                        job["case_ingestion"] = ing
                        job["logs"].append(
                            f"[{_now_iso()}] Guardado en caso {ing['case_id']}: "
                            f"{ing['profiles_ingested']} perfiles, {ing['identifiers_ingested']} identificadores."
                        )
                        snapshot = dict(job)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Aviso guardando resultado del job %s en caso: %s", job_id, exc)
            with _JOBS_LOCK:
                job = _JOBS.get(job_id)
                if job:
                    job["logs"].append(f"[{_now_iso()}] Aviso al guardar en caso: {exc}")
                    snapshot = dict(job)

    if snapshot:
        try:
            storage.save_job(snapshot)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("No se pudo actualizar el job %s en SQLite: %s", job_id, exc)
        with _JOBS_LOCK:
            _prune_finished_jobs_locked()


def _list_folder_files(out_dir: Path) -> list:
    """Devuelve lista de archivos generados en una carpeta de salida (solo nivel principal)."""
    if not out_dir or not out_dir.exists():
        return []
    archivos = []
    for f in sorted(out_dir.iterdir(), key=lambda p: p.name.lower()):
        if f.is_file():
            archivos.append({
                "name": f.name,
                "size": f.stat().st_size,
                "url": f"/api/reports/{out_dir.name}/{f.name}",
            })
    return archivos


def _run_in_background(job_id: str, worker):
    """Ejecuta `worker(job_id)` en un hilo demonio y captura cualquier error en el job."""
    def _runner():
        try:
            worker(job_id)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("Error ejecutando job en segundo plano %s", job_id)
            _update_job(
                job_id,
                status="error",
                step="Error durante la ejecución",
                progress=100,
                log=f"Error: {exc}",
                error=str(exc),
                finished_at=_now_iso(),
            )

    t = threading.Thread(target=_runner, daemon=True)
    t.start()


def _require_legitimate(confirm: bool):
    """Valida que el usuario confirmó el uso legítimo antes de lanzar una consulta OSINT."""
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Debes confirmar que tu uso es legítimo y sobre fuentes públicas autorizadas.",
        )
