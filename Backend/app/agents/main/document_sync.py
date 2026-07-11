from __future__ import annotations

import json
from typing import Any

from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec
from app.utils.model_output import extract_json_object


def _sync_prompt(
    *,
    artifact_name: str,
    structured_document: dict[str, Any],
    edited_markdown: str,
) -> str:
    return (
        f"You synchronize a user-edited {artifact_name} Markdown document back into its internal JSON.\n"
        "This is a document-sync boundary. Do not call tools, do not generate code, and return only "
        "one complete JSON object without markdown fences. Treat the edited Markdown as authoritative "
        "for user-visible business content. Preserve internal ids, schema details, dependencies, and "
        "metadata that the Markdown does not represent. Apply additions, edits, and removals expressed "
        "by the Markdown, but do not invent unrelated fields or discard hidden structured details.\n\n"
        f"Current internal JSON:\n{json.dumps(structured_document, ensure_ascii=False)}\n\n"
        f"User-edited Markdown:\n{edited_markdown}"
    )


def _invoke_sync_model(
    *,
    artifact_name: str,
    structured_document: dict[str, Any],
    edited_markdown: str,
) -> dict[str, Any]:
    settings = Settings.from_env()
    result = create_chat_model(settings).invoke(
        _sync_prompt(
            artifact_name=artifact_name,
            structured_document=structured_document,
            edited_markdown=edited_markdown,
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
) -> dict[str, Any]:
    synced = _invoke_sync_model(
        artifact_name="RequirementSpec",
        structured_document=existing_spec,
        edited_markdown=edited_markdown,
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
        authoritative_agent_spec=True,
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
) -> dict[str, Any]:
    synced = _invoke_sync_model(
        artifact_name="ProjectPlan",
        structured_document=existing_plan,
        edited_markdown=edited_markdown,
    )
    normalized = create_project_plan(
        requirement_spec,
        agent_note="synchronized from user-edited ProjectPlan Markdown",
        planning_source="user_edited_markdown",
        agent_plan=synced,
        authoritative_agent_plan=True,
    )
    if isinstance(synced.get("app"), dict):
        normalized["app"] = synced["app"]
    for key in (
        "page_detail_plans",
        "data_source_detail_plans",
        "detail_confirmation_summary",
        "page_detail_confirmation_summary",
        "data_source_detail_confirmation_summary",
    ):
        value = synced.get(key, existing_plan.get(key))
        if value is not None:
            normalized[key] = value
    errors = validate_api_contract_consistency(normalized)
    if errors:
        raise ValueError("Edited ProjectPlan is inconsistent: " + "; ".join(errors))
    normalized["markdown_sync"] = {
        "status": "synchronized",
        "source": "user_edited_markdown",
    }
    return normalized
