"""T0.1 正式 Artifact Gate、confirmed 输入及 Build 绑定边界。"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.graph.nodes.tasks import (
    _build_prerequisite_errors, _existing_build_task_plan, _resolve_build_context,
    prepare_build_tasks,
)
from app.graph.subgraphs.build import (
    _bound_build_task_plan_for_build, _latest_build_task_plan_for_build,
)
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.workspace.task_documents import build_task_plan_sha256
from tests.dag_planning_baseline_fixtures import (
    build_context, candidate_tasks, confirmed_baseline, execution_scope, formal_artifacts, project_plan,
    workspace_snapshot, write_json,
)
from tests.test_build_task_reuse_workspace import _ready_template


PLAN_PATH = ".xcodeagent/plans/build-task-plan.json"
ARTIFACT_PATHS = {
    "requirement_spec": ".xcodeagent/specs/requirement-spec.json",
    "product_plan": ".xcodeagent/plans/product-plan.json",
    "ui_designs": ".xcodeagent/specs/ui-designs.json",
    "technical_plan": ".xcodeagent/plans/technical-plan.json",
}


class DagPlanningBaselineGateTests(unittest.TestCase):
    def test_empty_workspace_has_no_planning_baseline(self) -> None:
        """空工作区不虚构历史 DAG。"""

        with tempfile.TemporaryDirectory() as workspace:
            self.assertEqual(_existing_build_task_plan({"workspace": workspace}), {})

    def test_persisted_confirmed_dag_reaches_skeleton_and_build_context(self) -> None:
        """已确认正式 DAG 从磁盘进入后续规划骨架，再解析新目标上下文。"""

        plan = project_plan()
        baseline = confirmed_baseline(plan, execution_scope())
        # 只提供 confirmed 文件，不固化当前读取器优先 checkpoint 的 legacy 行为。
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            path = write_json(root, PLAN_PATH, baseline)
            before = path.read_bytes()
            state = {"workspace": workspace}
            loaded = _existing_build_task_plan(state)
            skeleton = ensure_build_unit_skeleton(plan, workspace_snapshot(), loaded)
            context = _resolve_build_context(state, plan, execution_scope(name="customers"), skeleton)
            self.assertEqual(loaded, baseline)
            self.assertEqual(skeleton["task_registry"], baseline["task_registry"])
            self.assertEqual(context["endpoint_ids"], ["customers.list"])
            self.assertEqual(context["entity_ids"], ["Customer"])
            self.assertIn("backend:bootstrap", context["required_unit_ids"])
            self.assertEqual(skeleton["task_registry"]["backend:bootstrap::config"]["status"], "completed")
            self.assertEqual(path.read_bytes(), before)

    def test_prepare_pipeline_stops_at_confirmation_with_empty_or_confirmed_input(self) -> None:
        """真实 prepare 链路接受空基线或 confirmed DAG，但新规划必须重新等待确认。"""

        plan = project_plan()
        scope = execution_scope()
        baseline = confirmed_baseline(plan, execution_scope("endpoint", "customers"))
        for has_baseline in (False, True):
            with self.subTest(confirmed_input=has_baseline), tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                for key, payload in formal_artifacts(plan).items():
                    write_json(root, ARTIFACT_PATHS[key], payload)
                if has_baseline:
                    write_json(root, PLAN_PATH, baseline)
                # 只隔离模型 I/O 和模板工程准备；Context、校验、编译及正式门禁全部真实运行。
                with patch("app.graph.nodes.tasks.inspect_template_generation_readiness", return_value=_ready_template(root)), patch(
                    "app.agents.main.task_preparer._invoke_live_main_agent",
                    return_value=json.dumps({"tasks": candidate_tasks(build_context(plan, scope))}),
                ) as model, patch(
                    "app.graph.nodes.tasks.ensure_build_unit_skeleton", wraps=ensure_build_unit_skeleton,
                ) as skeleton:
                    result = prepare_build_tasks({
                        "workspace": workspace, "project_plan": plan,
                        "workspace_snapshot": workspace_snapshot(), "build_execution_scope": scope,
                    })
                self.assertEqual(skeleton.call_args.args[2], baseline if has_baseline else {})
                model.assert_called_once()
                self.assertEqual(result["status"], "requires_user_input")
                self.assertEqual(result["clarification"]["mode"], "build_task_plan_confirmation")
                self.assertEqual(result["build_task_plan"]["confirmation_status"], "pending")
                self.assertTrue(result["build_task_plan"]["task_graph"]["validation"]["is_valid"])
                self.assertEqual(result["build_execution_scope"], scope)

    def test_formal_gate_accepts_confirmed_artifacts_and_explicit_ui_skip(self) -> None:
        """正式产物全部确认或 UI 显式跳过时通过门禁，不修改输入。"""

        plan = project_plan()
        artifacts = formal_artifacts(plan)
        for ui_status in ("confirmed", "skipped"):
            with self.subTest(ui_status=ui_status), tempfile.TemporaryDirectory() as workspace, patch(
                "app.graph.nodes.tasks.inspect_template_generation_readiness", return_value={"ready": True, "errors": []}
            ):
                artifacts["ui_designs"]["confirmation_status"] = ui_status
                before = deepcopy(artifacts)
                errors = _build_prerequisite_errors({}, plan, workspace=workspace,
                    build_execution_scope=execution_scope(), formal_artifacts=artifacts)
                self.assertEqual(errors, [])
                self.assertEqual(artifacts, before)

    def test_unconfirmed_or_missing_formal_artifact_blocks_before_model_call(self) -> None:
        """逐项缺失或未确认上游产物必须拦截模型调用，checkpoint 不代替正式文件。"""

        plan = project_plan()
        for missing_key in ARTIFACT_PATHS:
            for status in (None, "pending"):
                with self.subTest(artifact=missing_key, status=status), tempfile.TemporaryDirectory() as workspace, patch(
                    "app.graph.nodes.tasks.inspect_template_generation_readiness", return_value={"ready": True, "errors": []}
                ), patch("app.graph.nodes.tasks.prepare_build_tasks_with_main_agent") as preparer:
                    artifacts = formal_artifacts(plan)
                    for key, relative in ARTIFACT_PATHS.items():
                        if key == missing_key:
                            if status is None:
                                continue
                            artifacts[key]["confirmation_status"] = status
                        write_json(Path(workspace), relative, artifacts[key])
                    result = prepare_build_tasks({
                        "workspace": workspace, "project_plan": plan,
                        "requirement_spec": {"confirmation_status": "confirmed"},
                        "product_plan": {"confirmation_status": "confirmed"},
                        "ui_designs": {"confirmation_status": "confirmed"},
                        "build_execution_scope": execution_scope(),
                    })
                    self.assertEqual(result["status"], "requires_user_input")
                    self.assertTrue(result["clarification"])
                    preparer.assert_not_called()
                    self.assertFalse((Path(workspace) / PLAN_PATH).exists())

    def test_build_reads_confirmed_file_instead_of_checkpoint_or_pending_file(self) -> None:
        """Build 入口读取 confirmed 正式文件，独立 pending 文件与 checkpoint 不覆盖它。"""

        plan = project_plan()
        confirmed = confirmed_baseline(plan, execution_scope())
        pending = deepcopy(confirmed)
        pending["confirmation_status"] = "pending"
        pending["task_registry"]["orders:page"]["description"] = "not authorized"
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            write_json(root, PLAN_PATH, confirmed)
            write_json(root, ".xcodeagent/plans/build-task-plan.pending.json", pending)
            actual, errors = _latest_build_task_plan_for_build({
                "workspace": workspace, "build_task_plan": pending,
                "build_execution_scope": execution_scope(),
            })
            self.assertEqual(errors, [])
            self.assertEqual(actual, confirmed)

    def test_build_blocks_without_formal_plan_even_with_confirmed_checkpoint(self) -> None:
        """只有 checkpoint 或独立 pending 文件时，Build 不得启动。"""

        confirmed = confirmed_baseline(project_plan(), execution_scope())
        with tempfile.TemporaryDirectory() as workspace:
            pending = {**confirmed, "confirmation_status": "pending"}
            write_json(Path(workspace), ".xcodeagent/plans/build-task-plan.pending.json", pending)
            actual, errors = _latest_build_task_plan_for_build({
                "workspace": workspace, "build_task_plan": confirmed,
                "build_execution_scope": execution_scope(),
            })
            self.assertEqual(actual, {})
            self.assertTrue(any("不存在" in error for error in errors))

    def test_build_gate_rejects_unconfirmed_failed_invalid_and_wrong_scope(self) -> None:
        """确认、状态、拓扑和 scope 任一不满足时，Build 必须拒绝。"""

        baseline = confirmed_baseline(project_plan(), execution_scope())
        cases = (
            ({"confirmation_status": "pending"}, "尚未确认"),
            ({"confirmation_status": "failed"}, "confirmation_status"),
            ({"status": "failed"}, "status=failed"),
            ({"task_graph": {"validation": {"is_valid": False, "errors": ["broken graph"]}}}, "broken graph"),
            ({"schema_version": "invalid"}, "schema_version"),
            ({"build_execution_scope": execution_scope(name="customers")}, "scope"),
        )
        for change, error_text in cases:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as workspace:
                write_json(Path(workspace), PLAN_PATH, {**baseline, **change})
                _, errors = _latest_build_task_plan_for_build({
                    "workspace": workspace, "build_task_plan": baseline,
                    "build_execution_scope": execution_scope(),
                })
                self.assertTrue(any(error_text in error for error in errors), errors)

    def test_build_run_binds_confirmed_digest_and_rejects_later_drift(self) -> None:
        """Build 绑定确认版本副本，后续正式计划变化必须停止而非换计划执行。"""

        baseline = confirmed_baseline(project_plan(), execution_scope())
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            write_json(root, PLAN_PATH, baseline)
            state = {"workspace": workspace, "build_execution_scope": execution_scope()}
            actual, binding, errors = _bound_build_task_plan_for_build(state)
            self.assertEqual(errors, [])
            self.assertEqual(actual, baseline)
            self.assertEqual(binding["build_run_plan_sha256"], build_task_plan_sha256(baseline))
            snapshot_path = Path(binding["build_run_plan_path"])
            snapshot_bytes = snapshot_path.read_bytes()
            self.assertEqual(json.loads(snapshot_bytes), baseline)
            _, _, repeated_errors = _bound_build_task_plan_for_build({**state, **binding})
            self.assertEqual(repeated_errors, [])
            changed = deepcopy(baseline)
            changed["task_registry"]["orders:page"]["description"] = "新确认版本"
            write_json(root, PLAN_PATH, changed)
            _, _, drift_errors = _bound_build_task_plan_for_build({**state, **binding})
            self.assertTrue(any("已变化" in error for error in drift_errors), drift_errors)
            self.assertEqual(snapshot_path.read_bytes(), snapshot_bytes)
