"""提交身份：模板基线提交必须用仓库服务端认可的邮箱。

公司内 Gitee 会校验提交的 author email，不认就拒绝推送并报 `git002
Unauthorized email account cannot submit code`。而 `git init` 出来的新仓库没有任何
身份可用，早先固定写成 `DevAgentStudio <devagentstudio@local>`，必然被拒。

取值优先级（高到低）：

1. `DEVAGENTSTUDIO_GIT_AUTHOR_NAME` / `DEVAGENTSTUDIO_GIT_AUTHOR_EMAIL`：显式配置，
   公司内把它设成员工本人的公司邮箱最稳妥。
2. 本机已配置的全局 Git 身份（`git config --get user.email`）：公司机器上通常就是
   员工自己的公司邮箱 —— 既然本机能用 SourceTree/命令行推送，说明这份身份是被认可的。
3. 兜底占位身份：本机完全没配身份时仍要能提交，否则 Bootstrap 会直接失败。此时推送
   仍会被服务端拒绝，但报错会如实说明原因，而不是让整个模板初始化挂掉。
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger("uvicorn.error")

AUTHOR_NAME_ENV_VAR = "DEVAGENTSTUDIO_GIT_AUTHOR_NAME"
AUTHOR_EMAIL_ENV_VAR = "DEVAGENTSTUDIO_GIT_AUTHOR_EMAIL"

# 本机没有可用身份时的占位值。仅用于让 git 能提交，不代表被服务端认可。
_FALLBACK_NAME = "DevAgentStudio"
_FALLBACK_EMAIL = "devagentstudio@local"

_IDENTITY_TIMEOUT_SECONDS = 15


def _read_local_git_identity(key: str) -> str:
    """读取本机已配置的 Git 身份字段；未配置或读取失败时返回空串。

    显式限定 `--global` / `--system`，**不用**不带限定符的 `git config --get`：
    后者会连同"当前工作目录所在仓库"的本地配置一起读，而开发模式下后端的 cwd 正是
    本项目的 git 仓库 —— 项目本地若设过 user.email，会悄悄污染生成应用的提交身份。
    """

    for scope in ("--global", "--system"):
        try:
            completed = subprocess.run(
                ["git", "config", scope, "--get", key],
                capture_output=True,
                # 按字节捕获，避免 Windows 读取线程按系统编码解码中文 Git 身份失败。
                text=False,
                timeout=_IDENTITY_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode != 0:
            continue
        try:
            # 在当前线程严格解码 UTF-8；损坏的身份不替换为乱码后写入新仓库。
            value = (completed.stdout or b"").decode("utf-8").strip()
        except UnicodeDecodeError:
            logger.warning("Git 身份配置不是有效 UTF-8：scope=%s，key=%s。", scope, key)
            continue
        if completed.returncode == 0 and value:
            return value
    return ""


def resolve_commit_identity() -> tuple[str, str]:
    """解析提交身份，返回 `(name, email)`。

    只有显式配置才要求两项齐全；缺任一项就整体回落到本机身份，避免出现
    "配了邮箱却用了默认用户名"这种半截组合。
    """

    configured_name = os.getenv(AUTHOR_NAME_ENV_VAR, "").strip()
    configured_email = os.getenv(AUTHOR_EMAIL_ENV_VAR, "").strip()
    if configured_name and configured_email:
        return configured_name, configured_email

    local_name = _read_local_git_identity("user.name")
    local_email = _read_local_git_identity("user.email")
    if local_name and local_email:
        logger.info(
            "提交身份取自本机 Git 配置：%s <%s>。如需指定请设置 %s / %s。",
            local_name,
            local_email,
            AUTHOR_NAME_ENV_VAR,
            AUTHOR_EMAIL_ENV_VAR,
        )
        return local_name, local_email

    logger.warning(
        "本机没有可用的 Git 提交身份，本次提交使用占位身份 %s <%s>。"
        "公司内仓库会因邮箱未授权拒绝推送，请设置 %s / %s。",
        _FALLBACK_NAME,
        _FALLBACK_EMAIL,
        AUTHOR_NAME_ENV_VAR,
        AUTHOR_EMAIL_ENV_VAR,
    )
    return _FALLBACK_NAME, _FALLBACK_EMAIL


def apply_commit_identity(workspace: str | Path, run_git) -> tuple[str, str]:
    """把解析出的身份写入工作区的仓库级配置，返回实际写入的 `(name, email)`。

    写 `--local` 而不是依赖全局配置：工作区是应用自己的独立仓库，身份固定在仓库里
    才可预期 —— 用户之后改动全局 Git 配置也不会悄悄改变已有应用的提交身份。

    `run_git` 由调用方注入（`BootstrapGitManager._run`），避免这里再实现一遍
    进程执行与错误包装。
    """

    name, email = resolve_commit_identity()
    root = Path(workspace).expanduser().resolve()
    run_git(root, ["git", "config", "--local", "user.name", name])
    run_git(root, ["git", "config", "--local", "user.email", email])
    return name, email
