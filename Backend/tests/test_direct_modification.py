from __future__ import annotations

import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from app.agents.direct_modification import (
    DirectModificationDecision,
    _direct_modification_classifier_prompt,
    _partial_json_response_value,
    _data_source_direct_modification_prompt,
    _frontend_direct_modification_prompt,
    _normalize_direct_modification_decision,
    classify_direct_modification_intent,
    invoke_data_source_direct_modification,
    invoke_frontend_direct_modification,
    parse_direct_modification_agent_result,
)
from app.graph.direct_modification_workflow import (
    _route_backend,
    _route_classification,
    _route_direct_repair,
    _route_direct_entry,
    _route_frontend,
    _route_direct_validation,
    _route_scan_workspace,
    build_direct_modification_graph,
    direct_next_node_name,
)
from app.graph.nodes.direct_modification import (
    classify_direct_modification,
    execute_backend_direct_modification,
    execute_frontend_direct_modification,
    execute_workspace_direct_modification,
    finalize_direct_modification,
    respond_to_casual_conversation,
    respond_to_workspace_question,
    validate_direct_fix,
)
from app.graph.nodes.direct_repair import direct_modification_repair
from app.protocols.direct_modification import (
    DirectModificationInput,
    _report_custom_progress,
    _direct_confirmation_continuation,
    _conversation_request,
    build_conversation_ag_ui_stream,
    conversation_capabilities,
    conversation_input,
)
from app.protocols.direct_modification_projection import (
    direct_progress_payload,
    direct_node_process_step,
    direct_node_running_process_step,
    direct_node_started_event,
)
from app.services.direct_modification import validated_dynamic_workspace_paths
from app.tools.execute import ExecuteInput


class DirectModificationPromptTests(unittest.TestCase):
    """验证快速修改 Prompt 与正式生成 Prompt 保持隔离。"""

    def test_frontend_prompt_requires_two_builtin_skills(self) -> None:
        """前端写代码前必须完整读取两个指定内置 Skill。"""

        prompt = _frontend_direct_modification_prompt(
            user_request="修改统计卡片间距",
            conversation_summary="",
            backend_handoff=None,
        )

        self.assertIn(
            "/.xcodeagent/builtin-skills/code-block-template/SKILL.md",
            prompt,
        )
        self.assertIn(
            "/.xcodeagent/builtin-skills/react-develop-specification/SKILL.md",
            prompt,
        )
        self.assertIn("read_file(limit=400)", prompt)
        self.assertIn("task and write_todos are unavailable", prompt)
        self.assertIn("appropriate to the actual change scope", prompt)
        self.assertIn("avoid unrelated repository-wide scans", prompt)
        self.assertIn("Internal verification and self-repair", prompt)
        self.assertIn("repair the implementation", prompt)
        self.assertIn("rerun the relevant check", prompt)
        self.assertIn("Return status=completed only after", prompt)
        self.assertIn("transient read/search/tool error is not by itself a task failure", prompt)
        self.assertIn("`| head`", prompt)
        self.assertNotIn("timeout=120", prompt)
        self.assertNotIn("at most one focused", prompt)
        self.assertNotIn("Approved frontend tasks", prompt)
        self.assertNotIn("ProjectPlan context", prompt)
        self.assertNotIn("BuildTaskPlan summary", prompt)

    def test_frontend_direct_packet_prioritizes_source_candidates(self) -> None:
        """前端快速修改只授权源码根，并优先传递扫描得到的业务源码。"""

        with patch(
            "app.agents.direct_modification.invoke_small_task_agent",
            return_value='{"status":"already_satisfied"}',
        ) as invoke:
            invoke_frontend_direct_modification(
                user_request="把宠物照片卡片宽度改成200px",
                conversation_summary="",
                backend_handoff=None,
                candidate_files=[
                    "frontend/src/pages/PetPhotoList/index.tsx",
                    "frontend/node_modules/pkg/index.js",
                ],
                approved_paths=[
                    "frontend/vite.config.ts",
                    "frontend/node_modules/pkg/config.js",
                ],
                workspace="/workspace",
                selected_skill_names=[],
            )

        packet = invoke.call_args.kwargs["packet"]
        self.assertEqual(
            packet["allowedPaths"],
            [
                "Frontend/src/**",
                "frontend/src/**",
                "frontend/vite.config.ts",
            ],
        )
        self.assertEqual(
            packet["candidateFiles"],
            [
                "frontend/vite.config.ts",
                "frontend/src/pages/PetPhotoList/index.tsx",
            ],
        )

    def test_backend_prompt_has_no_required_builtin_skill(self) -> None:
        """后端 Prompt 保留执行约束，但不声明不存在的必读内置 Skill。"""

        prompt = _data_source_direct_modification_prompt(
            user_request="增加状态校验",
            conversation_summary="",
        )

        self.assertIn("no mandatory built-in skills", prompt)
        self.assertIn("backendHandoff", prompt)
        self.assertIn("Internal verification and self-repair", prompt)
        self.assertIn("repair the implementation", prompt)
        self.assertIn("rerun the relevant check", prompt)
        self.assertNotIn("/.xcodeagent/builtin-skills/", prompt)
        self.assertNotIn("Approved data-source tasks", prompt)

    def test_backend_direct_packet_adds_only_approved_config_path(self) -> None:
        """后端快速修改默认只写源码根，配置文件必须通过追加授权进入。"""

        with patch(
            "app.agents.direct_modification.invoke_small_task_agent",
            return_value='{"status":"already_satisfied"}',
        ) as invoke:
            invoke_data_source_direct_modification(
                user_request="更新 Maven 依赖配置",
                conversation_summary="",
                approved_paths=[
                    "backend/pom.xml",
                    "backend/node_modules/pkg/config.js",
                ],
                workspace="/workspace",
                selected_skill_names=[],
            )

        packet = invoke.call_args.kwargs["packet"]
        self.assertEqual(
            packet["allowedPaths"],
            [
                "Backend/app/**",
                "Backend/src/**",
                "Backend/tests/**",
                "backend/app/**",
                "backend/src/**",
                "backend/tests/**",
                "backend/pom.xml",
            ],
        )
        self.assertEqual(packet["candidateFiles"], ["backend/pom.xml"])

    def test_execute_tool_guidance_preserves_real_check_exit_code(self) -> None:
        """执行工具说明应阻止 Agent 用管道掩盖检查命令的退出码。"""

        description = ExecuteInput.model_fields["command"].description or ""

        self.assertIn("| head", description)
        self.assertIn("hide the real exit code", description)
        self.assertIn("pnpm typecheck", description)
        self.assertNotIn("npx tsc", description)

    def test_agent_result_requires_valid_json(self) -> None:
        """无效 Agent 文本必须被归一化为失败，而不是误报完成。"""

        result = parse_direct_modification_agent_result("done")

        self.assertEqual(result["status"], "failed")
        self.assertIn("JSON", result["failureReason"])

    def test_classifier_normalizes_unsafe_results_to_clarification(self) -> None:
        """未知归属、低置信度和无效分类字段都必须安全降级为等待补充。"""

        payloads = [
            {"route": "implementation_fix", "owner": "unknown", "confidence": 0.95},
            {"route": "implementation_fix", "owner": "frontend", "confidence": 0.64},
            {"route": "implementation_fix", "owner": "invalid", "confidence": 0.95},
            {"route": "clarification", "owner": "frontend", "confidence": 0.95},
            {"intent": "invalid", "owner": "frontend", "confidence": 0.95},
            {},
        ]

        for payload in payloads:
            with self.subTest(payload=payload):
                decision = _normalize_direct_modification_decision(payload)
                self.assertEqual(decision.scope, "clarification")
                self.assertTrue(decision.clarification_question)

    def test_classifier_replaces_english_clarification_with_chinese_copy(self) -> None:
        """模型返回英文澄清问题时必须转换为稳定的中文用户提示。"""

        decision = _normalize_direct_modification_decision(
            {
                "route": "clarification",
                "owner": "unknown",
                "confidence": 0.9,
                "reason": "The request does not describe a change.",
                "clarificationQuestion": (
                    "What would you like to modify? Please describe the change, location, "
                    "and expected behavior."
                ),
            }
        )

        self.assertEqual(
            decision.clarification_question,
            "请说明您想修改的具体内容，并补充修改位置和预期效果。",
        )

    def test_classifier_prompt_requests_direct_casual_response(self) -> None:
        """普通对话分类时应在同一次模型调用中产出可直接展示的回答。"""

        prompt = _direct_modification_classifier_prompt(
            user_request="你是谁",
            conversation_summary="",
            workspace_snapshot={
                "workspace_revision": "revision-1",
                "tech_stack": ["React"],
                "frontend": {
                    "pages": [
                        {"path": "frontend/src/pages/PetPhotoList/index.tsx"}
                    ],
                    "components": [
                        {
                            "path": "frontend/src/pages/PetPhotoList/PetCard.tsx",
                            "name": "PetCard",
                        }
                    ],
                },
            },
        )

        self.assertIn("answer the user's message directly in response", prompt)
        self.assertIn('"response":"final answer only for casual_chat"', prompt)
        self.assertIn("For every other intent, response must be an empty string", prompt)
        self.assertIn("latest user supplement", prompt)
        self.assertIn("do not ask the same clarification again", prompt)
        self.assertIn("PetPhotoList", prompt)
        self.assertIn("必须先判断用户请求是否改变了已确认的产品、设计或技术语义", prompt)
        self.assertIn("不能因为可以通过编辑或删除源文件实现", prompt)
        self.assertIn("formal_revision/design_stage_revision/requirement-spec", prompt)
        self.assertIn("frontend/backend owner 不能覆盖更高优先级的 formal_revision 判断", prompt)
        self.assertIn("every exact existing file required", prompt)
        self.assertIn("not limited to known config-file types", prompt)

    def test_classifier_prompt_routes_clear_requests_without_design_details(self) -> None:
        """明确的业务意图即使缺少实现细节也必须进入对应 workflow。"""

        prompt = _direct_modification_classifier_prompt(
            user_request="新增一个页面",
            conversation_summary="",
            workspace_snapshot=None,
        )

        self.assertIn(
            "conservative about write authorization, not about collecting design details",
            prompt,
        )
        self.assertIn("The downstream workflow owns requirements clarification", prompt)
        self.assertIn("'新增一个客户管理页面'", prompt)
        self.assertIn("route here and let the requirements workflow fill them in", prompt)
        self.assertIn("'订单支持批量归档'", prompt)
        self.assertIn("'订单卡片改成双列'", prompt)
        self.assertIn("'列表接口增加状态和时间筛选参数'", prompt)
        self.assertIn("'把 mock 数据换成 MySQL'", prompt)
        self.assertIn("只有连业务对象、动作或期望结果都无法识别时，才使用 clarification", prompt)
        self.assertIn("不能回答“无法安全执行”", prompt)
        self.assertIn("For formal_revision, keep clarificationQuestion and questions empty", prompt)
        self.assertIn("the next step is the formal revision confirmation", prompt)

    def test_classifier_normalizes_new_page_as_formal_revision_without_paths(self) -> None:
        """新增页面不需要源码路径即可归一化为需求阶段正式修订。"""

        decision = _normalize_direct_modification_decision(
            {
                "route": "formal_revision",
                "formalBranch": "design_stage_revision",
                "revisionType": "requirement_scope_change",
                "earliestArtifact": "requirement-spec",
                "owner": "none",
                "confidence": 0.92,
                "reason": "用户明确要新增页面，属于产品范围变化。",
                "candidatePaths": [],
                "affectedArtifactKeys": [],
                "affectedResourceKeys": [],
            },
            user_request="新增一个页面",
        )

        self.assertEqual(decision.intent, "formal_revision")
        self.assertEqual(decision.formal_branch.value, "design_stage_revision")
        self.assertEqual(decision.earliest_artifact.value, "requirement-spec")
        self.assertEqual(decision.target_paths, ())

    def test_classifier_normalizes_existing_page_ui_change_as_frontend_fix(self) -> None:
        """既有页面的 UI 微调必须直接进入当前前端小修改，不得回退 UI 设计阶段。"""

        decision = _normalize_direct_modification_decision(
            {
                "route": "formal_revision",
                "formalBranch": "design_stage_revision",
                "revisionType": "ui_visual_change",
                "earliestArtifact": "ui-design",
                "owner": "none",
                "confidence": 0.92,
                "reason": "页面布局发生变化",
                "candidatePaths": [],
                "affectedArtifactKeys": ["ui-design", "technical-plan"],
                "affectedResourceKeys": ["page:orders"],
            },
            user_request="把订单页改成双列布局",
        )

        self.assertEqual(decision.intent, "implementation_fix")
        self.assertEqual(decision.owner, "frontend")
        self.assertIsNone(decision.formal_branch)
        self.assertIsNone(decision.revision_type)
        self.assertIsNone(decision.earliest_artifact)

    def test_dynamic_path_authorization_accepts_non_whitelisted_existing_file(self) -> None:
        """动态授权不限制文件类型，但仍要求精确安全路径和磁盘存在性。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            config = root / "frontend" / "vite.config.ts"
            config.parent.mkdir(parents=True)
            config.write_text("export default {}\n", encoding="utf-8")
            dependency_config = root / "frontend" / "node_modules" / "pkg" / "config.js"
            dependency_config.parent.mkdir(parents=True)
            dependency_config.write_text("module.exports = {}\n", encoding="utf-8")
            env_file = root / "frontend" / ".env.local"
            env_file.write_text("TOKEN=secret\n", encoding="utf-8")
            custom_file = root / "frontend" / "tooling" / "custom.rules"
            custom_file.parent.mkdir(parents=True)
            custom_file.write_text("old-rule\n", encoding="utf-8")
            lock_file = root / "frontend" / "package-lock.json"
            lock_file.write_text("{}\n", encoding="utf-8")

            approved = validated_dynamic_workspace_paths(
                workspace=workspace,
                request="请修改前端的自定义工具规则和 Vite 配置",
                owner="frontend",
                target_paths=[
                    "frontend/tooling/custom.rules",
                    "frontend/vite.config.ts",
                    "frontend/node_modules/pkg/config.js",
                    "frontend/.env.local",
                    "frontend/missing.config.ts",
                    "frontend/*.config.ts",
                    "frontend/package-lock.json",
                    "/frontend/vite.config.ts",
                    "C:\\frontend\\vite.config.ts",
                ],
            )
            read_only = validated_dynamic_workspace_paths(
                workspace=workspace,
                request="请解释一下这个工程",
                owner="frontend",
                target_paths=["frontend/vite.config.ts"],
            )

        self.assertEqual(
            approved,
            ["frontend/tooling/custom.rules", "frontend/vite.config.ts"],
        )
        self.assertEqual(read_only, [])

    def test_classifier_stream_extracts_only_response_prefix(self) -> None:
        """分类 JSON 流式生成时只向用户暴露 response，不泄露路由字段。"""

        partial = '{"response":"你好，"route":"casual_chat"}'

        self.assertEqual(_partial_json_response_value(partial), "你好，")
        self.assertNotIn("casual_chat", _partial_json_response_value(partial))

    def test_classifier_empty_request_and_model_error_request_clarification(self) -> None:
        """空输入或分类模型异常时都必须返回可见的兜底澄清问题。"""

        empty_decision = classify_direct_modification_intent(user_request="   ")
        with patch(
            "app.agents.direct_modification.create_chat_model",
            side_effect=RuntimeError("offline"),
        ):
            error_decision = classify_direct_modification_intent(user_request="sdf")

        for decision in (empty_decision, error_decision):
            self.assertEqual(decision.owner, "unknown")
            self.assertEqual(decision.scope, "clarification")
            self.assertTrue(decision.clarification_question)


class DirectModificationNodeTests(unittest.TestCase):
    """验证分类、Agent 执行、测试和收口节点的快速模式语义。"""

    def test_code_implementation_fix_requires_user_confirmation(self) -> None:
        """前后端实现修复在写入代码前必须先展示确认门。"""

        decision = DirectModificationDecision(
            intent="implementation_fix",
            owner="frontend",
            scope="direct",
            confidence=0.98,
            reason="已确认语义不变，仅修复页面点击问题。",
            clarification_question="",
        )
        with patch(
            "app.graph.nodes.direct_modification.classify_direct_modification_intent",
            return_value=decision,
        ):
            update = classify_direct_modification(
                {
                    "request": "修复订单列表按钮点击无反应",
                    "workspace": "/tmp/workspace",
                }
            )

        self.assertEqual(update["status"], "requires_user_input")
        self.assertEqual(update["clarification"]["mode"], "implementation_fix_confirmation")
        self.assertEqual(_route_classification(update), "finalize")

    def test_workspace_implementation_fix_keeps_direct_path(self) -> None:
        """普通工作区文件实现修复不增加代码修改确认门。"""

        decision = DirectModificationDecision(
            intent="implementation_fix",
            owner="workspace",
            scope="direct",
            confidence=0.98,
            reason="只修改明确的文档文件。",
            clarification_question="",
            target_paths=("README.md",),
        )
        with patch(
            "app.graph.nodes.direct_modification.classify_direct_modification_intent",
            return_value=decision,
        ):
            update = classify_direct_modification(
                {
                    "request": "更新 README",
                    "workspace": "/tmp/workspace",
                }
            )

        self.assertEqual(update["status"], "in_progress")
        self.assertEqual(_route_classification(update), "execute_workspace")

    def test_classifier_receives_workspace_snapshot_created_by_scan(self) -> None:
        """分类节点必须读取前置扫描快照，而不是只依赖用户文本。"""

        captured: dict[str, object] = {}

        def classify_with_snapshot(**kwargs: object) -> DirectModificationDecision:
            """记录分类输入并返回明确的前端局部修改。"""

            captured.update(kwargs)
            return DirectModificationDecision(
                intent="implementation_fix",
                owner="frontend",
                scope="direct",
                confidence=0.98,
                reason="扫描结果中存在宠物照片列表页。",
                clarification_question="",
            )

        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "workspace_revision": "revision-1",
                        "frontend": {
                            "pages": [
                                {"path": "frontend/src/pages/PetPhotoList/index.tsx"}
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "app.graph.nodes.direct_modification.classify_direct_modification_intent",
                side_effect=classify_with_snapshot,
            ):
                update = classify_direct_modification(
                    {
                        "request": "把宠物照片列表页每个卡片宽度改成200px",
                        "workspace_snapshot_path": str(snapshot_path),
                        "direct_modification_handoff_decision": "approved",
                    }
                )

        snapshot = captured["workspace_snapshot"]
        self.assertIsInstance(snapshot, dict)
        self.assertIn("PetPhotoList", json.dumps(snapshot))
        self.assertEqual(update["direct_modification_owner"], "frontend")
        self.assertEqual(_route_classification(update), "execute_frontend")

    def test_classifier_accepts_fullstack_direct_request(self) -> None:
        """跨端局部需求应继续执行后端阶段，而不是转正式工作流。"""

        decision = DirectModificationDecision(
            intent="implementation_fix",
            owner="fullstack",
            scope="direct",
            confidence=0.95,
            reason="需要新增接口并展示结果。",
            clarification_question="",
        )
        with patch(
            "app.graph.nodes.direct_modification.classify_direct_modification_intent",
            return_value=decision,
        ):
            update = classify_direct_modification(
                {
                    "request": "新增统计接口并展示",
                    "direct_modification_handoff_decision": "approved",
                }
            )

        self.assertEqual(update["status"], "in_progress")
        self.assertEqual(update["direct_modification_owner"], "fullstack")
        self.assertEqual(update["launch_result"], {})
        self.assertIs(update["quality_gate_passed"], False)
        self.assertEqual(_route_classification(update), "execute_backend")
        self.assertEqual(_route_scan_workspace({"status": "completed"}), "classify_intent")

    def test_classifier_promotes_valid_config_target_to_run_approval(self) -> None:
        """分类器给出的现有安全配置路径应只在当前运行内加入追加授权。"""

        decision = DirectModificationDecision(
            intent="implementation_fix",
            owner="frontend",
            scope="direct",
            confidence=0.98,
            reason="用户明确要求修改现有 Vite 配置。",
            clarification_question="",
            target_paths=("frontend/vite.config.ts",),
        )
        with tempfile.TemporaryDirectory() as workspace:
            config = Path(workspace) / "frontend" / "vite.config.ts"
            config.parent.mkdir(parents=True)
            config.write_text("export default {}\n", encoding="utf-8")
            with patch(
                "app.graph.nodes.direct_modification.classify_direct_modification_intent",
                return_value=decision,
            ):
                update = classify_direct_modification(
                    {
                        "request": "请修改 Vite 构建配置",
                        "workspace": workspace,
                        "direct_modification_handoff_decision": "approved",
                    }
                )

        self.assertEqual(
            update["direct_modification_approved_paths"],
            ["frontend/vite.config.ts"],
        )

    def test_classifier_routes_identity_question_to_toolless_conversation(self) -> None:
        """身份类常规问题应由分类调用直接回答，不再触发第二次模型调用。"""

        decision = _normalize_direct_modification_decision(
            {
                "route": "casual_chat",
                "owner": "none",
                "confidence": 0.99,
                "reason": "用户在询问助手身份。",
                "clarificationQuestion": "",
                "response": "我是 XCodeAgent，可以协助开发和回答常规问题。",
                "targetPaths": [],
            }
        )
        with patch(
            "app.graph.nodes.direct_modification.classify_direct_modification_intent",
            return_value=decision,
        ):
            update = classify_direct_modification({"request": "你是谁"})

        self.assertEqual(update["conversation_intent"], "casual_chat")
        self.assertEqual(update["direct_modification_owner"], "none")
        self.assertEqual(update["status"], "completed")
        self.assertIn("XCodeAgent", update["conversation_response"])
        self.assertEqual(_route_classification(update), "finalize")

    def test_casual_conversation_bypasses_tests_and_launch(self) -> None:
        """常规对话回复应直接完成，不生成代码差异、测试或预览状态。"""

        with patch(
            "app.graph.nodes.direct_modification.answer_casual_conversation",
            return_value="我是 XCodeAgent，可以协助开发和回答常规问题。",
        ):
            answered = respond_to_casual_conversation(
                {"request": "你是谁", "direct_modification_summary": ""}
            )
        finalized = finalize_direct_modification(
            {
                **answered,
                "request": "你是谁",
                "conversation_intent": "casual_chat",
                "direct_stage_results": {},
                "direct_code_change_sets": [],
            }
        )

        self.assertEqual(finalized["status"], "completed")
        self.assertEqual(finalized["phase"], "conversation")
        self.assertIn("XCodeAgent", finalized["message"])
        self.assertEqual(finalized["code_changes"], {})

    def test_formal_revision_rejection_keeps_cancel_message_as_success(self) -> None:
        """正式修改取消应保留取消文案和成功终态，不伪装成修改完成或错误。"""

        finalized = finalize_direct_modification(
            {
                "request": "删除订单导出功能",
                "status": "completed",
                "message": "已取消本次正式修改，当前正式产物保持不变。",
                "conversation_intent": "formal_revision",
                "direct_modification_owner": "none",
                "direct_modification_scope": "formal",
                "direct_modification_handoff_decision": "rejected",
                "direct_stage_results": {},
                "direct_code_change_sets": [],
            }
        )

        self.assertEqual(finalized["status"], "completed")
        self.assertEqual(
            finalized["message"],
            "已取消本次正式修改，当前正式产物保持不变。",
        )
        self.assertNotIn("启动预览", finalized["message"])

    def test_implementation_rejection_overrides_stale_failure_status(self) -> None:
        """实现修改取消必须以成功终态收口，即使恢复快照仍残留失败状态。"""

        finalized = finalize_direct_modification(
            {
                "request": "修复登录按钮无响应",
                "status": "failed",
                "message": "用户已取消本次修改确认，本次工作区不会继续写入。",
                "conversation_intent": "implementation_fix",
                "direct_modification_owner": "frontend",
                "direct_modification_scope": "direct",
                "direct_modification_handoff_decision": "rejected",
                "direct_stage_results": {},
                "direct_code_change_sets": [],
            }
        )

        self.assertEqual(finalized["status"], "completed")
        self.assertEqual(
            finalized["message"],
            "用户已取消本次修改确认，本次工作区不会继续写入。",
        )
        self.assertEqual(finalized["direct_modification_result"]["status"], "completed")

    def test_workspace_question_uses_read_only_answer_node(self) -> None:
        """工程解释类问题应进入只读工作区节点并保留自然语言回复。"""

        with patch(
            "app.graph.nodes.direct_modification.answer_workspace_question",
            return_value="该项目的前端使用 React 和 Vite。",
        ) as answer:
            update = respond_to_workspace_question(
                {
                    "request": "这个项目的前端栈是什么？",
                    "workspace": "/workspace",
                    "selected_skill_names": [],
                }
            )

        self.assertEqual(update["status"], "completed")
        self.assertEqual(update["conversation_response"], "该项目的前端使用 React 和 Vite。")
        answer.assert_called_once()

    def test_workspace_change_uses_precise_non_product_path(self) -> None:
        """文档修改应限制在分类器给出的普通工作区路径并直接完成。"""

        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "README.md"
            target.write_text("old\n", encoding="utf-8")

            def fake_invoke(**_kwargs) -> str:
                """模拟 SmallTask 修改精确授权的文档路径。"""

                target.write_text("new\n", encoding="utf-8")
                return json.dumps(
                    {
                        "status": "completed",
                        "summary": "更新 README",
                        "changedFiles": ["README.md"],
                        "verification": ["文档修改无需构建"],
                        "alreadySatisfied": False,
                        "failureReason": None,
                    }
                )

            with patch(
                "app.graph.nodes.direct_modification.invoke_workspace_direct_modification",
                side_effect=fake_invoke,
            ):
                update = execute_workspace_direct_modification(
                    {
                        "request": "更新 README",
                        "workspace": workspace,
                        "direct_modification_target_paths": ["README.md"],
                        "direct_stage_results": {},
                        "direct_code_change_sets": [],
                    }
                )

        self.assertEqual(update["status"], "completed")
        self.assertEqual(update["direct_stage_results"]["workspace"]["changedFiles"], ["README.md"])

    def test_clarification_summary_is_finalized_once_and_reused_on_next_run(self) -> None:
        """等待轮只在收尾节点记录一次摘要，下一轮分类可读取旧请求和澄清问题。"""

        question = "请说明要修改哪个页面或接口，以及期望结果。"
        waiting_decision = DirectModificationDecision(
            intent="clarification",
            owner="unknown",
            scope="clarification",
            confidence=0.2,
            reason="需求信息不足。",
            clarification_question=question,
        )
        with patch(
            "app.graph.nodes.direct_modification.classify_direct_modification_intent",
            return_value=waiting_decision,
        ):
            classified = classify_direct_modification({"request": "sdf"})

        self.assertNotIn("direct_modification_summary", classified)
        # LangGraph 会按 ProjectState 过滤未声明的 message；投影必须从 clarification 回退读取。
        filtered_classified = {key: value for key, value in classified.items() if key != "message"}
        waiting_step = direct_node_process_step("classify_intent", filtered_classified)
        self.assertEqual(waiting_step["status"], "requires_user_input")
        self.assertEqual(waiting_step["detail"], question)
        progress_payload = direct_progress_payload(
            {"request": "sdf", **filtered_classified},
            events=[],
            process_step=waiting_step,
        )
        self.assertEqual(progress_payload["summary"]["request"], "sdf")
        self.assertEqual(progress_payload["state"]["request"], "sdf")

        finalized = finalize_direct_modification({**filtered_classified, "request": "sdf"})
        self.assertEqual(finalized["message"], question)
        summary = finalized["direct_modification_summary"]
        self.assertEqual(summary.count("用户：sdf"), 1)
        self.assertEqual(summary.count(question), 1)

        captured: dict[str, str] = {}

        def classify_follow_up(**kwargs: str) -> DirectModificationDecision:
            """记录新一轮分类上下文并返回可直接执行的前端修改。"""

            captured.update(kwargs)
            return DirectModificationDecision(
                intent="implementation_fix",
                owner="frontend",
                scope="direct",
                confidence=0.96,
                reason="补充内容已明确页面和修改结果。",
                clarification_question="",
            )

        with patch(
            "app.graph.nodes.direct_modification.classify_direct_modification_intent",
            side_effect=classify_follow_up,
        ):
            continued = classify_direct_modification(
                {
                    **classified,
                    **finalized,
                    "request": "把首页标题改成欢迎用户",
                    "direct_modification_handoff_decision": "approved",
                }
            )

        self.assertEqual(captured["user_request"], "把首页标题改成欢迎用户")
        self.assertEqual(captured["conversation_summary"], summary)
        self.assertEqual(captured["conversation_summary"].count("用户：sdf"), 1)
        self.assertEqual(continued["status"], "in_progress")
        self.assertEqual(continued["clarification"], {})
        self.assertEqual(_route_classification(continued), "execute_frontend")
        self.assertEqual(_route_scan_workspace({"status": "completed"}), "classify_intent")

    def test_confirmation_gate_completes_classification_step(self) -> None:
        """分类成功生成独立确认卡时，分类步骤自身必须显示为完成。"""

        step = direct_node_process_step(
            "classify_intent",
            {
                "status": "requires_user_input",
                "message": "该请求会修改已确认的正式语义，请确认是否进入正式修改流程。",
                "clarification": {
                    "mode": "revision_impact_confirmation",
                    "status": "requires_user_input",
                },
            },
        )

        self.assertEqual(step["status"], "completed")
        self.assertEqual(step["title"], "已完成 判断修改类型")

    def test_progress_payload_can_hide_internal_process_step(self) -> None:
        """内部收尾仍可更新状态和事件，但不得强制生成用户可见步骤。"""

        payload = direct_progress_payload(
            {"status": "requires_user_input", "phase": "conversation"},
            events=[],
            process_step=None,
        )

        self.assertNotIn("processStep", payload)

    def test_frontend_execution_uses_real_workspace_diff(self) -> None:
        """前端阶段以工作区快照为权威变更清单。"""

        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "frontend" / "src" / "Page.tsx"

            def fake_invoke(**_kwargs) -> str:
                """模拟 Agent 写入前端文件并返回结构化结果。"""

                target.parent.mkdir(parents=True)
                target.write_text("export default null\n", encoding="utf-8")
                return json.dumps(
                    {
                        "status": "completed",
                        "summary": "完成页面修改",
                        "changedFiles": ["model-invented.tsx"],
                        "verification": ["pnpm build"],
                        "alreadySatisfied": False,
                        "failureReason": None,
                    }
                )

            with patch(
                "app.graph.nodes.direct_modification.invoke_frontend_direct_modification",
                side_effect=fake_invoke,
            ):
                update = execute_frontend_direct_modification(
                    {
                        "request": "修改页面",
                        "workspace": workspace,
                        "direct_stage_results": {},
                        "direct_code_change_sets": [],
                    }
                )

        self.assertEqual(update["status"], "in_progress")
        self.assertEqual(
            update["direct_stage_results"]["frontend"]["changedFiles"],
            ["frontend/src/Page.tsx"],
        )

    def test_backend_execution_builds_handoff_from_actual_diff(self) -> None:
        """后端阶段应把真实改动文件补充到前端交接信息。"""

        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "backend" / "src" / "Api.java"

            def fake_invoke(**_kwargs) -> str:
                """模拟 Agent 写入后端文件并返回接口交接。"""

                target.parent.mkdir(parents=True)
                target.write_text("class Api {}\n", encoding="utf-8")
                return json.dumps(
                    {
                        "status": "completed",
                        "summary": "新增接口",
                        "changedFiles": [],
                        "verification": ["mvn test"],
                        "alreadySatisfied": False,
                        "failureReason": None,
                        "backendHandoff": {
                            "summary": "新增统计接口",
                            "endpoints": [
                                {
                                    "method": "GET",
                                    "path": "/api/statistics",
                                    "request": None,
                                    "response": {"total": "number"},
                                }
                            ],
                            "notes": [],
                        },
                    }
                )

            with patch(
                "app.graph.nodes.direct_modification.invoke_data_source_direct_modification",
                side_effect=fake_invoke,
            ):
                update = execute_backend_direct_modification(
                    {
                        "request": "新增统计接口",
                        "workspace": workspace,
                        "direct_stage_results": {},
                        "direct_code_change_sets": [],
                    }
                )

        self.assertEqual(update["status"], "in_progress")
        self.assertEqual(update["backend_handoff"]["changedFiles"], ["backend/src/Api.java"])

    def test_frontend_failure_preserves_changes_created_before_exception(self) -> None:
        """Agent 运行异常时仍应返回可审核、可撤销的真实差异。"""

        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "frontend" / "src" / "Page.tsx"

            def failing_invoke(**_kwargs) -> str:
                """模拟 Agent 修改文件后在验证阶段异常退出。"""

                target.parent.mkdir(parents=True)
                target.write_text("export default null\n", encoding="utf-8")
                raise TimeoutError("model verification timed out")

            with patch(
                "app.graph.nodes.direct_modification.invoke_frontend_direct_modification",
                side_effect=failing_invoke,
            ):
                update = execute_frontend_direct_modification(
                    {
                        "request": "修改页面颜色",
                        "workspace": workspace,
                        "direct_stage_results": {},
                        "direct_code_change_sets": [],
                    }
                )

        result = update["direct_stage_results"]["frontend"]
        self.assertEqual(update["status"], "in_progress")
        self.assertEqual(result["changedFiles"], ["frontend/src/Page.tsx"])
        self.assertIn("TimeoutError", result["failureReason"])
        self.assertIs(result["partialChanges"], True)
        self.assertEqual(update["code_changes"]["summary"]["files"], 1)

    def test_final_acceptance_turns_recovered_stage_into_success(self) -> None:
        """最终验收通过后，工具异常只保留为告警而不能覆盖任务成功。"""

        finalized = finalize_direct_modification(
            {
                "request": "修改页面颜色",
                "status": "completed",
                "conversation_intent": "implementation_fix",
                "direct_modification_owner": "frontend",
                "direct_modification_scope": "direct",
                "direct_stage_results": {
                    "frontend": {
                        "status": "failed",
                        "summary": "某次 read 工具调用失败，但文件已经写入。",
                        "failureReason": "ReadError: path unavailable",
                        "partialChanges": True,
                    }
                },
                "direct_code_change_sets": [],
                "quality_gate_passed": True,
                "launch_result": {"status": "running"},
            }
        )

        self.assertEqual(finalized["status"], "completed")
        stage_result = finalized["direct_modification_result"]["stageResults"]["frontend"]
        self.assertEqual(stage_result["status"], "completed")
        self.assertIs(stage_result["recoveredFromToolFailure"], True)
        self.assertIn("最终验收", stage_result["summary"])
        self.assertIn("最终验收", finalized["message"])

    def test_direct_validation_only_checks_real_changed_layers(self) -> None:
        """快速验证必须只执行真实差异所属层，并跳过重复依赖安装。"""

        captured: dict = {}

        def fake_checks(state, **kwargs):
            """记录范围验证参数并返回前端通过证据。"""

            captured["state"] = state
            captured.update(kwargs)
            return {
                "test_results": [
                    {
                        "id": "frontend_build",
                        "name": "前端构建检查",
                        "passed": True,
                        "required": True,
                        "evidence": "ok",
                    }
                ],
                "test_events": ["frontend_build"],
            }

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.direct_modification.run_integration_checks",
                side_effect=fake_checks,
            ):
                update = validate_direct_fix(
                    {
                        "workspace": workspace,
                        "direct_code_change_sets": [
                            {"files": [{"path": "Frontend/src/Page.tsx"}]}
                        ],
                    }
                )

        self.assertEqual(captured["affected_layers"], {"frontend"})
        self.assertIs(captured["install_frontend_dependencies"], False)
        self.assertEqual(captured["state"]["unit_test_affected_layers"], ["frontend"])
        self.assertEqual(update["status"], "completed")
        self.assertEqual(update["integration_next_action"], "finalize_direct_modification")
        self.assertEqual(_route_direct_validation(update), "finalize")

    def test_failed_free_conversation_test_enters_bounded_repair_node(self) -> None:
        """自由对话测试失败且有精确证据时应进入独立自动修复节点。"""

        state = {
            "status": "failed",
            "quality_gate_passed": False,
            "integration_next_action": "direct_modification_repair",
            "repair_iteration": 0,
            "max_repair_iterations": 3,
        }

        self.assertEqual(_route_direct_validation(state), "direct_modification_repair")
        self.assertEqual(
            direct_next_node_name("validate_direct_fix", state),
            "direct_modification_repair",
        )

    def test_direct_validation_ignores_unattributed_same_layer_failure(self) -> None:
        """同层检查失败未指向本次文件时只记录告警，不得触发自动修复。"""

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.direct_modification.run_integration_checks",
                return_value={
                    "test_results": [
                        {
                            "id": "frontend_build",
                            "name": "前端构建检查",
                            "layer": "frontend",
                            "passed": False,
                            "required": True,
                            "evidence": "Frontend/src/LegacyPanel.tsx 存在历史类型错误。",
                        }
                    ],
                    "test_events": ["frontend_build"],
                },
            ):
                update = validate_direct_fix(
                    {
                        "workspace": workspace,
                        "direct_code_change_sets": [
                            {"files": [{"path": "Frontend/src/Page.tsx"}]}
                        ],
                    }
                )

        self.assertEqual(update["status"], "completed")
        self.assertTrue(update["quality_gate_passed"])
        self.assertEqual(update["revision_requests"], [])
        self.assertTrue(update["test_results"][0]["advisory"])

    def test_direct_validation_repairs_failure_attributed_to_changed_file(self) -> None:
        """检查证据命中真实变更文件时仍应进入有界自动修复。"""

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.direct_modification.run_integration_checks",
                return_value={
                    "test_results": [
                        {
                            "id": "frontend_build",
                            "name": "前端构建检查",
                            "layer": "frontend",
                            "passed": False,
                            "required": True,
                            "evidence": "Frontend/src/Page.tsx:12 类型不匹配。",
                        }
                    ],
                    "test_events": ["frontend_build"],
                },
            ):
                update = validate_direct_fix(
                    {
                        "workspace": workspace,
                        "direct_code_change_sets": [
                            {"files": [{"path": "Frontend/src/Page.tsx"}]}
                        ],
                        "repair_iteration": 0,
                        "max_repair_iterations": 3,
                    }
                )

        self.assertEqual(update["status"], "failed")
        self.assertFalse(update["quality_gate_passed"])
        self.assertEqual(update["integration_next_action"], "direct_modification_repair")
        self.assertEqual(len(update["revision_requests"]), 1)

    def test_direct_repair_executes_bounded_task_and_returns_to_test(self) -> None:
        """自由对话修复应只使用实际变更文件，并在成功后回到集成测试。"""

        with tempfile.TemporaryDirectory() as workspace:
            task_id = "repair:frontend_build"
            plan = {
                "version": "0.1.0",
                "status": "ready",
                "decision": "repair",
                "tasks": [
                    {
                        "id": task_id,
                        "owner": "frontend",
                        "status": "pending",
                        "allowed_paths": ["Frontend/src/App.tsx"],
                        "target_files": ["Frontend/src/App.tsx"],
                        "change_scope": [
                            {"operation": "modify", "path": "Frontend/src/App.tsx"}
                        ],
                    }
                ],
            }
            execution = {
                "results": [
                    {
                        "taskId": task_id,
                        "status": "completed",
                        "summary": "修复完成",
                        "changedFiles": ["Frontend/src/App.tsx"],
                        "verification": ["pnpm build"],
                        "alreadySatisfied": False,
                        "failureReason": "",
                        "escalation": {},
                    }
                ],
                "codeChangeSets": [
                    {
                        "files": [
                            {
                                "path": "Frontend/src/App.tsx",
                                "changeType": "modified",
                            }
                        ]
                    }
                ],
                "unauthorizedPaths": [],
            }
            with (
                patch(
                    "app.graph.nodes.direct_repair.plan_repairs_with_repair_planner_agent",
                    return_value=plan,
                ) as planner,
                patch(
                    "app.graph.nodes.direct_repair.execute_small_task_batch",
                    return_value=execution,
                ) as executor,
            ):
                update = direct_modification_repair(
                    {
                        "workspace": workspace,
                        "selected_skill_names": [],
                        "test_report": {"passed": False},
                        "revision_requests": [
                            {
                                "id": "revision:frontend_build",
                                "owner": "frontend",
                                "owners": ["frontend"],
                                "reason": "前端构建失败",
                                "failed_check": {
                                    "id": "frontend_build",
                                    "name": "前端构建检查",
                                    "passed": False,
                                    "evidence": "TS error",
                                },
                            }
                        ],
                        "direct_stage_results": {
                            "frontend": {"changedFiles": ["Frontend/src/App.tsx"]}
                        },
                        "direct_code_change_sets": [],
                        "small_task_results": [],
                        "small_task_code_change_sets": [],
                        "repair_iteration": 0,
                        "max_repair_iterations": 3,
                    }
                )

        planner.assert_called_once()
        executor.assert_called_once()
        self.assertEqual(update["status"], "in_progress")
        self.assertEqual(update["repair_iteration"], 1)
        self.assertEqual(update["integration_next_action"], "validate_direct_fix")
        self.assertEqual(update["direct_code_change_sets"], execution["codeChangeSets"])
        self.assertEqual(_route_direct_repair(update), "validate_direct_fix")

    def test_direct_repair_stops_before_planner_when_budget_is_exhausted(self) -> None:
        """达到三轮修复上限时不能再次调用 Planner 或 SmallTask。"""

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.direct_repair.plan_repairs_with_repair_planner_agent"
            ) as planner:
                update = direct_modification_repair(
                    {
                        "workspace": workspace,
                        "revision_requests": [{"id": "revision:frontend_build"}],
                        "repair_iteration": 3,
                        "max_repair_iterations": 3,
                        "direct_code_change_sets": [],
                        "small_task_results": [],
                        "small_task_code_change_sets": [],
                    }
                )

        planner.assert_not_called()
        self.assertEqual(update["status"], "failed")
        self.assertEqual(update["integration_next_action"], "handle_failure")
        self.assertIn("3 轮上限", update["message"])

    def test_direct_repair_missing_file_scope_fails_without_formal_revision(self) -> None:
        """修复任务缺少真实文件时必须停止，不能伪造 TechnicalPlan 正式升级。"""

        plan = {
            "version": "0.1.0",
            "status": "ready",
            "decision": "repair",
            "tasks": [
                {
                    "id": "repair:frontend_build",
                    "owner": "frontend",
                    "status": "pending",
                    "allowed_paths": [
                        "<no file paths — repair is a command-level operation>"
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.graph.nodes.direct_repair.plan_repairs_with_repair_planner_agent",
                    return_value=plan,
                ),
                patch(
                    "app.graph.nodes.direct_repair.execute_small_task_batch"
                ) as executor,
            ):
                update = direct_modification_repair(
                    {
                        "workspace": workspace,
                        "revision_requests": [
                            {
                                "id": "revision:frontend_build",
                                "owner": "frontend",
                                "owners": ["frontend"],
                                "failed_check": {
                                    "id": "frontend_build",
                                    "name": "前端构建检查",
                                    "passed": False,
                                    "evidence": "TS error",
                                },
                            }
                        ],
                        "direct_stage_results": {
                            "frontend": {"changedFiles": ["Frontend/src/App.tsx"]}
                        },
                        "direct_code_change_sets": [],
                        "small_task_results": [],
                        "small_task_code_change_sets": [],
                        "repair_iteration": 0,
                        "max_repair_iterations": 3,
                    }
                )

        executor.assert_not_called()
        self.assertEqual(update["status"], "failed")
        self.assertNotIn("revision_impact", update)
        self.assertEqual(update["clarification"], {})
        self.assertIn("真实代码文件范围", update["message"])

    def test_direct_repair_explicit_formal_escalation_keeps_confirmation(self) -> None:
        """RepairPlanner 明确证明正式语义变化时仍应保留正式修改确认门。"""

        plan = {
            "version": "0.1.0",
            "status": "requires_user_confirmation",
            "decision": "requires_user_confirmation",
            "escalationKind": "formal_revision",
            "reason": "修复需要改变已确认的 API 契约。",
            "requestedPaths": [],
            "tasks": [],
            "candidateTasks": [],
        }
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.direct_repair.plan_repairs_with_repair_planner_agent",
                return_value=plan,
            ):
                update = direct_modification_repair(
                    {
                        "workspace": workspace,
                        "request": "修复接口调用",
                        "revision_requests": [
                            {
                                "id": "revision:backend_build",
                                "owner": "backend",
                                "owners": ["backend"],
                                "failed_check": {
                                    "id": "backend_build",
                                    "name": "后端构建检查",
                                    "passed": False,
                                    "evidence": "contract mismatch",
                                },
                            }
                        ],
                        "direct_stage_results": {
                            "backend": {"changedFiles": ["Backend/app/api.py"]}
                        },
                        "direct_code_change_sets": [],
                        "small_task_results": [],
                        "small_task_code_change_sets": [],
                        "repair_iteration": 0,
                        "max_repair_iterations": 3,
                    }
                )

        self.assertEqual(update["status"], "requires_user_input")
        self.assertEqual(
            update["clarification"]["mode"],
            "revision_impact_confirmation",
        )
        self.assertEqual(update["revision_impact"]["reason"], plan["reason"])

    def test_validation_success_is_finalized_without_preview_or_acceptance(self) -> None:
        """范围验证通过后快速通道直接完成，不生成预览或正式验收状态。"""

        update = finalize_direct_modification(
            {
                "request": "修改页面",
                "status": "completed",
                "direct_modification_owner": "frontend",
                "direct_modification_scope": "direct",
                "direct_stage_results": {
                    "frontend": {"status": "completed", "summary": "完成页面修改"}
                },
                "direct_code_change_sets": [],
                "test_report_path": "/private/workspace/.xcodeagent/reports/test-report.md",
                "quality_gate_passed": True,
            }
        )

        self.assertEqual(update["status"], "completed")
        self.assertEqual(update["acceptance_request"], {})
        self.assertEqual(update["clarification"], {})
        self.assertEqual(update["direct_modification_result"]["launchResult"], {})
        self.assertIsNone(update["direct_modification_result"]["previewUrl"])
        self.assertEqual(
            update["direct_modification_result"]["tests"]["reportPath"],
            ".xcodeagent/reports/test-report.md",
        )

    def test_fullstack_routes_backend_then_frontend(self) -> None:
        """fullstack 成功路径必须固定后端优先，再执行前端和测试。"""

        state = {"status": "in_progress", "direct_modification_owner": "fullstack"}
        self.assertEqual(_route_backend(state), "execute_frontend")
        self.assertEqual(_route_frontend(state), "validate_direct_fix")

    def test_progress_projection_uses_graph_route_for_next_running_step(self) -> None:
        """节点完成后必须沿真实路由立即投射下一节点的运行中状态。"""

        state = {"status": "completed"}
        next_node = direct_next_node_name("scan_workspace_code", state)
        running_step = direct_node_running_process_step(str(next_node))
        completed_step = direct_node_process_step(
            "scan_workspace_code",
            {"status": "in_progress", "message": "代码扫描完成。"},
        )

        self.assertEqual(next_node, "classify_intent")
        self.assertNotEqual(running_step["id"], completed_step["id"])
        self.assertEqual(running_step["status"], "running")
        self.assertEqual(running_step["title"], "正在执行 判断修改类型")
        self.assertEqual(completed_step["status"], "completed")

    def test_preserves_route_requires_code_scan_before_owner_execution(self) -> None:
        """契约保持时真实 Graph 顺序必须先扫描代码，再进入 owner 节点。"""

        pending = {
            "status": "in_progress",
            "conversation_intent": "implementation_fix",
            "direct_modification_owner": "frontend",
            "change_impact_enabled": True,
            "change_impact_code_scan_required": True,
        }
        self.assertEqual(_route_classification(pending), "scan_change_impact_code")
        self.assertEqual(
            direct_next_node_name("classify_intent", pending),
            "scan_change_impact_code",
        )
        no_finding = {
            **pending,
            "change_impact_code_scan_required": False,
            "change_impact_code_scan": {"performed": True, "findings": []},
        }
        self.assertEqual(
            direct_next_node_name("scan_change_impact_code", no_finding),
            "finalize_direct_modification",
        )
        with_finding = {
            **no_finding,
            "change_impact_code_scan": {
                "performed": True,
                "findings": [{"path": "Frontend/src/App.tsx"}],
            },
        }
        self.assertEqual(
            direct_next_node_name("scan_change_impact_code", with_finding),
            "execute_frontend",
        )

    def test_fake_graph_executes_navigation_scan_then_contract_code_scan_then_owner(self) -> None:
        """真实编译后的 Graph 必须把导航扫描与目标 code.scan 严格分成两步。"""

        calls: list[str] = []

        def fake_workspace_scan(_state: dict[str, Any]) -> dict[str, Any]:
            """模拟工作区导航摘要，不返回目标代码证据。"""

            calls.append("scan_workspace_code")
            return {
                "phase": "scan_workspace_code",
                "status": "completed",
                "workspace_snapshot_summary": {"code_graph": {"provider": "fake"}},
                "workspace_snapshot_path": "",
                "timeline": ["scan_workspace_code"],
            }

        def fake_classify(_state: dict[str, Any]) -> dict[str, Any]:
            """模拟契约 preserves 后等待目标代码扫描。"""

            calls.append("classify_intent")
            return {
                "status": "in_progress",
                "conversation_intent": "implementation_fix",
                "direct_modification_owner": "frontend",
                "change_impact_enabled": True,
                "change_impact_code_scan_required": True,
                "direct_modification_target_paths": [],
                "direct_modification_approved_paths": [],
                "direct_code_change_sets": [],
                "direct_stage_results": {},
            }

        def fake_code_scan(_state: dict[str, Any]) -> dict[str, Any]:
            """返回一条可交给前端 owner 的目标源码 finding。"""

            calls.append("scan_change_impact_code")
            return {
                "status": "in_progress",
                "change_impact_code_scan_required": False,
                "change_impact_code_scan": {
                    "performed": True,
                    "findings": [{"path": "Frontend/src/Login.tsx"}],
                },
            }

        def fake_owner(_state: dict[str, Any]) -> dict[str, Any]:
            """模拟 owner 执行节点，并在这里结束本次假运行。"""

            calls.append("execute_frontend")
            return {"status": "requires_user_input", "phase": "execute_frontend"}

        def fake_finalize(_state: dict[str, Any]) -> dict[str, Any]:
            """记录终态节点，避免触发真实模型或文件写入。"""

            calls.append("finalize_direct_modification")
            return {"status": "requires_user_input", "phase": "conversation"}

        with patch(
            "app.graph.direct_modification_workflow.scan_workspace_code",
            side_effect=fake_workspace_scan,
        ), patch(
            "app.graph.direct_modification_workflow.classify_direct_modification",
            side_effect=fake_classify,
        ), patch(
            "app.graph.direct_modification_workflow.scan_change_impact_code",
            side_effect=fake_code_scan,
        ), patch(
            "app.graph.direct_modification_workflow.execute_frontend_direct_modification",
            side_effect=fake_owner,
        ), patch(
            "app.graph.direct_modification_workflow.finalize_direct_modification",
            side_effect=fake_finalize,
        ):
            graph = build_direct_modification_graph(checkpointer=None)
            graph.invoke(
                {
                    "request": "修复登录按钮",
                    "workspace": "/tmp/fake-user-workspace",
                    "status": "in_progress",
                }
            )

        self.assertEqual(
            calls,
            [
                "scan_workspace_code",
                "classify_intent",
                "scan_change_impact_code",
                "execute_frontend",
                "finalize_direct_modification",
            ],
        )

    def test_compiled_graph_continuation_starts_at_code_scan(self) -> None:
        """确认续跑的编译 Graph 必须跳过导航扫描和意图分类。"""

        calls: list[str] = []

        def fake_code_scan(_state: dict[str, Any]) -> dict[str, Any]:
            """模拟确认后的只读源码证据扫描。"""

            calls.append("scan_change_impact_code")
            return {
                "status": "in_progress",
                "change_impact_code_scan": {
                    "performed": True,
                    "findings": [{"path": "Frontend/src/Login.tsx"}],
                },
            }

        def fake_owner(_state: dict[str, Any]) -> dict[str, Any]:
            """模拟确认后的前端写入节点。"""

            calls.append("execute_frontend")
            return {"status": "in_progress", "phase": "execute_frontend"}

        def fake_finalize(_state: dict[str, Any]) -> dict[str, Any]:
            """模拟确认续跑的终态节点。"""

            calls.append("finalize_direct_modification")
            return {"status": "completed", "phase": "conversation"}

        with patch(
            "app.graph.direct_modification_workflow.scan_workspace_code",
            side_effect=AssertionError("确认续跑不应扫描工作区导航"),
        ), patch(
            "app.graph.direct_modification_workflow.classify_direct_modification",
            side_effect=AssertionError("确认续跑不应重新分类"),
        ), patch(
            "app.graph.direct_modification_workflow.scan_change_impact_code",
            side_effect=fake_code_scan,
        ), patch(
            "app.graph.direct_modification_workflow.execute_frontend_direct_modification",
            side_effect=fake_owner,
        ), patch(
            "app.graph.direct_modification_workflow.finalize_direct_modification",
            side_effect=fake_finalize,
        ):
            graph = build_direct_modification_graph(checkpointer=None)
            graph.invoke(
                {
                    "request": "修复登录按钮",
                    "workspace": "/tmp/fake-user-workspace",
                    "status": "in_progress",
                    "conversation_intent": "implementation_fix",
                    "direct_modification_owner": "frontend",
                    "change_impact_enabled": True,
                    "direct_modification_resume_node": "scan_change_impact_code",
                }
            )

        self.assertEqual(
            calls,
            [
                "scan_change_impact_code",
                "execute_frontend",
                "finalize_direct_modification",
            ],
        )

    def test_started_event_exposes_running_copy(self) -> None:
        """快速模式节点开始事件必须包含可见的正在执行文案。"""

        event = direct_node_started_event(
            "execute_frontend",
            run_id="direct-run",
            thread_id="direct-thread",
        )

        self.assertEqual(event["type"], "conversation.node.started")
        self.assertEqual(event["status"], "running")
        self.assertEqual(event["message"], "正在执行：执行前端修改")


class DirectModificationProtocolTests(unittest.TestCase):
    """验证快速修改公开 AG-UI 契约。"""

    def test_approved_implementation_confirmation_uses_server_checkpoint(self) -> None:
        """实现确认必须复用服务端分类结果并从代码扫描节点继续。"""

        request = DirectModificationInput.model_validate(
            {
                "workspaceRoot": "/workspace",
                "originalRequest": "修复登录按钮无响应",
                "handoffDecision": "approved",
            }
        )
        continuation = _direct_confirmation_continuation(
            request=request,
            thread_id="conversation-thread",
            checkpoint_values={
                "request": "修复登录按钮无响应",
                "active_thread_id": "conversation-thread",
                "status": "requires_user_input",
                "conversation_intent": "implementation_fix",
                "direct_modification_owner": "frontend",
                "direct_modification_scope": "direct",
                "direct_modification_target_paths": ["Frontend/src/Login.tsx"],
                "direct_modification_approved_paths": [],
                "change_impact_code_scan_required": True,
                "clarification": {
                    "mode": "implementation_fix_confirmation",
                    "requestedPaths": [],
                },
            },
        )

        self.assertIsNotNone(continuation)
        self.assertEqual(continuation["direct_modification_resume_node"], "scan_change_impact_code")
        self.assertEqual(continuation["conversation_intent"], "implementation_fix")
        self.assertEqual(continuation["direct_modification_owner"], "frontend")
        self.assertEqual(continuation["request"], "修复登录按钮无响应")
        self.assertEqual(continuation["status"], "in_progress")

    def test_rejected_implementation_confirmation_is_completed_terminal_state(self) -> None:
        """取消实现修改确认必须直接成功收口，不得成为失败 Workflow。"""

        request = DirectModificationInput.model_validate(
            {
                "workspaceRoot": "/workspace",
                "originalRequest": "修复登录按钮无响应",
                "handoffDecision": "rejected",
            }
        )
        continuation = _direct_confirmation_continuation(
            request=request,
            thread_id="conversation-thread",
            checkpoint_values={
                "request": "修复登录按钮无响应",
                "active_thread_id": "conversation-thread",
                "status": "requires_user_input",
                "conversation_intent": "implementation_fix",
                "direct_modification_owner": "frontend",
                "direct_modification_scope": "direct",
                "direct_modification_target_paths": ["Frontend/src/Login.tsx"],
                "direct_modification_approved_paths": [],
                "change_impact_code_scan_required": True,
                "clarification": {
                    "mode": "implementation_fix_confirmation",
                    "requestedPaths": [],
                },
            },
        )

        self.assertIsNotNone(continuation)
        self.assertEqual(continuation["status"], "completed")
        self.assertEqual(
            continuation["direct_modification_resume_node"],
            "finalize_direct_modification",
        )
        self.assertEqual(
            continuation["message"],
            "用户已取消本次修改确认，本次工作区不会继续写入。",
        )

    def test_confirmation_without_matching_checkpoint_fails_closed(self) -> None:
        """没有匹配的服务端确认 checkpoint 时禁止把批准动作当新请求重分类。"""

        request = DirectModificationInput.model_validate(
            {
                "workspaceRoot": "/workspace",
                "originalRequest": "修复登录按钮无响应",
                "handoffDecision": "approved",
            }
        )
        with self.assertRaisesRegex(ValueError, "确认续跑找不到匹配"):
            _direct_confirmation_continuation(
                request=request,
                thread_id="conversation-thread",
                checkpoint_values={"status": "completed"},
            )

    def test_stream_approved_confirmation_emits_code_scan_as_first_node(self) -> None:
        """确认续跑的 AG-UI 事件应从代码证据扫描开始且不包含意图分类。"""

        captured_states: list[dict[str, Any]] = []
        checkpoint_values = {
            "request": "修复登录按钮无响应",
            "active_thread_id": "confirmation-thread",
            "status": "requires_user_input",
            "conversation_intent": "implementation_fix",
            "direct_modification_owner": "frontend",
            "direct_modification_scope": "direct",
            "direct_modification_target_paths": ["Frontend/src/Login.tsx"],
            "direct_modification_approved_paths": [],
            "change_impact_enabled": True,
            "change_impact_code_scan_required": True,
            "change_impact_analysis": {"analysisStatus": "completed"},
            "clarification": {
                "mode": "implementation_fix_confirmation",
                "requestedPaths": [],
            },
        }
        final_state = {
            "phase": "conversation",
            "status": "completed",
            "message": "前端修改完成",
            "conversation_intent": "implementation_fix",
            "direct_modification_owner": "frontend",
            "direct_modification_scope": "direct",
            "direct_modification_result": {
                "status": "completed",
                "summary": "前端修改完成",
            },
        }

        class FakeGraph:
            """提供确认续跑测试所需的最小 Graph 接口。"""

            def __init__(self) -> None:
                self.state_calls = 0

            async def aget_state(self, _config):
                """首次返回待确认 checkpoint，末次返回运行终态。"""

                self.state_calls += 1
                return SimpleNamespace(
                    values=checkpoint_values if self.state_calls == 1 else final_state
                )

            async def astream(self, initial_state, *_args, **_kwargs):
                """发送确认后代码扫描、前端执行和收口事件。"""

                captured_states.append(initial_state)
                yield "updates", {
                    "scan_change_impact_code": {
                        "phase": "change_impact_code_scan",
                        "status": "in_progress",
                        "message": "已取得目标代码证据。",
                        "change_impact_code_scan": {
                            "performed": True,
                            "findings": [{"path": "Frontend/src/Login.tsx"}],
                        },
                    }
                }
                yield "updates", {
                    "execute_frontend": {
                        "phase": "execute_frontend",
                        "status": "in_progress",
                        "message": "前端修改完成。",
                    }
                }
                yield "updates", {"finalize_direct_modification": final_state}

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.protocols.direct_modification.direct_modification_graph_for_request",
                    new=AsyncMock(return_value=FakeGraph()),
                ),
                patch(
                    "app.protocols.direct_modification.cleanup_workflow_checkpoints",
                    new=AsyncMock(return_value=0),
                ),
            ):
                stream = build_conversation_ag_ui_stream(
                    payload={
                        "threadId": "confirmation-thread",
                        "runId": "confirmation-run",
                        "messages": [
                            {
                                "role": "user",
                                "content": "用户已确认实现修改范围，请继续执行。",
                            }
                        ],
                        "forwardedProps": {
                            "conversation": {
                                "workspaceRoot": workspace,
                                "selectedSkillNames": [],
                                "originalRequest": "修复登录按钮无响应",
                                "handoffDecision": "approved",
                            }
                        },
                    }
                )

                async def collect() -> str:
                    """消费确认续跑事件流。"""

                    return "".join([frame async for frame in stream])

                frames = asyncio.run(collect())

        self.assertEqual(len(captured_states), 1)
        self.assertEqual(captured_states[0]["request"], "修复登录按钮无响应")
        self.assertEqual(
            captured_states[0]["direct_modification_resume_node"],
            "scan_change_impact_code",
        )
        self.assertNotIn("判断修改类型", frames)
        self.assertIn("取得目标代码证据", frames)

    def test_stream_rejected_revision_impact_finishes_without_scan_or_error(self) -> None:
        """取消正式修改影响范围后应直接成功收口，不扫描、不分类、不报错。"""

        captured_states: list[dict[str, Any]] = []
        final_state = {
            "phase": "conversation",
            "status": "completed",
            "message": "已取消本次正式修改，当前正式产物保持不变。",
            "conversation_intent": "formal_revision",
            "direct_modification_owner": "none",
            "direct_modification_scope": "formal",
            "direct_modification_handoff_decision": "rejected",
            "direct_modification_result": {
                "status": "completed",
                "summary": "已取消本次正式修改，当前正式产物保持不变。",
            },
        }

        class FakeGraph:
            """提供取消收口测试所需的最小 Graph 接口。"""

            async def aget_state(self, _config):
                """收口完成后返回已取消的成功终态。"""

                return SimpleNamespace(values=final_state)

            async def astream(self, initial_state, *_args, **_kwargs):
                """断言取消只进入收口节点。"""

                captured_states.append(initial_state)
                yield "updates", {"finalize_direct_modification": final_state}
        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.protocols.direct_modification.direct_modification_graph_for_request",
                    new=AsyncMock(return_value=FakeGraph()),
                ),
                patch(
                    "app.protocols.direct_modification.cleanup_workflow_checkpoints",
                    new=AsyncMock(return_value=0),
                ),
                patch(
                    "app.protocols.direct_modification.submit_revision_impact"
                ) as submit_impact,
            ):
                stream = build_conversation_ag_ui_stream(
                    payload={
                        "threadId": "impact-thread",
                        "runId": "impact-cancel-run",
                        "messages": [
                            {
                                "role": "user",
                                "content": "用户已取消本次正式修改。",
                            }
                        ],
                        "forwardedProps": {
                            "conversation": {
                                "workspaceRoot": workspace,
                                "selectedSkillNames": [],
                                "originalRequest": "删除订单导出功能",
                                "handoffDecision": "rejected",
                                "impactInteractionId": "impact-cancel-1",
                            }
                        },
                    }
                )

                async def collect() -> str:
                    """消费取消请求的完整 AG-UI 事件流。"""

                    return "".join([frame async for frame in stream])

                frames = asyncio.run(collect())

        self.assertEqual(len(captured_states), 1)
        self.assertEqual(
            captured_states[0]["direct_modification_resume_node"],
            "finalize_direct_modification",
        )
        submit_impact.assert_called_once_with(
            str(Path(workspace).resolve()),
            interaction_id="impact-cancel-1",
            decision="rejected",
        )
        self.assertIn("已取消本次正式修改", frames)
        self.assertNotIn("扫描工作区代码", frames)
        self.assertNotIn("判断修改类型", frames)
        self.assertNotIn("RUN_ERROR", frames)
        self.assertNotIn("执行失败", frames)

    def test_direct_graph_entry_skips_classification_for_continuation(self) -> None:
        """确认续跑的 Graph 首节点不应回到工作区扫描或意图分类。"""

        self.assertEqual(
            _route_direct_entry(
                {"direct_modification_resume_node": "scan_change_impact_code"}
            ),
            "scan_change_impact_code",
        )
        self.assertEqual(
            _route_direct_entry({}),
            "scan_workspace_code",
        )

    def test_clarification_resume_keeps_latest_user_answer(self) -> None:
        """恢复澄清不能让上一轮 originalRequest 覆盖本轮回答。"""

        request = _conversation_request(
            original_request="请修改页面并创建一个新的配置文件。",
            latest_user_request="创建到 Backend/app/config/new_feature.py。",
        )

        self.assertIn("请修改页面并创建一个新的配置文件。", request)
        self.assertIn("创建到 Backend/app/config/new_feature.py。", request)
        self.assertIn("本轮用户补充：", request)

    def test_stream_passes_latest_answer_into_graph_state(self) -> None:
        """实际 AG-UI 恢复流应把最新回答写入 Graph 输入，而不是只传旧问题。"""

        captured_states: list[dict[str, Any]] = []
        final_state = {
            "phase": "conversation",
            "status": "completed",
            "message": "已收到补充信息。",
            "conversation_intent": "casual_chat",
            "direct_modification_owner": "none",
            "direct_modification_scope": "respond",
            "direct_modification_result": {"status": "completed", "summary": "已收到补充信息。"},
        }

        class FakeGraph:
            """捕获协议层传入的首个 Graph 状态。"""

            async def astream(self, initial_state, *_args, **_kwargs):
                """发送最小终态，验证初始请求内容已经合并。"""

                captured_states.append(initial_state)
                yield "updates", {"finalize_direct_modification": final_state}

            async def aget_state(self, _config):
                """返回固定终态，完成 AG-UI 流程。"""

                return SimpleNamespace(values=final_state)

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.protocols.direct_modification.direct_modification_graph_for_request",
                    new=AsyncMock(return_value=FakeGraph()),
                ),
                patch(
                    "app.protocols.direct_modification.cleanup_workflow_checkpoints",
                    new=AsyncMock(return_value=0),
                ),
            ):
                stream = build_conversation_ag_ui_stream(
                    payload={
                        "threadId": "clarification-thread",
                        "runId": "clarification-run",
                        "messages": [
                            {
                                "role": "user",
                                "content": "创建到 Backend/app/config/new_feature.py，并用于单元测试。",
                            }
                        ],
                        "forwardedProps": {
                            "conversation": {
                                "workspaceRoot": workspace,
                                "selectedSkillNames": [],
                                "originalRequest": "我想新增 test.tsx 文件。",
                            }
                        },
                    }
                )

                async def collect() -> str:
                    """消费恢复流，触发 Graph 输入捕获。"""

                    return "".join([frame async for frame in stream])

                asyncio.run(collect())

        self.assertEqual(len(captured_states), 1)
        request = captured_states[0]["request"]
        self.assertIn("我想新增 test.tsx 文件。", request)
        self.assertIn("创建到 Backend/app/config/new_feature.py，并用于单元测试。", request)

    def test_capabilities_publish_independent_targetless_endpoint(self) -> None:
        """健康检查应声明独立端点且请求不需要 target。"""

        capability = conversation_capabilities()

        self.assertEqual(capability["endpoint"], "/conversation/run")
        self.assertEqual(capability["customEventName"], "conversation")
        self.assertEqual(capability["stateSnapshotKey"], "conversation")
        self.assertIs(capability["targetRequired"], False)
        self.assertEqual(capability["executionPolicy"]["subagentsEnabled"], False)
        self.assertEqual(capability["executionPolicy"]["todoPlanningEnabled"], False)
        self.assertEqual(
            capability["executionPolicy"]["implementationFixConfirmation"]["requiredOwners"],
            ["frontend", "backend", "fullstack"],
        )
        self.assertEqual(
            capability["executionPolicy"]["confirmationContinuation"]["skips"],
            ["scan_workspace_code", "classify_intent"],
        )
        self.assertIs(
            capability["formalRevision"]["additionalModelImpactAnalysis"],
            False,
        )
        self.assertEqual(
            capability["formalRevision"]["userVisibleExplanation"],
            "reason-only",
        )
        self.assertNotIn("changeImpact", capability)

    def test_input_reads_only_direct_modification_payload(self) -> None:
        """协议只读取嵌套业务字段，不要求页面或接口身份。"""

        value = conversation_input(
            {
                "forwardedProps": {
                    "conversation": {
                        "workspaceRoot": "/workspace",
                        "selectedSkillNames": [],
                    }
                }
            }
        )

        self.assertEqual(value["workspaceRoot"], "/workspace")
        self.assertNotIn("target", value)

    def test_tool_activity_projects_realtime_safe_process_step(self) -> None:
        """Agent 工具活动必须实时投射为前端可消费的安全工具步骤。"""

        reported = []

        async def report(progress) -> None:
            """收集单次协议进度，供断言结构化工具步骤。"""

            reported.append(progress)

        asyncio.run(
            _report_custom_progress(
                report,
                chunk={
                    "type": "conversation.tool_activity",
                    "node_name": "execute_frontend",
                    "activity": {
                        "callId": "read-app",
                        "tool": "read_file",
                        "status": "running",
                        "message": "正在读取文件：/src/App.tsx",
                        "path": "/src/App.tsx",
                    },
                },
                state={"status": "in_progress"},
                events=[],
            )
        )

        self.assertEqual(len(reported), 1)
        process_step = reported[0].data["processStep"]
        self.assertEqual(process_step["id"], "direct-tool:read-app")
        self.assertEqual(process_step["status"], "running")
        self.assertEqual(process_step["title"], "read_file")
        self.assertEqual(process_step["detail"], "正在读取文件：/src/App.tsx")
        self.assertEqual(process_step["nodeName"], "execute_frontend")
        self.assertEqual(process_step["sequence"], 65)

    def test_text_delta_projects_to_ag_ui_stream_without_process_noise(self) -> None:
        """模型正文增量必须进入文本流，而不是伪装成“正在思考”进度。"""

        reported = []
        text_deltas = []

        async def report(progress) -> None:
            """收集文本增量对应的协议进度占位。"""

            reported.append(progress)

        async def report_text(delta: str) -> None:
            """收集可直接展示给用户的助手正文。"""

            text_deltas.append(delta)

        asyncio.run(
            _report_custom_progress(
                report,
                chunk={"type": "conversation.text_delta", "delta": "我是 XCodeAgent。"},
                state={"status": "in_progress"},
                events=[],
                report_text=report_text,
            )
        )

        self.assertEqual(text_deltas, ["我是 XCodeAgent。"])
        self.assertEqual(reported, [])

    def test_stream_hides_pending_finalizer_process_step(self) -> None:
        """正式修改待确认时只展示已完成的分类步骤，不展示内部收尾步骤。"""

        waiting_state = {
            "phase": "conversation",
            "status": "requires_user_input",
            "message": "该请求会修改已确认的正式语义，请确认是否进入正式修改流程。",
            "conversation_intent": "formal_revision",
            "direct_modification_owner": "none",
            "direct_modification_scope": "formal",
            "clarification": {
                "mode": "revision_impact_confirmation",
                "status": "requires_user_input",
                "message": "该请求会修改已确认的正式语义，请确认是否进入正式修改流程。",
            },
            "direct_modification_result": {
                "status": "requires_user_input",
                "summary": "该请求会修改已确认的正式语义，请确认是否进入正式修改流程。",
            },
        }

        class FakeGraph:
            """提供正式修改待确认投影测试所需的最小 Graph。"""

            async def astream(self, *_args, **_kwargs):
                """依次发送分类确认与内部收尾更新。"""

                yield "updates", {"classify_intent": waiting_state}
                yield "updates", {"finalize_direct_modification": waiting_state}

            async def aget_state(self, _config):
                """返回与更新一致的待确认终态。"""

                return SimpleNamespace(values=waiting_state)

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.protocols.direct_modification.direct_modification_graph_for_request",
                    new=AsyncMock(return_value=FakeGraph()),
                ),
                patch(
                    "app.protocols.direct_modification.cleanup_workflow_checkpoints",
                    new=AsyncMock(return_value=0),
                ),
            ):
                stream = build_conversation_ag_ui_stream(
                    payload={
                        "threadId": "formal-wait-thread",
                        "runId": "formal-wait-run",
                        "messages": [
                            {"id": "message-1", "role": "user", "content": "删除订单导出功能"}
                        ],
                        "forwardedProps": {
                            "conversation": {
                                "workspaceRoot": workspace,
                                "selectedSkillNames": [],
                            }
                        },
                    }
                )

                async def collect() -> str:
                    """消费正式修改待确认事件流。"""

                    return "".join([frame async for frame in stream])

                frames = asyncio.run(collect())

        self.assertIn('direct:classify_intent', frames)
        self.assertNotIn('direct:finalize_direct_modification', frames)
        self.assertIn('已完成 判断修改类型', frames)

    def test_stream_emits_complete_ag_ui_lifecycle(self) -> None:
        """独立 Graph 结果必须发送自定义事件、快照和正常完成事件。"""

        final_state = {
            "phase": "conversation",
            "status": "completed",
            "message": "快速修改完成",
            "direct_modification_owner": "frontend",
            "direct_modification_scope": "direct",
            "direct_modification_result": {
                "status": "completed",
                "summary": "快速修改完成",
            },
            "code_changes": {},
        }

        class FakeGraph:
            """提供协议测试所需的最小异步 Graph 接口。"""

            async def astream(self, *_args, **_kwargs):
                """发送一条完整前端快速修改路径，验证步骤开始和完成顺序。"""

                yield "updates", {
                    "scan_workspace_code": {
                        "phase": "scan_workspace_code",
                        "status": "completed",
                        "message": "代码扫描完成。",
                        "workspace_snapshot_summary": {
                            "code_graph": {
                                "status": "cache_hit",
                                "available": True,
                                "message": "代码索引缓存已就绪。",
                            }
                        },
                    }
                }
                yield "updates", {
                    "classify_intent": {
                        "phase": "classify_intent",
                        "status": "in_progress",
                        "message": "已识别前端修改。",
                        "conversation_intent": "implementation_fix",
                        "direct_modification_owner": "frontend",
                        "direct_modification_scope": "direct",
                    }
                }
                yield "updates", {
                    "execute_frontend": {
                        "phase": "execute_frontend",
                        "status": "in_progress",
                        "message": "前端修改完成。",
                    }
                }
                yield "updates", {
                    "validate_direct_fix": {
                        "phase": "validate_direct_fix",
                        "status": "completed",
                        "message": "本次修改范围验证通过。",
                        "quality_gate_passed": True,
                    }
                }
                yield "updates", {"finalize_direct_modification": final_state}

            async def aget_state(self, _config):
                """返回与更新一致的最终状态。"""

                return SimpleNamespace(values=final_state)

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.protocols.direct_modification.direct_modification_graph_for_request",
                    new=AsyncMock(return_value=FakeGraph()),
                ),
                patch(
                    "app.protocols.direct_modification.cleanup_workflow_checkpoints",
                    new=AsyncMock(return_value=0),
                ),
            ):
                stream = build_conversation_ag_ui_stream(
                    payload={
                        "threadId": "direct-thread",
                        "runId": "direct-run",
                        "messages": [
                            {"id": "message-1", "role": "user", "content": "修改页面"}
                        ],
                        "forwardedProps": {
                            "conversation": {
                                "workspaceRoot": workspace,
                                "selectedSkillNames": [],
                            }
                        },
                    }
                )

                async def collect() -> str:
                    """消费完整快速修改事件流。"""

                    return "".join([frame async for frame in stream])

                frames = asyncio.run(collect())

        self.assertIn("conversation", frames)
        self.assertIn("RUN_STARTED", frames)
        self.assertIn("STATE_SNAPSHOT", frames)
        self.assertIn("RUN_FINISHED", frames)
        self.assertIn("正在执行 判断修改类型", frames)
        self.assertNotIn("启动本地预览", frames)
        self.assertLess(
            frames.index("正在执行 执行前端修改"),
            frames.index("已完成 执行前端修改"),
        )
        self.assertEqual(frames.count("TEXT_MESSAGE_CONTENT"), 1)


if __name__ == "__main__":
    unittest.main()
