"""T2.2 工作区外部能力必须具有平台证据，不以源码扫描线索冒充能力完成。"""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from app.services.build_task_reuse import resolve_reuse_facts
from app.services.planning_frozen import freeze_json
from app.services.template_state import load_template_state, template_context
from tests.test_build_task_reuse import _inputs


def _template_state_context(workspace: Path) -> dict:
    """写入并读取真实 V2 TemplateState，生成 Build 允许冻结的唯一模板证据。"""

    state_path = workspace / ".devagentstudio/template-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{"schemaVersion":2,"templateRevision":"template-r1","requested":{},"effective":{},"appliedAdditions":{}}',
        encoding="utf-8",
    )
    return template_context(load_template_state(workspace))


def _ready_template(workspace: Path) -> dict:
    """提供 Adapter 测试所需的原始 V2 State，调用方必须自行冻结为 BuildContext。"""

    _template_state_context(workspace)
    (workspace / ".devagentstudio/application.json").write_text(
        '{"schemaVersion":6,"configRevision":1,"auth":{"enable":false},"authorization":{"enabled":false,"initialAdministratorSubjects":[]}}',
        encoding="utf-8",
    )
    return load_template_state(workspace)


class BuildTaskReuseWorkspaceTests(unittest.TestCase):
    def test_real_template_evidence_provides_external_shell_without_tasks(self) -> None:
        """真实模板门禁满足 shell 前置能力，计算过程不造 Task、不写任何文件。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            inputs = _inputs()
            context = _template_state_context(workspace)
            inputs["template_state_context"] = context
            inputs["build_context"]["template_context"] = context
            before_files = {str(path.relative_to(workspace)): (path.read_bytes(), path.stat().st_mtime_ns) for path in workspace.rglob("*") if path.is_file()}
            before_inputs = deepcopy(inputs)

            facts = resolve_reuse_facts(**inputs)

            self.assertEqual(facts.issues, ())
            self.assertEqual(facts.retained_task_ids_by_unit["frontend:shell"], ())
            self.assertEqual(facts.reusable_capabilities_by_unit["frontend:shell"], {})
            self.assertEqual(len(facts.external_capabilities), 1)
            capability = facts.external_capabilities[0]
            self.assertEqual((capability.unit_id, capability.capability_id), ("frontend:shell", "frontend.shell.ready"))
            self.assertEqual(capability.workspace_revision, "snapshot-1")
            self.assertEqual(capability.source, "template_state")
            self.assertEqual(capability.source_refs["state_path"], ".devagentstudio/template-state.json")
            self.assertEqual(len(capability.source_refs["template_state_sha256"]), 64)
            self.assertEqual(resolve_reuse_facts(**freeze_json(inputs)), facts)
            self.assertEqual(inputs, before_inputs)
            self.assertEqual({str(path.relative_to(workspace)): (path.read_bytes(), path.stat().st_mtime_ns) for path in workspace.rglob("*") if path.is_file()}, before_files)
            with self.assertRaises(TypeError):
                capability.source_refs["manifest_path"] = "changed"

    def test_scanned_file_names_and_api_clients_do_not_prove_capability(self) -> None:
        """扫描到 package/App/HTTP client 或模型声称检查成功，都不能证明 workspace 能力。"""

        inputs = _inputs()
        inputs["workspace_snapshot"].update({
            "entrypoints": [{"path": "frontend/src/App.tsx"}],
            "high_value_files": [{"path": "frontend/package.json"}],
            "frontend": {"api_clients": [{"path": "frontend/src/apis/orders.ts", "kind": "http_client"}]},
            "workspace_analysis": {"inspection_status": "completed", "entry_files": ["frontend/src/App.tsx"]},
        })
        facts = resolve_reuse_facts(**inputs)
        self.assertEqual(facts.external_capabilities, ())
        self.assertEqual(facts.reusable_capabilities_by_unit["frontend:api-client"], {})

    def test_failed_template_gate_does_not_provide_external_capability(self) -> None:
        """真实模板门禁失败属于前置输入问题，不能通过生成 shell Task 修复。"""

        with tempfile.TemporaryDirectory() as directory:
            inputs = _inputs()
            inputs["template_state_context"] = {}
            facts = resolve_reuse_facts(**inputs)
            self.assertEqual(facts.external_capabilities, ())
            self.assertEqual(facts.issues[0].code, "WORKSPACE_TEMPLATE_NOT_READY")
            self.assertEqual(facts.issues[0].category, "input")
            self.assertFalse(facts.issues[0].retryable)

    def test_external_evidence_requires_snapshot_identity_and_matching_variant(self) -> None:
        """缺失检查身份或当前模板变体不匹配时，不把无归属证据发布为外部能力。"""

        with tempfile.TemporaryDirectory() as directory:
            context = _template_state_context(Path(directory))
            for bad_input, expected_code in (
                ({"workspace_snapshot": {}}, "WORKSPACE_EVIDENCE_IDENTITY_MISSING"),
                ({"build_context": {"template_context": {}}}, "WORKSPACE_TEMPLATE_CONTEXT_MISMATCH"),
            ):
                with self.subTest(code=expected_code):
                    inputs = {**_inputs(), "template_state_context": context, **bad_input}
                    if "build_context" not in bad_input:
                        inputs["build_context"] = {**inputs["build_context"], "template_context": context}
                    facts = resolve_reuse_facts(**inputs)
                    self.assertEqual(facts.external_capabilities, ())
                    self.assertEqual(facts.issues[0].code, expected_code)


if __name__ == "__main__":
    unittest.main()
