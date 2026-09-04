"""T2.4 模板前置事实、shell 非生成边界及正式规划基线门禁。"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.agents.main.task_preparer_prompt import scoped_prompt_build_context
from app.graph.nodes.tasks import (
    _merge_prepared_scope_tasks, _replaceable_unit_ids, prepare_build_tasks,
)
from app.services.application_template_generation import (
    inspect_template_generation_readiness, prepare_application_template_generation,
    validate_application_template_generation,
)
from app.services.build_task_planner import build_task_candidate_contract_errors
from app.services.build_task_reuse import resolve_reuse_facts, resolve_template_prerequisite_facts
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.engineering_acceptance import compile_engineering_acceptance
from app.services.unit_generation_requirements import (
    GenerationRequirementsError, resolve_generation_requirements,
)
from tests.dag_planning_baseline_fixtures import (
    build_context, candidate_tasks, execution_scope, formal_artifacts, project_plan,
    workspace_snapshot, write_json,
)
from tests.test_build_task_reuse import _plan, _task
from tests.test_build_task_reuse_workspace import _ready_template


PLAN_PATH = ".xcodeagent/plans/build-task-plan.json"
SHELL = "frontend:shell"
SHELL_STATE = {
    "generation_strategy": "prerequisite_only",
    "participation": "prerequisite_only",
    "generation_status": "not_required",
}


def _state(root: Path) -> dict:
    """写入真实节点所需正式上游，模板工程由调用者独立准备。"""

    plan = project_plan()
    paths = {
        "requirement_spec": ".xcodeagent/specs/requirement-spec.json",
        "product_plan": ".xcodeagent/plans/product-plan.json",
        "ui_designs": ".xcodeagent/specs/ui-designs.json",
        "technical_plan": ".xcodeagent/plans/technical-plan.json",
    }
    for key, artifact in formal_artifacts(plan).items():
        write_json(root, paths[key], artifact)
    return {
        "workspace": str(root), "project_plan": plan,
        "workspace_snapshot": workspace_snapshot(), "build_execution_scope": execution_scope(),
    }


def _history(status: str) -> dict:
    """提供文件已确认但 shell 执行状态可变的历史任务，不作为本轮模型候选。"""

    task = {
        **_task("historical-shell", unit_id=SHELL, status=status),
        "change_scope": [{"operation": "modify", "path": "frontend/src/components/ExistingShell.tsx"}],
    }
    return _plan(compile_engineering_acceptance([task])[0])


class FrontendShellPrerequisiteTests(unittest.TestCase):
    def test_ready_main_and_auth_provide_same_prerequisite_contract(self) -> None:
        """两类真实模板证据均提供 shell 能力；空历史不创建任何 shell 任务或需求。"""

        for variant in ("main", "auth"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                readiness = _ready_template(root)
                if variant == "main":
                    write_json(root, ".xcodeagent/plans/product-plan.json", {
                        "schema_version": "product-plan.v5", "confirmation_status": "confirmed",
                        "pages": [{"pageId": "orders", "name": "订单", "path": "/orders"}],
                    })
                    write_json(root, ".xcodeagent/specs/ui-designs.json", {
                        "schema_version": "ui-manifest.v3", "confirmation_status": "skipped",
                    })
                    (root / "frontend/src/constants/menus.ts").write_text("export const BIZ_MENUS = [];", encoding="utf-8")
                    prepare_application_template_generation(root, {
                        "targets": {name: {"status": "succeeded", "attempt": 1, "branch": "main"} for name in ("frontend", "backend")},
                    })
                    validate_application_template_generation(root)
                    readiness = inspect_template_generation_readiness(root)
                plan = project_plan()
                snapshot = workspace_snapshot()
                skeleton = ensure_build_unit_skeleton(plan, snapshot)
                context = {**build_context(plan, execution_scope()), "template_variant": variant}
                before = deepcopy(skeleton)
                files = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
                facts = resolve_template_prerequisite_facts(
                    unit_skeleton=skeleton, build_context=context,
                    workspace_snapshot=snapshot, template_readiness=readiness,
                )
                requirements = resolve_generation_requirements(
                    required_unit_ids=context["required_unit_ids"], build_execution_scope=execution_scope(),
                    unit_skeleton=skeleton, reuse_facts=facts, formal_target=plan,
                )
                self.assertEqual(facts.issues, ())
                self.assertEqual(facts.external_capabilities[0].capability_id, "frontend.shell.ready")
                self.assertEqual(requirements.generation_strategy_by_unit[SHELL], "prerequisite_only")
                self.assertEqual(requirements.generation_requirements_by_unit[SHELL], ())
                self.assertNotIn(SHELL, requirements.planning_unit_ids)
                self.assertEqual({key: skeleton["build_units"][SHELL][key] for key in SHELL_STATE}, SHELL_STATE)
                self.assertEqual(skeleton["build_units"][SHELL]["task_ids"], [])
                self.assertEqual(skeleton, before)
                self.assertEqual(files, {path: path.read_bytes() for path in root.rglob("*") if path.is_file()})

    def test_historical_status_does_not_change_shell_prerequisite(self) -> None:
        """历史 pending、failed、completed 均不参与模板能力和 shell 生成决策。"""

        with tempfile.TemporaryDirectory() as directory:
            readiness = _ready_template(Path(directory))
            plan = project_plan()
            snapshot = workspace_snapshot()
            context = {**build_context(plan, execution_scope()), "template_variant": "auth"}
            previous_facts = None
            for status in ("pending", "failed", "completed"):
                with self.subTest(status=status):
                    history = _history(status)
                    skeleton = ensure_build_unit_skeleton(plan, snapshot, history)
                    reused = ensure_build_unit_skeleton(plan, snapshot, skeleton)
                    facts = resolve_reuse_facts(
                        confirmed_plan=history, unit_skeleton=reused, build_context=context,
                        workspace_snapshot=snapshot, formal_plan=plan, template_readiness=readiness,
                    )
                    self.assertEqual(facts.issues, ())
                    self.assertEqual(facts.retained_task_ids_by_unit[SHELL], ("historical-shell",))
                    if previous_facts is not None:
                        self.assertEqual(facts, previous_facts)
                    previous_facts = facts
                    for target in ({"type": "page", "id": "orders"}, {"type": "application", "id": "application"}):
                        self.assertNotIn(SHELL, _replaceable_unit_ids(reused, {**context, "target": target}, set(context["required_unit_ids"])))
                    self.assertEqual({key: reused["build_units"][SHELL][key] for key in SHELL_STATE}, SHELL_STATE)

    def test_missing_template_blocks_even_with_completed_historical_shell(self) -> None:
        """历史执行成功和文件名线索均不能抵消真实模板门禁失败。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = _state(root)
            write_json(root, PLAN_PATH, _history("completed"))
            before = (root / PLAN_PATH).read_bytes()
            with patch("app.graph.nodes.tasks.prepare_build_tasks_with_main_agent") as model:
                result = prepare_build_tasks(state)
            model.assert_not_called()
            self.assertEqual(result["clarification"]["mode"], "build_prerequisite_error")
            self.assertIn("manifest", str(result["clarification"]["errors"]))
            self.assertEqual((root / PLAN_PATH).read_bytes(), before)
            plan = state["project_plan"]
            skeleton = ensure_build_unit_skeleton(plan, workspace_snapshot())
            context = build_context(plan, execution_scope())
            facts = resolve_template_prerequisite_facts(
                unit_skeleton=skeleton, build_context=context, workspace_snapshot=workspace_snapshot(),
                template_readiness=inspect_template_generation_readiness(root),
            )
            self.assertEqual(facts.external_capabilities, ())
            self.assertEqual(facts.issues[0].level, "pre_generation")
            self.assertFalse(facts.issues[0].retryable)
            with self.assertRaises(GenerationRequirementsError):
                resolve_generation_requirements(
                    required_unit_ids=context["required_unit_ids"], build_execution_scope=execution_scope(),
                    unit_skeleton=skeleton, reuse_facts=facts, formal_target=plan,
                )

    def test_real_prepare_excludes_shell_generation_and_preserves_architecture_edge(self) -> None:
        """真实准备链路接受首次规划或 failed shell 历史，模型输入和 Task 依赖都排除 shell。"""

        for has_history in (False, True):
            with self.subTest(history=has_history), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _ready_template(root)
                state = _state(root)
                if has_history:
                    write_json(root, PLAN_PATH, _history("failed"))
                candidates = candidate_tasks(build_context(state["project_plan"], execution_scope()))
                with patch("app.agents.main.task_preparer._invoke_live_main_agent", return_value=json.dumps({"tasks": candidates})) as model:
                    result = prepare_build_tasks(state)
                self.assertEqual(result.get("clarification", {}).get("mode"), "build_task_plan_confirmation", result)
                model.assert_called_once()
                context = model.call_args.kwargs["build_context"]
                self.assertNotIn(SHELL, context["planning_unit_ids"])
                self.assertNotIn(SHELL, model.call_args.args[0]["allowed_unit_ids"])
                self.assertEqual(context["external_capabilities"][0]["capability_id"], "frontend.shell.ready")
                compiled = result["build_task_plan"]
                self.assertIn({"from": SHELL, "to": "page:orders", "type": "depends_on"}, compiled["unit_graph"]["edges"])
                page = compiled["task_registry"]["orders:page"]
                self.assertIn(SHELL, page["unit_dependencies"])
                self.assertNotIn(SHELL, page["missing_unit_dependencies"])
                self.assertNotIn("historical-shell", page["dependencies"])
                shell_tasks = [task for task in compiled["task_registry"].values() if task["unit_id"] == SHELL]
                self.assertEqual(len(shell_tasks), int(has_history))
                if has_history:
                    self.assertEqual(shell_tasks[0]["status"], "failed")
                self.assertEqual({key: compiled["build_units"][SHELL][key] for key in SHELL_STATE}, SHELL_STATE)

    def test_shell_candidate_is_explicitly_rejected(self) -> None:
        """模型误输出 shell 时明确报错，不能静默丢弃或解释为模板修复。"""

        task = _task("illegal-shell", unit_id=SHELL)
        self.assertIn("prerequisite_only", str(build_task_candidate_contract_errors({"tasks": [task]})))
        with self.assertRaisesRegex(ValueError, "prerequisite_only"):
            _merge_prepared_scope_tasks({}, {"tasks": [task]}, {"required_unit_ids": [SHELL]})

    def test_model_prompt_keeps_shell_out_of_generation_units(self) -> None:
        """页面、接口和组合 Prompt 均剔除 shell 生成资格，架构输入保持只读。"""

        for mode in ("page", "endpoint", "combined"):
            for planning_units in (None, [], [SHELL, "page:orders"]):
                with self.subTest(mode=mode, planning_units=planning_units):
                    context = {"required_unit_ids": [SHELL, "page:orders"]}
                    if planning_units is not None:
                        context["planning_unit_ids"] = planning_units
                    before = deepcopy(context)
                    prompt_context = scoped_prompt_build_context(context, mode)
                    self.assertNotIn(SHELL, prompt_context["required_unit_ids"])
                    self.assertNotIn(SHELL, prompt_context.get("planning_unit_ids", []))
                    self.assertEqual(context, before)

    def test_template_becoming_unready_blocks_endpoint_generation(self) -> None:
        """首道门禁后模板失效时，Endpoint Scope 也必须在模型调用前停止。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready = _ready_template(root)
            state = _state(root)
            state["build_execution_scope"] = execution_scope("endpoint")
            with patch("app.graph.nodes.tasks.inspect_template_generation_readiness", side_effect=[ready, {"ready": False, "errors": ["模板失效"]}]), patch(
                "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            ) as model:
                result = prepare_build_tasks(state)
            model.assert_not_called()
            self.assertEqual(result["clarification"]["issues"][0]["code"], "WORKSPACE_TEMPLATE_NOT_READY")
            self.assertFalse((root / PLAN_PATH).exists())

    def test_missing_workspace_revision_blocks_before_model(self) -> None:
        """模板已就绪但快照证据无身份时不能发布 shell 能力或进入模型生成。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _ready_template(root)
            state = _state(root)
            state["workspace_snapshot"] = {}
            with patch("app.graph.nodes.tasks.prepare_build_tasks_with_main_agent") as model:
                result = prepare_build_tasks(state)
            model.assert_not_called()
            issue = result["clarification"]["issues"][0]
            self.assertEqual(issue["code"], "WORKSPACE_EVIDENCE_IDENTITY_MISSING")
            self.assertEqual(issue["level"], "pre_generation")
            self.assertFalse(issue["retryable"])
            self.assertFalse((root / PLAN_PATH).exists())

    def test_not_ready_without_error_text_still_blocks(self) -> None:
        """ready=False 是明确失败，即便 errors 数组为空也不能被视为就绪。"""

        with tempfile.TemporaryDirectory() as directory:
            state = _state(Path(directory))
            with patch("app.graph.nodes.tasks.inspect_template_generation_readiness", return_value={"ready": False, "errors": []}), patch(
                "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            ) as model:
                result = prepare_build_tasks(state)
            model.assert_not_called()
            self.assertEqual(result["clarification"]["mode"], "build_prerequisite_error")
            self.assertIn("模板前置门禁未就绪", str(result["clarification"]["errors"]))

    def test_invalid_formal_plan_blocks_before_generation_and_is_not_overwritten(self) -> None:
        """正式文件存在但非 ConfirmedPlan 必须阻断；有效 checkpoint 和 pending sidecar 均不能救场。"""

        invalid_plans = [
            "{broken", "[]", json.dumps({**_history("failed"), "confirmation_status": "pending"}),
            json.dumps({**_history("failed"), "status": "failed"}),
            json.dumps({**_history("failed"), "schema_version": "invalid"}),
            json.dumps({**_history("failed"), "task_graph": {"validation": {"is_valid": False}}}),
        ]
        for contents in invalid_plans:
            with self.subTest(contents=contents[:60]), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _ready_template(root)
                state = _state(root)
                state["build_task_plan"] = _history("completed")
                write_json(root, ".xcodeagent/plans/build-task-plan.pending.json", _history("completed"))
                (root / PLAN_PATH).write_text(contents, encoding="utf-8")
                with patch("app.graph.nodes.tasks.prepare_build_tasks_with_main_agent") as model:
                    result = prepare_build_tasks(state)
                model.assert_not_called()
                self.assertEqual(result["clarification"]["mode"], "confirmed_baseline_error")
                self.assertEqual(result["clarification"]["code"], "confirmed_baseline_invalid")
                self.assertEqual(result["clarification"]["artifact"], PLAN_PATH)
                self.assertFalse(result["build_task_plan_persisted"])
                self.assertIn("ConfirmedPlan", str(result["clarification"]["errors"]))
                self.assertEqual((root / PLAN_PATH).read_text(encoding="utf-8"), contents)


if __name__ == "__main__":
    unittest.main()
