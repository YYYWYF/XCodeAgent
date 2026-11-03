"""读取服务配置；上游凭证始终只驻留服务端。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from urllib3.util import parse_url

ROOT = Path(__file__).resolve().parents[1]
GatewayTier = Literal["fast", "pro", "ultra"]


def _prepare_es_node_url(value: str) -> str:
    """清理单节点地址、校验无效字符，并为省略协议的地址补充 HTTP。"""
    node = value.strip()
    if not node or "\\" in node or any(character.isspace() for character in node):
        raise ValueError("ES 节点地址为空或含有无效字符")
    if "://" not in node:
        node = "http://" + node
    return node


@dataclass(frozen=True)
class GatewayProfile:
    """将选中档位的地址与模型标识绑定，供请求、目录和观测统一读取。"""

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
    upstream_trust_env: bool = False
    max_timeout_seconds: float = 1800
    default_timeout_seconds: float = 300
    max_retries: int = 2
    max_request_bytes: int = 16 * 1024 * 1024
    model_observation_output_enabled: bool = True
    # ES 支持单节点或逗号分隔的多个节点；留空保持控制台输出。
    es_url: str = ""
    es_index: str = Field(default="model-call-observations", pattern=r"^[a-z0-9][a-z0-9._-]*$", max_length=255)
    es_username: str = ""
    es_password: SecretStr = SecretStr("")
    es_request_timeout_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)
    # 旧远程配置只保留为已有部署的输入字段；不控制本地追踪，也不启用上报。
    langsmith_enabled: bool = False
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_project: str = "devagent-model-transmit"

    @staticmethod
    def validate_url(value: str) -> str:
        """禁止 URL 内嵌凭证、片段和查询参数，避免误转发秘密。"""
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("服务地址必须是有效的 HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("服务地址不能包含凭证、查询参数或片段")
        return value.rstrip("/")

    @staticmethod
    def parse_es_node(value: str) -> tuple[str, dict[str, str | int | bool]]:
        """只解析一个 ES 节点，返回规范地址及官方客户端接收的节点配置。"""
        parsed = parse_url(_prepare_es_node_url(value))
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("ES 节点缺少有效地址")
        if parsed.auth is not None or parsed.query is not None or parsed.fragment is not None:
            raise ValueError("ES 节点地址不能包含凭证、查询参数或片段")
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 9200)
        if not 1 <= port <= 65535:
            raise ValueError("ES 节点端口必须在 1 到 65535 之间")
        prefix = (parsed.path or "").rstrip("/")
        host: dict[str, str | int | bool] = {
            "host": parsed.host.strip("[]"),
            "port": port,
            "use_ssl": parsed.scheme == "https",
        }
        if prefix:
            host["url_prefix"] = prefix
        normalized = parsed._replace(port=port, path=prefix or None).url
        return normalized, host

    @field_validator("es_url", mode="before")
    @classmethod
    def normalize_es_nodes(cls, value: str) -> str:
        """先拆分多节点，再逐个解析；整份节点列表不传给 URL 解析器。"""
        if not isinstance(value, str):
            raise ValueError("ES_URL 必须为 IP、IP:端口或逗号分隔的节点地址")
        if not value.strip():
            return ""
        nodes = []
        for position, node in enumerate(value.replace("，", ",").split(","), start=1):
            try:
                normalized, _ = cls.parse_es_node(node)
            except ValueError:
                raise ValueError(
                    f"ES_URL 第 {position} 个节点格式无效：支持 IP、IP:端口或 HTTP(S) 地址；"
                    "多个节点用逗号分隔，不能包含空节点、账号密码、内部空白、查询参数或片段"
                ) from None
            if normalized not in nodes:
                nodes.append(normalized)
        return ",".join(nodes)

    @model_validator(mode="after")
    def validate_active(self) -> "Settings":
        """只校验已启用上游及所选网关档位，其他档位允许暂未配置。"""
        if not self.service_token.get_secret_value().strip():
            raise ValueError("SERVICE_TOKEN 必须非空")
        if self.model_upstream == "direct":
            try:
                self.direct_base_url = self.validate_url(self.direct_base_url)
            except ValueError:
                raise ValueError("DIRECT_BASE_URL 必须为有效 HTTP(S) 地址，且不包含凭证、查询参数或片段") from None
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
        if not 0 < self.default_timeout_seconds <= self.max_timeout_seconds or self.max_retries < 0:
            raise ValueError("超时和重试配置无效")
        if bool(self.es_username) != bool(self.es_password.get_secret_value()):
            raise ValueError("ES_USERNAME 和 ES_PASSWORD 必须同时配置")
        return self

    @property
    def es_urls(self) -> list[str]:
        """返回已经校验、补齐协议并去重的集群节点，供官方客户端建连接池。"""
        return self.es_url.split(",") if self.es_url else []

    @property
    def es_hosts(self) -> list[dict[str, str | int | bool]]:
        """直接提供节点配置列表，避免客户端再次把多 IP 字符串作为一个 URL 解析。"""
        return [self.parse_es_node(node)[1] for node in self.es_urls]

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
        for secret in (self.service_token, self.direct_api_key, self.gateway_api_key, self.gateway_key,
                       self.langsmith_api_key, self.es_password):
            value = secret.get_secret_value()
            if value:
                text = text.replace(value, "[REDACTED]")
        return text
