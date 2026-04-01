"""Action schema for tool-calling agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import uuid


ALLOWED_RISK_LEVELS = {"low", "medium", "high", "critical"}


@dataclass(slots=True)
class ActionEnvelope:
    """Normalized tool action requested by the planner/LLM."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timeout_sec: float = 15.0
    retry: int = 0
    safety_level: str = "medium"

    def validate(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise ValueError("Action name must be a non-empty string")
        if not isinstance(self.args, dict):
            raise ValueError("Action args must be a dictionary")
        if self.timeout_sec <= 0 or self.timeout_sec > 300:
            raise ValueError("timeout_sec must be in range (0, 300]")
        if self.retry < 0 or self.retry > 5:
            raise ValueError("retry must be in range [0, 5]")
        if self.safety_level not in ALLOWED_RISK_LEVELS:
            raise ValueError("Invalid safety_level")


@dataclass(slots=True)
class ToolResult:
    """Structured output from tool executor."""

    tool_name: str
    trace_id: str
    ok: bool
    output: str
    error: str | None = None
    attempts: int = 1
