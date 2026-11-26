from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any

from app.agents.frontend.execution_scope import (
    FRONTEND_ENGINEERING_DOC, FRONTEND_TEMPLATE_AGENTS_DOC, FrontendTaskExecutionScope,
)
from app.agents.frontend.project_plan_context import FrontendTaskProjectionError
from app.agents.frontend.retry_guard import FrontendTaskRetryExhausted
from app.agents.frontend.task_packet import PAGE, STATIC, frontend_task_packet, task_kind
from app.agents.frontend.static_prompt import _frontend_generation_prompt
from app.agents.frontend.trace import frontend_agent_trace
from app.agents.tool_activity_stream import (
    ToolActivityCallback,
    invoke_agent_with_tool_activity,
)
from app.observability.build_instrumentation import record_prompt
from app.config import Settings
from app.services.build_result_coordinator import create_agent_task_results


def _scoped_generation_prompt(packets: list[dict]) -> str:
    """只序列化任务包，正式实施事实和规范引用各注入一次。"""
    task_text = json.dumps(packets, ensure_ascii=False, separators=(",", ":"))
    page = any(packet["kind"] == PAGE for packet in packets)
    execution = (
        "每个任务先单独调用 begin_frontend_task。顺序完成四步，每步后单独调用 "
        "advance_frontend_step 并简述完成情况。Step 1 只读，可按精确文件调用 grep 或 "
        "code_graph_context。在 Step 1 完整理解需求并按需核对任务已声明的 API、设计稿、模板组件、"
        "类型和常量依赖，确定实施方案，"
        "不要求读完全部文件。Step 2/3 复用核对结果，仅可写已声明的业务目标，不开始无关文件探索。"
        "Step 4 只读核对，单独调用 "
        "complete_frontend_task 后立即停止本任务的工具探索。各控制工具单独发送一轮。"
        "Step 2/3 验收失败时依据返回的具体问题在当前步骤修复；无法修复时用 defer_to_repair 前进，"
        "并在 Step 4 以 acceptance_verification_failed 提交失败。"
        if page else
        "平台已初始化当前 API 模块任务的权限上下文，仅实施当前 API 模块职责。"
        "直接使用 implementation_contract 中已注入的正式 API 契约，不搜索其他业务 API 文件获取契约。"
        "按需读取已授权的统一请求封装 src/apis/service.ts 和当前目标文件；"
        "已有目标文件时，先依据当前 implementation_contract 和适用工程规范核对现有实现，仅在发现明确差异时修改。"
        "若已有实现满足当前任务全部要求，直接返回 already_satisfied，不进行文件修改或无必要的重复核对。"
        "禁止仅因示例写法、非强制性风格偏好或等价实现方式不同而改写已有代码。"
        "存在明确差异时，仅增量修复不满足契约或适用工程规范的部分。"
        "必要示例仅用于编码参考，不覆盖业务契约。"
        "模板 AGENTS.md 中列出的工程文件仅用于说明结构，不是逐一读取清单；"
        "无当前任务明确依赖时，不读取相邻 API、登录或权限模块。"
        "当已有信息足以判断任务状态时，立即完成必要修改或直接提交已满足结果，"
        "仅进行必要自检，不再为了寻找参考而探索其他文件。"
        "无关文件读取被拒绝时不得转向目录遍历或无关的 grep、Code Graph 搜索；"
        "若存在真实契约冲突或必要信息缺失，只能在既有授权范围内定向核对，"
        "无法完成时如实返回 failed，不得虚报完成或请求扩大权限。"
    )
    skill_loading = (
        "先单独调用 begin_frontend_task，等待成功返回 Step 1。"
        "此调用不得与任何其他工具并发或同批发送。仅在进入 Step 1 后，读取：\n"
        f"1. {FRONTEND_ENGINEERING_DOC}\n"
        f"2. {FRONTEND_TEMPLATE_AGENTS_DOC}\n"
        "Step 1 之前不得尝试读取 Skill 或业务文件。"
        "本页面后续步骤复用已读取的两份核心文档，不因切换步骤重复读取。"
        "仅按需阅读示例，后续任务复用规范。"
        if page else
        f"开始实施时读取 {FRONTEND_ENGINEERING_DOC} "
        f"和 {FRONTEND_TEMPLATE_AGENTS_DOC}；仅按需阅读示例，"
        "后续任务复用规范。"
    )
    page_guidance = (
        "页面消费 implementation_contract.api_dependencies 的导出。简单查询在页面直接使用 useRequest，"
        "复杂复用或跨组件编排再抽取 Hook。"
        "遇到复杂表单、联动或批量选择时，可按需读取 references/component-patterns.md 对应章节；"
        "多标签页或导入导出组合可按需读取 references/page-structure-examples.md 对应章节。"
        "这些路径相对 Engineering Skill，仅读取指定章节或必要范围，不默认完整读取全部 references，"
        "不新增 Skill 阅读子任务，也不扩大业务文件权限。"
        "通过 dataSource、loading 或 options 传给组件，事件型 mutation 可直接调用业务函数。"
        "确认设计稿和所选模板只参考布局与组件，按规范重新绑定数据，省略示例数据和预览控件。"
        "设计稿缺失或不可读时按页面契约实现。页面按 authorization_actions 的 resourceConstant "
        "精确使用模板 Permission 与 import { RESOURCES } from '@/constants/resources'，保持契约指定的权限方案。"
        "已声明的权限模板、API、类型和常量的实际导出和用法优先在 Step 1 按需核对；Step 3 复用可靠结果，"
        "仅当文件版本变化、核对未完成或出现明确契约冲突时，定向补读已授权精确文件；"
        "读取失败不得猜测接口或扩大搜索范围。"
        "导航使用契约提供的目标与参数。" if page else ""
    )
    prompt = (
        "仅实施以下已批准的任务。implementation_contract 是唯一实施事实入口，"
        "文件用于核对允许范围内的现有实现，按业务契约、模板组件与权限契约、工程规范、示例的优先级实施。使用虚拟绝对文件路径。\n"
        f"{execution}"
        "普通业务文件未变化时优先复用已读正文，需核对时可正常重读。禁止目录遍历和 Shell。\n"
        f"{skill_loading}遵循 implementation_contract.frontend_standards 中适用规则。"
        "两份核心文档均须单次完整读取（offset=0，limit=2000），同一任务每份最多成功读取一次。"
        "多页面任务可复用已有上下文，不强制跨任务重复读取。"
        "Engineering Skill 负责通用开发规范，模板 AGENTS.md 负责当前工程真实能力和导出；"
        "有歧义时仅核对已授权源码。implementation_contract 与已确认 API、设计及权限契约决定业务实施事实，"
        "实际文件授权和步骤约束由平台工具校验，任何文档均不可扩权或覆盖业务契约。"
        "核心文档缺失、不可读或正文截断时，不搜索替代 AGENTS.md、不回退旧边界 Skill、不猜测模板接口；"
        "核心文档读取失败或无法确认完整交付时，返回 failed、failure_category=tool_error 和简明原因，"
        "不将读取失败归类为契约或计划冲突，交由原调度和修复链路处理。"
        "先确定方案、集中修改，无需输出独立计划。已有 API 文件使用 edit_file 增量实现。"
        f"{page_guidance}\n"
        "最终输出单一 JSON 对象 {\"task_results\":[...]}。每项提供 task_id、summary、"
        f"{'与完成工具一致的 ' if page else ''}status（completed、already_satisfied 或 failed）。"
        "失败提供 failure_category、failure_reason，契约外变更提交 change_request。"
        "契约或计划不适用时分别使用 contract_mismatch 或 plan_mismatch。"
        "这两类失败必须提供非空且具体说明所需调整的 change_request；其他失败分类不得携带 change_request。"
        "平台独立判定验收后才能完成任务。\n"
        f"前端任务包\n{task_text}"
    )
    record_prompt(prompt, task_context=task_text)
    return prompt


def _task_failure(task: dict, reason: str, category: str) -> dict:
    """使用现有任务结果字段报告平台准备或步骤协议失败。"""
    return {"task_id": task["id"], "owner": task.get("owner"), "status": "failed",
            "failure_category": category, "failure_reason": reason, "agent_note": reason}


def _invoke_live_frontend_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None,
    selected_skill_names: list[str] | None,
    page_template: dict[str, Any] | None = None,
    ui_designs: dict[str, Any] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
    scope: FrontendTaskExecutionScope | None = None,
) -> str:
    """使用本次工作流的技能白名单调用前端 Deep Agent。"""

    # 延迟创建可确保 Agent 的工作区和技能权限只属于本次运行。
    from app.agents import create_agent_bundle

    bundle = create_agent_bundle(workspace, selected_skill_names)
    agent = bundle.frontend_build_factory(scope) if scope else bundle.frontend
    payload = {
        "messages": [
            {
                "role": "user",
                **({"id": "frontend-build-initial-tasks"} if scope else {}),
                "content": (_scoped_generation_prompt(scope.packets) if scope else _frontend_generation_prompt(
                    project_plan=project_plan,
                    build_task_plan=build_task_plan,
                    tasks=tasks,
                    page_template=page_template,
                    ui_designs=ui_designs,
                )),
            }
        ]
    }
    with frontend_agent_trace(workspace=workspace, tasks=tasks) as trace:
        if scope and trace:
            scope.trace = trace.frontend_event
        return invoke_agent_with_tool_activity(
            agent, payload,
            workspace=workspace,
            on_tool_activity=on_tool_activity,
            config={"callbacks": [trace]} if trace is not None else None,
        )


def generate_frontend_with_deep_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
    page_template: dict[str, Any] | None = None,
    ui_designs: dict[str, Any] | None = None,
    template_page_id: str | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> list[dict[str, Any]]:
    """通过带技能白名单的 Frontend Deep Agent 执行已批准任务。"""

    if not tasks:
        return []

    settings = Settings.from_env()
    results = {}
    static_tasks, scoped_tasks, packets = [], [], []
    for task in tasks:
        kind = task_kind(task)
        if kind == STATIC:
            static_tasks.append(task)
            continue
        try:
            if not workspace:
                raise FrontendTaskProjectionError(task.get("id"), "workspace", "missing_workspace")
            packet = frontend_task_packet(
                project_plan, build_task_plan, task, ui_designs=ui_designs,
                page_template=page_template, template_page_id=template_page_id,
                workspace=workspace,
            )
        except FrontendTaskProjectionError as exc:
            results[task["id"]] = _task_failure(task, str(exc), "runner_protocol_error")
            continue
        scoped_tasks.append(task)
        packets.append(packet)

    # 静态模块与受控任务分开调用，避免静态工作区授权进入受控 Agent。
    # API 独占一次调用，确保步骤工具不可见且授权不会混入后续任务。
    groups = []
    for task, packet in zip(scoped_tasks, packets, strict=True):
        # 连续页面保持原有批量调用，只有 API 为隔离工具集合而独占调用。
        if packet["kind"] == PAGE and groups and task_kind(groups[-1][0][-1]) == PAGE:
            groups[-1][0].append(task)
            groups[-1][1].packets.append(packet)
        else:
            groups.append(([task], FrontendTaskExecutionScope(workspace, [packet])))
    groups.append((static_tasks, None))
    for group, group_scope in groups:
        if not group:
            continue
        try:
            with group_scope.task_context() if group_scope else nullcontext():
                agent_note = _invoke_live_frontend_agent(
                    project_plan=project_plan, build_task_plan=build_task_plan, tasks=group,
                    workspace=workspace, selected_skill_names=selected_skill_names,
                    page_template=page_template, ui_designs=ui_designs,
                    on_tool_activity=on_tool_activity, scope=group_scope,
                )
        except FrontendTaskRetryExhausted:
            if group_scope is None or not group_scope.retry.termination:
                raise
            agent_note = _terminated_task_reports(group, group_scope)
        reports = create_agent_task_results(
            group, agent_note,
            executed_by={
                "agent": "frontend-generation-agent", "mode": "live", "model": settings.model_name,
                "source": "frontend_deep_agent", "requiredSkillsLoaded": list(selected_skill_names or []),
            },
            require_structured=True,
            strict_schema=group_scope is not None,
        )
        for task, report in zip(group, reports, strict=True):
            if (group_scope and task_kind(task) == PAGE
                and report["status"] in {"completed", "already_satisfied"}
                and group_scope.outcomes.get(task["id"]) != report["status"]):
                report = _task_failure(task, "任务未按四步协议完成或报告状态与完成工具不一致。",
                                       "runner_protocol_error")
            results[task["id"]] = report
    return [results[task["id"]] for task in tasks]


def _terminated_task_reports(tasks: list[dict], scope: FrontendTaskExecutionScope) -> str:
    """从正式完成回执与证据构造报告；外层继续执行原平台验收。"""
    termination = scope.retry.termination
    reports = []
    for task in tasks:
        identity = task["id"]
        status = scope.outcomes.get(identity)
        if status in {"completed", "already_satisfied"} and scope.evidence.get(identity):
            reports.append({"task_id": identity, "status": status,
                            "summary": "任务已通过正式完成工具确认；后续任务执行终止。"})
            continue
        current = identity == termination["task_id"]
        category = "tool_error" if current else "runner_crash"
        reason = termination["reason"] if current else "前端调用已终止，本任务未取得成功完成回执，等待显式重试。"
        failure = _task_failure(task, reason, category)
        reports.append({"task_id": identity, "status": "failed", "summary": reason,
                        "failure_category": failure["failure_category"], "failure_reason": reason})
    return json.dumps({"task_results": reports}, ensure_ascii=False)
