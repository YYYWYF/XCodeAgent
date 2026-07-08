from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _load_environment() -> None:
    env_file = os.getenv("XCODEAGENT_BACKEND_ENV_FILE")
    if env_file:
        load_dotenv(Path(env_file).expanduser(), override=False)
        return
    load_dotenv()


_load_environment()

_DISPLAY_MODEL_SUFFIX = re.compile(r"\s+\[[^\]]+\]\s*$")


@dataclass(frozen=True)
class Settings:
    model_base_url: str
    model_api_key: str
    model_name: str
    model_provider: str = "openai"
    model_trust_env: bool = False
    model_output_log_enabled: bool = False
    default_system_prompt: str = (
        "You are a helpful local agent. Answer clearly and concisely."
    )
    default_temperature: float = 0.2
    default_max_tokens: int = 2048

    @property
    def model_api_name(self) -> str:
        return _DISPLAY_MODEL_SUFFIX.sub("", self.model_name).strip()

    @property
    def provider_api_name(self) -> str:
        return "openai"

    @classmethod
    def from_env(cls) -> "Settings":
        base_url = _required_any("MODEL_BASE_URL", "OPENAI_BASE_URL")
        model_provider = (os.getenv("MODEL_PROVIDER", "").strip().lower() or "openai")
        if model_provider == "openai-compatible":
            model_provider = "openai"
        if model_provider != "openai":
            raise RuntimeError("Only OpenAI-compatible MODEL_PROVIDER=openai is supported.")
        return cls(
            model_base_url=base_url,
            model_api_key=_required_any("MODEL_API_KEY", "OPENAI_API_KEY"),
            model_name=_required_any("MODEL_NAME", "OPENAI_MODEL"),
            model_provider=model_provider,
            model_trust_env=_env_bool("MODEL_TRUST_ENV", default=False),
            model_output_log_enabled=_env_bool(
                "MODEL_OUTPUT_LOG_ENABLED", default=False
            ),
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


def _required_any(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    raise RuntimeError(f"Missing required environment variable: {' or '.join(names)}")


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
