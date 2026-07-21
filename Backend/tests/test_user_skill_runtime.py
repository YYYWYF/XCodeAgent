from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepagents.middleware.skills import SkillsMiddleware

from app.agents.workspace_scope import create_workspace_backend
from app.services import user_skill_runtime
from app.services import user_skills
from app.services.builtin_skills import BUILTIN_SKILLS_VIRTUAL_ROOT


class UserSkillRuntimeTests(unittest.TestCase):
    def test_missing_root_creates_empty_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            missing = Path(temporary_root) / "missing"

            snapshot = user_skill_runtime.create_user_skill_runtime_snapshot(root=missing)

            self.assertEqual(snapshot.skills, ())
            self.assertEqual(snapshot.issues, ())
            self.assertEqual(snapshot.backend.ls("/").entries, [])

    def test_symlinked_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            parent = Path(temporary_root)
            target = parent / "target"
            target.mkdir()
            linked_root = parent / "skills"
            try:
                linked_root.symlink_to(target, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("Symlinks are unavailable in this environment.")

            snapshot = user_skill_runtime.create_user_skill_runtime_snapshot(
                root=linked_root
            )

            self.assertEqual(snapshot.skills, ())
            self.assertEqual(snapshot.issues[0].code, "unsafe_root")

    def test_snapshot_copies_complete_skill_and_deepagents_loads_metadata(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary_root,
            tempfile.TemporaryDirectory() as workspace,
        ):
            root = Path(temporary_root)
            self._write_skill(root / "sample", name="sample")
            reference = root / "sample" / "references" / "guide.md"
            reference.parent.mkdir()
            reference.write_text("runtime guide\n", encoding="utf-8")
            script = root / "sample" / "scripts" / "check.py"
            script.parent.mkdir()
            script.write_text("print('check')\n", encoding="utf-8")
            script.chmod(0o755)

            snapshot = user_skill_runtime.create_user_skill_runtime_snapshot(root=root)
            backend = create_workspace_backend(
                workspace,
                user_skills_backend=snapshot.backend,
            )
            middleware = SkillsMiddleware(
                backend=backend,
                sources=[user_skill_runtime.USER_SKILLS_VIRTUAL_ROOT],
            )
            update = middleware.before_agent({}, object(), {})

            self.assertEqual(snapshot.skills, ("sample",))
            self.assertIsNotNone(update)
            metadata = update["skills_metadata"][0]
            self.assertEqual(metadata["name"], "sample")
            self.assertEqual(
                metadata["path"],
                f"{user_skill_runtime.USER_SKILLS_VIRTUAL_ROOT}sample/SKILL.md",
            )
            self.assertNotIn("content", metadata)
            snapshot_mode = stat.S_IMODE(
                (Path(snapshot.backend.cwd) / "sample" / "SKILL.md").stat().st_mode
            )
            self.assertEqual(snapshot_mode & 0o222, 0)
            script_mode = stat.S_IMODE(
                (
                    Path(snapshot.backend.cwd) / "sample" / "scripts" / "check.py"
                ).stat().st_mode
            )
            self.assertEqual(script_mode & 0o333, 0)
            write_result = snapshot.backend.write("/sample/new.md", "blocked")
            edit_result = snapshot.backend.edit(
                "/sample/SKILL.md",
                "sample",
                "changed",
            )
            self.assertIsNotNone(write_result.error)
            self.assertIsNotNone(edit_result.error)
            reference_result = backend.read(
                f"{user_skill_runtime.USER_SKILLS_VIRTUAL_ROOT}sample/references/guide.md"
            )
            self.assertIsNone(reference_result.error)
            self.assertEqual(reference_result.file_data["content"], "runtime guide\n")

    def test_user_skill_metadata_overrides_builtin_skill(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary_root,
            tempfile.TemporaryDirectory() as workspace,
        ):
            root = Path(temporary_root)
            self._write_skill(
                root / "react-develop-specification",
                name="react-develop-specification",
                body="user override",
            )
            snapshot = user_skill_runtime.create_user_skill_runtime_snapshot(root=root)
            backend = create_workspace_backend(
                workspace,
                include_builtin_skills=True,
                user_skills_backend=snapshot.backend,
            )
            middleware = SkillsMiddleware(
                backend=backend,
                sources=[
                    BUILTIN_SKILLS_VIRTUAL_ROOT,
                    user_skill_runtime.USER_SKILLS_VIRTUAL_ROOT,
                ],
            )

            update = middleware.before_agent({}, object(), {})
            metadata = {
                item["name"]: item
                for item in ([] if update is None else update["skills_metadata"])
            }

            self.assertEqual(
                metadata["react-develop-specification"]["path"],
                f"{user_skill_runtime.USER_SKILLS_VIRTUAL_ROOT}"
                "react-develop-specification/SKILL.md",
            )

    def test_snapshot_remains_stable_after_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "sample", name="sample", body="old body")
            snapshot = user_skill_runtime.create_user_skill_runtime_snapshot(root=root)

            self._write_skill(root / "sample", name="sample", body="new body")
            result = snapshot.backend.read("/sample/SKILL.md")

            self.assertIsNone(result.error)
            self.assertIn("old body", result.file_data["content"])
            self.assertNotIn("new body", result.file_data["content"])

    def test_explicit_selection_filters_snapshot_and_force_loads_complete_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "alpha", name="alpha", body="alpha instructions")
            self._write_skill(root / "beta", name="beta", body="beta instructions")
            reference = root / "alpha" / "references" / "guide.md"
            reference.parent.mkdir()
            reference.write_text("supporting resource", encoding="utf-8")

            snapshot = user_skill_runtime.create_user_skill_runtime_snapshot(
                root=root,
                selected_skill_names=("alpha",),
            )

            self.assertEqual(snapshot.skills, ("alpha",))
            self.assertEqual(len(snapshot.prompt_documents), 1)
            self.assertEqual(
                snapshot.prompt_documents[0].content,
                (root / "alpha" / "SKILL.md").read_text(encoding="utf-8"),
            )
            self.assertIsNotNone(snapshot.backend.read("/beta/SKILL.md").error)
            self.assertEqual(
                snapshot.backend.read("/alpha/references/guide.md").file_data["content"],
                "supporting resource",
            )
            prompt = user_skill_runtime.build_required_user_skills_prompt(
                snapshot.prompt_documents
            )
            self.assertIn("<selected-skill name=\"alpha\"", prompt)
            self.assertIn("# alpha instructions", prompt)
            self.assertNotIn("supporting resource", prompt)

    def test_unknown_explicit_selection_is_rejected_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "alpha", name="alpha")

            with self.assertRaises(user_skill_runtime.SelectedSkillUnavailableError):
                user_skill_runtime.create_user_skill_runtime_snapshot(
                    root=root,
                    selected_skill_names=("missing",),
                )

    def test_disabled_skill_is_removed_from_snapshot_and_explicit_selection(self) -> None:
        """确认关闭技能不会进入快照且无法被显式强制选择。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / "environment" / "skills"
            self._write_skill(root / "alpha", name="alpha")
            self._write_skill(root / "beta", name="beta")
            first_revision = user_skill_runtime.get_user_skill_runtime_revision(root)

            user_skills.update_user_skill_enabled(
                "beta/SKILL.md",
                False,
                root=root,
            )
            snapshot = user_skill_runtime.create_user_skill_runtime_snapshot(root=root)
            second_revision = user_skill_runtime.get_user_skill_runtime_revision(root)

            self.assertEqual(snapshot.skills, ("alpha",))
            self.assertIsNotNone(snapshot.backend.read("/beta/SKILL.md").error)
            self.assertNotEqual(first_revision, second_revision)
            with self.assertRaises(user_skill_runtime.SelectedSkillUnavailableError):
                user_skill_runtime.create_user_skill_runtime_snapshot(
                    root=root,
                    selected_skill_names=("beta",),
                )

    def test_explicit_selection_prompt_budget_rejects_without_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "alpha", name="alpha", body="完整技能指令")

            with (
                patch.object(user_skill_runtime, "MAX_SELECTED_SKILLS_PROMPT_BYTES", 4),
                self.assertRaises(user_skill_runtime.SelectedSkillsContextTooLargeError),
            ):
                user_skill_runtime.create_user_skill_runtime_snapshot(
                    root=root,
                    selected_skill_names=("alpha",),
                )

    def test_revision_changes_when_supporting_resource_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "sample", name="sample")
            resource = root / "sample" / "reference.md"
            resource.write_text("first", encoding="utf-8")
            first_revision = user_skill_runtime.get_user_skill_runtime_revision(root)

            resource.write_text("second version", encoding="utf-8")
            second_revision = user_skill_runtime.get_user_skill_runtime_revision(root)

            self.assertNotEqual(first_revision, second_revision)

    def test_stale_expected_revision_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "sample", name="sample", body="first")
            revision = user_skill_runtime.get_user_skill_runtime_revision(root)
            self._write_skill(root / "sample", name="sample", body="second")

            with self.assertRaises(user_skill_runtime.UserSkillSnapshotChangedError):
                user_skill_runtime.create_user_skill_runtime_snapshot(
                    revision,
                    root=root,
                )

    def test_symlink_or_special_file_skips_only_affected_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "valid", name="valid")
            self._write_skill(root / "linked", name="linked")
            target = root / "target.txt"
            target.write_text("outside", encoding="utf-8")
            link = root / "linked" / "reference.txt"
            try:
                link.symlink_to(target)
            except (NotImplementedError, OSError):
                self.skipTest("Symlinks are unavailable in this environment.")

            snapshot = user_skill_runtime.create_user_skill_runtime_snapshot(root=root)

            self.assertEqual(snapshot.skills, ("valid",))
            self.assertTrue(
                any(issue.relative_path == "linked/SKILL.md" for issue in snapshot.issues)
            )

    def test_bundle_limits_skip_entire_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "sample", name="sample")
            (root / "sample" / "large.bin").write_bytes(b"12345")

            with patch.object(user_skill_runtime, "MAX_SKILL_RESOURCE_BYTES", 4):
                snapshot = user_skill_runtime.create_user_skill_runtime_snapshot(root=root)

            self.assertEqual(snapshot.skills, ())
            self.assertTrue(snapshot.issues)

    def test_file_count_limit_skips_entire_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "sample", name="sample")
            (root / "sample" / "reference.md").write_text(
                "reference",
                encoding="utf-8",
            )

            with patch.object(user_skill_runtime, "MAX_SKILL_BUNDLE_FILES", 1):
                snapshot = user_skill_runtime.create_user_skill_runtime_snapshot(root=root)

            self.assertEqual(snapshot.skills, ())
            self.assertTrue(snapshot.issues)

    def test_skill_and_catalog_size_limits_skip_complete_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "alpha", name="alpha")
            self._write_skill(root / "beta", name="beta")
            alpha_size = (root / "alpha" / "SKILL.md").stat().st_size
            beta_size = (root / "beta" / "SKILL.md").stat().st_size

            with patch.object(
                user_skill_runtime,
                "MAX_SKILL_BUNDLE_BYTES",
                min(alpha_size, beta_size) - 1,
            ):
                skill_limited = user_skill_runtime.create_user_skill_runtime_snapshot(
                    root=root
                )
            with patch.object(
                user_skill_runtime,
                "MAX_USER_SKILL_SNAPSHOT_BYTES",
                alpha_size,
            ):
                catalog_limited = user_skill_runtime.create_user_skill_runtime_snapshot(
                    root=root
                )

            self.assertEqual(skill_limited.skills, ())
            self.assertEqual(catalog_limited.skills, ("alpha",))
            self.assertTrue(
                any(issue.code == "catalog_too_large" for issue in catalog_limited.issues)
            )

    def test_duplicate_name_uses_later_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "alpha", name="duplicate", body="alpha body")
            self._write_skill(root / "zeta", name="duplicate", body="zeta body")

            snapshot = user_skill_runtime.create_user_skill_runtime_snapshot(root=root)

            self.assertEqual(snapshot.skills, ("duplicate",))
            self.assertIsNotNone(snapshot.backend.read("/zeta/SKILL.md").file_data)
            self.assertIsNotNone(snapshot.backend.read("/alpha/SKILL.md").error)
            self.assertTrue(
                any(issue.code == "duplicate_name" for issue in snapshot.issues)
            )

    def test_fifo_resource_is_rejected_when_supported(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO files are unavailable in this environment.")
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "sample", name="sample")
            fifo_path = root / "sample" / "events.pipe"
            try:
                os.mkfifo(fifo_path)
            except OSError:
                self.skipTest("FIFO files are unavailable in this environment.")

            snapshot = user_skill_runtime.create_user_skill_runtime_snapshot(root=root)

            self.assertEqual(snapshot.skills, ())
            self.assertTrue(snapshot.issues)

    @staticmethod
    def _write_skill(
        directory: Path,
        *,
        name: str,
        body: str = "runtime instructions",
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: Runtime sample skill\n"
            "---\n\n"
            f"# {body}\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
