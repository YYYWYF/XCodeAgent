"""把已有生成校验结果转换为用户与修复规划共用的确定性诊断。"""

from pathlib import Path
from typing import Any


_REPAIR_HINTS = {
    "unauthorized_paths": "恢复测试范围外的写入，仅修改授权测试文件。",
    "invalid_paths": "使用当前允许的平铺前端测试或后端 Test.java 路径。",
    "missing_files": "生成缺失文件或修正实际测试文件路径。",
    "too_many_files": "合并测试，保持当前最多五个测试文件的约定。",
    "unaffected_layer_paths": "只修改本轮受影响端的测试。",
    "invalid_contents": "修正测试内容，保留有效业务断言，不修改生产代码迎合测试。",
}
_STYLE_TOKENS = ("toHaveStyle", "getComputedStyle", "toMatchSnapshot", "toMatchInlineSnapshot")
_DISABLED_TOKENS = ("test.skip", "test.todo", "test.only", "it.skip", "it.todo", "it.only",
                    "describe.skip", "describe.todo", "describe.only", "@Disabled", "@Ignore")


def generation_failure_diagnostics(workspace: str, validation: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """解释已有失败事实，不改变生成规则；精确原因先于模型总结进入修复输入。"""

    root = Path(workspace).expanduser().resolve()
    details = []
    for key, hint in _REPAIR_HINTS.items():
        for relative in validation.get(key) or []:
            item = {"category": key, "path": str(relative), "repair": hint}
            if key == "invalid_contents":
                path = (root / str(relative)).resolve()
                if path.is_relative_to(root) and path.is_file():
                    content = path.read_text(encoding="utf-8", errors="replace")
                    hits = [
                        {"token": token, "line": number}
                        for number, line in enumerate(content.splitlines(), 1)
                        for token in (*_STYLE_TOKENS, *_DISABLED_TOKENS)
                        if token in line
                    ]
                    if hits:
                        item["violations"] = hits
                        item["repair"] = "移除禁止的样式/快照断言或跳过/独占标记，保留业务行为测试；不要改生产代码迎合测试。"
                    unavailable = (validation.get("unavailable_imports") or {}).get(str(relative))
                    if unavailable:
                        item["unavailable_imports"] = unavailable
                        item["repair"] = "移除不可用的测试依赖，使用项目已有能力，不新增依赖。"
            details.append(item)
    lines = []
    for item in details:
        hits = "、".join(f"{v['token']}（第 {v['line']} 行）" for v in item.get("violations", []))
        imports = "、".join(item.get("unavailable_imports", []))
        lines.append(f"{item['path']}：{item['category']}" + (f"；禁止项 {hits}" if hits else "")
                     + (f"；不可用依赖 {imports}" if imports else "") + f"。{item['repair']}")
    return "生成检查失败：" + "\n".join(lines) if lines else "生成检查未通过，请检查生成结果及校验记录。", details
