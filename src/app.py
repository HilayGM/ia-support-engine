"""API real de soporte; los motores ausentes producen 503, nunca simulaciones."""

import asyncio
import inspect
import os
import re
import unicodedata
from importlib import import_module
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from src.engines.generative import GenerativeConfigError
from src.engines.traditional import analyze_traditional
from src.schemas import AnalyzeRequest, AnalyzeResponse, EngineResult, Mode

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

app = FastAPI(title="AI Support Engine", version="0.1.0")
ENGINE_TIMEOUT_SECONDS = 30.0
DEFAULT_GENERATIVE_CALLABLE = "src.engines.generative:analyze_generative"

# Capa conservadora de revisión; no altera las predicciones de cada motor.
# Es una heurística, no una defensa exhaustiva contra prompt injection.
SECURITY_SIGNALS = re.compile(
    r"\b(?:seguridad|security|vulnerabilidad\w*|vulnerabilit\w*|phishing|"
    r"malware|ransomware|inyeccion|injection|xss|csrf|exploit|brecha|"
    r"intruso|hack\w*|jailbreak)\b|"
    r"\b(?:fuga|filtracion|robo)\s+de\s+(?:datos|credenciales|contrasenas)\b|"
    r"\b(?:credenciales|tokens?|contrasenas|datos)\s+(?:expuest\w*|filtrad\w*|robad\w*)\b|"
    r"\b(?:acceso\s+no\s+autorizado|unauthorized\s+access|data\s+breach)\b|"
    r"\b(?:ignora|ignore|olvida)\b.*\b(?:instrucciones|instructions|reglas)\b|"
    r"\bsystem\s*(?:override|:)|"
    r"\bclasifica\w*\b.*\b(?:low|baja|feature)\b|"
    r"\brequireshumanreview\s*[=:]\s*false\b",
    re.VERBOSE,
)


class EngineUnavailable(Exception):
    """El motor o su configuración todavía no están disponibles."""


def get_engine(mode: Mode) -> Callable[[str], Any]:
    if mode == "traditional":
        return analyze_traditional
    target = os.getenv("AI_SUPPORT_GENERATIVE_CALLABLE", DEFAULT_GENERATIVE_CALLABLE)
    module_name, separator, function_name = target.partition(":")
    if not separator or not module_name or not function_name.isidentifier():
        raise EngineUnavailable
    try:
        engine = getattr(import_module(module_name), function_name)
        if not callable(engine):
            raise EngineUnavailable
        return engine
    except Exception:
        # No propagar nombres de módulos, rutas, configuración ni excepciones.
        raise EngineUnavailable from None


def possible_security_incident(message: str) -> bool:
    normalized = unicodedata.normalize("NFKD", message)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    normalized = " ".join(normalized.lower().split())
    return SECURITY_SIGNALS.search(normalized) is not None


async def execute_engine(engine: Callable[[str], Any], message: str) -> Any:
    if inspect.iscoroutinefunction(engine):
        return await engine(message)
    result = await run_in_threadpool(engine, message)
    return await result if inspect.isawaitable(result) else result


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # La respuesta predeterminada puede repetir datos del ticket. Solo exponemos
    # ubicación y tipo de error, nunca input, ctx ni el cuerpo recibido.
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Solicitud inválida: revise los campos, tipos y valores permitidos.",
            "errors": [{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()],
        },
    )


@app.post(
    "/api/analyze",
    response_model=AnalyzeResponse,
    responses={
        500: {"description": "Error del motor tradicional o de su contrato."},
        502: {"description": "Error del proveedor generativo o de su contrato."},
        503: {"description": "Motor generativo no disponible o no configurado."},
        504: {"description": "Tiempo de espera del motor agotado."},
    },
)
async def analyze(payload: AnalyzeRequest) -> AnalyzeResponse:
    try:
        engine = get_engine(payload.mode)
    except EngineUnavailable:
        raise HTTPException(503, "Motor generativo no disponible o no configurado.") from None

    error_status = 502 if payload.mode == "generative" else 500
    try:
        raw = await asyncio.wait_for(
            execute_engine(engine, payload.message), timeout=ENGINE_TIMEOUT_SECONDS
        )
    except GenerativeConfigError:
        raise HTTPException(503, "Motor generativo no disponible o no configurado.") from None
    except TimeoutError:
        raise HTTPException(504, "Tiempo de espera del motor agotado.") from None
    except Exception:
        raise HTTPException(error_status, "No fue posible ejecutar el motor solicitado.") from None

    try:
        result = EngineResult.model_validate(raw)
        data = result.model_dump()
        data["requiresHumanReview"] = (
            result.requiresHumanReview
            or result.category == "SECURITY"
            or result.priority == "CRITICAL"
            or possible_security_incident(payload.message)
        )
        return AnalyzeResponse(**data, mode=payload.mode)
    except (ValidationError, TypeError, ValueError):
        raise HTTPException(error_status, "El motor devolvió una respuesta inválida.") from None
