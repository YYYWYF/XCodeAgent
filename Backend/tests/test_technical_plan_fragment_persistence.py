"""验证技术规划片段在连接异常、进程退出和输入变化后的当前输入恢复。"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import TestCase
from unittest.mock import patch

from app.agents.main.technical_plan_generation import generate_technical_plan_sections
from app.agents.main.technical_plan_catalog_parts_validation import catalog_registry
from app.services.technical_plan_fragments import TechnicalPlanFragmentStore
from tests.test_technical_plan_modular_generation import modular_fixture, fragment_outputs


class TechnicalPlanFragmentPersistenceTests(TestCase):
    """不恢复旧 checkpoint，新的生成调用只读取精确匹配当前输入的片段。"""

    def setUp(self):
        """为每个用例准备独立受管目录和四类片段输入。"""

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.requirement, self.catalog, self.contracts, self.pages = modular_fixture()
        self.requirement["technical_plan_generation_workspace"] = self.temp.name
        self.outputs = fragment_outputs(self.catalog, self.contracts, self.pages)

    def test_network_failure_reuses_durable_fragments_without_in_memory_progress(self):
        """首模块成功后连接失败，新调用无需携带旧内存或 checkpoint 正文即可继续。"""

        with patch("builtins.input", side_effect=AssertionError("测试不等待人工输入")):
            responses = iter(self.outputs[:2])

            def disconnect(prompt):
                """在两个成功片段之后模拟下一次真实模型调用连接异常。"""

                try:
                    return next(responses)
                except StopIteration:
                    raise ConnectionError("模型连接中断")

            with self.assertRaisesRegex(ConnectionError, "连接中断"):
                generate_technical_plan_sections(self.requirement, invoke=disconnect)
        remaining = list(self.outputs[2:])
        result = generate_technical_plan_sections(self.requirement, invoke=lambda prompt: remaining.pop(0))
        self.assertEqual(remaining, [])
        self.assertEqual(result["api_contracts"], self.contracts)
        self.assertEqual(result["pages"], self.pages)

    def test_process_exit_retains_completed_catalog(self):
        """子进程在第二次模型调用直接退出，父进程仍能复用已同步提交的共享目录。"""

        fixture = Path(self.temp.name) / "inputs.json"
        fixture.write_text(json.dumps({"requirement": self.requirement, "catalog": catalog_registry(self.catalog)}))
        script = '''
import json,os,sys
from app.agents.main.technical_plan_generation import generate_technical_plan_sections
value=json.load(open(sys.argv[1]))
count=0
def invoke(prompt):
    """共享目录返回成功后，在下一次调用强制结束测试子进程。"""
    global count
    count+=1
    if count>1: os._exit(73)
    return json.dumps(value["catalog"])
generate_technical_plan_sections(value["requirement"],invoke=invoke)
'''
        process = subprocess.run([sys.executable, "-c", script, str(fixture)], cwd=Path(__file__).parents[1], capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 73, process.stderr.decode())
        remaining = list(self.outputs[1:])
        result = generate_technical_plan_sections(self.requirement, invoke=lambda prompt: remaining.pop(0))
        self.assertEqual(result["pages"], self.pages)
        self.assertEqual(remaining, [])

    def test_changed_input_replaces_current_directory_and_late_write_is_rejected(self):
        """新输入只保留自身引用，旧运行迟到的写入不能覆盖新输入片段。"""

        store = TechnicalPlanFragmentStore(self.temp.name)
        old, new = "a" * 64, "b" * 64
        store.load(old)
        store.save(old, "catalog", {"entities": [], "contracts": []})
        self.assertEqual(store.load(new), {})
        with self.assertRaisesRegex(ValueError, "输入已改变"):
            store.save(old, "catalog", {})
        with self.assertRaisesRegex(ValueError, "输入已改变"):
            store.replace(old, {"catalog": {}})
        self.assertEqual(store.load(new), {})
        store.clear(old)
        self.assertTrue(store.path.exists())
        store.clear(new)
        self.assertFalse(store.path.exists())

    def test_failed_atomic_replace_preserves_previous_published_fragments(self):
        """目录替换失败保留上次完整版本，不发布半份 JSON 或成功回执。"""

        store = TechnicalPlanFragmentStore(self.temp.name)
        digest = "c" * 64
        store.load(digest)
        store.save(digest, "catalog", {"valid": True})
        with patch("app.services.technical_plan_fragments.os.replace", side_effect=OSError("磁盘暂不可写")):
            with self.assertRaisesRegex(OSError, "暂不可写"):
                store.save(digest, "contract:orders", {"id": "orders"})
        self.assertEqual(store.load(digest), {"catalog": {"valid": True}})

    def test_corrupt_content_is_rejected_without_silent_regeneration(self):
        """片段引用摘要不一致时明确报错，不把损坏内容作为可复用候选。"""

        store = TechnicalPlanFragmentStore(self.temp.name)
        digest = "d" * 64
        store.load(digest)
        store.save(digest, "catalog", {"valid": True})
        reference = json.loads(store.path.read_text())["completed"]["catalog"]
        body = Path(self.temp.name) / ".devagentstudio/artifacts/sha256" / (reference["artifactId"] + ".json")
        body.write_text("{}")
        with self.assertRaisesRegex(ValueError, "校验失败"):
            store.load(digest)
