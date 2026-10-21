"""读取服务配置；上游凭证始终只驻留服务端。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
GatewayTier = Literal["fast", "pro", "ultra"]


@dataclass(frozen=True)
class GatewayProfile:
    """将选中档位的地址与模型标识绑定，供请求、目录和历史统一读取。"""

    tier: GatewayTier
    base_url: str
    model_name: str


class Settings(BaseSettings):
    """定义当前服务配置，不读取 Studio 的环境文件。"""

    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True)
    model_upstream: Literal["direct", "gateway"] = "direct"
    service_token: SecretStr = SecretStr("local-model-transmit-dev-token")
    direct_base_url: str = "http://open-llm.uat.cmbchina.cn/llm/deepseek-v4p1-flash-w8a8/v1"
    direct_model_name: str = "deepseek-v4p1-flash-w8a8"
    direct_api_key: SecretStr = SecretStr("")
    gateway_tier: GatewayTier = "fast"
    gateway_fast_base_url: str = "http://lt5404-agent-gateway.paas.cmbchina.cn/pattern/llm/fast/v1"
    gateway_fast_model_name: str = "fast"
    gateway_pro_base_url: str = ""
    gateway_pro_model_name: str = ""
    gateway_ultra_base_url: str = ""
    gateway_ultra_model_name: str = ""
    gateway_api_key: SecretStr = SecretStr("")
    gateway_key: SecretStr = SecretStr("")
    gateway_spaid_source: Literal["request", "fixed"] = "request"
    gateway_spaid: str = ""
    history_db_path: Path = ROOT / "data" / "model_history.sqlite3"
    upstream_trust_env: bool = False
    max_timeout_seconds: float = 1800
    default_timeout_seconds: float = 300
    max_retries: int = 2
    max_request_bytes: int = 16 * 1024 * 1024
    langsmith_enabled: bool = False
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_project: str = "devagent-model-transmit"
    mock_analytics_enabled: bool = False

    @staticmethod
    def validate_url(value: str) -> str:
        """禁止 URL 内嵌凭证、片段和查询参数，避免误转发秘密。"""
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("服务地址必须是有效的 HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("服务地址不能包含凭证、查询参数或片段")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_active(self) -> "Settings":
        """只校验已启用上游及所选网关档位，其他档位允许暂未配置。"""
        if not self.service_token.get_secret_value().strip():
            raise ValueError("SERVICE_TOKEN 必须非空")
        if self.model_upstream == "direct":
            self.direct_base_url = self.validate_url(self.direct_base_url)
            if not self.direct_api_key.get_secret_value().strip():
                raise ValueError("当前 direct 上游缺少 DIRECT_API_KEY")
        else:
            profile = self.gateway_profile
            prefix = f"GATEWAY_{profile.tier.upper()}"
            if not profile.base_url:
                raise ValueError(f"当前 gateway 档位缺少 {prefix}_BASE_URL")
            try:
                self.validate_url(profile.base_url)
            except ValueError:
                raise ValueError(f"{prefix}_BASE_URL 必须为有效 HTTP(S) 地址，且不包含凭证、查询参数或片段") from None
            if not profile.model_name:
                raise ValueError(f"当前 gateway 档位缺少 {prefix}_MODEL_NAME")
            if not (self.gateway_api_key.get_secret_value().strip() and self.gateway_key.get_secret_value().strip()):
                raise ValueError("当前 gateway 上游缺少 GATEWAY_API_KEY 或 GATEWAY_KEY")
        if self.model_upstream == "gateway" and self.gateway_spaid_source == "fixed" and not self.gateway_spaid.strip():
            raise ValueError("固定 spaid 模式缺少 GATEWAY_SPAID")
        if self.langsmith_enabled and not self.langsmith_api_key.get_secret_value().strip():
            raise ValueError("启用 LangSmith 时必须配置 LANGSMITH_API_KEY")
        if self.langsmith_enabled:
            self.langsmith_endpoint = self.validate_url(self.langsmith_endpoint)
        if not 0 < self.default_timeout_seconds <= self.max_timeout_seconds or self.max_retries < 0:
            raise ValueError("超时和重试配置无效")
        return self

    @property
    def gateway_profile(self) -> GatewayProfile:
        """依据显式档位同时解析地址和模型名称，不推测路径或自动切换档位。"""
        return GatewayProfile(
            tier=self.gateway_tier,
            base_url=getattr(self, f"gateway_{self.gateway_tier}_base_url").strip().rstrip("/"),
            model_name=getattr(self, f"gateway_{self.gateway_tier}_model_name").strip(),
        )

    @property
    def gateway_metadata(self) -> dict[str, str]:
        """仅为 gateway 返回服务端确定的公开档位，供目录和观测信息使用。"""
        return {"gatewayTier": self.gateway_tier} if self.model_upstream == "gateway" else {}

    @property
    def active_model(self) -> str:
        """返回当前已启用上游的模型名称。"""
        return self.direct_model_name if self.model_upstream == "direct" else self.gateway_profile.model_name

    def redact(self, text: str) -> str:
        """在对外错误与日志中移除服务持有的凭证。"""
        for secret in (self.service_token, self.direct_api_key, self.gateway_api_key, self.gateway_key, self.langsmith_api_key):
            value = secret.get_secret_value()
            if value:
                text = text.replace(value, "[REDACTED]")
        return text
