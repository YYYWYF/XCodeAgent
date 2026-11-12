"""适配 SaaS 单表创建及只读结果查询，不自动重发 SQL。"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.services.internal_service_tls import build_internal_service_ssl_context
from app.services.managed_database_client import (
    ManagedDatabaseClient,
    ManagedDatabaseRegistrationError,
    unwrap_bee_envelope,
)


class ManagedTableQueryTransientError(ManagedDatabaseRegistrationError):
    """仅标识只读请求的临时传输故障，不授权任何 SQL 重放。"""


class ManagedTableError(ManagedDatabaseRegistrationError):
    """保存经过允许列表转换的产品提示，不透传 SaaS 原始错误正文。"""

    def __init__(self, code: str, title: str, message: str, action: str = "refresh") -> None:
        """区分明确拒绝建表与结果未知，提供对应的用户操作建议。"""

        super().__init__(message, uncertain=action != "rebuild")
        self.issue = {"code": code, "title": title, "message": message, "action": action}


def _response_error(status: int, code: str | None, *, creating: bool) -> ManagedTableError:
    """按当前 HTTP 状态与错误码共同映射，不能仅凭 409 建议删除重建。"""

    if status == 409 and code == "STRUCTURE_MISMATCH":
        if creating:
            return ManagedTableError(
                code, "表结构已变更",
                "当前实体设计与已有表结构不一致，普通建表不会更新已有表。"
                "请点击“重建表”应用当前设计；重建会清空该表全部数据。",
                "rebuild",
            )
        return ManagedTableError(code, "表结构与当前设计不一致", "请刷新执行结果查看当前结构差异，再决定后续操作。")
    if status == 409 and code == "RESOURCE_BUSY":
        return ManagedTableError(
            code, "数据库资源暂忙",
            "请稍后点击“刷新执行结果”，确认没有执行中或待核对的操作后再提交。",
        )
    if status == 409 and code == "TABLE_BINDING_CONFLICT":
        return ManagedTableError(
            code, "目标表暂时无法使用",
            "表名已被占用或表归属不可用，请联系管理员核对当前应用的表归属。",
        )
    return ManagedTableError(
        "RESULT_UNCONFIRMED", "暂未取得明确结果",
        "请点击“刷新执行结果”查看原操作记录。结果确认前，请勿重复执行或重建。",
    )


class ManagedTableClient(ManagedDatabaseClient):
    """复用注册客户端的地址、密钥和超时约束。"""

    def batch_create_tables(self, datasource_key: str, sqls: list[str]) -> Any:
        """一次提交完整 CREATE 列表，不复用单表请求体或自动重试。"""

        return self.request(datasource_key, "table-operations/batch", body={"mode": "CREATE", "sqls": sqls})

    def request(self, datasource_key: str, path: str, *, body: dict | None = None) -> Any:
        """发送一次请求；错误脱敏，提交结果未知时提示先查询原记录。"""

        from app.services.builtin_authorization_task_store import check_task_inputs, input_guard

        check_task_inputs()
        managed_task = input_guard.get() is not None
        if not re.fullmatch(r"[0-9a-f]{12}", datasource_key):
            raise ValueError("数据源 datasourceKey 无效。")
        url = f"{self._url}/{datasource_key}/{path}"
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout,
                              follow_redirects=False, trust_env=False,
                              verify=build_internal_service_ssl_context()) as client:
                response = client.request(
                    "GET" if body is None else "POST", url, json=body,
                    headers={**{"Authorization": f"Bearer {self._key}", "Accept": "application/json"},
                               **self._actor_headers()},
                )
        except httpx.RequestError:
            if managed_task and (body is None or path == "tables/check"):
                raise ManagedTableQueryTransientError("SaaS 原操作查询暂时不可用。") from None
            raise ManagedDatabaseRegistrationError(
                "SaaS 请求未取得确认结果。若已提交执行，请刷新原操作记录，不要重复提交。",
                uncertain=body is not None,
            ) from None
        # Bee 框架 @BeeResponseBody 统一返回 HTTP 200，@ResponseStatus 设置的状态码不生效。
        if response.status_code != 200:
            raise _response_error(response, creating=path == "table-operations" and body is not None and body.get("mode") == "CREATE")
        try:
            return unwrap_bee_envelope(response.json())
        except ValueError:
            payload = None
        # SaaS 的 JSON 接口统一包装结果；业务模型仍由各调用方校验。
        wrapped = isinstance(payload, dict) and set(payload) == {"returnCode", "errorMsg", "body"}
        if response.status_code != expected:
            if managed_task and (body is None or path == "tables/check") and response.status_code in {500, 502, 503, 504}:
                raise ManagedTableQueryTransientError("SaaS 原操作查询暂时不可用。")
            code = payload["returnCode"] if wrapped and isinstance(payload["errorMsg"], str) else None
            raise _response_error(
                response.status_code, code if isinstance(code, str) else None,
                creating=path == "table-operations" and body is not None and body.get("mode") == "CREATE",
            )
        if not wrapped or payload["returnCode"] != "SUC0000" or payload["errorMsg"] is not None:
            raise ManagedDatabaseRegistrationError(
                "SaaS 响应不符合当前接口合同，请先核对原操作记录，不要重复提交。",
                uncertain=body is not None,
            )
        return payload["body"]
