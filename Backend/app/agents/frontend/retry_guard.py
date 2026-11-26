"""受控前端运行内的独立失败链；只消费操作结果，不读取文件状态。"""

import json

from langchain_core.messages import ToolMessage

from app.agents.frontend.task_packet import virtual_file
from app.workspace.virtual_paths import is_host_workspace_virtual_path

FRONTEND_MAX_RETRIES = 3
CONTROL = {"begin_frontend_task", "advance_frontend_step", "complete_frontend_task"}


class FrontendTaskRetryExhausted(RuntimeError):
    """把确定性终止传播到真实 Agent 调用边界。"""


class FrontendRetryGuard:
    """各任务和各操作独立计数，终止后所有入口均拒绝继续。"""

    def __init__(self, scope):
        """绑定唯一平台状态，运行内保存计数和终止证据。"""
        self.scope = scope
        self.failures = {}
        self.termination = None

    def identity(self):
        """包括 begin 前的真实任务，结束后使用独立收尾身份。"""
        scope = self.scope
        return scope.packets[scope.index]["id"] if scope.index < len(scope.packets) else "@finalize"

    def check(self):
        """在工具和主模型、摘要模型调用前阻止已终止的执行。"""
        if self.termination:
            raise FrontendTaskRetryExhausted(self.termination["reason"])

    def stop(self, code, reason):
        """先锁定终止证据，再抛出不属于工具授权错误的专用异常。"""
        if self.termination is None:
            self.termination = {"task_id": self.identity(), "code": code, "reason": reason}
            self.scope.record("frontend_task_terminated", error_code=code)
        self.check()

    def fail(self, key, reason, *, tool="", path=""):
        """首次失败为零，第 3 次重试失败立即终止；每次只登记一次。"""
        self.check()
        previous = self.failures.get(key)
        count = previous["retry_count"] + 1 if previous else 0
        feedback = {
            "task_id": self.identity(), "step": self.scope.step,
            "task_started": bool(self.scope.current), "failure_category": key[1],
            "retry_count": count, "remaining_retries": FRONTEND_MAX_RETRIES - count,
            "reason": reason,
            "next_action": ("继续当前任务和步骤，不得重新 begin。" if self.scope.current else
                            "按真实 task_id 单独 begin。" if self.scope.index < len(self.scope.packets) else
                            "仅返回 task_results。"),
        }
        self.failures[key] = feedback
        self.scope.record("frontend_tool_failure" if tool else "frontend_context_compression_failed",
                          error_code=key[1], tool_name=tool, virtual_path=path,
                          failure_key=list(key), retry_count=count,
                          remaining_retries=FRONTEND_MAX_RETRIES - count)
        if count >= FRONTEND_MAX_RETRIES:
            self.scope.record("frontend_retry_exhausted", error_code=key[1], failure_key=list(key))
            self.stop(key[1], f"{key[1]}：首次失败后重试 {count} 次仍失败；{reason}")
        return feedback

    def target(self, args):
        """只保留合法虚拟目标；拒绝宿主路径和模型任意非法路径逃逸计数。"""
        raw = args.get("file_path", args.get("path"))
        if not isinstance(raw, str) or not raw.startswith("/") or is_host_workspace_virtual_path(self.scope.root, raw):
            return "@invalid"
        try:
            return virtual_file(raw)
        except ValueError:
            return "@invalid"

    def process(self, request, result, *, denied=False, identity=None, step=None):
        """统一消费真实 ToolMessage 与已验证的有限工具结果协议。"""
        if not isinstance(result, ToolMessage):
            return result
        name, args = request.tool_call["name"], request.tool_call["args"]
        task = identity or self.identity()
        step = self.scope.step if step is None else step
        path = self.target(args)
        text = result.content if isinstance(result.content, str) else ""
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            payload = None
        structure = isinstance(payload, dict) and bool(payload.get("acceptance_failures"))
        failed = denied or result.status == "error" or structure
        if isinstance(payload, dict):
            failed |= payload.get("status") == "error" or payload.get("exit_code", 0) != 0
        # code_graph_context 的拒绝是纯文本，其成功协议始终为 JSON 列表。
        if name == "code_graph_context":
            failed |= not isinstance(payload, list) or any(item.get("status") == "unavailable" for item in payload)
        if failed:
            if name in CONTROL:
                if self.scope.index < len(self.scope.packets) and args.get("task_id") != task:
                    category, suffix = "task_id_mismatch", ()
                else:
                    category, suffix = ("structure_rejected" if structure else "step_rejected"), (step,)
            elif name in {"write_file", "edit_file"} and any(fragment in text for fragment in (
                "文件身份或内容元数据不一致", "文件已变化，请读取当前版本后重试")):
                category, suffix = "file_version_conflict", (path,)
            elif name == "edit_file" and text.startswith(("Error: String not found", "Error: String '", "Error: old_string ends")):
                category, suffix = "content_match_failed", (path,)
            elif denied:
                category, suffix = "permission_denied", (name, path)
            else:
                category, suffix = "tool_result_error", (name, path)
            feedback = self.fail((task, category, *suffix), text, tool=name,
                                 path="" if path == "@invalid" else path)
            return result.model_copy(update={"status": "error", "content": text + "\n" + json.dumps(feedback, ensure_ascii=False)})
        # 对应操作成功才解除失败链；读成功不能解除任何写入错误。
        for key in list(self.failures):
            if key[0] != task:
                continue
            recovered = (key[1] in {"file_version_conflict", "content_match_failed"}
                         and name in {"write_file", "edit_file"} and key[2:] == (path,))
            recovered |= key[1] in {"permission_denied", "tool_result_error"} and key[2:] == (name, path)
            recovered |= name in CONTROL and (key[1] == "task_id_mismatch" or
                          key[1] in {"step_rejected", "structure_rejected"} and key[2:] == (step,))
            if recovered:
                del self.failures[key]
        return result
