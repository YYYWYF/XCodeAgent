"""Server-owned 的首次 Workspace Bootstrap 编排服务。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.application_lifecycle import (
    begin_application_template_generation,
    complete_workspace_bootstrap,
)
from app.services.application_config import read_application_config
from app.services.repository_branch import push_workspace_branch
from app.services.workspace_bootstrap.coordinator import template_mutation_coordinator
from app.services.workspace_bootstrap.materializer import WorkspaceMaterializer
from app.services.workspace_bootstrap.models import ArchiveLimits, WorkspaceBootstrapError
from app.services.workspace_bootstrap.readiness import (
    WorkspaceTemplateStatus,
    classify_workspace_template,
    validate_workspace_bootstrap_readiness,
)
from app.services.workspace_bootstrap.requested_config import compile_template_requested_config
from app.services.workspace_bootstrap.run_log import TemplateRunLog
from app.services.workspace_bootstrap.template_engine_client import TemplateEngineClient
from app.services.workspace_bootstrap.template_generate import cleanup_template_download
from app.services.workspace_bootstrap.template_package import validate_template_package


class WorkspaceBootstrapService:
    """持有后台任务，保证 AG-UI 订阅断开不会取消实际 Bootstrap。"""

    def __init__(self, settings: Settings) -> None:
        """保存运行配置和按 Workspace 去重的后台任务表。"""

        self._settings = settings
        self._tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def trigger(self, workspace: str | Path) -> asyncio.Task[dict[str, Any]]:
        """创建或复用一个 Server-owned Bootstrap Task。"""

        root = Path(workspace).expanduser().resolve()
        key = str(root)
        async with self._lock:
            current = self._tasks.get(key)
            if current is not None and not current.done():
                return current
            task = asyncio.create_task(self._run(root), name=f"workspace-bootstrap:{root.name}")
            self._tasks[key] = task
            task.add_done_callback(
                lambda completed: self._clear_completed_task(key, completed)
            )
            return task

    def active_count(self) -> int:
        """让后台退出只观察本服务仍未退出的真实任务，不依赖磁盘状态推测。"""
        return sum(not task.done() for task in self._tasks.values())

    def _clear_completed_task(
        self,
        key: str,
        completed: asyncio.Task[dict[str, Any]],
    ) -> None:
        """只移除自身，避免完成回调误删随后创建的新一轮任务。"""

        if self._tasks.get(key) is completed:
            self._tasks.pop(key, None)

    async def _run(self, workspace: Path) -> dict[str, Any]:
        """为一次完整 Bootstrap 管理独立日志的失败保留与成功清理。"""

        run_log = TemplateRunLog.create(workspace)
        run_log.event(
            stage="bootstrap",
            event="bootstrap_started",
            message="开始模板初始化",
            workspacePath=str(workspace),
            targetDirectory=str(workspace),
            templateName="Template Engine V3 Generate",
            templateVersion="由返回 ZIP 的 TemplateState 确定",
        )
        try:
            result = await self._run_bootstrap(workspace, run_log)
        except BaseException as exc:
            run_log.finish_failure(exc)
            raise
        run_log.finish_success(
            final_path=str(workspace),
            reusedExistingTemplate=bool(result.get("reusedExistingTemplate", False)),
            repositoryBranchStatus=(result.get("repositoryBranch") or {}).get("status", "unknown"),
        )
        return result

    async def _run_bootstrap(
        self,
        workspace: Path,
        run_log: TemplateRunLog,
    ) -> dict[str, Any]:
        """编译请求、下载校验、事务物化，并确保失败写入 lifecycle。"""

        download_path: Path | None = None
        coordinator_started = False
        # 先拒绝非 TechnicalPlan-confirmed 的旧入口，避免下载后才发现 lifecycle 非法。
        with run_log.stage("lifecycle_precondition", "校验模板生成生命周期入口"):
            await asyncio.to_thread(begin_application_template_generation, workspace)
        with run_log.stage("mutation_coordination", "登记 Workspace 模板物化任务"):
            template_mutation_coordinator.begin_preparation(workspace)
        coordinator_started = True
        try:
            with run_log.stage("configuration_validation", "校验应用和 TechnicalPlan 配置"):
                requested_config = await asyncio.to_thread(
                    compile_template_requested_config,
                    workspace,
                    include_application_config=True,
                )
                application_config = await asyncio.to_thread(read_application_config, workspace)
                run_log.event(
                    stage="configuration_validation",
                    event="configuration_validated",
                    message="模板请求配置已校验",
                    applicationName=application_config.get("appName", ""),
                    configRevision=application_config.get("configRevision", "unknown"),
                    requestedConfigKeys=sorted(requested_config),
                    capabilityKeys=sorted((requested_config.get("capabilities") or {}).keys()),
                )
            source_revision = application_config.get("configRevision")
            with run_log.stage("preparation_guard", "确认模板准备任务未被取消"):
                template_mutation_coordinator.raise_if_preparation_cancelled(workspace)
            # 新迭代沿用已有工程：模板请求只由 application.json 派生，各迭代完全一致，
            # 因此已物化的模板本来就在正确的状态。重新下载会把应用累积的业务代码整体覆盖掉。
            with run_log.stage("workspace_template_classification", "检查工作区模板前置状态"):
                template_status = await asyncio.to_thread(
                    classify_workspace_template,
                    workspace,
                    requested_config=requested_config,
                )
                run_log.event(
                    stage="workspace_template_classification",
                    event="classification_completed",
                    message="工作区模板状态已判定",
                    templateStatus=template_status.value,
                )
                if template_status is WorkspaceTemplateStatus.STALE:
                    raise WorkspaceBootstrapError(
                        "工作区已有工程产物，但模板状态与应用当前配置不一致（或工程不完整）。"
                        "为避免覆盖已有代码，本次不会重新拉取模板；"
                        "请改用独立的项目目录重建应用，或先手工清理该工作区。"
                    )
            if template_status is WorkspaceTemplateStatus.READY:
                with run_log.stage("lifecycle_ready_commit", "确认已就绪模板的生命周期状态"):
                    lifecycle = await asyncio.to_thread(
                        complete_workspace_bootstrap,
                        workspace,
                        succeeded=True,
                        readiness_verified=True,
                    )
                # 沿用已有工程时也要确保远端分支存在：分支名可能是后补上的（例如新建应用
                # 时因故没写进 application.json），或者远端分支被删过。这里同样是非致命
                # 收尾，失败只报告不影响 Bootstrap 结果。
                with run_log.stage("repository_branch_push", "检查并推送应用远端分支"):
                    repository_branch = await asyncio.to_thread(push_workspace_branch, workspace)
                    run_log.event(
                        stage="repository_branch_push",
                        event="repository_branch_result",
                        message="远端分支收尾完成",
                        repositoryBranch=repository_branch,
                    )
                return {
                    "workspaceRoot": str(workspace),
                    "reusedExistingTemplate": True,
                    "lifecycle": lifecycle.model_dump(mode="json", by_alias=True),
                    "repositoryBranch": repository_branch,
                }
            client = TemplateEngineClient(
                base_url=self._settings.template_engine_base_url,
                connect_timeout=self._settings.template_engine_connect_timeout_seconds,
                read_timeout=self._settings.template_engine_read_timeout_seconds,
                max_package_bytes=self._settings.template_package_max_bytes,
            )
            with run_log.stage("template_engine_generate", "请求模板引擎并下载模板包"):
                download = await client.generate(requested_config, run_log=run_log)
            download_path = download.temporary_path
            with run_log.stage("preparation_guard", "确认模板包下载后未被取消"):
                template_mutation_coordinator.raise_if_preparation_cancelled(workspace)
            with run_log.stage(
                "template_package_validation",
                "校验模板 ZIP 文件、安全性及结构契约",
                archivePath=str(download_path),
                archiveExists=download_path.is_file(),
                archiveSizeBytes=download.size,
                sha256=download.sha256,
            ):
                package = await asyncio.to_thread(
                    validate_template_package,
                    download_path,
                    ArchiveLimits(
                        max_package_bytes=self._settings.template_package_max_bytes,
                        max_files=self._settings.template_package_max_files,
                        max_extracted_bytes=self._settings.template_package_max_extracted_bytes,
                    ),
                )
                run_log.event(
                    stage="template_package_validation",
                    event="template_contract_validated",
                    message="模板结构和 TemplateState 契约校验成功",
                    templateRevision=package.template_state.templateRevision,
                )
            with run_log.stage("preparation_guard", "确认模板包校验后未被取消"):
                template_mutation_coordinator.raise_if_preparation_cancelled(workspace)
            with run_log.stage("mutation_commit_fence", "进入不可中断的物化提交区"):
                template_mutation_coordinator.enter_commit_section(workspace)
            lifecycle: Any = None

            def assert_application_revision(root: Path) -> None:
                """在 Git baseline 前拒绝模板下载期间发生的配置修订竞争。"""

                current = read_application_config(root)
                if current.get("configRevision") != source_revision:
                    raise WorkspaceBootstrapError(
                        "Bootstrap 期间 application.json.configRevision 已变化，请重新确认 TechnicalPlan。"
                    )

            def commit_readiness(root: Path) -> None:
                """在物化事务内验证完整性，并把唯一 READY 写入与其绑定。"""

                nonlocal lifecycle
                with run_log.stage("readiness_validation", "检查正式产物、模板状态和 Git baseline"):
                    validate_workspace_bootstrap_readiness(
                        root,
                        requested_config=requested_config,
                    )
                assert_application_revision(root)
                with run_log.stage("lifecycle_ready_commit", "提交 Bootstrap READY 生命周期状态"):
                    lifecycle = complete_workspace_bootstrap(
                        root,
                        succeeded=True,
                        readiness_verified=True,
                    )
                    run_log.event(
                        stage="lifecycle_ready_commit",
                        event="lifecycle_ready_committed",
                        message="生命周期 READY 状态已提交",
                        lifecycleStage=lifecycle.initialization.stage.value,
                    )

            with run_log.stage("template_materialization", "事务化物化模板文件并完成 readiness"):
                await asyncio.to_thread(
                    WorkspaceMaterializer().materialize,
                    workspace=workspace,
                    archive_path=package.archive_path,
                    template_state=package.template_state,
                    before_baseline=assert_application_revision,
                    readiness=commit_readiness,
                    run_log=run_log,
                )
            if lifecycle is None:
                raise WorkspaceBootstrapError("Bootstrap 事务未提交 lifecycle READY。")
            # 基线已提交，此时才存在可推送的分支内容。必须在 materialize 事务**之外**执行：
            # 事务内异常会触发 journal.rollback() 撤销整个模板物化，网络推送绝不能进去。
            with run_log.stage("repository_branch_push", "检查并推送应用远端分支"):
                repository_branch = await asyncio.to_thread(push_workspace_branch, workspace)
                run_log.event(
                    stage="repository_branch_push",
                    event="repository_branch_result",
                    message="远端分支收尾完成",
                    repositoryBranch=repository_branch,
                )
            return {
                "workspaceRoot": str(workspace),
                "lifecycle": lifecycle.model_dump(mode="json", by_alias=True),
                "repositoryBranch": repository_branch,
            }
        except Exception as exc:
            with run_log.stage("lifecycle_failure_commit", "记录模板初始化失败生命周期状态"):
                lifecycle = await asyncio.to_thread(
                    complete_workspace_bootstrap,
                    workspace,
                    succeeded=False,
                    error_message=str(exc),
                )
            if isinstance(exc, WorkspaceBootstrapError):
                raise
            raise WorkspaceBootstrapError(str(exc)) from exc
        finally:
            try:
                if download_path is not None:
                    cleanup_template_download(download_path, run_log=run_log)
            finally:
                # 文件被杀毒软件等占用也必须释放协调器，避免后续执行一直被阻止。
                if coordinator_started:
                    with run_log.stage("mutation_coordination_release", "释放模板物化协调器"):
                        template_mutation_coordinator.finish(workspace)


def workspace_bootstrap_service(settings: Settings) -> WorkspaceBootstrapService:
    """为 FastAPI 进程创建唯一的 Bootstrap 服务实例。"""

    return WorkspaceBootstrapService(settings)
