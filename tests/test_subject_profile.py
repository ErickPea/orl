# -*- coding: utf-8 -*-
"""Pruebas para Fase 4: Perfil enriquecido del sujeto (7 categorías, epistemic status y human-in-the-loop)."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import web_app  # noqa: E402
from modules import storage  # noqa: E402
from modules.connectors.common import empty_bundle  # noqa: E402
from modules.subject_profile import compute_subject_profile  # noqa: E402


class TestSubjectProfile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        storage.set_db_path(Path(self._tmp.name) / "subj_prof.db")
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_all_seven_categories_epistemic_rules_and_human_review(self):
        cid = storage.create_case(title="Caso Perfil Sujeto", subject_name="Erick Peña")["case"]["id"]
        b = empty_bundle("instagram", "erick144", access_method="browser_supervised")
        b["profile"] = {
            "site": "Instagram",
            "url": "https://instagram.com/erick144/",
            "username": "erick144",
            "display_name": "Erick Peña",
            "bio": "Ingeniero de software en Bogotá. Amante de la fotografía y el ciclismo.",
            "location": "Bogotá",
        }
        b["posts"].append({
            "external_id": "p1",
            "author": "erick144",
            "content": "Saliendo a correr en bici #ciclismo #tecnologia. Feliz aniversario mi esposa!",
            "published_at": "2026-11-18 09:00:00",
        })
        b["interactions"].extend([
            {"source_handle": "laura_amiga", "target_handle": "erick144", "kind": "comment", "weight": 2.0, "evidence_count": 2},
            {"source_handle": "erick144", "target_handle": "laura_amiga", "kind": "reply", "weight": 2.0, "evidence_count": 1},
        ])
        storage.ingest_connector_bundle(cid, b)
        from modules.text_analysis import analyze_case_nlp
        corpus = storage.get_case_corpus_for_nlp(cid)
        storage.save_case_nlp_analysis(cid, analyze_case_nlp(corpus, provider="local"))

        prof = compute_subject_profile(cid, persist=True)
        cats = set(prof["by_category"].keys())
        for expected in ("hobbies", "places", "friends", "ideas", "personality", "occupation", "relationship_status"):
            self.assertIn(expected, cats)

        # Regla dura: `ideas` nunca se marca `explicit` sin cita literal
        for idea in prof["by_category"]["ideas"]:
            self.assertIn(idea["epistemic_status"], ("ambiguous", "model_inference"))

        # Human-in-the-loop: validar / ambiguo / descartar y persistir al recalcular
        first_entry = prof["entries"][0]
        upd = web_app.api_case_subject_profile_review(
            cid,
            first_entry["id"],
            web_app.SubjectProfileReviewRequest(human_review="validated"),
        )
        self.assertEqual(upd["entry"]["human_review"], "validated")
        if len(prof["entries"]) > 1:
            amb = web_app.api_case_subject_profile_review(
                cid,
                prof["entries"][1]["id"],
                web_app.SubjectProfileReviewRequest(human_review="ambiguous"),
            )
            self.assertEqual(amb["entry"]["human_review"], "ambiguous")

        # Recalcular preserva la revisión humana
        recomp = compute_subject_profile(cid, persist=True)
        matched = [e for e in recomp["entries"] if e["category"] == first_entry["category"] and e["value"] == first_entry["value"]]
        self.assertEqual(matched[0]["human_review"], "validated")


if __name__ == "__main__":
    unittest.main()
