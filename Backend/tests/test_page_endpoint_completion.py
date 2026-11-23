"""验证页面关联接口在重试、修复及目录刷新后仍保留真实完成事实。"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.services.application_lifecycle import (
    create_application_lifecycle, start_workbench_execution, write_application_lifecycle,
)
from app.services.build_scheduler import resolve_execution_slice, reset_failed_tasks_for_retry
from app.services.build_task_planner import replace_build_task_plan_tasks
from app.services.development_artifacts import complete_initial_development, refresh_development_artifacts
from app.services.development_completion_evidence import completed_page_endpoints
from app.workspace.task_documents import build_task_plan_sha256


def page_build_state() -> dict:
    """用真实计划重建器和调度切片构造含同模块其他接口的页面执行。"""

    scope = {"type": "page", "targetId": "one"}
    page = {
        "id": "page", "unit_id": "page:one", "owner": "frontend", "status": "pending",
        "dependencies": ["backend"], "target_files": ["frontend/src/pages/one/index.tsx"],
        "source_refs": {"endpoint_designs": [{"apiContractId": "api", "endpointId": "get"}]},
    }
    backend = {
        "id": "backend", "unit_id": "backend:module:api", "owner": "backend", "status": "pending",
        "dependencies": [], "target_files": ["backend/Get.java"],
        "deliverables": [{"id": "get", "kind": "backend.endpoint_controller", "target_id": "get",
                          "paths": ["backend/Get.java"]}],
    }
    other = {**deepcopy(backend), "id": "other", "target_files": ["backend/Other.java"],
             "deliverables": [{"id": "other", "kind": "backend.endpoint_controller", "target_id": "other",
                               "paths": ["backend/Other.java"]}]}
    plan = replace_build_task_plan_tasks({
        "schema_version": "build-dag.v4", "confirmation_status": "confirmed", "status": "ready",
        "build_execution_scope": scope,
        "unit_graph": {"nodes": ["page:one", "backend:module:api"], "edges": [
            {"from": "backend:module:api", "to": "page:one", "type": "depends_on"},
        ]},
    }, [page, backend, other])
    tasks = [{**task, "status": "completed"} for task in plan["task_registry"].values()]
    return {
        "active_run_id": "run", "unit_test_gate_passed": True,
        "build_run_id": "build-" + "1" * 32, "build_run_plan_sha256": build_task_plan_sha256(plan),
        "build_execution_scope": scope, "build_task_plan": plan, "tasks": tasks,
        "build_execution_slice": resolve_execution_slice(
            build_task_plan=plan, tasks=tasks, build_execution_scope=scope,
        ),
    }


class PageEndpointCompletionTests(unittest.TestCase):
    """只以本轮成功任务和单测门禁登记页面关联接口。"""

    def test_runtime_retry_and_repair_do_not_invalidate_completion(self) -> None:
        """重试审计字段不改变正式计划摘要，成功接口仍可登记。"""

        for metadata in ({"retry_count": 1}, {"completed_by_repair": True, "repair_closed_at": "now"}):
            with self.subTest(metadata=metadata):
                state = page_build_state()
                state["tasks"][1].update(metadata)
                state["build_task_plan"] = replace_build_task_plan_tasks(state["build_task_plan"], state["tasks"])
                self.assertEqual(build_task_plan_sha256(state["build_task_plan"]), state["build_run_plan_sha256"])
                targets = completed_page_endpoints(state, page_id="one", run_id="run")
                self.assertEqual([(target.api_contract_id, target.endpoint_id) for target in targets], [("api", "get")])

    def test_incomplete_or_unbound_execution_does_not_complete_endpoint(self) -> None:
        """单测未过、身份不符、任务失败、合同变化及切片缺项都不能登记完成。"""

        for invalid in ("unit_gate", "run", "failed", "contract", "slice"):
            with self.subTest(invalid=invalid):
                state = page_build_state()
                if invalid == "unit_gate":
                    state["unit_test_gate_passed"] = False
                elif invalid == "run":
                    state["active_run_id"] = "other-run"
                elif invalid == "failed":
                    state["tasks"][1]["status"] = "failed"
                elif invalid == "contract":
                    state["build_task_plan"]["task_registry"]["backend"]["target_files"] = ["backend/Changed.java"]
                else:
                    state["build_execution_slice"]["task_ids"].remove("backend")
                self.assertEqual(completed_page_endpoints(state, page_id="one", run_id="run"), [])

    def test_completed_endpoint_stays_complete_after_catalog_refresh(self) -> None:
        """本轮新增页面和接口通过后，刷新不得把接口重新记为需要开发。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace) / ".devagentstudio"
            plans = root / "plans"
            plans.mkdir(parents=True)
            (plans / "product-plan.json").write_text(json.dumps({
                "confirmation_status": "confirmed", "pages": [{"pageId": "one"}],
            }))
            (plans / "technical-plan.json").write_text(json.dumps({
                "confirmation_status": "confirmed", "entities": [],
                "api_contracts": [{"id": "api", "endpoints": [{"id": "get"}, {"id": "other"}]}],
            }))
            (root / "development-change-snapshot.json").write_text(json.dumps({
                "schemaVersion": 1, "pages": {"one": "added"},
                "endpoints": {"api": {"get": "added", "other": "added"}}, "entities": {},
            }))
            lifecycle = create_application_lifecycle(application_id="test", application_name="测试")
            lifecycle.initialization.stage = ApplicationLifecycleStage.READY_FOR_WORKBENCH
            lifecycle.initialization.status = ApplicationLifecycleStatus.COMPLETED
            write_application_lifecycle(workspace, lifecycle)
            start_workbench_execution(
                workspace, scope="page", target_id="one", page_id="one", thread_id="thread",
                run_id="run", phase="api_design_readiness_gate", initial_development_entry=True,
            )
            state = page_build_state()
            # 经过真实失败重试入口后再完成，不手写重试计数来替代运行事实。
            state["tasks"][1]["status"] = "failed"
            state["tasks"] = reset_failed_tasks_for_retry(state["tasks"], {"backend"})
            state["tasks"][1]["status"] = "completed"
            state["build_task_plan"] = replace_build_task_plan_tasks(state["build_task_plan"], state["tasks"])
            complete_initial_development(workspace, run_id="run", build_state=state)
            completed = refresh_development_artifacts(workspace)
            endpoint = completed.development_artifacts.endpoints["api"]["get"]
            self.assertEqual(endpoint.initial_development_status, "completed")
            self.assertEqual(endpoint.change_status, "unchanged")
            self.assertEqual(endpoint.completed_run_id, "run")
            self.assertEqual(completed.development_artifacts.endpoints["api"]["other"].initial_development_status, "pending")
