"""Motor de clasificación de soporte basado en Machine Learning supervisado.

Implementa un modelo independiente de los motores tradicional y generativo usando
TF-IDF (n-gramas 1 y 2) + Regresión Logística (scikit-learn), entrenado y
alimentado ("nutrido") a partir de un dataset CSV (`data/tickets.csv`).

Características de eficiencia:
- Entrenamiento separado de la inferencia (`train_ml_model` + persistencia `.joblib`).
- Carga perezosa y caché en memoria (`lru_cache`) para inferencia en milisegundos.
- Reentrenamiento incremental sencillo agregando nuevos ejemplos (`feed_dataset`).
- Cumple el contrato interno de 4 campos (`category`, `priority`, `response`,
  `requiresHumanReview`).
"""

from __future__ import annotations

import argparse
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.engines.traditional import (
    BASE_PRIORITY_BY_CATEGORY,
    RESPONSE_TEMPLATES,
    VALID_CATEGORIES,
    VALID_PRIORITIES,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_PATH = PROJECT_ROOT / "data" / "tickets.csv"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "models" / "support_ml_model.joblib"
LOW_CONFIDENCE_THRESHOLD = 0.45


class MLModelError(RuntimeError):
    """Error base controlado del motor de Machine Learning."""


class MLModelUnavailableError(MLModelError):
    """El modelo, dataset o dependencias ML no están disponibles."""


def _resolve_dataset_path(dataset_path: str | Path | None = None) -> Path:
    if dataset_path is not None:
        return Path(dataset_path)
    env_path = os.getenv("AI_SUPPORT_ML_DATASET", "").strip()
    return Path(env_path) if env_path else DEFAULT_DATASET_PATH


def _resolve_model_path(model_path: str | Path | None = None) -> Path:
    if model_path is not None:
        return Path(model_path)
    env_path = os.getenv("AI_SUPPORT_ML_MODEL", "").strip()
    return Path(env_path) if env_path else DEFAULT_MODEL_PATH


def _import_ml_deps() -> tuple[Any, ...]:
    """Importa dependencias de ML de forma diferida para no penalizar otros modos."""
    try:
        import joblib
        import pandas as pd
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score, classification_report
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import FeatureUnion, Pipeline
    except ImportError as exc:
        raise MLModelUnavailableError(
            "Faltan dependencias de Machine Learning (pandas, scikit-learn, joblib)."
        ) from exc
    return (
        joblib,
        pd,
        TfidfVectorizer,
        LogisticRegression,
        accuracy_score,
        classification_report,
        train_test_split,
        FeatureUnion,
        Pipeline,
    )


def _build_pipeline(
    TfidfVectorizer: Any,
    LogisticRegression: Any,
    FeatureUnion: Any,
    Pipeline: Any,
    random_state: int,
) -> Any:
    """Construye el pipeline TF-IDF (palabras + subpalabras) + Regresión Logística."""
    return Pipeline(
        [
            (
                "features",
                FeatureUnion(
                    [
                        (
                            "word",
                            TfidfVectorizer(
                                ngram_range=(1, 2),
                                strip_accents="unicode",
                                lowercase=True,
                                sublinear_tf=True,
                            ),
                        ),
                        (
                            "char",
                            TfidfVectorizer(
                                analyzer="char_wb",
                                ngram_range=(3, 5),
                                strip_accents="unicode",
                                lowercase=True,
                                sublinear_tf=True,
                            ),
                        ),
                    ]
                ),
            ),
            (
                "classifier",
                LogisticRegression(
                    max_iter=1000,
                    C=3.0,
                    class_weight="balanced",
                    random_state=random_state,
                ),
            ),
        ]
    )


def _load_and_validate_dataset(dataset_path: Path, pd: Any) -> Any:
    """Lee y valida el CSV de entrenamiento garantizando columnas y valores válidos."""
    if not dataset_path.is_file():
        raise MLModelUnavailableError(
            f"No se encontró el archivo de entrenamiento: {dataset_path}"
        )

    data = pd.read_csv(dataset_path)
    required_cols = {"message", "category"}
    if not required_cols.issubset(data.columns):
        raise MLModelError(
            "El CSV debe contener al menos las columnas 'message' y 'category'."
        )

    cleaned = data.copy()
    cleaned["message"] = cleaned["message"].astype(str).str.strip()
    cleaned["category"] = cleaned["category"].astype(str).str.strip().str.upper()
    cleaned = cleaned[cleaned["message"] != ""].drop_duplicates(subset=["message"])

    invalid_categories = set(cleaned["category"]) - set(VALID_CATEGORIES)
    if invalid_categories:
        raise MLModelError(
            f"Categorías inválidas en dataset: {sorted(invalid_categories)}"
        )

    has_priority_column = "priority" in cleaned.columns
    if has_priority_column:
        cleaned["priority"] = cleaned["priority"].astype(str).str.strip().str.upper()
        invalid_priorities = set(cleaned["priority"]) - set(VALID_PRIORITIES)
        if invalid_priorities:
            raise MLModelError(
                f"Prioridades inválidas en dataset: {sorted(invalid_priorities)}"
            )
    else:
        cleaned["priority"] = cleaned["category"].map(BASE_PRIORITY_BY_CATEGORY)

    if len(cleaned) < 20:
        raise MLModelError(
            "El dataset debe tener al menos 20 filas válidas para dividir 80/20."
        )
    result = cleaned.reset_index(drop=True)
    result.attrs["has_priority_column"] = has_priority_column
    return result


def clear_model_cache() -> None:
    """Invalida la caché en memoria para recargar un modelo recién reentrenado."""
    _load_model_bundle_cached.cache_clear()


def train_ml_model(
    dataset_path: str | Path | None = None,
    model_path: str | Path | None = None,
    test_size: float = 0.2,
    random_state: int = 42,
) -> dict[str, Any]:
    """Entrena el modelo TF-IDF + LogisticRegression, evalúa 80/20 y persiste `.joblib`.

    Primero evalúa sobre la partición estratificada `train_test_split` para obtener
    métricas honestas (`classification_report`), y luego entrena los pipelines finales
    con el 100% del dataset validado para maximizar cobertura en producción.
    """
    (
        joblib,
        pd,
        TfidfVectorizer,
        LogisticRegression,
        accuracy_score,
        classification_report,
        train_test_split,
        FeatureUnion,
        Pipeline,
    ) = _import_ml_deps()

    def make_pipeline() -> Any:
        return _build_pipeline(
            TfidfVectorizer, LogisticRegression, FeatureUnion, Pipeline, random_state
        )

    resolved_dataset = _resolve_dataset_path(dataset_path)
    resolved_model = _resolve_model_path(model_path)
    data = _load_and_validate_dataset(resolved_dataset, pd)

    X_train, X_test, y_cat_train, y_cat_test = train_test_split(
        data["message"],
        data["category"],
        test_size=test_size,
        stratify=data["category"],
        random_state=random_state,
    )
    eval_cat_pipeline = make_pipeline()
    eval_cat_pipeline.fit(X_train, y_cat_train)
    cat_predictions = eval_cat_pipeline.predict(X_test)
    category_accuracy = float(accuracy_score(y_cat_test, cat_predictions))
    category_report = classification_report(
        y_cat_test, cat_predictions, zero_division=0
    )

    has_priority_column = bool(data.attrs.get("has_priority_column", False))
    priority_accuracy: float | None = None
    has_stratifiable_priorities = (data["priority"].value_counts() >= 5).all()
    if has_priority_column and data["priority"].nunique() > 1 and has_stratifiable_priorities:
        Xp_train, Xp_test, yp_train, yp_test = train_test_split(
            data["message"],
            data["priority"],
            test_size=test_size,
            stratify=data["priority"],
            random_state=random_state,
        )
        eval_prio_pipeline = make_pipeline()
        eval_prio_pipeline.fit(Xp_train, yp_train)
        priority_accuracy = float(
            accuracy_score(yp_test, eval_prio_pipeline.predict(Xp_test))
        )

    final_category_pipeline = make_pipeline()
    final_category_pipeline.fit(data["message"], data["category"])

    final_priority_pipeline = None
    if has_priority_column and data["priority"].nunique() > 1:
        final_priority_pipeline = make_pipeline()
        final_priority_pipeline.fit(data["message"], data["priority"])

    metrics = {
        "dataset_rows": int(len(data)),
        "category_counts": {k: int(v) for k, v in data["category"].value_counts().items()},
        "category_accuracy": round(category_accuracy, 4),
        "priority_accuracy": round(priority_accuracy, 4)
        if priority_accuracy is not None
        else None,
        "category_report": category_report,
    }
    bundle = {
        "category_pipeline": final_category_pipeline,
        "priority_pipeline": final_priority_pipeline,
        "metrics": metrics,
    }

    resolved_model.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, resolved_model, compress=3)
    clear_model_cache()
    return metrics


def feed_dataset(
    records: list[dict[str, str]],
    dataset_path: str | Path | None = None,
    model_path: str | Path | None = None,
    retrain: bool = True,
) -> dict[str, Any]:
    """Agrega nuevos ejemplos validados al CSV para nutrir el modelo y reentrena.

    Ignora mensajes duplicados ya existentes en el dataset.
    """
    if not records:
        raise ValueError("Debe proporcionar al menos un registro para nutrir el dataset.")

    _, pd, *_ = _import_ml_deps()
    resolved_dataset = _resolve_dataset_path(dataset_path)
    existing = _load_and_validate_dataset(resolved_dataset, pd)
    seen = set(existing["message"].str.lower())

    new_rows: list[dict[str, str]] = []
    for item in records:
        msg = str(item.get("message", "")).strip()
        cat = str(item.get("category", "")).strip().upper()
        if not msg:
            raise ValueError("Cada registro debe incluir un 'message' no vacío.")
        if cat not in VALID_CATEGORIES:
            raise ValueError(f"Categoría inválida: {cat!r}")
        prio = str(item.get("priority") or BASE_PRIORITY_BY_CATEGORY[cat]).strip().upper()
        if prio not in VALID_PRIORITIES:
            raise ValueError(f"Prioridad inválida: {prio!r}")
        if msg.lower() not in seen:
            seen.add(msg.lower())
            new_rows.append({"message": msg, "category": cat, "priority": prio})

    if new_rows:
        updated = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True)
        updated.to_csv(resolved_dataset, index=False)

    metrics = (
        train_ml_model(resolved_dataset, model_path)
        if retrain and new_rows
        else None
    )
    return {
        "added_rows": len(new_rows),
        "total_rows": int(len(existing) + len(new_rows)),
        "metrics": metrics,
    }


@lru_cache(maxsize=1)
def _load_model_bundle_cached(model_path_str: str, dataset_path_str: str) -> dict[str, Any]:
    """Carga el modelo serializado una sola vez; si no existe aún, entrena desde CSV."""
    joblib, *_ = _import_ml_deps()
    model_path = Path(model_path_str)
    dataset_path = Path(dataset_path_str)

    if not model_path.is_file():
        if not dataset_path.is_file():
            raise MLModelUnavailableError(
                "No existe el modelo ML ni el archivo de entrenamiento."
            )
        train_ml_model(dataset_path=dataset_path, model_path=model_path)

    return joblib.load(model_path)


def _get_bundle(
    dataset_path: str | Path | None = None,
    model_path: str | Path | None = None,
) -> dict[str, Any]:
    return _load_model_bundle_cached(
        str(_resolve_model_path(model_path).resolve()),
        str(_resolve_dataset_path(dataset_path).resolve()),
    )


def predict_ml_details(
    message: str,
    dataset_path: str | Path | None = None,
    model_path: str | Path | None = None,
) -> dict[str, Any]:
    """Predice categoría, prioridad y confianza probabilística usando el modelo ML."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("El campo 'message' debe ser una cadena de texto no vacía.")

    cleaned = message.strip()
    bundle = _get_bundle(dataset_path=dataset_path, model_path=model_path)
    cat_pipeline = bundle["category_pipeline"]
    prio_pipeline = bundle.get("priority_pipeline")

    probabilities = cat_pipeline.predict_proba([cleaned])[0]
    classes = cat_pipeline.classes_
    best_idx = int(probabilities.argmax())
    category = str(classes[best_idx])
    confidence = float(probabilities[best_idx])

    if prio_pipeline is not None:
        priority = str(prio_pipeline.predict([cleaned])[0])
    else:
        priority = BASE_PRIORITY_BY_CATEGORY[category]

    requires_human_review = (
        category == "SECURITY"
        or priority == "CRITICAL"
        or confidence < LOW_CONFIDENCE_THRESHOLD
    )
    response = RESPONSE_TEMPLATES[category][priority]

    return {
        "category": category,
        "priority": priority,
        "response": response,
        "requiresHumanReview": requires_human_review,
        "confidence": round(confidence, 4),
    }


def analyze_ml(message: str) -> dict[str, Any]:
    """Punto de entrada compatible con el contrato interno de 4 campos del proyecto."""
    details = predict_ml_details(message)
    return {
        "category": details["category"],
        "priority": details["priority"],
        "response": details["response"],
        "requiresHumanReview": details["requiresHumanReview"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Entrena o consulta el motor de Machine Learning (TF-IDF + LogisticRegression)."
    )
    parser.add_argument("--train", action="store_true", help="Entrena y guarda el modelo desde CSV.")
    parser.add_argument("--dataset", type=Path, default=None, help="Ruta al CSV de entrenamiento.")
    parser.add_argument("--model", type=Path, default=None, help="Ruta al archivo .joblib.")
    parser.add_argument("--predict", type=str, help="Clasifica un mensaje de prueba.")
    args = parser.parse_args(argv)

    if args.train or not args.predict:
        metrics = train_ml_model(dataset_path=args.dataset, model_path=args.model)
        print(f"Dataset entrenado: {metrics['dataset_rows']} filas")
        print(f"Distribución: {metrics['category_counts']}")
        print(f"Exactitud categoría (test 20%): {metrics['category_accuracy']:.2%}")
        if metrics["priority_accuracy"] is not None:
            print(f"Exactitud prioridad (test 20%): {metrics['priority_accuracy']:.2%}")
        print("\nClassification Report (categoría):\n")
        print(metrics["category_report"])

    sample = args.predict or "Alguien entró a mi cuenta sin autorización"
    result = predict_ml_details(sample, dataset_path=args.dataset, model_path=args.model)
    print(f"Ticket de prueba: {sample}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
