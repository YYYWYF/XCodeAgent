"""需求分析模型输出的 JSON 恢复与内容重试：引号损坏自愈，坏输出重调一次。"""

import json

import pytest

from app.agents.main import requirements_analyzer
from app.agents.main.requirements_analyzer import (
    _is_malformed_spec_json_error,
    _recover_requirement_spec_json,
    analyze_requirements_with_chat_model,
)


def _complete_spec_dict() -> dict:
    return {
        "app_info": {"name": "测试应用", "summary": "摘要"},
        "user_roles": [{"id": "r1", "name": "管理员", "description": ""}],
        "feature_modules": [
            {"id": "m1", "name": "模块", "description": "", "priority": "must"}
        ],
        "pages": [{"pageId": "p1", "name": "首页", "path": "/"}],
        "entities": [],
        "business_flows": [{"id": "f1", "name": "流程", "description": "", "steps": ["开始"]}],
    }


def test_recovery_leaves_complete_spec_untouched():
    spec = _complete_spec_dict()
    assert _recover_requirement_spec_json("任何文本", spec) is spec


def test_recovery_ignores_text_without_contract_markers():
    assert _recover_requirement_spec_json("模型只回了一句话", None) is None


def test_recovery_repairs_unescaped_quotes_in_spec_json():
    broken = (
        '{"app_info": {"name": "武汉"分行"项目管理", "summary": "摘要"}, '
        '"user_roles": [], "feature_modules": [], "pages": [], '
        '"entities": [], "business_flows": []}'
    )
    recovered = _recover_requirement_spec_json(broken, None)
    assert isinstance(recovered, dict)
    assert recovered["app_info"]["name"] == '武汉"分行"项目管理'


def test_recovery_replaces_nested_object_fallback_with_repaired_root():
    # 外层坏了时 extract_json_object 会捞到内层子对象（缺顶层字段）；
    # 恢复层应用修复后的完整根对象替换它。
    nested_only = {"pageId": "p1", "name": "首页"}
    broken = (
        '{"app_info": {"name": "测试"引用"应用", "summary": "s"}, '
        '"user_roles": [], "feature_modules": [], "pages": []}'
    )
    recovered = _recover_requirement_spec_json(broken, nested_only)
    assert isinstance(recovered, dict)
    assert "app_info" in recovered


def test_malformed_error_classification():
    assert _is_malformed_spec_json_error(ValueError("需求 AI 未返回完整 RequirementSpec JSON。"))
    assert _is_malformed_spec_json_error(
        ValueError("需求 AI 返回的新 RequirementSpec 缺少完整字段：pages")
    )
    assert not _is_malformed_spec_json_error(ValueError("ProductPlan 校验失败"))


def test_malformed_json_retries_model_call_once(monkeypatch):
    calls = {"count": 0}

    def fake_invoke(*args, **kwargs):
        calls["count"] += 1
        return {"messages": []}  # 无 tool_calls 且无 content → 未返回完整 JSON

    monkeypatch.setattr(requirements_analyzer, "_invoke_live_chat_model", fake_invoke)

    with pytest.raises(ValueError, match="未返回完整 RequirementSpec JSON"):
        analyze_requirements_with_chat_model("做一个项目管理应用")
    assert calls["count"] == 2  # 首次 + 一次内容重试


def test_valid_json_does_not_retry(monkeypatch):
    from langchain_core.messages import AIMessage

    calls = {"count": 0}

    def fake_invoke(*args, **kwargs):
        calls["count"] += 1
        return {"messages": [AIMessage(content=json.dumps(_complete_spec_dict(), ensure_ascii=False))]}

    monkeypatch.setattr(requirements_analyzer, "_invoke_live_chat_model", fake_invoke)
    # 权限事实提取是独立的模型调用，此处与本次 JSON 恢复验证无关，mock 为空事实。
    monkeypatch.setattr(
        requirements_analyzer,
        "_extract_authorization_facts",
        lambda *args, **kwargs: {},
    )

    result = analyze_requirements_with_chat_model("做一个项目管理应用")
    assert result["requirement_spec"]["app_info"]["name"] == "测试应用"
    assert calls["count"] == 1
