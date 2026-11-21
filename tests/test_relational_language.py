# -*- coding: utf-8 -*-
"""Pruebas unitarias e integración para el Motor de Lenguaje Relacional Contextual."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import storage  # noqa: E402
from modules.account_investigator import run_case_relational_analysis  # noqa: E402
from modules.relational_language import (  # noqa: E402
    analyze_case_relational_language,
    analyze_single_text_relational,
)


class TestRelationalLanguageRules(unittest.TestCase):
    def test_explicit_family_and_partner_expressions(self):
        s_fam = analyze_single_text_relational(
            "Feliz cumpleaños mi hermano mayor @carlos, orgulloso de todo lo que logras",
            source_handle="erick",
            target_handle="carlos",
            platform="reddit",
            has_reciprocal_history=True,
        )
        self.assertTrue(any(s["category"] == "familia" and s["epistemic_type"] == "explicit" for s in s_fam))

        s_par = analyze_single_text_relational(
            "Feliz aniversario mi esposa @laura, 5 años increíbles juntos",
            source_handle="erick",
            target_handle="laura",
            platform="bluesky",
        )
        self.assertTrue(any(s["category"] == "pareja" and s["epistemic_type"] == "explicit" and s["confidence"] == "ALTA" for s in s_par))

    def test_colloquial_sarcasm_third_person_and_emoji_are_strictly_ambiguous(self):
        # 1) Coloquial + risa
        s1 = analyze_single_text_relational("Buena esa bro 😂", source_handle="a", target_handle="b")
        self.assertTrue(s1)
        self.assertTrue(all(s["epistemic_type"] == "ambiguous" for s in s1))
        self.assertIn("posible_sarcasmo_o_humor", s1[0]["ambiguity_flags"])

        # 2) Tercera persona no dirigida
        s2 = analyze_single_text_relational("El primo de un amigo compró esa moto", source_handle="a", target_handle="b")
        self.assertTrue(s2)
        self.assertTrue(all(s["epistemic_type"] == "ambiguous" for s in s2))
        self.assertIn("tercera_persona_no_dirigida", s2[0]["ambiguity_flags"])

        # 3) Sarcasmo explícito /s
        s3 = analyze_single_text_relational("Sí claro, mi amor, como digas /s", source_handle="a", target_handle="b")
        self.assertTrue(s3)
        self.assertTrue(all(s["epistemic_type"] == "ambiguous" for s in s3))

        # 4) Emoji afectivo aislado
        s4 = analyze_single_text_relational("❤️❤️", source_handle="a", target_handle="b")
        self.assertTrue(s4)
        self.assertEqual(s4[0]["epistemic_type"], "ambiguous")
        self.assertIn("emoji_sin_texto_explicito", s4[0]["ambiguity_flags"])

    def test_regionalisms_diminutives_and_typos(self):
        s_reg = analyze_single_text_relational("Qué más prcero, todo bien pana?", source_handle="a", target_handle="b")
        self.assertTrue(any(s["category"] == "amistad" for s in s_reg))
        self.assertTrue(all(s["epistemic_type"] == "ambiguous" for s in s_reg))

        s_typo = analyze_single_text_relational("Gracias hrmnooo por la ayuda amigoooo", source_handle="a", target_handle="b")
        cats = {s["category"] for s in s_typo}
        self.assertIn("familia", cats)
        self.assertIn("amistad", cats)

        s_dim = analyze_single_text_relational("Te quiero mucho abuelita hermosa", source_handle="a", target_handle="b")
        self.assertTrue(any(s["category"] == "familia" and s["subcategory"] == "abuelo_a" for s in s_dim))

    @patch("modules.relational_language._call_relational_llm")
    def test_hybrid_llm_marks_as_model_inference(self, mock_llm):
        mock_llm.return_value = (
            {
                "items": [
                    {
                        "index": 0,
                        "verdict": "likely_relation",
                        "category": "amistad",
                        "confidence": "MEDIA",
                        "reason": "Trato cercano frecuente en tono amistoso",
                    }
                ]
            },
            "gemini",
        )
        sigs = analyze_case_relational_language(
            comments=[{"id": 1, "author_handle": "juan", "target_handle": "erick", "content": "Nos vemos el sábado parcero", "platform": "reddit"}],
            posts=[],
            provider="gemini",
        )
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0]["epistemic_type"], "model_inference")
        self.assertEqual(sigs[0]["engine"], "gemini")


class TestRelationalHumanReviewAndSna(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        storage.set_db_path(Path(self._tmp.name) / "rel.db")
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_human_review_preserved_and_reflected_in_sna(self):
        detail = storage.create_case(title="Caso Relacional", subject_name="Erick", seeds={"username": "erick"})
        cid = detail["case"]["id"]

        storage.ingest_connector_bundle(
            cid,
            {
                "platform": "bluesky",
                "handle": "erick",
                "access_method": "public_api",
                "posts": [],
                "comments": [
                    {
                        "external_id": "c1",
                        "post_external_id": "",
                        "author_handle": "carlos",
                        "target_handle": "erick",
                        "content": "Feliz cumpleaños mi hermano mayor!",
                        "published_at": "2026-11-10 10:00:00",
                        "metadata": {"platform": "bluesky", "url": "https://bsky.app/post/1"},
                    },
                    {
                        "external_id": "c2",
                        "post_external_id": "",
                        "author_handle": "troll",
                        "target_handle": "erick",
                        "content": "Claro que sí bro 😂",
                        "published_at": "2026-11-11 11:00:00",
                        "metadata": {"platform": "bluesky", "url": "https://bsky.app/post/2"},
                    },
                ],
                "interactions": [
                    {"source_handle": "carlos", "target_handle": "erick", "kind": "comment", "weight": 1.6, "evidence_count": 1},
                    {"source_handle": "erick", "target_handle": "carlos", "kind": "reply", "weight": 1.8, "evidence_count": 1},
                    {"source_handle": "troll", "target_handle": "erick", "kind": "comment", "weight": 1.6, "evidence_count": 1},
                ],
                "media_items": [],
                "reactions": [],
                "warnings": [],
            },
        )

        out = run_case_relational_analysis(cid, provider="local")
        self.assertEqual(out["stats"]["total"], 2)
        by_src = {s["source_handle"]: s for s in out["signals"]}

        # Validar señal de Carlos y descartar la de troll
        storage.update_relational_signal_review(cid, by_src["carlos"]["id"], "validated", reviewer_note="Confirmado por contexto")
        storage.update_relational_signal_review(cid, by_src["troll"]["id"], "dismissed", reviewer_note="Uso coloquial")

        # Re-ejecutar análisis debe preservar las revisiones humanas
        out2 = run_case_relational_analysis(cid, provider="local")
        self.assertEqual(out2["save_stats"]["preserved_reviewed"], 2)

        # SNA debe incluir `relational_cues` en Carlos y omitir la señal descartada de troll
        sna = storage.compute_and_save_case_sna(cid)
        nodes_by_id = {u["id"]: u for u in sna["close_circle"]}
        self.assertEqual(nodes_by_id["carlos"]["relational_cues"]["validated_count"], 1)
        self.assertEqual(nodes_by_id["carlos"]["label"], "@carlos")  # NUNCA renombra automáticamente el nodo
        self.assertEqual(nodes_by_id["troll"]["relational_cues"]["total_signals"], 0)


if __name__ == "__main__":
    unittest.main()
