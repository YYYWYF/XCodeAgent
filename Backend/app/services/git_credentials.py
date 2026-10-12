"""Git 远端凭证来源：公司外读 .env，公司内交给本机 git 凭证助手。

远端推送需要认证，凭证有两种来源，用 DEVAGENTSTUDIO_GIT_CREDENTIAL_SOURCE 切换：

- `env`（默认）：读 DEVAGENTSTUDIO_GIT_USERNAME / DEVAGENTSTUDIO_GIT_TOKEN，拼成
  `https://用户名:令牌@主机/路径` 交给 git。公司外使用，凭证随 .env 分发。
- `helper`：不注入任何凭证，用仓库原始地址调用 git，认证交给本机 git 凭证助手
  （Windows 凭据管理器 / macOS 钥匙串）。公司内使用 —— 员工本机用 SourceTree 或
  命令行登录一次即可，仓库权限由 git 服务端按员工身份校验，应用不接触凭证。

推送路径上的两处调用方（services/repository_branch.py、services/version_publish.py）
共用本模块，避免各自写一份凭证判断。
"""

from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger("uvicorn.error")

CREDENTIAL_SOURCE_ENV_VAR = "DEVAGENTSTUDIO_GIT_CREDENTIAL_SOURCE"

# env：从 .env 注入凭证；helper：交给本机 git 凭证助手。
_SOURCE_ENV = "env"
_SOURCE_HELPER = "helper"
_VALID_SOURCES = frozenset({_SOURCE_ENV, _SOURCE_HELPER})

# 完全没有可用凭证：git 连用户名/密码都拿不到。Windows 上 Git Credential Manager 的
# 典型输出是 "could not read Username ... terminal prompts disabled"。
_NO_CREDENTIAL_MARKERS = (
    "could not read username",
    "could not read password",
    "terminal prompts disabled",
)

# 认证被拒：凭证拿到了但服务端不接受（凭证失效，或账号没有该仓库权限）。
# 注意 "access denied" 也带 "authentication failed"，两者同属这一类，不要归到"没有凭证"。
_REJECTED_MARKERS = (
    "authentication failed",
    "invalid username or password",
    "access denied",
    "permission denied",
    "permission to",
    "forbidden",
    "returned error: 401",
    "returned error: 403",
)

# 公司内 Gitee 的 policy 校验错误码。这类拒绝会给出精确原因，比"服务端拒绝"这句
# 笼统提示有用得多，所以放在通用的 _SERVER_REJECTED_MARKERS 之前优先匹配。
#
# git002：提交的 author email 未被仓库授权。取值见 services/git_identity.py。
_EMAIL_UNAUTHORIZED_MARKERS = (
    "unauthorized email account",
    "git002",
)

# git017：分支名不符合仓库白名单（只允许字母、数字、下划线）。
_BRANCH_NAME_POLICY_MARKERS = (
    "branch name only allow",
    "git017",
)

# 远端分支已存在且与本地历史分叉（非快进）。
#
# 模板基线是 `git init` 出来的**全新历史**，与远端既有分支没有共同祖先，所以只要远端
# 已存在同名分支，普通推送必然被拒；即使确认过覆盖、带了 --force，服务端也可能仍以
# "denying non-fast-forward ... you should pull first" 拒绝（仓库保护策略）。
# 这不是凭证或提交信息问题，重试也不会成功，必须先把远端那条分支处理掉。
_NON_FAST_FORWARD_MARKERS = (
    "non-fast-forward",
    "you should pull first",
    "denying non-fast-forward",
)

# 推送被服务端钩子拒绝。**不能断言原因** —— pre-receive hook 既可能校验提交信息规范
# （公司内仓库常见要求：`Z000000-0000 #comment feat：说明`），也可能校验账号写权限。
# git 输出 `! [remote rejected] ... (pre-receive hook declined)`，真正的理由在服务端
# 自己打的 `remote:` 行里，所以提示只说明"服务端拒绝"，把原话留给用户看。
_SERVER_REJECTED_MARKERS = (
    "pre-receive hook declined",
    "remote rejected",
    "hook declined",
)

# 仓库不可见：内部 Gitee 与 GitHub 对无权限的仓库常直接回 404，避免泄露仓库是否存在，
# 所以"找不到"既可能是没权限也可能是真不存在，提示要同时覆盖两种可能。
_NOT_FOUND_MARKERS = (
    "repository not found",
    "returned error: 404",
    "not found",
)


def credential_source() -> str:
    """读取凭证来源；未配置或取值非法时回落到 env（保持原有行为）。"""

    raw = os.getenv(CREDENTIAL_SOURCE_ENV_VAR, "").strip().lower()
    if not raw:
        return _SOURCE_ENV
    if raw not in _VALID_SOURCES:
        logger.warning(
            "%s 取值非法（%s），按 %s 处理。可选值：env、helper。",
            CREDENTIAL_SOURCE_ENV_VAR,
            raw,
            _SOURCE_ENV,
        )
        return _SOURCE_ENV
    return raw


def uses_local_credentials() -> bool:
    """是否把认证交给本机 git 凭证助手（公司内模式）。"""

    return credential_source() == _SOURCE_HELPER


def git_env() -> dict[str, str]:
    """构造 git 子进程环境：禁用交互式提问。

    应用由 Electron 拉起、没有 TTY，凭证缺失时 git 若停下来等输入会把整个流程挂住
    （Windows 上 Git Credential Manager 还可能弹出登录窗）。禁用提问后 git 立即以
    明确错误返回，再由 git_failure_detail 转成用户能照做的提示。
    """

    return {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def credential_failure_hint(detail: str) -> str:
    """把 git 的认证类 stderr 转成可操作的中文提示；非认证类返回空串。

    提示内容取决于凭证来源：helper 模式下要引导用户去本机登录，env 模式下则要引导
    用户更新 .env —— 两种情况用户该做的事完全不同，不能共用一句话。
    """

    lowered = detail.lower()
    local = uses_local_credentials()
    if any(marker in lowered for marker in _NO_CREDENTIAL_MARKERS):
        if local:
            return (
                "本机没有该仓库的 Git 凭证，请先用 SourceTree 或 git 命令行登录一次"
                "（凭证会存入系统凭据管理器）"
            )
        return "未配置 Git 凭证，请在 .env 设置 DEVAGENTSTUDIO_GIT_USERNAME 与 DEVAGENTSTUDIO_GIT_TOKEN"
    # policy 错误码优先于笼统的"服务端拒绝"：它们给出的是精确原因，照做即可解决。
    # 注意邮箱这条要提示"重试按钮不管用" —— 重试推送的是已存在的那个提交，
    # 改了 .env 里的身份也不会改变它，必须重建应用或改写该提交的 author。
    if any(marker in lowered for marker in _EMAIL_UNAUTHORIZED_MARKERS):
        return (
            "服务端拒绝了本次推送：提交的邮箱未被该仓库授权（git002）。"
            "平台提交身份取自本机 Git 全局配置（user.name / user.email），"
            "可在 .env 用 DEVAGENTSTUDIO_GIT_AUTHOR_NAME / _EMAIL 指定。"
            "注意：已生成的提交无法靠重试修正（重试推送的是同一个提交），"
            "请重新创建应用，或在该工作区执行 "
            "git commit --amend --no-edit --reset-author 后再重试。服务端返回如下"
        )
    if any(marker in lowered for marker in _BRANCH_NAME_POLICY_MARKERS):
        return (
            "服务端拒绝了本次推送：分支名不符合仓库白名单（git017，只允许字母、数字、下划线）。"
            "请在新建应用时把版本号改成合规写法（例如 v1_0）。服务端返回如下"
        )
    # 非快进要排在通用"服务端拒绝"之前：这类文本同样含 `[remote rejected]`，
    # 但真因是远端已有同名分支，跟提交信息、权限都无关，重试也不会成功。
    if any(marker in lowered for marker in _NON_FAST_FORWARD_MARKERS):
        return (
            "远端已存在同名分支，且与本地历史没有共同祖先（平台推送的是全新初始化的"
            "模板基线，无法快进合并），服务端不允许覆盖。请先删除远端的该分支，"
            "或改用其他版本号后重试。服务端返回如下"
        )
    if any(marker in lowered for marker in _SERVER_REJECTED_MARKERS):
        return (
            "服务端拒绝了本次推送（常见原因：提交信息不符合仓库规范，"
            "或账号没有该仓库的推送权限）。服务端返回如下"
        )
    if any(marker in lowered for marker in _REJECTED_MARKERS):
        if local:
            return (
                "认证被拒绝：本机凭证可能已失效，请重新登录一次；若仍失败，"
                "请联系仓库管理员确认该账号是否有此仓库权限"
            )
        return (
            "认证被拒绝：.env 里的 Git 凭证可能已失效，请更新后重试；若仍失败，"
            "请联系仓库管理员确认该账号是否有此仓库权限"
        )
    if any(marker in lowered for marker in _NOT_FOUND_MARKERS):
        return "当前账号无权访问该仓库（或仓库不存在），请联系仓库管理员确认权限"
    return ""


def git_failure_detail(completed: subprocess.CompletedProcess[str]) -> str:
    """提取失败原因；认证类失败附上可操作的中文提示。

    服务端 policy 提示（如提交信息规范要求 + 正确格式示例）可能很长，上限放宽到 2000，
    避免把最关键的"应该怎么写"截掉。结果会持久化进 application.json 并展示在卡片上。
    """

    raw = (completed.stderr or completed.stdout or "").strip() or "未知错误"
    hint = credential_failure_hint(raw)
    # 保留原始 stderr：调用方（如 repository_branch 的瞬时故障判断）还要按原文匹配。
    text = f"{hint}：{raw}" if hint else raw
    return text[:2000]
