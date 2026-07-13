from __future__ import annotations

import hashlib
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import user_skill_documents, user_skills


class UserSkillDocumentTests(unittest.TestCase):
    def test_create_uses_frontmatter_name_and_creates_missing_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / "skills"
            content = self._new_skill_content("weather_query")

            document = user_skill_documents.create_user_skill_document(
                content,
                root=root,
            )

            skill_file = root / "weather_query" / "SKILL.md"
            self.assertEqual(skill_file.read_text(encoding="utf-8"), content)
            self.assertEqual(document.name, "weather_query")
            self.assertEqual(document.relative_path, "weather_query/SKILL.md")
            self.assertEqual(
                document.revision,
                hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )

    def test_create_uses_environment_specific_resolved_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / ".xcodeagent_st" / "skills"
            with patch.object(
                user_skill_documents,
                "resolve_user_skills_root",
                return_value=root,
            ):
                user_skill_documents.create_user_skill_document(
                    self._new_skill_content("environment_skill")
                )

            self.assertTrue((root / "environment_skill" / "SKILL.md").is_file())

    def test_create_rejects_invalid_frontmatter_and_names(self) -> None:
        invalid_contents = {
            "missing_opening": (
                "name: missing_opening\n"
                "description: Missing opening delimiter\n"
                "---\n"
            ),
            "missing_description": "---\nname: missing_description\n---\n",
            "yaml_end_marker": (
                "---\nname: yaml_end\ndescription: Wrong delimiter\n...\n"
            ),
            "hyphenated_name": self._new_skill_content("weather-query"),
            "uppercase_name": self._new_skill_content("WeatherQuery"),
        }
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / "skills"
            for case, content in invalid_contents.items():
                with self.subTest(case=case):
                    with self.assertRaises(
                        (user_skills.SkillFrontmatterError, user_skill_documents.SkillNameError)
                    ):
                        user_skill_documents.create_user_skill_document(
                            content,
                            root=root,
                        )

            self.assertFalse(root.exists())

    def test_create_rejects_invalid_utf8_and_oversized_content(self) -> None:
        oversized_content = self._new_skill_content("large_skill") + (
            "x" * user_skill_documents.MAX_SKILL_CONTENT_BYTES
        )
        invalid_utf8 = self._new_skill_content("invalid_utf8") + "\ud800"

        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / "skills"
            with self.assertRaises(user_skills.SkillFrontmatterError):
                user_skill_documents.create_user_skill_document(
                    invalid_utf8,
                    root=root,
                )
            with self.assertRaises(user_skill_documents.SkillContentTooLargeError):
                user_skill_documents.create_user_skill_document(
                    oversized_content,
                    root=root,
                )

            self.assertFalse(root.exists())

    def test_create_rejects_existing_directory_or_skill_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "legacy")

            with self.assertRaises(user_skill_documents.SkillAlreadyExistsError):
                user_skill_documents.create_user_skill_document(
                    self._new_skill_content("sample"),
                    root=root,
                )

            reserved = root / "reserved_skill"
            reserved.mkdir()
            with self.assertRaises(user_skill_documents.SkillAlreadyExistsError):
                user_skill_documents.create_user_skill_document(
                    self._new_skill_content("reserved_skill"),
                    root=root,
                )

            self.assertFalse((root / "sample").exists())
            self.assertEqual(list(reserved.iterdir()), [])

    def test_create_rejects_symlink_root_and_cleans_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            parent = Path(temporary_root)
            target = parent / "target"
            target.mkdir()
            linked_root = parent / "skills"
            try:
                linked_root.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")

            with self.assertRaises(user_skill_documents.SkillPathError):
                user_skill_documents.create_user_skill_document(
                    self._new_skill_content("linked_skill"),
                    root=linked_root,
                )
            self.assertEqual(list(target.iterdir()), [])

            root = parent / "safe-skills"
            with patch("app.services.user_skill_documents.os.open", side_effect=OSError):
                with self.assertRaises(
                    user_skill_documents.SkillPathError
                ) as error_context:
                    user_skill_documents.create_user_skill_document(
                        self._new_skill_content("failed_skill"),
                        root=root,
                    )
            self.assertFalse((root / "failed_skill").exists())
            self.assertNotIn(str(root), str(error_context.exception))

    def test_delete_removes_skill_directory_and_supporting_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            skill_directory = root / "sample"
            self._write_skill(skill_directory)
            references = skill_directory / "references"
            references.mkdir()
            (references / "guide.md").write_text("Guide", encoding="utf-8")

            deleted = user_skill_documents.delete_user_skill(
                "sample/SKILL.md",
                root=root,
            )

            self.assertEqual(deleted.name, "sample")
            self.assertEqual(deleted.relative_path, "sample/SKILL.md")
            self.assertFalse(skill_directory.exists())
            self.assertTrue(root.is_dir())

    def test_delete_rejects_symlink_and_sanitizes_delete_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            target = root / "target"
            self._write_skill(target)
            link = root / "linked"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")

            with self.assertRaises(user_skill_documents.SkillPathError):
                user_skill_documents.delete_user_skill(
                    "linked/SKILL.md",
                    root=root,
                )
            self.assertTrue((target / "SKILL.md").is_file())

            with patch(
                "app.services.user_skill_documents.shutil.rmtree",
                side_effect=OSError,
            ):
                with self.assertRaises(
                    user_skill_documents.SkillPathError
                ) as error_context:
                    user_skill_documents.delete_user_skill(
                        "target/SKILL.md",
                        root=root,
                    )
            self.assertTrue(target.is_dir())
            self.assertNotIn(str(root), str(error_context.exception))

    def test_reads_complete_content_and_sha256_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "sample")
            skill_file = root / "sample" / "SKILL.md"
            expected_content = skill_file.read_text(encoding="utf-8")

            document = user_skill_documents.read_user_skill_document(
                "sample/SKILL.md",
                root=root,
            )

        self.assertEqual(document.name, "sample")
        self.assertEqual(document.content, expected_content)
        self.assertEqual(
            document.revision,
            hashlib.sha256(expected_content.encode("utf-8")).hexdigest(),
        )

    def test_save_validates_revision_and_preserves_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "sample")
            skill_file = root / "sample" / "SKILL.md"
            skill_file.chmod(0o640)
            original = user_skill_documents.read_user_skill_document(
                "sample/SKILL.md",
                root=root,
            )
            updated_content = original.content.replace(
                "name: sample",
                "name: updated-sample",
            ).replace("description: Sample skill", "description: Updated skill")

            saved = user_skill_documents.save_user_skill_document(
                "sample/SKILL.md",
                updated_content,
                original.revision,
                root=root,
            )

            self.assertEqual(skill_file.read_text(encoding="utf-8"), updated_content)
            self.assertEqual(stat.S_IMODE(skill_file.stat().st_mode), 0o640)
        self.assertEqual(saved.name, "updated-sample")
        self.assertNotEqual(saved.revision, original.revision)

    def test_invalid_content_and_oversized_content_do_not_change_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "sample")
            skill_file = root / "sample" / "SKILL.md"
            original = user_skill_documents.read_user_skill_document(
                "sample/SKILL.md",
                root=root,
            )
            invalid_content = "---\nname: sample\n---\n# Missing description\n"
            oversized_content = (
                "---\nname: sample\ndescription: Sample\n---\n"
                + "x" * user_skill_documents.MAX_SKILL_CONTENT_BYTES
            )

            with self.assertRaises(user_skills.SkillFrontmatterError):
                user_skill_documents.save_user_skill_document(
                    "sample/SKILL.md",
                    invalid_content,
                    original.revision,
                    root=root,
                )
            with self.assertRaises(user_skill_documents.SkillContentTooLargeError):
                user_skill_documents.save_user_skill_document(
                    "sample/SKILL.md",
                    oversized_content,
                    original.revision,
                    root=root,
                )

            self.assertEqual(skill_file.read_text(encoding="utf-8"), original.content)

    def test_revision_conflict_does_not_overwrite_external_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "sample")
            skill_file = root / "sample" / "SKILL.md"
            original = user_skill_documents.read_user_skill_document(
                "sample/SKILL.md",
                root=root,
            )
            external_content = original.content.replace("Sample skill", "External change")
            skill_file.write_text(external_content, encoding="utf-8")

            with self.assertRaises(user_skill_documents.SkillRevisionConflictError):
                user_skill_documents.save_user_skill_document(
                    "sample/SKILL.md",
                    original.content.replace("Sample skill", "Local change"),
                    original.revision,
                    root=root,
                )

            self.assertEqual(skill_file.read_text(encoding="utf-8"), external_content)

    def test_rejects_traversal_nested_and_symlink_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "sample")
            for relative_path in (
                "../sample/SKILL.md",
                "group/sample/SKILL.md",
                "sample\\SKILL.md",
                "/sample/SKILL.md",
            ):
                with self.subTest(relative_path=relative_path):
                    with self.assertRaises(user_skill_documents.SkillPathError):
                        user_skill_documents.read_user_skill_document(
                            relative_path,
                            root=root,
                        )

            link = root / "linked"
            try:
                link.symlink_to(root / "sample", target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")
            with self.assertRaises(user_skill_documents.SkillPathError):
                user_skill_documents.read_user_skill_document(
                    "linked/SKILL.md",
                    root=root,
                )

    @staticmethod
    def _write_skill(directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(
            "---\n"
            "name: sample\n"
            "description: Sample skill\n"
            "---\n"
            "# Skill body\n",
            encoding="utf-8",
        )

    @staticmethod
    def _new_skill_content(name: str) -> str:
        return (
            "---\n"
            f"name: {name}\n"
            "description: Newly created skill\n"
            "---\n\n"
            "# Skill body\n"
        )


if __name__ == "__main__":
    unittest.main()
