"""内置数据库 SaaS 普通 JSON 接口的测试响应。"""

import httpx


def saas_success(status: int, body: object) -> httpx.Response:
    """按 SaaS 当前成功合同包装业务响应。"""

    return httpx.Response(status, json={"returnCode": "SUC0000", "errorMsg": None, "body": body})
