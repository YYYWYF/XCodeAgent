"""T2.4 模板前置事实、shell 非生成边界及正式规划基线门禁。"""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from app.services.application_template_generation import (
    inspect_template_generation_readiness, prepare_application_template_generation,
    validate_application_template_generation,
)
from app.services.build_task_reuse import resolve_reuse_facts, resolve_template_prerequisite_facts
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.engineering_acceptance import compile_engineering_acceptance
from app.services.unit_generation_requirements import (
    GenerationRequirementsError, resolve_generation_requirements,
)
from tests.dag_planning_baseline_fixtures import (
    build_context, execution_scope, formal_artifacts, project_plan,
    workspace_snapshot, write_json,
)
from tests.test_build_task_reuse import _plan, _task
from tests.test_build_task_reuse_workspace import _ready_template


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
                    endpoint_designs=context["endpoint_designs"],
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
                    self.assertEqual({key: reused["build_units"][SHELL][key] for key in SHELL_STATE}, SHELL_STATE)



if __name__ == "__main__":
    unittest.main()
