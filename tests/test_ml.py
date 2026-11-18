"""Pruebas unitarias para el motor de Machine Learning (src/engines/ml.py)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.engines.ml import (
    DEFAULT_DATASET_PATH,
    MLModelError,
    MLModelUnavailableError,
    analyze_ml,
    clear_model_cache,
    feed_dataset,
    predict_ml_details,
    train_ml_model,
)
from src.engines.traditional import VALID_CATEGORIES, VALID_PRIORITIES


class TestMLDatasetIntegrity(unittest.TestCase):
    """Valida el dataset de 200 tickets que nutre al modelo ML."""

    def test_tickets_csv_has_200_unique_balanced_rows(self) -> None:
        self.assertTrue(DEFAULT_DATASET_PATH.is_file())
        df = pd.read_csv(DEFAULT_DATASET_PATH)
        self.assertEqual(len(df), 200)
        self.assertTrue({"message", "category", "priority"}.issubset(df.columns))
        self.assertEqual(df["message"].astype(str).str.strip().nunique(), 200)
        self.assertEqual(
            df["category"].value_counts().to_dict(),
            {"ACCESS": 50, "BUG": 50, "FEATURE": 50, "SECURITY": 50},
        )
        self.assertTrue(set(df["priority"]).issubset(VALID_PRIORITIES))


class TestMLTrainingAndFeeding(unittest.TestCase):
    """Valida entrenamiento 80/20, persistencia y alimentación incremental."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="ml_test_"))
        clear_model_cache()

    def tearDown(self) -> None:
        clear_model_cache()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_train_and_persist_achieves_high_category_accuracy(self) -> None:
        model_path = self.temp_dir / "model.joblib"
        metrics = train_ml_model(
            dataset_path=DEFAULT_DATASET_PATH,
            model_path=model_path,
        )
        self.assertTrue(model_path.is_file())
        self.assertEqual(metrics["dataset_rows"], 200)
        self.assertGreaterEqual(metrics["category_accuracy"], 0.85)
        self.assertIn("precision", metrics["category_report"])

    def test_two_column_csv_without_priority_trains_and_predicts(self) -> None:
        two_col_csv = self.temp_dir / "two_col.csv"
        df = pd.read_csv(DEFAULT_DATASET_PATH)[["message", "category"]]
        df.to_csv(two_col_csv, index=False)

        model_path = self.temp_dir / "two_col.joblib"
        metrics = train_ml_model(dataset_path=two_col_csv, model_path=model_path)
        self.assertIsNone(metrics["priority_accuracy"])

        res = predict_ml_details(
            "Alguien entró a mi cuenta sin autorización",
            dataset_path=two_col_csv,
            model_path=model_path,
        )
        self.assertEqual(res["category"], "SECURITY")
        self.assertIn(res["priority"], VALID_PRIORITIES)
        self.assertTrue(res["requiresHumanReview"])

    def test_feed_dataset_appends_unique_rows_skips_duplicates_and_retrains(self) -> None:
        dataset_copy = self.temp_dir / "tickets.csv"
        shutil.copy2(DEFAULT_DATASET_PATH, dataset_copy)
        model_path = self.temp_dir / "fed_model.joblib"

        train_ml_model(dataset_path=dataset_copy, model_path=model_path)
        existing_msg = pd.read_csv(dataset_copy)["message"].iloc[0]

        result = feed_dataset(
            records=[
                {"message": existing_msg, "category": "ACCESS"},  # duplicado
                {
                    "message": "Detectamos acceso anómalo mediante token SSH filtrado en logs",
                    "category": "SECURITY",
                    "priority": "CRITICAL",
                },
            ],
            dataset_path=dataset_copy,
            model_path=model_path,
        )
        self.assertEqual(result["added_rows"], 1)
        self.assertEqual(result["total_rows"], 201)
        self.assertIsNotNone(result["metrics"])
        self.assertEqual(result["metrics"]["dataset_rows"], 201)

    def test_missing_dataset_and_model_raises_unavailable(self) -> None:
        missing_csv = self.temp_dir / "missing.csv"
        missing_model = self.temp_dir / "missing.joblib"
        with self.assertRaises(MLModelUnavailableError):
            predict_ml_details(
                "No puedo iniciar sesión",
                dataset_path=missing_csv,
                model_path=missing_model,
            )

    def test_invalid_dataset_columns_raises_ml_error(self) -> None:
        bad_csv = self.temp_dir / "bad.csv"
        bad_csv.write_text("texto,etiqueta\nhola,BUG\n", encoding="utf-8")
        with self.assertRaises(MLModelError):
            train_ml_model(dataset_path=bad_csv, model_path=self.temp_dir / "m.joblib")


class TestMLInferenceContract(unittest.TestCase):
    """Valida el contrato de 4 campos, predicciones y revisión humana."""

    def test_analyze_ml_contract_and_core_predictions(self) -> None:
        cases = [
            ("Alguien entró a mi cuenta sin autorización", "SECURITY", True),
            ("No puedo iniciar sesión con mi contraseña.", "ACCESS", False),
            ("El módulo de reportes no carga y muestra una excepción.", "BUG", False),
            ("Sería útil agregar modo oscuro como nueva funcionalidad.", "FEATURE", False),
        ]
        for msg, expected_cat, must_review in cases:
            with self.subTest(msg=msg):
                res = analyze_ml(msg)
                self.assertEqual(
                    set(res.keys()),
                    {"category", "priority", "response", "requiresHumanReview"},
                )
                self.assertIn(res["category"], VALID_CATEGORIES)
                self.assertIn(res["priority"], VALID_PRIORITIES)
                self.assertIsInstance(res["response"], str)
                self.assertTrue(res["response"].strip())
                self.assertEqual(res["category"], expected_cat)
                if must_review:
                    self.assertTrue(res["requiresHumanReview"])

    def test_invalid_messages_raise_value_error(self) -> None:
        for bad in ("", "   ", None, 123):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    analyze_ml(bad)  # type: ignore[arg-type]

    def test_low_confidence_triggers_human_review(self) -> None:
        class DummyPipeline:
            classes_ = ["FEATURE", "BUG", "ACCESS", "SECURITY"]

            def predict_proba(self, _items):
                import numpy as np

                return np.array([[0.30, 0.25, 0.25, 0.20]])

        dummy_bundle = {"category_pipeline": DummyPipeline(), "priority_pipeline": None}
        with patch("src.engines.ml._get_bundle", return_value=dummy_bundle):
            details = predict_ml_details("Consulta ambigua general")
        self.assertLess(details["confidence"], 0.45)
        self.assertTrue(details["requiresHumanReview"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
