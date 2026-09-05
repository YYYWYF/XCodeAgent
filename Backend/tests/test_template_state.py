from __future__ import annotations

import unittest

from app.services.template_state import effective_capabilities, has_capability, validate_template_state
from app.services.workspace_bootstrap.models import TemplateStateError


def _state() -> dict[str, object]:
    """构造当前 Engine 冻结四字段 TemplateState fixture。"""

    return {
        "templateRevision": "2026.09.04.1",
        "managedFiles": {"frontend/package.json": "{}"},
        "requested": {"login": {"enabled": True}},
        "effective": {"login": {"enabled": True}},
    }


class TemplateStateTests(unittest.TestCase):
    """验证 XCodeAgent 不宽松解释 Engine State。"""

    def test_reads_effective_capabilities(self) -> None:
        """确认有效能力仅来自 Engine 的 effective 字段。"""

        state = validate_template_state(_state())
        self.assertTrue(has_capability(state, "login"))
        self.assertEqual(sorted(effective_capabilities(state)), ["login"])

    def test_rejects_unknown_or_invalid_fields(self) -> None:
        """确认旧字段或不完整 capability 不会被兼容接受。"""

        with self.assertRaises(TemplateStateError):
            validate_template_state({**_state(), "migrations": []})
        invalid = _state()
        invalid["effective"] = {"login": {"enabled": False}}
        with self.assertRaises(TemplateStateError):
            effective_capabilities(invalid)
