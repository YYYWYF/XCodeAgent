"""用标准 SSE 库与 AG-UI SDK 消费 SaaS 只读动作，不手写事件传输解析。"""

from typing import Any
from uuid import uuid4

import httpx
from httpx_sse import SSEError, connect_sse
from ag_ui.core import Event, CustomEvent, RunStartedEvent, RunFinishedEvent, RunErrorEvent, StateSnapshotEvent, RunAgentInput
from pydantic import TypeAdapter, ValidationError

from app.services.managed_database_client import ManagedDatabaseClient


class ManagedDatabaseAgUiClient(ManagedDatabaseClient):
    """沿用服务地址、凭据及超时约束，读取完整生命周期后才返回数据。"""

    def query(self, datasource_key: str, path: str, query: dict[str, Any]) -> Any:
        """发出一次查询，校验 SDK 事件、关联身份与终态，不自动重试。"""

        thread_id, run_id = uuid4().hex, uuid4().hex
        payload = RunAgentInput(thread_id=thread_id, run_id=run_id, messages=[], tools=[], context=[], state={},
                                forwarded_props={"action": "QUERY", "query": query}).model_dump(by_alias=True, mode="json", exclude_none=True)
        started = finished = False
        candidate = snapshot = final = None
        failure_code = None
        size = count = 0
        decoder = TypeAdapter(Event)
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout, follow_redirects=False, trust_env=False) as client:
                with connect_sse(client, "POST", f"{self._url}/{datasource_key}/{path}", json=payload,
                                 headers={"Authorization": f"Bearer {self._key}", "Accept": "text/event-stream"}) as source:
                    if source.response.status_code != 200:
                        raise ValueError("SaaS 表数据查询接口不可用，请确认新接口已部署及服务鉴权配置。")
                    for item in source.iter_sse():
                        count += 1
                        size += len(item.data.encode("utf-8"))
                        if count > 128 or size > 12 * 1024 * 1024:
                            raise ValueError("SaaS 表数据响应过大，请减小每页条数后重新查询。")
                        event = decoder.validate_json(item.data)
                        if finished:
                            raise ValueError("SaaS 查询结束后仍返回事件，请核对服务协议。")
                        if isinstance(event, RunStartedEvent):
                            if started or event.thread_id != thread_id or event.run_id != run_id:
                                raise ValueError("SaaS 查询事件身份不一致。")
                            started = True
                        elif not started:
                            raise ValueError("SaaS 查询缺少开始事件。")
                        elif isinstance(event, RunErrorEvent):
                            raise ValueError("SaaS 表数据查询未正常结束，请重新查询。")
                        elif isinstance(event, CustomEvent):
                            if event.name == "TABLE_DATA_RESULT":
                                candidate = event.value
                            elif event.name == "TABLE_DATA_ERROR":
                                failure_code = event.value.get("code") if isinstance(event.value, dict) else "INVALID_RESPONSE"
                        elif isinstance(event, StateSnapshotEvent):
                            snapshot = event.snapshot.get("tableData") if isinstance(event.snapshot, dict) else None
                        elif isinstance(event, RunFinishedEvent):
                            if event.thread_id != thread_id or event.run_id != run_id:
                                raise ValueError("SaaS 查询结束事件身份不一致。")
                            finished, final = True, event.result
        except (httpx.HTTPError, SSEError, ValidationError):
            raise ValueError("SaaS 表数据查询连接中断或响应无效，请重新查询。") from None
        if not started or not finished:
            raise ValueError("SaaS 表数据查询未完整结束，不能使用本次结果。")
        if failure_code:
            hints = {"RESOURCE_BUSY": "数据库正在变更，请稍后刷新表数据。",
                     "RESOURCE_NOT_FOUND": "当前应用或数据表已不可用，请刷新数据表清单。",
                     "TABLE_BINDING_CONFLICT": "当前数据表归属不可用，请刷新数据表清单。",
                     "TABLE_DATA_TOO_LARGE": "本页数据过大，请减小每页条数。",
                     "TABLE_DATA_UNSUPPORTED": "该表暂不支持数据预览，请核对主键及字段数量。"}
            raise ValueError(hints.get(failure_code, "SaaS 表数据读取失败，请检查资源状态后重新查询。"))
        if candidate is None or snapshot != candidate or final != candidate:
            raise ValueError("SaaS 查询结果与最终状态不一致，请重新查询。")
        return candidate
