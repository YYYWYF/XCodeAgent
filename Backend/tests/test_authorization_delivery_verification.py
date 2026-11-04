"""只读投影核验的异常来源和不明确 SQL 边界。"""

import json
import tempfile
import unittest
from pathlib import Path

from app.services.authorization_projection_sources import SOURCE_RECEIPT, XML_FILES, source_digest
from app.services.authorization_workspace_projector import verify_projection
from tests.authorization_delivery_fixture import install_authorization


class AuthorizationDeliveryVerificationTests(unittest.TestCase):
    """即使模板来源可信，不明确的表引用也不能通过交付核验。"""

    def setUp(self) -> None:
        """生成七文件并保存安装来源，后续全部核验只读。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        (self.root / ".devagentstudio").mkdir()
        application = {"authorization": {}, "managedDatabase": {"datasourceKey": "12ab34cd56ef", "datasourceId": 1}}
        install_authorization(self.root, application, {})

    def test_malformed_sources_are_reported_as_delivery_errors(self) -> None:
        """非对象来源记录和非字典文件映射均返回可由完成节点处理的 ValueError。"""

        path = self.root / SOURCE_RECEIPT
        for value in ([], None, {"files": []}, {"files": None}):
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "来源摘要损坏"):
                verify_projection(self.root, "12ab34cd56ef")

    def test_ambiguous_table_references_fail_even_for_trusted_template_bytes(self) -> None:
        """限定名、动态名、间接引用及子查询中的逗号多表均不自动猜测。"""

        relative = XML_FILES[0]
        path, receipt = self.root / relative, self.root / SOURCE_RECEIPT
        record = json.loads(receipt.read_text())
        for statement in (
            "SELECT id FROM `12ab34cd56ef_role`.other",
            "SELECT id FROM ${table}",
            'SELECT id <include refid="tables"/>',
            "SELECT id FROM 12ab34cd56ef_role WHERE EXISTS "
            "(SELECT id FROM 12ab34cd56ef_role, role_member)",
            "SELECT id FROM",
        ):
            with self.subTest(statement=statement):
                content = f'<mapper><select id="test">{statement}</select></mapper>'
                path.write_text(content)
                record["files"][relative]["digest"] = source_digest(content.replace("12ab34cd56ef_", "").encode())
                receipt.write_text(json.dumps(record))
                with self.assertRaises(ValueError):
                    verify_projection(self.root, "12ab34cd56ef")
                self.assertEqual(path.read_text(), content)

    def test_unsafe_managed_path_is_rejected_without_following_symlink(self) -> None:
        """只读验证与模板物化共用路径约束，不信任软链接受管文件。"""

        path = self.root / XML_FILES[0]
        original = path.read_bytes()
        target = self.root / "other.xml"
        target.write_bytes(original)
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            verify_projection(self.root, "12ab34cd56ef")
        self.assertEqual(target.read_bytes(), original)
