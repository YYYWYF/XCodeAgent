"""前端步骤检查只消费当前已授权的任务文件与 API 依赖。"""

import os

from app.agents.frontend.file_backend import file_revision
from app.services.frontend_step_acceptance import evaluate_frontend_page


class FrontendStepChecks:
    """保存最近的检查证据与读取版本，供 Step 4 核对。"""

    def __init__(self, scope):
        """绑定当前运行的动态只读范围。"""
        self.scope = scope
        self.versions: dict[str, tuple | None] = {}
        self.results: list[dict] = []

    def _version(self, path: str) -> tuple | None:
        """先验证当前读权限，再取得文件版本。"""
        try:
            _, physical = self.scope.authorize("read_file", path)
            return file_revision(physical.stat())
        except (OSError, ValueError):
            return None

    def _read(self, path: str) -> bytes:
        """读取用于验收的有界文件，并拒绝检查期间出现的版本漂移。"""
        self.versions[path] = self._version(path)
        _, physical = self.scope.authorize("read_file", path)
        fd = os.open(physical, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            if file_revision(os.fstat(stream.fileno())) != self.versions[path]:
                raise ValueError("打开文件时版本已变化")
            data = stream.read(512_001)
        if self._version(path) != self.versions[path]:
            raise ValueError("检查期间文件版本已变化")
        return data

    def run(self, *, structure_only: bool = False) -> list[dict]:
        """使用任务包中的页面入口、目标及直接依赖检查当前磁盘内容。"""
        task = self.scope.current
        contract = task["implementation_contract"]
        steps = f"step_{self.scope.step}"
        entry = contract["page_entry"]
        if not structure_only:
            self.versions.clear()
        sources: dict[str, bytes] = {}

        def read(path: str) -> bytes:
            """同一次检查复用原始快照，避免入口的结构与请求规则读取不同版本。"""
            if path not in sources:
                sources[path] = self._read(path)
            return sources[path]

        common = {
            "task_id": task["id"], "step_id": steps, "entry": entry,
            "targets": [item["path"] for item in task["change_scope"]
                        if item["operation"] != "delete" and item["path"].endswith((".ts", ".tsx"))],
            "queries": [{"api_contract_id": api["id"], "endpoint_id": endpoint["id"],
                         "path": endpoint["path"]}
                        for api in contract.get("api_contracts", []) for endpoint in api["endpoints"]
                        if endpoint.get("method") == "GET" and endpoint.get("path")],
            "dependencies": contract["api_dependencies"], "read": read,
        }
        findings = evaluate_frontend_page(**common, structure_only=True)
        if not structure_only:
            findings.extend(evaluate_frontend_page(**common))
            self.results = findings
        for item in findings:
            self.scope.record("frontend_acceptance_rule_result", **{
                key: value for key, value in item.items() if key not in {"task_id", "step_id"}
            })
        return findings

    def final(self) -> list[dict]:
        """当 Step 3 后文件发生变化时，使用最新只读快照重新检查。"""
        if not self.results or any(self._version(path) != version
                                   for path, version in self.versions.items()):
            return self.run()
        return self.results

    def clear(self) -> None:
        """释放上一个任务的全部检查证据。"""
        self.versions.clear()
        self.results.clear()
