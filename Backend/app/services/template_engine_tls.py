"""模板服务网关的 TLS 连接设置，供所有访问 template_engine_base_url 的客户端复用。"""

from __future__ import annotations

import ssl


def build_template_engine_ssl_context() -> ssl.SSLContext:
    """构建允许旧式重协商的 TLS 上下文，兼容未实现 RFC 5746 的模板服务网关。"""

    # 模板服务在 TLS 1.2 上使用旧式重协商；OpenSSL 3.5 起客户端默认拒绝该握手，
    # 表现为 SSLError(UNSAFE_LEGACY_RENEGOTIATION_DISABLED)，上层只能看到连接失败。
    context = ssl.create_default_context()
    context.options |= ssl.OP_LEGACY_SERVER_CONNECT
    return context
