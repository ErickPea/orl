# -*- coding: utf-8 -*-
"""
Adaptador de navegación supervisada para Instagram Web.
Usa selectores semánticos (`header`, `article`, `a[href*='/p/']`, `time`) sobre la sesión
autenticada del usuario, pausando ante cualquier checkpoint o login-wall.
"""
import re
from typing import Callable

from modules.browser_adapters.base import BaseBrowserAdapter
from modules.browser_adapters.selectors import get_platform_selectors
from modules.connectors.common import extract_urls


# Para qué sirve: normaliza una URL o handle de Instagram limpiando query params y extrayendo el username.
# Cómo funciona: recorta `?`/`#`, extrae el segmento raíz tras `instagram.com/` y construye `https://www.instagram.com/{uname}/`.
def _normalize_ig_url(target: str) -> tuple:
    raw = (target or "").strip().split("?", 1)[0].split("#", 1)[0]
    if raw.startswith(("http://", "https://")):
        parts = [p for p in raw.split("instagram.com/", 1)[-1].split("/") if p]
        uname = parts[0].lstrip("@") if parts else ""
        return f"https://www.instagram.com/{uname}/", uname.lower()
    uname = raw.lstrip("@").strip("/")
    return f"https://www.instagram.com/{uname}/", uname.lower()


class InstagramBrowserAdapter(BaseBrowserAdapter):
    """Adaptador DOM/ARIA supervisado para perfiles, grid de publicaciones y comentarios en Instagram Web."""
    platform = "instagram"

    # Para qué sirve: recolecta perfil, grid de posts, captions, imágenes y comentarios visibles en Instagram Web.
    # Cómo funciona: recorre el grid hasta `max_posts`, abre cada `/p/` o `/reel/` (soporta `resume_after_url`) y aplica fallbacks declarativos.
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
        prof_url, target_user = _normalize_ig_url(target_url)
        sel_cfg = get_platform_selectors("instagram")
        prof_cfg = sel_cfg.get("profile") or {}
        posts_cfg = sel_cfg.get("posts") or {}
        exp_cfg = sel_cfg.get("expand_comments") or {}

        delay = float(limits.get("delay_seconds", 2.0))
        max_posts = int(limits.get("max_posts", 12))
        max_comments = int(limits.get("max_comments_per_post", 15))

        update_status("Abriendo perfil en Instagram...", 12, f"Navegando a {prof_url}")
        page.goto(prof_url, wait_until="domcontentloaded", timeout=30000)
        if not ctrl.human_delay(delay, delay + 1.2):
            return
        if not check_gate():
            return

        # 1) Extraer perfil visible desde <header>
        update_status("Extrayendo datos públicos del perfil...", 24, "Leyendo cabecera y biografía visible...")
        header_data = page.evaluate(
            """(cfg) => {
                const h = document.querySelector(cfg.header_selector || 'header');
                if (!h) return { missingHeader: true };
                const h1 = h.querySelector(cfg.heading_selector || 'h1, h2');
                const img = h.querySelector(cfg.avatar_selector || 'img');
                const links = Array.from(h.querySelectorAll(cfg.links_selector || 'a[href]')).map(a => a.href);
                return {
                    missingHeader: false,
                    title: h1 ? h1.innerText.trim() : '',
                    text: (h.innerText || '').trim(),
                    avatar: img ? img.src : '',
                    links: links
                };
            }""",
            prof_cfg,
        ) or {}
        if header_data.get("missingHeader"):
            self.record_selector_telemetry(
                page,
                update_status,
                step_name="profile_header",
                failed_selector=prof_cfg.get("header_selector") or "header",
                fallback_used="empty_header",
            )

        bio_text = (header_data.get("text") or "")[:600]
        links = [u for u in (header_data.get("links") or []) if "instagram.com" not in u.lower()]
        for u in extract_urls(bio_text):
            if u not in links:
                links.append(u)

        emit(
            "profile",
            target_user,
            "",
            bio_text,
            "",
            prof_url,
            {
                "site": "Instagram",
                "username": target_user,
                "display_name": header_data.get("title") or target_user,
                "bio": bio_text,
                "avatar_url": header_data.get("avatar") or "",
                "website": links[0] if links else "",
                "public_links": links,
            },
        )

        # 2) Scroll progresivo para recolectar URLs de publicaciones (/p/ o /reel/)
        post_links = []
        seen_links = set()
        for scroll_idx in range(4):
            if not ctrl.wait_if_paused():
                return
            if not check_gate():
                return
            found = page.evaluate(
                """(linkSel) => Array.from(document.querySelectorAll(linkSel || "a[href*='/p/'], a[href*='/reel/']"))
                    .map(a => a.href.split('?')[0])""",
                posts_cfg.get("post_link_selector"),
            ) or []
            for href in found:
                if href not in seen_links:
                    seen_links.add(href)
                    post_links.append(href)
            if len(post_links) >= max_posts:
                break
            update_status(f"Explorando grid de publicaciones ({len(post_links)} encontradas)...", 32 + scroll_idx * 4, f"Scroll #{scroll_idx + 1} en el perfil...")
            page.mouse.wheel(0, 1200)
            if not ctrl.human_delay(delay, delay + 1.0):
                return

        post_links = post_links[:max_posts]
        if not post_links:
            update_status("Sin publicaciones visibles en el perfil", 90, "El perfil es privado o no tiene publicaciones visibles.")
            return

        # 3) Abrir cada publicación y extraer texto, fecha, imágenes y comentarios visibles
        resume_after = (limits.get("resume_after_url") or "").strip()
        skipping = bool(resume_after)
        total = len(post_links)
        for idx, purl in enumerate(post_links, start=1):
            if skipping:
                if purl == resume_after:
                    skipping = False
                continue
            if not ctrl.wait_if_paused():
                return
            pct = 45 + int((idx / total) * 48)
            update_status(f"Analizando publicación {idx}/{total}...", pct, f"Abriendo {purl}")
            if hasattr(ctrl, "save_cursor"):
                ctrl.save_cursor(purl)
            page.goto(purl, wait_until="domcontentloaded", timeout=25000)
            if not ctrl.human_delay(delay, delay + 1.2):
                return
            if not check_gate():
                return

            # Intentar expandir comentarios si existe botón visible ("Ver los X comentarios" / "Cargar más")
            try:
                page.evaluate(
                    """(cfg) => {
                        const btnSel = (cfg.button_selectors || ['button', 'div[role="button"]', 'span[role="button"]']).join(', ');
                        const kws = cfg.keywords || ['ver los', 'view all', 'cargar más', 'load more'];
                        const btns = Array.from(document.querySelectorAll(btnSel));
                        for (const b of btns) {
                            const t = (b.innerText || '').toLowerCase();
                            if (kws.some(k => t.includes(k))) {
                                b.click();
                                break;
                            }
                        }
                    }""",
                    exp_cfg,
                )
                page.wait_for_timeout(700)
            except Exception:
                pass

            post_data = page.evaluate(
                """(args) => {
                    const { maxCom, targetUser, cfg } = args;
                    const pCfg = cfg.posts || {};
                    let art = null;
                    for (const s of (pCfg.article_selectors || ['article', 'main', 'body'])) {
                        art = document.querySelector(s);
                        if (art) break;
                    }
                    art = art || document.body;
                    const timeEl = art.querySelector(pCfg.time_selector || 'time');
                    const pubAt = timeEl ? (timeEl.getAttribute('datetime') || timeEl.innerText || '') : '';
                    const imgs = Array.from(art.querySelectorAll(pCfg.image_selector || 'img'))
                        .map(i => ({src: i.src || '', alt: i.alt || ''}))
                        .filter(i => i.src && !i.src.includes('s150x150') && !i.src.includes('profile'));

                    const noiseSet = new Set((cfg.ui_noise_words || []).map(x => x.toLowerCase()));
                    const isUiNoise = (txt) => {
                        const s = (txt || '').trim();
                        if (!s || s.length < 2) return true;
                        const low = s.toLowerCase();
                        if (low === targetUser || noiseSet.has(low)) return true;
                        if (/^\\d+\\s*(sem|s|d|h|m|min|a|w|y)$/i.test(low)) return true;
                        if (/^\\d+\\s+me gusta$/i.test(low)) return true;
                        if (/^ver los \\d+ comentarios/i.test(low)) return true;
                        return false;
                    };

                    // Caption con registro de fallback declarativo
                    let caption = '';
                    let captionStrategy = '';
                    const failedCap = [];
                    const h1 = art.querySelector('h1');
                    if (h1 && !isUiNoise(h1.innerText)) {
                        caption = h1.innerText.trim();
                        captionStrategy = 'ig_caption_h1';
                    } else {
                        failedCap.push('ig_caption_h1');
                    }
                    if (!caption) {
                        const og = document.querySelector('meta[property="og:description"], meta[name="description"]');
                        if (og && og.content) {
                            const m = og.content.match(/on Instagram:\\s*["“]([\\s\\S]+)["”]$/i) || og.content.match(/en Instagram:\\s*["“]([\\s\\S]+)["”]$/i);
                            if (m && m[1]) {
                                caption = m[1].trim();
                                captionStrategy = 'ig_caption_og_meta';
                            }
                        }
                        if (!caption) failedCap.push('ig_caption_og_meta');
                    }
                    if (!caption) {
                        const spans = Array.from(art.querySelectorAll('span[dir="auto"]'))
                            .map(el => (el.innerText || '').trim())
                            .filter(t => !isUiNoise(t));
                        if (spans.length) {
                            caption = spans[0];
                            captionStrategy = 'ig_caption_span_auto';
                        } else {
                            failedCap.push('ig_caption_span_auto');
                        }
                    }

                    // Comentarios con estrategias declarativas (ul li -> div rows)
                    const comments = [];
                    const seenCom = new Set();
                    const reserved = new Set(cfg.reserved_paths || ['p', 'reel', 'reels', 'explore', 'accounts', 'stories', 'direct', 'tv', 'about', 'legal']);
                    const extractUserFromHref = (href) => {
                        if (!href) return '';
                        const clean = href.split('?')[0].split('#')[0].replace(/^https?:\\/\\/[^/]+/i, '');
                        const parts = clean.split('/').filter(Boolean);
                        if (parts.length !== 1) return '';
                        const u = parts[0].toLowerCase();
                        if (reserved.has(u) || u.length < 2) return '';
                        return u;
                    };

                    const pushComment = (user, text, displayName, profileUrl, isReply) => {
                        const cleanTxt = (text || '').trim();
                        if (!user || isUiNoise(cleanTxt) || cleanTxt.toLowerCase() === user) return;
                        const key = user + '::' + cleanTxt.slice(0, 80);
                        if (seenCom.has(key) || comments.length >= maxCom) return;
                        seenCom.add(key);
                        comments.push({
                            author: user,
                            author_display_name: (displayName || user).trim(),
                            author_profile_url: profileUrl || ('https://www.instagram.com/' + user + '/'),
                            is_reply: Boolean(isReply),
                            text: cleanTxt.slice(0, 500),
                        });
                    };

                    let commentStrategy = 'ig_comments_ul_li';
                    const failedComment = [];
                    for (const li of Array.from(art.querySelectorAll('ul li'))) {
                        const a = li.querySelector('a[href^="/"]');
                        const user = a ? extractUserFromHref(a.getAttribute('href')) : '';
                        if (!user) continue;
                        const textSpans = Array.from(li.querySelectorAll('span[dir="auto"]'))
                            .map(s => (s.innerText || '').trim())
                            .filter(t => !isUiNoise(t) && t.toLowerCase() !== user);
                        const txt = textSpans.length ? textSpans.join(' ') : (li.innerText || '').replace(a.innerText || '', '').trim();
                        const isReply = Boolean(li.closest('ul ul'));
                        pushComment(user, txt, (a.innerText || '').trim(), a.href || '', isReply);
                    }
                    if (!comments.length) {
                        failedComment.push('ig_comments_ul_li');
                        commentStrategy = 'ig_comments_div_rows';
                    }

                    if (comments.length < maxCom) {
                        const links = Array.from(art.querySelectorAll('a[href^="/"]'));
                        for (const a of links) {
                            const user = extractUserFromHref(a.getAttribute('href'));
                            if (!user) continue;
                            let box = a.parentElement;
                            let foundTexts = [];
                            for (let depth = 0; depth < 4 && box; depth++) {
                                const spans = Array.from(box.querySelectorAll('span[dir="auto"]'))
                                    .map(s => (s.innerText || '').trim())
                                    .filter(t => !isUiNoise(t) && t.toLowerCase() !== user && !t.includes('\\nResponder') && t.length <= 500);
                                const authorsInBox = new Set(
                                    Array.from(box.querySelectorAll('a[href^="/"]'))
                                        .map(x => extractUserFromHref(x.getAttribute('href')))
                                        .filter(Boolean)
                                );
                                if (authorsInBox.size <= 2 && spans.length) {
                                    foundTexts = spans;
                                    break;
                                }
                                box = box.parentElement;
                            }
                            if (foundTexts.length) {
                                const best = foundTexts.sort((x, y) => y.length - x.length)[0];
                                if (best && best !== caption) {
                                    const isReply = best.startsWith('@');
                                    pushComment(user, best, (a.innerText || '').trim(), a.href || '', isReply);
                                }
                            }
                        }
                    }

                    return {
                        pubAt,
                        caption: (caption || '').slice(0, 1200),
                        imgs: imgs.slice(0, 3),
                        comments,
                        telemetry: { captionStrategy, failedCap, commentStrategy, failedComment }
                    };
                }""",
                {"maxCom": max_comments, "targetUser": target_user, "cfg": sel_cfg},
            ) or {}
            tel = post_data.get("telemetry") or {}
            if tel.get("failedCap") and tel.get("captionStrategy"):
                self.record_selector_telemetry(
                    page,
                    update_status,
                    step_name=f"ig_caption_{idx}",
                    failed_selector=",".join(tel["failedCap"]),
                    fallback_used=tel["captionStrategy"],
                )
            if tel.get("failedComment") and post_data.get("comments"):
                self.record_selector_telemetry(
                    page,
                    update_status,
                    step_name=f"ig_comments_{idx}",
                    failed_selector=",".join(tel["failedComment"]),
                    fallback_used=tel.get("commentStrategy") or "ig_comments_div_rows",
                )

            ext_id = re.sub(r"[^a-zA-Z0-9_-]", "", purl.rstrip("/").split("/")[-1]) or f"ig_{idx}"
            media_urls = [im["src"] for im in (post_data.get("imgs") or []) if im.get("src")]
            emit(
                "post",
                target_user,
                "",
                post_data.get("caption") or "",
                post_data.get("pubAt") or "",
                purl,
                {"external_id": ext_id, "media_urls": media_urls},
            )
            for m_i, im in enumerate(post_data.get("imgs") or [], start=1):
                emit(
                    "media",
                    target_user,
                    "",
                    im.get("alt") or "",
                    post_data.get("pubAt") or "",
                    im["src"],
                    {"filename": f"ig_{ext_id}_{m_i}.jpg", "media_type": "image", "post_external_id": ext_id},
                )
            last_root_cid = ""
            for c_i, cm in enumerate(post_data.get("comments") or [], start=1):
                c_auth = (cm.get("author") or "").lower()
                c_text = cm.get("text") or ""
                if not c_auth or not c_text:
                    continue
                c_target = target_user
                if c_auth == target_user:
                    # Si el propio sujeto responde mencionando a alguien, registrar reciprocidad hacia el mencionado
                    mentions = re.findall(r"@([A-Za-z0-9_.]{2,30})", c_text)
                    other = next((m.lower() for m in mentions if m.lower() != target_user), "")
                    if not other:
                        continue
                    c_target = other
                cid_str = f"{ext_id}_c{c_i}"
                is_rep = bool(cm.get("is_reply"))
                parent_cid = last_root_cid if is_rep else ""
                if not is_rep:
                    last_root_cid = cid_str
                norm_c = self.normalize_comment_record(
                    author_handle=c_auth,
                    text=c_text,
                    author_display_name=cm.get("author_display_name") or c_auth,
                    author_profile_url=cm.get("author_profile_url") or f"https://www.instagram.com/{c_auth}/",
                    is_reply=is_rep,
                    parent_comment_id=parent_cid,
                )
                emit(
                    "comment",
                    c_auth,
                    c_target,
                    c_text,
                    post_data.get("pubAt") or "",
                    purl,
                    {
                        "external_id": cid_str,
                        "post_external_id": ext_id,
                        "author_display_name": norm_c["author_display_name"],
                        "author_profile_url": norm_c["author_profile_url"],
                        "is_reply": norm_c["is_reply"],
                        "parent_comment_id": norm_c["parent_comment_id"],
                    },
                )
