"""二次修改完成后的安全版本检查与 Git 提交服务。"""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.git_commit_message import ensure_board_number
from app.services.version_publish import PUBLISH_REMOTE_NAME
from app.services.workspace_process_registry import workspace_process_registry
from app.workspace.workspace import _is_sensitive_path


class VersionControlError(ValueError):
    """表示当前工作区不能安全完成版本控制动作。"""


class VersionControlFile(BaseModel):
    """描述 Git 工作区中的一个实际变更文件。"""

    model_config = ConfigDict(populate_by_name=True)

    path: str
    status: str
    index_status: str = Field(alias="indexStatus")
    worktree_status: str = Field(alias="worktreeStatus")
    staged: bool
    untracked: bool


class VersionControlSnapshot(BaseModel):
    """返回提交前重新读取的仓库事实和变更指纹。"""

    model_config = ConfigDict(populate_by_name=True)

    workspace_root: str = Field(alias="workspaceRoot")
    repository_root: str = Field(alias="repositoryRoot")
    branch: str
    head: str
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    dirty: bool
    has_staged_changes: bool = Field(alias="hasStagedChanges")
    files: list[VersionControlFile]
    requested_paths: list[str] = Field(alias="requestedPaths")
    eligible_paths: list[str] = Field(alias="eligiblePaths")
    # eligible_paths 里排除 .devagentstudio 平台产物后的业务代码变更。
    # 提交提醒（角标、返回首页确认、各档提醒）按这个口径计数，避免平台自身的
    # 状态流转被当成"用户改了代码"。提交弹窗与提交校验仍用 eligible_paths，
    # 所以产物照常可见、可勾选、可提交。
    code_paths: list[str] = Field(alias="codePaths")
    # HEAD 的提交信息首行；未建立基线时为空串。
    head_message: str = Field(alias="headMessage")
    unavailable_paths: list[str] = Field(alias="unavailablePaths")


class InspectVersionControlRequest(BaseModel):
    """校验一次只读 Git 状态检查请求。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["inspect"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1)
    requested_paths: list[str] = Field(alias="requestedPaths", min_length=1, max_length=200)


class InspectAllVersionControlRequest(BaseModel):
    """校验一次全量只读 Git 状态检查请求（里程碑提醒用，不限定文件范围）。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["inspect_all"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1)


class SummarizeVersionControlRequest(BaseModel):
    """校验一次"按本次改动生成变更说明"的请求。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["summarize"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1)


class SummarizeVersionControlResult(BaseModel):
    """返回模型生成的业务摘要；生成不出来时为空串。

    字段名刻意不用 `summary`：`preview_runtime_guard.record_product_interaction` 把
    `data["summary"]` 当作**对象**读取（`data["summary"]["status"]`，为 conversation 流程
    预留），动作数据里放字符串 `summary` 会让它 AttributeError 崩在流收尾处。
    """

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["summarize"] = "summarize"
    change_summary: str = Field(alias="changeSummary")


class CommitVersionControlRequest(BaseModel):
    """校验一次需要用户明确确认的精确文件提交请求。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["commit"]
    confirmed: bool
    workspace_root: str = Field(alias="workspaceRoot", min_length=1)
    requested_paths: list[str] = Field(alias="requestedPaths", min_length=1, max_length=200)
    selected_paths: list[str] = Field(alias="selectedPaths", min_length=1, max_length=200)
    expected_fingerprint: str = Field(
        alias="expectedFingerprint", pattern=r"^[a-f0-9]{64}$"
    )
    message: str = Field(min_length=1, max_length=200)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        """把提交信息限制为单行可审阅文本。"""

        normalized = value.strip()
        if "\n" in normalized or "\r" in normalized:
            raise ValueError("提交信息必须是单行文本。")
        return normalized


class VersionControlCommitResult(BaseModel):
    """返回成功提交及提交后的剩余工作区状态。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["commit"] = "commit"
    workspace_root: str = Field(alias="workspaceRoot")
    repository_root: str = Field(alias="repositoryRoot")
    commit_sha: str = Field(alias="commitSha", min_length=7)
    message: str
    committed_paths: list[str] = Field(alias="committedPaths")
    remaining_dirty: bool = Field(alias="remainingDirty")
    snapshot: VersionControlSnapshot


def inspect_version_control(request: InspectVersionControlRequest) -> VersionControlSnapshot:
    """重新读取独立工作区仓库状态，并只返回本轮请求关联的变更文件。"""

    workspace_root = _resolve_workspace_root(request.workspace_root)
    repository_root = _resolve_independent_repository_root(workspace_root)
    requested_paths = _normalize_requested_paths(workspace_root, request.requested_paths)
    status_bytes, status_files = _read_status(repository_root)
    status_by_path = {item.path: item for item in status_files}
    eligible_paths = [path for path in requested_paths if path in status_by_path]
    unavailable_paths = [path for path in requested_paths if path not in status_by_path]
    head = _read_head(repository_root)
    branch = _read_branch(repository_root)
    fingerprint = _build_fingerprint(
        repository_root,
        workspace_root,
        head,
        status_bytes,
        status_files,
    )
    return VersionControlSnapshot(
        workspaceRoot=str(workspace_root),
        repositoryRoot=str(repository_root),
        branch=branch,
        head=head,
        fingerprint=fingerprint,
        dirty=bool(status_files),
        hasStagedChanges=any(item.staged for item in status_files),
        files=[status_by_path[path] for path in eligible_paths],
        requestedPaths=requested_paths,
        eligiblePaths=eligible_paths,
        codePaths=_business_code_paths(eligible_paths),
        headMessage=_read_head_message(repository_root),
        unavailablePaths=unavailable_paths,
    )


def inspect_all_version_control(request: InspectAllVersionControlRequest) -> VersionControlSnapshot:
    """读取工作区全部 Git 变更，不限定文件范围（里程碑提醒用）。"""

    workspace_root = _resolve_workspace_root(request.workspace_root)
    repository_root = _resolve_independent_repository_root(workspace_root)
    status_bytes, status_files = _read_status(repository_root)
    all_paths = [item.path for item in status_files]
    head = _read_head(repository_root)
    branch = _read_branch(repository_root)
    fingerprint = _build_fingerprint(
        repository_root,
        workspace_root,
        head,
        status_bytes,
        status_files,
    )
    return VersionControlSnapshot(
        workspaceRoot=str(workspace_root),
        repositoryRoot=str(repository_root),
        branch=branch,
        head=head,
        fingerprint=fingerprint,
        dirty=bool(status_files),
        hasStagedChanges=any(item.staged for item in status_files),
        files=status_files,
        requestedPaths=all_paths,
        eligiblePaths=all_paths,
        codePaths=_business_code_paths(all_paths),
        headMessage=_read_head_message(repository_root),
        unavailablePaths=[],
    )


def commit_version_control(
    request: CommitVersionControlRequest,
) -> VersionControlCommitResult:
    """在指纹和暂存区复验通过后，仅提交用户明确选择的文件。"""

    if not request.confirmed:
        raise VersionControlError("提交代码前需要用户明确确认。")

    inspect_request = InspectVersionControlRequest(
        action="inspect",
        workspaceRoot=request.workspace_root,
        requestedPaths=request.requested_paths,
    )
    snapshot = inspect_version_control(inspect_request)
    if not hmac.compare_digest(snapshot.fingerprint, request.expected_fingerprint):
        raise VersionControlError("工作区已发生变化，请重新审阅后再提交。")
    if snapshot.head == "UNBORN":
        raise VersionControlError("当前仓库还没有基线提交，不能作为二次修改直接提交。")
    if snapshot.has_staged_changes:
        raise VersionControlError("检测到已有暂存修改，请先处理暂存区后再提交。")

    workspace_root = Path(snapshot.workspace_root)
    repository_root = Path(snapshot.repository_root)
    selected_paths = _normalize_requested_paths(workspace_root, request.selected_paths)
    eligible_paths = set(snapshot.eligible_paths)
    if any(path not in eligible_paths for path in selected_paths):
        raise VersionControlError("所选文件已不属于当前可提交变更，请重新审阅。")

    # 只对业务代码做空白预检。`diff --check -- <无路径>` 会退化成检查全量，
    # 所以没有业务代码时必须整个跳过，否则"只提交产物"的场景仍会被产物自己挡住
    # （设计版本提醒提交的就是清一色 .devagentstudio 文件）。
    checked_paths = _business_paths(selected_paths)
    if checked_paths:
        _run_git_checked(
            repository_root,
            ["diff", "--check", "--", *checked_paths],
            "所选文件未通过空白错误检查",
        )
    _run_git_checked(
        repository_root,
        ["add", "--", *selected_paths],
        "无法暂存所选文件",
    )
    try:
        if checked_paths:
            _run_git_checked(
                repository_root,
                ["diff", "--cached", "--check", "--", *checked_paths],
                "暂存内容未通过提交前检查",
            )
        staged_paths = _read_staged_paths(repository_root)
        if set(staged_paths) != set(selected_paths):
            raise VersionControlError("暂存内容与所选文件不一致，已停止提交。")
        _run_git_checked(
            repository_root,
            # 统一补看板编号：这条路径包含验收阶段的静默自动保存，用户看不到提交信息，
            # 所以不能依赖前端默认值带编号。
            ["commit", "-m", ensure_board_number(request.message)],
            "Git 提交失败",
            timeout=60,
        )
    except Exception:
        _unstage_selected_paths(repository_root, selected_paths)
        raise

    commit_sha = _run_git_checked(
        repository_root,
        ["rev-parse", "HEAD"],
        "无法读取提交结果",
    ).strip()
    after_snapshot = inspect_version_control(inspect_request)
    return VersionControlCommitResult(
        workspaceRoot=str(workspace_root),
        repositoryRoot=str(repository_root),
        commitSha=commit_sha,
        message=request.message,
        committedPaths=selected_paths,
        remainingDirty=after_snapshot.dirty,
        snapshot=after_snapshot,
    )


# 生成的摘要上限，与前端「变更说明」输入框的 maxLength 同一口径。
_SUMMARY_MAX_CHARS = 200
# 喂给模型的文件清单条数上限：一轮开发可能动几百个文件，全量塞进提示词只会稀释信号。
_SUMMARY_MAX_FILES = 60
# 业务产物摘要里每类条目（页面 / 接口 / 实体）的条数上限。
_SUMMARY_MAX_BUSINESS_ITEMS = 24

_SUMMARY_SYSTEM_PROMPT = (
    "你是研发交付助手。根据给定的『本轮业务产物』与『本次代码改动清单』，"
    "写一条面向业务人员的变更说明，用于 Git 提交信息。要求："
    "1) 用业务语言描述做了什么，不要出现文件路径、类名、函数名；"
    "2) 一到三句话，中文，不分行、不加编号或 Markdown；"
    f"3) 总长度不超过 {_SUMMARY_MAX_CHARS} 个字符；"
    "4) 只输出这条说明本身，不要任何前后缀、解释或引号。"
)


async def summarize_version_control(
    request: SummarizeVersionControlRequest,
) -> SummarizeVersionControlResult:
    """按本次推送会带上的改动生成一条业务变更说明，供提交弹窗预填。

    注意口径是「本次推送会带上什么」，**不是**「工作区有什么未提交改动」：验收阶段
    的 `AcceptanceCommitDock` 已经把这一轮的代码自动提交了，用户点「提交并推送」时
    工作区通常是干净的，改动都在本地提交里（见 `_read_publish_base`）。

    生成只是便利：读不到仓库、没有改动、产物缺失或模型调用失败时一律返回空串，
    由前端保持空框让用户自己写。这里绝不抛错 —— 一个填默认值的动作不该把用户挡在
    提交弹窗外。
    """

    try:
        workspace_root = _resolve_workspace_root(request.workspace_root)
        repository_root = _resolve_independent_repository_root(workspace_root)
        _status_bytes, status_files = _read_status(repository_root)
    except (VersionControlError, OSError):
        return SummarizeVersionControlResult(changeSummary="")

    business_digest = _read_business_digest(workspace_root)
    change_digest = _read_change_digest(repository_root, status_files)
    # 没有业务改动就没有可说明的内容，不必打扰模型。
    if not change_digest:
        return SummarizeVersionControlResult(changeSummary="")

    prompt = (
        f"【本轮业务产物】\n{business_digest or '（未找到已确认的规划产物）'}\n\n"
        f"【本次代码改动清单】\n{change_digest}"
    )

    try:
        summary = await _generate_summary_text(prompt)
    except Exception:
        # 模型侧任何异常都降级为空串，交给用户手写。
        return SummarizeVersionControlResult(changeSummary="")
    return SummarizeVersionControlResult(changeSummary=_normalize_summary(summary))


async def _generate_summary_text(prompt: str) -> str:
    """调用当前配置的聊天模型，取回纯文本摘要。

    模型相关导入必须留在函数内：`app.agents` 会经 `app.agents.__init__` 拉起整棵 agent
    树，其中 `app.services.development_review_files` 又反向 import 本模块，写成模块级
    导入会构成循环（本模块被最先导入时直接 ImportError）。代价是测试要 patch 源头
    `app.agents.model_factory.create_chat_model`，而不是本模块属性。

    取文本必须走 `_coerce_content_text`，不能自己拼 content：推理模型（本项目实际用的
    glm-5.2 就是）会把思考过程作为 `{"type": "thinking", ...}` block 混在 content 列表里，
    与真正的正文并列。自己按 `item["text"]` 取值会把这些 block 读成空串、把正文整个漏掉，
    表现为"模型明明答了，摘要却是空的"。
    """

    from langchain_core.messages import HumanMessage, SystemMessage

    from app.agents.messages import _coerce_content_text
    from app.agents.model_factory import create_chat_model
    from app.config import Settings

    model = create_chat_model(Settings.from_env())
    response = await model.ainvoke(
        [SystemMessage(content=_SUMMARY_SYSTEM_PROMPT), HumanMessage(content=prompt)]
    )
    return _coerce_content_text(getattr(response, "content", response))


def _normalize_summary(value: str) -> str:
    """把模型输出收敛成单行、无引号、不超长的提交说明。"""

    text = " ".join((value or "").split())
    text = text.strip().strip('"').strip("'").strip()
    if len(text) > _SUMMARY_MAX_CHARS:
        text = text[:_SUMMARY_MAX_CHARS].rstrip()
    return text


def _read_publish_base(repository_root: Path) -> str | None:
    """确定「本次推送的基线」——远端分支已经到哪，就从哪里算起。

    发布流程把 `HEAD:refs/heads/<branch>` 强推上去（见 `version_publish.publish_version`），
    所以「本次改动」= 本地有、远端没有的那部分。三种情况依次尝试：

    1. 分支配了上游（`@{u}`）：直接用。
    2. **发布过**：发布用的 remote 是 `devagentstudio-publish`，它按显式 refspec 推送，
       **不会**配置 upstream（实测 `@{u}` 必然报 "no upstream configured"），但 `git push`
       会更新远端跟踪引用，所以从 `refs/remotes/devagentstudio-publish/*` 取。这是第二次
       及以后发布的正常路径 —— 只差这一点会让第二次发布把历史全量重算一遍。
    3. **从未发布**：没有可比对象，以根提交为基线 —— 即"这个应用从模板初始化以来的全部
       内容"，正是首次推送会整体交付的东西。

    取不到基线时返回 None，调用方退化为只看工作区未提交改动。
    """

    upstream = _run_git(
        repository_root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
    )
    if upstream.returncode == 0 and upstream.stdout.strip():
        return upstream.stdout.strip()

    published = _run_git(
        repository_root,
        [
            "for-each-ref",
            "--format=%(refname:short)",
            f"refs/remotes/{PUBLISH_REMOTE_NAME}/",
        ],
    )
    if published.returncode == 0 and published.stdout.strip():
        return published.stdout.strip().splitlines()[0]

    roots = _run_git(repository_root, ["rev-list", "--max-parents=0", "HEAD"])
    if roots.returncode == 0 and roots.stdout.strip():
        # 多根（合并过历史）时取最早那一个，保证基线足够靠前。
        return roots.stdout.strip().splitlines()[-1]
    return None


def _read_change_digest(
    repository_root: Path, status_files: list[VersionControlFile]
) -> str:
    """把「本次推送会带上」的业务改动整理成"路径 +N -M"清单。

    两条来源合起来才是完整的推送内容：

    - `git diff --numstat <基线>`：覆盖基线之后的**已提交**改动与**未提交**改动。
      用 `workspace_process_registry` 这个仓库统一的 git 入口执行。
    - 未跟踪文件：不在 diff 里，按状态补「（新增）」。新建的整页代码全靠这一条，
      漏掉它新建页面在提示词里会完全看不见。

    最后按 `_business_paths` 滤掉 `.devagentstudio` 平台产物：它们会随版本一起提交，
    但不是业务内容。实测一个只有 Hello World 的应用，验收提交里 16 个文件有 14 个是
    平台产物（build-runs、recovery sqlite、reports），不过滤的话模型会对着构建日志写摘要。

    没有任何业务改动时返回空串，调用方据此跳过模型调用。
    """

    base = _read_publish_base(repository_root)
    arguments = ["diff", "--numstat"] + ([base] if base else [])
    completed = _run_git(repository_root, arguments, timeout=30)

    stats: dict[str, tuple[int, int]] = {}
    if completed.returncode == 0:
        for line in completed.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added, removed, path = parts
            # 二进制文件给的是 "-"，按 0 计，避免 int() 抛错。
            stats[path] = (
                int(added) if added.isdigit() else 0,
                int(removed) if removed.isdigit() else 0,
            )

    # 未跟踪文件补进来（diff 不包含它们）。
    untracked = [item.path for item in status_files if item.untracked]
    business_paths = _business_paths(list(stats) + untracked)
    if not business_paths:
        return ""

    listed = business_paths[:_SUMMARY_MAX_FILES]
    lines: list[str] = []
    for path in listed:
        if path in stats:
            lines.append(f"  {path}  +{stats[path][0]} -{stats[path][1]}")
        else:
            lines.append(f"  {path}（新增）")
    if len(business_paths) > _SUMMARY_MAX_FILES:
        lines.append(f"  …另有 {len(business_paths) - _SUMMARY_MAX_FILES} 个文件未列出")

    total_added = sum(stats[path][0] for path in business_paths if path in stats)
    total_removed = sum(stats[path][1] for path in business_paths if path in stats)
    lines.append(
        f"  共 {len(business_paths)} 个文件，+{total_added} / -{total_removed}"
    )
    return "\n".join(lines)


def _read_business_digest(workspace_root: Path) -> str:
    """从已确认的规划产物里取出业务名清单（页面 / 接口 / 实体）。

    刻意比 `iteration_service._build_iteration_section` 窄：那个函数是给 AGENTS.md 用的
    完整摘要（含 application.json、UI 设计稿等），喂给一条 200 字说明属于浪费。这里只要
    业务名，且条数封顶，保证提示词有界。
    """

    artifact_dir = workspace_root / ".devagentstudio"
    lines: list[str] = []

    product_plan = _load_artifact_json(artifact_dir / "plans" / "product-plan.json")
    pages = product_plan.get("pages") if isinstance(product_plan, dict) else None
    if isinstance(pages, list) and pages:
        names: list[str] = []
        for page in pages[:_SUMMARY_MAX_BUSINESS_ITEMS]:
            if not isinstance(page, dict):
                continue
            name = str(page.get("name") or page.get("pageId") or "").strip()
            description = str(page.get("description") or "").strip()
            if name:
                names.append(f"{name}（{description}）" if description else name)
        if names:
            lines.append("页面：" + "、".join(names))

    tech_plan = _load_artifact_json(artifact_dir / "plans" / "technical-plan.json")
    if isinstance(tech_plan, dict):
        endpoints = tech_plan.get("endpoints")
        if isinstance(endpoints, list) and endpoints:
            labels: list[str] = []
            for endpoint in endpoints[:_SUMMARY_MAX_BUSINESS_ITEMS]:
                if not isinstance(endpoint, dict):
                    continue
                method = str(endpoint.get("method") or "").strip()
                path = str(endpoint.get("path") or "").strip()
                summary = str(
                    endpoint.get("summary") or endpoint.get("name") or ""
                ).strip()
                label = " ".join(part for part in (method, path) if part)
                if label:
                    labels.append(f"{label}（{summary}）" if summary else label)
            if labels:
                lines.append("接口：" + "、".join(labels))

        entities = tech_plan.get("entities")
        if isinstance(entities, list) and entities:
            entity_names = [
                str(entity.get("name") or entity.get("id") or "").strip()
                for entity in entities[:_SUMMARY_MAX_BUSINESS_ITEMS]
                if isinstance(entity, dict)
            ]
            entity_names = [name for name in entity_names if name]
            if entity_names:
                lines.append("实体：" + "、".join(entity_names))

    return "\n".join(lines)


def _load_artifact_json(path: Path) -> dict[str, object] | None:
    """安全加载规划产物 JSON，缺失或损坏时返回 None（摘要降级，不报错）。"""

    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _resolve_workspace_root(value: str) -> Path:
    """解析并校验版本控制动作指定的工作区根目录。"""

    root = Path(value).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise VersionControlError("工作目录不存在或不是文件夹。")
    return root


def _resolve_independent_repository_root(workspace_root: Path) -> Path:
    """要求工作区本身就是 Git 根目录，避免误提交父目录仓库。"""

    if not shutil.which("git"):
        raise VersionControlError("未检测到 Git，无法检查版本状态。")
    completed = _run_git(
        workspace_root,
        ["rev-parse", "--show-toplevel"],
    )
    if completed.returncode != 0:
        raise VersionControlError("当前工作目录还不是 Git 仓库。")
    repository_root = Path(completed.stdout.strip()).resolve()
    if repository_root != workspace_root:
        raise VersionControlError("工作区必须使用自己的 Git 仓库，不能提交到父目录仓库。")
    return repository_root


def _normalize_requested_paths(workspace_root: Path, values: list[str]) -> list[str]:
    """规范化、去重并拒绝越界或敏感的提交候选路径。"""

    normalized_paths: list[str] = []
    for value in values:
        raw_value = value.replace("\\", "/").strip()
        raw_path = Path(raw_value).expanduser()
        if raw_path.is_absolute():
            try:
                raw_value = raw_path.resolve(strict=False).relative_to(workspace_root).as_posix()
            except ValueError as exc:
                raise VersionControlError(f"变更文件超出工作目录：{value}") from exc
        path = PurePosixPath(raw_value)
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
            or any(character in raw_value for character in ("\x00", "\r", "\n"))
        ):
            raise VersionControlError(f"变更文件路径无效：{value}")
        normalized = path.as_posix()
        target = (workspace_root / normalized).resolve(strict=False)
        try:
            target.relative_to(workspace_root)
        except ValueError as exc:
            raise VersionControlError(f"变更文件超出工作目录：{value}") from exc
        if ".git" in path.parts or _is_sensitive_path(target):
            raise VersionControlError(f"敏感或内部文件不能提交：{normalized}")
        if normalized not in normalized_paths:
            normalized_paths.append(normalized)
    if not normalized_paths:
        raise VersionControlError("至少需要一个变更文件。")
    return normalized_paths


_RUNTIME_ARTIFACT_PREFIXES = (
    ".devagentstudio/runtime/",
    ".devagentstudio/cache/",
    ".devagentstudio/checkpoints/",
)


def _is_runtime_artifact_path(path: str) -> bool:
    """判断 git status 路径是否属于 .devagentstudio 运行时产物。"""

    normalized = path.replace("\\", "/").lstrip("/")
    return any(normalized.startswith(prefix) for prefix in _RUNTIME_ARTIFACT_PREFIXES)


_PLATFORM_ARTIFACT_PREFIX = ".devagentstudio/"


def _is_platform_artifact_path(path: str) -> bool:
    """判断路径是否属于 .devagentstudio 平台产物（规划文档、状态快照、报告）。

    与 `_is_runtime_artifact_path` 的区别：运行时产物（日志/缓存/checkpoint）**根本不进**
    提交候选；平台产物要进版本、要能被追溯，只是**不计入"用户改了代码"的提醒口径**。
    """

    return path.replace("\\", "/").lstrip("/").startswith(_PLATFORM_ARTIFACT_PREFIX)


def _business_code_paths(eligible_paths: list[str]) -> list[str]:
    """从可提交文件里筛出业务代码，供提交提醒计数使用。"""

    return [path for path in eligible_paths if not _is_platform_artifact_path(path)]


def _business_paths(paths: list[str]) -> list[str]:
    """筛出参与提交前空白检查的路径：排除 `.devagentstudio` 平台产物。

    空白检查是为了在提交前拦住手写代码里的疏忽（文档 §5.2 的确定性预检）。但
    `.devagentstudio` 下的规划文档、状态快照与 AGENTS.md 都是**平台自己生成**的，
    用户改不了也不该为它们负责 —— 平台生成的内容触发平台自己的门禁、反过来挡住
    用户提交，是纯粹的误伤（AGENTS.md 曾因 `', '.join` 产出悬空逗号而触发过）。

    生成侧已按行清理行尾空白（见 iteration_service._build_iteration_section）；
    这里是第二层保险，同时让**已存在**的旧产物不再卡住提交。

    调用方必须处理返回空列表的情形：`git diff --check --` 不带路径会退化成检查
    全量，反而把被排除的产物又检查回来。
    """

    return [path for path in paths if not _is_platform_artifact_path(path)]


def _read_status(repository_root: Path) -> tuple[bytes, list[VersionControlFile]]:
    """使用 NUL 分隔的 porcelain 输出读取全部暂存、未暂存和未跟踪文件。"""

    completed = workspace_process_registry.run(
        [
            "git",
            "-c",
            "core.quotepath=false",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
        workspace=repository_root,
        cwd=str(repository_root),
        capture_output=True,
        check=False,
        timeout=15,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise VersionControlError(f"无法读取 Git 状态：{detail or '未知错误'}")

    records = completed.stdout.split(b"\0")
    files: list[VersionControlFile] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        status_code = record[:2].decode("ascii", errors="replace")
        path = record[3:].decode("utf-8", errors="replace")
        # 跳过 .devagentstudio 下的运行时产物，避免日志、缓存和 checkpoint
        # 污染提交候选列表与提交前空白检查。
        if _is_runtime_artifact_path(path):
            if status_code[0] in {"R", "C"} and index < len(records):
                index += 1
            continue
        index_status, worktree_status = status_code[0], status_code[1]
        files.append(
            VersionControlFile(
                path=path,
                status=status_code,
                indexStatus=index_status,
                worktreeStatus=worktree_status,
                staged=index_status not in {" ", "?"},
                untracked=status_code == "??",
            )
        )
        if index_status in {"R", "C"} and index < len(records):
            index += 1
    return completed.stdout, files


def _read_head(repository_root: Path) -> str:
    """读取当前 HEAD；未建立基线时返回稳定占位值。"""

    completed = _run_git(repository_root, ["rev-parse", "HEAD"])
    return completed.stdout.strip() if completed.returncode == 0 else "UNBORN"


def _read_head_message(repository_root: Path) -> str:
    """读取 HEAD 的提交信息首行。

    界面用它说明"当前保存的是什么" —— 自动提交（模板 baseline、验收）由平台发起，
    用户没有参与写信息，所以更要把它显示出来，否则那个 commit 对用户是黑盒。
    未建立基线时返回空串。
    """

    completed = _run_git(repository_root, ["log", "-1", "--format=%s"])
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _read_branch(repository_root: Path) -> str:
    """读取当前分支名称，并为 detached HEAD 提供明确文案。"""

    completed = _run_git(repository_root, ["branch", "--show-current"])
    branch = completed.stdout.strip() if completed.returncode == 0 else ""
    return branch or "detached HEAD"


def _build_fingerprint(
    repository_root: Path,
    workspace_root: Path,
    head: str,
    status_bytes: bytes,
    status_files: list[VersionControlFile],
) -> str:
    """按工作区、HEAD、差异内容和未跟踪文件元数据生成并发复验指纹。"""

    digest = hashlib.sha256()
    digest.update(str(workspace_root).encode("utf-8"))
    digest.update(b"\0")
    digest.update(head.encode("ascii", errors="replace"))
    digest.update(b"\0")
    digest.update(status_bytes)
    for arguments in (["diff", "--binary"], ["diff", "--cached", "--binary"]):
        completed = workspace_process_registry.run(
            ["git", *arguments],
            workspace=repository_root,
            cwd=str(repository_root),
            capture_output=True,
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            raise VersionControlError("无法生成当前 Git 变更指纹。")
        digest.update(b"\0")
        digest.update(hashlib.sha256(completed.stdout).digest())
    for item in status_files:
        if not item.untracked:
            continue
        target = repository_root / item.path
        try:
            file_stat = target.stat()
        except OSError as exc:
            raise VersionControlError("未跟踪文件在状态检查期间发生变化，请重试。") from exc
        digest.update(b"\0")
        digest.update(item.path.encode("utf-8"))
        digest.update(f":{file_stat.st_size}:{file_stat.st_mtime_ns}".encode("ascii"))
    return digest.hexdigest()


def _read_staged_paths(repository_root: Path) -> list[str]:
    """读取提交前暂存区的精确文件集合。"""

    completed = workspace_process_registry.run(
        ["git", "diff", "--cached", "--name-only", "-z"],
        workspace=repository_root,
        cwd=str(repository_root),
        capture_output=True,
        check=False,
        timeout=15,
    )
    if completed.returncode != 0:
        raise VersionControlError("无法复核暂存文件。")
    return [
        item.decode("utf-8", errors="replace")
        for item in completed.stdout.split(b"\0")
        if item
    ]


def _unstage_selected_paths(repository_root: Path, selected_paths: list[str]) -> None:
    """提交失败时只还原本轮创建的暂存状态，不触碰工作区内容。"""

    workspace_process_registry.run(
        ["git", "restore", "--staged", "--", *selected_paths],
        workspace=repository_root,
        cwd=str(repository_root),
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )


def _run_git(
    repository_root: Path,
    arguments: list[str],
    *,
    timeout: int = 15,
) -> subprocess.CompletedProcess[str]:
    """以固定参数和超时执行无 shell 的 Git 子命令。"""

    return workspace_process_registry.run(
        ["git", *arguments],
        workspace=repository_root,
        cwd=str(repository_root),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _run_git_checked(
    repository_root: Path,
    arguments: list[str],
    error_prefix: str,
    *,
    timeout: int = 15,
) -> str:
    """执行必须成功的 Git 子命令，并把错误转换为稳定业务异常。"""

    completed = _run_git(repository_root, arguments, timeout=timeout)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        raise VersionControlError(f"{error_prefix}：{detail}")
    return completed.stdout
