# -*- coding: utf-8 -*-
"""
Adaptador de navegación supervisada para Facebook Web.
Usa selectores semánticos (`[role='main']`, `[role='article']`, enlaces de perfil) sobre
la sesión autenticada del usuario, pausando ante cualquier checkpoint o muro de login.
"""
import re
from typing import Callable

from modules.browser_adapters.base import BaseBrowserAdapter
from modules.browser_adapters.selectors import get_platform_selectors
from modules.connectors.common import extract_urls


# Para qué sirve: normaliza una URL o handle de Facebook para navegar directamente al perfil y obtener su username.
# Cómo funciona: si es URL extrae el primer segmento tras `facebook.com/`; si es alias construye `https://www.facebook.com/{uname}`.
def _normalize_fb_url(target: str) -> tuple:
    raw = (target or "").strip()
    if raw.startswith(("http://", "https://")):
        parts = [p for p in raw.split("facebook.com/", 1)[-1].split("/") if p]
        uname = parts[0].lstrip("@") if parts else "facebook_user"
        return raw, uname.lower()
    uname = raw.lstrip("@").strip("/")
    return f"https://www.facebook.com/{uname}", uname.lower()


class FacebookBrowserAdapter(BaseBrowserAdapter):
    """Adaptador DOM/ARIA supervisado para perfiles, publicaciones y comentarios en Facebook Web."""
    platform = "facebook"

    # Para qué sirve: extrae perfil, publicaciones, imágenes y comentarios visibles de Facebook Web respetando pausa y checkpoints.
    # Cómo funciona: usa perfiles de `selectors.py`, hace scroll, abre el diálogo de comentarios con telemetría y emite hallazgos en vivo.
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
        prof_url, target_user = _normalize_fb_url(target_url)
        sel_cfg = get_platform_selectors("facebook")
        prof_cfg = sel_cfg.get("profile") or {}
        posts_cfg = sel_cfg.get("posts") or {}
        cbtn_cfg = sel_cfg.get("comment_button") or {}
        com_cfg = sel_cfg.get("comments") or {}

        delay = float(limits.get("delay_seconds", 2.2))
        max_posts = int(limits.get("max_posts", 12))
        max_comments = int(limits.get("max_comments_per_post", 15))

        update_status("Abriendo perfil en Facebook...", 12, f"Navegando a {prof_url}")
        page.goto(prof_url, wait_until="domcontentloaded", timeout=30000)
        if not ctrl.human_delay(delay, delay + 1.4):
            return
        if not check_gate():
            return

        # 1) Cabecera e información pública visible del perfil
        update_status("Extrayendo información pública del perfil...", 25, "Leyendo nombre, presentación y enlaces...")
        prof_data = page.evaluate(
            """(cfg) => {
                let main = null;
                let usedMain = '';
                for (const s of (cfg.main_selectors || ['[role="main"]', 'body'])) {
                    main = document.querySelector(s);
                    if (main) { usedMain = s; break; }
                }
                main = main || document.body;
                let h1 = null;
                for (const hs of (cfg.heading_selectors || ['h1'])) {
                    h1 = main.querySelector(hs);
                    if (h1) break;
                }
                const links = Array.from(main.querySelectorAll(cfg.external_link_selector || 'a[href^="http"]'))
                    .map(a => a.href)
                    .filter(u => !u.includes('facebook.com'));
                return {
                    name: h1 ? h1.innerText.trim() : '',
                    intro: (main.innerText || '').slice(0, 700),
                    links: Array.from(new Set(links)).slice(0, 8),
                    usedMain
                };
            }""",
            prof_cfg,
        ) or {}
        if prof_data.get("usedMain") == "body":
            self.record_selector_telemetry(
                page,
                update_status,
                step_name="profile_main",
                failed_selector=(prof_cfg.get("main_selectors") or ["[role='main']"])[0],
                fallback_used="body",
            )

        bio_txt = (prof_data.get("intro") or "")[:500]
        links = prof_data.get("links") or extract_urls(bio_txt)
        emit(
            "profile",
            target_user,
            "",
            bio_txt,
            "",
            prof_url,
            {
                "site": "Facebook",
                "username": target_user,
                "display_name": prof_data.get("name") or target_user,
                "bio": bio_txt,
                "website": links[0] if links else "",
                "public_links": links,
            },
        )

        # 2) Scroll progresivo para cargar publicaciones y desplegar botones "Ver más comentarios"
        seen_posts = set()
        collected = 0
        for scroll_idx in range(6):
            if not ctrl.wait_if_paused():
                return
            if not check_gate():
                return

            # 1) Extraer publicaciones de nivel superior (excluyendo artículos anidados que son comentarios)
            articles = page.evaluate(
                """(cfg) => {
                    const artSel = cfg.article_selector || '[role="article"]';
                    const allArts = Array.from(document.querySelectorAll(artSel));
                    const topArts = allArts.filter(a => !a.parentElement || !a.parentElement.closest(artSel));
                    const msgSel = (cfg.message_selectors || ['[data-ad-preview="message"]', 'div[dir="auto"]']).join(', ');
                    const permSel = (cfg.permalink_selectors || ['a[href*="/posts/"]', 'a[href*="/permalink/"]', 'a[href*="story_fbid"]']).join(', ');
                    const imgSel = cfg.image_selector || 'img[src*="scontent"]';
                    return topArts.map((art, idx) => {
                        art.setAttribute('data-osint-fb-idx', String(idx));
                        const msgEl = art.querySelector(msgSel);
                        const text = msgEl ? (msgEl.innerText || '').trim() : (art.innerText || '').slice(0, 500);
                        const linkEl = art.querySelector(permSel);
                        const url = linkEl ? linkEl.href.split('?')[0] : '';
                        const imgs = Array.from(art.querySelectorAll(imgSel)).map(i => i.src).slice(0, 2);
                        return {idx, text: text.slice(0, 1200), url, imgs};
                    });
                }""",
                posts_cfg,
            ) or []

            for a in articles:
                if not ctrl.wait_if_paused():
                    return
                if not check_gate():
                    return
                txt = (a.get("text") or "").strip()
                if len(txt) < 5:
                    continue
                sig = (a.get("url") or txt[:80]).lower()
                if sig in seen_posts:
                    continue
                seen_posts.add(sig)
                collected += 1
                ext_id = f"fb_{collected}"
                purl = a.get("url") or prof_url

                # 2) Identificar y validar el botón de burbuja de chat para comentar (perfil declarativo)
                art_sel = f"[role='article'][data-osint-fb-idx='{a['idx']}']"
                opened_dialog = False
                dialog_sel = cbtn_cfg.get("dialog_selector") or "[role='dialog']"
                try:
                    btn_probe = page.evaluate(
                        """(args) => {
                            const { artSel, cfg } = args;
                            const art = document.querySelector(artSel);
                            if (!art) return { ok: false, strategy: '', failed: ['article_not_found'] };
                            const forbidden = new RegExp(cfg.forbidden_regex || 'me gusta|no me gusta|dislike|\\\\blike\\\\b|reacci|react|compartir|share', 'i');
                            const minW = cfg.min_width || 20;
                            const minH = cfg.min_height || 20;
                            const iconSel = (cfg.icon_selectors || ['i[data-visualcompletion="css-img"]', 'svg']).join(', ');
                            const btns = Array.from(art.querySelectorAll('[role="button"]')).filter(b => {
                                const ownerArt = b.closest('[role="article"]');
                                if (ownerArt && ownerArt !== art) return false;
                                const aria = (b.getAttribute('aria-label') || '').trim();
                                const txt = (b.innerText || '').trim();
                                if (forbidden.test(aria) || forbidden.test(txt)) return false;
                                const rect = b.getBoundingClientRect();
                                return rect.width >= minW && rect.height >= minH;
                            });

                            let target = null;
                            let strategy = '';
                            const failed = [];
                            for (const st of (cfg.strategies || [])) {
                                if (st.kind === 'child_selector') {
                                    target = btns.find(b => b.querySelector(st.selector));
                                } else if (st.kind === 'aria_regex_with_icon') {
                                    const reAria = new RegExp(st.pattern, 'i');
                                    target = btns.find(b => {
                                        const aria = (b.getAttribute('aria-label') || '').trim();
                                        const hasIcon = Boolean(b.querySelector(iconSel));
                                        return hasIcon && reAria.test(aria);
                                    });
                                }
                                if (target) {
                                    strategy = st.id;
                                    break;
                                }
                                failed.push(st.id);
                            }
                            if (!target) return { ok: false, strategy: '', failed };
                            target.scrollIntoView({ block: 'center', behavior: 'instant' });
                            target.setAttribute('data-osint-comment-target', '1');
                            return { ok: true, strategy, failed };
                        }""",
                        {"artSel": art_sel, "cfg": cbtn_cfg},
                    ) or {}
                    if btn_probe.get("failed"):
                        self.record_selector_telemetry(
                            page,
                            update_status,
                            step_name=f"comment_button_post_{collected}",
                            failed_selector=",".join(btn_probe["failed"]),
                            fallback_used=btn_probe.get("strategy") or "inline_comments",
                        )
                    if btn_probe.get("ok"):
                        com_btn = page.locator(f"{art_sel} [data-osint-comment-target='1']").first
                        com_btn.click(timeout=2000)
                        if not ctrl.human_delay(0.9, 1.5):
                            return
                        if page.locator(dialog_sel).count() > 0:
                            opened_dialog = True
                except Exception as exc:
                    self.record_selector_telemetry(
                        page,
                        update_status,
                        step_name=f"comment_click_post_{collected}",
                        failed_selector="comment_button_click",
                        fallback_used="inline_comments",
                        detail=str(exc)[:120],
                    )

                # 3) Expandir "Ver más comentarios" / "Ver respuestas" (en modal o inline, excluyendo reacciones)
                scope_sel = dialog_sel if opened_dialog else art_sel
                expand_kws = tuple(cbtn_cfg.get("expand_more_keywords") or ("ver más comentarios", "view more comments", "ver los", "ver respuestas", "view replies"))
                expand_bad = tuple(cbtn_cfg.get("expand_forbidden") or ("me gusta", "like", "dislike", "reacci", "compartir", "share"))
                try:
                    for _ in range(2):
                        more_btns = page.locator(f"{scope_sel} [role='button']")
                        clicked_more = False
                        for bi in range(min(more_btns.count(), 18)):
                            b_el = more_btns.nth(bi)
                            btxt = (b_el.inner_text(timeout=300) or "").lower()
                            baria = (b_el.get_attribute("aria-label", timeout=300) or "").lower()
                            if any(bad in btxt or bad in baria for bad in expand_bad):
                                continue
                            if any(k in btxt for k in expand_kws):
                                b_el.click(timeout=800)
                                clicked_more = True
                                if not ctrl.human_delay(0.6, 1.0):
                                    return
                                break
                        if not clicked_more:
                            break
                except Exception:
                    pass

                # 4) Extraer comentarios del modal [role="dialog"] si se abrió, o inline dentro del [role="article"]
                extracted_comments = page.evaluate(
                    """(args) => {
                        const { scopeSel, maxCom, isDialog, comCfg, artSel } = args;
                        const root = document.querySelector(scopeSel);
                        if (!root) return [];
                        const artQuery = artSel || '[role="article"]';
                        let subArts = Array.from(root.querySelectorAll(artQuery));
                        if (isDialog && subArts.length > 1) {
                            const nested = subArts.filter(sa => sa.parentElement && sa.parentElement.closest(artQuery));
                            if (nested.length) subArts = nested;
                        }
                        const comments = [];
                        const seen = new Set();
                        const replySel = (comCfg.reply_container_selectors || ['div[aria-label*="Respuesta"]', 'div[aria-label*="Reply"]']).join(', ');
                        for (const sa of subArts) {
                            if (comments.length >= maxCom) break;
                            const linkEl = sa.querySelector(comCfg.author_link || 'a[role="link"]');
                            const authorEl = sa.querySelector(comCfg.author_name || 'a[role="link"] span, strong');
                            const bodyEls = Array.from(sa.querySelectorAll(comCfg.body_selector || 'div[dir="auto"]'));
                            const auth = authorEl ? authorEl.innerText.trim() : '';
                            const authUrl = linkEl ? (linkEl.href || '').split('?')[0] : '';
                            const body = bodyEls.map(b => (b.innerText || '').trim()).filter(t => t && t !== auth).join(' ').trim();
                            const isReply = Boolean(sa.parentElement && sa.parentElement.closest(replySel));
                            if (auth && body && auth !== body) {
                                const k = auth + '::' + body.slice(0, 60);
                                if (!seen.has(k)) {
                                    seen.add(k);
                                    comments.push({
                                        author: auth,
                                        author_display_name: auth,
                                        author_profile_url: authUrl,
                                        is_reply: isReply,
                                        text: body.slice(0, 400)
                                    });
                                }
                            }
                        }
                        return comments;
                    }""",
                    {
                        "scopeSel": scope_sel,
                        "maxCom": max_comments,
                        "isDialog": opened_dialog,
                        "comCfg": com_cfg,
                        "artSel": posts_cfg.get("article_selector") or '[role="article"]',
                    },
                ) or []

                if opened_dialog:
                    try:
                        page.keyboard.press("Escape")
                        ctrl.human_delay(0.4, 0.7)
                    except Exception:
                        pass

                emit("post", target_user, "", txt, "", purl, {"external_id": ext_id, "media_urls": a.get("imgs") or []})
                for m_i, murl in enumerate(a.get("imgs") or [], start=1):
                    emit("media", target_user, "", txt[:150], "", murl, {"filename": f"{ext_id}_{m_i}.jpg", "media_type": "image", "post_external_id": ext_id})
                last_root_cid = ""
                for c_i, cm in enumerate(extracted_comments, start=1):
                    auth_slug = re.sub(r"\s+", "_", (cm.get("author") or "").strip().lower())[:45]
                    if not auth_slug or auth_slug == target_user:
                        continue
                    cid_str = f"{ext_id}_c{c_i}"
                    is_rep = bool(cm.get("is_reply"))
                    parent_cid = last_root_cid if is_rep else ""
                    if not is_rep:
                        last_root_cid = cid_str
                    norm_c = self.normalize_comment_record(
                        author_handle=auth_slug,
                        text=cm.get("text") or "",
                        author_display_name=cm.get("author_display_name") or cm.get("author") or auth_slug,
                        author_profile_url=cm.get("author_profile_url") or "",
                        is_reply=is_rep,
                        parent_comment_id=parent_cid,
                    )
                    emit(
                        "comment",
                        auth_slug,
                        target_user,
                        norm_c["text"],
                        "",
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

                if collected >= max_posts:
                    break

            pct = 35 + min(55, int((collected / max(1, max_posts)) * 55))
            update_status(f"Recorriendo muro ({collected}/{max_posts} publicaciones)...", pct, f"Scroll #{scroll_idx + 1}: {collected} publicaciones capturadas.")
            if collected >= max_posts:
                break
            page.mouse.wheel(0, 1400)
            if not ctrl.human_delay(delay, delay + 1.3):
                return
