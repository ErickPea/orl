# -*- coding: utf-8 -*-
"""Pruebas DOM locales con HTML estático para los adaptadores de Facebook e Instagram."""
import ast
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.browser_adapters.selectors import get_platform_selectors  # noqa: E402


def _extract_js_evaluate_snippet(py_path: Path, marker_substring: str) -> str:
    txt = py_path.read_text(encoding="utf-8")
    idx = txt.find(marker_substring)
    if idx == -1:
        raise AssertionError(f"No se encontró {marker_substring} en {py_path}")
    m = re.search(r'"""([\s\S]*?)"""', txt[idx:])
    if not m:
        raise AssertionError(f"No se encontró bloque JS tras {marker_substring} en {py_path}")
    return ast.literal_eval('"""' + m.group(1) + '"""').strip()


# Simulador ligero de DOM en Node.js para probar los querySelectors/closest/getAttribute reales sin internet
_NODE_DOM_HARNESS = r"""
class MockElem {
  constructor(tag, attrs = {}, text = "", children = []) {
    this.tagName = tag.toUpperCase();
    this.attrs = { ...attrs };
    this._text = text;
    this.children = [];
    this.parentElement = null;
    for (const c of children) this.appendChild(c);
  }
  appendChild(c) {
    c.parentElement = this;
    this.children.push(c);
    return c;
  }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  get href() { return this.attrs.href || ""; }
  get src() { return this.attrs.src || ""; }
  get alt() { return this.attrs.alt || ""; }
  get content() { return this.attrs.content || ""; }
  get innerText() {
    if (this._text) return this._text;
    return this.children.map(c => c.innerText).filter(Boolean).join(" ").trim();
  }
  scrollIntoView() {}
  getBoundingClientRect() { return { width: 42, height: 32 }; }
  _matchesOne(sel) {
    sel = sel.trim();
    if (!sel) return false;
    const tagM = sel.match(/^([a-zA-Z0-9]+)/);
    if (tagM && this.tagName !== tagM[1].toUpperCase()) return false;
    for (const am of sel.matchAll(/\[([a-zA-Z0-9_-]+)(?:(\*|\^)?=['"]([^'"]+)['"])?\]/g)) {
      const attr = am[1], op = am[2], val = am[3];
      const cur = this.getAttribute(attr);
      if (cur === null) return false;
      if (val !== undefined) {
        if (op === "*" && !cur.includes(val)) return false;
        if (op === "^" && !cur.startsWith(val)) return false;
        if (!op && cur !== val) return false;
      }
    }
    return true;
  }
  matches(selectorGroup) {
    return selectorGroup.split(",").some(part => {
      const tokens = part.trim().split(/\s+/);
      if (!this._matchesOne(tokens[tokens.length - 1])) return false;
      let cur = this.parentElement;
      for (let i = tokens.length - 2; i >= 0; i--) {
        while (cur && !cur._matchesOne(tokens[i])) cur = cur.parentElement;
        if (!cur) return false;
        cur = cur.parentElement;
      }
      return true;
    });
  }
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => {
      for (const ch of n.children) {
        if (ch.matches(sel)) out.push(ch);
        walk(ch);
      }
    };
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  closest(sel) {
    let cur = this;
    while (cur) {
      if (cur.matches && cur.matches(sel)) return cur;
      cur = cur.parentElement;
    }
    return null;
  }
}
"""


class TestBrowserDomFixtures(unittest.TestCase):
    def _run_node_eval(self, script_body: str) -> dict:
        proc = subprocess.run(
            ["node", "-e", _NODE_DOM_HARNESS + "\n" + script_body],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return json.loads(proc.stdout.strip())

    def test_facebook_comment_button_excludes_dislike_and_extracts_comments(self):
        fb_path = ROOT / "modules" / "browser_adapters" / "facebook.py"
        btn_js = _extract_js_evaluate_snippet(fb_path, "btn_probe = page.evaluate(")
        ext_js = _extract_js_evaluate_snippet(fb_path, "extracted_comments = page.evaluate(")
        cfg = get_platform_selectors("facebook")

        script = f"""
        const btnFn = {btn_js};
        const extFn = {ext_js};
        const doc = new MockElem("body");
        const post = new MockElem("div", {{ role: "article", "data-osint-fb-idx": "0" }});
        // Botón prohibido que antes se confundía (No me gusta / Dislike)
        const dislikeBtn = new MockElem("div", {{ role: "button", "aria-label": "No me gusta" }}, "No me gusta", [new MockElem("svg")]);
        // Botón real de burbuja de chat sin data-ad-rendering-role para forzar fallback a aria-label="Comentar" + svg
        const commentBtn = new MockElem("div", {{ role: "button", "aria-label": "Comentar" }}, "12", [new MockElem("svg")]);
        // Comentario anidado dentro del post
        const commentArt = new MockElem("div", {{ role: "article" }}, "", [
          new MockElem("a", {{ role: "link", href: "https://www.facebook.com/laura.gomez" }}, "", [
            new MockElem("span", {{}}, "Laura Gómez")
          ]),
          new MockElem("div", {{ dir: "auto" }}, "Qué buena foto en Medellín!")
        ]);
        post.appendChild(dislikeBtn);
        post.appendChild(commentBtn);
        post.appendChild(commentArt);
        doc.appendChild(post);
        globalThis.document = doc;

        const probe = btnFn({{ artSel: "[role='article'][data-osint-fb-idx='0']", cfg: {json.dumps(cfg["comment_button"])} }});
        const extracted = extFn({{ scopeSel: "[role='article'][data-osint-fb-idx='0']", maxCom: 10, isDialog: false, comCfg: {json.dumps(cfg["comments"])}, artSel: "[role='article']" }});
        console.log(JSON.stringify({{
          probe,
          dislikeMarked: dislikeBtn.getAttribute("data-osint-comment-target"),
          commentMarked: commentBtn.getAttribute("data-osint-comment-target"),
          extracted
        }}));
        """
        out = self._run_node_eval(script)
        self.assertTrue(out["probe"]["ok"])
        self.assertEqual(out["probe"]["strategy"], "fb_aria_comment_with_icon")
        self.assertEqual(out["probe"]["failed"], ["fb_data_ad_comment_button"])
        self.assertIsNone(out["dislikeMarked"])
        self.assertEqual(out["commentMarked"], "1")
        self.assertEqual(len(out["extracted"]), 1)
        self.assertEqual(out["extracted"][0]["author"], "Laura Gómez")
        self.assertIn("Medellín", out["extracted"][0]["text"])

    def test_instagram_post_fallback_telemetry_and_comment_extraction(self):
        ig_path = ROOT / "modules" / "browser_adapters" / "instagram.py"
        post_js = _extract_js_evaluate_snippet(ig_path, "post_data = page.evaluate(")
        cfg = get_platform_selectors("instagram")

        script = f"""
        const postFn = {post_js};
        const doc = new MockElem("body");
        // Meta og:description cuando falta h1 -> prueba fallback + telemetría
        doc.appendChild(new MockElem("meta", {{ property: "og:description", content: 'Erick on Instagram: "Tarde de café en Bogotá"' }}));
        const art = new MockElem("article");
        art.appendChild(new MockElem("time", {{ datetime: "2026-11-10T15:00:00Z" }}, "10 nov"));
        art.appendChild(new MockElem("img", {{ src: "https://cdn.example/foto.jpg", alt: "Café y montañas" }}));
        // Fila moderna div (sin ul li) para validar fallback ig_comments_ul_li -> ig_comments_div_rows
        const row = new MockElem("div", {{}}, "", [
          new MockElem("a", {{ href: "/carlos_amigo/" }}, "Carlos Amigo"),
          new MockElem("span", {{ dir: "auto" }}, "Nos vemos en Chapinero bro")
        ]);
        art.appendChild(row);
        doc.appendChild(art);
        globalThis.document = doc;

        const data = postFn({{ maxCom: 10, targetUser: "erick144", cfg: {json.dumps(cfg)} }});
        console.log(JSON.stringify(data));
        """
        out = self._run_node_eval(script)
        self.assertEqual(out["caption"], "Tarde de café en Bogotá")
        self.assertEqual(out["telemetry"]["captionStrategy"], "ig_caption_og_meta")
        self.assertIn("ig_caption_h1", out["telemetry"]["failedCap"])
        self.assertEqual(out["telemetry"]["commentStrategy"], "ig_comments_div_rows")
        self.assertIn("ig_comments_ul_li", out["telemetry"]["failedComment"])
        self.assertEqual(len(out["comments"]), 1)
        self.assertEqual(out["comments"][0]["author"], "carlos_amigo")
        self.assertIn("Chapinero", out["comments"][0]["text"])


if __name__ == "__main__":
    unittest.main()
