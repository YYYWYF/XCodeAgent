from __future__ import annotations

import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()

_DISPLAY_MODEL_SUFFIX = re.compile(r"\s+\[[^\]]+\]\s*$")


@dataclass(frozen=True)
class Settings:
    anthropic_base_url: str
    anthropic_auth_token: str
    anthropic_model: str
    anthropic_trust_env: bool = False
    default_system_prompt: str = "You are a helpful local agent. Answer clearly and concisely."
    default_temperature: float = 0.2
    default_max_tokens: int = 2048

    @property
    def anthropic_api_model(self) -> str:
        return _DISPLAY_MODEL_SUFFIX.sub("", self.anthropic_model).strip()

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            anthropic_base_url=_required("ANTHROPIC_BASE_URL"),
            anthropic_auth_token=_required("ANTHROPIC_AUTH_TOKEN"),
            anthropic_model=_required("ANTHROPIC_MODEL"),
            anthropic_trust_env=_env_bool("ANTHROPIC_TRUST_ENV", default=False),
            default_system_prompt=os.getenv(
                "AGENT_SYSTEM_PROMPT",
                "You are a helpful local agent. Answer clearly and concisely.",
            ),
            default_temperature=float(os.getenv("AGENT_TEMPERATURE", "0.2")),
            default_max_tokens=int(os.getenv("AGENT_MAX_TOKENS", "2048")),
        )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
