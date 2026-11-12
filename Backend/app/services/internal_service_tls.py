"""内网 Java 平台服务的 TLS 连接设置，供所有访问 xcode-agent-srv 网关的客户端复用。"""

from __future__ import annotations

import ssl


def build_internal_service_ssl_context() -> ssl.SSLContext:
    """构建允许旧式重协商的 TLS 上下文，兼容未实现 RFC 5746 的内网服务网关。"""

    # 内网网关在 TLS 1.2 上使用旧式重协商；OpenSSL 3.5 起客户端默认拒绝该握手，
    # 表现为 SSLError(UNSAFE_LEGACY_RENEGOTIATION_DISABLED)，上层只能看到连接失败。
    context = ssl.create_default_context()
    context.options |= ssl.OP_LEGACY_SERVER_CONNECT
    return context
