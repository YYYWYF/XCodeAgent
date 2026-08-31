from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.agents.test_generation.generator import (
    _build_prompt,
    _cached_test_result,
    _validate_test_files,
)


class TestGenerationCapabilitiesTests(unittest.TestCase):
    """覆盖前端测试命名放宽和现有 npm 能力边界。"""

    def test_prompt_forbids_new_frontend_npm_packages(self) -> None:
        """前端测试提示必须把项目现有依赖作为唯一第三方包能力清单。"""

        prompt = _build_prompt(
            {
                "source_files": ["frontend/src/pages/Orders/index.tsx"],
                "frontend_test_capabilities": {
                    "manifest_found": True,
                    "manifest_path": "frontend/package.json",
                    "available_packages": [
                        "@testing-library/react",
                        "react",
                    ],
                    "internal_alias_prefixes": ["@/"],
                },
            }
        )

        self.assertIn("frontend_test_capabilities.available_packages", prompt)
        self.assertIn("Never import a package that is absent from that list", prompt)
        self.assertIn("@testing-library/react", prompt)

    def test_frontend_test_filename_does_not_require_hyphen(self) -> None:
        """前端测试只校验目录和后缀，不再要求文件名包含连字符。"""

        with tempfile.TemporaryDirectory() as workspace:
            test_path = Path(workspace) / "frontend/tests/PageAgeEntry.test.tsx"
            test_path.parent.mkdir(parents=True)
            test_path.write_text(
                "it('renders PageAgeEntry', () => expect(true).toBe(true));\n",
                encoding="utf-8",
            )
            validation = _validate_test_files(
                workspace,
                ["frontend/tests/PageAgeEntry.test.tsx"],
                ["frontend/tests/PageAgeEntry.test.tsx"],
                new_files=["frontend/tests/PageAgeEntry.test.tsx"],
                source_files=["frontend/src/pages/PageAgeEntry/index.tsx"],
            )

        self.assertTrue(validation["valid"])
        self.assertEqual(validation["invalid_paths"], [])

    def test_frontend_test_rejects_npm_package_missing_from_manifest(self) -> None:
        """生成测试不得导入当前前端 package.json 未声明的新 npm 包。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            package_path = root / "frontend/package.json"
            package_path.parent.mkdir(parents=True)
            package_path.write_text(
                json.dumps(
                    {
                        "dependencies": {"react": "18.2.0"},
                        "devDependencies": {
                            "@testing-library/react": "16.2.0"
                        },
                    }
                ),
                encoding="utf-8",
            )
            test_path = root / "frontend/tests/PageAgeEntry.test.tsx"
            test_path.parent.mkdir(parents=True)
            test_path.write_text(
                "import userEvent from '@testing-library/user-event';\n"
                "it('renders PageAgeEntry', () => expect(userEvent).toBeDefined());\n",
                encoding="utf-8",
            )
            validation = _validate_test_files(
                workspace,
                ["frontend/tests/PageAgeEntry.test.tsx"],
                ["frontend/tests/PageAgeEntry.test.tsx"],
                new_files=["frontend/tests/PageAgeEntry.test.tsx"],
                source_files=["frontend/src/pages/PageAgeEntry/index.tsx"],
            )

        self.assertFalse(validation["valid"])
        self.assertEqual(
            validation["unavailable_imports"],
            {
                "frontend/tests/PageAgeEntry.test.tsx": [
                    "@testing-library/user-event"
                ]
            },
        )
        self.assertEqual(
            validation["invalid_contents"],
            ["frontend/tests/PageAgeEntry.test.tsx"],
        )

    def test_invalid_cached_test_is_regenerated_instead_of_reused(self) -> None:
        """旧测试引用不可用依赖时必须让缓存失效并重新进入生成。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_relative = "frontend/src/pages/PageAgeEntry/index.tsx"
            source_path = root / source_relative
            source_path.parent.mkdir(parents=True)
            source_path.write_text(
                "export const PageAgeEntry = () => null;\n",
                encoding="utf-8",
            )
            package_path = root / "frontend/package.json"
            package_path.write_text(
                json.dumps(
                    {
                        "dependencies": {"react": "18.2.0"},
                        "devDependencies": {
                            "@testing-library/react": "16.2.0"
                        },
                    }
                ),
                encoding="utf-8",
            )
            test_relative = "frontend/tests/PageAgeEntry.test.tsx"
            test_path = root / test_relative
            test_path.parent.mkdir(parents=True)
            test_path.write_text(
                "import userEvent from '@testing-library/user-event';\n"
                "it('renders PageAgeEntry', () => expect(userEvent).toBeDefined());\n",
                encoding="utf-8",
            )
            mapping_path = root / ".xcodeagent/cache/unit-test-mappings.json"
            mapping_path.parent.mkdir(parents=True)
            mapping_path.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "sourcePaths": [source_relative],
                                "sourceHashes": {
                                    source_relative: hashlib.sha256(
                                        source_path.read_bytes()
                                    ).hexdigest()
                                },
                                "testPath": test_relative,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            cached = _cached_test_result(workspace, [source_relative])

        self.assertIsNone(cached)


if __name__ == "__main__":
    unittest.main()
