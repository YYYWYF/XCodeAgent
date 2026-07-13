from __future__ import annotations

import hashlib
import stat
import tempfile
import unittest
from pathlib import Path

from app.services import user_skill_documents, user_skills


class UserSkillDocumentTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
