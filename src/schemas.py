"""Contrato estricto compartido por la API y el benchmark (Pydantic 2)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

Mode = Literal["traditional", "generative"]
Category = Literal["ACCESS", "BUG", "FEATURE", "SECURITY"]
Priority = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
MessageText = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=10_000)
]
ResponseText = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]


class StrictModel(BaseModel):
    """Rechaza campos extra y conversiones implícitas de tipos."""

    model_config = ConfigDict(extra="forbid", strict=True)


class AnalyzeRequest(StrictModel):
    message: MessageText
    mode: Mode


class EngineResult(StrictModel):
    """Salida interna documentada de cuatro campos, sin el modo HTTP."""

    category: Category
    priority: Priority
    response: ResponseText
    requiresHumanReview: bool


class AnalyzeResponse(EngineResult):
    mode: Mode

    @model_validator(mode="after")
    def require_security_review(self) -> "AnalyzeResponse":
        if self.category == "SECURITY" and not self.requiresHumanReview:
            raise ValueError("SECURITY requiere revisión humana.")
        return self
