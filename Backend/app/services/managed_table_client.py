"""解析表操作及元数据响应，并保留受控业务提示。"""

from __future__ import annotations

import httpx

from app.services.managed_database_bee import unwrap_bee_envelope
from app.services.managed_database_client import ManagedDatabaseRegistrationError


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


def decode_table_response(response: httpx.Response, path: str, *, body: dict | None, managed_task: bool) -> object:
    """按原读写语义解析 Bee，失败时只公开受控错误和提示。"""

    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.status_code != 200:
        if (managed_task and (body is None or path == "tables/check")
                and response.status_code in {500, 502, 503, 504}):
            raise ManagedTableQueryTransientError("SaaS 原操作查询暂时不可用。")
        wrapped = isinstance(payload, dict) and set(payload) == {"returnCode", "errorMsg", "body"}
        code = payload["returnCode"] if wrapped and isinstance(payload["errorMsg"], str) else None
        raise _response_error(
            response.status_code, code if isinstance(code, str) else None,
            creating=path == "table-operations" and body is not None and body.get("mode") == "CREATE",
        )
    try:
        return unwrap_bee_envelope(payload)
    except ValueError:
        raise ManagedDatabaseRegistrationError(
            "SaaS 响应不符合当前接口合同，请先核对原操作记录，不要重复提交。",
            uncertain=body is not None,
        ) from None
