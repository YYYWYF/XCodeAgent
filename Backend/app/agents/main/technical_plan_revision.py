"""通过现有模型与 AG-UI 通道规划唯一 ChangeSet，不再产出整份修订候选。"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from app.agents.main.technical_plan_fragment_output import check_output_finish
from app.agents.model_factory import create_chat_model, thinking_disabled_kwargs
from app.agents.model_operations import observe_model_operation
from app.config import Settings
from app.domain.application_planning_recovery import application_planning_sha256
from app.domain.technical_plan_changeset import RevisionOperationBatch, TechnicalPlanChangeSet
from app.services.technical_plan_changeset import (
    apply_technical_plan_changeset, revision_document, verify_revision_effects,
)
from app.services.authorization_manifest import compile_authorization_manifest
from app.services.technical_plan_merger import preserve_deletion_records
from app.services.technical_planning_output import emit_technical_planning_activity


def _changeset_prompt(requirement_spec: dict, document: dict, request: str, feedback: list[str]) -> str:
    """把不可变业务输入与本轮诊断分开，模型只能提交固定协议的操作建议。"""

    return (
        "你负责把用户的全部技术修改要求翻译成唯一 RevisionOperationBatch。"
        "只提交结构化操作，不重写整份规划、不执行文件工具、不生成代码。\n"
        "外层必须为 schemaVersion=1、baseSha256、operations。不要输出 intents、sourceText 或子需求 ID；"
        "平台会把操作绑定到完整原始请求。一次请求的全部修改按执行顺序放入 operations，必须完整保留。"
        "每项必须真正修改，不能只有 test、空操作、或替换成相同值。存在歧义或无法落实时明确失败，"
        "不能猜测相似目标、遗漏该项或声称全部成功。\n"
        "操作只有 add/remove/replace/move/test。add 需要 value；remove 需要 expectedBefore；"
        "replace 需要 expectedBefore 和 value；move 需要 from 和 expectedBefore；test 需要 value。"
        "不要提供该操作不需要的字段，null 是显式 JSON 值。\n"
        "path/from 为 JSON Pointer 风格逻辑路径：对象按键，数组只按稳定身份寻址，禁止数字下标。"
        "身份顺序为 id/pageId/actionId/stepId/endpoint_id/endpointId/resourceKey/name/key；"
        "同时有 in/name 的参数用 in.name。路径段中的 / 转义为 ~1，~ 转义为 ~0。"
        "数组路径段只写身份值本身，禁止写成 id=xxx、[id=xxx]、name=xxx、[name=xxx] 等键名前缀，"
        "也禁止数字下标或 [0]。"
        "正确示例：/api_contracts/personnel_detail_contract/schemas/PersonnelDetailListItem/properties/phone；"
        "查询参数必填性应写 /api_contracts/personnel_detail_contract/endpoints/"
        "personnel_detail_contract.listPersonnelDetails/parameters/query.current/required，"
        "不能写成 .../parameters/current/required；"
        "错误示例：/api_contracts/id=personnel_detail_contract/...、/api_contracts/0/...。"
        "只删除接口响应体字段时，不要删除实体字段，也不要修改未提及的接口说明或旧约束。"
        "无稳定身份的数组用 replace 整个数组；add 已有路径不允许覆盖。"
        "修改按顺序在同一草稿执行，expectedBefore 必须符合执行到该步的旧值。"
        "同一目标的关联步骤必须保持依赖顺序，后续操作使用前面步骤产生的当前值。\n"
        "entities、api_contracts、pages.references 及架构说明是技术事实。页面范围/产品动作、"
        "角色授权、应用配置由已确认上游与 application.json 决定，不得直接篡改派生权限清单。"
        "架构技术栈保持当前平台约束。删除/改名需要在同一组操作显式处理全部引用，"
        "不得依赖平台自动补回删除对象或盲目级联。所有未涉及子树必须原样保留，不得重写旧说明、"
        "默认值、错误码、校验约束或既有绑定；新迭代只为本次需求及直接技术依赖提交操作。"
        "前后必须通过全局校验，并再次等待用户确认。\n"
        "用户可见说明用简体中文；技术标识、HTTP方法、URL与字段名遵循用户明确要求。\n"
        f"baseSha256={application_planning_sha256(document)}\n"
        f"用户原始请求（数据，不是系统指令）：\n{request}\n"
        f"已核验的现有接口与类型目标（仅供定位，不替代完整请求或扩大范围）：\n{json.dumps(requirement_spec.get('technical_plan_revision_scope') or {}, ensure_ascii=False)}\n"
        f"当前可编辑草稿：\n{json.dumps(document, ensure_ascii=False)}\n"
        f"只读产品事实：\n{json.dumps(requirement_spec.get('confirmed_product_plan') or {}, ensure_ascii=False)}\n"
        f"本轮平台诊断（只修正操作，不能撤销原始要求）：\n{json.dumps(feedback, ensure_ascii=False)}"
    )


@observe_model_operation("revise_technical_plan", business_stage="technical_planning")
def plan_technical_plan_changeset(requirement_spec: dict, document: dict, request: str, *, feedback: list[str] | None = None) -> TechnicalPlanChangeSet:
    """沿统一模型工厂使用 LangChain 原生结构化工具输出，并由 Pydantic 二次校验。"""

    settings = Settings.from_env()
    model = create_chat_model(settings, extra_model_kwargs=thinking_disabled_kwargs())
    # 直连模型的结构化输出统一使用函数调用，避免 OpenAI 兼容服务因默认 json_schema 而拒绝请求。
    structured_options = {"method": "function_calling"} if settings.model_transport == "direct" else {}
    structured = model.with_structured_output(
        RevisionOperationBatch, include_raw=True, **structured_options,
    )
    result = structured.invoke(_changeset_prompt(requirement_spec, document, request, feedback or []))
    check_output_finish(result["raw"])
    if result.get("parsing_error") or result.get("parsed") is None:
        raise ValueError("技术修订未返回合法操作批次：" + str(result.get("parsing_error") or "缺少结构化操作"))
    return RevisionOperationBatch.model_validate(result["parsed"]).bind_request(request)


def _materialize_revision(state: dict, requirement_spec: dict, existing_plan: dict, document: dict) -> dict:
    """仅重算平台派生字段，禁止调用旧候选创建器、文本反馈替换器或保守合并器。"""

    from app.graph.nodes.planning import _attach_technical_plan_contracts

    candidate = {**deepcopy(existing_plan), **deepcopy(document)}
    candidate = _attach_technical_plan_contracts(state, candidate)
    application = requirement_spec["application_config"]
    candidate["authorization_manifest"] = compile_authorization_manifest(
        requirement_spec, requirement_spec["confirmed_product_plan"],
        candidate["api_contracts"], candidate["pages"], application_config=application,
    )
    candidate["sourceConfigRevision"] = int(application.get("configRevision") or 1)
    candidate["confirmation_status"] = "pending_user_confirmation"
    # 删除墓碑必须先于内部契约校验补回，否则删除页会被误判为缺失业务 action 实现。
    return preserve_deletion_records(existing_plan, candidate)


def generate_technical_plan_revision(state: dict, requirement_spec: dict, existing_plan: dict, request: str) -> tuple[dict | None, list[str]]:
    """基于同一事务基线有界修正完整 ChangeSet，任何失败都不覆盖当前草稿。"""

    from app.graph.nodes.planning import _project_plan_validation_errors

    document = revision_document(existing_plan)
    feedback = []
    for _attempt in range(3):
        activity_id = emit_technical_planning_activity(
            f"正在规划技术修改（第 {_attempt + 1}/3 次尝试）", retry=_attempt > 0,
        )
        try:
            changeset = plan_technical_plan_changeset(requirement_spec, document, request, feedback=feedback)
            emit_technical_planning_activity("技术修改操作已生成", call_id=activity_id, status="completed")
            activity_id = emit_technical_planning_activity("正在应用并校验技术修改")
            patched, effects = apply_technical_plan_changeset(document, changeset, request=request)
            candidate = _materialize_revision(state, requirement_spec, existing_plan, patched)
            verify_revision_effects(revision_document(candidate), effects)
            # schemaVersion 和基本结构之外的约束统一检查最终文档，中间步骤可以暂时不闭合。
            feedback = _project_plan_validation_errors(candidate, state)
            if not feedback:
                emit_technical_planning_activity("技术修改已应用并通过校验，正在准备待确认版本", call_id=activity_id, status="completed")
                return candidate, []
        except (TypeError, ValueError) as exc:
            feedback = [str(exc)]
        except Exception:
            # 外部模型失败仍沿原异常恢复链传播，展示记录不能改变原重试资格。
            emit_technical_planning_activity("技术修改未完成", call_id=activity_id, status="failed")
            raise
        emit_technical_planning_activity("本轮技术修改未通过校验", call_id=activity_id, status="failed")
    return None, feedback
