from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory

from app.services import planning_run as planning_transitions
from app.services.build_task_progress import (
    BuildTaskProgressTracker,
    DAG_GENERATION_STAGES,
    PlanningRunProgressPublisher,
    build_task_artifacts,
    project_artifact_output,
    project_build_context_output,
    project_candidate_tasks_output,
    project_compiled_tasks_output,
    project_contract_validation_output,
    project_dag_validation_output,
    project_unit_skeleton_output,
)
from app.services.planning_run_controller import (
    PlanningRunController,
    PlanningRunProjectionError,
)
from app.services.planning_run_events import (
    CandidateInvalid,
    CandidateReady,
    GenerationStarted,
    GlobalCheckStarted,
    GlobalRepairStarted,
    RunFailed,
    UnitAttemptStarted,
    UnitValidationStarted,
)
from app.services.planning_run_progress import (
    DAG_GENERATION_SNAPSHOT_SCHEMA_VERSION,
    project_planning_run_progress,
)
from tests.planning_run_fixtures import (
    AT,
    candidate,
    exhausted,
    identity,
    issue,
    ready,
    repair_decision,
    run,
    unit,
)


class BuildTaskProgressTests(unittest.TestCase):
    def test_tracker_emits_complete_ordered_snapshots(self) -> None:
        """每次更新都发送固定七阶段完整快照，并保留稳定顺序。"""

        events: list[dict] = []
        tracker = BuildTaskProgressTracker(events.append)

        tracker.start("unit_skeleton", "正在生成骨架")
        tracker.complete("unit_skeleton", "骨架完成")
        tracker.start("build_context", "正在解析上下文")

        self.assertEqual(len(events), 3)
        self.assertEqual(
            [stage["id"] for stage in events[-1]["dag_generation"]["stages"]],
            [stage_id for stage_id, _ in DAG_GENERATION_STAGES],
        )
        self.assertEqual(events[-1]["dag_generation"]["stages"][0]["status"], "completed")
        self.assertEqual(events[-1]["dag_generation"]["stages"][1]["status"], "running")

    def test_snapshot_projects_topological_tasks_and_safe_fields(self) -> None:
        """有效 DAG 按拓扑序投射任务，且不携带模型原文和内部 JSON。"""

        tracker = BuildTaskProgressTracker()
        plan = {
            "agent_note": "secret raw model response",
            "build_units": {"page:home": {"id": "page:home"}},
            "task_registry": {
                "page": {
                    "id": "page",
                    "title": "实现首页",
                    "owner": "frontend",
                    "status": "pending",
                    "dependencies": ["api"],
                    "change_scope": [{"path": "frontend/src/pages/Home.tsx"}],
                    "acceptance_criteria": ["首页可渲染"],
                },
                "api": {
                    "id": "api",
                    "title": "实现首页 API",
                    "owner": "backend",
                    "status": "pending",
                    "dependencies": [],
                    "change_scope": [{"path": "backend/app/api.py"}],
                    "acceptance_criteria": ["接口返回成功"],
                },
            },
            "task_graph": {
                "nodes": ["page", "api"],
                "topological_order": ["api", "page"],
                "edges": [{"from": "api", "to": "page"}],
                "validation": {"is_valid": True, "errors": []},
            },
            "summary": {"frontend": 1, "data_source": 1},
            "execution": {"batches": [{"tasks": ["api"]}, {"tasks": ["page"]}]},
        }

        tracker.complete("task_compilation", "任务已编译", build_task_plan=plan)
        snapshot = tracker.snapshot()

        self.assertEqual([task["id"] for task in snapshot["tasks"]], ["api", "page"])
        self.assertEqual(snapshot["tasks"][1]["changePaths"], ["frontend/src/pages/Home.tsx"])
        self.assertEqual(snapshot["summary"]["batchCount"], 2)
        self.assertNotIn("agent_note", str(snapshot))
        self.assertNotIn(".json", str(snapshot))

    def test_invalid_dag_keeps_every_registry_task(self) -> None:
        """无效 DAG 不得按部分拓扑序裁掉尚未排序的注册任务。"""

        tracker = BuildTaskProgressTracker()
        plan = {
            "task_registry": {
                "first": {"id": "first", "title": "第一项", "status": "pending"},
                "second": {"id": "second", "title": "第二项", "status": "pending"},
            },
            "task_graph": {
                "nodes": ["first", "second"],
                "topological_order": ["first"],
                "validation": {"is_valid": False, "errors": ["cycle"]},
            },
        }

        tracker.fail("dag_validation", "存在循环", build_task_plan=plan)

        self.assertEqual(
            [task["id"] for task in tracker.snapshot()["tasks"]],
            ["first", "second"],
        )

    def test_artifacts_expose_only_json_confirmation_summary(self) -> None:
        """产物投影只公开当前 JSON 计划和确认状态，不再生成 Markdown。"""

        artifacts = build_task_artifacts(
            {
                "confirmation_status": "pending",
                "build_execution_scope": {"type": "application", "targetId": "application"},
            }
        )

        self.assertEqual(len(artifacts), 1)
        self.assertNotIn("path", artifacts[0])
        self.assertEqual(artifacts[0]["kind"], "json")
        self.assertEqual(artifacts[0]["confirmationStatus"], "pending")

    def test_stage_outputs_are_frozen_and_cover_every_generation_phase(self) -> None:
        """每个阶段拥有独立结构化产物，后续计划变化不得覆盖已完成阶段。"""

        tracker = BuildTaskProgressTracker()
        plan = {
            "unit_skeleton": {"reused": True},
            "build_units": {
                "frontend:shell": {
                    "kind": "frontend",
                    "status": "prepared",
                    "task_ids": ["shell"],
                }
            },
            "unit_graph": {
                "schema_version": "build-unit-graph.v3",
                "edges": [{"from": "application:root", "to": "frontend:shell", "type": "contains"}],
                "validation": {"is_valid": True, "errors": []},
            },
            "task_registry": {
                "shell": {
                    "id": "shell",
                    "title": "生成壳页面",
                    "owner": "frontend",
                    "status": "pending",
                    "dependencies": [],
                    "change_scope": [{"path": "Frontend/src/App.tsx"}],
                    "acceptance_criteria": ["可启动"],
                }
            },
            "task_graph": {
                "roots": ["shell"],
                "leaves": ["shell"],
                "topological_order": ["shell"],
                "edges": [],
                "validation": {"is_valid": True, "errors": []},
            },
            "execution": {"batches": [{"mode": "serial", "tasks": ["shell"]}]},
            "summary": {"frontend": 1, "backend": 0, "database": 0},
        }

        unit_output = project_unit_skeleton_output(plan)
        tracker.complete(
            "unit_skeleton",
            "骨架完成",
            build_task_plan=plan,
            output=unit_output,
        )
        unit_output["units"][0]["status"] = "tampered"
        plan["build_units"]["frontend:shell"]["status"] = "completed"
        tracker.complete(
            "build_context",
            "上下文完成",
            build_task_plan=plan,
            output=project_build_context_output(
                {
                    "target": {"type": "page", "id": "home"},
                    "required_unit_ids": ["frontend:shell"],
                    "endpoint_ids": [],
                },
                plan,
            ),
        )
        tracker.complete(
            "contract_validation",
            "契约完成",
            output=project_contract_validation_output({}, []),
        )
        tracker.complete("model_planning", "候选任务完成", output=project_candidate_tasks_output(plan))
        tracker.complete("task_compilation", "编译完成", output=project_compiled_tasks_output(plan))
        tracker.complete("dag_validation", "校验完成", output=project_dag_validation_output(plan))
        artifacts = build_task_artifacts(
            {
                "confirmation_status": "pending",
                "build_execution_scope": {"type": "page", "targetId": "home"},
            }
        )
        tracker.complete(
            "artifact_persistence",
            "产物完成",
            artifacts=artifacts,
            output=project_artifact_output(artifacts),
        )

        snapshot = tracker.snapshot()
        outputs = {stage["id"]: stage.get("output") for stage in snapshot["stages"]}
        self.assertEqual(
            {output["kind"] for output in outputs.values() if output},
            {
                "unit_graph",
                "build_context",
                "contract_validation",
                "candidate_tasks",
                "compiled_tasks",
                "dag_validation",
                "artifacts",
            },
        )
        self.assertEqual(outputs["unit_skeleton"]["units"][0]["status"], "prepared")
        self.assertEqual(outputs["task_compilation"]["tasks"][0]["id"], "shell")
        self.assertEqual(outputs["artifact_persistence"]["count"], 1)
        self.assertEqual(snapshot["tasks"][0]["id"], "shell")
        self.assertEqual(snapshot["artifacts"][0]["id"], "build_task_plan")

    def test_failed_stage_keeps_partial_output_and_failure_detail(self) -> None:
        """失败阶段仍保留已投射产物，供前端展开查看失败证据。"""

        tracker = BuildTaskProgressTracker()
        tracker.fail(
            "contract_validation",
            "契约校验发现 1 个问题：缺少 endpoint",
            output=project_contract_validation_output(
                {
                    "endpoint_ids": ["orders.list"],
                },
                ["缺少 endpoint: orders.list"],
            ),
        )

        stage = next(
            stage for stage in tracker.snapshot()["stages"] if stage["id"] == "contract_validation"
        )
        self.assertEqual(stage["status"], "failed")
        self.assertIn("缺少 endpoint", stage["detail"])
        self.assertEqual(stage["output"]["issues"], ["缺少 endpoint: orders.list"])

    def test_projection_caps_stage_records_and_text(self) -> None:
        """阶段列表复用 200 条记录和 1000 字符字段上限。"""

        registry = {
            f"task-{index}": {
                "id": f"task-{index}",
                "title": f"任务 {index}",
                "owner": "frontend",
                "status": "pending",
            }
            for index in range(201)
        }
        plan = {
            "task_registry": registry,
            "task_graph": {
                "nodes": list(registry),
                "validation": {"is_valid": True, "errors": []},
            },
            "build_units": {
                f"unit-{index}": {"kind": "frontend", "status": "prepared"}
                for index in range(201)
            },
        }

        self.assertEqual(len(project_candidate_tasks_output(plan)["tasks"]), 200)
        self.assertEqual(len(project_unit_skeleton_output(plan)["units"]), 200)

        tracker = BuildTaskProgressTracker()
        tracker.fail("unit_skeleton", "x" * 2_000)
        detail = tracker.snapshot()["stages"][0]["detail"]
        self.assertEqual(len(detail), 1_000)

    def test_edge_projection_is_bounded(self) -> None:
        """依赖边超过上限时只发送前 500 条并标记截断。"""

        plan = {
            "unit_graph": {
                "edges": [
                    {"from": f"unit:{index}", "to": f"unit:{index + 1}", "type": "depends_on"}
                    for index in range(501)
                ],
                "validation": {"is_valid": True, "errors": []},
            },
            "build_units": {},
        }
        output = project_unit_skeleton_output(plan)
        self.assertEqual(len(output["edges"]["items"]), 500)
        self.assertTrue(output["edges"]["truncated"])


class PlanningRunProgressProjectorTests(unittest.TestCase):
    """验证 PlanningRun 是新进度快照的唯一事实来源。"""

    def test_projects_every_unit_status_without_predictive_percentage(self) -> None:
        """七种 Unit 状态均原样投影，汇总只使用离散事实计数。"""

        pending = run()
        generating = planning_transitions.begin_generation(run(), at=AT)
        attempt = identity(generating)
        generating = planning_transitions.mark_unit_generating(
            generating, attempt, at=AT
        )
        validating = planning_transitions.mark_unit_validating(
            generating, attempt, at=AT
        )
        snapshots = {
            "not_required": run(unit("frontend:shell", "prerequisite_only")),
            "pending": pending,
            "generating": generating,
            "validating": validating,
            "candidate_ready": ready(
                planning_transitions.begin_generation(run(), at=AT)
            ),
            "round_exhausted": exhausted(
                planning_transitions.begin_generation(run(), at=AT)
            ),
            "aborted": planning_transitions.cancel(run(), at=AT),
        }

        for expected_status, snapshot in snapshots.items():
            with self.subTest(status=expected_status):
                projected = project_planning_run_progress(snapshot)
                self.assertEqual(projected["units"][0]["status"], expected_status)
                self.assertFalse(
                    any("percent" in key.lower() for key in _nested_keys(projected))
                )

    def test_reuse_and_generate_projects_counts_without_candidate_body(self) -> None:
        """复用并新增只暴露数量，Candidate Task 正文和内部身份不进入投影。"""

        state = planning_transitions.begin_generation(
            run(unit(retained=("confirmed:one", "confirmed:two"))),
            at=AT,
        )
        state, attempt = _start_attempt(state)
        state = planning_transitions.mark_unit_validating(state, attempt, at=AT)
        generated = candidate(state).model_copy(
            update={
                "tasks": (
                    {
                        "id": "task:secret",
                        "unit_id": "page:orders",
                        "body": "candidate-body-must-stay-private",
                    },
                )
            }
        )
        state = planning_transitions.record_candidate_ready(state, generated, at=AT)

        projected = project_planning_run_progress(state)
        projected_unit = projected["units"][0]
        self.assertEqual(projected_unit["participation"], "reuse_and_generate")
        self.assertEqual(projected_unit["retainedTaskCount"], 2)
        self.assertEqual(projected_unit["candidateTaskCount"], 1)
        self.assertNotIn("candidate-body-must-stay-private", str(projected))
        self.assertNotIn("latestCandidateId", projected_unit)
        self.assertNotIn("candidates", projected)

    def test_shell_is_projected_as_taskless_prerequisite(self) -> None:
        """frontend:shell 保持 prerequisite_only/not_required 且不制造 Candidate。"""

        projected = project_planning_run_progress(
            run(
                unit(
                    "frontend:shell",
                    "prerequisite_only",
                )
            )
        )

        shell = projected["units"][0]
        self.assertEqual(shell["id"], "frontend:shell")
        self.assertEqual(shell["participation"], "prerequisite_only")
        self.assertEqual(shell["generationStrategy"], "prerequisite_only")
        self.assertEqual(shell["status"], "not_required")
        self.assertEqual(shell["candidateTaskCount"], 0)
        self.assertEqual(shell["localAttemptLimit"], 0)
        self.assertEqual(projected["summary"]["readyUnitCount"], 1)

    def test_global_repair_projects_round_and_safe_issues(self) -> None:
        """Global reopen 后透传轮次和问题，但省略 Candidate 身份与诊断上下文。"""

        state = ready(planning_transitions.begin_generation(run(), at=AT))
        state = planning_transitions.begin_global_check(state, at=AT)
        global_issue = issue(level="global").model_copy(
            update={
                "task_ids": ("candidate-secret",),
                "details": {"candidateExcerpt": "private-fragment"},
            }
        )
        state = planning_transitions.begin_global_repair(
            state,
            repair_decision(global_issue),
            at=AT,
        )

        projected = project_planning_run_progress(state)
        self.assertEqual(projected["globalRepairRound"], 1)
        self.assertEqual(projected["globalRepairLimit"], 2)
        self.assertEqual(projected["phase"], "generating_units")
        self.assertEqual(projected["globalIssues"][0]["code"], global_issue.code)
        self.assertNotIn("taskIds", projected["globalIssues"][0])
        self.assertNotIn("candidate-secret", str(projected))
        self.assertNotIn("details", projected["globalIssues"][0])
        self.assertNotIn("private-fragment", str(projected))
        self.assertEqual(projected["units"][0]["generationRound"], 2)

    def test_failed_cancelled_and_round_exhausted_keep_run_semantics(self) -> None:
        """终态原样透传，Unit 轮次耗尽本身不得伪装成 Run failed。"""

        round_exhausted = exhausted(
            planning_transitions.begin_generation(run(), at=AT)
        )
        failed = planning_transitions.fail(
            planning_transitions.begin_generation(run(), at=AT),
            issue(level="system", category="infrastructure", retryable=False),
            at=AT,
        )
        cancelled = planning_transitions.cancel(
            planning_transitions.begin_generation(run(), at=AT),
            at=AT,
        )

        exhausted_projection = project_planning_run_progress(round_exhausted)
        self.assertEqual(exhausted_projection["status"], "active")
        self.assertEqual(
            exhausted_projection["summary"]["roundExhaustedUnitCount"], 1
        )
        failed_projection = project_planning_run_progress(failed)
        cancelled_projection = project_planning_run_progress(cancelled)
        self.assertEqual(failed_projection["status"], "failed")
        self.assertEqual(failed_projection["summary"]["failureIssueCount"], 1)
        self.assertEqual(cancelled_projection["status"], "cancelled")
        self.assertEqual(cancelled_projection["summary"]["failureIssueCount"], 0)
        self.assertEqual(cancelled_projection["units"][0]["status"], "aborted")

    def test_revision_and_input_snapshot_are_unchanged(self) -> None:
        """revision 原样透传，修改导出对象也不能反向改变冻结 Run。"""

        state = planning_transitions.begin_generation(run(), at=AT)
        before = state.model_dump_json()
        projected = project_planning_run_progress(state)

        self.assertEqual(projected["schemaVersion"], DAG_GENERATION_SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(
            set(projected),
            {
                "schemaVersion",
                "planningRunId",
                "revision",
                "status",
                "phase",
                "globalRepairRound",
                "globalRepairLimit",
                "units",
                "globalIssues",
                "summary",
            },
        )
        self.assertEqual(projected["planningRunId"], state.planning_run_id)
        self.assertEqual(projected["revision"], state.revision)
        projected["units"][0]["status"] = "tampered"
        self.assertEqual(state.model_dump_json(), before)


class PlanningRunProgressStreamIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """验证 Controller 关键转换经现有 custom event 发送完整新快照。"""

    def setUp(self) -> None:
        """为每个 Controller 提供隔离的 PlanningRun 持久化目录。"""

        self.directory = TemporaryDirectory()
        self.events: list[dict] = []
        self.publisher = PlanningRunProgressPublisher(self.events.append)
        self.controller = PlanningRunController(
            run(),
            {"workspace": self.directory.name},
            publish=self.publisher,
        )

    def tearDown(self) -> None:
        """释放测试创建的临时 PlanningRun 文件。"""

        self.directory.cleanup()

    async def test_start_emits_existing_event_type_with_complete_snapshot(self) -> None:
        """Generation start 使用既有事件类型发送 revision 1 完整快照。"""

        await self.controller.apply(GenerationStarted(at=AT))

        event = self.events[-1]
        snapshot = event["dag_generation"]
        self.assertEqual(event["type"], "prepare_build_tasks.progress")
        self.assertEqual(event["node_name"], "prepare_build_tasks")
        self.assertEqual(event["status"], "running")
        self.assertEqual(snapshot["revision"], 1)
        self.assertEqual(snapshot["phase"], "generating_units")
        self.assertEqual(
            set(snapshot),
            {
                "schemaVersion",
                "planningRunId",
                "revision",
                "status",
                "phase",
                "globalRepairRound",
                "globalRepairLimit",
                "units",
                "globalIssues",
                "summary",
            },
        )

    async def test_unit_transitions_each_emit_one_complete_snapshot(self) -> None:
        """生成、校验和 Candidate ready 各自产生一次完整状态更新。"""

        await self.controller.apply(GenerationStarted(at=AT))
        attempt = identity(self.controller.snapshot)
        await self.controller.apply(UnitAttemptStarted(identity=attempt, at=AT))
        await self.controller.apply(UnitValidationStarted(identity=attempt, at=AT))
        await self.controller.apply(
            CandidateReady(candidate=candidate(self.controller.snapshot), at=AT)
        )

        self.assertEqual(
            [event["dag_generation"]["units"][0]["status"] for event in self.events],
            ["pending", "generating", "validating", "candidate_ready"],
        )
        self.assertTrue(
            all(event["dag_generation"]["summary"] for event in self.events)
        )

    async def test_retry_emits_invalid_result_and_next_attempt(self) -> None:
        """Local 内容失败与下一 Attempt 分开发送，不生成 token 级事件。"""

        await self.controller.apply(GenerationStarted(at=AT))
        first = identity(self.controller.snapshot)
        await self.controller.apply(UnitAttemptStarted(identity=first, at=AT))
        await self.controller.apply(
            CandidateInvalid(
                candidate=candidate(self.controller.snapshot, valid=False),
                at=AT,
            )
        )
        second = identity(self.controller.snapshot)
        await self.controller.apply(UnitAttemptStarted(identity=second, at=AT))

        snapshots = [event["dag_generation"] for event in self.events]
        self.assertEqual(len(snapshots), 4)
        self.assertEqual(snapshots[-2]["units"][0]["status"], "pending")
        self.assertEqual(snapshots[-2]["units"][0]["attemptInRound"], 1)
        self.assertEqual(snapshots[-1]["units"][0]["status"], "generating")
        self.assertEqual(snapshots[-1]["units"][0]["attemptInRound"], 2)

    async def test_global_repair_emits_round_and_full_issue_snapshot(self) -> None:
        """Global check 与 reopen 都发送完整快照并保留真实 repair 轮次。"""

        await self.controller.apply(GenerationStarted(at=AT))
        attempt = identity(self.controller.snapshot)
        await self.controller.apply(UnitAttemptStarted(identity=attempt, at=AT))
        await self.controller.apply(UnitValidationStarted(identity=attempt, at=AT))
        await self.controller.apply(
            CandidateReady(candidate=candidate(self.controller.snapshot), at=AT)
        )
        await self.controller.apply(GlobalCheckStarted(at=AT))
        global_issue = issue(level="global")
        await self.controller.apply(
            GlobalRepairStarted(decision=repair_decision(global_issue), at=AT)
        )

        checking, repairing = self.events[-2:]
        self.assertEqual(checking["dag_generation"]["phase"], "global_check")
        self.assertEqual(repairing["dag_generation"]["globalRepairRound"], 1)
        self.assertEqual(repairing["dag_generation"]["globalIssues"][0]["code"], global_issue.code)
        self.assertEqual(repairing["dag_generation"]["units"][0]["generationRound"], 2)

    async def test_failed_emit_failure_does_not_rollback_planning_state(self) -> None:
        """Run failure 可发送；writer 失败时已提交的 PlanningRun 仍保持正确终态。"""

        await self.controller.apply(GenerationStarted(at=AT))
        fatal = issue(level="system", category="infrastructure", retryable=False)
        await self.controller.apply(RunFailed(issue=fatal, at=AT))

        event = self.events[-1]
        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["dag_generation"]["status"], "failed")

        def failing_writer(event: dict) -> None:
            """仅在 failed 快照发布时模拟 custom stream 写入故障。"""

            if event["dag_generation"]["status"] == "failed":
                raise OSError("stream unavailable")

        publisher = PlanningRunProgressPublisher(failing_writer)
        controller = PlanningRunController(
            run(),
            {"workspace": self.directory.name},
            publish=publisher,
        )
        await controller.apply(GenerationStarted(at=AT))
        with self.assertRaises(PlanningRunProjectionError):
            await controller.apply(RunFailed(issue=fatal, at=AT))
        self.assertEqual(controller.snapshot.status, "failed")
        self.assertEqual(controller.snapshot.revision, 2)

    async def test_cancelled_emits_terminal_full_snapshot(self) -> None:
        """取消通过同一事件类型发送 cancelled Run 和 aborted Unit。"""

        await self.controller.apply(GenerationStarted(at=AT))
        await self.controller.cancel(at=AT)

        event = self.events[-1]
        self.assertEqual(event["type"], "prepare_build_tasks.progress")
        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["dag_generation"]["status"], "cancelled")
        self.assertEqual(event["dag_generation"]["units"][0]["status"], "aborted")

    async def test_revision_order_is_strict_and_rejects_duplicate_emission(self) -> None:
        """正常提交严格递增，重复或倒序快照在写入 stream 前被拒绝。"""

        await self.controller.apply(GenerationStarted(at=AT))
        attempt = identity(self.controller.snapshot)
        await self.controller.apply(UnitAttemptStarted(identity=attempt, at=AT))
        await self.controller.apply(UnitValidationStarted(identity=attempt, at=AT))

        revisions = [event["dag_generation"]["revision"] for event in self.events]
        self.assertEqual(revisions, [1, 2, 3])
        with self.assertRaisesRegex(ValueError, "revision 必须严格递增"):
            await self.publisher(self.controller.projection)
        self.assertEqual(len(self.events), 3)


def _start_attempt(state):
    """为 projector 测试按领域规则启动当前 Unit 的下一 Attempt。"""

    attempt = identity(state)
    return planning_transitions.mark_unit_generating(state, attempt, at=AT), attempt


def _nested_keys(value) -> list[str]:
    """递归收集 JSON 投影键，验证不存在预测百分比字段。"""

    if isinstance(value, dict):
        return [
            str(key)
            for key in value
        ] + [
            nested
            for item in value.values()
            for nested in _nested_keys(item)
        ]
    if isinstance(value, list):
        return [nested for item in value for nested in _nested_keys(item)]
    return []


if __name__ == "__main__":
    unittest.main()
