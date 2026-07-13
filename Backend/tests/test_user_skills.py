from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.protocols import user_skills as user_skills_protocol
from app.services import user_skills


class UserSkillsServiceTests(unittest.TestCase):
    def test_missing_directory_returns_empty_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / "missing"

            catalog = user_skills.list_user_skills(root)

        self.assertEqual(catalog.root, "~/.xcodeagent_dev/skills")
        self.assertEqual(catalog.skills, [])
        self.assertEqual(catalog.skipped_count, 0)

    def test_lists_only_valid_direct_child_skills_in_name_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(
                root / "zeta",
                name="zeta",
                description="Last skill",
                version="2.4",
            )
            self._write_skill(
                root / "alpha",
                name="Alpha",
                description="First skill",
            )
            self._write_skill(
                root / "category" / "nested",
                name="nested",
                description="Must not be discovered",
            )

            catalog = user_skills.list_user_skills(root)

        self.assertEqual([skill.name for skill in catalog.skills], ["Alpha", "zeta"])
        self.assertEqual(catalog.skills[0].relative_path, "alpha/SKILL.md")
        self.assertEqual(catalog.skills[1].version, "2.4")
        self.assertIn("+00:00", catalog.skills[0].updated_at)

    def test_invalid_and_unreadable_skills_are_skipped_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            self._write_skill(root / "valid", name="valid", description="Usable")
            invalid_dir = root / "invalid"
            invalid_dir.mkdir()
            (invalid_dir / "SKILL.md").write_text(
                "---\nname: invalid\n---\n# Missing description\n",
                encoding="utf-8",
            )
            unreadable_dir = root / "unreadable"
            unreadable_dir.mkdir()
            (unreadable_dir / "SKILL.md").write_bytes(b"\xff\xfe\x00")

            catalog = user_skills.list_user_skills(root)

        self.assertEqual([skill.name for skill in catalog.skills], ["valid"])
        self.assertEqual(catalog.skipped_count, 2)
        self.assertEqual(
            {issue.code for issue in catalog.issues},
            {"invalid_frontmatter", "read_error"},
        )
        self.assertTrue(
            all(not issue.relative_path.startswith(str(root)) for issue in catalog.issues)
        )

    def test_skill_directory_symlink_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            target = root / "target"
            self._write_skill(target, name="target", description="Real skill")
            link = root / "linked"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")

            catalog = user_skills.list_user_skills(root)

        self.assertEqual([skill.name for skill in catalog.skills], ["target"])
        self.assertEqual(catalog.skipped_count, 1)
        self.assertEqual(catalog.issues[0].code, "symlink_ignored")
        self.assertEqual(catalog.issues[0].relative_path, "linked/SKILL.md")

    def test_environment_selects_the_matching_user_skill_directory(self) -> None:
        with patch.dict(
            os.environ,
            {user_skills.USER_SKILLS_WORKING_DIR_ENV: ".xcodeagent_st"},
        ):
            self.assertEqual(
                user_skills.resolve_user_skills_root(),
                Path.home() / ".xcodeagent_st" / "skills",
            )
            self.assertEqual(user_skills.user_skills_root_label(), "~/.xcodeagent_st/skills")

    @staticmethod
    def _write_skill(
        directory: Path,
        *,
        name: str,
        description: str,
        version: str | None = None,
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        version_line = f"version: '{version}'\n" if version else ""
        (directory / "SKILL.md").write_text(
            "---\n"
            f'name: "{name}"\n'
            f"description: {description}\n"
            f"{version_line}"
            "---\n"
            "# Skill body\n",
            encoding="utf-8",
        )


class UserSkillsAgUiTests(unittest.TestCase):
    def test_stream_emits_catalog_lifecycle_and_result(self) -> None:
        catalog = user_skills.UserSkillCatalog(
            root="~/.xcodeagent_dev/skills",
            skills=[
                user_skills.UserSkillSummary(
                    name="sample",
                    description="Sample skill",
                    directory_name="sample",
                    relative_path="sample/SKILL.md",
                    updated_at="2026-07-13T00:00:00+00:00",
                )
            ]
        )

        async def collect() -> list[str]:
            with patch.object(
                user_skills_protocol,
                "list_user_skills",
                return_value=catalog,
            ):
                stream = user_skills_protocol.build_user_skills_ag_ui_stream(
                    payload={
                        "threadId": "skills-thread",
                        "runId": "skills-run",
                        "forwardedProps": {"skillCatalog": {"action": "list"}},
                    },
                    accept="text/event-stream",
                )
                return [frame async for frame in stream]

        payload = "\n".join(asyncio.run(collect()))

        self.assertIn("RUN_STARTED", payload)
        self.assertIn("TEXT_MESSAGE_START", payload)
        self.assertIn("skill-catalog", payload)
        self.assertIn("STATE_SNAPSHOT", payload)
        self.assertIn("RUN_FINISHED", payload)
        self.assertIn('"status":"completed"', payload)
        self.assertIn('"directoryName":"sample"', payload)

    def test_invalid_action_is_returned_as_structured_failure(self) -> None:
        async def collect() -> list[str]:
            stream = user_skills_protocol.build_user_skills_ag_ui_stream(
                payload={
                    "threadId": "skills-thread",
                    "runId": "skills-run",
                    "forwardedProps": {"skillCatalog": {"action": "remove"}},
                },
                accept="text/event-stream",
            )
            return [frame async for frame in stream]

        payload = "\n".join(asyncio.run(collect()))

        self.assertIn('"status":"failed"', payload)
        self.assertIn("ValueError", payload)
        self.assertIn("RUN_FINISHED", payload)


if __name__ == "__main__":
    unittest.main()
