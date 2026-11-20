"""验证同一 Endpoint 重跑时复用已完成任务，并重新派发未完成任务。"""

from copy import deepcopy
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from app.graph.subgraphs.build import run_build_scheduler
from app.services.build_task_planner import replace_build_task_plan_tasks
from app.workspace.task_documents import write_build_run_task_plan_json
from tests.test_build_subgraph_scheduler import _ready_build_state, _write_workspace_file


class EndpointBuildRestartTests(TestCase):
    """构造已确认计划、派发记录和当前工作区，验证重跑的实际任务范围。"""

    def test_restart_keeps_completed_endpoint_tasks_from_same_plan(self) -> None:
        """上次整轮重跑清掉投影状态时，仍可复用同计划入口副本里的完成记录。"""

        with TemporaryDirectory() as workspace:
            state = self._state(workspace, surviving_completion=False)
            _write_workspace_file(workspace, "Backend/app/objects.py")
            calls, result = self._run(state)

        self.assertEqual(calls, ["service"])
        self.assertEqual(result["build_summary"]["status"], "completed")
        self.assertEqual({task["id"]: task["status"] for task in result["tasks"]},
                         {"objects": "completed", "service": "completed"})

    def test_restart_checks_completed_output_still_exists(self) -> None:
        """缺失已完成任务文件时，必须重新派发该任务和未完成的下游。"""

        with TemporaryDirectory() as workspace:
            calls, result = self._run(self._state(workspace, surviving_completion=True))

        self.assertEqual(calls, ["objects", "service"])
        self.assertEqual(result["build_summary"]["status"], "completed")

    def test_restart_does_not_reuse_a_task_with_a_business_failure(self) -> None:
        """旧入口曾记录成功，也不能掩盖后来发生的任务合同失败。"""

        with TemporaryDirectory() as workspace:
            state = self._state(workspace, surviving_completion=False, contract_failure=True)
            _write_workspace_file(workspace, "Backend/app/objects.py")
            calls, _ = self._run(state)

        self.assertEqual(calls[0], "objects")

    def _state(self, workspace: str, *, surviving_completion: bool, contract_failure: bool = False) -> dict:
        """准备最新正式计划，以及同计划上次运行开始时的只读副本。"""

        scope = {"type": "endpoint", "targetId": "orders.modify", "apiContractId": "orders"}
        unit_id = "backend:endpoint:orders:orders.modify"
        tasks = [
            {"id": name, "unit_id": unit_id, "owner": "backend", "status": "pending", "dependencies": deps,
             "target_files": [f"Backend/app/{name}.py"],
             "change_scope": [{"operation": "add", "path": f"Backend/app/{name}.py"}]}
            for name, deps in (("objects", []), ("service", ["objects"]))
        ]
        completed_objects = {**tasks[0], "status": "completed", "last_result_status": "completed"}
        run_id = "build-" + "1" * 32
        plan = {
            "schema_version": "build-dag.v4",
            "build_units": {unit_id: {
                "id": unit_id, "kind": "backend",
                "task_ids": ["objects", "service"],
            }},
            "unit_graph": {"nodes": [unit_id], "edges": []},
            "last_update": {"stage": "build_scheduler", "updated_by": "build-scheduler", "build_run_id": run_id},
        }
        current_objects = completed_objects if surviving_completion else tasks[0]
        if contract_failure:
            current_objects = {**current_objects, "status": "failed", "failure_category": "contract_mismatch"}
        state = _ready_build_state(workspace, {
            "workspace": workspace, "project_plan": {"version": "1.0.0"},
            "build_execution_scope": scope, "resume_from": "build",
            "build_task_plan": replace_build_task_plan_tasks(plan, [current_objects, tasks[1]]),
        })
        snapshot = deepcopy(state["build_task_plan"])
        snapshot["task_registry"]["objects"].update(status="completed", last_result_status="completed")
        write_build_run_task_plan_json(state, build_run_id=run_id, build_task_plan=snapshot)
        return state

    def _run(self, state: dict) -> tuple[list[str], dict]:
        """仅替换外部 Agent，保留实际 Build 入口、任务调度和磁盘校验。"""

        calls: list[str] = []

        def runner(**kwargs):
            """写出任务声明的文件，记录实际收到的任务。"""
            for task in kwargs["tasks"]:
                calls.append(task["id"])
                _write_workspace_file(kwargs["workspace"], task["target_files"][0])
            return [{"task_id": task["id"], "owner": "backend", "status": "completed"}
                    for task in kwargs["tasks"]]

        with patch("app.graph.subgraphs.build.generate_data_sources_with_deep_agent", side_effect=runner):
            result = run_build_scheduler(state)
        return calls, result
