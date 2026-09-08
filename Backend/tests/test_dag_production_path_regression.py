"""T11.6.1 production Workflow 到 legacy planner 的回归锚点。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from langgraph.checkpoint.memory import InMemorySaver

from app.graph.workflow import build_graph
from tests.dag_planning_baseline_fixtures import (
    execution_scope,
    formal_artifacts,
    project_plan,
    workspace_snapshot,
    write_json,
)
from tests.test_build_task_reuse_workspace import _ready_template


ARTIFACT_PATHS = {
    "requirement_spec": ".xcodeagent/specs/requirement-spec.json",
    "product_plan": ".xcodeagent/plans/product-plan.json",
    "ui_designs": ".xcodeagent/specs/ui-designs.json",
    "technical_plan": ".xcodeagent/plans/technical-plan.json",
}


class DagProductionPathRegressionTests(unittest.TestCase):
    """验证 production Workflow 的 DAG planning 实际调用边界。"""

    def test_production_workflow_does_not_call_legacy_scope_planner(self) -> None:
        """从 production 入口进入真实节点，并用抛错陷阱锁定 legacy 调用。"""

        plan = project_plan()
        scope = execution_scope()
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            for key, payload in formal_artifacts(plan).items():
                write_json(root, ARTIFACT_PATHS[key], payload)
            snapshot_path = write_json(
                root,
                ".xcodeagent/cache/workspace-snapshot.json",
                workspace_snapshot(),
            )

            prepare_build_tasks_with_main_agent = Mock(
                side_effect=AssertionError(
                    "production Workflow 仍调用 legacy scope-level planner"
                )
            )
            graph = build_graph(checkpointer=InMemorySaver())
            with patch(
                "app.graph.nodes.tasks.inspect_template_generation_readiness",
                return_value=_ready_template(root),
            ), patch(
                "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
                new=prepare_build_tasks_with_main_agent,
            ):
                graph.invoke(
                    {
                        "resume_from": "prepare_build_tasks",
                        "workspace": workspace,
                        "project_plan": plan,
                        "workspace_snapshot_path": str(snapshot_path),
                        "build_execution_scope": scope,
                    },
                    config={
                        "configurable": {
                            "thread_id": "t11-6-1-production-path-regression"
                        }
                    },
                )

            prepare_build_tasks_with_main_agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
