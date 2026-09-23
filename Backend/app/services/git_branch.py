"""Git 分支名的校验规则。

刻意放在独立模块里且**不依赖任何其它 service**：远端分支动作（`repository_branch.py`）
和提交推送（`version_publish.py`）都要用它，放在任一方都会造成循环导入。

前端 `service/repositoryBranch.ts` 里有一份等价实现（用于表单即时校验），
两边规则必须保持一致。
"""

from __future__ import annotations

import re

class GitBranchError(ValueError):
    """表示分支名非法，或工作区/远端仓库不能完成分支动作。"""


# Git 明确禁止出现在分支名里的字符（见 git-check-ref-format）。反斜杠用 chr(92) 表示，
# 避免源码里出现裸反斜杠。
_FORBIDDEN_BRANCH_CHARS = frozenset(" ~^:?*[") | {chr(92)}

# 控制字符同样不允许，用正则一次性拦掉。
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def validate_branch_name(value: str) -> str:
    """校验并规范化分支名，拒绝 Git 不接受的写法。"""

    branch_name = str(value or "").strip()
    if not branch_name:
        raise GitBranchError("分支名不能为空。")
    if len(branch_name) > 255:
        raise GitBranchError("分支名过长，请控制在 255 个字符以内。")
    if _CONTROL_CHARS.search(branch_name):
        raise GitBranchError("分支名不能包含控制字符。")
    if any(character in _FORBIDDEN_BRANCH_CHARS for character in branch_name):
        raise GitBranchError("分支名不能包含空格，或 ~ ^ : ? * [ 这类字符。")
    if ".." in branch_name:
        raise GitBranchError("分支名不能包含连续的两个点。")
    if "@{" in branch_name:
        raise GitBranchError("分支名不能包含 @{ 。")
    if branch_name.startswith("-"):
        raise GitBranchError("分支名不能以短横线开头。")
    if branch_name.startswith("/") or branch_name.endswith("/"):
        raise GitBranchError("分支名不能以斜杠开头或结尾。")
    if "//" in branch_name:
        raise GitBranchError("分支名不能包含连续的两个斜杠。")
    if branch_name.endswith(".") or branch_name.endswith(".lock"):
        raise GitBranchError("分支名不能以点或 .lock 结尾。")
    if branch_name == "HEAD":
        raise GitBranchError("分支名不能叫 HEAD。")
    return branch_name
