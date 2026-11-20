"""一次前端 Build Agent 调用独享的任务或步骤授权。"""

import asyncio
from contextlib import contextmanager
from collections import deque
from collections.abc import Callable
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from threading import RLock
from typing import Any

from langchain_core.tools import tool

from app.agents.frontend.read_ledger import FrontendReadLedger
from app.agents.frontend.step_checks import FrontendStepChecks
from app.agents.frontend.task_packet import API, PAGE, SERVICE, virtual_file
from app.services.agent_memory_runtime import AGENT_MEMORY_VIRTUAL_PATH
from app.services.builtin_skills import BUILTIN_SKILLS_VIRTUAL_ROOT, FRONTEND_ENGINEERING_SKILL_NAME
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.services.run_workers import current_run_id
from app.workspace.virtual_paths import is_host_workspace_virtual_path

_CONTROL = {"begin_frontend_task", "advance_frontend_step", "complete_frontend_task"}
_OPERATIONS = {"read_file": "read", "write_file": "write", "edit_file": "write",
               "delete_file": "delete", "grep": "search"}
_SKILL_ROOTS = (BUILTIN_SKILLS_VIRTUAL_ROOT, USER_SKILLS_VIRTUAL_ROOT)
_STATUSES = {"completed", "already_satisfied", "failed"}
FRONTEND_ENGINEERING_DOC = f"{BUILTIN_SKILLS_VIRTUAL_ROOT}{FRONTEND_ENGINEERING_SKILL_NAME}/SKILL.md"
FRONTEND_TEMPLATE_AGENTS_DOC = "/frontend/AGENTS.md"
FRONTEND_CORE_DOCS = frozenset({FRONTEND_ENGINEERING_DOC, FRONTEND_TEMPLATE_AGENTS_DOC})


class FrontendTaskExecutionScope:
    """持有任务顺序、有限步骤、文件授权以及本轮访问记录。"""

    def __init__(self, workspace: str, packets: list[dict],
                 trace: Callable[..., None] | None = None):
        """绑定工作区、已编译任务与可选追踪。"""
        self.root = Path(workspace).resolve(strict=True)
        if any(packet["kind"] == API for packet in packets) and len(packets) != 1:
            raise ValueError("API 模块必须使用独立任务作用域")
        self.packets = packets
        self.trace = trace
        self.index = 0
        self.step = 0
        self.outcomes: dict[str, str] = {}
        self.evidence: dict[str, list[str | dict]] = {}
        self.task_level = len(packets) == 1 and packets[0]["kind"] == API
        self.active = False
        self.run_id = current_run_id() or None
        self.reads = FrontendReadLedger()
        self.core_docs_read: set[tuple[str, str]] = set()
        self.revisions: dict[tuple[int, str], deque] = {}
        self.revert_warned: set[tuple[int, str]] = set()
        self.mutation_notice: dict | None = None
        self.graph_queries: set[str] = set()
        self.lock = RLock()
        self.async_lock = asyncio.Lock()
        self.checks = FrontendStepChecks(self)

    @property
    def current(self) -> dict | None:
        """返回活动任务，任务之间无活动授权。"""
        return self.packets[self.index] if self.step or self.active else None

    @contextmanager
    def task_context(self):
        """首次调用模型前激活 API 授权，任何退出路径均撤销并释放执行缓存。"""
        if self.active or self.step:
            raise ValueError("前端任务上下文不可重复激活")
        self.active = self.task_level
        try:
            yield self
        finally:
            self.active = False
            self.step = 0
            self.checks.clear()
            self.reads.clear()
            self.core_docs_read.clear()
            self.graph_queries.clear()
            self.revisions.clear()
            self.revert_warned.clear()
            self.mutation_notice = None

    def record(self, event: str, **fields: Any) -> None:
        """追踪步骤身份与权限决策，避免记录源码和模型提交的证据正文。"""
        if self.trace:
            active = self.current or {}
            self.trace(event, run_id=self.run_id, task_id=active.get("id"), unit_id=active.get("unit_id"),
                       task_kind=active.get("kind"), step_id=f"step_{self.step}" if self.step else None,
                       **fields)

    def note_mutation(self, path: str, before: str | None, after: str) -> None:
        """记录当前步骤有界版本序列，只对真实回退发送一次提示。"""
        key = (self.step, path)
        changed = before != after
        history = self.revisions.setdefault(key, deque(maxlen=3))
        reverted = False
        if changed:
            self.reads.invalidate(path)
            if not history or history[-1] != before:
                history.clear()
                history.append(before)
            history.append(after)
            reverted = len(history) == 3 and history[0] == history[2]
        warning = reverted and key not in self.revert_warned
        if warning:
            self.revert_warned.add(key)
        self.mutation_notice = {"changed": changed, "no_change": not changed,
                                "revert_detected": reverted, "warn_revert": warning}
        self.record("frontend_file_mutation", virtual_path=path,
                    frontend_step_edit_count=int(changed),
                    frontend_step_no_change_edit_count=int(not changed),
                    frontend_step_revert_count=int(reverted))

    def transition(self, name: str, arguments: dict) -> dict:
        """仅接受顺序启动、相邻推进及最后一步完成。"""
        with self.lock:
            task_id = arguments.get("task_id")
            if self.index >= len(self.packets) or task_id != self.packets[self.index]["id"]:
                raise ValueError("任务 ID 与当前派发顺序不匹配")
            if self.task_level:
                raise ValueError("当前任务的步骤推进来源未获授权")
            previous = self.step
            findings = []
            if name == "begin_frontend_task" and not previous:
                self.step = 1
                self.evidence[task_id] = []
                self.reads.clear()
                self.graph_queries.clear()
            elif name == "advance_frontend_step" and 1 <= previous < 4:
                evidence = arguments.get("completion_evidence")
                if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 1000:
                    raise ValueError("请提供 1 至 1000 字的步骤记录")
                if self.current["kind"] == PAGE and previous in {2, 3}:
                    findings = self.checks.run(structure_only=previous == 2)
                    failed = [item for item in findings if item["status"] == "failed"
                              and item["rule_id"] == "FE-PAGE-STRUCTURE"]
                    if failed and not arguments.get("defer_to_repair"):
                        return {"step": f"step_{previous}", "acceptance_failures": failed[:10]}
                self.evidence[task_id].append(evidence)
                self.step += 1
            elif name == "complete_frontend_task" and previous == 4:
                status = arguments.get("status")
                if status not in _STATUSES:
                    raise ValueError("任务状态无效")
                if self.current["kind"] == PAGE:
                    findings = self.checks.final()
                    failed = [item for item in findings if item["status"] == "failed"
                              and item["rule_id"] == "FE-PAGE-STRUCTURE"]
                    if failed and status != "failed":
                        return {"step": "step_4", "acceptance_failures": failed[:10],
                                "next_action": "以 failed 完成本任务，由现有 Repair 处理。"}
                self.outcomes[task_id] = status
            else:
                raise ValueError("当前步骤不允许该状态切换")
            self.record(
                "frontend_step_transition", step_transition=name, previous_step=previous,
                from_step=previous, to_step=self.step if name != "complete_frontend_task" else 0,
                source="agent", reason=None, evidence_ids=[],
            )
            # 保留 AST 的真实发现，告警不要求 Agent 自报失败。
            warnings = [item for item in findings if item["status"] in {"failed", "not_evaluable"}
                        and not (item["rule_id"] == "FE-PAGE-STRUCTURE" and item["status"] == "failed")]
            warning_result = {"ast_warnings": warnings[:10]} if warnings else {}
            if name == "complete_frontend_task":
                self.checks.clear()
                self.reads.clear()
                self.revisions.clear()
                self.revert_warned.clear()
                self.mutation_notice = None
                self.step = 0
                self.index += 1
                return {"status": status, **warning_result}
            return {"step": f"step_{self.step}", "readable_files": sorted(self.readable()),
                    "writable_files": sorted(self.writable()),
                    **warning_result}

    def readable(self) -> set[str]:
        """从当前任务精确目标及既有依赖授权计算读取范围，页面继续受步骤限制。"""
        task = self.current
        if task is None:
            return set()
        readable = set(task["target_files"])
        contract = task["implementation_contract"]
        if task["kind"] == API and self.task_level:
            readable.add(SERVICE)
        if task["kind"] == PAGE:
            if self.step in {1, 2} and contract.get("confirmed_design"):
                readable.add(contract["confirmed_design"])
            if self.step in {1, 3, 4}:
                readable.update(module["path"] for module in contract["api_dependencies"])
            if self.step in {1, 3}:
                readable.update(contract.get("type_and_constant_files", []))
                readable.update(contract.get("template_read_dependencies", []))
        return readable

    def writable(self) -> dict[str, str]:
        """API 按任务声明开放操作，页面仅在实现和定向修正步骤开放。"""
        if self.current is None or (not self.task_level and self.step not in {2, 3}):
            return {}
        return {item["path"]: item["operation"] for item in self.current["change_scope"]
                if item["operation"] != "read"}

    def mark_core_doc_read(self, path: str) -> None:
        """仅由串行工具成功返回完整正文后登记当前任务的核心文档。"""
        self.core_docs_read.add((self.current["id"], path))

    def physical_path(self, path: str) -> Path:
        """校验真实路径、链接、文件类型及硬链接，缺失目标可用于合法创建。"""
        if is_host_workspace_virtual_path(self.root, path):
            raise ValueError("不能使用宿主工作区绝对路径")
        current = self.root
        parts = path[1:].split("/")
        for index, part in enumerate(parts):
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                continue
            if index < len(parts) - 1:
                is_junction = getattr(current, "is_junction", None)
                if S_ISDIR(info.st_mode) and not (is_junction and is_junction()):
                    continue
            elif S_ISREG(info.st_mode) and info.st_nlink == 1:
                continue
            raise ValueError("目标必须是工作区内无链接的普通文件")
        if current.resolve() != current:
            raise ValueError("目标路径映射与授权路径不一致")
        return current

    def authorize(self, name: str, raw_path: Any) -> tuple[str, Path | None]:
        """在工具执行前校验精确路径和操作；默认拒绝未登记的工具。"""
        if self.current is None:
            raise ValueError("没有活动的前端任务上下文")
        if name not in _OPERATIONS:
            raise ValueError("此工具在前端 Build 中不可用，请使用精确文件路径")
        if not isinstance(raw_path, str) or not raw_path.startswith("/"):
            raise ValueError("工具只接受工作区虚拟绝对文件路径")
        path = virtual_file(raw_path)
        if path not in FRONTEND_CORE_DOCS and path.casefold() in {doc.casefold() for doc in FRONTEND_CORE_DOCS}:
            raise ValueError("核心文档必须使用精确虚拟路径及大小写")
        if path in FRONTEND_CORE_DOCS:
            if name != "read_file":
                raise ValueError("核心文档仅允许 read_file 精确只读")
            if (self.current["id"], path) in self.core_docs_read:
                raise ValueError("本任务已读取该规范，请复用已读取内容")
            if not self.task_level and self.step != 1:
                raise ValueError("页面核心文档仅允许在 Step 1 读取")
            return path, (self.physical_path(path) if path == FRONTEND_TEMPLATE_AGENTS_DOC else None)
        scope_label = "任务" if self.task_level else "步骤"
        if name == "read_file" and self.step < 4 and (
            path == AGENT_MEMORY_VIRTUAL_PATH or path.startswith(_SKILL_ROOTS)
        ):
            return path, None
        if name in {"read_file", "grep"}:
            if path not in self.readable() or (name == "grep" and (
                (not self.task_level and self.step != 1) or not path.startswith("/frontend/")
            )):
                raise ValueError(f"文件不在当前{scope_label}的读取范围")
        else:
            operation = self.writable().get(path)
            if (operation != "delete" if name == "delete_file" else
                operation not in {"add", "modify", "write"}):
                raise ValueError(f"文件操作未获当前{scope_label}授权")
        physical = self.physical_path(path)
        if name == "write_file" and physical.exists() and self.current["kind"] == API:
            raise ValueError("已有 API 模块必须使用 edit_file 增量修改")
        return path, physical

    def control_tools(self) -> list:
        """提供只接受任务 ID、证据与状态的步骤控制工具。"""
        if self.task_level:
            return []
        @tool
        def begin_frontend_task(task_id: str) -> dict:
            """启动顺序中的下一项任务。"""
            return self.transition("begin_frontend_task", {"task_id": task_id})

        @tool
        def advance_frontend_step(task_id: str, completion_evidence: str, defer_to_repair: bool = False) -> dict:
            """进入下一步；验收失败时原地修正，无法修正可标记交给 Repair。"""
            return self.transition("advance_frontend_step", {
                "task_id": task_id, "completion_evidence": completion_evidence,
                "defer_to_repair": defer_to_repair,
            })

        @tool
        def complete_frontend_task(task_id: str, status: str) -> dict:
            """在第四步提交状态，立即关闭本任务的文件权限。"""
            return self.transition("complete_frontend_task", {"task_id": task_id, "status": status})

        return [begin_frontend_task, advance_frontend_step, complete_frontend_task]

    def code_graph_tool(self):
        """提供按 API 任务范围或页面 Step 1 范围构建符号视图的工具。"""
        from app.agents.frontend.scoped_code_graph import create_scoped_code_graph_tool

        return create_scoped_code_graph_tool(self)
