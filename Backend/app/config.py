from __future__ import annotations

import dataclasses
import os
import re
from dataclasses import dataclass, field
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
    anthropic_api_version: str = "2023-06-01"
    model_custom_headers: dict[str, str] = field(default_factory=dict)
    default_system_prompt: str = (
        "You are a helpful local agent. Answer clearly and concisely."
    )
    default_temperature: float = 0.2
    default_max_tokens: int = 2048
    # UI 确认节点生成 React 设计稿的生成 token 上限。推理模型（如 glm-5.2）的
    # 思考过程与正文共用该预算，且网关会把 thinking 以 [{'thinking': ..}] 碎片
    # 形式逐 token 拼进 content——16384 时思考可吃掉大部分预算导致正文在
    # `export default` 前被截断，故提到 32768 给足余量。
    # UI 设计稿节点的独立模型配置（可选）。GLM-5.2 等推理模型在长代码任务上
    # thinking 压不掉、生成耗时是正文的两三倍；给 UI 节点单独配一个非推理模型
    # （如 DeepSeek-V4 Pro，其官方 API 真正支持 thinking 开关）可显著缩短生成
    # 时间，其他节点继续用全局模型。四项（base_url/api_key/name）同时配置才生效，
    # 缺任一项即回落全局模型；provider/headers/trust_env 缺省跟随全局。
    ui_design_model_base_url: str = ""
    ui_design_model_api_key: str = ""
    ui_design_model_name: str = ""
    ui_design_model_provider: str = ""
    ui_design_model_custom_headers: dict[str, str] = field(default_factory=dict)
    ui_design_model_trust_env: bool | None = None
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
        return self.model_provider

    def for_ui_design_model(self) -> "Settings":
        """返回 UI 设计稿节点应使用的模型配置视图。

        独立配置（UI_DESIGN_MODEL_BASE_URL/API_KEY/NAME 三项齐全）生效时，返回
        替换了模型字段的 Settings 副本（provider/custom_headers/trust_env 缺省
        跟随全局值）；未配置时返回自身（回落全局模型）。
        """

        if not (
            self.ui_design_model_base_url
            and self.ui_design_model_api_key
            and self.ui_design_model_name
        ):
            return self
        provider = self.ui_design_model_provider or self.model_provider
        if provider == "openai-compatible":
            provider = "openai"
        if provider not in ("openai", "anthropic"):
            raise RuntimeError(
                "Only UI_DESIGN_MODEL_PROVIDER=openai or anthropic is supported."
            )
        return dataclasses.replace(
            self,
            model_base_url=self.ui_design_model_base_url,
            model_api_key=self.ui_design_model_api_key,
            model_name=self.ui_design_model_name,
            model_provider=provider,
            model_custom_headers=(
                self.ui_design_model_custom_headers or self.model_custom_headers
            ),
            model_trust_env=(
                self.ui_design_model_trust_env
                if self.ui_design_model_trust_env is not None
                else self.model_trust_env
            ),
        )

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量加载模型、UI 设计生成和持久化配置。"""

        base_url = _required_any("MODEL_BASE_URL", "OPENAI_BASE_URL")
        model_provider = (os.getenv("MODEL_PROVIDER", "").strip().lower() or "openai")
        if model_provider == "openai-compatible":
            model_provider = "openai"
        if model_provider not in ("openai", "anthropic"):
            raise RuntimeError(
                "Only MODEL_PROVIDER=openai or anthropic is supported."
            )
        custom_headers_raw = os.getenv("MODEL_CUSTOM_HEADERS", "")
        custom_headers = _parse_custom_headers(custom_headers_raw)
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
            anthropic_api_version=os.getenv(
                "ANTHROPIC_API_VERSION", "2023-06-01"
            ),
            model_custom_headers=custom_headers,
            default_system_prompt=os.getenv(
                "AGENT_SYSTEM_PROMPT",
                "You are a helpful local agent. Answer clearly and concisely.",
            ),
            default_temperature=float(os.getenv("AGENT_TEMPERATURE", "0.2")),
            default_max_tokens=int(os.getenv("AGENT_MAX_TOKENS", "2048")),
            ui_design_model_base_url=os.getenv("UI_DESIGN_MODEL_BASE_URL", "").strip(),
            ui_design_model_api_key=os.getenv("UI_DESIGN_MODEL_API_KEY", "").strip(),
            ui_design_model_name=os.getenv("UI_DESIGN_MODEL_NAME", "").strip(),
            ui_design_model_provider=(
                os.getenv("UI_DESIGN_MODEL_PROVIDER", "").strip().lower()
            ),
            ui_design_model_custom_headers=_parse_custom_headers(
                os.getenv("UI_DESIGN_MODEL_CUSTOM_HEADERS", "")
            ),
            ui_design_model_trust_env=(
                _env_bool("UI_DESIGN_MODEL_TRUST_ENV", default=False)
                if os.getenv("UI_DESIGN_MODEL_TRUST_ENV") is not None
                else None
            ),
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


def _parse_custom_headers(raw: str) -> dict[str, str]:
    """把 MODEL_CUSTOM_HEADERS 环境变量解析为 header dict。

    格式："Key1: Value1\\nKey2: Value2"，与 .env 里多行字符串兼容。
    """

    if not raw or not raw.strip():
        return {}
    headers: dict[str, str] = {}
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key:
            headers[key] = value
    return headers


def dag_business_self_check_enabled() -> bool:
    """读取 DAG 执行阶段业务自检开关，未配置时默认关闭。"""

    return _env_bool("XCODEAGENT_DAG_BUSINESS_SELF_CHECK_ENABLED", default=False)
