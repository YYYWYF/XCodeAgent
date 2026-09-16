"""从受信公共 Git 模板构建当前 Bootstrap ZIP。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from app.config import Settings
from app.services.template_reconcile.protocol_v2 import TemplateStateV2
from app.services.workspace_bootstrap.fs import remove_managed_path
from app.services.workspace_bootstrap.models import (
    GitTemplateError,
    TemplatePackageDownload,
    TemplatePackageError,
)
from app.services.workspace_process_registry import workspace_process_registry

logger = logging.getLogger(__name__)

_STATE_PATH = ".xcodeagent/template-state.json"
_ENGINE_MANAGED_ROOTS = frozenset({"frontend", "backend"})
_GIT_SUPPLEMENT_ROOTS = frozenset({"agent-runtime"})


class GitTemplatePackageBuilder:
    """按与 Engine Package 相同的根目录契约构建本地临时 ZIP。"""

    def __init__(self, settings: Settings) -> None:
        """保存公开模板仓库与单次 clone 超时配置。"""

        self._settings = settings

    def generate(
        self,
        workspace: str | Path,
        requested_config: dict[str, Any],
        managed_roots: tuple[str, ...],
    ) -> TemplatePackageDownload:
        """拉取本轮所需三端模板并生成可进入统一校验链路的 ZIP。"""

        root = Path(workspace).expanduser().resolve()
        repositories = self._repositories(requested_config, managed_roots)
        descriptor, archive_name = tempfile.mkstemp(
            prefix="xcodeagent-git-template-", suffix=".zip"
        )
        os.close(descriptor)
        Path(archive_name).unlink(missing_ok=True)
        try:
            # clone 目录仅用于本轮 Preparation；任何失败都不会触碰真实 Workspace。
            with tempfile.TemporaryDirectory(
                prefix="xcodeagent-git-template-sources-",
                ignore_cleanup_errors=True,
            ) as directory:
                source_root = Path(directory)
                revisions: dict[str, dict[str, str]] = {}
                for target, repository_url, branch in repositories:
                    revisions[target] = self._clone_repository(
                        root,
                        source_root,
                        target=target,
                        repository_url=repository_url,
                        branch=branch,
                    )
                state = _template_state(requested_config, revisions)
                _write_archive(
                    Path(archive_name),
                    source_root,
                    managed_roots,
                    state,
                )
            archive = Path(archive_name)
            digest = _file_sha256(archive)
            return TemplatePackageDownload(
                temporary_path=archive,
                sha256=digest,
                size=archive.stat().st_size,
                content_type="application/zip",
            )
        except Exception:
            Path(archive_name).unlink(missing_ok=True)
            raise

    def supplement_engine_package(
        self,
        workspace: str | Path,
        download: TemplatePackageDownload,
        managed_roots: tuple[str, ...],
    ) -> TemplatePackageDownload:
        """把 Engine V1 ZIP 缺失的 Git 专有 root 补进同一 Package，并保留 Engine TemplateState。"""

        archive = Path(download.temporary_path)
        try:
            seen_roots = _package_file_roots(archive)
        except zipfile.BadZipFile as exc:
            raise TemplatePackageError("模板 ZIP 已损坏或格式无效。") from exc
        missing_roots = frozenset(managed_roots) - seen_roots
        # frontend/backend 仍以 Engine 为准；缺它们时交给后续 exact-root 校验，避免误用 Git 覆盖。
        if missing_roots & _ENGINE_MANAGED_ROOTS or not (
            missing_roots & _GIT_SUPPLEMENT_ROOTS
        ):
            return download
        repositories = self._git_supplement_targets(missing_roots)
        logger.info(
            "Engine 模板缺少 %s，开始从 Git 补齐。",
            "、".join(target for target, _url, _branch in repositories),
        )
        descriptor, archive_name = tempfile.mkstemp(
            prefix="xcodeagent-engine-template-supplement-", suffix=".zip"
        )
        os.close(descriptor)
        Path(archive_name).unlink(missing_ok=True)
        merged = Path(archive_name)
        root = Path(workspace).expanduser().resolve()
        try:
            with tempfile.TemporaryDirectory(
                prefix="xcodeagent-git-template-supplement-",
                ignore_cleanup_errors=True,
            ) as directory:
                source_root = Path(directory)
                extra_roots: dict[str, Path] = {}
                for target, repository_url, branch in repositories:
                    self._clone_repository(
                        root,
                        source_root,
                        target=target,
                        repository_url=repository_url,
                        branch=branch,
                    )
                    extra_roots[target] = source_root / target
                _merge_archive(archive, extra_roots, merged)
            digest = _file_sha256(merged)
            supplemented = TemplatePackageDownload(
                temporary_path=merged,
                sha256=digest,
                size=merged.stat().st_size,
                content_type="application/zip",
            )
            archive.unlink(missing_ok=True)
            return supplemented
        except Exception:
            merged.unlink(missing_ok=True)
            raise

    def materialize_missing_agent_runtime_root(self, workspace: str | Path) -> bool:
        """已有工作区缺少 Runtime 工程时，从公开 Git 模板补齐 agent-runtime 根目录。"""

        root = Path(workspace).expanduser().resolve()
        destination = root / "agent-runtime"
        if destination.is_symlink():
            raise GitTemplateError("Agent Runtime 工程目录不能是符号链接。")
        pyproject = destination / "pyproject.toml"
        if pyproject.is_file() and not pyproject.is_symlink():
            return False
        if destination.exists():
            leftover = [
                path
                for path in destination.rglob("*")
                if path.is_file() and not path.is_symlink()
            ]
            if leftover:
                raise GitTemplateError(
                    "未找到有效的 Agent Runtime 工程：agent-runtime/pyproject.toml。"
                )
        repository_url = self._settings.template_git_agent_runtime_repository_url
        branch = self._settings.template_git_agent_runtime_branch
        if not repository_url or not branch:
            raise GitTemplateError("agent-runtime Git 模板地址或分支未配置。")
        with tempfile.TemporaryDirectory(
            prefix="xcodeagent-git-template-runtime-",
            ignore_cleanup_errors=True,
        ) as directory:
            source_root = Path(directory)
            self._clone_repository(
                root,
                source_root,
                target="agent-runtime",
                repository_url=repository_url,
                branch=branch,
            )
            cloned = source_root / "agent-runtime"
            if destination.exists():
                remove_managed_path(destination)
            shutil.copytree(cloned, destination, symlinks=False)
        if not pyproject.is_file() or pyproject.is_symlink():
            raise GitTemplateError(
                "未找到有效的 Agent Runtime 工程：agent-runtime/pyproject.toml。"
            )
        return True

    def _git_supplement_targets(
        self, missing_roots: frozenset[str]
    ) -> tuple[tuple[str, str, str], ...]:
        """只返回 Engine ZIP 无法提供、必须从公开 Git 补齐的 root。"""

        unexpected = missing_roots - _ENGINE_MANAGED_ROOTS - _GIT_SUPPLEMENT_ROOTS
        if unexpected:
            raise GitTemplateError(
                "Engine 模板 ZIP 缺少无法从 Git 补齐的 managed roots："
                + "、".join(sorted(unexpected))
            )
        repository_url = self._settings.template_git_agent_runtime_repository_url
        branch = self._settings.template_git_agent_runtime_branch
        if not repository_url or not branch:
            raise GitTemplateError("agent-runtime Git 模板地址或分支未配置。")
        return (
            (
                "agent-runtime",
                repository_url,
                branch,
            ),
        )

    def _repositories(
        self,
        requested_config: dict[str, Any],
        managed_roots: tuple[str, ...],
    ) -> tuple[tuple[str, str, str], ...]:
        """把当前权限能力和受管 roots 映射为唯一模板仓库集合。"""

        capabilities = requested_config.get("capabilities")
        if not isinstance(capabilities, dict):
            raise GitTemplateError("Git 模板请求缺少 capabilities 对象。")
        frontend_backend_branch = (
            "auth" if "authorization" in capabilities else "main"
        )
        repositories = [
            (
                "frontend",
                self._settings.template_git_frontend_repository_url,
                frontend_backend_branch,
            ),
            (
                "backend",
                self._settings.template_git_backend_repository_url,
                frontend_backend_branch,
            ),
        ]
        if "agent-runtime" in managed_roots:
            repositories.append(
                (
                    "agent-runtime",
                    self._settings.template_git_agent_runtime_repository_url,
                    self._settings.template_git_agent_runtime_branch,
                )
            )
        for target, repository_url, branch in repositories:
            if not repository_url or not branch:
                raise GitTemplateError(f"{target} Git 模板地址或分支未配置。")
        return tuple(repositories)

    def _clone_repository(
        self,
        workspace: Path,
        source_root: Path,
        *,
        target: str,
        repository_url: str,
        branch: str,
    ) -> dict[str, str]:
        """单次浅克隆一个模板并移除嵌套 Git 元数据。"""

        target_root = source_root / target
        started_at = time.monotonic()
        # 临时调试日志：定位现场问题后统一删除 temporary-bootstrap-debug 标记代码。
        logger.warning(
            "[temporary-bootstrap-debug] Git 模板开始拉取 target=%s branch=%s timeout_seconds=%s",
            target,
            branch,
            self._settings.template_git_clone_timeout_seconds,
        )
        try:
            result = workspace_process_registry.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--single-branch",
                    "--branch",
                    branch,
                    "--",
                    repository_url,
                    str(target_root),
                ],
                workspace=workspace,
                cwd=str(source_root),
                capture_output=True,
                text=True,
                timeout=self._settings.template_git_clone_timeout_seconds,
                check=False,
            )
        except Exception as exc:
            logger.error(
                "[temporary-bootstrap-debug] Git 模板拉取异常 target=%s branch=%s "
                "elapsed_seconds=%.3f error_type=%s detail=%s",
                target,
                branch,
                time.monotonic() - started_at,
                type(exc).__name__,
                _safe_git_diagnostic(
                    getattr(exc, "stderr", None) or getattr(exc, "stdout", None)
                ),
            )
            raise GitTemplateError(f"{target} Git 模板拉取失败。") from exc
        if result.returncode != 0:
            logger.error(
                "[temporary-bootstrap-debug] Git 模板拉取失败 target=%s branch=%s "
                "elapsed_seconds=%.3f returncode=%s detail=%s",
                target,
                branch,
                time.monotonic() - started_at,
                result.returncode,
                _safe_git_diagnostic(result.stderr or result.stdout),
            )
            raise GitTemplateError(f"{target} Git 模板拉取失败。")
        logger.warning(
            "[temporary-bootstrap-debug] Git 模板拉取完成 target=%s branch=%s elapsed_seconds=%.3f",
            target,
            branch,
            time.monotonic() - started_at,
        )
        try:
            revision_result = workspace_process_registry.run(
                ["git", "rev-parse", "HEAD"],
                workspace=workspace,
                cwd=str(target_root),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception as exc:
            raise GitTemplateError(f"{target} Git 模板提交无法确认。") from exc
        revision = str(revision_result.stdout or "").strip()
        if revision_result.returncode != 0 or not revision:
            raise GitTemplateError(f"{target} Git 模板提交无法确认。")
        git_dir = target_root / ".git"
        try:
            remove_managed_path(git_dir)
        except OSError:
            # Windows 可能短暂锁住只读 pack；元数据仍会在打包时被跳过。
            logger.warning("临时 Git 元数据无法立即删除，打包时将跳过 .git：target=%s", target)
        _reject_symbolic_links(target_root)
        if target == "agent-runtime" and (target_root / ".env").exists():
            raise GitTemplateError("agent-runtime Git 模板不能包含真实 .env 文件。")
        return {
            "repositoryUrl": repository_url,
            "branch": branch,
            "commitSha": revision,
        }


def _safe_git_diagnostic(value: object) -> str:
    """压平并脱敏 Git 诊断文本，避免临时日志泄露 URL 中的认证信息。"""

    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value or "")
    redacted = re.sub(r"(https?://)[^/@\s]+@", r"\1***@", text)
    compact = " ".join(redacted.split())
    return compact[:1000] or "<empty>"


def _template_state(
    requested_config: dict[str, Any],
    revisions: dict[str, dict[str, str]],
) -> TemplateStateV2:
    """用已拉取提交与请求能力构造当前唯一 V2 TemplateState。"""

    capabilities = requested_config.get("capabilities")
    if not isinstance(capabilities, dict):
        raise GitTemplateError("Git 模板请求缺少 capabilities 对象。")
    enabled: dict[str, dict[str, Any]] = {}
    for capability_id, definition in capabilities.items():
        if (
            not isinstance(capability_id, str)
            or not isinstance(definition, dict)
            or definition.get("enabled") is not True
            or not isinstance(definition.get("config"), dict)
        ):
            raise GitTemplateError("Git 模板请求包含无效 capability 定义。")
        enabled[capability_id] = {
            "enabled": True,
            "config": definition["config"],
        }
    canonical_revisions = json.dumps(
        revisions,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    revision = "git-" + hashlib.sha256(canonical_revisions).hexdigest()
    return TemplateStateV2.model_validate(
        {
            "schemaVersion": 2,
            "templateRevision": revision,
            "requested": enabled,
            "effective": enabled,
            "appliedAdditions": {},
        }
    )


def _iter_template_files(root: Path) -> Iterator[Path]:
    """遍历可打进 ZIP 的普通文件，跳过嵌套 Git 元数据。"""

    for path in sorted(root.rglob("*")):
        if ".git" in path.parts or not path.is_file() or path.is_symlink():
            continue
        yield path


def _package_file_roots(archive_path: Path) -> set[str]:
    """统计 Engine ZIP 中除 TemplateState 外已出现的顶层文件 root。"""

    roots: set[str] = set()
    with zipfile.ZipFile(archive_path) as package:
        for entry in package.infolist():
            if entry.is_dir() or entry.filename == _STATE_PATH:
                continue
            path = PurePosixPath(entry.filename)
            if path.parts:
                roots.add(path.parts[0])
    return roots


def _merge_archive(
    source_archive: Path,
    extra_roots: dict[str, Path],
    dest_archive: Path,
) -> None:
    """复制 Engine ZIP 全部条目，再写入 Git 补齐的普通文件。"""

    with zipfile.ZipFile(source_archive) as source, zipfile.ZipFile(
        dest_archive, "w", compression=zipfile.ZIP_DEFLATED
    ) as dest:
        existing = {name for name in source.namelist() if not name.endswith("/")}
        for info in source.infolist():
            if info.is_dir():
                dest.writestr(info.filename, b"")
                continue
            dest.writestr(info.filename, source.read(info.filename))
        for root_name, root in extra_roots.items():
            if not root.is_dir() or root.is_symlink():
                raise GitTemplateError(f"Git 模板缺少有效 {root_name} 根目录。")
            for path in _iter_template_files(root):
                arcname = (Path(root_name) / path.relative_to(root)).as_posix()
                if arcname in existing or arcname == _STATE_PATH:
                    raise GitTemplateError(f"Git 模板与 Engine ZIP 路径冲突：{arcname}")
                dest.write(path, arcname)


def _reject_symbolic_links(root: Path) -> None:
    """拒绝把模板仓库中的符号链接带入受管工作区。"""

    symbolic = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if ".git" not in path.parts and path.is_symlink()
    ]
    if symbolic:
        raise GitTemplateError(
            "Git 模板包含不允许的符号链接：" + "、".join(symbolic[:20])
        )


def _write_archive(
    archive_path: Path,
    source_root: Path,
    managed_roots: tuple[str, ...],
    state: TemplateStateV2,
) -> None:
    """把三端普通文件和唯一 TemplateState 写入临时 ZIP。"""

    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as package:
        for root_name in managed_roots:
            root = source_root / root_name
            if not root.is_dir() or root.is_symlink():
                raise GitTemplateError(f"Git 模板缺少有效 {root_name} 根目录。")
            for path in _iter_template_files(root):
                package.write(
                    path,
                    (Path(root_name) / path.relative_to(root)).as_posix(),
                )
        package.writestr(
            ".xcodeagent/template-state.json",
            json.dumps(
                state.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )


def _file_sha256(path: Path) -> str:
    """流式计算临时模板 ZIP 摘要，避免按包大小占用额外内存。"""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
