"""模型接入方式及转发连接配置；只校验当前启用的连接。"""

import os
from urllib.parse import urlsplit


def validate_model_transport(value: str) -> str:
    """严格选择接入方式，错误配置不得隐式切换到另一种连接。"""
    value = value.strip().lower()
    if value not in {"direct", "transmit"}:
        raise ValueError("MODEL_TRANSPORT must be direct or transmit")
    return value


def service_url(value: str) -> str:
    """校验转发地址且不在错误中泄漏 URL 中误填的凭据。"""
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and parsed.hostname and not (
            parsed.username or parsed.password or parsed.query or parsed.fragment
        )
        if parsed.port is not None:
            valid = valid and 1 <= parsed.port <= 65535
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("模型转发地址必须为不含凭证、查询参数或片段的 HTTP(S) URL")
    return value


def load_transmit_settings(transport: str) -> dict:
    """直连时忽略转发配置；转发时所有模型使用唯一全局服务。"""
    if transport != "transmit":
        return {}
    return {
        "model_transmit_url": service_url(os.getenv("MODEL_TRANSMIT_URL", "http://127.0.0.1:8100")),
        "model_transmit_token": os.getenv("MODEL_TRANSMIT_TOKEN", "").strip(),
        "model_transmit_trust_env": os.getenv("MODEL_TRANSMIT_TRUST_ENV", "").strip().lower() in {"1", "true", "yes", "on"},
        "model_transmit_spaid": os.getenv("MODEL_TRANSMIT_SPAID", "").strip(),
    }
