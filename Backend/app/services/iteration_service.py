"""应用迭代服务：发起新迭代时清空规划产物并生成迭代上下文总结。"""

from __future__ import annotations

from app.branding import WORKSPACE_ARTIFACT_DIR

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from app.services.repository_branch import read_workspace_branch_name
from app.services.iteration_artifacts import begin_iteration_round
from app.services.product_plan_carryover import (
    DEFINITION_FIELDS as _DEFINITION_FIELDS,
    write_product_plan_carryover,
)
from app.services.ui_design_carryover import (
    carried_page_keys,
    write_ui_design_carryover,
)


class IterationError(ValueError):
    """表示发起新迭代时出错。"""


class StartIterationRequest(BaseModel):
    """校验一次发起新迭代请求。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["start_iteration"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1)
    # 发起新迭代所基于的分支名，仅用于写 AGENTS.md 的迭代标题。
    branch_name: str = Field(alias="branchName", min_length=1, max_length=255)
    description: str = Field(default="")


class StartIterationResult(BaseModel):
    """返回发起新迭代后的工作区状态。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["start_iteration"] = "start_iteration"
    workspace_root: str = Field(alias="workspaceRoot")
    cleared: bool


# 发起新迭代时清空的 .devagentstudio 子目录（如果存在）。
# 只登记**平台自己产生**的规划/运行态目录；未列名的一律保留。
_CLEARABLE_DIRS = {
    "specs",
    "plans",
    "drafts",
    "cache",
    "checkpoints",
    "datasource",
    "ui-design",
    # 预览运行态（PID、日志、preview-runtime.json）：瞬时数据，随迭代作废。
    "runtime",
    # 物化事务暂存目录：只可能残留自中断的 Bootstrap。
    "bootstrap-staging",
}

# 发起新迭代时清空的 .devagentstudio 顶层文件（除保留项外）。
#
# 注意这是一份**显式清单**：没列进来的文件一律保留。`iteration-artifacts.json`
# （每轮的页面计划/设计事实）正是靠这一点跨迭代存活 —— 它是版本维度的历史，
# 清了就没法回答"这个产物是哪一轮做的"。别把它加进来。
_CLEARABLE_FILES = {
    "application-lifecycle.json",
    "application-lifecycle.json.bak",
}


def start_iteration(request: StartIterationRequest) -> StartIterationResult:
    """清空 .devagentstudio 规划产物，保留应用工程本体与迭代上下文。

    **不重建工程**：新迭代是在已有代码上继续加功能，因此 frontend/backend/.git 与
    template-state.json 一律保留——那份代码（模板 + 历次迭代累积的业务代码）就是产品本身。
    模板请求只由 application.json 派生，各迭代完全一致，所以现有模板本来就合格，
    下一次 Bootstrap 会识别"已物化"直接进入就绪态，不会重新拉取（见
    `workspace_bootstrap/readiness.py::classify_workspace_template`）。

    保留 AGENTS.md / application.json / template-state.json；清空 specs/plans/drafts/
    checkpoints/ui-design 与 lifecycle 快照。

    调用方负责在前端重置 lifecycle 为 collecting_requirement 并更新分支记录。
    """

    workspace_root = _resolve_workspace_root(request.workspace_root)
    devagentstudio_dir = workspace_root / WORKSPACE_ARTIFACT_DIR
    if not devagentstudio_dir.is_dir():
        raise IterationError("工作区缺少 .devagentstudio 目录，无法发起新迭代。")

    # 先结束上一轮遗留的工作台 execution 并取消在途 run，
    # 避免旧 workflow 持续推送 lifecycle 事件把界面拉回验收阶段。
    _stop_active_workbench_executions(workspace_root)

    # 再停预览，且必须早于任何文件清理（见 _stop_workspace_preview 的说明）。
    _stop_workspace_preview(workspace_root)

    cleared = _clear_iteration_artifacts(
        devagentstudio_dir,
        workspace_root=workspace_root,
        branch_name=request.branch_name,
    )

    return StartIterationResult(
        workspaceRoot=str(workspace_root),
        cleared=cleared,
    )


def _stop_workspace_preview(workspace_root: Path) -> None:
    """停止工作区预览进程；必须早于本次迭代的任何文件清理。

    预览前端以 `<workspace>/frontend` 为工作目录运行，PID 记录在
    `.devagentstudio/runtime/launch/frontend.pid`。清空 `.devagentstudio` 会连这个 PID 文件一起删掉，
    之后再想停就找不到进程；而仍在运行的 Vite 会用 `mkdir(recursive)` 重建
    `frontend/.vite/deps`，让下一次 Bootstrap 的前置校验误判成"已存在受管产物：frontend"
    而拒绝生成模板——用户看到的就是"确认保存"后报错、规划会话终止。
    """

    from app.services.project_launcher import stop_project_preview

    result = stop_project_preview(workspace_root)
    if result.get("status") == "failed":
        # 停止失败不阻断迭代：文件清理照常进行，残留进程由下一次 Bootstrap 的校验兜底暴露。
        print(f"[iteration] 停止工作区预览失败：{result.get('message')}")


def _stop_active_workbench_executions(workspace_root: Path) -> None:
    """结束当前工作区全部活跃工作台 execution，取消在途 run 并释放资源锁。"""
    from app.services.application_lifecycle import (
        end_workbench_execution,
        load_application_lifecycle,
    )
    from app.services.workspace_process_registry import workspace_process_registry

    lifecycle = load_application_lifecycle(workspace_root)
    if lifecycle is None:
        return
    for run_id in list(lifecycle.active_executions.keys()):
        workspace_process_registry.cancel_run(run_id)
        try:
            end_workbench_execution(workspace_root, run_id=run_id, missing_ok=True)
        except Exception:
            # 单个 execution 收口失败不应阻断迭代清理；文件级清空仍会继续。
            pass


def generate_agents_context(
    workspace_root: str | Path,
    *,
    branch_name: str,
    description: str,
) -> None:
    """提交推送时生成/追加 .devagentstudio/AGENTS.md 迭代上下文总结。

    读取当前 specs/plans 产物，提取摘要追加到 AGENTS.md。
    如果文件已存在（之前迭代生成过），追加新段落；否则新建。
    """

    root = Path(workspace_root).expanduser().resolve()
    devagentstudio = root / WORKSPACE_ARTIFACT_DIR
    agents_md = devagentstudio / "AGENTS.md"

    section = _build_iteration_section(
        devagentstudio, branch_name=branch_name, description=description
    )

    if agents_md.exists():
        existing = agents_md.read_text(encoding="utf-8")
        agents_md.write_text(existing.rstrip() + "\n\n" + section, encoding="utf-8")
    else:
        header = "# 迭代上下文\n\n> 本文件记录每轮迭代的设计与计划产物摘要，供下一轮迭代的大模型作为起点。\n\n"
        agents_md.write_text(header + section, encoding="utf-8")


def _clear_iteration_artifacts(
    devagentstudio_dir: Path,
    *,
    workspace_root: Path,
    branch_name: str,
) -> bool:
    """删除本轮的规划与运行态产物，**未列名的一律保留**。

    这里刻意不做"未知条目一律删除"的兜底：`.devagentstudio` 同时承载平台数据与**模板契约**
    （见 `materializer._materialization_targets`，模板 ZIP 可交付任意 `.devagentstudio/<子项>`，
    例如 `.devagentstudio/template-contracts/route-projector.json`）。新迭代沿用已有工程、
    不再重新物化，被删掉的模板契约就没有任何东西能把它补回来——构建期会以
    "模板缺少 Route Projector Descriptor" 这类难以定位的错误暴露出来。

    因此只删明确登记的平台产物；AGENTS.md、application.json、template-state.json
    以及全部模板契约都原样保留。

    `ui-design` 是唯一的例外：清空它之前先登记上一轮**已确认**的设计稿，
    这些页面的代码目录按登记的 PageKey 保留下来供新迭代继承（见 `ui_design_carryover`）。
    必须在删除 `specs/` 之前读 manifest，否则登记不到任何东西。
    """

    # 先登记继承来源与新一轮的开始，再清空 —— 顺序不能反：manifest 与上一轮
    # ProductPlan 都在即将被清掉的目录里。
    _register_ui_design_carryover(workspace_root, devagentstudio_dir)
    _register_product_plan_carryover(workspace_root, devagentstudio_dir)
    begin_iteration_round(workspace_root, branch_name)
    carried_keys = carried_page_keys(workspace_root)

    cleared_any = False
    for entry in devagentstudio_dir.iterdir():
        name = entry.name
        if entry.is_dir() and name in _CLEARABLE_DIRS:
            if name == "ui-design" and carried_keys:
                # 只清掉没被继承的设计稿，保留登记过的页面代码目录。
                _clear_ui_design_except(entry, carried_keys)
            else:
                shutil.rmtree(entry, ignore_errors=True)
            cleared_any = True
        elif entry.is_file() and name in _CLEARABLE_FILES:
            entry.unlink(missing_ok=True)
            cleared_any = True
    return cleared_any


def _register_ui_design_carryover(workspace_root: Path, devagentstudio_dir: Path) -> None:
    """把上一轮状态为 confirmed 的页面登记为下一轮的继承来源。

    只登记 `confirmed`：只有真正产出并通过校验的设计稿才值得继承，
    `pending`/`queued`/`generation_failed` 描述的是"本轮没做完的事"，
    必须由新迭代按新计划重新推导，不能继承。

    没有可继承的页面时会清掉旧记录 —— 否则新迭代会继承到更早一轮的来源。
    """

    try:
        manifest = json.loads(
            (devagentstudio_dir / "specs" / "ui-designs.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        # 读不到 manifest（首轮、或从未进入设计阶段）：没有可继承的东西。
        write_ui_design_carryover(workspace_root, source_branch="", pages={})
        return

    raw_pages = manifest.get("pages") if isinstance(manifest, dict) else None
    pages: dict[str, dict[str, str]] = {}
    for page in raw_pages if isinstance(raw_pages, list) else []:
        if not isinstance(page, dict) or str(page.get("status") or "") != "confirmed":
            continue
        page_id = str(page.get("pageId") or "").strip()
        page_key = str(page.get("page_key") or "").strip()
        if not page_id or not page_key:
            continue
        pages[page_id] = {
            "pageKey": page_key,
            "templateId": str(page.get("template_id") or "").strip(),
            "templateSourcePath": str(page.get("template_source_path") or "").strip(),
        }

    write_ui_design_carryover(
        workspace_root,
        source_branch=read_workspace_branch_name(workspace_root),
        pages=pages,
    )


def _register_product_plan_carryover(workspace_root: Path, devagentstudio_dir: Path) -> None:
    """登记上一轮的页面定义，供下一轮对「用户没要求改的页面」整份沿用。

    新一轮会整份重新生成 ProductPlan，模型会顺手重写其余页面的信息项与操作 ——
    语义没变但 `itemId` 全换，于是上一轮按旧 ID 产出的设计稿对不上、继承被拒，
    用户被迫重做本可沿用的页面。

    登记两样东西：
    - `requirement`：上一轮 RequirementSpec 的该页条目 —— 下一轮用它判断"用户改没改这一页"
    - `definition`：上一轮 ProductPlan 里模型补充的那部分 —— 未变时整份沿用

    读不到任一份产物（首轮、或从未进入规划）时清掉旧记录 —— 否则会沿用更早一轮的内容。
    """

    try:
        plan = json.loads(
            (devagentstudio_dir / "plans" / "product-plan.json").read_text(encoding="utf-8")
        )
        spec = json.loads(
            (devagentstudio_dir / "specs" / "requirement-spec.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        write_product_plan_carryover(workspace_root, source_branch="", pages={})
        return

    requirement_pages = {
        str(page.get("pageId") or "").strip(): page
        for page in _dict_entries(spec.get("pages") if isinstance(spec, dict) else None)
        if str(page.get("pageId") or "").strip()
    }
    pages: dict[str, dict[str, Any]] = {}
    for page in _dict_entries(plan.get("pages") if isinstance(plan, dict) else None):
        page_id = str(page.get("pageId") or "").strip()
        # 两半缺一不可：没有 requirement 无从判断"变没变"，没有 definition 没有可沿用的内容。
        if not page_id or page_id not in requirement_pages:
            continue
        pages[page_id] = {
            "requirement": requirement_pages[page_id],
            "definition": {field: page.get(field) for field in _DEFINITION_FIELDS},
        }

    write_product_plan_carryover(
        workspace_root,
        source_branch=read_workspace_branch_name(workspace_root),
        pages=pages,
    )


def _dict_entries(value: Any) -> list[dict[str, Any]]:
    """把未知值收敛成字典条目列表。"""

    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]

    raw_pages = plan.get("pages") if isinstance(plan, dict) else None
    pages: dict[str, dict[str, list[str]]] = {}
    for page in raw_pages if isinstance(raw_pages, list) else []:
        if not isinstance(page, dict):
            continue
        page_id = str(page.get("pageId") or "").strip()
def _clear_ui_design_except(ui_design_dir: Path, keep_keys: set[str]) -> None:
    """清空 `ui-design/`，但保留 `pages/<keep_keys 中的 PageKey>/` 这些设计稿目录。"""

    for entry in ui_design_dir.iterdir():
        if entry.name != "pages" or not entry.is_dir():
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            continue
        for page_entry in entry.iterdir():
            if page_entry.is_dir() and page_entry.name in keep_keys:
                continue
            if page_entry.is_dir():
                shutil.rmtree(page_entry, ignore_errors=True)
            else:
                page_entry.unlink(missing_ok=True)


def _build_iteration_section(
    devagentstudio: Path,
    *,
    branch_name: str,
    description: str,
) -> str:
    """从 application.json + specs/plans/endpoints/datasource/ui-designs 构建完整的迭代摘要。"""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines: list[str] = [f"## 分支 {branch_name}（{now}）", ""]

    if description.strip():
        lines.append(f"**变更说明**：{description.strip()}")
        lines.append("")

    # —— 应用配置（application.json）——
    app_config = _load_json(devagentstudio / "application.json")
    if app_config:
        lines.append("### 应用现状")
        lines.append("")
        app_name = str(app_config.get("appName") or app_config.get("name") or "").strip()
        senario = str(app_config.get("senario") or "").strip()
        terminal = str(app_config.get("terminal") or "").strip()
        if app_name:
            lines.append(f"- 应用名称：{app_name}")
        if senario:
            lines.append(f"- 应用场景：{senario}")
        if terminal:
            lines.append(f"- 目标终端：{terminal}")
        layout = app_config.get("layout") or {}
        if isinstance(layout, dict) and layout:
            layout_type = str(layout.get("type") or "").strip()
            use_header = layout.get("useHeader")
            use_footer = layout.get("useFooter")
            layout_parts: list[str] = []
            if layout_type:
                layout_parts.append(f"布局={layout_type}")
            if use_header is not None:
                layout_parts.append(f"页头={'启用' if use_header else '禁用'}")
            if use_footer is not None:
                layout_parts.append(f"页脚={'启用' if use_footer else '禁用'}")
            if layout_parts:
                lines.append(f"- 导航布局：{', '.join(layout_parts)}")
        theme = app_config.get("theme") or {}
        if isinstance(theme, dict) and theme.get("primaryColor"):
            lines.append(f"- 主题色：{theme['primaryColor']}")
        datasource_cfg = app_config.get("datasource") or {}
        if isinstance(datasource_cfg, dict) and datasource_cfg.get("type"):
            lines.append(f"- 数据源类型：{datasource_cfg['type']}")
        auth = app_config.get("auth") or {}
        if isinstance(auth, dict):
            auth_enabled = auth.get("enable")
            if auth_enabled is not None:
                lines.append(f"- 认证：{'启用' if auth_enabled else '不启用'}")
        authz = app_config.get("authorization") or {}
        if isinstance(authz, dict) and authz.get("enabled") is not None:
            lines.append(f"- 权限控制：{'启用' if authz['enabled'] else '不启用'}")
        menus = app_config.get("menus") or {}
        if isinstance(menus, dict) and menus.get("enable"):
            menu_items = menus.get("items") or []
            if isinstance(menu_items, list) and menu_items:
                lines.append(f"- 菜单：已启用（{len(menu_items)} 个菜单项）")
        pages_list = app_config.get("pages") or []
        if isinstance(pages_list, list) and pages_list:
            lines.append(f"- 已有页面：{', '.join(str(p) for p in pages_list)}")
        lines.append("")

    # —— 需求规格 ——
    spec = _load_json(devagentstudio / "specs" / "requirement-spec.json")
    if spec:
        lines.append("### 需求规格")
        lines.append("")
        summary = str(spec.get("summary") or spec.get("source_request") or "").strip()
        if summary:
            lines.append(f"> {summary}")
            lines.append("")
        roles = spec.get("user_roles") or []
        if isinstance(roles, list) and roles:
            lines.append("**用户角色**：")
            for role in roles:
                if isinstance(role, dict):
                    role_desc = str(role.get("description") or "").strip()
                    lines.append(f"- {role.get('name', '')}：{role_desc}" if role_desc else f"- {role.get('name', '')}")
            lines.append("")
        modules = spec.get("feature_modules") or []
        if isinstance(modules, list) and modules:
            lines.append("**功能模块**：")
            for module in modules:
                if isinstance(module, dict):
                    mod_desc = str(module.get("description") or "").strip()
                    mod_priority = str(module.get("priority") or "").strip()
                    priority_tag = f"（{mod_priority}）" if mod_priority else ""
                    lines.append(f"- {module.get('name', '')}{priority_tag}：{mod_desc}" if mod_desc else f"- {module.get('name', '')}{priority_tag}")
            lines.append("")

    # —— 产品计划（页面详情）——
    product_plan = _load_json(devagentstudio / "plans" / "product-plan.json")
    if product_plan:
        lines.append("### 产品计划")
        lines.append("")
        pages = product_plan.get("pages") or []
        if isinstance(pages, list) and pages:
            lines.append("**页面**：")
            for page in pages:
                if isinstance(page, dict):
                    page_id = page.get("pageId") or page.get("id") or ""
                    page_name = page.get("name") or ""
                    page_path = page.get("path") or ""
                    page_desc = str(page.get("description") or "").strip()
                    page_goal = str(page.get("goal") or "").strip()
                    info_items = page.get("information_items") or []
                    lines.append(f"- **{page_name}**（{page_id}，路径：{page_path}）")
                    if page_desc:
                        lines.append(f"  - 描述：{page_desc}")
                    if page_goal:
                        lines.append(f"  - 目标：{page_goal}")
                    if isinstance(info_items, list) and info_items:
                        item_names = [
                            str(item.get("name", "")).strip()
                            for item in info_items
                            if isinstance(item, dict)
                        ]
                        # 过滤空名：名称为空时 ', '.join 会产出悬空的 ", "，
                        # 那行尾空格会被提交前的 `git diff --check` 判为空白错误，
                        # 反过来挡住用户提交（见 workspace_commit_check 的说明）。
                        item_names = [name for name in item_names if name]
                        if item_names:
                            lines.append(f"  - 信息项：{', '.join(item_names)}")
            lines.append("")
        flows = product_plan.get("business_flows") or []
        if isinstance(flows, list) and flows:
            lines.append("**业务流程**：")
            for flow in flows:
                if isinstance(flow, dict):
                    flow_desc = str(flow.get("description") or "").strip()
                    flow_steps = flow.get("steps") or []
                    lines.append(f"- {flow.get('name', '')}")
                    if flow_desc:
                        lines.append(f"  - {flow_desc}")
                    if isinstance(flow_steps, list) and flow_steps:
                        for step in flow_steps:
                            lines.append(f"  - 步骤：{step}")
            lines.append("")

    # —— UI 设计稿摘要 ——
    ui_designs = _load_json(devagentstudio / "specs" / "ui-designs.json")
    if ui_designs:
        designs = ui_designs.get("designs") or ui_designs.get("pages") or []
        if isinstance(designs, list) and designs:
            lines.append("### UI 设计稿")
            lines.append("")
            for design in designs:
                if isinstance(design, dict):
                    d_page = design.get("pageId") or design.get("page") or ""
                    d_desc = str(design.get("description") or design.get("summary") or "").strip()
                    lines.append(f"- {d_page}：{d_desc}" if d_desc else f"- {d_page}")
            lines.append("")

    # —— 技术计划（架构 + 实体字段）——
    tech_plan = _load_json(devagentstudio / "plans" / "technical-plan.json")
    if tech_plan:
        lines.append("### 技术计划")
        lines.append("")
        arch = tech_plan.get("architecture") or {}
        if isinstance(arch, dict):
            frontend = str(arch.get("frontend") or "").strip()
            backend = str(arch.get("backend") or "").strip()
            data = str(arch.get("data") or "").strip()
            if frontend:
                lines.append(f"- 前端架构：{frontend}")
            if backend:
                lines.append(f"- 后端架构：{backend}")
            if data:
                lines.append(f"- 数据层：{data}")
        entities = tech_plan.get("entities") or []
        if isinstance(entities, list) and entities:
            lines.append("")
            lines.append("**实体**：")
            for entity in entities:
                if isinstance(entity, dict):
                    e_name = entity.get("name") or ""
                    e_id = entity.get("id") or ""
                    e_desc = str(entity.get("description") or "").strip()
                    fields = entity.get("fields") or []
                    lines.append(f"- **{e_name}**（{e_id}）")
                    if e_desc:
                        lines.append(f"  - 描述：{e_desc}")
                    if isinstance(fields, list) and fields:
                        for field in fields:
                            if isinstance(field, dict):
                                f_name = field.get("name") or ""
                                f_label = field.get("label") or ""
                                f_type = field.get("type") or ""
                                f_required = "必填" if field.get("required") else "选填"
                                lines.append(f"  - {f_label}（{f_name}）：{f_type}，{f_required}")
            lines.append("")

    # —— 接口设计（请求/响应摘要）——
    endpoints_dir = devagentstudio / "plans" / "endpoints"
    if endpoints_dir.is_dir():
        endpoint_files = sorted(endpoints_dir.glob("*.json"))
        if endpoint_files:
            lines.append("### 接口设计")
            lines.append("")
            for ef in endpoint_files:
                ep = _load_json(ef)
                if ep and isinstance(ep, dict):
                    ep_name = ep.get("name") or ef.stem
                    ep_desc = str(ep.get("description") or "").strip()
                    ep_method = str(ep.get("method") or ep.get("httpMethod") or "").strip()
                    ep_path = str(ep.get("path") or ep.get("url") or "").strip()
                    lines.append(f"- **{ep_name}**")
                    if ep_method or ep_path:
                        lines.append(f"  - {ep_method} {ep_path}")
                    if ep_desc:
                        lines.append(f"  - 描述：{ep_desc}")
            lines.append("")

    # —— 数据源配置 ——
    datasource = _load_json(devagentstudio / "datasource" / "index.json")
    if datasource:
        sources = datasource.get("sources") or []
        if isinstance(sources, list) and sources:
            lines.append("### 数据源")
            lines.append("")
            for src in sources:
                if isinstance(src, dict):
                    src_name = src.get("name") or ""
                    src_type = src.get("type") or ""
                    src_url = str(src.get("baseUrl") or "").strip()
                    lines.append(f"- **{src_name}**（类型：{src_type}）")
                    if src_url:
                        lines.append(f"  - 地址：{src_url}")
            lines.append("")

    # 逐行清掉行尾空白：本函数有十几处 lines.append 由不同数据源拼字符串，
    # 任一处的空值都可能留下行尾空格，而提交前的 `git diff --check` 会因此
    # 挡住用户提交 —— 平台自己生成的文件不该把用户卡在门外。
    return "\n".join(line.rstrip() for line in lines).rstrip() + "\n"


def _load_json(path: Path) -> dict[str, Any] | None:
    """安全加载 JSON 文件，失败返回 None。"""

    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _resolve_workspace_root(value: str) -> Path:
    """解析并校验工作区根目录。"""

    root = Path(value).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise IterationError("工作目录不存在或不是文件夹。")
    return root
