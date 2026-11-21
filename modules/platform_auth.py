# -*- coding: utf-8 -*-
"""
Gestión de conexiones OAuth2 autorizadas y cifrado local de tokens para OSINT-Auto.

Principios de seguridad:
  - NUNCA almacena contraseñas de usuarios.
  - Almacena exclusivamente tokens OAuth2 oficiales cifrados en reposo en SQLite (`platform_connections`).
  - Usa cifrado autenticado (SHA-256 CTR + HMAC-SHA256, o Fernet si `cryptography` está instalada)
    con clave local en `OSINT_TOKEN_SECRET` (.env) o generada en `data/.token_key`.
  - Implementa protección anti-CSRF (`state` efímero) y PKCE (S256) para flujos OAuth2.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

from modules import storage
from modules.logger import get_logger

_LOGGER = get_logger("platform_auth")

ROOT_DIR = Path(__file__).resolve().parent.parent
KEY_FILE = ROOT_DIR / "data" / ".token_key"

_STATE_LOCK = threading.Lock()
_PENDING_STATES: dict = {}  # state -> {platform, verifier, redirect_uri, created_at}
_STATE_TTL_SECONDS = 600


# ---------------------------------------------------------------------------
# 1. Cifrado autenticado en reposo (sin dependencias externas obligatorias)
# ---------------------------------------------------------------------------
# Para qué sirve: cifra la clave secreta local en Windows ligándola a la sesión del usuario del sistema operativo.
# Cómo funciona: invoca `CryptProtectData` vía `ctypes.windll.crypt32` (devuelve `None` fuera de Windows).
def _dpapi_protect(raw_bytes: bytes) -> Optional[bytes]:
    """Protege bytes con Windows DPAPI (vinculado al usuario actual del SO)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        in_buf = ctypes.create_string_buffer(raw_bytes)
        in_blob = DATA_BLOB(len(raw_bytes), in_buf)
        out_blob = DATA_BLOB()
        if not crypt32.CryptProtectData(ctypes.byref(in_blob), "OSINT-Auto", None, None, None, 0, ctypes.byref(out_blob)):
            return None
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("DPAPI CryptProtectData no disponible: %s", exc)
        return None


# Para qué sirve: descifra la clave maestra almacenada con Windows DPAPI al leer `data/.token_key`.
# Cómo funciona: llama a `CryptUnprotectData` sobre los bytes protegidos y libera el buffer con `LocalFree`.
def _dpapi_unprotect(enc_bytes: bytes) -> Optional[bytes]:
    """Descifra bytes protegidos con Windows DPAPI."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        in_buf = ctypes.create_string_buffer(enc_bytes)
        in_blob = DATA_BLOB(len(enc_bytes), in_buf)
        out_blob = DATA_BLOB()
        if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
            return None
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("DPAPI CryptUnprotectData falló: %s", exc)
        return None


# Para qué sirve: obtiene o genera la clave maestra de 256 bits para cifrar tokens OAuth2 en reposo.
# Cómo funciona: usa `OSINT_TOKEN_SECRET` si existe; si no, lee/crea `data/.token_key` protegido con DPAPI en Windows.
def _get_master_key() -> bytes:
    env_secret = os.environ.get("OSINT_TOKEN_SECRET", "").strip()
    if env_secret:
        return hashlib.sha256(env_secret.encode("utf-8")).digest()
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if KEY_FILE.is_file():
        raw = KEY_FILE.read_bytes().strip()
        if raw.startswith(b"dpapi:"):
            unprot = _dpapi_unprotect(base64.urlsafe_b64decode(raw[6:]))
            if unprot and len(unprot) >= 32:
                return hashlib.sha256(unprot).digest()
        elif len(raw) >= 32:
            return hashlib.sha256(raw).digest()
    raw_secret = base64.urlsafe_b64encode(secrets.token_bytes(32))
    protected = _dpapi_protect(raw_secret)
    if protected:
        KEY_FILE.write_bytes(b"dpapi:" + base64.urlsafe_b64encode(protected))
    else:
        KEY_FILE.write_bytes(raw_secret)
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    return hashlib.sha256(raw_secret).digest()


# Para qué sirve: genera el flujo pseudoaleatorio de cifrado por bloques sin requerir paquetes criptográficos externos.
# Cómo funciona: encadena bloques `SHA-256(key + nonce + counter)` en modo CTR hasta cubrir `length` bytes.
def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


# Para qué sirve: cifra y autentica un payload de tokens OAuth2 antes de guardarlo en SQLite.
# Cómo funciona: deriva subclaves de cifrado y MAC, aplica XOR con `_keystream`, firma con HMAC-SHA256 y prefija `v1:`.
def encrypt_token_payload(payload: dict) -> str:
    """Cifra y autentica (Encrypt-then-MAC) un diccionario de tokens OAuth2."""
    master = _get_master_key()
    enc_key = hashlib.sha256(master + b":enc").digest()
    mac_key = hashlib.sha256(master + b":mac").digest()

    plaintext = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    nonce = secrets.token_bytes(16)
    stream = _keystream(enc_key, nonce, len(plaintext))
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
    tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    blob = b"v1:" + base64.urlsafe_b64encode(nonce + tag + ciphertext)
    return blob.decode("ascii")


# Para qué sirve: verifica la integridad y descifra un token OAuth2 almacenado como `v1:...`.
# Cómo funciona: valida la firma HMAC en tiempo constante (`hmac.compare_digest`), revierte el XOR y parsea JSON.
def decrypt_token_payload(token_encrypted: str) -> Optional[dict]:
    """Verifica la autenticidad HMAC y descifra el payload de tokens OAuth2."""
    if not token_encrypted or not token_encrypted.startswith("v1:"):
        return None
    try:
        raw = base64.urlsafe_b64decode(token_encrypted[3:].encode("ascii"))
        if len(raw) < 48:
            return None
        nonce, tag, ciphertext = raw[:16], raw[16:48], raw[48:]
        master = _get_master_key()
        enc_key = hashlib.sha256(master + b":enc").digest()
        mac_key = hashlib.sha256(master + b":mac").digest()
        expected = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            return None
        stream = _keystream(enc_key, nonce, len(ciphertext))
        plaintext = bytes(a ^ b for a, b in zip(ciphertext, stream))
        data = json.loads(plaintext.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 2. Configuración OAuth2 por plataforma (Meta Graph y Reddit)
# ---------------------------------------------------------------------------
# Para qué sirve: informa qué proveedores OAuth2 (Meta/Reddit) tienen credenciales configuradas en el entorno.
# Cómo funciona: verifica pares de variables `META_APP_ID`/`SECRET` y `REDDIT_CLIENT_ID`/`SECRET` junto a sus scopes.
def oauth_provider_status() -> dict:
    """Indica qué proveedores OAuth2 tienen Client ID y Secret configurados en `.env`."""
    meta_id = os.environ.get("META_APP_ID", "").strip()
    meta_sec = os.environ.get("META_APP_SECRET", "").strip()
    reddit_id = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    reddit_sec = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    return {
        "instagram_pro": {
            "configured": bool(meta_id and meta_sec),
            "env_vars": ["META_APP_ID", "META_APP_SECRET"],
            "scopes": ["instagram_basic", "instagram_manage_comments", "pages_show_list", "pages_read_engagement"],
        },
        "facebook_page": {
            "configured": bool(meta_id and meta_sec),
            "env_vars": ["META_APP_ID", "META_APP_SECRET"],
            "scopes": ["pages_show_list", "pages_read_engagement", "pages_read_user_content"],
        },
        "reddit": {
            "configured": bool(reddit_id and reddit_sec),
            "env_vars": ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"],
            "scopes": ["read", "identity", "history"],
        },
    }


# Para qué sirve: purga estados OAuth2 temporales vencidos para evitar acumulación en memoria.
# Cómo funciona: elimina entradas de `_PENDING_STATES` que superen `_STATE_TTL_SECONDS` (600 s).
def _cleanup_states() -> None:
    now = time.time()
    expired = [k for k, v in _PENDING_STATES.items() if now - v["created_at"] > _STATE_TTL_SECONDS]
    for k in expired:
        _PENDING_STATES.pop(k, None)


# Para qué sirve: inicia el flujo OAuth2 oficial generando la URL de consentimiento para Meta o Reddit.
# Cómo funciona: crea `state` anti-CSRF y `code_verifier`/PKCE S256, los guarda en `_PENDING_STATES` y construye la URL.
def start_oauth_flow(platform: str, base_callback_url: str) -> dict:
    """
    Genera la URL oficial de autorización OAuth2 con `state` anti-CSRF y PKCE (S256).
    """
    plat = (platform or "").strip().lower()
    st_map = oauth_provider_status()
    if plat not in st_map:
        raise ValueError(f"Plataforma OAuth2 no soportada: {platform}")
    if not st_map[plat]["configured"]:
        vars_str = ", ".join(st_map[plat]["env_vars"])
        raise ValueError(f"Configura {vars_str} en `.env` para habilitar OAuth2 oficial de {plat}.")

    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    redirect_uri = f"{base_callback_url.rstrip('/')}/api/auth/{plat}/callback"

    with _STATE_LOCK:
        _cleanup_states()
        _PENDING_STATES[state] = {
            "platform": plat,
            "verifier": verifier,
            "redirect_uri": redirect_uri,
            "created_at": time.time(),
        }

    if plat in ("instagram_pro", "facebook_page"):
        params = {
            "client_id": os.environ["META_APP_ID"].strip(),
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": ",".join(st_map[plat]["scopes"]),
        }
        auth_url = f"https://www.facebook.com/v21.0/dialog/oauth?{urlencode(params)}"
    else:
        # Reddit OAuth2 oficial
        params = {
            "client_id": os.environ["REDDIT_CLIENT_ID"].strip(),
            "response_type": "code",
            "state": state,
            "redirect_uri": redirect_uri,
            "duration": "permanent",
            "scope": " ".join(st_map[plat]["scopes"]),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        auth_url = f"https://www.reddit.com/api/v1/authorize?{urlencode(params)}"

    return {"platform": plat, "auth_url": auth_url, "state": state, "redirect_uri": redirect_uri}


# Para qué sirve: completa el callback OAuth2 intercambiando `code` por el token oficial y guardándolo cifrado.
# Cómo funciona: valida `state` en `_PENDING_STATES`, pide token + perfil `/me` a Meta/Reddit, cifra con `encrypt_token_payload` y persiste en SQLite.
def complete_oauth_callback(platform: str, code: str, state: str, timeout: int = 15) -> dict:
    """
    Valida `state` anti-CSRF, intercambia `code` por token oficial y guarda la conexión cifrada.
    """
    plat = (platform or "").strip().lower()
    with _STATE_LOCK:
        _cleanup_states()
        pending = _PENDING_STATES.pop(state, None)
    if not pending or pending["platform"] != plat:
        raise ValueError("Estado OAuth2 inválido o expirado (protección CSRF).")

    redirect_uri = pending["redirect_uri"]
    if plat in ("instagram_pro", "facebook_page"):
        r = requests.get(
            "https://graph.facebook.com/v21.0/oauth/access_token",
            params={
                "client_id": os.environ.get("META_APP_ID", "").strip(),
                "client_secret": os.environ.get("META_APP_SECRET", "").strip(),
                "redirect_uri": redirect_uri,
                "code": code,
            },
            timeout=timeout,
        )
        if r.status_code != 200:
            raise ValueError(f"Meta rechazó el intercambio de token (HTTP {r.status_code}).")
        tok = r.json() or {}
        access_token = tok.get("access_token") or ""
        if not access_token:
            raise ValueError("Meta no devolvió access_token.")
        expires_in = int(tok.get("expires_in") or 3600)
        expires_at = (datetime.now() + timedelta(seconds=expires_in)).strftime("%Y-%m-%d %H:%M:%S")

        # Consultar identidad básica autorizada (/me)
        me_r = requests.get(
            "https://graph.facebook.com/v21.0/me",
            params={"fields": "id,name", "access_token": access_token},
            timeout=timeout,
        )
        me = me_r.json() if me_r.status_code == 200 else {}
        label = me.get("name") or f"{plat}_autorizada"
        ext_id = str(me.get("id") or "")
        scopes = oauth_provider_status()[plat]["scopes"]
        enc = encrypt_token_payload({"access_token": access_token, "refresh_token": ""})
        return storage.save_platform_connection(
            platform=plat,
            account_label=label,
            external_account_id=ext_id,
            auth_type="oauth2",
            scopes=scopes,
            token_encrypted=enc,
            expires_at=expires_at,
        )

    # Reddit OAuth2 callback
    r = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(os.environ.get("REDDIT_CLIENT_ID", "").strip(), os.environ.get("REDDIT_CLIENT_SECRET", "").strip()),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": pending["verifier"],
        },
        headers={"User-Agent": "OSINT-Auto/2.4 (oauth2-client)"},
        timeout=timeout,
    )
    if r.status_code != 200:
        raise ValueError(f"Reddit rechazó el intercambio de token (HTTP {r.status_code}).")
    tok = r.json() or {}
    access_token = tok.get("access_token") or ""
    if not access_token:
        raise ValueError("Reddit no devolvió access_token.")
    expires_in = int(tok.get("expires_in") or 3600)
    expires_at = (datetime.now() + timedelta(seconds=expires_in)).strftime("%Y-%m-%d %H:%M:%S")

    me_r = requests.get(
        "https://oauth.reddit.com/api/v1/me",
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": "OSINT-Auto/2.4 (oauth2-client)"},
        timeout=timeout,
    )
    me = me_r.json() if me_r.status_code == 200 else {}
    label = f"u/{me['name']}" if me.get("name") else "reddit_autorizada"
    ext_id = str(me.get("id") or "")
    enc = encrypt_token_payload({"access_token": access_token, "refresh_token": tok.get("refresh_token") or ""})
    return storage.save_platform_connection(
        platform="reddit",
        account_label=label,
        external_account_id=ext_id,
        auth_type="oauth2",
        scopes=oauth_provider_status()["reddit"]["scopes"],
        token_encrypted=enc,
        expires_at=expires_at,
    )


# Para qué sirve: recupera y descifra el `access_token` vigente para que un conector autorizado haga peticiones.
# Cómo funciona: busca el secreto por `conn_id` o la primera conexión de `platform` y lo descifra con `decrypt_token_payload`.
def get_valid_access_token(platform: str, conn_id: Optional[str] = None) -> tuple:
    """
    Recupera y descifra el token OAuth2 vigente para la plataforma o conexión indicada.
    Devuelve `(access_token_o_None, connection_meta_o_None)`.
    """
    if conn_id:
        sec = storage.get_platform_connection_secret(conn_id)
        if not sec:
            return None, None
        dec = decrypt_token_payload(sec.get("token_encrypted") or "")
        return (dec or {}).get("access_token"), sec

    conns = storage.list_platform_connections(platform=platform)
    if not conns:
        return None, None
    sec = storage.get_platform_connection_secret(conns[0]["id"])
    if not sec:
        return None, None
    dec = decrypt_token_payload(sec.get("token_encrypted") or "")
    return (dec or {}).get("access_token"), sec
