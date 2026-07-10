from __future__ import annotations

import os
import unittest
from pathlib import Path

from app.workspace.spec_documents import REPOSITORY_ROOT, workspace_root


class WorkflowArtifactPathTests(unittest.TestCase):
    def test_default_workspace_root_is_repository_var_workspaces(self) -> None:
        self.assertEqual(
            workspace_root({"project_id": "demo-project"}),
            REPOSITORY_ROOT / "var" / "workspaces" / "demo-project",
        )

    def test_default_workspace_root_is_independent_of_cwd(self) -> None:
        original_cwd = Path.cwd()
        try:
            os.chdir(REPOSITORY_ROOT / "Backend")
            self.assertEqual(
                workspace_root({"project_id": "demo-project"}),
                REPOSITORY_ROOT / "var" / "workspaces" / "demo-project",
            )
        finally:
            os.chdir(original_cwd)

    def test_relative_workspace_is_resolved_from_repository_root(self) -> None:
        self.assertEqual(
            workspace_root({"workspace": "var/workspaces/custom-project"}),
            REPOSITORY_ROOT / "var" / "workspaces" / "custom-project",
        )

    def test_absolute_workspace_is_preserved(self) -> None:
        absolute_workspace = Path("/tmp/xcodeagent-workspace")

        self.assertEqual(
            workspace_root({"workspace": str(absolute_workspace)}),
            absolute_workspace,
        )


if __name__ == "__main__":
    unittest.main()
