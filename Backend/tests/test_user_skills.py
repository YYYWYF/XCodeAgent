from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.protocols import user_skills as user_skills_protocol
from app.services import user_skill_documents
from app.services import user_skill_imports
from app.services import user_skill_settings
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
        for working_dir in (
            ".xcodeagent_dev",
            ".xcodeagent_st",
            ".xcodeagent_uat",
            ".xcodeagent",
        ):
            with self.subTest(working_dir=working_dir), patch.dict(
                os.environ,
                {user_skills.USER_SKILLS_WORKING_DIR_ENV: working_dir},
            ):
                self.assertEqual(
                    user_skills.resolve_user_skills_root(),
                    Path.home() / working_dir / "skills",
                )
                self.assertEqual(
                    user_skills.user_skills_root_label(), f"~/{working_dir}/skills"
                )

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
    def test_capabilities_include_catalog_and_import_actions(self) -> None:
        self.assertEqual(
            user_skills_protocol.user_skills_capabilities()["actions"],
            [
                "list",
                "get",
                "save",
                "create",
                "delete",
                "import",
                "set-enabled",
            ],
        )

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
        self.assertIn('"builtinSkills"', payload)

    def test_stream_supports_set_enabled_action(self) -> None:
        """确认启停动作返回完整 AG-UI 生命周期和更新后的摘要。"""

        skill = user_skills.UserSkillSummary(
            name="sample",
            description="Sample skill",
            directory_name="sample",
            relative_path="sample/SKILL.md",
            updated_at="2026-07-19T00:00:00+00:00",
            enabled=False,
        )

        async def collect() -> str:
            with patch.object(
                user_skills_protocol,
                "update_user_skill_enabled",
                return_value=skill,
            ) as update_enabled:
                stream = user_skills_protocol.build_user_skills_ag_ui_stream(
                    payload={
                        "threadId": "skills-enabled-thread",
                        "runId": "skills-enabled-run",
                        "forwardedProps": {
                            "skillCatalog": {
                                "action": "set-enabled",
                                "relativePath": "sample/SKILL.md",
                                "enabled": False,
                            }
                        },
                    },
                    accept="text/event-stream",
                )
                result = "\n".join([frame async for frame in stream])
                update_enabled.assert_called_once_with("sample/SKILL.md", False)
                return result

        payload = asyncio.run(collect())

        self.assertIn('"action":"set-enabled"', payload)
        self.assertIn('"enabled":false', payload)
        self.assertIn('"skill"', payload)
        self.assertIn("STATE_SNAPSHOT", payload)
        self.assertIn("RUN_FINISHED", payload)

    def test_set_enabled_rejects_missing_boolean(self) -> None:
        """确认启停参数缺失时通过完整 AG-UI 失败生命周期返回。"""

        async def collect() -> str:
            stream = user_skills_protocol.build_user_skills_ag_ui_stream(
                payload={
                    "threadId": "skills-enabled-thread",
                    "runId": "skills-enabled-run",
                    "forwardedProps": {
                        "skillCatalog": {
                            "action": "set-enabled",
                            "relativePath": "sample/SKILL.md",
                        }
                    },
                },
                accept="text/event-stream",
            )
            return "\n".join([frame async for frame in stream])

        payload = asyncio.run(collect())

        self.assertIn('"action":"set-enabled"', payload)
        self.assertIn('"status":"failed"', payload)
        self.assertIn("ValidationError", payload)
        self.assertIn("RUN_FINISHED", payload)

    def test_set_enabled_reports_settings_write_failure(self) -> None:
        """确认状态文件写入失败时保留完整 AG-UI 错误生命周期。"""

        async def collect() -> str:
            with patch.object(
                user_skills_protocol,
                "update_user_skill_enabled",
                side_effect=user_skill_settings.SkillSettingsError(
                    "无法保存用户技能启用状态。"
                ),
            ):
                stream = user_skills_protocol.build_user_skills_ag_ui_stream(
                    payload={
                        "threadId": "skills-enabled-thread",
                        "runId": "skills-enabled-run",
                        "forwardedProps": {
                            "skillCatalog": {
                                "action": "set-enabled",
                                "relativePath": "sample/SKILL.md",
                                "enabled": False,
                            }
                        },
                    },
                    accept="text/event-stream",
                )
                return "\n".join([frame async for frame in stream])

        payload = asyncio.run(collect())

        self.assertIn('"status":"failed"', payload)
        self.assertIn("SkillSettingsError", payload)
        self.assertIn("无法保存用户技能启用状态", payload)
        self.assertIn("RUN_FINISHED", payload)

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

    def test_create_failure_is_returned_as_structured_failure(self) -> None:
        async def collect() -> list[str]:
            with patch.object(
                user_skills_protocol,
                "create_user_skill_document",
                side_effect=user_skill_documents.SkillAlreadyExistsError(
                    "技能 duplicate_skill 已存在。"
                ),
            ):
                stream = user_skills_protocol.build_user_skills_ag_ui_stream(
                    payload={
                        "threadId": "skills-create-thread",
                        "runId": "skills-create-run",
                        "forwardedProps": {
                            "skillCatalog": {
                                "action": "create",
                                "content": (
                                    "---\nname: duplicate_skill\n"
                                    "description: Duplicate\n---\n"
                                ),
                            }
                        },
                    },
                    accept="text/event-stream",
                )
                return [frame async for frame in stream]

        payload = "\n".join(asyncio.run(collect()))

        self.assertIn('"action":"create"', payload)
        self.assertIn('"status":"failed"', payload)
        self.assertIn("SkillAlreadyExistsError", payload)
        self.assertIn("duplicate_skill", payload)
        self.assertIn("RUN_FINISHED", payload)

    def test_stream_supports_get_save_and_create_actions(self) -> None:
        document = user_skill_documents.UserSkillDocument(
            name="sample",
            relative_path="sample/SKILL.md",
            content="---\nname: sample\ndescription: Sample\n---\n",
            revision="a" * 64,
        )

        async def collect(action: str) -> str:
            input_payload = {
                "action": action,
                "relativePath": "sample/SKILL.md",
                "content": document.content,
                "expectedRevision": "b" * 64,
            }
            target = {
                "get": "read_user_skill_document",
                "save": "save_user_skill_document",
                "create": "create_user_skill_document",
            }[action]
            with patch.object(user_skills_protocol, target, return_value=document):
                stream = user_skills_protocol.build_user_skills_ag_ui_stream(
                    payload={
                        "threadId": f"skills-{action}-thread",
                        "runId": f"skills-{action}-run",
                        "forwardedProps": {"skillCatalog": input_payload},
                    },
                    accept="text/event-stream",
                )
                return "\n".join([frame async for frame in stream])

        for action in ("get", "save", "create"):
            with self.subTest(action=action):
                payload = asyncio.run(collect(action))
                self.assertIn(f'"action":"{action}"', payload)
                self.assertIn('"document"', payload)
                self.assertIn('"content":"---\\nname: sample', payload)
                self.assertIn("RUN_FINISHED", payload)

    def test_stream_supports_delete_action(self) -> None:
        deleted = user_skill_documents.DeletedUserSkill(
            name="sample",
            relative_path="sample/SKILL.md",
        )

        async def collect() -> str:
            with patch.object(
                user_skills_protocol,
                "delete_user_skill",
                return_value=deleted,
            ):
                stream = user_skills_protocol.build_user_skills_ag_ui_stream(
                    payload={
                        "threadId": "skills-delete-thread",
                        "runId": "skills-delete-run",
                        "forwardedProps": {
                            "skillCatalog": {
                                "action": "delete",
                                "relativePath": "sample/SKILL.md",
                            }
                        },
                    },
                    accept="text/event-stream",
                )
                return "\n".join([frame async for frame in stream])

        payload = asyncio.run(collect())

        self.assertIn('"action":"delete"', payload)
        self.assertIn('"deleted"', payload)
        self.assertIn('"relativePath":"sample/SKILL.md"', payload)
        self.assertIn("skill-catalog", payload)
        self.assertIn("STATE_SNAPSHOT", payload)
        self.assertIn("RUN_FINISHED", payload)

    def test_stream_supports_import_action(self) -> None:
        imported = user_skill_imports.ImportedUserSkill(
            root="~/.xcodeagent_dev/skills",
            imported=user_skills.UserSkillSummary(
                name="sample-import",
                description="Imported skill",
                directory_name="sample-import",
                relative_path="sample-import/SKILL.md",
                updated_at="2026-07-14T00:00:00+00:00",
            ),
        )

        async def collect() -> str:
            with patch.object(
                user_skills_protocol,
                "import_user_skill_archive",
                return_value=imported,
            ) as import_archive:
                stream = user_skills_protocol.build_user_skills_ag_ui_stream(
                    payload={
                        "threadId": "skills-import-thread",
                        "runId": "skills-import-run",
                        "forwardedProps": {
                            "skillCatalog": {
                                "action": "import",
                                "fileName": "sample.zip",
                                "archiveBase64": "UEs=",
                            }
                        },
                    },
                    accept="text/event-stream",
                )
                result = "\n".join([frame async for frame in stream])
                import_archive.assert_called_once_with("sample.zip", "UEs=")
                return result

        payload = asyncio.run(collect())

        self.assertIn('"action":"import"', payload)
        self.assertIn('"imported"', payload)
        self.assertIn('"name":"sample-import"', payload)
        self.assertIn('"root":"~/.xcodeagent_dev/skills"', payload)
        self.assertIn('"status":"completed"', payload)
        self.assertIn("skill-catalog", payload)
        self.assertIn("STATE_SNAPSHOT", payload)
        self.assertIn("RUN_FINISHED", payload)

    def test_import_failure_includes_structured_error_code(self) -> None:
        async def collect() -> str:
            with patch.object(
                user_skills_protocol,
                "import_user_skill_archive",
                side_effect=user_skill_imports.SkillArchivePathError(
                    "ZIP 不允许路径穿越。"
                ),
            ):
                stream = user_skills_protocol.build_user_skills_ag_ui_stream(
                    payload={
                        "threadId": "skills-import-thread",
                        "runId": "skills-import-run",
                        "forwardedProps": {
                            "skillCatalog": {
                                "action": "import",
                                "fileName": "unsafe.zip",
                                "archiveBase64": "UEs=",
                            }
                        },
                    },
                    accept="text/event-stream",
                )
                return "\n".join([frame async for frame in stream])

        payload = asyncio.run(collect())

        self.assertIn('"action":"import"', payload)
        self.assertIn('"status":"failed"', payload)
        self.assertIn('"code":"unsafe_archive_path"', payload)
        self.assertIn("SkillArchivePathError", payload)
        self.assertIn("RUN_FINISHED", payload)




if __name__ == "__main__":
    unittest.main()
