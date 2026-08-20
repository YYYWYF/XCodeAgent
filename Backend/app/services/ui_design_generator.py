"""为 UI 确认节点生成单个页面的 React + antd 设计稿代码。

设计稿是一段自包含的 .tsx（React + antd5 + @ant-design/pro-components），
由 LLM 按 antd-ui-design SKILL.md 规范生成，写入可运行的设计稿工程
UiDesignProject 的 src/pages/<PageKey>/index.tsx，并把页面注册到该工程的
BIZ_MENUS 菜单。代码使用内联静态 Mock 数据，不接入 API；通过本地状态表达
已确认的筛选、弹窗、表单和页面状态，作为可交互的产品 UI 原型。
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from app.agents.messages import _coerce_content_text, strip_thinking_fragments
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.builtin_skills import read_builtin_skill_md
from app.services.ui_design_manifest import validate_ui_design_code
from app.workspace.spec_documents import REPOSITORY_ROOT


logger = logging.getLogger(__name__)


UI_DESIGN_SKILL_NAME = "antd-ui-design"

# 设计稿 .tsx 落盘的相对路径（方案 B：不再 clone 模板工程，.tsx 直接落到工作区
# .xcodeagent/ui-design/pages/<PageKey>/index.tsx，由前端 DesignRenderer 编译渲染）。
PAGES_RELATIVE_DIR = "pages"

_FALLBACK_SKILL_NOTE = (
    "(antd-ui-design SKILL.md 未找到，请仍按以下规范生成：输出单个自包含 .tsx "
    "文件，React + antd5 + @ant-design/pro-components，Pro 系列从 "
    "@ant-design/pro-components 导入、基础组件从 antd 导入、图标从 "
    "@ant-design/icons 导入；用内联静态 Mock 数据数组（8-15 条），ProTable 用 "
    "dataSource 不用 request；禁 API/useEffect/fetch/axios/mockjs/xlsx；不包 "
    "ProLayout/PageContainer 布局外壳；用 React 本地状态实现筛选、弹窗和表单交互；只返回 "
    "tsx 代码不包 markdown 围栏。)"
)

# 匹配 markdown 代码围栏（```tsx ... ``` 或 ```ts ... ``` 或 ``` ... ```）
_CODE_FENCE_RE = re.compile(
    r"^\s*```(?:tsx|ts|jsx|js)?\s*\n(?P<code>.*?)\n```\s*$",
    re.DOTALL,
)

# import 行首匹配：兼容行首残留的碎片括号（`]`/`}`/`'`）——剥离 thinking 碎片
# 后可能与 import 同行粘连，行首正则 `^\s*import` 会失配导致组件被误判"未 import"。
_IMPORT_LINE_RE = re.compile(r"^[\]}\'\s]*import\s+")


def _is_multiline_import_continuation(line: str) -> bool:
    """判断一行是否是多行 import 块的续行（非 import 开头、非空行）。

    多行 import 形如::

        import {
          Button,
          Row as R,
        } from 'antd';

    续行包括：``} from 'antd';``（闭合括号+from 子句）、``  Button,``
    （缩进标识符+可选逗）、``from 'antd';``（from 子句独立成行）。
    用精确模式避免误匹配正文代码（如 ``const X = 5;`` 不匹配）。
    """

    s = line.strip()
    if not s:
        return False
    # } from 'antd'; / ] from ... / } 仅闭合括号
    if re.match(r"^[}\]]\s*(?:from\s+['\"]|$)", s):
        return True
    # from 'antd'; （from 子句独立成行）
    if re.match(r"^from\s+['\"]", s):
        return True
    # 缩进标识符：Button, / Button as Btn, / Button
    if re.match(r"^\w+(?:\s+as\s+\w+)?\s*,?\s*(?://.*)?$", s):
        return True
    return False

def _create_ui_design_model(settings: Settings):
    """创建 UI 设计稿专用模型实例，尽量压低 GLM-5.2 的 thinking 预算。

    GLM-5.2 默认开启深度思考，thinking 与正文共享 max_tokens，复杂页常在写完
    代码前耗尽预算被截断（缺 export default）。

    实测（curl 对照网关）：只传 Anthropic 原生 `thinking.type=disabled` 或只传
    Zhipu `reasoning_effort=none` 都能压掉短任务的 thinking；但**两者同时传反而
    触发更多 thinking**（语义冲突），且长代码任务上两者都压不掉。因此只传
    Anthropic 原生字段（协议标准、短任务确有效），长任务的截断兜底交给
    `generate_page_react_code` 里的断点续写逻辑（`_is_likely_truncated` +
    `_build_continuation_prompt`），不依赖网关对 thinking 参数的不稳定处理。
    """

    return create_chat_model(
        settings,
        extra_model_kwargs={"thinking": {"type": "disabled"}},
    ).bind(max_tokens=settings.ui_design_max_tokens)


def _ui_design_skill_document() -> str:
    """读取 antd-ui-design 技能 SKILL.md 全文，缺失时返回降级提示。"""

    content = read_builtin_skill_md(UI_DESIGN_SKILL_NAME)
    return content if content else _FALLBACK_SKILL_NOTE


def _page_brief(page: dict[str, Any]) -> str:
    """把 ProductPlan 单页语义组织成 prompt 友好的设计输入。"""

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
    goal = str(page.get("goal") or "").strip()
    if goal:
        lines.append(f"- product goal: {goal}")
    information_items = page.get("information_items")
    if isinstance(information_items, list) and information_items:
        lines.append(
            "- required information items: "
            + json.dumps(information_items, ensure_ascii=False)
        )
    actions = page.get("actions")
    if isinstance(actions, list) and actions:
        lines.append("- approved product actions: " + json.dumps(actions, ensure_ascii=False))
    state_requirements = page.get("state_requirements")
    if isinstance(state_requirements, dict) and state_requirements:
        lines.append(
            "- required product states: "
            + json.dumps(state_requirements, ensure_ascii=False)
        )
    for key, label in (
        ("navigation_targets", "approved navigation target pageIds"),
        ("allowed_roles", "approved product roles"),
        ("acceptance_criteria", "product acceptance criteria"),
    ):
        value = page.get(key)
        if isinstance(value, list) and value:
            lines.append(f"- {label}: " + json.dumps(value, ensure_ascii=False))
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
        "- CRITICAL: Output the .tsx code DIRECTLY. Do NOT reason step by step, "
        "do NOT produce thinking blocks, do NOT explore alternatives before "
        "writing code. Start writing the import statements immediately.\n"
        "- Return a single .tsx file's source code ONLY. Do not wrap it in "
        "markdown fences, do not add commentary before or after.\n"
        "- The component name MUST be PascalCase. Use the suggested PageKey as "
        f"the component name: {page_key}.\n"
        "- The page renders inside a ProLayout <Outlet/>, so do NOT include any "
        "layout shell (no ProLayout, no PageContainer, no header/sider). Wrap "
        "content in at most a <div style={{ padding: 24 }}>.\n"
        "- Use inline static Mock data (8-15 rows). ProTable MUST use `dataSource`, "
        "NEVER `request`. No API calls, no useEffect/fetch/axios, no mockjs/xlsx.\n"
        "- Implement declared local interactions with React state and Mock data: filters update visible "
        "results, dialogs/drawers open and close, tabs switch, forms validate, and confirmation flows can "
        "complete locally. Cross-page controls may remain non-navigating in the isolated preview.\n"
        "- Render success, loading, empty, error, and validation states with a compact in-page preview "
        "switcher when those states cannot naturally be reached from the main interaction.\n"
        "- Use Ant Design theme tokens and responsive layout rules; the page must remain readable in light "
        "and dark themes and at wide/compact PC widths. Do not introduce standalone hard-coded brand colors.\n"
        "- Infer the page type from the page name and description (no components "
        "field is provided): list/search → ProTable, detail → ProDescriptions, "
        "dashboard/overview → ProCard + Statistic, login → centered Card + ProForm, "
        "tabs → ProCard tabs, card list → ProList. Default to ProTable when unsure.\n"
        "- Adapt the columns/fields/mock data to THIS page's purpose. Do NOT copy "
        "the skill's order-list example verbatim.\n\n"
        + _product_fact_boundary_rules()
        + "--- PAGE TO DESIGN ---\n"
        f"{_page_brief(page)}\n"
        "--- END PAGE ---\n\n"
        "--- INJECTED antd-ui-design SKILL.md (content inlined) ---\n"
        + skill_document
        + "\n--- END INJECTED SKILL.md ---\n"
    )


def _product_fact_boundary_rules() -> str:
    """返回 UI 生成与调整共用的产品事实边界约束。"""

    return (
        "--- PRODUCT FACT BOUNDARY (MANDATORY) ---\n"
        "- ProductPlan is the ONLY source of product facts. Page names, routes, roles, states, "
        "information items, actions, navigation targets, metrics, filters, fields, and business "
        "labels are immutable. Never add, rename, remove, broaden, or reinterpret them.\n"
        "- You may decide visual layout, component composition, spacing, hierarchy, typography, "
        "responsive arrangement, theme-token usage, and the visual presentation of declared states.\n"
        "- Mock rows may provide example VALUES only for fields explicitly named by a declared "
        "information item. Do not invent additional metrics, counters, metadata fields, buttons, "
        "links, tabs, filters, calls to action, or business sections.\n"
        "- Render every declared action. Each interactive JSX opening tag that implements it must "
        "carry static `data-action-id=\"<actionId>\"` and static "
        "`data-control-id=\"<actionId>-control\"` (use a numeric suffix for another distinct source "
        "control). Never emit an undeclared data-action-id.\n"
        "- When an action's behavior.type is `interface`, the implementing JSX opening tag must also "
        "carry static `data-ui-effect` with a concise product-readable description of the actual local "
        "interaction, such as `打开订单筛选抽屉` or `切换到异常订单 Tab`. This UI effect is owned by "
        "UiDesign and must not mention endpoints, HTTP, schemas, or backend implementation.\n"
        "- For every `interface` step inside a sequence behavior, the implementing JSX opening tag must carry "
        "the parent `data-action-id`, static `data-action-step-id=\"<stepId>\"`, and its own static "
        "`data-ui-effect`. Multiple interface steps in one sequence must be tagged separately.\n"
        "- Render every declared information item. Put static "
        "`data-information-item-id=\"<itemId>\"` and static "
        "`data-control-id=\"<itemId>-display\"` on the business display component itself. Never emit "
        "an undeclared data-information-item-id.\n"
        "- CRITICAL binding rule: `data-information-item-id`, `data-control-id`, "
        "`data-action-id`, `data-action-step-id`, `data-ui-effect`, and "
        "`data-preview-only` values MUST be static string literals written "
        "directly in the JSX (e.g. `data-information-item-id=\"dashboard_page-project-total\"`). "
        "NEVER bind them via JSX expressions like `{item.itemId}`, `{m.id}`, "
        "template literals, or `.map()` callback variables. When several declared "
        "items share the same visual structure, write each one out explicitly as a "
        "separate JSX element with its own hard-coded static id instead of "
        "iterating over a data array. A static analyzer reads these attributes "
        "and cannot resolve runtime expressions; expression-bound ids are treated "
        "as MISSING bindings and will fail validation.\n"
        "- Any control used solely to switch prototype states must carry "
        "`data-preview-only=\"true\"`; it is review tooling, not product UI. Do not create any other "
        "preview-only business-looking control or content.\n"
        "- Cross-page action handlers may stay local/no-op in the isolated preview, but their "
        "visible intent and data-action-id must still match ProductPlan exactly.\n"
        "--- END PRODUCT FACT BOUNDARY ---\n"
    )


def _extract_tsx_code(text: str) -> str:
    """从模型返回文本中提取 .tsx 代码，去掉 markdown 围栏与前后说明。

    glm-5.2 等推理模型常在正文里先输出中文思考过程（其中可能引用 import 字样、
    甚至内嵌示例代码），再把真正的代码放在末尾。因此以「最后一个 export default」
    所在的代码块为真代码：它可能在围栏内，也可能是末尾的裸代码。这样既不会取
    第一个 import 行（会混入思考过程），也不会误取思考过程里的示例围栏块。
    """

    # 防御性剥离 thinking 碎片（网关可能把 [{'thinking': ..}] 逐 token 拼进
    # content，虽已由 _coerce_content_text 剥过，这里对直接传入的文本再兜底）。
    stripped = strip_thinking_fragments(text).strip()
    if not stripped:
        return stripped

    lines = stripped.splitlines()
    exports = list(re.finditer(r"export\s+default", stripped))
    if exports:
        last_export = exports[-1]
        e_start, e_end = last_export.start(), last_export.end()

        # 1) 最后一个 export default 落在某个围栏块内 → 返回该围栏块
        for m in re.finditer(
            r"```(?:tsx|ts|jsx|js)?\s*\n(.*?)\n```", stripped, re.DOTALL
        ):
            if m.start(1) <= e_start and e_end <= m.end(1):
                return m.group(1).strip()

        # 2) 否则取该 export default 之前的代码块。
        # 从最近的 import 行开始反向扫描，包含所有 import 行、空行、以及
        # 多行 import 的续行（} from 'antd'; 和缩进的 Button, 等）。
        # 不用"找第一个 import"——prose 里也可能引用 import 字样。
        export_idx = stripped.count("\n", 0, e_start)
        import_idx = -1
        for i in range(export_idx - 1, -1, -1):
            if _IMPORT_LINE_RE.match(lines[i]):
                import_idx = i
                break
        if import_idx >= 0:
            start = import_idx
            j = import_idx - 1
            while j >= 0 and (
                not lines[j].strip()
                or _IMPORT_LINE_RE.match(lines[j])
                or _is_multiline_import_continuation(lines[j])
            ):
                if lines[j].strip():
                    start = j
                j -= 1
            return "\n".join(lines[start : export_idx + 1]).strip()

    # 3) 兜底：整段被围栏包裹 / 去掉开头说明行，保留以 import 开头的代码
    fence_match = _CODE_FENCE_RE.match(stripped)
    if fence_match:
        return fence_match.group("code").strip()
    start = 0
    for i, line in enumerate(lines):
        if line.lstrip("]}\' \t").startswith(("import ", "//", "/*", "const ", "export ")):
            start = i
            break
    return "\n".join(lines[start:]).strip() or stripped


def _invoke_ui_design_model(
    model: Any,
    prompt: str,
    *,
    page_id: str,
    max_retries: int,
) -> Any:
    """对无副作用的 UI 模型调用做外层瞬时异常重试。"""

    attempts = max(1, max_retries + 1)
    for attempt in range(1, attempts + 1):
        try:
            return model.invoke(prompt)
        except Exception as exc:
            if attempt >= attempts:
                raise
            logger.warning(
                "ui_design_model_invoke_failed page_id=%s attempt=%s/%s error=%s",
                page_id,
                attempt,
                attempts,
                str(exc)[:200],
            )
            time.sleep(min(0.5 * attempt, 2.0))
    raise RuntimeError("UI 设计模型调用未返回结果。")


# ---------------------------------------------------------------------------
# 生成后静态校验：捕获 esbuild 查不出的"空代码"与"未定义引用"两类错误
# ---------------------------------------------------------------------------

# React / TS 内置、无需 import 即可在 JSX 中使用的大写标识符。
_REACT_BUILTIN_TAGS = {"Fragment", "React"}


def _collect_imported_and_local_names(code: str) -> set[str]:
    """收集代码中所有已 import 或本地定义的标识符名。

    覆盖：默认导入、命名导入（含 `as` 重命名）、命名空间导入（`* as X`）、
    解构赋值（`const { Text, Title } = Typography`）、以及 const/function/
    type/interface/enum/class 的顶层定义。
    """

    names: set[str] = set()
    # import 语句：默认导入 + 命名导入块 + 命名空间。
    # 行首放宽到允许残留的碎片括号（] } ' 与空白），防止 thinking 碎片残留
    # 粘连时整条 import 被漏掉、组件被误判"未 import"。
    for m in re.finditer(
        r"^[\]}\'\s]*import\s+(?:(\w+)(?:\s*,\s*)?)?(\{[^}]*\})?\s*(\*\s+as\s+(\w+))?\s*from",
        code,
        re.MULTILINE,
    ):
        if m.group(1):  # 默认导入名
            names.add(m.group(1))
        if m.group(2):  # 命名导入块 { A, B as C }
            for binding in m.group(2)[1:-1].split(","):
                token = binding.strip()
                if not token:
                    continue
                # 取 `as` 后的重命名，否则取首标识符
                as_match = re.search(r"\bas\s+(\w+)$", token)
                names.add(as_match.group(1) if as_match else token.split()[0])
        if m.group(4):  # * as Namespace
            names.add(m.group(4))

    # 解构赋值：const { Text, Title } = Typography;（含默认值与重命名）
    for m in re.finditer(
        r"(?:const|let|var)\s*\{([^}]*)\}\s*=",
        code,
    ):
        for binding in m.group(1).split(","):
            token = binding.strip()
            if not token:
                continue
            as_match = re.search(r"\bas\s+(\w+)", token)
            names.add(as_match.group(1) if as_match else re.match(r"\w+", token).group(0))

    # 顶层定义：const/function/type/interface/enum/class X
    for m in re.finditer(
        r"(?:^|\n)\s*(?:export\s+)?(?:const|let|var|function|type|interface|enum|class)\s+([A-Za-z_$][\w$]*)",
        code,
    ):
        names.add(m.group(1))
    return names


def _collect_jsx_component_tags(code: str) -> set[str]:
    """收集代码中 JSX 里所有大写开头的组件标签名（如 ProTable、Button）。

    只取首字母大写的标签（小写是 HTML 原生标签，不需 import）。成员表达式
    （如 `<ProForm.Text>`）取首段 `ProForm`。
    """

    tags: set[str] = set()
    for m in re.finditer(r"<([A-Z][\w$]*)", code):
        tags.add(m.group(1))
    return tags


def _find_undefined_refs(code: str) -> list[str]:
    """返回 JSX 中使用但未 import/未定义的组件名列表（已排序去重）。

    捕获 LLM 最常见的错误：用了 `<ProTable>` 却忘记在 import 里加上
    `ProTable`。esbuild 的 transform 只做语法转换不查引用，这类错误能通过
    语法校验但在浏览器里 ReferenceError 白屏。
    """

    if not code.strip():
        return []
    defined = _collect_imported_and_local_names(code) | _REACT_BUILTIN_TAGS
    used = _collect_jsx_component_tags(code)
    return sorted(used - defined)


def _is_meaningful_code(code: str) -> bool:
    """判断生成的代码是否是有意义的页面组件（非空、有 export default）。

    拒绝三类无效输出：纯空白（LLM 返回空）、无 `export default`（未导出
    页面组件，路由 lazy import 会拿到 undefined）、以及过短碎片（< 30 字符，
    通常是截断或错误占位）。
    """

    if not code or not code.strip():
        return False
    if len(code.strip()) < 30:
        return False
    return bool(re.search(r"export\s+default", code))


def _merge_truncated_code(partial: str, tail: str) -> str:
    """把截断的前半部分与续写的尾部拼成完整代码。

    续写输出可能混入三类杂质，需清理后再拼：
    1. 续写模型重新输出了 import/开头（没听"从断点续写"指令）；
    2. 续写开头重复了 partial 末尾的若干字符（重叠）；
    3. 续写只输出了一小段、或仍缺 export default（又截断了）。

    策略：若 tail 自带 export default 且包含完整 import 头（说明模型重新输出了
    整份代码），直接以 tail 为准（它更完整）；否则把 tail 拼到 partial 末尾，
    并去掉两者重叠的前缀部分。
    """

    partial = partial.rstrip()
    tail = tail.strip()
    if not tail:
        return partial
    # 情形 1：tail 自己就是一份完整代码（带 import 头 + export default），
    # 模型无视"续写"指令重写了整份——直接采用 tail。
    if re.search(r"export\s+default", tail) and re.search(
        r"^[\]}\'\s]*import\s", tail, re.MULTILINE
    ):
        return tail
    # 情形 2：正常续写。去掉 tail 开头与 partial 末尾重叠的部分，避免重复。
    # 从长到短找 partial 后缀 == tail 前缀的最长重叠（上限 200 字符）。
    max_overlap = min(200, len(partial), len(tail))
    overlap = 0
    for size in range(max_overlap, 0, -1):
        if partial.endswith(tail[:size]):
            overlap = size
            break
    return partial + tail[overlap:]


def _is_likely_truncated(code: str) -> bool:
    """判定提取出的代码是否因输出预算耗尽而在 `export default` 前被截断。

    推理模型（glm-5.2）的 thinking 与正文共享 max_tokens，复杂页（ProTable）
    常在写完代码前就耗尽预算。此时网关把截断伪装成 stop_reason=end_turn（实测），
    无法用 finish_reason 区分，只能看代码特征：
    - 已有实质内容（非空、有一定长度、含 import / JSX 结构）；
    - 但缺少 `export default` 导出。
    两者同时满足即判定截断，可走「续写」而非整页重生成，避免重复浪费 thinking。
    """

    if not code or len(code.strip()) < 200:
        return False
    if re.search(r"export\s+default", code):
        return False
    # 有 import 或 JSX 标签结构，说明模型确实在写代码（只是没写完），
    # 而非返回了无关文本。
    return bool(re.search(r"^[\]}\'\s]*import\s", code, re.MULTILINE)) or "<" in code


# 设计稿工程允许的 import 来源白名单。SKILL.md 约束只用这三个库 + react。
# 白名单外的依赖（umi/mockjs/xlsx/axios/@/apis 等）工程未安装或会冲突，
# import 它会导致 Vite "Failed to resolve import" 白屏。
_ALLOWED_IMPORT_SOURCES = {
    "react",
    "react-dom",
    "antd",
    "@ant-design/pro-components",
    "@ant-design/icons",
    "@ant-design/cssinjs",
    # dayjs 是 antd5 的传递依赖，页面模板（commonTable/tabsTable）用它做日期格式化。
    # 选模板作设计稿时模板代码原样落盘，校验需放行 dayjs，否则被当作禁用依赖拦截。
    "dayjs",
}

# 常见禁用来源 → 修复指引。LLM 常误从这些库引路由/数据/导出功能。
_FORBIDDEN_IMPORT_HINTS = {
    "umi": "umi 是框架，设计稿工程未安装。路由参数用 react-router-dom 的 "
    "`useParams`/`useNavigate`（`import { useParams, useNavigate } from "
    "'react-router-dom'`），或设计稿用 Mock 数据直接渲染、不用路由参数。",
    "mockjs": "工程未安装 mockjs。Mock 数据用内联静态数组。",
    "xlsx": "工程未安装 xlsx。导出按钮 onClick 给 no-op 即可。",
    "axios": "设计稿禁 API 请求。用内联静态 Mock 数据数组，不引 axios。",
    "dayjs": "如需日期格式化，用原生 Date 方法或工程已装的 date-fns。",
}


def _find_forbidden_imports(code: str) -> list[str]:
    """返回代码中引用的非白名单 import 来源（排序去重）。

    捕获 LLM 违反 SKILL.md 引入 umi/mockjs/xlsx/axios 等未安装依赖的错误。
    esbuild 语法校验查不出 import 是否可解析，只在浏览器加载时报
    "Failed to resolve import" 白屏。
    """

    sources: set[str] = set()
    for m in re.finditer(
        r"^[\]}\'\s]*import\s+(?:[^'\";]+\s+from\s+)?['\"]([^'\"]+)['\"]",
        code,
        re.MULTILINE,
    ):
        source = m.group(1).strip()
        if not source:
            continue
        # 取包名主体：去 v1 前缀、去 @scope/name 之外的子路径、去版本
        pkg = source
        if pkg.startswith("@"):
            parts = pkg.split("/")
            pkg = "/".join(parts[:2]) if len(parts) >= 2 else pkg
        else:
            pkg = pkg.split("/")[0]
        if pkg and pkg not in _ALLOWED_IMPORT_SOURCES:
            sources.add(source)
    return sorted(sources)


def validate_page_code(
    project_dir: str,
    code: str,
    page: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """对单页设计稿代码做完整校验：非空 + 未定义引用 + 禁用依赖 + esbuild 语法。

    返回 (是否通过, 错误信息)。错误信息为人类可读的修复指引，供回喂 LLM
    自动修复或展示给用户。esbuild 不可用时跳过语法校验（仅降级，不阻断）。
    """

    if not _is_meaningful_code(code):
        return (
            False,
            "生成的代码为空、过短或缺少 `export default` 导出。"
            "请输出一个完整的、以 `export default <ComponentName>` 结尾的页面组件。",
        )
    forbidden = _find_forbidden_imports(code)
    if forbidden:
        hints = []
        for source in forbidden:
            pkg = source.split("/")[0] if not source.startswith("@") else "/".join(source.split("/")[:2])
            hint = _FORBIDDEN_IMPORT_HINTS.get(pkg)
            if hint:
                hints.append(f"- {source}: {hint}")
            else:
                hints.append(
                    f"- {source}: 工程未安装此依赖，设计稿只允许 react / antd / "
                    f"@ant-design/pro-components / @ant-design/icons。请改用白名单内"
                    f"的等价写法或内联 Mock 数据。"
                )
        return (
            False,
            "以下 import 引用了设计稿工程未安装或禁用的依赖，会导致 Vite 加载时"
            "“Failed to resolve import”白屏：\n" + "\n".join(hints),
        )
    undefined = _find_undefined_refs(code)
    if undefined:
        return (
            False,
            "以下组件在 JSX 中被使用但未 import 或未定义，会导致运行时 "
            "ReferenceError 白屏：" + ", ".join(undefined) + "。"
            "请在 import 语句中补充这些组件（Pro 系列从 "
            "@ant-design/pro-components、基础组件从 antd、图标从 "
            "@ant-design/icons 导入）。",
        )
    if isinstance(page, dict):
        contract_errors = validate_ui_design_code(page, code)
        if contract_errors:
            return (
                False,
                "UI 设计稿越过或遗漏了 ProductPlan 产品事实边界：\n"
                + "\n".join(f"- {item}" for item in contract_errors),
            )
    # 语法校验放最后：前三项是 LLM 高频错误，esbuild 查不出。
    return validate_tsx(project_dir, code)


def _build_repair_prompt(
    page: dict[str, Any], page_key: str, prev_code: str, errors: list[str]
) -> str:
    """构造修复 prompt：把前次代码与具体错误清单回喂 LLM 让其定向修正。"""

    error_block = "\n".join(f"- {e}" for e in errors)
    return (
        "You previously generated a React + antd5 + @ant-design/pro-components "
        ".tsx page for the page below, but it failed validation. Fix the issues "
        "and return the COMPLETE corrected .tsx file.\n\n"
        "Output rules (same as before):\n"
        "- Return a single .tsx file's source code ONLY. No markdown fences, "
        "no commentary.\n"
        "- Keep the parts that were correct; only fix the reported problems.\n"
        f"- The component name MUST be {page_key}, exported via "
        "`export default`.\n\n"
        + _product_fact_boundary_rules()
        + "--- PAGE TO DESIGN ---\n"
        f"{_page_brief(page)}\n"
        "--- END PAGE ---\n\n"
        "--- PREVIOUS CODE (has bugs) ---\n"
        f"{prev_code}\n"
        "--- END PREVIOUS CODE ---\n\n"
        "--- VALIDATION ERRORS TO FIX ---\n"
        f"{error_block}\n"
        "--- END ERRORS ---\n\n"
        "Return the full corrected .tsx file now."
    )


def _build_continuation_prompt(
    page: dict[str, Any], page_key: str, partial_code: str
) -> str:
    """构造截断续写 prompt：把已生成的部分代码回喂，让模型从断点续完。

    与整页 repair 的区别：repair 是"代码写完了但校验不过，定向改"；续写是
    "代码因 token 耗尽没写完，从断点接着写"。续写能复用已写好的前半部分，
    避免整页重生成再次消耗 thinking 预算（glm-5.2 复杂页 thinking 可达数万
    token，重生成大概率在同一处再次截断）。
    """

    return (
        "You previously started generating a React + antd5 + "
        "@ant-design/pro-components .tsx page for the page below, but the "
        "output was CUT OFF before completion because the output token limit "
        "was reached mid-file. The partial code is shown below.\n\n"
        "Continue writing the file from EXACTLY where it stopped. Output ONLY "
        "the remaining code that completes the component, ending with "
        f"`export default {page_key};`. Do NOT repeat any code that is already "
        "present, do NOT restart from the imports, do NOT add commentary or "
        "markdown fences. Begin your reply with the very next character that "
        "should follow the partial code.\n\n"
        "Keep the continuation CONCISE: prefer completing the current JSX "
        "structure over adding new sections, so the file finishes within the "
        "remaining token budget.\n\n"
        + _product_fact_boundary_rules()
        + "--- PAGE TO DESIGN ---\n"
        f"{_page_brief(page)}\n"
        "--- END PAGE ---\n\n"
        "--- PARTIAL CODE (cut off at the token limit, incomplete) ---\n"
        f"{partial_code}\n"
        "--- END PARTIAL CODE ---\n\n"
        "Output only the remaining code that completes this file now."
    )


def _build_adjust_prompt(
    page: dict[str, Any], page_key: str, prev_code: str, instruction: str
) -> str:
    """构造调整 prompt：把现有设计稿与用户调整指令回喂 LLM 让其定向修改。

    仿 _build_repair_prompt 的"前次代码 + 修改要求"模式，但替换错误清单为
    用户的自然语言调整指令。强调保留页面整体结构、仅按指令调整，仍遵循
    antd-ui-design skill 规范（纯视觉、内联 Mock、Pro 组件）。
    """

    return (
        "You previously generated a React + antd5 + @ant-design/pro-components "
        ".tsx page for the page below. The user has reviewed it and requested "
        "specific adjustments. Apply the adjustments and return the COMPLETE "
        "updated .tsx file.\n\n"
        "Output rules (same as initial generation):\n"
        "- Return a single .tsx file's source code ONLY. No markdown fences, "
        "no commentary.\n"
        "- Keep the parts the user did not ask to change; only apply the "
        "requested adjustments. Preserve the overall page structure, component "
        "selection, and layout unless the instruction explicitly changes them.\n"
        f"- The component name MUST be {page_key}, exported via "
        "`export default`.\n"
        "- Still follow the antd-ui-design skill: React + antd5 + "
        "@ant-design/pro-components, inline static Mock data (8-15 rows), "
        "ProTable uses `dataSource` not `request`, no API/useEffect/fetch, "
        "local-state handlers only, no layout shell.\n\n"
        + _product_fact_boundary_rules()
        + "--- PAGE TO DESIGN ---\n"
        f"{_page_brief(page)}\n"
        "--- END PAGE ---\n\n"
        "--- CURRENT DESIGN CODE (to be adjusted) ---\n"
        f"{prev_code}\n"
        "--- END CURRENT CODE ---\n\n"
        "--- USER ADJUSTMENT INSTRUCTION ---\n"
        f"{instruction}\n"
        "--- END INSTRUCTION ---\n\n"
        "Return the full adjusted .tsx file now."
    )


def generate_adjusted_page_react_code(
    page: dict[str, Any],
    page_key: str,
    project_dir: str,
    prev_code: str,
    instruction: str,
) -> str:
    """基于现有设计稿 + 用户调整指令调 LLM 重新生成，并校验+自动修复。

    结构与 generate_page_react_code 一致，只是 prompt 换成调整版（前次代码 +
    调整指令）。校验失败时回喂错误定向修复，最多重试 ui_design_max_retries 次。
    全部失败抛 ValueError，由调用方标记 generation_failed。
    """

    settings = Settings.from_env()
    model = _create_ui_design_model(settings)
    page_id = str(page.get("pageId") or page.get("id") or "")
    max_retries = max(0, settings.ui_design_max_retries)

    prompt = _build_adjust_prompt(page, page_key, prev_code, instruction)
    result = _invoke_ui_design_model(
        model,
        prompt,
        page_id=page_id,
        max_retries=max_retries,
    )
    content = _coerce_content_text(getattr(result, "content", ""))
    code = _extract_tsx_code(content)
    logger.info(
        "ui_design_adjusted page_id=%s attempt=1 code_chars=%s",
        page_id,
        len(code),
    )

    ok, err = validate_page_code(project_dir, code, page)
    attempt = 1
    while not ok and attempt <= max_retries:
        attempt += 1
        logger.warning(
            "ui_design_adjust_validate_failed page_id=%s attempt=%s err=%s",
            page_id,
            attempt - 1,
            err[:200],
        )
        repair_prompt = _build_repair_prompt(page, page_key, code, [err])
        result = _invoke_ui_design_model(
            model,
            repair_prompt,
            page_id=page_id,
            max_retries=max_retries,
        )
        content = _coerce_content_text(getattr(result, "content", ""))
        code = _extract_tsx_code(content)
        logger.info(
            "ui_design_adjust_repaired page_id=%s attempt=%s code_chars=%s",
            page_id,
            attempt,
            len(code),
        )
        ok, err = validate_page_code(project_dir, code, page)

    if not ok:
        raise ValueError(
            f"ui_design adjust validation failed after {attempt} attempts: {err[:300]}"
        )
    return code


def resolve_adjust_target_pages(
    pages: list[dict[str, Any]], instruction: str
) -> list[str]:
    """让大模型根据调整指令 + 所有页面信息，判断需要调整哪些页面。

    用户未通过 @页面名 显式指定目标时调用。返回需要调整的 pageId 列表。
    模型返回 JSON 数组（pageId 字符串）；解析失败或返回空时返回空列表，
    由调用方决定是否提示用户。
    """

    if not pages or not instruction.strip():
        return []
    settings = Settings.from_env()
    model = create_chat_model(
        settings,
        extra_model_kwargs={"thinking": {"type": "disabled"}},
    )
    page_briefs = "\n".join(
        f"- pageId: {p.get('pageId') or p.get('id') or ''}"
        f" | name: {p.get('name') or ''}"
        f" | path: {p.get('path') or '/'}"
        f" | description: {p.get('description') or ''}"
        for p in pages
        if isinstance(p, dict)
    )
    prompt = (
        "You are deciding which pages need UI design adjustments based on a "
        "user's natural-language instruction.\n\n"
        "Below is the list of pages in the application. Decide which pages the "
        "user's instruction applies to. Return ONLY a JSON array of pageId "
        "strings (the pageId values from the list below). No commentary, no "
        "markdown fences.\n\n"
        "If the instruction does not clearly apply to any page, return an empty "
        "array []. If it applies to all pages, include all pageIds.\n\n"
        "--- PAGES ---\n"
        f"{page_briefs}\n"
        "--- END PAGES ---\n\n"
        f"--- USER INSTRUCTION ---\n{instruction}\n--- END INSTRUCTION ---\n\n"
        "Return the JSON array now."
    )
    result = _invoke_ui_design_model(
        model,
        prompt,
        page_id="adjust-target-resolution",
        max_retries=max(0, settings.ui_design_max_retries),
    )
    content = _coerce_content_text(getattr(result, "content", "")).strip()
    # 去掉可能的 markdown 围栏。
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content).strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("resolve_adjust_target_pages parse_failed content=%s", content[:200])
        return []
    if not isinstance(parsed, list):
        return []
    valid_ids = {
        str(p.get("pageId") or p.get("id") or "").strip()
        for p in pages
        if isinstance(p, dict)
    }
    return [str(pid).strip() for pid in parsed if str(pid).strip() in valid_ids]


def generate_page_react_code(
    page: dict[str, Any], page_key: str, project_dir: str = ""
) -> str:
    """调用 LLM 为单个页面生成 React 设计稿 .tsx 代码，并校验+自动修复。

    生成后对代码做完整校验（非空 + 未定义引用 + esbuild 语法）。校验失败时
    把具体错误回喂 LLM 让其定向修正，最多重试 `ui_design_max_retries` 次。
    全部重试仍失败则抛出 ValueError，由调用方（Graph 节点）捕获并持久化为
    节点 generation_failed 状态——避免把白屏代码静默写入工程。

    project_dir 用于 esbuild 语法校验定位依赖；缺省时跳过语法校验（仅做
    非空与未定义引用检查，仍能拦截 LLM 高频错误）。
    """

    settings = Settings.from_env()
    model = _create_ui_design_model(settings)
    page_id = str(page.get("pageId") or page.get("id") or "")
    max_retries = max(0, settings.ui_design_max_retries)

    # 首次生成
    prompt = _build_ui_design_prompt(page, page_key)
    result = _invoke_ui_design_model(
        model,
        prompt,
        page_id=page_id,
        max_retries=max_retries,
    )
    raw_content = getattr(result, "content", "")
    content = _coerce_content_text(raw_content)
    code = _extract_tsx_code(content)
    logger.info(
        "ui_design_generated page_id=%s attempt=1 content_chars=%s code_chars=%s",
        page_id,
        len(content),
        len(code),
    )
    # 调试：content 类型、前200/后200字符，用于排查 thinking 残留导致提取错误
    logger.warning(
        "ui_design_debug page_id=%s content_type=%s content_head=%s content_tail=%s "
        "code_head=%s code_tail=%s",
        page_id,
        type(raw_content).__name__,
        repr(content[:200]),
        repr(content[-200:]) if len(content) > 200 else "",
        repr(code[:200]),
        repr(code[-200:]) if len(code) > 200 else "",
    )

    # 校验 + 自动修复重试闭环
    ok, err = validate_page_code(project_dir, code, page)
    attempt = 1
    continuation_used = False
    while not ok and attempt <= max_retries:
        # 截断优先续写：代码有实质内容但缺 export default，说明模型在 token
        # 耗尽前一直在正常写代码，只是没写完。此时整页 repair 会丢弃已写好的
        # 大半代码并再次消耗 thinking 预算（很可能在同一处再截断），续写只补
        # 剩余部分，省预算且更可能成功。续写不占 repair 重试次数。
        if _is_likely_truncated(code) and not continuation_used:
            continuation_used = True
            logger.warning(
                "ui_design_truncated page_id=%s code_chars=%s，尝试断点续写",
                page_id,
                len(code),
            )
            continuation_prompt = _build_continuation_prompt(page, page_key, code)
            result = _invoke_ui_design_model(
                model,
                continuation_prompt,
                page_id=page_id,
                max_retries=max_retries,
            )
            raw_content = getattr(result, "content", "")
            content = _coerce_content_text(raw_content)
            tail = _extract_tsx_code(content)
            # 续写可能仍带 thinking 碎片/重复开头，拼接前剥掉尾部块里的 import
            # 与已存在的前缀，只保留真正的"后续代码"。
            completed = _merge_truncated_code(code, tail)
            logger.info(
                "ui_design_continued page_id=%s tail_chars=%s merged_chars=%s",
                page_id,
                len(tail),
                len(completed),
            )
            code = completed
            ok, err = validate_page_code(project_dir, code, page)
            if ok:
                break
            # 续写后仍不过：若非截断问题则落入下方 repair；若仍截断则放弃续写
            # （continuation_used 已置位），走整页 repair。

        attempt += 1
        logger.warning(
            "ui_design_validate_failed page_id=%s attempt=%s err=%s",
            page_id,
            attempt - 1,
            err[:200],
        )
        repair_prompt = _build_repair_prompt(page, page_key, code, [err])
        result = _invoke_ui_design_model(
            model,
            repair_prompt,
            page_id=page_id,
            max_retries=max_retries,
        )
        raw_content = getattr(result, "content", "")
        content = _coerce_content_text(raw_content)
        code = _extract_tsx_code(content)
        logger.info(
            "ui_design_repaired page_id=%s attempt=%s code_chars=%s",
            page_id,
            attempt,
            len(code),
        )
        logger.warning(
            "ui_design_debug_repair page_id=%s content_type=%s "
            "content_head=%s code_head=%s code_tail=%s",
            page_id,
            type(raw_content).__name__,
            repr(content[:200]),
            repr(code[:200]),
            repr(code[-200:]) if len(code) > 200 else "",
        )
        ok, err = validate_page_code(project_dir, code, page)

    if not ok:
        # 全部重试仍失败：抛出，由节点标记 generation_failed，不写白屏代码。
        raise ValueError(
            f"ui_design code validation failed after {attempt} attempts: {err[:300]}"
        )
    return code


# ---------------------------------------------------------------------------
# PageKey / 菜单路径派生
# ---------------------------------------------------------------------------


def _page_key_from_page_id(page_id: str) -> str:
    """从 pageId 派生 PascalCase 的 PageKey，用作目录名与菜单 key。

    规则：按 _ / - / 空格分段 → 每段首字母大写拼接，保留所有段（含 page 后缀）。
    例：order_list_page → OrderListPage，dashboard_page → DashboardPage，
    login_page → LoginPage，user_detail_page → UserDetailPage。

    与 build_context_resolver 和 frontend_scaffold 的共享调用保持一致，
    避免 UI 确认、模板初始化和任务拆分阶段看到不同的 PageKey。
    """

    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(page_id or "page")).strip("-")
    segments = [s for s in re.split(r"[-_\s]+", cleaned) if s]
    if not segments:
        return "Page"
    pascal = "".join(seg[:1].upper() + seg[1:].lower() for seg in segments)
    # 确保以字母开头
    if not pascal[:1].isalpha():
        pascal = "Page" + pascal
    return pascal


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
    """读取已落盘的单页 .tsx 代码，缺失时返回 None。

    兼容旧命名：统一 PageKey 命名前，设计稿目录用不带 Page 后缀的 key
    （dashboard_page → Dashboard）。若新 key 目录不存在，回退查找旧 key 目录，
    使已有工作区无需重新生成设计稿即可继续。
    """

    target = pages_dir(project_dir) / page_key / "index.tsx"
    code = _read_page_file(target)
    if code is not None:
        return code
    legacy_key = _legacy_page_key(page_key)
    if legacy_key and legacy_key != page_key:
        legacy_target = pages_dir(project_dir) / legacy_key / "index.tsx"
        return _read_page_file(legacy_target)
    return None


def delete_page_code(project_dir: str, page_key: str) -> None:
    """删除单页已落盘设计稿，供"重新生成"绕过 load_page_code 的复用。

    删除整个页面目录（index.tsx 及同目录其他文件）。目录不存在时静默返回，
    与 load_page_code 的缺失语义一致。
    """

    target_dir = pages_dir(project_dir) / page_key
    if not target_dir.exists():
        return
    try:
        import shutil

        shutil.rmtree(target_dir)
    except OSError:
        logger.warning("ui_design_delete_failed page_key=%s", page_key)


# 页面模板在 Frontend 工程的源码目录，与前端 templateService 的 import.meta.glob
# （../templates/*/manifest.json）对应。后端用 REPOSITORY_ROOT 定位 Frontend 工程。
_TEMPLATES_DIR = REPOSITORY_ROOT / "Frontend" / "src" / "renderer" / "src" / "templates"


def load_template_source(template_id: str) -> str:
    """按 manifest.id 读取页面模板的 index.tsx 源码，供选模板作设计稿时直接落盘。

    遍历 templates/*/manifest.json 匹配 id，返回对应目录下的 index.tsx 内容。
    模板源码是成熟可运行的 Pro 组件页面，直接用作设计稿无需 LLM 生成或校验。
    找不到模板时抛 ValueError，由调用方（ui_confirmation 节点）捕获标记失败。
    """

    template_id = str(template_id or "").strip()
    if not template_id:
        raise ValueError("load_template_source: template_id 为空。")
    if not _TEMPLATES_DIR.is_dir():
        raise ValueError(
            f"load_template_source: 模板目录不存在：{_TEMPLATES_DIR}。"
            "请确认 Frontend 工程的 src/renderer/src/templates 已就绪。"
        )
    for entry in _TEMPLATES_DIR.iterdir():
        if not entry.is_dir():
            continue
        manifest_path = entry / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(manifest.get("id") or "").strip() != template_id:
            continue
        index_path = entry / "index.tsx"
        if not index_path.is_file():
            raise ValueError(
                f"load_template_source: 模板 {template_id} 缺少 index.tsx：{index_path}。"
            )
        return index_path.read_text(encoding="utf-8")
    raise ValueError(
        f"load_template_source: 未找到 id={template_id} 的页面模板，"
        f"已扫描目录：{_TEMPLATES_DIR}。"
    )


def _read_page_file(target: Path) -> str | None:
    """读取单个 .tsx 文件，缺失或不可读时返回 None。"""

    if not target.is_file():
        return None
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _legacy_page_key(page_key: str) -> str:
    """返回旧命名约定下的 PageKey（去掉末尾 Page 段）。

    统一命名前的 _page_key_from_page_id 会去掉末尾 page 段
    （DashboardPage → Dashboard）。这里反向推导，仅用于回退查找已落盘的旧目录。
    """

    if not page_key:
        return ""
    # PascalCase → snake_case 再去末尾 page 段，复用与旧实现等价的拆分
    spaced = re.sub(r"(?<!^)(?=[A-Z])", "_", str(page_key)).lower()
    segments = [s for s in spaced.split("_") if s]
    if segments and segments[-1] == "page":
        segments = segments[:-1]
    if not segments:
        return ""
    return "".join(seg[:1].upper() + seg[1:] for seg in segments)


def _esbuild_main_path() -> str | None:
    """在 Frontend 工程的 pnpm .pnpm 目录下定位 esbuild 的 lib/main.js。

    方案 B：设计稿不再 clone 模板工程（也就没有自己的 node_modules），
    esbuild 改从主 Frontend 工程找——它是 vite 的间接依赖，.pnpm 下有
    esbuild@*。REPOSITORY_ROOT 是 repo 根，Frontend 工程在其下。
    """

    pnpm_dir = REPOSITORY_ROOT / "Frontend" / "node_modules" / ".pnpm"
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

    project_dir 参数保留以兼容调用方签名，但方案 B 下 esbuild 改从 Frontend
    工程查找（见 _esbuild_main_path），不再依赖设计稿工程的 node_modules。
    """

    main_path = _esbuild_main_path()
    if not main_path:
        logger.warning("ui_design_validate_skip esbuild_not_found")
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
        # 显式指定 UTF-8：Windows 上 text=True 默认用 locale 编码（中文系统为 GBK），
        # 当生成代码含 GBK 无法编码的字符时，写 stdin 会抛 UnicodeEncodeError；
        # errors="replace" 同时避免子进程输出含异常字节时再次中断校验。
        proc = subprocess.run(
            ["node", "-e", script, main_path],
            input=code,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError, UnicodeError) as exc:
        logger.warning("ui_design_validate_error %s", exc)
        return True, ""
    if proc.returncode == 0:
        return True, ""
    return False, proc.stderr.strip() or "esbuild syntax error"
