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

# 推送被服务端钩子拒绝。**不能断言原因** —— pre-receive hook 既可能校验提交信息规范
# （公司内仓库常见要求：`Z991700-1911 #comment feat：说明`），也可能校验账号写权限。
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
