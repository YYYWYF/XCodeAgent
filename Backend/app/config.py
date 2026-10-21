from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _load_environment() -> None:
    env_file = os.getenv("DEVAGENTSTUDIO_BACKEND_ENV_FILE")
    if env_file:
        load_dotenv(Path(env_file).expanduser(), override=False)
        return
    load_dotenv()


_load_environment()

@dataclass(frozen=True)
class Settings:
    model_transmit_url: str = "http://127.0.0.1:8100"
    model_transmit_token: str = field(default="", repr=False)
    model_transmit_trust_env: bool = False
    model_transmit_spaid: str = ""
    model_output_log_enabled: bool = False
    model_timeout_seconds: float = 120.0
    model_max_retries: int = 2
    default_system_prompt: str = (
        "You are a helpful local agent. Answer clearly and concisely."
    )
    default_temperature: float = 0.2
    default_max_tokens: int = 32768
    # UI 确认节点生成 React 设计稿的生成 token 上限。推理模型（如 glm-5.2）的
    # 思考过程与正文共用该预算，且网关会把 thinking 以 [{'thinking': ..}] 碎片
    # 形式逐 token 拼进 content——16384 时思考可吃掉大部分预算导致正文在
    # `export default` 前被截断，故提到 32768 给足余量。
    # UI 和 DAG 只保留业务调用预算；所有场景共用转发服务当前启用的模型。
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
    # DAG Unit Generation 独立预算；仅提供配置，不改变其他 Agent 的模型参数。
    dag_unit_max_tokens: int = 4096
    dag_unit_generation_concurrency: int = 3
    # 第一版固定策略，不作为构造参数或环境变量配置开放。
    dag_unit_local_max_attempts: int = field(default=3, init=False)
    dag_global_repair_limit: int = field(default=2, init=False)
    dag_business_self_check_enabled: bool = False
    checkpoint_db_path: str = ""  # populated in from_env
    checkpoint_retention_days: int = 30
    execution_recovery_heartbeat_seconds: float = 10.0
    execution_recovery_lease_ttl_seconds: float = 45.0
    langsmith_tracing_enabled: bool = False
    langsmith_project: str = ""
    langsmith_endpoint: str = ""
    template_engine_base_url: str = ""
    template_engine_connect_timeout_seconds: float = 10.0
    template_engine_read_timeout_seconds: float = 120.0
    template_package_max_bytes: int = 104857600
    template_package_max_files: int = 10000
    template_package_max_extracted_bytes: int = 524288000
    template_reconcile_enabled: bool = True
    git_username: str = ""
    git_token: str = ""

    @property
    def model_name(self) -> str:
        """返回本应用的逻辑模型标识，实际模型名称由转发服务响应提供。"""
        return "studio-model"

    @property
    def model_api_name(self) -> str:
        """为工作流证据提供稳定的逻辑模型名，不读取上游模型环境变量。"""
        return self.model_name

    @property
    def model_provider(self) -> str:
        """标识所有模型调用均经过独立转发服务。"""
        return "transmit"

    @property
    def provider_api_name(self) -> str:
        """返回当前传输提供方，保持工作流元数据的含义明确。"""
        return self.model_provider

    @classmethod
    def from_env(cls) -> "Settings":
        """读取转发服务连接和既有业务预算，不读取或持有真实上游连接信息。"""
        return cls(
            model_transmit_url=os.getenv("MODEL_TRANSMIT_URL", "http://127.0.0.1:8100").strip().rstrip("/"),
            model_transmit_token=os.getenv("MODEL_TRANSMIT_TOKEN", "").strip(),
            model_transmit_trust_env=_env_bool("MODEL_TRANSMIT_TRUST_ENV", default=False),
            model_transmit_spaid=os.getenv("MODEL_TRANSMIT_SPAID", "").strip(),
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
            default_max_tokens=int(os.getenv("AGENT_MAX_TOKENS", "32768")),
            ui_design_max_tokens=int(
                os.getenv("DEVAGENTSTUDIO_UI_DESIGN_MAX_TOKENS", "32768")
            ),
            ui_design_max_retries=int(
                os.getenv("DEVAGENTSTUDIO_UI_DESIGN_MAX_RETRIES", "1")
            ),
            ui_design_concurrency=int(
                os.getenv("DEVAGENTSTUDIO_UI_DESIGN_CONCURRENCY", "3")
            ),
            build_task_plan_max_retries=int(
                os.getenv("BUILD_TASK_PLAN_MAX_RETRIES", "2")
            ),
            dag_unit_max_tokens=_env_int(
                "DEVAGENTSTUDIO_DAG_UNIT_MAX_TOKENS", default=4096, minimum=1
            ),
            dag_unit_generation_concurrency=_env_int(
                "DEVAGENTSTUDIO_DAG_UNIT_CONCURRENCY", default=3, minimum=1
            ),
            dag_business_self_check_enabled=_env_bool(
                "DEVAGENTSTUDIO_DAG_BUSINESS_SELF_CHECK_ENABLED", default=False
            ),
            checkpoint_db_path=os.getenv("DEVAGENTSTUDIO_CHECKPOINT_DB", ""),
            checkpoint_retention_days=int(
                os.getenv("DEVAGENTSTUDIO_CHECKPOINT_RETENTION_DAYS", "30")
            ),
            execution_recovery_heartbeat_seconds=execution_recovery_heartbeat_seconds(),
            execution_recovery_lease_ttl_seconds=execution_recovery_lease_ttl_seconds(),
            langsmith_tracing_enabled=_env_bool("LANGSMITH_TRACING", default=False),
            langsmith_project=os.getenv("LANGSMITH_PROJECT", ""),
            langsmith_endpoint=os.getenv("LANGSMITH_ENDPOINT", ""),
            template_engine_base_url=os.getenv(
                "DEVAGENTSTUDIO_TEMPLATE_ENGINE_BASE_URL", ""
            ).rstrip("/"),
            template_engine_connect_timeout_seconds=float(
                os.getenv("DEVAGENTSTUDIO_TEMPLATE_ENGINE_CONNECT_TIMEOUT_SECONDS", "10")
            ),
            template_engine_read_timeout_seconds=float(
                os.getenv("DEVAGENTSTUDIO_TEMPLATE_ENGINE_READ_TIMEOUT_SECONDS", "120")
            ),
            template_package_max_bytes=int(
                os.getenv("DEVAGENTSTUDIO_TEMPLATE_PACKAGE_MAX_BYTES", "104857600")
            ),
            template_package_max_files=int(
                os.getenv("DEVAGENTSTUDIO_TEMPLATE_PACKAGE_MAX_FILES", "10000")
            ),
            template_package_max_extracted_bytes=int(
                os.getenv(
                    "DEVAGENTSTUDIO_TEMPLATE_PACKAGE_MAX_EXTRACTED_BYTES", "524288000"
                )
            ),
            template_reconcile_enabled=_env_bool(
                "DEVAGENTSTUDIO_TEMPLATE_RECONCILE_ENABLED", default=True
            ),
            git_username=os.getenv("DEVAGENTSTUDIO_GIT_USERNAME", ""),
            git_token=os.getenv("DEVAGENTSTUDIO_GIT_TOKEN", ""),
        )


def _env_int(name: str, *, default: int, minimum: int) -> int:
    """严格读取有下界的整数配置，非法值报告变量名而不静默回退默认值。"""

    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer >= {minimum}.") from exc
    if value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}.")
    return value


def _env_float(name: str, *, default: float, minimum: float) -> float:
    """严格读取有下界的浮点配置，非法值报告变量名。"""

    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number >= {minimum}.") from exc
    if value < minimum:
        raise ValueError(f"{name} must be a number >= {minimum}.")
    return value


def execution_recovery_heartbeat_seconds() -> float:
    """读取 Durable Execution lease 的心跳间隔。"""

    return _env_float(
        "XCODEAGENT_EXECUTION_RECOVERY_HEARTBEAT_SECONDS",
        default=10.0,
        minimum=0.001,
    )


def execution_recovery_lease_ttl_seconds() -> float:
    """读取 lease TTL，并确保至少覆盖三个心跳周期。"""

    heartbeat_seconds = execution_recovery_heartbeat_seconds()
    ttl_seconds = _env_float(
        "XCODEAGENT_EXECUTION_RECOVERY_LEASE_TTL_SECONDS",
        default=45.0,
        minimum=0.001,
    )
    if ttl_seconds < heartbeat_seconds * 3:
        raise ValueError(
            "XCODEAGENT_EXECUTION_RECOVERY_LEASE_TTL_SECONDS must be at least "
            "three heartbeat intervals."
        )
    return ttl_seconds


def _env_bool(name: str, *, default: bool) -> bool:
    """把常见环境变量布尔值转换为 Python 布尔值。"""

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def dag_business_self_check_enabled() -> bool:
    """读取 DAG 执行阶段业务自检开关，未配置时默认关闭。"""

    return _env_bool("DEVAGENTSTUDIO_DAG_BUSINESS_SELF_CHECK_ENABLED", default=False)
