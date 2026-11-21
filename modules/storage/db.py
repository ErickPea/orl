# -*- coding: utf-8 -*-
"""
Capa de persistencia SQLite para OSINT-Auto (Fase 1).

Gestiona:
  - Casos de investigación / auditoría (`cases`)
  - Entidades principales y asociadas (`entities`)
  - Semillas / identificadores verificados (`identifiers`)
  - Perfiles normalizados con resolución de entidades (`profiles`)
  - Archivos multimedia analizados (`media_items`)
  - Tablas preparadas para Fases 2–5 (`posts`, `comments`, `interactions`)
  - Bitácora de ejecuciones por caso (`case_runs`) y persistencia de jobs (`jobs`)
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from modules import utils
from modules.entity_resolution import (
    evaluate_case_resolution,
    normalize_text,
    normalize_url,
)
from modules.logger import get_logger

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = ROOT_DIR / "data" / "osint_cases.db"

_LOGGER = get_logger("storage.db")
_DB_LOCK = threading.RLock()
_CONN_LOCK = threading.Lock()
_THREAD_LOCAL = threading.local()
_CONN_REGISTRY: set = set()
_POOL_EPOCH = 0
_CUSTOM_DB_PATH: Optional[Path] = None

VALID_IDENTIFIER_KINDS = {"username", "email", "phone", "full_name", "location", "domain", "other"}
VALID_MANUAL_STATUS = {"auto", "confirmed", "rejected"}
VALID_PURPOSES = {"autodiagnostico", "auditoria", "investigacion", "periodismo", "otro"}


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_path() -> Path:
    if _CUSTOM_DB_PATH is not None:
        return _CUSTOM_DB_PATH
    env_path = os.environ.get("OSINT_DB_PATH", "").strip()
    if env_path:
        return Path(env_path)
    return DEFAULT_DB_PATH


def close_all_connections() -> None:
    """Cierra todas las conexiones SQLite cacheadas en el pool (seguro para tests y shutdown)."""
    global _POOL_EPOCH
    with _CONN_LOCK:
        _POOL_EPOCH += 1
        conns = list(_CONN_REGISTRY)
        _CONN_REGISTRY.clear()
    for conn in conns:
        try:
            conn.close()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Error cerrando conexión SQLite del pool: %s", exc)


def set_db_path(path: Optional[Path]) -> None:
    """Permite redirigir la base de datos (útil para pruebas automatizadas)."""
    global _CUSTOM_DB_PATH
    close_all_connections()
    _CUSTOM_DB_PATH = Path(path) if path is not None else None


def _get_thread_conn(db_path: Path) -> sqlite3.Connection:
    db_key = str(db_path.resolve())
    epoch = _POOL_EPOCH
    if getattr(_THREAD_LOCAL, "epoch", None) != epoch:
        _THREAD_LOCAL.epoch = epoch
        _THREAD_LOCAL.conns = {}
        _THREAD_LOCAL.depths = {}
        _THREAD_LOCAL.wal_set = set()

    conn = _THREAD_LOCAL.conns.get(db_key)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except sqlite3.Error:
            _THREAD_LOCAL.conns.pop(db_key, None)
            _THREAD_LOCAL.depths.pop(db_key, None)
            _THREAD_LOCAL.wal_set.discard(db_key)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    with _CONN_LOCK:
        if _POOL_EPOCH == epoch:
            _CONN_REGISTRY.add(conn)
        else:
            # Hubo un cambio de ruta/cierre concurrente; descartar antes de cachear
            conn.close()
            return _get_thread_conn(db_path)

    _THREAD_LOCAL.conns[db_key] = conn
    _THREAD_LOCAL.depths[db_key] = 0
    return conn


@contextmanager
def _connect():
    db_path = get_db_path()
    db_key = str(db_path.resolve())
    conn = _get_thread_conn(db_path)
    depth = _THREAD_LOCAL.depths.get(db_key, 0)
    _THREAD_LOCAL.depths[db_key] = depth + 1
    sp_name = f"sp_nested_{depth}" if depth > 0 else None
    if sp_name:
        conn.execute(f"SAVEPOINT {sp_name}")
    try:
        yield conn
        if sp_name:
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        else:
            conn.commit()
    except Exception:
        if sp_name:
            try:
                conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            except sqlite3.Error:
                conn.rollback()
        else:
            conn.rollback()
        raise
    finally:
        cur_depth = _THREAD_LOCAL.depths.get(db_key, 1)
        _THREAD_LOCAL.depths[db_key] = max(0, cur_depth - 1)


def _add_missing_columns(conn: sqlite3.Connection, table: str, specs: tuple) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for col_name, col_def in specs:
        if col_name not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")


def _mig_v1_media_columns(conn: sqlite3.Connection) -> None:
    _add_missing_columns(
        conn,
        "media_items",
        (
            ("source_url", "TEXT DEFAULT ''"),
            ("alt_text", "TEXT DEFAULT ''"),
            ("dhash", "TEXT DEFAULT ''"),
            ("ocr_text", "TEXT DEFAULT ''"),
            ("vision_json", "TEXT DEFAULT '{}'"),
            ("local_path", "TEXT DEFAULT ''"),
            ("owner_type", "TEXT DEFAULT 'subject'"),
            ("owner_id", "INTEGER"),
            ("post_id", "INTEGER"),
            ("published_at", "TEXT DEFAULT ''"),
        ),
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_post ON media_items(post_id)")


def _mig_v2_cases_and_posts(conn: sqlite3.Connection) -> None:
    _add_missing_columns(
        conn,
        "cases",
        (
            ("nlp_json", "TEXT DEFAULT '{}'"),
            ("sna_json", "TEXT DEFAULT '{}'"),
            ("friend_scope_enabled", "INTEGER DEFAULT 0"),
        ),
    )
    _add_missing_columns(
        conn,
        "posts",
        (
            ("published_location", "TEXT DEFAULT ''"),
            ("owner_type", "TEXT DEFAULT 'subject'"),
            ("owner_id", "INTEGER"),
        ),
    )


def _mig_v3_comments_interactants_agent(conn: sqlite3.Connection) -> None:
    _add_missing_columns(
        conn,
        "comments",
        (
            ("interactant_id", "INTEGER REFERENCES interactants(id)"),
            ("owner_type", "TEXT DEFAULT 'subject'"),
            ("owner_id", "INTEGER"),
        ),
    )
    _add_missing_columns(
        conn,
        "interactants",
        (
            ("owner_type", "TEXT DEFAULT 'interactant'"),
            ("status", "TEXT DEFAULT 'active'"),
            ("bio", "TEXT DEFAULT ''"),
            ("label", "TEXT DEFAULT ''"),
        ),
    )
    _add_missing_columns(conn, "interactions", (("visual_proximity_score", "REAL DEFAULT 0"),))
    _add_missing_columns(conn, "browser_agent_runs", (("resume_cursor", "TEXT DEFAULT ''"),))


def _mig_v4_jobs_status_index(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")


def _mig_v5_http_cache(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS http_cache (
            cache_key TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            query TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_http_cache_expires ON http_cache(expires_at)")


_MIGRATIONS = (
    (1, "v1_media_columns_and_index", _mig_v1_media_columns),
    (2, "v2_cases_and_posts_columns", _mig_v2_cases_and_posts),
    (3, "v3_comments_interactants_agent", _mig_v3_comments_interactants_agent),
    (4, "v4_jobs_status_index", _mig_v4_jobs_status_index),
    (5, "v5_http_cache", _mig_v5_http_cache),
)


def _run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied = {
        int(r["version"])
        for r in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    for ver, name, fn in _MIGRATIONS:
        if ver in applied:
            continue
        fn(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (ver, name, _now_iso()),
        )
        _LOGGER.info("Migración SQLite aplicada: v%s (%s)", ver, name)


def get_schema_version() -> int:
    """Devuelve la última versión de migración aplicada en la base actual."""
    try:
        with _connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations").fetchone()
            return int(row["v"]) if row else 0
    except sqlite3.OperationalError:
        return 0


def init_db() -> Path:
    """Crea las tablas e índices si no existen, activa WAL y aplica migraciones versionadas."""
    db_path = get_db_path()
    with _DB_LOCK:
        with _connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                subject_name TEXT DEFAULT '',
                purpose TEXT DEFAULT 'autodiagnostico',
                notes TEXT DEFAULT '',
                status TEXT DEFAULT 'open',
                resolution_json TEXT DEFAULT '{}',
                nlp_json TEXT DEFAULT '{}',
                sna_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                entity_type TEXT DEFAULT 'person',
                is_primary INTEGER DEFAULT 1,
                summary_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS identifiers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                value TEXT NOT NULL,
                normalized TEXT NOT NULL,
                source TEXT DEFAULT 'manual',
                confidence TEXT DEFAULT 'ALTA',
                created_at TEXT NOT NULL,
                UNIQUE(case_id, entity_id, kind, normalized),
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                site TEXT NOT NULL,
                url TEXT NOT NULL,
                normalized_url TEXT NOT NULL,
                username TEXT DEFAULT '',
                display_name TEXT DEFAULT '',
                bio TEXT DEFAULT '',
                location TEXT DEFAULT '',
                avatar_url TEXT DEFAULT '',
                website TEXT DEFAULT '',
                sources TEXT DEFAULT '',
                osint_confidence TEXT DEFAULT 'MEDIA',
                resolution_score INTEGER DEFAULT 0,
                resolution_level TEXT DEFAULT 'REVISAR',
                resolution_reasons TEXT DEFAULT '[]',
                manual_status TEXT DEFAULT 'auto',
                metadata_json TEXT DEFAULT '{}',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                UNIQUE(case_id, normalized_url),
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                entity_id TEXT,
                profile_id INTEGER,
                external_id TEXT DEFAULT '',
                platform TEXT NOT NULL,
                author TEXT DEFAULT '',
                content TEXT DEFAULT '',
                published_at TEXT DEFAULT '',
                url TEXT DEFAULT '',
                published_location TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                captured_at TEXT NOT NULL,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE SET NULL,
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS media_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                entity_id TEXT,
                profile_id INTEGER,
                filename TEXT NOT NULL,
                media_type TEXT DEFAULT 'image',
                sha256 TEXT DEFAULT '',
                md5 TEXT DEFAULT '',
                file_size INTEGER DEFAULT 0,
                gps_lat REAL,
                gps_lon REAL,
                captured_date TEXT DEFAULT '',
                device TEXT DEFAULT '',
                folder TEXT DEFAULT '',
                clean_copy_url TEXT DEFAULT '',
                source_url TEXT DEFAULT '',
                alt_text TEXT DEFAULT '',
                dhash TEXT DEFAULT '',
                ocr_text TEXT DEFAULT '',
                vision_json TEXT DEFAULT '{}',
                local_path TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                tags_json TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE SET NULL,
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                post_id INTEGER,
                external_id TEXT DEFAULT '',
                author_handle TEXT NOT NULL,
                target_handle TEXT DEFAULT '',
                content TEXT DEFAULT '',
                published_at TEXT DEFAULT '',
                sentiment TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                captured_at TEXT NOT NULL,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                source_handle TEXT NOT NULL,
                target_handle TEXT NOT NULL,
                kind TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                evidence_count INTEGER DEFAULT 1,
                metadata_json TEXT DEFAULT '{}',
                updated_at TEXT NOT NULL,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS case_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                job_id TEXT DEFAULT '',
                kind TEXT NOT NULL,
                target TEXT NOT NULL,
                status TEXT DEFAULT 'done',
                folder TEXT DEFAULT '',
                summary_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                finished_at TEXT DEFAULT '',
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                case_id TEXT,
                kind TEXT NOT NULL,
                target TEXT NOT NULL,
                status TEXT NOT NULL,
                step TEXT DEFAULT '',
                progress INTEGER DEFAULT 0,
                folder TEXT DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT,
                error TEXT,
                logs_json TEXT DEFAULT '[]',
                files_json TEXT DEFAULT '[]',
                result_json TEXT,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS platform_connections (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                account_label TEXT DEFAULT '',
                external_account_id TEXT DEFAULT '',
                auth_type TEXT NOT NULL,
                scopes_json TEXT DEFAULT '[]',
                token_encrypted TEXT NOT NULL,
                expires_at TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS profile_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                profile_id INTEGER,
                platform TEXT NOT NULL,
                source_url TEXT NOT NULL,
                access_method TEXT NOT NULL,
                display_name TEXT DEFAULT '',
                username TEXT DEFAULT '',
                bio TEXT DEFAULT '',
                public_links_json TEXT DEFAULT '[]',
                public_info_json TEXT DEFAULT '{}',
                captured_at TEXT NOT NULL,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                post_id INTEGER,
                platform TEXT NOT NULL,
                reaction_type TEXT NOT NULL,
                actor_handle TEXT DEFAULT '',
                count INTEGER DEFAULT 1,
                captured_at TEXT NOT NULL,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS evidence_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                record_kind TEXT NOT NULL,
                ref_table TEXT NOT NULL,
                ref_id INTEGER,
                platform TEXT NOT NULL,
                source_url TEXT DEFAULT '',
                access_method TEXT NOT NULL,
                excerpt TEXT DEFAULT '',
                captured_at TEXT NOT NULL,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS relational_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                evidence_id INTEGER,
                comment_id INTEGER,
                post_id INTEGER,
                platform TEXT NOT NULL,
                source_handle TEXT NOT NULL,
                target_handle TEXT DEFAULT '',
                category TEXT NOT NULL,
                subcategory TEXT DEFAULT '',
                matched_expression TEXT NOT NULL,
                context_excerpt TEXT NOT NULL,
                epistemic_type TEXT NOT NULL,
                ambiguity_flags_json TEXT DEFAULT '[]',
                confidence TEXT DEFAULT 'BAJA',
                engine TEXT DEFAULT 'local_rules',
                review_status TEXT DEFAULT 'pending',
                reviewer_note TEXT DEFAULT '',
                reviewed_at TEXT DEFAULT '',
                detected_at TEXT NOT NULL,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                FOREIGN KEY (evidence_id) REFERENCES evidence_records(id) ON DELETE SET NULL,
                FOREIGN KEY (comment_id) REFERENCES comments(id) ON DELETE CASCADE,
                FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS browser_agent_runs (
                id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                target_url TEXT NOT NULL,
                status TEXT NOT NULL,              -- 'waiting_login' | 'running' | 'paused' | 'paused_checkpoint' | 'completed' | 'cancelled' | 'error'
                step TEXT DEFAULT '',
                progress INTEGER DEFAULT 0,
                limits_json TEXT DEFAULT '{}',
                stats_json TEXT DEFAULT '{}',
                logs_json TEXT DEFAULT '[]',
                error TEXT DEFAULT '',
                committed INTEGER DEFAULT 0,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT DEFAULT '',
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS browser_staged_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                item_kind TEXT NOT NULL,           -- 'profile' | 'post' | 'comment' | 'media'
                platform TEXT NOT NULL,
                author_handle TEXT DEFAULT '',
                target_handle TEXT DEFAULT '',
                content TEXT DEFAULT '',
                published_at TEXT DEFAULT '',
                source_url TEXT DEFAULT '',
                payload_json TEXT DEFAULT '{}',
                selected INTEGER DEFAULT 1,
                captured_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES browser_agent_runs(id) ON DELETE CASCADE,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS interactants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                handle TEXT DEFAULT '',
                display_name TEXT DEFAULT '',
                profile_url TEXT DEFAULT '',
                first_seen TEXT DEFAULT '',
                last_seen TEXT DEFAULT '',
                UNIQUE(case_id, platform, handle),
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS friend_scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                parent_run_id TEXT DEFAULT '',
                interactant_id INTEGER,
                target_handle TEXT DEFAULT '',
                target_url TEXT DEFAULT '',
                platform TEXT NOT NULL,
                status TEXT DEFAULT 'queued',      -- 'queued' | 'running' | 'paused_checkpoint' | 'done' | 'failed' | 'cancelled'
                started_at TEXT DEFAULT '',
                finished_at TEXT DEFAULT '',
                items_scanned INTEGER DEFAULT 0,
                visual_matches INTEGER DEFAULT 0,
                resume_cursor TEXT DEFAULT '',
                error TEXT DEFAULT '',
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                FOREIGN KEY (interactant_id) REFERENCES interactants(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS geo_hints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                media_id INTEGER NOT NULL,
                hint_type TEXT DEFAULT '',         -- 'flora' | 'architecture' | 'ocr' | 'climate' | 'object' | 'exif'
                value TEXT DEFAULT '',
                confidence REAL DEFAULT 0.0,
                evidence_quote TEXT DEFAULT '',
                source TEXT DEFAULT 'local_context',
                created_at TEXT DEFAULT '',
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                FOREIGN KEY (media_id) REFERENCES media_items(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS location_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                media_id INTEGER,
                owner_type TEXT DEFAULT 'subject', -- 'subject' | 'friend' | 'interactant'
                owner_id INTEGER,
                source TEXT DEFAULT 'local_context', -- 'exif' | 'vlm' | 'ocr' | 'ner' | 'nominatim' | 'manual'
                country TEXT DEFAULT '',
                region TEXT DEFAULT '',
                city TEXT DEFAULT '',
                lat REAL,
                lon REAL,
                confidence REAL DEFAULT 0.0,
                evidence_quote TEXT DEFAULT '',
                published_at TEXT DEFAULT '',
                inferred_at TEXT NOT NULL,
                is_manual_override INTEGER DEFAULT 0,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                FOREIGN KEY (media_id) REFERENCES media_items(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS subject_profile (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                category TEXT NOT NULL,            -- 'hobbies'|'places'|'friends'|'ideas'|'personality'|'occupation'|'relationship_status'
                value TEXT NOT NULL,
                confidence REAL DEFAULT 0.0,
                evidence_refs TEXT DEFAULT '[]',
                epistemic_status TEXT DEFAULT 'ambiguous', -- 'explicit' | 'ambiguous' | 'model_inference'
                human_review TEXT DEFAULT 'pending',       -- 'pending' | 'validated' | 'discarded'
                updated_at TEXT NOT NULL,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_profiles_case ON profiles(case_id);
            CREATE INDEX IF NOT EXISTS idx_identifiers_case ON identifiers(case_id);
            CREATE INDEX IF NOT EXISTS idx_runs_case ON case_runs(case_id);
            CREATE INDEX IF NOT EXISTS idx_media_case ON media_items(case_id);
            CREATE INDEX IF NOT EXISTS idx_posts_case ON posts(case_id);
            CREATE INDEX IF NOT EXISTS idx_comments_case ON comments(case_id);
            CREATE INDEX IF NOT EXISTS idx_interactions_case ON interactions(case_id);
            CREATE INDEX IF NOT EXISTS idx_snapshots_case ON profile_snapshots(case_id);
            CREATE INDEX IF NOT EXISTS idx_reactions_case ON reactions(case_id);
            CREATE INDEX IF NOT EXISTS idx_evidence_case ON evidence_records(case_id);
            CREATE INDEX IF NOT EXISTS idx_relational_case ON relational_signals(case_id);
            CREATE INDEX IF NOT EXISTS idx_agent_runs_case ON browser_agent_runs(case_id);
            CREATE INDEX IF NOT EXISTS idx_staged_items_run ON browser_staged_items(run_id);
            CREATE INDEX IF NOT EXISTS idx_interactants_case ON interactants(case_id);
            CREATE INDEX IF NOT EXISTS idx_friend_scan_case ON friend_scan_runs(case_id);
            CREATE INDEX IF NOT EXISTS idx_geo_hints_case ON geo_hints(case_id);
            CREATE INDEX IF NOT EXISTS idx_geo_hints_media ON geo_hints(media_id);
            CREATE INDEX IF NOT EXISTS idx_loc_hist_case ON location_history(case_id);
            CREATE INDEX IF NOT EXISTS idx_subj_prof_case ON subject_profile(case_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_started ON jobs(started_at DESC);
            """
            )
            _run_migrations(conn)
    return db_path


def _loads(raw: Optional[str], default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("JSON corrupto o inválido en SQLite (_loads): %s", exc)
        return default


def _normalize_identifier(kind: str, value: str) -> str:
    val = (value or "").strip()
    if kind == "username":
        return val.lower().lstrip("@")
    if kind in ("email", "domain"):
        return val.lower()
    if kind == "phone":
        return "".join(c for c in val if c.isdigit() or c == "+")
    return normalize_text(val)


def _http_cache_key(source: str, query: str) -> str:
    raw = f"{(source or '').strip().lower()}:{(query or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_http_cache(source: str, query: str) -> Optional[dict]:
    """Devuelve entrada vigente desde `http_cache` o None si expiró/no existe."""
    key = _http_cache_key(source, query)
    now_ts = int(time.time())
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT status_code, payload_json, created_at, expires_at FROM http_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
            if not row:
                return None
            if int(row["expires_at"]) <= now_ts:
                conn.execute("DELETE FROM http_cache WHERE cache_key = ?", (key,))
                return None
            return {
                "status_code": int(row["status_code"]),
                "payload": _loads(row["payload_json"], None),
                "created_at": int(row["created_at"]),
                "expires_at": int(row["expires_at"]),
            }
    except sqlite3.OperationalError:
        return None


def set_http_cache(source: str, query: str, status_code: int, payload, ttl_seconds: int = 1800) -> None:
    """Persiste una respuesta HTTP JSON en `http_cache` con TTL en segundos."""
    ttl = max(1, int(ttl_seconds or 1))
    key = _http_cache_key(source, query)
    now_ts = int(time.time())
    exp_ts = now_ts + ttl
    payload_str = json.dumps(payload, ensure_ascii=False)
    for attempt in range(2):
        try:
            with _connect() as conn:
                conn.execute(
                    """
                    INSERT INTO http_cache (cache_key, source, query, status_code, payload_json, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        status_code = excluded.status_code,
                        payload_json = excluded.payload_json,
                        created_at = excluded.created_at,
                        expires_at = excluded.expires_at
                    """,
                    (key, (source or "").strip().lower(), (query or "")[:500], int(status_code), payload_str, now_ts, exp_ts),
                )
            return
        except sqlite3.OperationalError:
            if attempt == 0:
                init_db()
            else:
                _LOGGER.warning("No se pudo guardar caché HTTP para (%s, %s)", source, query)


def purge_expired_http_cache() -> int:
    """Elimina registros expirados de `http_cache`."""
    now_ts = int(time.time())
    try:
        with _connect() as conn:
            cur = conn.execute("DELETE FROM http_cache WHERE expires_at <= ?", (now_ts,))
            return int(cur.rowcount or 0)
    except sqlite3.OperationalError:
        return 0


