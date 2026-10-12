"""提交信息规范：公司内看板要求每次提交都带上看板编号。

看板系统的约定格式是 `<编号> #comment <类型>：<说明>`，其中 `#comment` 是看板指令，
会把这条提交关联成该编号下的一条评论。缺编号的提交会被服务端的 pre-receive hook
拒绝，所以每条提交都必须带上。

编号是团队/项目级常量，换项目时改 `BOARD_COMMIT_NUMBER` 这一行即可。

**为什么统一在后端兜底**：提交入口不止一处，其中验收阶段的自动保存
（前端 useAcceptanceAutoCommit）是静默执行的 —— 用户既看不到也改不了它的提交信息。
所以不能只靠前端默认值，必须在真正执行 git commit 的地方统一补齐。
"""

from __future__ import annotations

# 看板编号。公司内每次提交都要带上它。
BOARD_COMMIT_NUMBER = "Z000000-0000"

# 看板系统的评论指令，跟在编号后面。
_COMMENT_DIRECTIVE = "#comment"


def ensure_board_number(message: str) -> str:
    """确保提交信息带上看板编号；已带则原样返回（幂等）。

    用"包含"而不是"以它开头"判断：前端默认值里已经带了编号，用户手动输入时也可能
    把它放在中间。宁可放过不补，也不要重复拼出两个编号 —— 后者会让信息变脏，而前者
    至少服务端校验仍能通过（编号在即可）。
    """

    body = (message or "").strip()
    if not body:
        return f"{BOARD_COMMIT_NUMBER} {_COMMENT_DIRECTIVE}"
    if BOARD_COMMIT_NUMBER in body:
        return body
    return f"{BOARD_COMMIT_NUMBER} {_COMMENT_DIRECTIVE} {body}"


def template_baseline_commit_message() -> str:
    """模板基线（首次物化）的提交信息。"""

    return f"{BOARD_COMMIT_NUMBER} {_COMMENT_DIRECTIVE} chore：模板初始化"
