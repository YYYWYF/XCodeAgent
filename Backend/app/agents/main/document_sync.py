from __future__ import annotations

import json
from typing import Any

from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.data_source_policy import DatasourceType
from app.services.project_plan import (
    TECHNICAL_PLAN_ARTIFACT_TYPE,
    create_project_plan,
    create_technical_plan,
    validate_project_plan_datasource_policy,
)
from app.services.product_plan import create_product_plan, validate_product_plan
from app.services.requirement_spec import create_requirement_spec
from app.utils.model_output import extract_json_object


def _sync_prompt(
    *,
    artifact_name: str,
    structured_document: dict[str, Any],
    edited_markdown: str,
    datasource_type: DatasourceType = "database",
) -> str:
    """构建 Markdown 同步提示，并守住产品、技术与实体设计边界。"""

    datasource_instruction = (
        "For RequirementSpec, entities are top-level items with id, name, description, and "
        "display-only fields (label and description). Do NOT generate field names, field types, or "
        "any data_sources; data source selection happens during entity design. The legacy type mock "
        "must never be emitted.\n\n"
        if artifact_name == "RequirementSpec"
        else (
            "For TechnicalPlan, preserve the complete top-level entities array with RequirementSpec "
            "ids/names/descriptions and the confirmed snake_case field definitions. API contracts bind "
            "one or more entities through entity_ids only. Never emit data_source_id, a top-level "
            "data_sources field, or entity data_source; source selection belongs to EntityDesign. "
            "module_boundaries describes code/service ownership and must not define entities or fields.\n\n"
        )
        if artifact_name == "TechnicalPlan"
        else (
            "For ProjectPlan, keep entities source-free and bind API contracts through entity_ids only. "
            "Do not emit a top-level data_sources field or assign entity data_source; those decisions "
            "belong to the separately confirmed entity design. Preserve entity ids and contract "
            "references, and never emit mock.\n\n"
        )
        if artifact_name == "ProjectPlan"
        else ""
    )
    visible_document = (
        {
            key: value
            for key, value in structured_document.items()
            if key not in {"data_sources", "acceptance_criteria"}
        }
        if artifact_name == "RequirementSpec"
        else structured_document
    )
    return (
        f"You synchronize a user-edited {artifact_name} Markdown document back into its internal JSON.\n"
        "This is a document-sync boundary. Do not call tools, do not generate code, and return only "
        "one complete JSON object without markdown fences. Treat the edited Markdown as authoritative "
        "for user-visible business content. Preserve internal ids, schema details, dependencies, and "
        "metadata that the Markdown does not represent. Apply additions, edits, and removals expressed "
        "by the Markdown, but do not invent unrelated fields or discard hidden structured details.\n\n"
        f"{datasource_instruction}"
        f"Current internal JSON:\n{json.dumps(visible_document, ensure_ascii=False)}\n\n"
        f"User-edited Markdown:\n{edited_markdown}"
    )


def _invoke_sync_model(
    *,
    artifact_name: str,
    structured_document: dict[str, Any],
    edited_markdown: str,
    datasource_type: DatasourceType = "database",
) -> dict[str, Any]:
    """调用 Markdown 同步模型并解析结构化 RequirementSpec。"""

    settings = Settings.from_env()
    result = create_chat_model(settings).invoke(
        _sync_prompt(
            artifact_name=artifact_name,
            structured_document=structured_document,
            edited_markdown=edited_markdown,
            datasource_type=datasource_type,
        )
    )
    content = getattr(result, "content", result)
    if isinstance(content, list):
        text = "\n".join(
            str(item.get("text", item)) if isinstance(item, dict) else str(item)
            for item in content
        )
    else:
        text = content if isinstance(content, str) else str(content)
    synced = extract_json_object(text)
    if not isinstance(synced, dict):
        raise ValueError(f"Failed to synchronize edited {artifact_name} Markdown")
    return synced


def sync_requirement_spec_from_markdown(
    existing_spec: dict[str, Any],
    edited_markdown: str,
    datasource_type: DatasourceType = "database",
) -> dict[str, Any]:
    """同步用户编辑的 RequirementSpec Markdown，并保留隐藏结构字段。"""

    synced = _invoke_sync_model(
        artifact_name="RequirementSpec",
        structured_document=existing_spec,
        edited_markdown=edited_markdown,
        datasource_type=datasource_type,
    )
    request = str(
        synced.get("source_request")
        or synced.get("summary")
        or existing_spec.get("source_request")
        or ""
    )
    normalized = create_requirement_spec(
        request,
        agent_note="synchronized from user-edited RequirementSpec Markdown",
        agent_spec=synced,
        existing_spec=existing_spec,
        authoritative_agent_spec=True,
        datasource_type=datasource_type,
    )
    for key in ("analyzed_by", "analysis_source"):
        if key in existing_spec:
            normalized[key] = existing_spec[key]
    normalized["markdown_sync"] = {
        "status": "synchronized",
        "source": "user_edited_markdown",
    }
    return normalized


def sync_project_plan_from_markdown(
    existing_plan: dict[str, Any],
    requirement_spec: dict[str, Any],
    edited_markdown: str,
    datasource_type: DatasourceType = "database",
) -> dict[str, Any]:
    """同步 ProjectPlan/TechnicalPlan Markdown，并保留实体设计边界。"""

    is_technical_plan = existing_plan.get("artifact_type") == TECHNICAL_PLAN_ARTIFACT_TYPE
    synced = _invoke_sync_model(
        artifact_name="TechnicalPlan" if is_technical_plan else "ProjectPlan",
        structured_document=existing_plan,
        edited_markdown=edited_markdown,
        datasource_type=datasource_type,
    )
    normalized = (
        create_technical_plan(
            requirement_spec,
            agent_plan={
                key: synced.get(key)
                for key in (
                    "architecture",
                    "entities",
                    "api_contracts",
                    "pages",
                )
            },
            datasource_type=datasource_type,
        )
        if is_technical_plan
        else create_project_plan(
            requirement_spec,
            agent_note="synchronized from user-edited ProjectPlan Markdown",
            planning_source="user_edited_markdown",
            agent_plan=synced,
            authoritative_agent_plan=True,
            datasource_type=datasource_type,
        )
    )
    if not is_technical_plan and isinstance(synced.get("app"), dict):
        normalized["app"] = synced["app"]
    errors = validate_api_contract_consistency(normalized)
    if not is_technical_plan:
        errors.extend(validate_project_plan_datasource_policy(normalized))
    if errors:
        raise ValueError("编辑后的项目计划存在不一致：" + "; ".join(errors))
    if not is_technical_plan:
        normalized["markdown_sync"] = {
            "status": "synchronized",
            "source": "user_edited_markdown",
        }
    return normalized


def sync_product_plan_from_markdown(
    existing_plan: dict[str, Any],
    requirement_spec: dict[str, Any],
    edited_markdown: str,
) -> dict[str, Any]:
    """同步产品编辑后的 ProductPlan Markdown，并恢复稳定页面与操作结构。"""

    synced = _invoke_sync_model(
        artifact_name="ProductPlan",
        structured_document=existing_plan,
        edited_markdown=edited_markdown,
    )
    normalized = create_product_plan(
        requirement_spec,
        agent_plan=synced,
        existing_plan=existing_plan,
    )
    errors = validate_product_plan(normalized, requirement_spec)
    if errors:
        raise ValueError("编辑后的产品规划存在不一致：" + "；".join(errors))
    normalized["markdown_sync"] = {
        "status": "synchronized",
        "source": "user_edited_markdown",
    }
    return normalized
