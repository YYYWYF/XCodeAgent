"""为 UI 确认节点生成单个页面的 React + antd 设计稿代码。

设计稿是一段自包含的 .tsx（React + antd5 + @ant-design/pro-components），
由 LLM 按 antd-ui-design SKILL.md 规范生成，写入可运行的设计稿工程
UiDesignProject 的 src/pages/<PageKey>/index.tsx，并把页面注册到该工程的
BIZ_MENUS 菜单。代码使用内联静态 Mock 数据，不接入 API/路由/权限/真实交互，
仅呈现视觉 UI 效果。
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.builtin_skills import read_builtin_skill_md


logger = logging.getLogger(__name__)


UI_DESIGN_SKILL_NAME = "antd-ui-design"

# 设计稿工程内页面与菜单的相对路径
PAGES_RELATIVE_DIR = "src/pages"
MENUS_RELATIVE_PATH = "src/constants/menus.ts"

_FALLBACK_SKILL_NOTE = (
    "(antd-ui-design SKILL.md 未找到，请仍按以下规范生成：输出单个自包含 .tsx "
    "文件，React + antd5 + @ant-design/pro-components，Pro 系列从 "
    "@ant-design/pro-components 导入、基础组件从 antd 导入、图标从 "
    "@ant-design/icons 导入；用内联静态 Mock 数据数组（8-15 条），ProTable 用 "
    "dataSource 不用 request；禁 API/useEffect/fetch/axios/mockjs/xlsx；不包 "
    "ProLayout/PageContainer 布局外壳；按钮 onClick/onFinish 给 no-op；只返回 "
    "tsx 代码不包 markdown 围栏。)"
)

# 匹配 markdown 代码围栏（```tsx ... ``` 或 ```ts ... ``` 或 ``` ... ```）
_CODE_FENCE_RE = re.compile(
    r"^\s*```(?:tsx|ts|jsx|js)?\s*\n(?P<code>.*?)\n```\s*$",
    re.DOTALL,
)


def _ui_design_skill_document() -> str:
    """读取 antd-ui-design 技能 SKILL.md 全文，缺失时返回降级提示。"""

    content = read_builtin_skill_md(UI_DESIGN_SKILL_NAME)
    return content if content else _FALLBACK_SKILL_NOTE


def _page_brief(page: dict[str, Any]) -> str:
    """把单个页面信息组织成 prompt 友好的简述。"""

    page_id = str(page.get("pageId") or page.get("id") or "").strip()
    name = str(page.get("name") or "").strip()
    path = str(page.get("path") or "/").strip()
    module_id = str(page.get("module_id") or "").strip()
    description = str(page.get("description") or "").strip()
    lines = [
        f"- pageId: {page_id or '(未命名)'}",
        f"- name: {name or '(未命名)'}",
        f"- path: {path}",
    ]
    if module_id:
        lines.append(f"- module_id: {module_id}")
    if description:
        lines.append(f"- description: {description}")
    return "\n".join(lines)


def _build_ui_design_prompt(page: dict[str, Any], page_key: str) -> str:
    """组合页面信息与技能全文，约束模型只返回单个页面的 .tsx 代码。"""

    skill_document = _ui_design_skill_document()
    return (
        "You are a UI design code generation model for an app-generation workflow.\n"
        "Generate ONE self-contained React + antd5 + @ant-design/pro-components "
        ".tsx page file for the single page described below, following the "
        "antd-ui-design skill strictly.\n"
        "Output rules:\n"
        "- Return a single .tsx file's source code ONLY. Do not wrap it in "
        "markdown fences, do not add commentary before or after.\n"
        "- The component name MUST be PascalCase. Use the suggested PageKey as "
        f"the component name: {page_key}.\n"
        "- The page renders inside a ProLayout <Outlet/>, so do NOT include any "
        "layout shell (no ProLayout, no PageContainer, no header/sider). Wrap "
        "content in at most a <div style={{ padding: 24 }}>.\n"
        "- Use inline static Mock data (8-15 rows). ProTable MUST use `dataSource`, "
        "NEVER `request`. No API calls, no useEffect/fetch/axios, no mockjs/xlsx.\n"
        "- Buttons/forms may render but handlers are no-op (() => {}).\n"
        "- Infer the page type from the page name and description (no components "
        "field is provided): list/search → ProTable, detail → ProDescriptions, "
        "dashboard/overview → ProCard + Statistic, login → centered Card + ProForm, "
        "tabs → ProCard tabs, card list → ProList. Default to ProTable when unsure.\n"
        "- Adapt the columns/fields/mock data to THIS page's purpose. Do NOT copy "
        "the skill's order-list example verbatim.\n\n"
        "--- PAGE TO DESIGN ---\n"
        f"{_page_brief(page)}\n"
        "--- END PAGE ---\n\n"
        "--- INJECTED antd-ui-design SKILL.md (content inlined) ---\n"
        + skill_document
        + "\n--- END INJECTED SKILL.md ---\n"
    )


def _extract_tsx_code(text: str) -> str:
    """从模型返回文本中提取 .tsx 代码，去掉 markdown 围栏与前后说明。"""

    stripped = text.strip()
    # 优先匹配整段被围栏包裹的情况
    fence_match = _CODE_FENCE_RE.match(stripped)
    if fence_match:
        return fence_match.group("code").strip()
    # 兜底：若文本中间出现围栏（前后有说明），取第一个围栏块
    inner = re.search(r"```(?:tsx|ts|jsx|js)?\s*\n(.*?)\n```", stripped, re.DOTALL)
    if inner:
        return inner.group(1).strip()
    # 无围栏：去掉常见的开头说明行（如 "Here is the code:"），保留以 import 开头的代码
    # 找到第一个 import 或 // 注释或 const/import 开头的行
    lines = stripped.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if line.strip().startswith(("import ", "//", "/*", "const ", "export ")):
            start = i
            break
    return "\n".join(lines[start:]).strip() or stripped


def generate_page_react_code(page: dict[str, Any], page_key: str) -> str:
    """调用 LLM 为单个页面生成 React 设计稿 .tsx 代码。

    返回 .tsx 源码字符串。模型调用失败时抛出，由调用方（Graph 节点）捕获并
    持久化为节点失败状态。
    """

    settings = Settings.from_env()
    prompt = _build_ui_design_prompt(page, page_key)
    result = create_chat_model(settings).bind(
        max_tokens=settings.ui_design_max_tokens
    ).invoke(prompt)
    content = _coerce_content_text(getattr(result, "content", ""))
    code = _extract_tsx_code(content)
    logger.info(
        "ui_design_generated page_id=%s content_chars=%s code_chars=%s",
        str(page.get("pageId") or page.get("id") or ""),
        len(content),
        len(code),
    )
    return code


# ---------------------------------------------------------------------------
# PageKey / 菜单路径派生
# ---------------------------------------------------------------------------


def _page_key_from_page_id(page_id: str) -> str:
    """从 pageId 派生 PascalCase 的 PageKey，用作目录名与菜单 key。

    规则：去掉 _page 后缀 → 按 _ / - 分段 → 每段首字母大写拼接。
    例：order_list_page → OrderList，dashboard_page → Dashboard，
    login_page → Login，user_detail_page → UserDetail。
    """

    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(page_id or "page")).strip("-")
    # 去掉末尾的 page 段
    segments = [s for s in re.split(r"[-_]+", cleaned) if s]
    if segments and segments[-1].lower() == "page":
        segments = segments[:-1]
    if not segments:
        return "Page"
    return "".join(seg[:1].upper() + seg[1:].lower() for seg in segments)


def derive_page_key(page: dict[str, Any], used_keys: set[str] | None = None) -> str:
    """派生 PageKey 并处理碰撞（追加数字后缀）。"""

    page_id = str(page.get("pageId") or page.get("id") or "page")
    base = _page_key_from_page_id(page_id)
    used = used_keys if used_keys is not None else set()
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _slug_from_page_key(page_key: str) -> str:
    """把 PascalCase PageKey 转为 kebab-case slug（OrderDetail → order-detail）。"""

    # 在大写字母前插连字符，再整体小写，合并多余连字符。
    spaced = re.sub(r"(?<!^)(?=[A-Z])", "-", str(page_key or "page"))
    return re.sub(r"-+", "-", spaced).strip("-").lower() or "page"


def menu_path_for_page(page: dict[str, Any], page_key: str) -> str:
    """把页面 path 转为菜单末级 path（无前导 /），保证每个页面唯一可访问。

    统一用 PageKey 派生的 kebab-case slug 作为菜单 path（OrderList →
    order-list，ProjectDetail → project-detail）。这样：
    - 详情页不再用 :id 动态参数（多个详情页会碰撞到同一路由），改用唯一 slug，
      设计稿用 Mock 数据展示，不需要真实 id。
    - 列表页/表单页也用 slug，与 PageKey 一一对应，由 derive_page_key 的
      used_keys 去重保证全局唯一。
    """

    del page  # menu_path 仅依赖 page_key，path 不再直接参与
    return _slug_from_page_key(page_key)


def route_path_for_page(menu_path: str) -> str:
    """返回前端可访问的完整路由路径（/page/<menu_path>）。"""

    return f"/page/{menu_path}"


# ---------------------------------------------------------------------------
# 菜单 path 是否含 React Router 动态参数（决定 hideInMenu）
# ---------------------------------------------------------------------------


def _has_react_router_path_param(path: str) -> bool:
    """判断菜单 path 是否包含 React Router 动态路径参数（如 :id）。"""

    route_part = str(path or "").split("?", 1)[0].split("#", 1)[0]
    return any(
        re.fullmatch(r":[A-Za-z0-9_][A-Za-z0-9_-]*", segment)
        for segment in route_part.split("/")
    )


# ---------------------------------------------------------------------------
# 写盘 / 读取 / 菜单重写
# ---------------------------------------------------------------------------


def pages_dir(project_dir: str) -> Path:
    """返回设计稿工程的页面目录路径。"""

    return Path(project_dir).expanduser().resolve() / PAGES_RELATIVE_DIR


def menus_file(project_dir: str) -> Path:
    """返回设计稿工程的菜单文件路径。"""

    return Path(project_dir).expanduser().resolve() / MENUS_RELATIVE_PATH


def persist_page_code(project_dir: str, page_key: str, code: str) -> str:
    """把单页 .tsx 原子写入设计稿工程，返回写入后的绝对路径。"""

    target_dir = pages_dir(project_dir) / page_key
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "index.tsx"
    tmp = target.with_suffix(".tsx.tmp")
    tmp.write_text(code, encoding="utf-8")
    tmp.replace(target)
    return str(target)


def load_page_code(project_dir: str, page_key: str) -> str | None:
    """读取已落盘的单页 .tsx 代码，缺失时返回 None。"""

    target = pages_dir(project_dir) / page_key / "index.tsx"
    if not target.is_file():
        return None
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _esbuild_main_path(project_dir: str) -> str | None:
    """在设计稿工程的 pnpm .pnpm 目录下定位 esbuild 的 lib/main.js。"""

    root = Path(project_dir).expanduser().resolve()
    pnpm_dir = root / "node_modules" / ".pnpm"
    if not pnpm_dir.is_dir():
        return None
    for entry in pnpm_dir.iterdir():
        if entry.name.startswith("esbuild@"):
            main = entry / "node_modules" / "esbuild" / "lib" / "main.js"
            if main.is_file():
                return str(main)
    return None


def validate_tsx(project_dir: str, code: str) -> tuple[bool, str]:
    """用 esbuild 校验 .tsx 语法（不查类型）。

    返回 (是否通过, 错误信息)。esbuild 不可用时降级为跳过校验（通过），
    仅记录警告——避免因校验工具缺失阻断生成。
    """

    main_path = _esbuild_main_path(project_dir)
    if not main_path:
        logger.warning(
            "ui_design_validate_skip esbuild_not_found project=%s", project_dir
        )
        return True, ""
    # 用 node -e 内联脚本调 esbuild.transform，通过 stdin 传代码避免命令行长度限制。
    # esbuild 主模块路径通过 process.argv[1] 传入（node -e 模式下 argv[1] 是首个用户参数）。
    script = (
        "const esbuild=require(process.argv[1]);"
        "let code='';process.stdin.on('data',d=>code+=d);"
        "process.stdin.on('end',()=>{"
        "esbuild.transform(code,{loader:'tsx'}).then(()=>{process.exit(0);})"
        ".catch(e=>{process.stderr.write(String(e.message));process.exit(1);});"
        "});"
    )
    try:
        proc = subprocess.run(
            ["node", "-e", script, main_path],
            input=code,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("ui_design_validate_error %s", exc)
        return True, ""
    if proc.returncode == 0:
        return True, ""
    return False, proc.stderr.strip() or "esbuild syntax error"


def _format_menu_entry(menu_path: str, name: str, page_key: str) -> str:
    """格式化单个 BIZ_MENUS 菜单项文本。"""

    # 转义字符串里的单引号
    safe_path = menu_path.replace("'", "\\'")
    safe_name = str(name or page_key).replace("'", "\\'")
    if _has_react_router_path_param(menu_path):
        return (
            f"  {{ path: '{safe_path}', name: '{safe_name}', "
            f"key: '{page_key}', hideInMenu: true }}"
        )
    return f"  {{ path: '{safe_path}', name: '{safe_name}', key: '{page_key}' }}"


def rewrite_menus(project_dir: str, pages: list[dict[str, Any]]) -> str:
    """全量重写设计稿工程的 menus.ts，注册全部页面菜单。

    pages 每项需含 menu_path / name / page_key。保留 DefaultPage 作为首项
    避免默认路由 404。原子写入（tmp + replace）。返回写入路径。
    """

    target = menus_file(project_dir)
    target.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [
        "import { Route } from '@/typings/workbench';",
        "",
        "// TODO 菜单类型跟随antd",
        "export const BIZ_MENUS: Route[] = [",
        "  {",
        "    path: 'default', // 菜单点击后的跳转路径就是 /default",
        "    name: '默认页面',",
        "    key: 'DefaultPage' // 如果渲染的是特定页面，key必须存在，且与src/pages下面的page的引用地址保持一致，要让import Page from '@/pages/Page’是一个有效语句",
        "  },",
    ]
    for page in pages:
        menu_path = str(page.get("menu_path") or "")
        name = str(page.get("name") or "")
        page_key = str(page.get("page_key") or "")
        if not menu_path or not page_key:
            continue
        lines.append(_format_menu_entry(menu_path, name, page_key) + ",")
    lines.append("];")
    lines.append("")
    content = "\n".join(lines)

    tmp = target.with_suffix(".ts.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)
    logger.info(
        "ui_design_menus_rewritten path=%s page_count=%s",
        target,
        len(pages),
    )
    return str(target)
