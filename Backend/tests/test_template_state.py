from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from app.services.template_state import (
    assert_template_context_matches,
    effective_capabilities,
    has_capability,
    load_template_state,
    requested_capabilities,
    template_context,
    validate_template_context,
    validate_template_state,
)
from app.services.workspace_bootstrap.models import TemplateStateError


FIXTURE_ROOT = Path(__file__).with_name("fixtures") / "template_state"


def _fixture(name: str) -> dict[str, object]:
    """读取从当前 Engine 输出结构冻结的 TemplateState fixture。"""

    value = json.loads((FIXTURE_ROOT / f"{name}.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("TemplateState fixture 必须是 JSON 对象。")
    return value


class TemplateStateTests(unittest.TestCase):
    """验证 XCodeAgent 不宽松解释 Engine State。"""

    def test_accepts_frozen_capability_fixtures(self) -> None:
        """确认无能力、仅登录和权限依赖补全三类 Engine State 均可消费。"""

        cases = {
            "none": ([], []),
            "login-only": (["login"], ["login"]),
            "authorization-effective": (["authorization"], ["authorization", "login"]),
        }
        for name, (requested_ids, effective_ids) in cases.items():
            with self.subTest(name=name):
                state = validate_template_state(_fixture(name))
                self.assertEqual(sorted(requested_capabilities(state)), requested_ids)
                self.assertEqual(sorted(effective_capabilities(state)), effective_ids)

    def test_reads_capabilities_only_from_their_engine_fields(self) -> None:
        """确认 requested 不会被补全，而 capability 查询只读取 effective。"""

        state = validate_template_state(_fixture("authorization-effective"))
        self.assertTrue(has_capability(state, "login"))
        self.assertTrue(has_capability(state, "authorization"))
        self.assertEqual(sorted(requested_capabilities(state)), ["authorization"])

    def test_rejects_unknown_top_level_or_invalid_nested_fields(self) -> None:
        """确认旧字段、宽松布尔和非 Core capability 结构不会被接受。"""

        with self.assertRaises(TemplateStateError):
            validate_template_state({**_fixture("none"), "migrations": []})
        missing = _fixture("none")
        missing.pop("effective")
        with self.assertRaises(TemplateStateError):
            validate_template_state(missing)
        invalid_values = (
            {"login": {"enabled": False}},
            {"login": {"enabled": 1}},
            {"": {"enabled": True}},
            [],
        )
        for invalid in invalid_values:
            with self.subTest(invalid=invalid), self.assertRaises(TemplateStateError):
                state = _fixture("none")
                state["effective"] = invalid
                validate_template_state(state)

        invalid_requested = _fixture("none")
        invalid_requested["requested"] = {"login": {"enabled": False}}
        with self.assertRaises(TemplateStateError):
            validate_template_state(invalid_requested)

    def test_rejects_invalid_revision_and_legacy_managed_files(self) -> None:
        """确认 revision 不宽松，且 V1 managedFiles 不能混入当前唯一 State。"""

        invalid_states = []
        for revision in ("", "   ", 1):
            state = _fixture("none")
            state["templateRevision"] = revision
            invalid_states.append(state)
        for managed_files in ([], {"": "content"}, {"frontend/package.json": 1}):
            state = _fixture("none")
            state["managedFiles"] = managed_files
            invalid_states.append(state)
        for state in invalid_states:
            with self.subTest(state=state), self.assertRaises(TemplateStateError):
                validate_template_state(state)

    def test_build_context_is_canonical_and_does_not_alias_state(self) -> None:
        """确认 Build 快照排序稳定，且返回值不能反向修改 Engine State。"""

        state = _fixture("authorization-effective")
        state["effective"] = {
            "login": {"enabled": True, "config": {}},
            "authorization": {"enabled": True, "config": {}},
        }
        original = deepcopy(state)
        context = template_context(state)
        self.assertEqual(
            list(context["effective_capabilities"]),
            ["authorization", "login"],
        )
        context["effective_capabilities"]["login"]["enabled"] = False
        self.assertEqual(state, original)

    def test_validates_and_matches_build_template_context(self) -> None:
        """确认相同绑定可复用，revision 或 capability 漂移都会阻断。"""

        state = _fixture("authorization-effective")
        context = template_context(state)
        self.assertEqual(assert_template_context_matches(state, context), context)
        self.assertEqual(validate_template_context(context), context)

        revision_drift = deepcopy(context)
        revision_drift["template_revision"] = "different-revision"
        with self.assertRaisesRegex(TemplateStateError, "不一致"):
            assert_template_context_matches(state, revision_drift)

        capability_drift = deepcopy(context)
        capability_drift["effective_capabilities"].pop("authorization")
        with self.assertRaisesRegex(TemplateStateError, "不一致"):
            assert_template_context_matches(state, capability_drift)

    def test_rejects_noncanonical_build_context(self) -> None:
        """确认 Build 绑定不能指向其他 State 路径或携带扩展字段。"""

        context = template_context(_fixture("login-only"))
        for invalid in (
            {**context, "state_path": ".xcodeagent/other.json"},
            {**context, "legacy_variant": "auth"},
            {**context, "effective_capabilities": {"login": {"enabled": False}}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(TemplateStateError):
                validate_template_context(invalid)

    def test_load_rejects_symlinked_state(self) -> None:
        """确认 Workspace Consumer 不跟随指向外部位置的 TemplateState 符号链接。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external.json"
            external.write_text(json.dumps(_fixture("none")), encoding="utf-8")
            state_path = root / ".xcodeagent/template-state.json"
            state_path.parent.mkdir()
            state_path.symlink_to(external)
            with self.assertRaises(TemplateStateError):
                load_template_state(root)
