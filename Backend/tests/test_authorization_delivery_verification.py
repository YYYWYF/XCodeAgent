"""只读交付核验忽略模板来源，仍保留物理表名及安全路径边界。"""

import json
import tempfile
import unittest
from pathlib import Path

from app.services.authorization_projection_sources import PO_FILES, SOURCE_RECEIPT, XML_FILES
from app.services.authorization_workspace_projector import verify_projection
from tests.authorization_delivery_fixture import install_authorization


class AuthorizationDeliveryVerificationTests(unittest.TestCase):
    """无模板来源依赖时，不明确的表引用仍不能通过交付核验。"""

    def setUp(self) -> None:
        """生成七文件并保存安装来源，后续全部核验只读。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        (self.root / ".devagentstudio").mkdir()
        application = {"authorization": {}, "managedDatabase": {"datasourceKey": "12ab34cd56ef", "datasourceId": 1}}
        install_authorization(self.root, application, {})

    def test_source_receipt_is_not_read_by_delivery_verification(self) -> None:
        """缺失、损坏或摘要不一致的来源记录均不参与只读交付核验。"""

        path = self.root / SOURCE_RECEIPT
        original = path.read_bytes()
        mismatch = json.loads(original)
        for source in mismatch["files"].values():
            source["digest"] = "sha256:wrong"
        for value in (None, b"{broken", b"[]", b"null", b'{"files": []}', json.dumps(mismatch).encode()):
            with self.subTest(value=value):
                if value is None:
                    path.unlink()
                else:
                    path.write_bytes(value)
                verify_projection(self.root, "12ab34cd56ef")
                self.assertEqual(path.read_bytes() if path.exists() else None, value)
                path.write_bytes(original)

    def test_ambiguous_or_invalid_mapper_references_remain_rejected(self) -> None:
        """限定名、动态名、间接引用及子查询中的逗号多表均不自动猜测。"""

        relative = XML_FILES[0]
        path = self.root / relative
        for statement in (
            "SELECT id FROM `12ab34cd56ef_role`.other",
            "SELECT id FROM ${table}",
            'SELECT id <include refid="tables"/>',
            "SELECT id FROM 12ab34cd56ef_role WHERE EXISTS "
            "(SELECT id FROM 12ab34cd56ef_role, role_member)",
            "SELECT id FROM",
            "SELECT id FROM role",
            "SELECT id FROM 98ab34cd56ef_role",
        ):
            with self.subTest(statement=statement):
                content = f'<mapper><select id="test">{statement}</select></mapper>'
                path.write_text(content)
                with self.assertRaises(ValueError):
                    verify_projection(self.root, "12ab34cd56ef")
                self.assertEqual(path.read_text(), content)
        path.write_text("<mapper><select></mapper>")
        with self.assertRaisesRegex(ValueError, "XML 无效"):
            verify_projection(self.root, "12ab34cd56ef")

    def test_po_table_name_anchor_must_be_unique(self) -> None:
        """四个 PO 的重复 TableName 锚点仍被拒绝，且核验不改写文件。"""

        for relative in PO_FILES:
            path = self.root / relative
            original = path.read_bytes()
            path.write_bytes(original + original)
            try:
                with self.assertRaisesRegex(ValueError, "TableName"):
                    verify_projection(self.root, "12ab34cd56ef")
                self.assertEqual(path.read_bytes(), original + original)
            finally:
                path.write_bytes(original)

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
