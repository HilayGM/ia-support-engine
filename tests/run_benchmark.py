"""Benchmark reproducible de la API: local en proceso o contra un servidor real.

No instala mocks. Los fallos permanecen en el denominador de todas las métricas.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any, Iterator, Literal

import httpx
from pydantic import StringConstraints, ValidationError, model_validator

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.schemas import (  # noqa: E402 - admite ejecución directa y como módulo
    AnalyzeResponse,
    Category,
    MessageText,
    Mode,
    Priority,
    ResponseText,
    StrictModel,
)

MODES: tuple[Mode, ...] = ("traditional", "generative")
DEFAULT_DATASET = Path(__file__).with_name("test_cases.json")


class TestCase(StrictModel):
    id: Annotated[str, StringConstraints(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]
    case_type: Literal["normal", "ambiguous", "prompt_injection"]
    message: MessageText
    expected_category: Category
    expected_priority: Priority
    expected_human_review: bool
    justification: ResponseText

    @model_validator(mode="after")
    def validate_security_reference(self) -> "TestCase":
        if self.expected_category == "SECURITY" and not self.expected_human_review:
            raise ValueError("Los incidentes de seguridad requieren revisión humana.")
        if self.case_type == "prompt_injection" and (
            self.expected_category != "SECURITY" or not self.expected_human_review
        ):
            raise ValueError("Los intentos de manipulación son casos de seguridad.")
        return self


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("El JSON contiene claves duplicadas.")
        result[key] = value
    return result


def load_dataset(path: Path) -> list[TestCase]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_object)
    if not isinstance(raw, list) or len(raw) < 10:
        raise ValueError("Se requieren al menos 10 casos en una lista JSON.")
    cases = [TestCase.model_validate(item) for item in raw]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("Los identificadores deben ser únicos.")
    counts = Counter(case.case_type for case in cases)
    if counts["normal"] < 4 or counts["ambiguous"] < 4 or counts["prompt_injection"] < 2:
        raise ValueError("Se requieren 4 normales, 4 ambiguos y 2 prompt injections como mínimo.")
    normal_categories = {case.expected_category for case in cases if case.case_type == "normal"}
    if normal_categories != {"ACCESS", "BUG", "FEATURE", "SECURITY"}:
        raise ValueError("Los casos normales deben cubrir las cuatro categorías.")
    return cases


@dataclass
class Observation:
    case_id: str
    repetition: int
    mode: Mode
    status: str
    http_status: int | None = None
    result: dict[str, Any] | None = None
    category_correct: bool = False
    priority_correct: bool = False
    joint_correct: bool = False
    review_correct: bool = False
    valid_format: bool = False


def evaluate(client: Any, case: TestCase, mode: Mode, repetition: int) -> Observation:
    observation = Observation(case.id, repetition, mode, "error")
    try:
        response = client.post("/api/analyze", json={"message": case.message, "mode": mode})
    except httpx.TimeoutException:
        observation.status = "timeout"
        return observation
    except httpx.TransportError:
        observation.status = "no_disponible"
        return observation
    except Exception:
        # Tampoco imprimir excepciones del servidor en pruebas locales.
        return observation
    observation.http_status = response.status_code
    if response.status_code != 200:
        observation.status = {503: "no_disponible", 504: "timeout"}.get(response.status_code, "error_http")
        return observation
    try:
        raw = json.loads(response.text, object_pairs_hook=unique_object)
        result = AnalyzeResponse.model_validate(raw)
        if result.mode != mode:
            raise ValueError("El modo devuelto no coincide con el solicitado.")
    except (ValidationError, ValueError, TypeError):
        observation.status = "formato_invalido"
        return observation
    observation.status = "ok"
    observation.result = result.model_dump()
    observation.valid_format = True
    observation.category_correct = result.category == case.expected_category
    observation.priority_correct = result.priority == case.expected_priority
    observation.joint_correct = observation.category_correct and observation.priority_correct
    observation.review_correct = result.requiresHumanReview == case.expected_human_review
    return observation


def summarize(observations: list[Observation]) -> dict[str, dict[str, Any]]:
    summary = {}
    for mode in MODES:
        rows = [row for row in observations if row.mode == mode]
        total = len(rows)
        metrics: dict[str, Any] = {
            "evaluated": total,
            "failures": sum(not row.valid_format for row in rows),
            "unavailable": sum(row.status == "no_disponible" for row in rows),
        }
        for field in ("category_correct", "priority_correct", "joint_correct", "valid_format", "review_correct"):
            metrics[field] = 100 * sum(getattr(row, field) for row in rows) / total if total else 0.0
        summary[mode] = metrics
    return summary


def markdown_report(cases: list[TestCase], observations: list[Observation]) -> str:
    references = {case.id: case for case in cases}
    lines = [
        "| Caso | Repetición | Motor | Referencia cat./prior. | Obtenido cat./prior. | Revisión | Estado HTTP | Formato | Acierto cat./prior./conjunto |",
        "| --- | ---: | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in observations:
        case = references[row.case_id]
        result = row.result or {}
        prediction = f"{result.get('category', '-')}/{result.get('priority', '-')}"
        hits = "/".join("sí" if hit else "no" for hit in (row.category_correct, row.priority_correct, row.joint_correct))
        review = str(result.get("requiresHumanReview", "-")).lower()
        lines.append(
            f"| {row.case_id} | {row.repetition} | {row.mode} | {case.expected_category}/{case.expected_priority} "
            f"| {prediction} | {review} | {row.status} ({row.http_status or '-'}) "
            f"| {'sí' if row.valid_format else 'no'} | {hits} |"
        )
    lines.extend([
        "",
        "| Motor | Evaluados | Fallos | No disponibles | Categoría | Prioridad | Conjunta | Formato válido | Revisión humana |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for mode, metrics in summarize(observations).items():
        percentages = " | ".join(
            f"{metrics[key]:.2f}%" for key in
            ("category_correct", "priority_correct", "joint_correct", "valid_format", "review_correct")
        )
        lines.append(
            f"| {mode} | {metrics['evaluated']} | {metrics['failures']} | {metrics['unavailable']} | {percentages} |"
        )
    lines.extend([
        "",
        "Denominador por motor = casos × repeticiones; cada fallo aporta cero aciertos a todas las métricas.",
        "Un 0% con el motor no disponible mide disponibilidad de la ejecución, no capacidad predictiva del modelo.",
        "La revisión humana es una métrica adicional; no interviene en la exactitud conjunta.",
    ])
    return "\n".join(lines)


@contextmanager
def api_client(base_url: str | None, timeout: float) -> Iterator[Any]:
    if base_url:
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, follow_redirects=False) as client:
            yield client
    else:
        from fastapi.testclient import TestClient
        from src.app import app

        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--base-url", help="Servidor real, por ejemplo http://127.0.0.1:8000; por defecto API en proceso.")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=40.0, help="Timeout HTTP remoto en segundos.")
    parser.add_argument("--json", action="store_true", help="Imprime resultados y resumen completos como JSON.")
    args = parser.parse_args(argv)
    if args.repeat < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--repeat debe ser >= 1 y --timeout debe ser positivo y finito.")
    if args.base_url:
        try:
            url = httpx.URL(args.base_url)
        except httpx.InvalidURL:
            parser.error("--base-url debe ser una URL HTTP(S) válida.")
        if url.scheme not in ("http", "https") or not url.host or url.path not in ("", "/") or url.query or url.fragment or url.userinfo:
            parser.error("--base-url debe ser un origen HTTP(S), sin ruta, consulta ni fragmento.")
    try:
        cases = load_dataset(args.dataset)
    except (OSError, UnicodeError, ValueError, ValidationError):
        parser.error("Dataset inválido: revise el JSON, campos, tipos, IDs, referencias y distribución de casos.")
    observations = []
    with api_client(args.base_url, args.timeout) as client:
        for repetition in range(1, args.repeat + 1):
            for case in cases:
                for mode in MODES:
                    observations.append(evaluate(client, case, mode, repetition))
    if args.json:
        print(json.dumps({"results": [asdict(row) for row in observations], "summary": summarize(observations)}, ensure_ascii=False, indent=2))
    else:
        print(markdown_report(cases, observations))
    # Una predicción errónea es una medición válida. Los errores operativos sí
    # producen salida 1 para hacer visibles las ejecuciones incompletas en CI.
    return int(any(not row.valid_format for row in observations))


if __name__ == "__main__":
    raise SystemExit(main())
