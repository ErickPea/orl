# -*- coding: utf-8 -*-
"""
Conector oficial de la API pública de GitHub (`https://api.github.com`).
Funciona sin clave; opcionalmente usa `GITHUB_TOKEN` en `.env` para ampliar el rate-limit.
"""
import os

import requests

from modules.connectors.common import (
    USER_AGENT,
    add_interaction,
    empty_bundle,
    extract_mentions,
)

GITHUB_API = "https://api.github.com"


# Para qué sirve: extrae el nombre de usuario limpio desde un handle `@usuario` o URL de GitHub.
# Cómo funciona: remueve `@` y toma el primer segmento posterior a `github.com/` si es URL.
def _clean_user(handle: str) -> str:
    h = (handle or "").strip().lstrip("@")
    if "github.com/" in h:
        parts = [p for p in h.split("github.com/", 1)[1].split("/") if p]
        return parts[0] if parts else ""
    return h


# Para qué sirve: obtiene perfil público, repositorios técnicos y actividad colaborativa reciente en GitHub.
# Cómo funciona: consulta `/users/{user}`, `/repos` y `/events/public` usando `GITHUB_TOKEN` si está configurado.
def fetch_github(handle: str, limit: int = 20, include_replies: bool = True, timeout: int = 12) -> dict:
    """Obtiene perfil público, repositorios (temas/lenguajes) y eventos públicos colaborativos."""
    user = _clean_user(handle)
    bundle = empty_bundle("github", user)
    if not user:
        bundle["warnings"].append("Username de GitHub vacío.")
        return bundle

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # 1) Perfil público
    try:
        r_u = requests.get(f"{GITHUB_API}/users/{user}", headers=headers, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        bundle["warnings"].append(f"Error de red consultando GitHub: {exc}")
        return bundle

    if r_u.status_code == 404:
        bundle["warnings"].append(f"Usuario '{user}' no encontrado en GitHub.")
        return bundle
    if r_u.status_code != 200:
        bundle["warnings"].append(f"GitHub API devolvió HTTP {r_u.status_code} (configura GITHUB_TOKEN en .env si hay rate-limit).")
        return bundle

    u = r_u.json() or {}
    login = u.get("login") or user
    bundle["handle"] = login
    bundle["profile"] = {
        "site": "GitHub",
        "url": u.get("html_url") or f"https://github.com/{login}",
        "username": login,
        "display_name": u.get("name") or login,
        "bio": u.get("bio") or "",
        "location": u.get("location") or "",
        "avatar_url": u.get("avatar_url") or "",
        "website": u.get("blog") or "",
        "sources": "GitHub-API",
        "osint_confidence": "ALTA",
        "metadata": {
            "company": u.get("company") or "",
            "twitter_username": u.get("twitter_username") or "",
            "public_repos": u.get("public_repos"),
            "followers": u.get("followers"),
            "following": u.get("following"),
            "created_at": u.get("created_at") or "",
        },
    }

    # 2) Repositorios públicos (intereses técnicos, lenguajes y topics)
    try:
        r_repos = requests.get(
            f"{GITHUB_API}/users/{login}/repos",
            params={"sort": "updated", "per_page": max(1, min(int(limit), 30))},
            headers=headers,
            timeout=timeout,
        )
        if r_repos.status_code == 200:
            for repo in r_repos.json() or []:
                name = repo.get("name") or ""
                desc = repo.get("description") or ""
                lang = repo.get("language") or ""
                topics = [t.lower() for t in (repo.get("topics") or [])]
                if lang:
                    topics = list(dict.fromkeys([lang.lower(), *topics]))
                content = f"Repositorio: {name}" + (f" — {desc}" if desc else "") + (f" [{lang}]" if lang else "")
                bundle["posts"].append({
                    "external_id": f"repo:{repo.get('id') or name}",
                    "platform": "github",
                    "author": login.lower(),
                    "content": content,
                    "published_at": repo.get("updated_at") or repo.get("created_at") or "",
                    "url": repo.get("html_url") or "",
                    "media_urls": [],
                    "mentions": extract_mentions(desc),
                    "hashtags": topics,
                    "metadata": {
                        "language": lang,
                        "stars": repo.get("stargazers_count", 0),
                        "fork": bool(repo.get("fork")),
                    },
                })
    except Exception as exc:  # noqa: BLE001
        bundle["warnings"].append(f"No se pudieron leer repositorios públicos: {exc}")

    # 3) Eventos públicos (comentarios en issues/PRs y colaboración con otros owners)
    if include_replies:
        try:
            r_ev = requests.get(
                f"{GITHUB_API}/users/{login}/events/public",
                params={"per_page": 30},
                headers=headers,
                timeout=timeout,
            )
            if r_ev.status_code == 200:
                for ev in r_ev.json() or []:
                    etype = ev.get("type") or ""
                    repo_name = (ev.get("repo") or {}).get("name") or ""
                    owner = repo_name.split("/", 1)[0].lower() if "/" in repo_name else ""
                    payload = ev.get("payload") or {}
                    created = ev.get("created_at") or ""

                    if owner and owner != login.lower():
                        add_interaction(
                            bundle["interactions"],
                            login,
                            owner,
                            "collab",
                            weight=1.3,
                            metadata={"platform": "github", "repo": repo_name, "event": etype},
                        )

                    if etype in ("IssueCommentEvent", "PullRequestReviewCommentEvent"):
                        comment = payload.get("comment") or {}
                        body = comment.get("body") or ""
                        cid = str(comment.get("id") or ev.get("id") or "")
                        if body:
                            bundle["comments"].append({
                                "external_id": f"ghcom:{cid}",
                                "post_external_id": repo_name,
                                "author_handle": login.lower(),
                                "target_handle": owner or repo_name,
                                "content": body[:1000],
                                "published_at": comment.get("created_at") or created,
                                "metadata": {"platform": "github", "repo": repo_name, "url": comment.get("html_url") or ""},
                            })
                            for m in extract_mentions(body):
                                add_interaction(bundle["interactions"], login, m, "mention", weight=1.0, metadata={"platform": "github"})
        except Exception as exc:  # noqa: BLE001
            bundle["warnings"].append(f"No se pudieron leer eventos públicos de GitHub: {exc}")

    return bundle
