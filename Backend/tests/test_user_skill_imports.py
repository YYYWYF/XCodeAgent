from __future__ import annotations

import base64
import io
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.services import user_skill_imports


class UserSkillImportTests(unittest.TestCase):
    def test_imports_root_skill_and_preserves_resources(self) -> None:
        archive = self._archive(
            {
                "SKILL.md": self._skill("weather-query"),
                "scripts/run.py": "print('ok')\n",
                ".DS_Store": "ignored",
                "__MACOSX/._SKILL.md": "ignored",
            }
        )
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / "skills"
            result = user_skill_imports.import_user_skill_archive(
                "weather.zip",
                base64.b64encode(archive).decode("ascii"),
                root=root,
            )

            self.assertEqual(result.imported.name, "weather-query")
            self.assertEqual(result.imported.relative_path, "weather-query/SKILL.md")
            self.assertEqual(
                (root / "weather-query" / "scripts" / "run.py").read_text(),
                "print('ok')\n",
            )
            self.assertFalse((root / "weather-query" / ".DS_Store").exists())

    def test_imports_single_wrapper_and_multiline_description(self) -> None:
        content = (
            "---\nname: wrapped_skill\ndescription: >\n"
            "  Search weather\n  safely.\nversion: '1.0'\n---\n# Body\n"
        )
        archive = self._archive(
            {"bundle/SKILL.md": content, "bundle/references/guide.md": "Guide"}
        )
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / "skills"
            result = user_skill_imports.import_user_skill_archive(
                "wrapped.zip", self._base64(archive), root=root
            )

            self.assertEqual(result.imported.description, "Search weather safely.")
            self.assertEqual(result.imported.version, "1.0")
            self.assertTrue(
                (root / "wrapped_skill" / "references" / "guide.md").is_file()
            )

    def test_uses_environment_specific_resolved_root(self) -> None:
        archive = self._archive({"SKILL.md": self._skill("environment-skill")})
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / ".xcodeagent_uat" / "skills"
            with patch.object(
                user_skill_imports, "resolve_user_skills_root", return_value=root
            ):
                user_skill_imports.import_user_skill_archive(
                    "environment.zip", self._base64(archive)
                )
            self.assertTrue((root / "environment-skill" / "SKILL.md").is_file())

    def test_rejects_missing_multiple_or_deep_skill_documents(self) -> None:
        cases = {
            "missing": {"README.md": "missing"},
            "multiple": {
                "SKILL.md": self._skill("one"),
                "nested/SKILL.md": self._skill("two"),
            },
            "deep": {"one/two/SKILL.md": self._skill("deep")},
        }
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / "skills"
            for case, files in cases.items():
                with self.subTest(case=case), self.assertRaises(
                    user_skill_imports.SkillArchiveFormatError
                ):
                    user_skill_imports.import_user_skill_archive(
                        f"{case}.zip", self._base64(self._archive(files)), root=root
                    )

    def test_rejects_traversal_absolute_backslash_and_case_conflicts(self) -> None:
        cases = {
            "traversal": {
                "SKILL.md": self._skill("unsafe"),
                "../outside.txt": "bad",
            },
            "absolute": {
                "SKILL.md": self._skill("unsafe"),
                "/outside.txt": "bad",
            },
            "backslash": {
                "SKILL.md": self._skill("unsafe"),
                "scripts\\run.py": "bad",
            },
            "case_conflict": {
                "SKILL.md": self._skill("unsafe"),
                "Guide.md": "one",
                "guide.md": "two",
            },
        }
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / "skills"
            for case, files in cases.items():
                with self.subTest(case=case), self.assertRaises(
                    user_skill_imports.SkillArchivePathError
                ):
                    user_skill_imports.import_user_skill_archive(
                        f"{case}.zip", self._base64(self._archive(files)), root=root
                    )

    def test_rejects_symlink_and_existing_skill(self) -> None:
        symlink = zipfile.ZipInfo("linked")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as package:
            package.writestr("SKILL.md", self._skill("linked_skill"))
            package.writestr(symlink, "target")
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / "skills"
            with self.assertRaises(user_skill_imports.SkillArchivePathError):
                user_skill_imports.import_user_skill_archive(
                    "linked.zip", self._base64(archive_buffer.getvalue()), root=root
                )

            existing = root / "duplicate"
            existing.mkdir(parents=True)
            with self.assertRaises(user_skill_imports.SkillImportConflictError):
                user_skill_imports.import_user_skill_archive(
                    "duplicate.zip",
                    self._base64(
                        self._archive({"SKILL.md": self._skill("duplicate")})
                    ),
                    root=root,
                )

    def test_enforces_file_count_and_expanded_size_limits(self) -> None:
        many_files = {"SKILL.md": self._skill("many")}
        many_files.update({f"assets/{index}.txt": "x" for index in range(3)})
        large_archive = self._archive(
            {"SKILL.md": self._skill("large"), "assets/data.bin": b"12345"}
        )
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / "skills"
            with patch.object(user_skill_imports, "MAX_ARCHIVE_FILES", 2):
                with self.assertRaises(user_skill_imports.SkillArchiveSizeError):
                    user_skill_imports.import_user_skill_archive(
                        "many.zip", self._base64(self._archive(many_files)), root=root
                    )
            with patch.object(user_skill_imports, "MAX_EXTRACTED_BYTES", 4):
                with self.assertRaises(user_skill_imports.SkillArchiveSizeError):
                    user_skill_imports.import_user_skill_archive(
                        "large.zip", self._base64(large_archive), root=root
                    )

    def test_rejects_invalid_base64_and_cleans_staging_after_write_failure(self) -> None:
        archive = self._archive({"SKILL.md": self._skill("cleanup")})
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / "skills"
            with self.assertRaises(user_skill_imports.SkillArchiveFormatError):
                user_skill_imports.import_user_skill_archive(
                    "invalid.zip", "not base64", root=root
                )
            with patch.object(os, "rename", side_effect=OSError("write failed")):
                with self.assertRaises(
                    user_skill_imports.SkillImportFilesystemError
                ):
                    user_skill_imports.import_user_skill_archive(
                        "cleanup.zip", self._base64(archive), root=root
                    )
            self.assertEqual(list(root.iterdir()), [])

    def test_rejects_encrypted_and_crc_damaged_archives(self) -> None:
        encrypted = bytearray(
            self._archive({"SKILL.md": self._skill("encrypted")})
        )
        local_header = encrypted.find(b"PK\x03\x04")
        central_header = encrypted.find(b"PK\x01\x02")
        encrypted[local_header + 6] |= 0x01
        encrypted[central_header + 8] |= 0x01

        damaged = bytearray(
            self._archive(
                {
                    "SKILL.md": self._skill("damaged"),
                    "assets/data.txt": "integrity-check",
                },
                compression=zipfile.ZIP_STORED,
            )
        )
        asset_header = damaged.find(b"PK\x03\x04", damaged.find(b"PK\x03\x04") + 1)
        name_length = int.from_bytes(damaged[asset_header + 26 : asset_header + 28], "little")
        extra_length = int.from_bytes(damaged[asset_header + 28 : asset_header + 30], "little")
        data_offset = asset_header + 30 + name_length + extra_length
        damaged[data_offset] ^= 0x01

        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / "skills"
            with self.assertRaises(user_skill_imports.SkillArchiveFormatError):
                user_skill_imports.import_user_skill_archive(
                    "encrypted.zip", self._base64(bytes(encrypted)), root=root
                )
            with self.assertRaises(user_skill_imports.SkillArchiveFormatError):
                user_skill_imports.import_user_skill_archive(
                    "damaged.zip", self._base64(bytes(damaged)), root=root
                )
            self.assertEqual(list(root.iterdir()), [])

    @staticmethod
    def _skill(name: str) -> str:
        return f"---\nname: {name}\ndescription: Test skill\n---\n# Skill\n"

    @staticmethod
    def _base64(archive: bytes) -> str:
        return base64.b64encode(archive).decode("ascii")

    @staticmethod
    def _archive(
        files: dict[str, str | bytes],
        *,
        compression: int = zipfile.ZIP_DEFLATED,
    ) -> bytes:
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=compression) as package:
            for path, content in files.items():
                package.writestr(path, content)
        return archive.getvalue()


if __name__ == "__main__":
    unittest.main()
