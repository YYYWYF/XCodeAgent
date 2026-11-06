"""将平台当前 UI 模型配置映射到 Creator 专属进程，不持久化另一份配置或凭据。"""

from __future__ import annotations

import math

from app.config import Settings
from app.services.agent_ui_design_project import command_environment


def creator_process_environment(settings: Settings) -> dict[str, str]:
    """使用相同 UI 模型优先级，仅向 Creator 子进程提供其公开支持的参数。"""
    selected = settings.for_ui_design_model()
    if selected.model_provider not in {"openai", "openai-compatible"}:
        raise ValueError("当前 Creator 包仅支持 OpenAI 兼容模型，请检查项目的 UI 模型提供方。")
    if not all((selected.model_base_url.strip(), selected.model_api_key.strip(), selected.model_api_name.strip())):
        raise ValueError("Creator 需要项目提供完整的模型地址、密钥和模型名称。")
    if selected.model_custom_headers:
        raise ValueError("当前 Creator 包不支持项目的自定义模型请求头。")
    if selected.model_trust_env:
        raise ValueError("当前 Creator 包不支持项目的模型代理环境设置。")
    if (not math.isfinite(selected.model_timeout_seconds) or selected.model_timeout_seconds <= 0
            or not math.isfinite(selected.default_temperature) or selected.default_temperature < 0
            or selected.ui_design_max_tokens <= 0 or selected.model_max_retries < 0):
        raise ValueError("项目的 Creator 模型超时、温度、输出预算或重试参数无效。")
    return {**command_environment(),
        "CREATOR_MODEL_PROVIDER": "openai",
        "CREATOR_MODEL_BASE_URL": selected.model_base_url,
        "CREATOR_MODEL_API_KEY": selected.model_api_key,
        "CREATOR_MODEL_NAME": selected.model_api_name,
        "CREATOR_MODEL_TIMEOUT_SECONDS": str(selected.model_timeout_seconds),
        "CREATOR_MODEL_TEMPERATURE": str(selected.default_temperature),
        # Creator 通过多次工具调用完成设计；SDK 会从上下文中预留每步最大输出。
        # 使用其公开默认的 8192 上限，避免整页生成预算挤占工具证据，同时尊重更小的平台上限。
        "CREATOR_MODEL_MAX_TOKENS": str(min(selected.ui_design_max_tokens, 8192)),
        "CREATOR_MODEL_MAX_RETRIES": str(selected.model_max_retries),
        "CREATOR_MODEL_OPENCODE_SESSION_HEADER": "1" if selected.model_opencode_go_headers_enabled else "0",
        "CREATOR_AGENT_MODE": "domain-write",
        "CREATOR_VERIFICATION_MODE": "static_only",
        "CREATOR_HOT_RELOAD": "false",
    }
