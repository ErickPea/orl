# -*- coding: utf-8 -*-
"""Pruebas de persistencia SQLite, migraciones versionadas, concurrencia y recuperación de jobs."""
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import storage  # noqa: E402
from routers import runtime  # noqa: E402


class TestPersistenceConcurrencyAndRecovery(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        storage.set_db_path(Path(self._tmp.name) / "conc.db")
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_versioned_schema_migrations_idempotent(self):
        v1 = storage.get_schema_version()
        self.assertGreaterEqual(v1, 4)
        # Re-ejecutar init_db() debe ser idempotente y conservar versión
        storage.init_db()
        self.assertEqual(storage.get_schema_version(), v1)
        with storage._connect() as conn:
            rows = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
            self.assertEqual(len(rows), v1)

    def test_concurrent_reads_and_writes_with_thread_pool(self):
        base = storage.create_case(title="Caso Concurrencia", subject_name="Erick")["case"]["id"]
        errors = []

        def writer(idx: int):
            try:
                for j in range(10):
                    storage.add_identifier(base, "username", f"user_{idx}_{j}", recalc=False)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def reader():
            try:
                for _ in range(25):
                    _ = storage.list_cases()
                    _ = storage.get_case_detail(base)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)] + [
            threading.Thread(target=reader) for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(errors, [])
        detail = storage.get_case_detail(base)
        usernames = [i for i in detail["identifiers"] if i["kind"] == "username"]
        self.assertEqual(len(usernames), 40)

    def test_recover_orphaned_jobs_and_memory_cap(self):
        j_run = {
            "id": "job_orphan_1",
            "case_id": None,
            "kind": "username",
            "target": "erick",
            "status": "running",
            "step": "Procesando...",
            "progress": 40,
            "logs": ["[2026-11-20 10:00:00] Inicio"],
            "started_at": "2026-11-20 10:00:00",
            "finished_at": None,
            "folder": "",
            "files": [],
            "result": None,
            "error": None,
        }
        j_done = {
            **j_run,
            "id": "job_done_1",
            "status": "done",
            "step": "Completado",
            "progress": 100,
            "finished_at": "2026-11-20 10:01:00",
        }
        storage.save_job(j_run)
        storage.save_job(j_done)

        recovered = storage.recover_orphaned_jobs("Reinicio simulado")
        self.assertEqual(recovered, 1)

        after_run = storage.get_persisted_job("job_orphan_1")
        after_done = storage.get_persisted_job("job_done_1")
        self.assertEqual(after_run["status"], "error")
        self.assertEqual(after_run["error"], "Reinicio simulado")
        self.assertTrue(any("Reinicio simulado" in line for line in after_run["logs"]))
        self.assertEqual(after_done["status"], "done")

        # Verificar cota máxima en memoria en routers.runtime._JOBS
        with runtime._JOBS_LOCK:
            runtime._JOBS.clear()
        total_to_create = runtime._MAX_IN_MEMORY_JOBS + 15
        active_job = runtime._create_job("username", "activo_en_curso")
        for i in range(total_to_create):
            jb = runtime._create_job("username", f"fin_{i}")
            runtime._update_job(jb["id"], status="done", progress=100, finished_at=f"2026-11-20 12:{i:02d}:00")

        with runtime._JOBS_LOCK:
            self.assertLessEqual(len(runtime._JOBS), runtime._MAX_IN_MEMORY_JOBS)
            self.assertIn(active_job["id"], runtime._JOBS)


if __name__ == "__main__":
    unittest.main()
