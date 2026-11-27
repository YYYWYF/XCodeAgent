"""分支避重、独立 AG-UI 协议及普通推送的定向回归。"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app.protocols.repository_branch import (
    RepositoryBranchAction,
    build_repository_branch_ag_ui_stream,
    repository_branch_capabilities,
)
from app.protocols.version_publish import build_version_publish_ag_ui_stream
from app.services.git_branch import GitBranchError
from app.services.repository_branch import (
    check_remote_branch,
    create_local_branch,
    delete_remote_branch,
    list_remote_branches,
    push_baseline_to_branch,
    read_workspace_repository_target,
)
from app.services.version_publish import (
    VersionPublishConflictError,
    VersionPublishError,
    VersionPublishRequest,
    publish_version,
)


def _git(root: Path, *arguments: str) -> str:
    """仅在本用例临时仓库执行真实 Git 并返回标准输出。"""

    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repository(root: Path) -> Path:
    """建立与真实用户仓库隔离、已有提交和身份的最小测试工程。"""

    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Branch Test")
    _git(root, "config", "user.email", "branch@example.invalid")
    (root / "code.txt").write_text("initial\n", encoding="utf-8")
    _git(root, "add", "code.txt")
    _git(root, "commit", "--quiet", "-m", "initial")
    return root


def _application(root: Path) -> None:
    """写入最小当前分支配置，不提供覆盖授权字段。"""

    metadata = root / ".devagentstudio"
    metadata.mkdir(exist_ok=True)
    (metadata / "application.json").write_text(json.dumps({
        "repoUrl": "https://repository.invalid/project.git", "branchName": "v1_0",
    }), encoding="utf-8")


class RepositoryBranchTests(unittest.TestCase):
    """覆盖远端查询失败的关闭式校验和创建前的无副作用拒绝。"""

    def test_list_reads_all_heads_and_deduplicates(self) -> None:
        """完整返回嵌套分支，排除标签，去重并排序。"""

        response = subprocess.CompletedProcess([], 0, stdout=(
            "a refs/heads/v1_1\na refs/heads/v1_0\n"
            "a refs/heads/feature/example\na refs/heads/v1_1\na refs/tags/v1_0\n"
        ), stderr="")
        with (
            patch("app.services.repository_branch.build_authenticated_remote_url", return_value="remote"),
            patch("app.services.repository_branch._run_git", return_value=response) as command,
        ):
            self.assertEqual(list_remote_branches("https://repository.invalid"), ["feature/example", "v1_0", "v1_1"])
        self.assertEqual(command.call_args.args[1], ["ls-remote", "--heads", "remote"])

    def test_list_retries_one_timeout_without_retrying_explicit_rejection(self) -> None:
        """网络超时可做一次有界重试，持续超时仍失败，明确鉴权拒绝不重复请求。"""

        response = subprocess.CompletedProcess([], 0, stdout="a refs/heads/v1_0\n", stderr="")
        timeout = subprocess.TimeoutExpired(["git", "ls-remote"], 20)
        with (
            patch("app.services.repository_branch.build_authenticated_remote_url", return_value="remote"),
            patch("app.services.repository_branch._run_git", side_effect=[timeout, response]) as command,
        ):
            self.assertEqual(list_remote_branches("https://repository.invalid"), ["v1_0"])
            self.assertEqual(command.call_count, 2)

        with (
            patch("app.services.repository_branch.build_authenticated_remote_url", return_value="remote"),
            patch("app.services.repository_branch._run_git", side_effect=timeout) as command,
        ):
            with self.assertRaisesRegex(GitBranchError, "无法读取远端"):
                list_remote_branches("https://repository.invalid")
            self.assertEqual(command.call_count, 2, "持续超时也不能无限重试")

        rejected = subprocess.CompletedProcess([], 1, stdout="", stderr="authentication failed")
        with (
            patch("app.services.repository_branch.build_authenticated_remote_url", return_value="remote"),
            patch("app.services.repository_branch._run_git", return_value=rejected) as command,
        ):
            with self.assertRaisesRegex(GitBranchError, "认证被拒绝"):
                list_remote_branches("https://repository.invalid")
            self.assertEqual(command.call_count, 1)

    def test_failed_list_never_means_empty(self) -> None:
        """非零返回码和查询超时必须变成业务错误，而不是可用名称。"""

        for failure in (
            subprocess.CompletedProcess([], 1, stdout="", stderr="authentication failed"),
            subprocess.TimeoutExpired("git", 20),
        ):
            with (
                self.subTest(failure=failure),
                patch("app.services.repository_branch.build_authenticated_remote_url", return_value="remote"),
                patch("app.services.repository_branch._run_git", side_effect=failure if isinstance(failure, Exception) else None,
                      return_value=failure),
            ):
                with self.assertRaisesRegex(GitBranchError, "无法读取远端"):
                    list_remote_branches("https://repository.invalid")

    def test_empty_list_and_exact_branch_check(self) -> None:
        """空远端合法；名称精确匹配，不能把相同前缀当作重名。"""

        with patch("app.services.repository_branch.list_remote_branches", return_value=[]):
            self.assertFalse(check_remote_branch("repo", "v1_0"))
        with patch("app.services.repository_branch.list_remote_branches", return_value=["v1_01"]):
            self.assertFalse(check_remote_branch("repo", "v1_0"))
            self.assertTrue(check_remote_branch("repo", "v1_01"))

    def test_create_conflict_or_query_failure_keeps_local_branch_unchanged(self) -> None:
        """创建前远端冲突或鉴权失败均不得 checkout 或生成本地版本。"""

        with tempfile.TemporaryDirectory() as directory:
            root = _repository(Path(directory) / "workspace")
            _application(root)
            previous = _git(root, "branch", "--show-current")
            for response in (["v1_1"], GitBranchError("无法读取远端版本列表")):
                with self.subTest(response=response), patch(
                    "app.services.repository_branch.list_remote_branches",
                    side_effect=response if isinstance(response, Exception) else None,
                    return_value=response,
                ):
                    result = create_local_branch(root, "v1_1")
                self.assertEqual(result["status"], "failed")
                self.assertEqual(_git(root, "branch", "--show-current"), previous)
                self.assertEqual(_git(root, "branch", "--list", "v1_1"), "")

    def test_create_from_history_and_fast_forward_retry(self) -> None:
        """新分支正确来自历史提交，已存在的本迭代远端允许普通快进重推。"""

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = _repository(base / "workspace")
            remote = base / "remote.git"
            _git(base, "init", "--bare", "--quiet", str(remote))
            _git(remote, "config", "receive.denyNonFastForwards", "true")
            _application(root)
            _git(root, "branch", "v1_0")
            historic = _git(root, "rev-parse", "v1_0")
            (root / "code.txt").write_text("newer\n", encoding="utf-8")
            _git(root, "commit", "--quiet", "-am", "newer")
            with patch("app.services.repository_branch.build_authenticated_remote_url", return_value=str(remote)):
                result = create_local_branch(root, "v1_1", start_point="v1_0")
                self.assertEqual(result["status"], "created")
                self.assertEqual(_git(root, "rev-parse", "HEAD"), historic)
                (root / "code.txt").write_text("iteration\n", encoding="utf-8")
                _git(root, "commit", "--quiet", "-am", "iteration")
                pushed = push_baseline_to_branch(root, "repo", "v1_1")
            self.assertEqual(pushed["status"], "pushed")
            self.assertEqual(_git(remote, "rev-parse", "refs/heads/v1_1"), _git(root, "rev-parse", "HEAD"))
            self.assertEqual(read_workspace_repository_target(root), ("https://repository.invalid/project.git", "v1_0"))

    def test_explicit_delete_remote_branch_preserves_local_branch_and_commit(self) -> None:
        """显式删除只影响临时远端，本地分支与提交保留，后续普通推送可重建远端。"""

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = _repository(base / "workspace")
            remote = base / "remote.git"
            _git(base, "init", "--bare", "--quiet", str(remote))
            _git(root, "branch", "-m", "v1_27")
            _git(root, "push", str(remote), "HEAD:refs/heads/v1_27")
            local_head = _git(root, "rev-parse", "HEAD")
            with patch(
                "app.services.repository_branch.build_authenticated_remote_url",
                return_value=str(remote),
            ):
                result = delete_remote_branch("repo", "v1_27")
                self.assertEqual(result["status"], "deleted")
                with self.assertRaises(subprocess.CalledProcessError):
                    _git(remote, "rev-parse", "--verify", "refs/heads/v1_27")
                pushed = push_baseline_to_branch(root, "repo", "v1_27")
            self.assertEqual(pushed["status"], "pushed")
            self.assertEqual(_git(root, "branch", "--show-current"), "v1_27")
            self.assertEqual(_git(root, "rev-parse", "HEAD"), local_head)
            self.assertEqual(_git(remote, "rev-parse", "refs/heads/v1_27"), local_head)

    def test_rejected_push_never_force_pushes_or_deletes(self) -> None:
        """普通推送失败只保留失败回执，不尝试强推或删除远端分支。"""

        def respond(root: Path, arguments: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
            """模拟已有本地分支和服务端拒绝，记录实际 Git 调用边界。"""

            if arguments[0] == "branch":
                return subprocess.CompletedProcess([], 0, stdout="v1_0\n", stderr="")
            if arguments[0] == "rev-parse":
                return subprocess.CompletedProcess([], 0, stdout="a" * 40, stderr="")
            return subprocess.CompletedProcess([], 1, stdout="", stderr="non-fast-forward")

        with (
            patch("app.services.repository_branch.build_authenticated_remote_url", return_value="remote"),
            patch("app.services.repository_branch._run_git", side_effect=respond) as commands,
        ):
            result = push_baseline_to_branch("/tmp/branch-test", "repo", "v1_0")
        self.assertEqual(result["status"], "failed")
        pushes = [call.args[1] for call in commands.call_args_list if call.args[1][0] == "push"]
        self.assertEqual(pushes, [["push", "remote", "HEAD:refs/heads/v1_0"]])

    def test_publish_divergence_preserves_remote_and_local_commit(self) -> None:
        """发布拒绝分叉，不删除远端，重复失败也不产生重复本地提交。"""

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = _repository(base / "workspace")
            remote = base / "remote.git"
            _git(base, "init", "--bare", "--quiet", str(remote))
            _git(remote, "config", "receive.denyNonFastForwards", "true")
            _git(root, "branch", "v1_0")
            _git(root, "push", str(remote), "HEAD:refs/heads/v1_0")
            other = base / "other"
            _git(base, "clone", "--quiet", "--branch", "v1_0", str(remote), str(other))
            _git(other, "config", "user.name", "Other Test")
            _git(other, "config", "user.email", "other@example.invalid")
            (other / "remote.txt").write_text("remote update\n", encoding="utf-8")
            _git(other, "add", "remote.txt")
            _git(other, "commit", "--quiet", "-m", "remote update")
            _git(other, "push", str(remote), "HEAD:refs/heads/v1_0")
            remote_head = _git(remote, "rev-parse", "refs/heads/v1_0")
            (root / "code.txt").write_text("local update\n", encoding="utf-8")
            request = VersionPublishRequest(action="publish", workspaceRoot=str(root), repoUrl="repo",
                                            branchName="v1_0", description="本地修改")
            with patch("app.services.version_publish.build_authenticated_remote_url", return_value=str(remote)):
                with self.assertRaisesRegex(VersionPublishConflictError, "本地提交已保留"):
                    publish_version(request)
                local_head = _git(root, "rev-parse", "HEAD")
                with self.assertRaises(VersionPublishError):
                    publish_version(request)
                self.assertEqual(_git(root, "rev-parse", "HEAD"), local_head)
            self.assertEqual(_git(remote, "rev-parse", "refs/heads/v1_0"), remote_head)
            self.assertNotEqual(local_head, remote_head)

    def test_publish_existing_branch_uses_fast_forward(self) -> None:
        """版本发布对本迭代既有基线采用普通推送并可在干净工作区重试。"""

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = _repository(base / "workspace")
            remote = base / "remote.git"
            _git(base, "init", "--bare", "--quiet", str(remote))
            _git(root, "push", str(remote), "HEAD:refs/heads/v1_0")
            _application(root)
            (root / "code.txt").write_text("finished\n", encoding="utf-8")
            request = VersionPublishRequest(action="publish", workspaceRoot=str(root), repoUrl="repo",
                                            branchName="v1_0", description="完成迭代")
            with patch("app.services.version_publish.build_authenticated_remote_url", return_value=str(remote)):
                result = publish_version(request)
                self.assertEqual(publish_version(request).commit_sha, result.commit_sha)
            self.assertEqual(_git(remote, "rev-parse", "refs/heads/v1_0"), result.commit_sha)


class RepositoryBranchProtocolTests(unittest.TestCase):
    """保证列表、检查及失败场景都使用完整 AG-UI 生命周期。"""

    def _events(self, action: dict) -> list[dict]:
        """收集当前动作的官方 AG-UI 流，只在测试中提取 SSE 事件。"""

        async def collect() -> str:
            """读取测试生产者发出的全部标准流帧。"""

            return "".join([chunk async for chunk in build_repository_branch_ag_ui_stream(payload={
                "threadId": "branch-test", "runId": "branch-test-run",
                "forwardedProps": {"repositoryBranch": action},
            })])
        stream = asyncio.run(collect())
        return [json.loads(line[5:]) for line in stream.splitlines() if line.startswith("data:")]

    def test_list_and_check_return_complete_branches(self) -> None:
        """list 不需工作区或分支名，check 同时返回存在性及完整列表。"""

        for action in ({"action": "list", "repoUrl": "repo"},
                       {"action": "check", "repoUrl": "repo", "branchName": "v1_0"}):
            with self.subTest(action=action), patch(
                "app.protocols.repository_branch.list_remote_branches", return_value=["v1_0", "v1_1"],
            ):
                events = self._events(action)
                result = next(event["value"] for event in events if event["type"] == "CUSTOM")
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["branches"], ["v1_0", "v1_1"])
                if action["action"] == "check":
                    self.assertTrue(result["exists"])
                self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertIn("list", repository_branch_capabilities()["actions"])

    def test_create_rejection_is_failed_not_created(self) -> None:
        """服务未创建本地分支时必须编码失败，不能误报完成后继续迭代。"""

        with patch("app.protocols.repository_branch.create_local_branch", return_value={
            "status": "failed", "message": "分支已存在，请修改分支名。",
        }):
            events = self._events({"action": "create", "workspaceRoot": "/tmp/branch-test", "branchName": "v1_0"})
        result = next(event["value"] for event in events if event["type"] == "CUSTOM")
        self.assertEqual(result["status"], "failed")
        for expected in ("RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_END", "CUSTOM", "STATE_SNAPSHOT", "RUN_FINISHED"):
            self.assertIn(expected, [event["type"] for event in events])

    def test_delete_returns_complete_ag_ui_result(self) -> None:
        """显式删除使用现有分支动作流，返回服务端确认后的删除状态。"""

        with patch("app.protocols.repository_branch.delete_remote_branch", return_value={
            "status": "deleted", "branchName": "v1_27", "message": "",
        }):
            events = self._events({
                "action": "delete", "repoUrl": "repo", "branchName": "v1_27",
            })
        result = next(event["value"] for event in events if event["type"] == "CUSTOM")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["action"], "delete")
        self.assertEqual(result["branchStatus"], "deleted")
        self.assertEqual(result["branchName"], "v1_27")
        self.assertIn("delete", repository_branch_capabilities()["actions"])
        self.assertNotIn("rename", repository_branch_capabilities()["actions"])
        with self.assertRaises(ValidationError):
            RepositoryBranchAction(action="delete", repoUrl="repo")

    def test_publish_conflict_exposes_structured_failure_reason(self) -> None:
        """发布分叉在 AG-UI 终态携带稳定标记，前端无需解析错误文案。"""

        async def collect() -> str:
            """收集一次模拟分叉发布产生的标准事件流。"""

            return "".join([chunk async for chunk in build_version_publish_ag_ui_stream(payload={
                "threadId": "publish-test",
                "forwardedProps": {"versionPublish": {
                    "action": "publish", "workspaceRoot": "/tmp/branch-test",
                    "repoUrl": "repo", "branchName": "v1_0", "description": "测试",
                }},
            })])

        with patch(
            "app.protocols.version_publish.publish_version",
            side_effect=VersionPublishConflictError("远端历史分叉，本地提交已保留。"),
        ):
            stream = asyncio.run(collect())
        events = [json.loads(line[5:]) for line in stream.splitlines() if line.startswith("data:")]
        result = next(event["value"] for event in events if event["type"] == "CUSTOM")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failureReason"], "branch_conflict")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")

    def test_overwrite_input_is_not_current_contract(self) -> None:
        """移除的覆盖授权字段不能借协议重新启用强推。"""

        with self.assertRaises(ValidationError):
            RepositoryBranchAction(action="push", workspaceRoot="workspace", allowOverwrite=True)
