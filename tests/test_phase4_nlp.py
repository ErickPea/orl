# -*- coding: utf-8 -*-
"""Pruebas unitarias e integración para la Fase 4 (NLP: Intereses, NER, Sentimiento y Timeline)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import storage  # noqa: E402
from modules.connectors import parse_imported_content, sample_import_template  # noqa: E402
from modules.text_analysis import (  # noqa: E402
    analyze_case_nlp,
    analyze_sentiment,
    build_activity_timeline,
    extract_entities,
    nlp_capabilities,
)


class TestTextAnalysis(unittest.TestCase):
    def test_sentiment_and_negation(self):
        pos = analyze_sentiment("Excelente ruta en la montaña, me encanta este café!")
        neg = analyze_sentiment("Qué pésimo servicio, un problema terrible y decepcionante.")
        negated = analyze_sentiment("No me encanta ni es bueno este resultado.")
        neu = analyze_sentiment("Reunión programada para el martes en la oficina.")

        self.assertEqual(pos["label"], "POSITIVO")
        self.assertEqual(neg["label"], "CRÍTICO")
        self.assertEqual(negated["label"], "CRÍTICO")
        self.assertEqual(neu["label"], "NEUTRO")

    def test_entities_and_timeline(self):
        texts = [
            "Estudié en Universidad Nacional de Colombia en Bogotá. Mi blog: https://seguridad.co #osint @carlos",
            "Charla en Medellín sobre ciberseguridad #osint con @laura",
        ]
        ent = extract_entities(texts, profile_locations=["Bogotá, Colombia"])
        place_names = {p["name"] for p in ent["places"]}
        org_names = {o["name"] for o in ent["organizations"]}
        dom_names = {d["name"] for d in ent["domains"]}

        self.assertIn("Bogotá", place_names)
        self.assertIn("Medellín", place_names)
        self.assertTrue(any("Universidad Nacional" in o for o in org_names))
        self.assertIn("seguridad.co", dom_names)

        tl = build_activity_timeline([
            "2026-10-15T09:30:00Z",
            "2026-10-15T21:15:00Z",
            "2026-10-16 22:00:00",
        ])
        self.assertEqual(tl["dated_items"], 3)
        self.assertEqual(tl["peak_slot"], "Noche (18-23)")

    def test_subject_vs_others_and_multimodal_fusion(self):
        corpus = {
            "subject_handles": ["ana_dev"],
            "bios": [{"site": "GitHub", "text": "Ingeniera en ciberseguridad y privacidad. Amo el ciclismo.", "location": "Bogotá"}],
            "posts": [
                {
                    "id": 1,
                    "platform": "bluesky",
                    "author": "ana_dev",
                    "content": "Excelente entrenamiento de ciclismo en la montaña hoy #ciclismo",
                    "published_at": "2026-10-10T07:30:00Z",
                }
            ],
            "comments": [
                {
                    "id": 10,
                    "author_handle": "tercero_critico",
                    "target_handle": "ana_dev",
                    "content": "Qué terrible clima, horrible lluvia y mal camino.",
                    "published_at": "2026-10-10T08:00:00Z",
                },
                {
                    "id": 11,
                    "author_handle": "ana_dev",
                    "target_handle": "tercero_critico",
                    "content": "Gracias por avisar, pero estuvo genial igual!",
                    "published_at": "2026-10-10T08:10:00Z",
                },
            ],
            "vision_themes": [
                {"label": "Deportes y Fitness", "count": 2, "evidences": ["Bicicleta de ruta detectada"]}
            ],
        }

        res = analyze_case_nlp(corpus, provider="local")
        self.assertEqual(res["sentiment"]["subject"]["dominant"], "POSITIVO")
        self.assertEqual(res["sentiment"]["others"]["dominant"], "CRÍTICO")
        self.assertEqual(res["comment_sentiments"][10], "CRÍTICO")
        self.assertEqual(res["comment_sentiments"][11], "POSITIVO")

        by_label = {i["label"]: i for i in res["unified_interests"]}
        self.assertIn("Deportes y Fitness", by_label)
        self.assertEqual(by_label["Deportes y Fitness"]["modality"], "Texto + Fotos")
        self.assertEqual(by_label["Deportes y Fitness"]["confidence"], "ALTA")
        self.assertGreaterEqual(len(by_label["Deportes y Fitness"]["evidence_quotes"]), 2)


class TestNlpStorageAndApi(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "phase4.db"
        storage.set_db_path(self.db_path)
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_nlp_persistence_updates_comments_sentiment_and_api(self):
        detail = storage.create_case(title="Caso NLP F4", subject_name="Mi Usuario", seeds={"username": "mi_usuario"})
        cid = detail["case"]["id"]

        bundle = parse_imported_content(json.dumps(sample_import_template()).encode("utf-8"), "export.json")
        storage.ingest_connector_bundle(cid, bundle)

        corpus = storage.get_case_corpus_for_nlp(cid)
        nlp_res = analyze_case_nlp(corpus, provider="local")
        self.assertTrue(storage.save_case_nlp_analysis(cid, nlp_res))

        # Verificar que `comments.sentiment` quedó poblado para Fase 5
        content = storage.get_case_content(cid)
        self.assertTrue(all(cm["sentiment"] in ("POSITIVO", "NEUTRO", "CRÍTICO") for cm in content["comments"]))

        import web_app
        self.assertTrue(nlp_capabilities(probe_ollama=False)["local_lexicon"])
        api_nlp = web_app.api_case_nlp_get(cid)
        self.assertTrue(api_nlp["analyzed"])
        self.assertGreaterEqual(api_nlp["counts"]["interests_count"], 1)


if __name__ == "__main__":
    unittest.main()
