from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.agents.small_task import invoke_small_task_agent
from app.agents.tool_activity_stream import (
    ToolActivityCallback,
    invoke_agent_with_tool_activity,
)
from app.agents.workspace_assistant.agent import workspace_assistant_prompt
from app.config import Settings
from app.middleware.direct_modification import DIRECT_MODIFICATION_MODE_MARKER
from app.domain.application_revision import (
    EarliestRevisionArtifact,
    FormalRevisionBranch,
    RevisionType,
)
from app.services.direct_modification import (
    direct_path_matches_owner,
    parse_direct_modification_agent_result,
)
from app.services.revision_routing import enforce_revision_routing
from app.utils.model_output import extract_json_object
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


ConversationIntent = Literal[
    "casual_chat",
    "workspace_question",
    "clarification",
    "implementation_fix",
    "formal_revision",
]
DirectModificationOwner = Literal[
    "frontend",
    "backend",
    "fullstack",
    "workspace",
    "none",
    "unknown",
]
DirectModificationScope = Literal[
    "respond",
    "read_only",
    "direct",
    "formal_revision",
    "clarification",
]

_MAX_CONVERSATION_SUMMARY_CHARS = 4_000
_MAX_WORKSPACE_ROUTING_CONTEXT_CHARS = 16_000
_CASUAL_RESPONSE_KEY_PATTERN = re.compile(r'"response"\s*:\s*"')
_DIRECT_IGNORED_PATH_PARTS = frozenset(
    {".next", ".turbo", ".venv", "build", "coverage", "dist", "node_modules", "target"}
)
_FRONTEND_REQUIRED_SKILLS = (
    "/.xcodeagent/builtin-skills/code-block-template/SKILL.md",
    "/.xcodeagent/builtin-skills/react-develop-specification/SKILL.md",
)
_DIRECT_VERIFICATION_REPAIR_INSTRUCTIONS = (
    "## Internal verification and self-repair\n"
    "Internal verification is part of implementation and is separate from the later integration_test "
    "quality gate. After writing code, choose the narrowest relevant existing verification commands "
    "from package metadata or build files. Prefer the repository's declared package manager and scripts; "
    "do not use npx when the command is already available through pnpm, yarn, npm scripts, or local "
    "dependencies. Run verification commands directly. Never append output-truncating or success-forcing "
    "shell constructs such as `| head`, `| tail`, `|| true`, or `; true`, because they can hide the real "
    "exit code. Read the execute result's exit_code, stdout, and stderr.\n"
    "If a selected check has a non-zero exit_code or reports an error related to the files or behavior "
    "you changed, do not finish: inspect the error, repair the implementation, and rerun the relevant "
    "check. Continue this edit-check-repair loop until the selected checks pass or a genuine blocker is "
    "identified. Do not broaden the change to repair demonstrably pre-existing or unrelated failures; "
    "return status=failed with the exact command and evidence for those blockers. Return status=completed "
    "only after all selected internal checks pass. For a style- or content-only change where no Agent-layer "
    "command is proportionate, record that decision in verification and let the independent integration_test "
    "perform the final repository gate. A transient read/search/tool error is not by itself a task failure: "
    "if the requested change was written and your final inspection or an independent acceptance check confirms "
    "the requested outcome, return status=completed (or already_satisfied) and record the transient error as "
    "a warning in verification or failureReason. Return status=failed only when the requested outcome is absent, "
    "the change is outside the authorized scope, or verification finds a genuine blocker.\n"
)


@dataclass(frozen=True)
class DirectModificationDecision:
    """保存自由对话路由和局部修改归属的规范化结果。"""

    intent: ConversationIntent
    owner: DirectModificationOwner
    scope: DirectModificationScope
    confidence: float
    reason: str
    clarification_question: str
    response: str = ""
    target_paths: tuple[str, ...] = ()
    formal_branch: FormalRevisionBranch | None = None
    revision_type: RevisionType | None = None
    earliest_artifact: EarliestRevisionArtifact | None = None
    affected_artifact_keys: tuple[str, ...] = ()
    affected_resource_keys: tuple[str, ...] = ()


def _direct_modification_classifier_prompt(
    *,
    user_request: str,
    conversation_summary: str,
    workspace_snapshot: dict[str, Any] | None = None,
) -> str:
    """构造自由对话、只读问答和工作区变更的统一分类 Prompt。"""

    return (
        "Route one message in an application-development workbench. Return exactly one JSON object "
        "and no Markdown. Classify by the user's requested outcome, not isolated keywords. "
        "You are conservative about write authorization, not about collecting design details: when the "
        "user's business intent is clear, route it to the matching workflow even if page paths, components, "
        "API details, visual tokens, or recommendation logic are not provided. The downstream workflow owns "
        "requirements clarification, design completion, implementation planning, and source-file discovery.\n\n"
        "Intents:\n"
        "- casual_chat: greetings, identity questions, general conversation, explanations, or general "
        "knowledge that does not require inspecting the current workspace. Examples: '你是谁', '解释一下 "
        "什么是 REST API', '帮我梳理一下这个概念'.\n"
        "- workspace_question: read-only questions that require inspecting or explaining the current "
        "workspace, code, configuration, documentation, or architecture. Examples: '订单列表页面在哪个 "
        "文件', '项目现在用的是什么状态管理', '检查登录接口的调用链，但不要修改代码'.\n"
        "- implementation_fix: a localized code-level correction where confirmed product and technical "
        "semantics remain unchanged. This includes small changes to an existing page's UI appearance, "
        "layout, spacing, copy, responsive behavior, or browser interaction. Route these UI-only requests "
        "to owner=frontend and execute them in the current workbench; do not send them back to any design "
        "stage. It may edit frontend/backend code only after the user confirms the proposed implementation "
        "scope; ordinary workspace files are the only implementation fixes that do not need this extra "
        "confirmation. Examples: '订单页的提交按钮点了没反应，修一下', '修复头像加载失败的前端报错', "
        "'订单卡片改成双列', '详情页换成深色视觉', '把表格间距调大', '接口已经确定，只把空值校验补上'.\n"
        "- formal_revision: a change to confirmed product or technical semantics. It must include "
        "formalBranch, revisionType, earliestArtifact and affected keys. A clear product request does not "
        "become clarification merely because implementation details are missing. Examples: '新增一个页面', "
        "'订单管理增加批量归档', '接口增加筛选字段'. "
        "For a new object with no existing ID, affectedArtifactKeys and affectedResourceKeys may be empty; "
        "never invent IDs or source paths.\n"
        "- clarification: use only when neither the business object/action nor the desired outcome can be "
        "identified. Do not use it to ask for a route, component list, implementation approach, or visual "
        "recommendation that the downstream workflow can determine. Examples: '帮我改一下', '这里有问题，"
        "处理下', '优化一下' (with no object, behavior, or expected result).\n\n"
        "Formal branches and revision types:\n"
        "- design_stage_revision only for requirement_scope_change or product_behavior_change; "
        "earliestArtifact is requirement-spec or product-plan. Existing-page UI-only changes are never "
        "design_stage_revision and never use ui-design as earliestArtifact.\n"
        "- workbench_plan_revision for technical_contract_change, endpoint_implementation_change, or "
        "data_source_change; earliestArtifact is technical-plan.\n"
        "Formal examples (choose the earliest semantic layer; do not ask for source paths):\n"
        "- requirement_scope_change -> design_stage_revision / requirement-spec: '新增一个客户管理页面', "
        "'增加供应商管理模块', '支持管理员角色'. Even without a route, component, or recommendation, "
        "route here and let the requirements workflow fill them in.\n"
        "- product_behavior_change -> design_stage_revision / product-plan: '订单支持批量归档', "
        "'审批通过后通知申请人', '增加导出订单操作'.\n"
        "- existing-page UI-only -> implementation_fix / frontend: '订单卡片改成双列', "
        "'详情页换成深色视觉', '把表格间距调大'. These are immediate small edits, not a design-stage "
        "revision. A new page, new behavior, or new data field is not UI-only.\n"
        "- technical_contract_change -> workbench_plan_revision / technical-plan: '列表接口增加状态和"
        "时间筛选参数', '响应里增加 total 字段'.\n"
        "- endpoint_implementation_change -> workbench_plan_revision / technical-plan: '删除改成软删除', "
        "'给订单接口增加审计记录'.\n"
        "- data_source_change -> workbench_plan_revision / technical-plan: '把 mock 数据换成 MySQL', "
        "'订单列表改从真实数据库读取'.\n\n"
        "Owners:\n"
        "- frontend: only UI, React, styling, browser interaction, or frontend API-consumption code.\n"
        "- backend: only server, data model, persistence, validation, or API implementation code.\n"
        "- fullstack: a localized request that requires both backend and frontend changes.\n"
        "- workspace: localized documentation, tests, scripts, repository configuration, or files outside "
        "the frontend/backend ownership roots. targetPaths must contain precise relative paths or narrow globs.\n"
        "- none: casual chat, read-only workspace questions, or formal workflow routing.\n"
        "- unknown: there is not enough information to choose safely.\n\n"
        "Write reason and clarificationQuestion in Simplified Chinese. The clarification question "
        "must state what concrete information the user should add. Never classify an identity question such "
        "as '你是谁' as a workspace change.\n\n"
        "When the current request contains an original user request followed by a latest user supplement, "
        "treat them as one continuation. The latest supplement is authoritative; a concrete path or directory "
        "provided there is already answered, so do not ask the same clarification again.\n\n"
        "下面可能包含上一轮缓存的有界工作区上下文；它只用于辅助本次路由分类，不要求输出额外的"
        "影响范围证据。请结合已有页面、组件、路由和 API，"
        "但必须先判断用户请求是否改变了已确认的产品、设计或技术语义，再选择 owner；其中既有页面 UI-only "
        "微调不算设计语义变化。改变页面的存在或范围、角色、"
        "模块、流程、操作、验收规则或正式契约的存在、范围、生命周期或业务含义，属于 formal_revision。"
        "去掉、移除、取消、下线、删除等口语表达应按实际语义理解；不能因为可以通过编辑或删除源文件实现，"
        "就把语义变更降级为 implementation_fix。既有页面的 UI 视觉、布局、间距、文案、响应式和交互微调"
        "都属于 implementation_fix/frontend，应由当前工作台立即执行，不得回退到 UI 设计阶段。"
        "只有在已确认产品/技术语义保持不变时，才使用 implementation_fix，例如修复按钮无响应、渲染错误"
        "或已确认交互的代码实现。如果产品/技术语义已经明确，"
        "即使没有页面路径、组件、接口细节或推荐逻辑，也必须选择对应 formal_revision；这些细节由后续 "
        "workflow 通过澄清和设计节点补齐。只有连业务对象、动作或期望结果都无法识别时，才使用 clarification。"
        "此时也不要猜测代码路径。工作区上下文只是局部证据，未在上下文中出现不代表文件或符号不存在。\n\n"
        "分类优先级：产品或技术语义变化 -> formal_revision；既有页面 UI-only 小改动 -> implementation_fix/frontend；"
        "目标或结果不明确 -> clarification；只读检查或解释 -> workspace_question。"
        "frontend/backend owner 不能覆盖更高优先级的 formal_revision 判断；UI-only 小改动本身不是 formal_revision。\n\n"
        "关键反例：‘用户请求新增一个页面，但未指定页面路径、组件和推荐逻辑’不能回答“无法安全执行”，"
        "也不能进入 clarification；应路由为 formal_revision/design_stage_revision/requirement-spec，"
        "并让后续需求节点补齐页面目标和实现细节。formal_revision 不需要猜测 candidatePaths，缺少既有路径时留空即可。\n\n"
        "For a localized frontend/backend change, put every exact existing file required by the user's outcome "
        "but located outside the normal source roots in targetPaths. This is not limited to known config-file "
        "types. Never propose a directory, broad glob, lockfile, secret file, installed dependency, generated "
        "output, migration, schema, or .xcodeagent artifact. targetPaths are only candidates; backend policy "
        "decides whether they are added to this run's file scope.\n\n"
        "For formal_revision, keep clarificationQuestion and questions empty: the next step is the formal "
        "revision confirmation, not a request for detailed design. For implementation_fix, do not ask for "
        "implementation details that the execution Agent can discover from the bounded workspace context.\n\n"
        "For casual_chat, answer the user's message directly in response. The response must be in the "
        "user's language, concise, natural, and ready to display as the final assistant message. Do not "
        "describe this routing decision, ask for modification details, or claim workspace inspection. "
        "Place response before the routing fields so the answer can be streamed as soon as it is available. "
        "For every other intent, response must be an empty string.\n\n"
        "Return this shape:\n"
        '{"response":"final answer only for casual_chat",'
        '"route":"casual_chat|workspace_question|clarification|implementation_fix|formal_revision",'
        '"formalBranch":"design_stage_revision|workbench_plan_revision|null",'
        '"revisionType":"requirement_scope_change|product_behavior_change|technical_contract_change|endpoint_implementation_change|data_source_change|null",'
        '"earliestArtifact":"requirement-spec|product-plan|technical-plan|null",'
        '"owner":"frontend|backend|fullstack|workspace|none|unknown",'
        '"confidence":0.0,"reason":"short reason",'
        '"clarificationQuestion":"question when clarification is needed",'
        '"candidatePaths":["precise/relative/path"],'
        '"affectedArtifactKeys":[],"affectedResourceKeys":[],"questions":[]}\n\n'
        f"Bounded workspace scan context:\n{_workspace_routing_context(workspace_snapshot)}\n\n"
        f"Bounded quick-chat summary:\n{conversation_summary or '(empty)'}\n\n"
        f"Current user request:\n{user_request}"
    )


def classify_direct_modification_intent(
    *,
    user_request: str,
    conversation_summary: str = "",
    workspace_snapshot: dict[str, Any] | None = None,
    on_response_delta: Callable[[str], None] | None = None,
) -> DirectModificationDecision:
    """调用配置模型判断消息应直接回答、只读检查、局部修改或转正式流程。"""

    if not user_request.strip():
        return _clarification_fallback("用户没有提供可执行的修改需求。")
    try:
        model = create_chat_model(Settings.from_env())
        messages = [
            SystemMessage(
                content=(
                    "You are a workbench intent router. Be conservative only about write authorization; "
                    "route clear product intent even when design details are missing. Output JSON only. "
                    "All human-readable routing fields must use Simplified Chinese."
                )
            ),
            HumanMessage(
                content=_direct_modification_classifier_prompt(
                    user_request=user_request.strip(),
                    conversation_summary=conversation_summary[
                        -_MAX_CONVERSATION_SUMMARY_CHARS:
                    ],
                    workspace_snapshot=workspace_snapshot,
                )
            ),
        ]
        if on_response_delta is None or not callable(getattr(model, "stream", None)):
            response = model.invoke(messages)
            text = _coerce_content_text(getattr(response, "content", "")) or ""
        else:
            text = _stream_classifier_response(model, messages, on_response_delta)
        return _normalize_direct_modification_decision(
            extract_json_object(text) or {},
            user_request=user_request,
            target=(workspace_snapshot or {}).get("currentTarget"),
        )
    except Exception as exc:
        return _clarification_fallback(
            f"意图识别失败（{type(exc).__name__}），需要用户补充后重试。"
        )


def _workspace_routing_context(snapshot: dict[str, Any] | None) -> str:
    """把扫描快照压缩为分类所需的页面、组件、路由和工程结构事实。"""

    if not isinstance(snapshot, dict) or not snapshot:
        return "(workspace scan unavailable)"
    frontend = snapshot.get("frontend")
    frontend = frontend if isinstance(frontend, dict) else {}
    backend = snapshot.get("backend")
    backend = backend if isinstance(backend, dict) else {}
    code_graph = snapshot.get("code_graph")
    code_graph = code_graph if isinstance(code_graph, dict) else {}
    context = {
        "workspaceRevision": str(snapshot.get("workspace_revision") or "")[:160],
        "techStack": _bounded_routing_items(snapshot.get("tech_stack"), limit=30),
        "projectRoots": _bounded_routing_items(snapshot.get("project_roots"), limit=30),
        "entrypoints": _bounded_routing_items(snapshot.get("entrypoints"), limit=50),
        "highValueFiles": _bounded_routing_items(snapshot.get("high_value_files"), limit=100),
        "buildCommands": _bounded_routing_items(snapshot.get("build_commands"), limit=30),
        "testCommands": _bounded_routing_items(snapshot.get("test_commands"), limit=30),
        "frontend": {
            "pages": _bounded_routing_items(frontend.get("pages"), limit=200),
            "components": _bounded_routing_items(frontend.get("components"), limit=200),
            "apiClients": _bounded_routing_items(frontend.get("api_clients"), limit=100),
        },
        "backend": {
            "apiRoutes": _bounded_routing_items(backend.get("api_routes"), limit=100),
            "models": _bounded_routing_items(backend.get("models"), limit=100),
        },
        "sharedContracts": _bounded_routing_items(
            snapshot.get("shared_contracts"),
            limit=100,
        ),
        "codeGraph": {
            "available": bool(code_graph.get("available")),
            "languages": _bounded_routing_items(code_graph.get("languages"), limit=20),
            "sampleSymbols": _bounded_routing_items(
                code_graph.get("sampleSymbols"),
                limit=100,
            ),
        },
    }
    encoded = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    return encoded[:_MAX_WORKSPACE_ROUTING_CONTEXT_CHARS]


def _bounded_routing_items(value: Any, *, limit: int) -> list[Any]:
    """裁剪扫描列表中的字段和值，避免分类上下文随仓库规模无界增长。"""

    if not isinstance(value, list):
        return []
    result: list[Any] = []
    for item in value[:limit]:
        if isinstance(item, dict):
            result.append(
                {
                    str(key)[:100]: str(field_value)[:500]
                    for key, field_value in list(item.items())[:20]
                }
            )
        else:
            result.append(str(item)[:500])
    return result


def _normalize_direct_modification_decision(
    payload: dict[str, Any],
    *,
    user_request: str = "",
    target: dict[str, Any] | None = None,
) -> DirectModificationDecision:
    """校验模型分类结果，并对低置信度结果执行安全降级。"""

    try:
        routed = enforce_revision_routing(
            payload,
            user_request=user_request,
            target=target,
        )
        candidate = routed.candidate
    except (TypeError, ValueError):
        return _clarification_fallback("模型没有返回符合当前二次修改合同的路由。")
    intent = candidate.route.value
    owner = candidate.owner
    confidence = candidate.confidence
    reason = candidate.reason
    clarification_question = _chinese_clarification_question(
        payload.get("clarificationQuestion")
        or payload.get("clarification_question")
        or "请补充要修改的功能、页面、组件或接口，以及期望结果。"
    )
    response = str(payload.get("response") or payload.get("answer") or "").strip()
    target_paths = tuple(_safe_target_paths(candidate.candidate_paths))
    if intent == "implementation_fix" and owner == "workspace" and not target_paths:
        return _clarification_fallback("工作区普通文件修改缺少精确路径。")
    if intent == "clarification":
        return DirectModificationDecision(
            intent="clarification",
            owner="unknown",
            scope="clarification",
            confidence=confidence,
            reason=reason,
            clarification_question=clarification_question,
            response="",
            target_paths=target_paths,
        )
    scope_by_intent = {
        "casual_chat": "respond",
        "workspace_question": "read_only",
        "implementation_fix": "direct",
        "formal_revision": "formal_revision",
    }
    return DirectModificationDecision(
        intent=intent,  # type: ignore[arg-type]
        owner=owner,  # type: ignore[arg-type]
        scope=scope_by_intent[intent],  # type: ignore[arg-type]
        confidence=confidence,
        reason=reason,
        clarification_question=clarification_question,
        response=response if intent == "casual_chat" else "",
        target_paths=target_paths,
        formal_branch=candidate.formal_branch,
        revision_type=candidate.revision_type,
        earliest_artifact=candidate.earliest_artifact,
        affected_artifact_keys=tuple(candidate.affected_artifact_keys),
        affected_resource_keys=tuple(candidate.affected_resource_keys),
    )


def _chinese_clarification_question(value: Any) -> str:
    """保留模型生成的中文澄清问题，并将非中文结果降级为稳定中文提示。"""

    question = str(value or "").strip()
    if question and any("\u4e00" <= char <= "\u9fff" for char in question):
        return question
    return "请说明您想修改的具体内容，并补充修改位置和预期效果。"


def _clarification_fallback(reason: str) -> DirectModificationDecision:
    """在分类不可用时返回不修改代码的澄清结果。"""

    return DirectModificationDecision(
        intent="clarification",
        owner="unknown",
        scope="clarification",
        confidence=0.0,
        reason=reason,
        clarification_question="请补充要修改的功能、页面、组件或接口，以及期望结果。",
    )


def _stream_classifier_response(
    model: Any,
    messages: list[SystemMessage | HumanMessage],
    on_response_delta: Callable[[str], None],
) -> str:
    """流式读取分类 JSON，并只转发其中可展示的 casual_chat 回复字段。"""

    accumulated = ""
    emitted_length = 0
    for chunk in model.stream(messages):
        token = _coerce_content_text(getattr(chunk, "content", chunk)) or ""
        if not token:
            continue
        accumulated += token
        response_prefix = _partial_json_response_value(accumulated)
        if len(response_prefix) <= emitted_length:
            continue
        on_response_delta(response_prefix[emitted_length:])
        emitted_length = len(response_prefix)
    return accumulated


def _partial_json_response_value(text: str) -> str:
    """从不完整 JSON 中安全提取 response 字符串的当前可解析前缀。"""

    match = _CASUAL_RESPONSE_KEY_PATTERN.search(text)
    if not match:
        return ""
    raw = text[match.end() :]
    escaped = False
    for index, character in enumerate(raw):
        if character == '"' and not escaped:
            decoded = _decode_json_string_prefix(raw[:index])
            if decoded is not None:
                return decoded
            break
        if character == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
    return _decode_json_string_prefix(raw) or ""


def _decode_json_string_prefix(raw: str) -> str | None:
    """把可能仍在生成中的 JSON 字符串前缀转换为普通文本。"""

    candidate = raw
    while candidate:
        try:
            value = json.loads(f'"{candidate}"')
            return value if isinstance(value, str) else None
        except json.JSONDecodeError:
            if not candidate.endswith("\\"):
                return None
            candidate = candidate[:-1]
    return ""


def _safe_target_paths(value: Any) -> list[str]:
    """裁剪分类器给出的普通工作区目标路径，拒绝敏感和正式工件范围。"""

    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:100]:
        path = str(item or "").strip().replace("\\", "/").lstrip("/")
        parts = [part for part in path.split("/") if part]
        lowered = path.casefold()
        if (
            not parts
            or ".." in parts
            or any(part == ".env" or part.startswith(".env.") for part in parts)
            or ".xcodeagent/" in lowered
        ):
            continue
        if path not in result:
            result.append(path[:1_000])
    return result


def answer_casual_conversation(*, user_request: str, conversation_summary: str = "") -> str:
    """使用无工具 ChatModel 回答身份、常规交流和通用知识问题。"""

    model = create_chat_model(Settings.from_env())
    response = model.invoke(
        [
            SystemMessage(
                content=(
                    "You are XCodeAgent, an AI application-development assistant inside a desktop "
                    "workbench. Answer normal conversation and general questions naturally. You do not "
                    "have workspace evidence in this mode, so never claim that you inspected or changed "
                    "files. Reply in the user's language and keep the answer concise unless detail is requested."
                )
            ),
            HumanMessage(
                content=(
                    f"Bounded conversation summary:\n{conversation_summary[-4_000:] or '(empty)'}\n\n"
                    f"Current user message:\n{user_request.strip()}"
                )
            ),
        ]
    )
    return (_coerce_content_text(getattr(response, "content", "")) or "").strip()


def answer_workspace_question(
    *,
    user_request: str,
    conversation_summary: str,
    workspace: str | None,
    selected_skill_names: list[str] | None,
    on_tool_activity: ToolActivityCallback | None = None,
    on_text_delta: Callable[[str], None] | None = None,
) -> str:
    """调用只读 Workspace Assistant 基于当前工程证据回答问题。"""

    from app.agents import create_agent_bundle

    return invoke_agent_with_tool_activity(
        create_agent_bundle(workspace, selected_skill_names).workspace_assistant,
        {
            "messages": [
                {
                    "role": "user",
                    "content": workspace_assistant_prompt(
                        request=user_request,
                        conversation_summary=conversation_summary,
                    ),
                }
            ]
        },
        workspace=workspace,
        on_tool_activity=on_tool_activity,
        on_text_delta=on_text_delta,
    )


def _frontend_direct_modification_prompt(
    *,
    user_request: str,
    conversation_summary: str,
    backend_handoff: dict[str, Any] | None = None,
) -> str:
    """构造不依赖正式计划产物的前端快速修改 Prompt。"""

    handoff = json.dumps(backend_handoff or {}, ensure_ascii=False, indent=2)[:8_000]
    return (
        f"{DIRECT_MODIFICATION_MODE_MARKER}\n"
        "You are handling a localized frontend modification in an existing application.\n"
        "Do not create, read as an execution contract, or depend on RequirementSpec, ProjectPlan, "
        "BuildTaskPlan, approved tasks, or a task DAG. Inspect the existing implementation, make "
        "the smallest safe change, and verify it with the repository's existing frontend commands.\n"
        "Write only frontend application code. You may read backend code and the backend handoff, "
        "but must not modify backend files. Never create temporary scripts for verification.\n"
        "This is a direct-edit run: task and write_todos are unavailable. Do not delegate discovery "
        "or verification. Inspect relevant code progressively and avoid unrelated repository-wide "
        "scans. After editing, call execute yourself for the focused existing typechecks, tests, or "
        "build commands appropriate to the actual change scope. Keep verification proportional for "
        "style-only changes. Never replace command execution with a manual repository-wide review.\n\n"
        f"{_DIRECT_VERIFICATION_REPAIR_INSTRUCTIONS}\n"
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}\n\n"
        "## Mandatory built-in skills\n"
        "Before writing any code, use read_file(limit=400) to read each file below completely and "
        "follow it. If a read fails, do not treat that single tool error as the final task result: "
        "continue only when the requested change can still be made safely, then verify the outcome "
        "and report the tool error as a warning if the task is otherwise complete.\n"
        f"1. `{_FRONTEND_REQUIRED_SKILLS[0]}`\n"
        f"2. `{_FRONTEND_REQUIRED_SKILLS[1]}`\n"
        "Read code-block-template references only when relevant to this request; do not load all "
        "references up front. These built-in skills are mounted read-only and are independent of "
        "the user-selected skill list.\n\n"
        "Return exactly one JSON object with: status (completed or failed), summary, changedFiles, "
        "verification, alreadySatisfied, and failureReason. Set alreadySatisfied=true only after "
        "verifying that the exact requested behavior already exists. Do not return Markdown around "
        "the JSON object.\n\n"
        f"Bounded quick-chat summary:\n{conversation_summary or '(empty)'}\n\n"
        f"Backend handoff for this run:\n{handoff}\n\n"
        f"Current user request:\n{user_request}"
    )


def _data_source_direct_modification_prompt(
    *,
    user_request: str,
    conversation_summary: str,
) -> str:
    """构造暂不要求内置 Skill 的后端快速修改 Prompt。"""

    return (
        f"{DIRECT_MODIFICATION_MODE_MARKER}\n"
        "You are handling a localized backend/data-source modification in an existing application.\n"
        "Do not create, read as an execution contract, or depend on RequirementSpec, ProjectPlan, "
        "BuildTaskPlan, approved tasks, or a task DAG. Inspect the existing implementation, make "
        "the smallest safe change, and verify it with the repository's existing backend commands.\n"
        "Write only backend application code. You may read frontend code to understand consumers, "
        "but must not modify frontend files. There are currently no mandatory built-in skills for "
        "this direct backend flow. Never create temporary scripts for verification.\n"
        "This is a direct-edit run: task and write_todos are unavailable. Do not delegate discovery "
        "or verification. Inspect relevant files progressively and avoid unrelated repository-wide "
        "scans. After editing, call execute yourself for the focused existing backend tests, checks, "
        "or build commands appropriate to the actual change scope. Never replace command execution "
        "with a manual repository-wide review.\n\n"
        f"{_DIRECT_VERIFICATION_REPAIR_INSTRUCTIONS}\n"
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}\n\n"
        "Return exactly one JSON object with: status (completed or failed), summary, changedFiles, "
        "verification, alreadySatisfied, failureReason, and backendHandoff. backendHandoff must "
        "contain summary, endpoints, changedFiles, and notes; endpoints must describe method, path, "
        "request, and response when an API changed. Set alreadySatisfied=true only after verifying "
        "the exact requested behavior already exists. Do not return Markdown around the JSON object.\n\n"
        f"Bounded quick-chat summary:\n{conversation_summary or '(empty)'}\n\n"
        f"Current user request:\n{user_request}"
    )


def _workspace_direct_modification_prompt(
    *,
    user_request: str,
    conversation_summary: str,
    target_paths: list[str],
) -> str:
    """构造文档、测试、脚本和普通配置文件的受限修改 Prompt。"""

    return (
        f"{DIRECT_MODIFICATION_MODE_MARKER}\n"
        "You are handling one localized non-product workspace change. Modify only the precise "
        "allowed paths in the task packet. This mode may update ordinary documentation, tests, "
        "scripts, or repository configuration, but must not modify application frontend/backend "
        "code, .env files, database migrations, RequirementSpec, ProjectPlan, API contracts, or "
        "other .xcodeagent workflow artifacts. Do not use task, write_todos, or subagents. Inspect "
        "only relevant context, make the smallest change, and use proportionate existing checks. "
        "For documentation-only work, state why no command is necessary. Return exactly one JSON "
        "object using the SmallTask result contract.\n\n"
        f"Authorized target paths:\n{json.dumps(target_paths, ensure_ascii=False)}\n\n"
        f"Bounded conversation summary:\n{conversation_summary[-4_000:] or '(empty)'}\n\n"
        f"Current user request:\n{user_request}"
    )


def invoke_frontend_direct_modification(
    *,
    user_request: str,
    conversation_summary: str,
    backend_handoff: dict[str, Any] | None,
    candidate_files: list[str] | None = None,
    approved_paths: list[str] | None = None,
    workspace: str | None,
    selected_skill_names: list[str] | None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> str:
    """复用 Frontend Agent 执行快速修改专用 Prompt。"""

    scoped_paths = _approved_owner_paths(approved_paths, owner="frontend")
    source_candidates = _approved_owner_paths(candidate_files, owner="frontend")
    task_candidates = list(dict.fromkeys([*scoped_paths, *source_candidates]))[:100]
    return invoke_small_task_agent(
        packet={
            "source": "free_chat",
            "owner": "frontend",
            "description": user_request,
            "allowedPaths": ["Frontend/src/**", "frontend/src/**", *scoped_paths],
            "approvedPaths": scoped_paths,
            "targetFiles": task_candidates,
            "candidateFiles": task_candidates,
            "acceptanceCriteria": ["按用户要求完成局部前端修改并通过相关检查。"],
            "request": user_request,
            "conversationSummary": conversation_summary[-4_000:],
            "backendHandoff": backend_handoff or {},
            "legacyInstructions": _frontend_direct_modification_prompt(
                user_request=user_request,
                conversation_summary=conversation_summary,
                backend_handoff=backend_handoff,
            ),
        },
        workspace=workspace,
        selected_skill_names=selected_skill_names,
        on_tool_activity=on_tool_activity,
    )


def invoke_data_source_direct_modification(
    *,
    user_request: str,
    conversation_summary: str,
    candidate_files: list[str] | None = None,
    approved_paths: list[str] | None = None,
    workspace: str | None,
    selected_skill_names: list[str] | None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> str:
    """复用 Data Source Agent 执行快速修改专用 Prompt。"""

    scoped_paths = _approved_owner_paths(approved_paths, owner="backend")
    source_candidates = _approved_owner_paths(candidate_files, owner="backend")
    task_candidates = list(dict.fromkeys([*scoped_paths, *source_candidates]))[:100]
    return invoke_small_task_agent(
        packet={
            "source": "free_chat",
            "owner": "backend",
            "description": user_request,
            "allowedPaths": [
                "Backend/app/**",
                "Backend/src/**",
                "Backend/tests/**",
                "backend/app/**",
                "backend/src/**",
                "backend/tests/**",
                *scoped_paths,
            ],
            "approvedPaths": scoped_paths,
            "targetFiles": task_candidates,
            "candidateFiles": task_candidates,
            "acceptanceCriteria": ["按用户要求完成局部后端修改并通过相关检查。"],
            "request": user_request,
            "conversationSummary": conversation_summary[-4_000:],
            "legacyInstructions": _data_source_direct_modification_prompt(
                user_request=user_request,
                conversation_summary=conversation_summary,
            ),
        },
        workspace=workspace,
        selected_skill_names=selected_skill_names,
        on_tool_activity=on_tool_activity,
    )


def invoke_workspace_direct_modification(
    *,
    user_request: str,
    conversation_summary: str,
    target_paths: list[str],
    approved_paths: list[str] | None = None,
    workspace: str | None,
    selected_skill_names: list[str] | None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> str:
    """使用共享 SmallTask Agent 执行精确路径限定的非产品工作区修改。"""

    scoped_paths = list(
        dict.fromkeys(
            [
                *_safe_target_paths(target_paths),
                *_approved_owner_paths(approved_paths, owner="workspace"),
            ]
        )
    )[:100]
    return invoke_small_task_agent(
        packet={
            "source": "free_chat",
            "kind": "workspace_change",
            "owner": "workspace",
            "description": user_request,
            "allowedPaths": scoped_paths,
            "approvedPaths": _approved_owner_paths(approved_paths, owner="workspace"),
            "targetFiles": scoped_paths,
            "acceptanceCriteria": ["按用户要求完成限定路径内的局部工作区修改。"],
            "request": user_request,
            "conversationSummary": conversation_summary[-4_000:],
            "legacyInstructions": _workspace_direct_modification_prompt(
                user_request=user_request,
                conversation_summary=conversation_summary,
                target_paths=scoped_paths,
            ),
        },
        workspace=workspace,
        selected_skill_names=selected_skill_names,
        on_tool_activity=on_tool_activity,
    )


def _approved_owner_paths(paths: list[str] | None, *, owner: str) -> list[str]:
    """只保留与自由对话当前 owner 目录一致的追加授权路径。"""

    return [
        path
        for path in paths or []
        if direct_path_matches_owner(str(path), owner)
        and not any(
            part.casefold() in _DIRECT_IGNORED_PATH_PARTS
            for part in str(path).replace("\\", "/").split("/")
        )
    ][:100]
