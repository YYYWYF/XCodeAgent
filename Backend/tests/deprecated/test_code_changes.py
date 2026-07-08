# from __future__ import annotations
# 
# import tempfile
# import unittest
# from pathlib import Path
# 
# from app.graph.agent import AgentRuntime
# from app.workspace import workspace
# from app.middleware.approvals import ApprovalGrant, approval_store
# 
# 
# class CodeChangeTests(unittest.TestCase):
#     def test_diff_stats_counts_unified_diff_lines(self) -> None:
#         diff = workspace._text_diff(
#             "alpha\nbeta\n",
#             "alpha\nbravo\ncharlie\n",
#             fromfile="example.txt",
#             tofile="example.txt",
#         )
# 
#         self.assertEqual(workspace._diff_stats(diff), {"additions": 2, "deletions": 1})
# 
#     def test_file_write_new_file_returns_pending_code_change(self) -> None:
#         with tempfile.TemporaryDirectory() as tmp:
#             result = workspace.write_file(
#                 workspace.WriteFileRequest(
#                     workspace_root=tmp,
#                     path="src/example.ts",
#                     content="const value = 1\n",
#                 )
#             )
# 
#         self.assertTrue(result["requires_approval"])
#         code_change = result["code_change"]
#         self.assertEqual(code_change["changeType"], "added")
#         self.assertEqual(code_change["additions"], 1)
#         self.assertEqual(code_change["deletions"], 0)
#         self.assertEqual(code_change["approvalId"], result["approval"]["id"])
# 
#     def test_file_patch_returns_applied_code_change_after_approval(self) -> None:
#         with tempfile.TemporaryDirectory() as tmp:
#             path = Path(tmp) / "example.py"
#             path.write_text("name = 'old'\n", encoding="utf-8")
#             pending = workspace.patch_file(
#                 workspace.PatchFileRequest(
#                     workspace_root=tmp,
#                     path="example.py",
#                     edits=[workspace.FileEdit(old_text="'old'", new_text="'new'")],
#                 )
#             )
#             grant_payload = approval_store.approve(pending["approval"]["id"])
# 
#             result = workspace.patch_file(
#                 workspace.PatchFileRequest(
#                     workspace_root=tmp,
#                     path="example.py",
#                     edits=[workspace.FileEdit(old_text="'old'", new_text="'new'")],
#                     approval=ApprovalGrant(id=grant_payload["id"], token=grant_payload["token"]),
#                 )
#             )
# 
#         code_change = result["code_change"]
#         self.assertFalse(result["requires_approval"])
#         self.assertTrue(code_change["executed"])
#         self.assertEqual(code_change["changeType"], "modified")
#         self.assertEqual(code_change["additions"], 1)
#         self.assertEqual(code_change["deletions"], 1)
# 
#     def test_file_delete_requires_approval_and_reports_deleted_diff(self) -> None:
#         with tempfile.TemporaryDirectory() as tmp:
#             path = Path(tmp) / "remove_me.txt"
#             path.write_text("one\ntwo\n", encoding="utf-8")
#             pending = workspace.delete_file(
#                 workspace.DeleteFileRequest(workspace_root=tmp, path="remove_me.txt")
#             )
#             grant_payload = approval_store.approve(pending["approval"]["id"])
# 
#             result = workspace.delete_file(
#                 workspace.DeleteFileRequest(
#                     workspace_root=tmp,
#                     path="remove_me.txt",
#                     approval=ApprovalGrant(id=grant_payload["id"], token=grant_payload["token"]),
#                 )
#             )
# 
#             self.assertFalse(path.exists())
# 
#         code_change = result["code_change"]
#         self.assertFalse(result["requires_approval"])
#         self.assertTrue(code_change["executed"])
#         self.assertEqual(code_change["changeType"], "deleted")
#         self.assertEqual(code_change["additions"], 0)
#         self.assertEqual(code_change["deletions"], 2)
# 
#     def test_agent_runtime_builds_code_change_set(self) -> None:
#         code_changes = [
#             {
#                 "id": "change-1",
#                 "tool": "file.write",
#                 "path": "a.ts",
#                 "changeType": "added",
#                 "additions": 2,
#                 "deletions": 0,
#                 "diff": "",
#                 "executed": True,
#             },
#             {
#                 "id": "change-2",
#                 "tool": "file.patch",
#                 "path": "a.ts",
#                 "changeType": "modified",
#                 "additions": 1,
#                 "deletions": 1,
#                 "diff": "",
#                 "executed": True,
#             },
#         ]
# 
#         payload = AgentRuntime._code_change_set(
#             code_changes,
#             approvals=[],
#             workspace_root="/tmp/workspace",
#         )
# 
#         self.assertIsNotNone(payload)
#         assert payload is not None
#         self.assertEqual(payload["status"], "applied")
#         self.assertEqual(payload["summary"], {"files": 1, "additions": 3, "deletions": 1})
# 
# 
# if __name__ == "__main__":
#     unittest.main()
