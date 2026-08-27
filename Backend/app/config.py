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
    model_timeout_seconds: float = 120.0
    model_max_retries: int = 2
    default_system_prompt: str = (
        "You are a helpful local agent. Answer clearly and concisely."
    )
    default_temperature: float = 0.2
    default_max_tokens: int = 2048
    # 完整技术规划与定向 Contract 修复使用独立输出预算，不影响其他 Agent。
    technical_plan_max_tokens: int = 32768
    # UI 确认节点生成 React 设计稿的生成 token 上限。推理模型（如 glm-5.2）的
    # 思考过程与正文共用该预算，且网关会把 thinking 以 [{'thinking': ..}] 碎片
    # 形式逐 token 拼进 content——16384 时思考可吃掉大部分预算导致正文在
    # `export default` 前被截断，故提到 32768 给足余量。
    ui_design_max_tokens: int = 32768
    # UI 设计稿生成后校验失败时的自动修复重试次数（回喂错误给 LLM 让其修正）。
    # 默认 1：外层校验重试 1 次 + 内层 API 重试 1 次，最坏 4 次 LLM 调用，
    # 单页最坏约 1 分钟内出结果；校验仍不过则标记 generation_failed 让用户手动重试，
    # 不长时间卡住。如需更激进的自动修复可调高（注意最坏调用数 = (n+1)^2）。
    ui_design_max_retries: int = 1
    # UI 设计稿并发生成 worker 数量：解耦式生成池（ui_design_generation_pool）同时
    # 调用 LLM 生成设计稿的并发上限。默认 3，避免单 API Key 触发模型服务限流。
    ui_design_concurrency: int = 3
    build_task_plan_max_retries: int = 2
    dag_business_self_check_enabled: bool = False
    checkpoint_db_path: str = ""  # populated in from_env
    checkpoint_retention_days: int = 30
    langsmith_tracing_enabled: bool = False
    langsmith_project: str = ""
    langsmith_endpoint: str = ""

    @property
    def model_api_name(self) -> str:
        return _DISPLAY_MODEL_SUFFIX.sub("", self.model_name).strip()

    @property
    def provider_api_name(self) -> str:
        return "openai"

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量加载模型、技术规划、UI 设计生成和持久化配置。"""

        base_url = _required_any("MODEL_BASE_URL", "OPENAI_BASE_URL")
        try:
            technical_plan_max_tokens = int(
                os.getenv("XCODEAGENT_TECHNICAL_PLAN_MAX_TOKENS", "32768")
            )
        except ValueError:
            raise ValueError("XCODEAGENT_TECHNICAL_PLAN_MAX_TOKENS 必须是正整数。") from None
        if technical_plan_max_tokens <= 0:
            raise ValueError("XCODEAGENT_TECHNICAL_PLAN_MAX_TOKENS 必须是正整数。")
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
            model_timeout_seconds=float(
                os.getenv("MODEL_TIMEOUT_SECONDS", "120.0")
            ),
            model_max_retries=int(os.getenv("MODEL_MAX_RETRIES", "2")),
            default_system_prompt=os.getenv(
                "AGENT_SYSTEM_PROMPT",
                "You are a helpful local agent. Answer clearly and concisely.",
            ),
            default_temperature=float(os.getenv("AGENT_TEMPERATURE", "0.2")),
            default_max_tokens=int(os.getenv("AGENT_MAX_TOKENS", "2048")),
            technical_plan_max_tokens=technical_plan_max_tokens,
            ui_design_max_tokens=int(
                os.getenv("XCODEAGENT_UI_DESIGN_MAX_TOKENS", "32768")
            ),
            ui_design_max_retries=int(
                os.getenv("XCODEAGENT_UI_DESIGN_MAX_RETRIES", "1")
            ),
            ui_design_concurrency=int(
                os.getenv("XCODEAGENT_UI_DESIGN_CONCURRENCY", "3")
            ),
            build_task_plan_max_retries=int(
                os.getenv("BUILD_TASK_PLAN_MAX_RETRIES", "2")
            ),
            dag_business_self_check_enabled=_env_bool(
                "XCODEAGENT_DAG_BUSINESS_SELF_CHECK_ENABLED", default=False
            ),
            checkpoint_db_path=os.getenv("XCODEAGENT_CHECKPOINT_DB", ""),
            checkpoint_retention_days=int(
                os.getenv("XCODEAGENT_CHECKPOINT_RETENTION_DAYS", "30")
            ),
            langsmith_tracing_enabled=_env_bool("LANGSMITH_TRACING", default=False),
            langsmith_project=os.getenv("LANGSMITH_PROJECT", ""),
            langsmith_endpoint=os.getenv("LANGSMITH_ENDPOINT", ""),
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
    """把常见环境变量布尔值转换为 Python 布尔值。"""

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def dag_business_self_check_enabled() -> bool:
    """读取 DAG 执行阶段业务自检开关，未配置时默认关闭。"""

    return _env_bool("XCODEAGENT_DAG_BUSINESS_SELF_CHECK_ENABLED", default=False)
