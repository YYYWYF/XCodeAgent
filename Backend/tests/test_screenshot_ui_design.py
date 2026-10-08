from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from app.agents.screenshot_requirements.models import RequirementInput
from app.agents.screenshot_ui_design.agent import (
    NormalizedReference,
    SCREENSHOT_UI_PIPELINE_VERSION,
    _adopt_current_generated_page,
    _analyze_references,
    _audit_failure_reasons,
    _generate_page_entry,
    _normalize_reference,
    _reusable_manifest,
    _sidebar_contains_unique_actions,
    _usable_content_candidate,
    _validate_screenshot_content,
    _validate_with_static_fixes,
    prepare_screenshot_ui_designs,
    regenerate_screenshot_ui_page,
)
from app.agents.screenshot_ui_design.contract_scaffold import (
    build_contract_scaffold,
    repair_contract_attributes,
    repair_equivalent_action_aliases,
)
from app.agents.screenshot_ui_design.dynamic_bindings import (
    repair_literal_collection_bindings,
    repair_mapped_card_bindings,
    repair_mapped_interface_effects,
)
from app.agents.screenshot_ui_design.finite_bindings import repair_finite_collection_bindings
from app.agents.screenshot_ui_design.semantic_bindings import repair_equivalent_visible_information
from app.agents.screenshot_ui_design.images import (
    detect_sidebar_width_ratio,
    load_ui_reference_images,
    page_images,
)
from app.agents.screenshot_ui_design.models import (
    ScreenshotBounds,
    ScreenshotLayoutRegion,
    ScreenshotSingleImagePageDecision,
    ScreenshotPageMapping,
    ScreenshotUiAnalysis,
    ScreenshotVisualAudit,
    ScreenshotVisualObservation,
)
from app.agents.screenshot_ui_design.prompts import (
    _visual_reference_text,
    build_mapping_prompt,
)
from app.agents.screenshot_ui_design.render_prompt import build_screenshot_render_prompt
from app.agents.screenshot_ui_design.runtime_styles import validate_screenshot_runtime_styles
from app.agents.screenshot_ui_design.shell_reuse import (
    recompose_page_with_shared_shell,
    refreshed_shared_shell_code,
)
from app.agents.screenshot_ui_design.shared_shell import SharedShell, bind_existing_sidebar_information
from app.agents.screenshot_ui_design.shared_shell import (
    compose_shared_shell,
    derive_shared_shell,
    remove_generated_sidebar,
    split_page_contract,
    validate_content_without_shell,
)
from app.agents.screenshot_ui_design.transport import _content_blocks, invoke_vision_json
from app.agents.screenshot_ui_design.targeted_repair import TsxRepairPatch
from app.agents.screenshot_ui_design.visibility_contract import validate_visible_bindings
from app.graph.application_planning_workflow import _route_product_planning
from app.graph.nodes.ui_confirmation import (
    _is_screenshot_sidebar_alignment,
    _recompose_screenshot_sidebars,
    _verified_ui_designs_for_confirmation,
)
from app.protocols.workflow.definition import WORKFLOW_NODE_LABELS
from app.protocols.workflow import build_workflow_ag_ui_stream
from app.protocols.workflow.projection import _workflow_next_nodes, _workflow_start_node
from app.services.ui_design_generator import persist_page_code
from app.services.ui_design_manifest import build_ui_page_manifest, validate_ui_design_code
from app.services.ui_design_project_setup import setup_ui_design_project
from app.workspace.spec_documents import load_ui_designs_json


VALID_TSX = """import React from 'react';

const DashboardPage = () => (
  <div style={{ padding: 24, minHeight: 420, fontFamily: 'Inter, PingFang SC, Microsoft YaHei, sans-serif', fontSize: '14px', background: '#eef2f7', color: '#1f2937', borderColor: '#d9e0e8' }}>
    <header data-visual-region-id="page_heading" style={{ background: '#ffffff', color: '#667085' }}>
      <h1 style={{ fontSize: '24px', color: '#1677ff' }}>项目概览</h1>
    </header>
    <section data-visual-region-id="content_panel">
      <p>这是根据截图视觉结构生成的页面内容区域。</p>
    </section>
  </div>
);

export default DashboardPage;
"""


def _write_png(path: Path, *, size: tuple[int, int] = (1200, 720)) -> bytes:
    """生成测试专用 PNG 并返回原始字节。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (238, 242, 247)).save(path, format="PNG")
    return path.read_bytes()


def _requirement_input(path: Path, workspace: Path, raw: bytes) -> dict:
    """按真实上传清单格式构造截图需求输入。"""

    return {
        "mode": "screenshot",
        "screenshots": [
            {
                "relativePath": path.relative_to(workspace).as_posix(),
                "name": path.name,
                "mimeType": "image/png",
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        ],
    }


def _settings() -> SimpleNamespace:
    """构造不访问环境变量和网络的截图 UI 测试设置。"""

    return SimpleNamespace(
        screenshot_model_api_name="vision-test",
        screenshot_max_output_tokens=16000,
        screenshot_ui_max_images_per_page=4,
        screenshot_ui_max_retries=1,
        screenshot_ui_min_similarity=78,
        screenshot_ui_page_image_max_side=1600,
        ui_design_max_tokens=12000,
        ui_design_concurrency=2,
    )


def _analysis(sha256: str) -> ScreenshotUiAnalysis:
    """构造单页单截图的严格视觉映射结果。"""

    return ScreenshotUiAnalysis(
        global_style_summary="浅灰背景、白色内容卡片、紧凑企业控制台风格。",
        observations=[
            ScreenshotVisualObservation(
                screenshot_sha256=sha256,
                suggested_rotation=0,
                source_viewport_width=1200,
                source_viewport_height=720,
                page_content_bounds=ScreenshotBounds(
                    x=220,
                    y=0,
                    width=980,
                    height=720,
                ),
                page_content_region="顶部标题，下方为单列内容区。",
                app_shell="左侧导航和顶部工具栏属于外层应用壳。",
                layout="内容区使用 24px 内边距。",
                dominant_background="#eef2f7",
                color_palette=["#eef2f7", "#ffffff", "#1677ff"],
                typography=["标题约 24px", "正文约 14px"],
                components=["标题", "内容卡片"],
                layout_regions=[
                    ScreenshotLayoutRegion(
                        name="页面标题",
                        role="page-title",
                        bounds=ScreenshotBounds(x=244, y=24, width=320, height=40),
                        layout="左对齐单行标题。",
                        visual_style="透明背景。",
                        typography="24px、600。",
                        visible_text=["项目概览"],
                    ),
                    ScreenshotLayoutRegion(
                        name="内容卡片",
                        role="content-card",
                        bounds=ScreenshotBounds(x=244, y=88, width=932, height=300),
                        layout="单列内容卡片。",
                        visual_style="白色背景、8px 圆角。",
                        typography="14px 正文。",
                        visible_text=[],
                    ),
                ],
                spacing_and_shape="8px 圆角，24px 主间距。",
                visual_summary="浅色后台概览页。",
            )
        ],
        page_mappings=[
            ScreenshotPageMapping(
                page_id="dashboard_page",
                screenshot_sha256s=[sha256],
                primary_screenshot_sha256=sha256,
                confidence=0.98,
                rationale="截图标题和 ProductPlan 页面名称一致。",
            )
        ],
        unresolved_questions=[],
    )


def _decode_custom_frames(frames: list[str], name: str) -> list[dict]:
    """解析指定名称的 AG-UI CUSTOM 帧值。"""

    values: list[dict] = []
    for frame in frames:
        for line in frame.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[len("data:"):].strip())
            except json.JSONDecodeError:
                continue
            if event.get("type") != "CUSTOM" or event.get("name") != name:
                continue
            value = event.get("value")
            if isinstance(value, dict):
                values.append(value)
    return values


class ScreenshotUiStaticRepairTests(unittest.TestCase):
    """验证截图页面进入视觉审查前的确定性静态修复。"""

    def test_equivalent_product_actions_share_a_visible_control(self) -> None:
        """同一安装行为被规划成两个 action 时，复用真实按钮而不伪造第二个视觉操作。"""

        page = {
            "actions": [
                {
                    "actionId": "install_plugin",
                    "description": "用户点击添加按钮以安装某个插件。",
                    "behavior": {"type": "business", "expectedResult": "系统提示用户授权或配置插件，安装成功后插件出现在已安装列表中。"},
                },
                {
                    "actionId": "plugin_card_add",
                    "description": "用户点击某个插件项上的添加按钮以安装该插件。",
                    "behavior": {"type": "business", "expectedResult": "系统提示用户授权或配置插件，安装成功后该插件出现在已安装列表中。"},
                },
            ],
            "information_items": [],
        }
        code = '<div><button data-action-id="plugin_card_add" data-control-id="add-control">添加</button></div>'
        repaired, count = repair_equivalent_action_aliases(page, code)
        self.assertEqual(count, 1)
        self.assertEqual(validate_ui_design_code(page, repaired), [])
        self.assertIn('data-action-id="install_plugin"', repaired)
        self.assertEqual(repaired.count("<button"), 1)

        unrelated = {
            **page,
            "actions": [
                {**page["actions"][0], "behavior": {"type": "business", "expectedResult": "删除插件及其数据"}},
                page["actions"][1],
            ],
        }
        untouched, count = repair_equivalent_action_aliases(unrelated, code)
        self.assertEqual((untouched, count), (code, 0))

    def test_sidebar_observation_short_labels_match_page_names(self) -> None:
        """截图只写短菜单名时仍保留跨页导航，且用户页内侧栏可安全重组。"""

        observation = _analysis("a" * 64).observations[0].model_copy(
            update={
                "page_content_bounds": ScreenshotBounds(x=0, y=0, width=1280, height=720),
                "layout_regions": [
                    ScreenshotLayoutRegion(
                        name="Left Sidebar", role="Navigation", bounds=ScreenshotBounds(x=0, y=0, width=250, height=720),
                        layout="vertical", visual_style="light", typography="14px",
                        visible_text=["新聊天", "定时任务", "资料库", "插件"],
                    ),
                    ScreenshotLayoutRegion(
                        name="Logo", role="brand", bounds=ScreenshotBounds(x=16, y=12, width=100, height=30),
                        layout="row", visual_style="light", typography="18px",
                        visible_text=["ChatGPT"],
                    ),
                ],
            }
        )
        pages = [
            {"pageId": "tasks", "name": "定时任务页面", "actions": [], "information_items": []},
            {"pageId": "library", "name": "资料库页面", "actions": [], "information_items": []},
        ]
        shell = derive_shared_shell([observation], pages)
        self.assertEqual(shell.primary_labels[:3], ("新聊天", "定时任务", "资料库"))
        content = build_contract_scaffold(pages[0], "TaskPage")
        custom = content.replace("    <main", "    <div><aside><nav>定时任务 资料库</nav></aside><main", 1).replace("</main>", "</main></div>", 1)
        recomposed = recompose_page_with_shared_shell(custom, page=pages[0], pages=pages, shell=shell)
        self.assertIn("function ScreenshotSharedPage()", recomposed)
        self.assertEqual(recomposed.count("<aside"), 1)
        self.assertIn('>{"定时任务"}</span>', recomposed)

    def test_sidebar_alignment_is_atomic_for_confirmed_pages(self) -> None:
        """跨页一致指令直接重组已完成页，排队页保持原状态。"""

        observation = _analysis("a" * 64).observations[0].model_copy(
            update={
                "layout_regions": [
                    ScreenshotLayoutRegion(
                        name="Left Sidebar", role="Global navigation", bounds=ScreenshotBounds(x=0, y=0, width=220, height=720),
                        layout="vertical", visual_style="light", typography="14px",
                        visible_text=["聊天", "任务", "插件"],
                    )
                ]
            }
        )
        pages = [
            {"pageId": page_id, "name": name, "actions": [], "information_items": []}
            for page_id, name in (("chat", "聊天页面"), ("task", "任务页面"), ("plugin", "插件页面"))
        ]
        with tempfile.TemporaryDirectory() as workspace:
            project_dir = str(setup_ui_design_project(workspace)["project_dir"])
            specs = Path(workspace) / ".xcodeagent" / "specs"
            specs.mkdir(parents=True, exist_ok=True)
            (specs / "screenshot-ui-reference.json").write_text(
                json.dumps({"observations": [observation.model_dump()]}, ensure_ascii=False),
                encoding="utf-8",
            )
            entries = []
            for page, key in ((pages[0], "ChatPage"), (pages[1], "TaskPage")):
                content = build_contract_scaffold(page, key)
                custom = content.replace("    <main", "    <div><aside><nav>聊天 任务 插件</nav></aside><main", 1).replace("</main>", "</main></div>", 1)
                path = persist_page_code(project_dir, key, custom)
                entries.append(build_ui_page_manifest(page, page_key=key, code_path=path, code=custom, status="confirmed"))
            entries.append(build_ui_page_manifest(pages[2], page_key="PluginPage", status="queued"))
            state = {"workspace": workspace, "product_plan": {"pages": pages}, "requirement_input": {"mode": "screenshot"}}
            self.assertTrue(_is_screenshot_sidebar_alignment(state, "任务侧边栏和聊天侧边栏保持一致"))
            aligned = _recompose_screenshot_sidebars(state, entries, project_dir)
            self.assertEqual([entry["status"] for entry in aligned], ["confirmed", "confirmed", "queued"])
            self.assertTrue(all("function ScreenshotSharedPage()" in entry["code"] for entry in aligned[:2]))
            self.assertEqual(aligned[0]["code"].count("<aside"), 1)
            self.assertEqual(aligned[1]["code"].count("<aside"), 1)

    def test_sidebar_recomposition_transfers_unclassified_product_bindings(self) -> None:
        """任意业务侧栏统一时，原有操作与信息项必须转交共享壳而非丢弃。"""

        page = {
            "pageId": "inbox", "name": "收件箱", "actions": [
                {"actionId": "search_records", "name": "搜索", "behavior": {
                    "type": "interface", "expectedResult": "显示搜索框"}},
                {"actionId": "create_record", "name": "新建记录", "behavior": {"type": "business"}},
            ],
            "information_items": [
                {"itemId": "page_title", "label": "页面标题"},
                {"itemId": "user_profile", "label": "用户资料"},
            ],
        }
        pages = [page, {"pageId": "archive", "name": "归档", "actions": [], "information_items": []}]
        shell = SharedShell(True, 220, ("邮件",), ("收件箱", "归档"), (), "账户", False)
        code = '''
const Inbox = () => <div><aside><nav>收件箱 归档</nav>
  <button data-action-id="search_records" data-control-id="search-button"
    data-ui-effect="显示搜索框">搜索</button>
  <button data-action-id="create_record" data-control-id="create-button">新建</button>
  <span data-information-item-id="page_title" data-control-id="title">收件箱</span>
  <span data-information-item-id="user_profile" data-control-id="profile">访客</span>
</aside><main>内容</main></div>;
export default Inbox;
'''
        recomposed = recompose_page_with_shared_shell(code, page=page, pages=pages, shell=shell)
        self.assertEqual(recomposed.count("<aside"), 1)
        self.assertEqual(validate_ui_design_code(page, recomposed), [])
        self.assertIn('data-action-id="search_records"', recomposed)
        self.assertIn('data-information-item-id="user_profile"', recomposed)
        second = recompose_page_with_shared_shell(recomposed, page=page, pages=pages, shell=shell)
        self.assertEqual(second, recomposed)
        self.assertEqual(validate_ui_design_code(page, second), [])

    def test_retry_button_is_fixed_and_revalidated(self) -> None:
        """未归属的重试按钮应补 preview-only 后立即重新校验。"""

        code = """
const UsageOverview = () => (
  <Result extra={<Button onClick={() => reload()}>重试加载</Button>} />
);
export default UsageOverview;
"""
        error = (
            "UI 设计稿越过或遗漏了 ProductPlan 产品事实边界：\n"
            "- 以下交互控件没有绑定 ProductPlan actionId，也未标记 "
            'data-preview-only="true"：Button。'
        )
        with patch(
            "app.agents.screenshot_ui_design.agent.validate_page_code",
            side_effect=[(False, error), (True, "")],
        ) as validate:
            fixed, ok, validation_error = _validate_with_static_fixes(
                "C:/project", code, {"pageId": "usage_overview"}
            )

        self.assertTrue(ok)
        self.assertEqual(validation_error, "")
        self.assertIn('data-preview-only="true"', fixed)
        self.assertEqual(validate.call_count, 2)

    def test_contract_scaffold_covers_complex_usage_and_profile_pages(self) -> None:
        """复杂仪表盘和资料页骨架必须完整绑定全部信息项与操作。"""

        usage_page = {
            "pageId": "usage_info",
            "name": "用量信息",
            "description": "展示账户用量。",
            "information_items": [
                {"itemId": "usage_balance", "label": "充值余额"},
                {"itemId": "usage_tokens", "label": "Tokens 使用量"},
            ],
            "actions": [],
        }
        profile_page = {
            "pageId": "profile",
            "name": "个人信息",
            "description": "展示并编辑个人资料。",
            "information_items": [
                {"itemId": "profile_username", "label": "用户名"},
                {"itemId": "profile_phone", "label": "手机号码"},
            ],
            "actions": [
                {
                    "actionId": "profile_save",
                    "name": "保存修改",
                    "behavior": {"type": "business"},
                },
                {
                    "actionId": "profile_open_theme",
                    "name": "选择主题",
                    "behavior": {
                        "type": "interface",
                        "expectedResult": "打开主题选择器",
                    },
                },
            ],
        }

        usage_code = build_contract_scaffold(usage_page, "UsageInfo")
        profile_code = build_contract_scaffold(profile_page, "Profile")

        self.assertEqual(validate_ui_design_code(usage_page, usage_code), [])
        self.assertEqual(validate_ui_design_code(profile_page, profile_code), [])
        self.assertIn('data-information-item-id="usage_balance"', usage_code)
        self.assertIn('data-action-id="profile_save"', profile_code)
        self.assertIn('data-ui-effect="打开主题选择器"', profile_code)

    def test_shared_sidebar_is_identical_and_completes_each_page_contract(self) -> None:
        """多页共用一份侧边栏样式，导航绑定从内容稿中剥离后仍完整。"""

        observation = _analysis("a" * 64).observations[0]
        observation = observation.model_copy(
            update={
                "layout_regions": [
                    ScreenshotLayoutRegion(
                        name="Sidebar shell",
                        role="App shell navigation container",
                        bounds=ScreenshotBounds(x=0, y=0, width=224, height=720),
                        layout="固定侧边栏",
                        visual_style="浅灰",
                        typography="14px",
                        visible_text=["Example", "Console"],
                    ),
                    ScreenshotLayoutRegion(
                        name="Primary navigation",
                        role="Global sidebar menu",
                        bounds=ScreenshotBounds(x=12, y=80, width=200, height=100),
                        layout="竖排",
                        visual_style="浅灰选中态",
                        typography="14px",
                        visible_text=["Overview", "Reports"],
                    ),
                ]
            }
        )
        shell = derive_shared_shell([observation])
        for count in range(1, 11):
            with self.subTest(screenshots=count):
                self.assertEqual(
                    derive_shared_shell([observation] * count).width,
                    shell.width,
                )
        pages = [
            {
                "pageId": page_id,
                "name": name,
                "information_items": [
                    {"itemId": f"{page_id}_value", "label": "Value"},
                    {"itemId": f"{page_id}_sidebar_nav", "label": "左侧导航项"},
                ],
                "actions": [
                    {
                        "actionId": f"navigate_{target}",
                        "name": target_name,
                        "behavior": {"type": "navigation", "targetPageId": target},
                    }
                    for target, target_name in (("overview", "Overview"), ("reports", "Reports"))
                ],
            }
            for page_id, name in (("overview", "Overview"), ("reports", "Reports"))
        ]
        styles = []
        for page in pages:
            content_page, actions, items = split_page_contract(page, pages, shell)
            self.assertEqual(content_page["actions"], [])
            self.assertEqual(len(content_page["information_items"]), 1)
            code = compose_shared_shell(
                build_contract_scaffold(content_page, "ContentPage"),
                page=page,
                pages=pages,
                shell=shell,
                shell_actions=actions,
                shell_items=items,
            )
            self.assertEqual(validate_ui_design_code(page, code), [])
            self.assertEqual(code.count("export default"), 1)
            self.assertIn("width: 'clamp(176px, 18.67vw, 420px)'", code)
            styles.append(code.split("const screenshotShellStyles:", 1)[1].split("function ScreenshotSharedPage", 1)[0])
        self.assertEqual(styles[0], styles[1])
        self.assertIn(
            "重复生成了全局侧边栏",
            validate_content_without_shell(
                "<aside><nav>Overview Reports</nav></aside>", pages
            ),
        )

    def test_shared_shell_uses_sidebar_evidence_and_refreshes_earlier_page(self) -> None:
        """补齐截图后沿用同一品牌导航，旧页面内容不因侧栏统一而丢失。"""

        base = _analysis("a" * 64).observations[0]
        sidebar = ScreenshotLayoutRegion(
            name="Sidebar Navigation",
            role="Primary navigation container",
            bounds=ScreenshotBounds(x=0, y=0, width=220, height=720),
            layout="左侧竖排",
            visual_style="浅灰",
            typography="14px",
            visible_text=["deepseek", "开放平台", "Overview", "Reports", "帮助"],
        )
        profile_content = ScreenshotLayoutRegion(
            name="Real Name Verification",
            role="Profile identity display",
            bounds=ScreenshotBounds(x=280, y=120, width=400, height=100),
            layout="内容区",
            visual_style="白色",
            typography="14px",
            visible_text=["实名认证", "已认证", "查看详情"],
        )
        initial = base.model_copy(update={"layout_regions": [sidebar]})
        expanded = base.model_copy(update={"layout_regions": [sidebar, profile_content]})
        pages = [
            {"pageId": "overview", "name": "Overview", "actions": [], "information_items": []},
            {"pageId": "reports", "name": "Reports", "actions": [], "information_items": []},
        ]
        first_shell = derive_shared_shell([initial])
        full_shell = derive_shared_shell([initial, expanded], pages)
        self.assertEqual(full_shell.brand, ("deepseek", "开放平台"))
        self.assertEqual(full_shell.footer_labels, ("帮助",))
        page = pages[0]
        content = build_contract_scaffold(page, "Overview")
        old = compose_shared_shell(
            content,
            page=page,
            pages=pages,
            shell=first_shell,
            shell_actions=[],
            shell_items=[],
        )
        updated = refreshed_shared_shell_code(
            old, page=page, pages=pages, shell=full_shell
        )
        self.assertIn('>{"deepseek"}</span>', updated)
        self.assertIn("const Overview", updated)
        self.assertEqual(updated.count("<aside"), 1)
        self.assertEqual(
            remove_generated_sidebar(
                "<div><aside><nav>Overview Reports</nav></aside><main>Visible</main></div>",
                pages,
            ),
            "<div><main>Visible</main></div>",
        )
        self.assertEqual(
            remove_generated_sidebar(
                "<div><aside>local details</aside><main>Visible</main></div>",
                pages,
            ),
            "",
        )

    def test_existing_page_and_retry_result_adopt_the_same_sidebar(self) -> None:
        """当前已生成页面和单页重试结果均应恢复为可见的统一壳产物。"""

        observation = _analysis("a" * 64).observations[0].model_copy(
            update={
                "layout_regions": [
                    ScreenshotLayoutRegion(
                        name="Sidebar shell",
                        role="App shell navigation container",
                        bounds=ScreenshotBounds(x=0, y=0, width=220, height=720),
                        layout="竖排",
                        visual_style="浅灰",
                        typography="14px",
                        visible_text=["Example"],
                    ),
                    ScreenshotLayoutRegion(
                        name="Primary navigation",
                        role="Global sidebar menu",
                        bounds=ScreenshotBounds(x=0, y=80, width=220, height=120),
                        layout="竖排",
                        visual_style="浅灰",
                        typography="14px",
                        visible_text=["Overview", "Reports"],
                    ),
                ]
            }
        )
        shell = derive_shared_shell([observation])
        pages = [
            {
                "pageId": page_id,
                "name": name,
                "information_items": [{"itemId": f"{page_id}_value", "label": "Value"}],
                "actions": [
                    {
                        "actionId": "navigate_reports",
                        "name": "Reports",
                        "behavior": {"type": "navigation", "targetPageId": "reports"},
                    }
                ],
            }
            for page_id, name in (("overview", "Overview"), ("reports", "Reports"))
        ]
        reference = NormalizedReference((observation,), {}, "浅色", ())
        with tempfile.TemporaryDirectory() as temporary:
            project_dir = str(setup_ui_design_project(temporary)["project_dir"])
            page = pages[0]
            content_page, actions, items = split_page_contract(page, pages, shell)
            content = build_contract_scaffold(content_page, "Overview")
            previous = content.replace(
                "    <main",
                "    <div><aside><nav>Overview Reports</nav></aside><main",
                1,
            ).replace("</main>", "</main></div>", 1)
            persist_page_code(project_dir, "Overview", previous)
            adopted = _adopt_current_generated_page(
                page=page,
                pages=pages,
                shell=shell,
                page_key="Overview",
                project_dir=project_dir,
                existing={
                    "status": "confirmed",
                    "visual_source": "screenshot",
                    "screenshot_ui_pipeline_version": "screenshot-render-v2",
                },
                reference=reference,
                verification={},
            )
            self.assertEqual(adopted["status"], "confirmed")
            self.assertEqual(adopted["screenshot_ui_pipeline_version"], SCREENSHOT_UI_PIPELINE_VERSION)
            self.assertEqual(adopted["visual_verification"]["status"], "review_required")
            self.assertEqual(adopted["code"].count("<aside"), 1)

            persisted = compose_shared_shell(
                content,
                page=page,
                pages=pages,
                shell=shell,
                shell_actions=actions,
                shell_items=items,
            )
            persist_page_code(project_dir, "Overview", persisted)
            retried = _adopt_current_generated_page(
                page=page,
                pages=pages,
                shell=shell,
                page_key="Overview",
                project_dir=project_dir,
                existing={"status": "generating"},
                reference=reference,
                verification={"visualVerification": {"status": "passed"}},
            )
            self.assertEqual(retried["status"], "confirmed")
            self.assertEqual(retried["visual_verification"]["status"], "passed")
            self.assertEqual(retried["code"].count("<aside"), 1)

    def test_dynamic_row_controls_use_static_contract_ids(self) -> None:
        """动态列表的实际控件保留行行为，但契约标识必须可静态识别。"""

        page = {
            "pageId": "records",
            "actions": [{"actionId": "edit_record", "name": "编辑"}],
            "information_items": [{"itemId": "record_rows", "label": "记录"}],
        }
        code = """
const Records = () => <div>
  {rows.map(row => <div data-information-item-id="record_rows"
    data-control-id={`row_${row.id}`}>
    <a data-action-id="edit_record" data-control-id={`edit_${row.id}`}
      onClick={() => edit(row)}>编辑</a>
  </div>)}
  <a href="#">隐私政策</a>
</div>;
export default Records;
"""
        fixed, changed = repair_contract_attributes(page, code)
        self.assertGreaterEqual(changed, 3)
        self.assertIn('data-control-id="record_rows-control"', fixed)
        self.assertIn('data-control-id="edit_record-control"', fixed)
        self.assertIn('<a href="#" data-preview-only="true">隐私政策</a>', fixed)
        self.assertEqual(validate_ui_design_code(page, fixed), [])

    def test_contract_guard_does_not_guess_information_item_bindings(self) -> None:
        """越界按钮可降为预览控件，但不能按 DOM 顺序猜测业务指标。"""

        page = {
            "pageId": "usage_info",
            "information_items": [
                {"itemId": "usage_balance", "label": "充值余额"},
                {"itemId": "usage_tokens", "label": "Tokens 使用量"},
            ],
            "actions": [],
        }
        code = """
const UsageInfo = () => (
  <div>
    <Statistic title="充值余额" value={1286} />
    <Statistic title="Tokens 使用量" value={2460000} />
    <Button data-action-id="export_data" data-control-id="export_data-control">导出</Button>
  </div>
);
export default UsageInfo;
"""

        fixed, changed = repair_contract_attributes(page, code)

        self.assertGreaterEqual(changed, 1)
        self.assertNotIn('data-action-id="export_data"', fixed)
        self.assertIn('data-preview-only="true"', fixed)
        self.assertNotIn('data-information-item-id="usage_balance"', fixed)
        self.assertTrue(validate_ui_design_code(page, fixed))

    def test_unowned_buttons_are_preview_only_and_hidden_actions_are_rejected(self) -> None:
        """辅助按钮不能卡死整页；隐藏操作也不能冒充已实现功能。"""

        page = {
            "pageId": "usage_info",
            "actions": [{"actionId": "usage_filter", "name": "筛选"}],
            "information_items": [],
        }
        code = """
const UsageInfo = () => (
  <section>
    <Button>知道了</Button>
    <div style={{ display: 'none' }}>
      <button data-action-id="usage_filter" data-control-id="usage_filter-control">筛选</button>
    </div>
  </section>
);
export default UsageInfo;
"""
        fixed, _ = repair_contract_attributes(page, code)

        self.assertIn('<Button data-preview-only="true">', fixed)
        self.assertTrue(validate_visible_bindings(fixed))

    def test_explicit_item_control_id_restores_only_matching_item_binding(self) -> None:
        """控件明确写出完整 itemId 时可补标记，模糊标签和 DOM 顺序不可猜测。"""

        page = {
            "pageId": "arbitrary_page",
            "information_items": [
                {"itemId": "period_choice", "label": "时间范围"},
                {"itemId": "account_choice", "label": "账户"},
            ],
            "actions": [{"actionId": "apply_filter", "name": "筛选"}],
        }
        code = (
            '<Select data-action-id="apply_filter" data-control-id="period_choice" />'
            '<Select data-control-id="account_choice-control" />'
            '<Statistic title="其他数值" value={5} />'
        )
        fixed, changed = repair_contract_attributes(page, code)

        self.assertEqual(changed, 2)
        self.assertIn('data-information-item-id="period_choice"', fixed)
        self.assertIn('data-information-item-id="account_choice"', fixed)
        self.assertNotIn('data-information-item-id="other"', fixed)
        self.assertIn("Statistic title=", fixed)

    def test_hidden_binding_guard_covers_nested_and_direct_hidden_forms(self) -> None:
        """通用可见性校验覆盖任意页面的隐藏祖先、标签和 CSS 类。"""

        samples = [
            '<article hidden><section><Button data-action-id="save" /></section></article>',
            '<main aria-hidden="true"><span data-information-item-id="balance" /></main>',
            '<div className="panel hidden"><a data-action-id="open" /></div>',
            '<Button style={{ visibility: "hidden" }} data-action-id="save" />',
        ]
        for code in samples:
            with self.subTest(code=code):
                self.assertTrue(validate_visible_bindings(code))
        self.assertEqual(
            validate_visible_bindings(
                '<section className="hidden md:block"><Button data-action-id="save" /></section>'
            ),
            [],
        )


class _ScreenshotProgressGraph:
    """模拟截图准备节点先发自定义进度，再完成节点。"""

    async def astream(self, initial_state, *, config, stream_mode):
        """按 LangGraph 多流模式产出截图准备进度与最终更新。"""

        yield "custom", {
            "type": "screenshot_ui_preparation.progress",
            "node_name": "screenshot_ui_preparation",
            "message": "正在根据截图准备 UI 设计稿。",
            "detail": {"total": 2},
        }
        yield "updates", {
            "screenshot_ui_preparation": {
                "phase": "screenshot_ui_preparation",
                "status": "completed",
                "requirement_input": {"mode": "screenshot"},
            }
        }

    def get_state(self, config):
        """返回截图准备完成后的最小 checkpoint 快照。"""

        return SimpleNamespace(
            values={
                "phase": "screenshot_ui_preparation",
                "status": "completed",
                "requirement_input": {"mode": "screenshot"},
            }
        )


class ScreenshotUiImageTests(unittest.TestCase):
    """验证 UI 保色预处理、输入安全和多模态传输格式。"""

    def test_ui_preprocess_preserves_lossless_color_and_limits_page_images(self) -> None:
        """UI 预处理应输出 PNG 且不执行需求识别阶段的增强与 JPEG 压缩。"""

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            path = workspace / ".xcodeagent/inputs/screenshots/batch/screen.png"
            raw = _write_png(path, size=(640, 2200))
            parsed = RequirementInput.model_validate(
                _requirement_input(path, workspace, raw)
            )
            prepared = load_ui_reference_images(
                str(workspace),
                parsed,
                max_tiles=4,
            )
            self.assertEqual(len(prepared), 1)
            self.assertTrue(prepared[0].overview.content.startswith(b"\x89PNG"))
            operations = ",".join(prepared[0].overview.operations)
            self.assertNotIn("contrast", operations)
            self.assertNotIn("unsharp", operations)
            selected = page_images(
                prepared,
                [parsed.screenshots[0].sha256],
                limit=3,
            )
            self.assertLessEqual(len(selected), 3)
            blocks = _content_blocks("test", selected)
            urls = [
                block["image_url"]["url"]
                for block in blocks
                if block.get("type") == "image_url"
            ]
            self.assertTrue(urls[0].startswith("data:image/png;base64,"))
            base64.b64decode(urls[0].split(",", 1)[1], validate=True)

    def test_ui_preprocess_rejects_path_outside_screenshot_root(self) -> None:
        """截图清单不能利用相对路径读取专用输入目录之外的文件。"""

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            outside = workspace / "outside.png"
            raw = _write_png(outside)
            value = _requirement_input(outside, workspace, raw)
            parsed = RequirementInput.model_validate(value)
            with self.assertRaisesRegex(ValueError, "超出允许目录"):
                load_ui_reference_images(str(workspace), parsed)


class ScreenshotUiWorkflowTests(unittest.TestCase):
    """验证截图 UI 准备产物、幂等性和原工作流分流。"""

    def test_reference_mapping_prioritizes_primary_screenshot(self) -> None:
        """单页图片配额受限时应先发送模型明确选出的主参考截图。"""

        first = "a" * 64
        primary = "b" * 64
        analysis = ScreenshotUiAnalysis(
            global_style_summary="test",
            observations=[],
            page_mappings=[
                ScreenshotPageMapping(
                    page_id="dashboard_page",
                    screenshot_sha256s=[first, primary],
                    primary_screenshot_sha256=primary,
                    confidence=0.9,
                    rationale="第二张是页面的默认状态。",
                )
            ],
            unresolved_questions=[],
        )
        normalized = _normalize_reference(
            analysis,
            [{"pageId": "dashboard_page"}],
            [
                SimpleNamespace(reference=SimpleNamespace(sha256=first)),
                SimpleNamespace(reference=SimpleNamespace(sha256=primary)),
            ],
        )
        self.assertEqual(
            normalized.mappings["dashboard_page"].screenshot_sha256s,
            [primary, first],
        )

    def test_mapping_prompt_does_not_force_unrelated_pages_to_share_screenshots(self) -> None:
        """截图缺少对应页面时必须允许留空，避免把其他应用页面风格错迁移。"""

        prompt = build_mapping_prompt(
            [{"pageId": "orders"}, {"pageId": "customers"}],
            [{"sha256": "a" * 64, "name": "orders.png"}],
        )
        self.assertIn("leave unsupported pages unmapped", prompt)

    def test_visual_json_retries_incomplete_root_instead_of_reading_nested_item(self) -> None:
        """多图输出截断后不能把其中一张观察误当整份分析结果。"""

        complete = json.dumps(
            {
                "global_style_summary": "浅色企业后台。",
                "page_id": "",
                "confidence": 0,
                "rationale": "主内容不明确。",
                "unresolved_questions": [],
            },
            ensure_ascii=False,
        )
        with patch(
            "app.agents.screenshot_ui_design.transport._post_chat",
            side_effect=['{"global_style_summary":"浅色","page_id":', complete],
        ) as model:
            result = invoke_vision_json(
                ScreenshotSingleImagePageDecision,
                settings=_settings(),
                system_prompt="test",
                user_prompt="test",
                images=[],
                max_tokens=1000,
                response_name="test_map",
            )
        self.assertEqual(model.call_count, 2)
        self.assertEqual(result.global_style_summary, "浅色企业后台。")
        self.assertIn("previous response failed validation", model.call_args.kwargs["user_prompt"])

    def test_one_failed_observation_does_not_invalidate_other_page_mappings(self) -> None:
        """映射已明确时，单图视觉细节失败只影响该图的观察证据。"""

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            first_path = workspace / ".xcodeagent/inputs/screenshots/run/first.png"
            second_path = workspace / ".xcodeagent/inputs/screenshots/run/second.png"
            first_raw = _write_png(first_path)
            second_raw = _write_png(second_path, size=(1100, 700))
            references = [
                _requirement_input(path, workspace, raw)["screenshots"][0]
                for path, raw in ((first_path, first_raw), (second_path, second_raw))
            ]
            screenshots = load_ui_reference_images(
                str(workspace),
                RequirementInput.model_validate(
                    {"mode": "screenshot", "screenshots": references}
                ),
            )
            first_hash, second_hash = [item.reference.sha256 for item in screenshots]
            mappings = [
                ScreenshotSingleImagePageDecision(
                    global_style_summary="浅色后台。",
                    page_id=page_id,
                    confidence=0.95,
                    rationale="页面标题一致。",
                    unresolved_questions=[],
                )
                for page_id, source in (("first", first_hash), ("second", second_hash))
            ]
            with patch(
                "app.agents.screenshot_ui_design.agent.invoke_vision_json",
                side_effect=[
                    *mappings,
                    _analysis(first_hash).observations[0],
                    RuntimeError("图像解析失败"),
                ],
            ) as model:
                result = _analyze_references(
                    [{"pageId": "first"}, {"pageId": "second"}],
                    screenshots,
                    _settings(),
                )
            self.assertEqual(model.call_count, 4)
            self.assertEqual(len(result.page_mappings), 2)
            self.assertEqual(len(result.observations), 1)
            self.assertIn("second.png", result.unresolved_questions[0])

    def test_partial_reference_retries_only_unmapped_screenshot(self) -> None:
        """已有成功映射不重复调用模型，失败图片可在下一轮独立恢复。"""

        first, second = "a" * 64, "b" * 64
        screenshots = [
            SimpleNamespace(
                reference=SimpleNamespace(sha256=source, name=f"{index}.png"),
                mapping_overview=SimpleNamespace(
                    source_sha256=source, width=1200, height=720
                ),
            )
            for index, source in enumerate((first, second), start=1)
        ]
        existing = NormalizedReference(
            observations=(_analysis(first).observations[0],),
            mappings={
                "first": ScreenshotPageMapping(
                    page_id="first",
                    screenshot_sha256s=[first],
                    primary_screenshot_sha256=first,
                    confidence=0.95,
                    rationale="先前已验证。",
                )
            },
            global_style_summary="浅色界面",
            unresolved_questions=("第二张先前失败",),
        )
        decision = ScreenshotSingleImagePageDecision(
            page_id="second",
            confidence=0.9,
            rationale="主内容匹配。",
            global_style_summary="浅色界面",
            unresolved_questions=[],
        )
        with patch(
            "app.agents.screenshot_ui_design.agent.invoke_vision_json",
            side_effect=[decision, _analysis(second).observations[0]],
        ) as model:
            result = _analyze_references(
                [{"pageId": "first"}, {"pageId": "second"}],
                screenshots,
                _settings(),
                existing=existing,
            )
        self.assertEqual(model.call_count, 2)
        self.assertEqual({item.page_id for item in result.page_mappings}, {"first", "second"})
        self.assertEqual(len(result.observations), 2)
        self.assertEqual(result.unresolved_questions, [])

    def test_ten_screenshots_can_map_to_fewer_pages_without_order_assumptions(self) -> None:
        """十张以内任意截图可多图同页，不要求截图数等于页面数。"""

        screenshots = [
            SimpleNamespace(
                reference=SimpleNamespace(sha256=f"{index:064x}", name=f"shot-{index}.png"),
                mapping_overview=SimpleNamespace(
                    source_sha256=f"{index:064x}", width=1200, height=720
                ),
            )
            for index in range(1, 11)
        ]

        def model_answer(model_type, **kwargs):
            """按图片摘要给出页面归属和对应的单图视觉观察。"""

            source = kwargs["images"][0].source_sha256
            if model_type is ScreenshotSingleImagePageDecision:
                page_id = "first" if int(source, 16) % 2 else "second"
                return ScreenshotSingleImagePageDecision(
                    global_style_summary="统一的浅色设计。",
                    page_id=page_id,
                    confidence=0.95,
                    rationale="主内容标题吻合。",
                    unresolved_questions=[],
                )
            return _analysis(source).observations[0]

        with patch(
            "app.agents.screenshot_ui_design.agent.invoke_vision_json",
            side_effect=model_answer,
        ) as model:
            result = _analyze_references(
                [{"pageId": "first"}, {"pageId": "second"}, {"pageId": "unpictured"}],
                screenshots,
                _settings(),
            )
        normalized = _normalize_reference(
            result,
            [{"pageId": "first"}, {"pageId": "second"}, {"pageId": "unpictured"}],
            screenshots,
        )
        self.assertEqual(model.call_count, 20)
        self.assertEqual(len(normalized.observations), 10)
        self.assertEqual(len(normalized.mappings["first"].screenshot_sha256s), 5)
        self.assertEqual(len(normalized.mappings["second"].screenshot_sha256s), 5)
        self.assertNotIn("unpictured", normalized.mappings)

    def test_one_to_ten_screenshots_are_all_analyzed_without_fixed_page_count(self) -> None:
        """每一种允许的图片数量都逐图处理，页数可少于截图数。"""

        for count in range(1, 11):
            with self.subTest(screenshots=count):
                screenshots = [
                    SimpleNamespace(
                        reference=SimpleNamespace(
                            sha256=f"{index:064x}", name=f"shot-{index}.png"
                        ),
                        mapping_overview=SimpleNamespace(
                            source_sha256=f"{index:064x}", width=1200, height=720
                        ),
                    )
                    for index in range(1, count + 1)
                ]

                def model_answer(model_type, **kwargs):
                    """模拟同页多状态截图及独立页面的任意混合。"""

                    source = kwargs["images"][0].source_sha256
                    if model_type is ScreenshotSingleImagePageDecision:
                        page_id = "primary" if int(source, 16) % 2 else "secondary"
                        return ScreenshotSingleImagePageDecision(
                            global_style_summary="浅色产品界面。",
                            page_id=page_id,
                            confidence=0.9,
                            rationale="主内容匹配。",
                            unresolved_questions=[],
                        )
                    return _analysis(source).observations[0]

                with patch(
                    "app.agents.screenshot_ui_design.agent.invoke_vision_json",
                    side_effect=model_answer,
                ) as model:
                    result = _analyze_references(
                        [{"pageId": "primary"}, {"pageId": "secondary"}],
                        screenshots,
                        _settings(),
                    )
                mapped_sources = {
                    source
                    for mapping in result.page_mappings
                    for source in mapping.screenshot_sha256s
                }
                self.assertEqual(model.call_count, count * 2)
                self.assertEqual(len(result.observations), count)
                self.assertEqual(len(mapped_sources), count)

    def test_visual_prompt_prioritizes_primary_image_without_duplicate_geometry(self) -> None:
        """多图同页时主图优先且不重复整份观察与几何事实。"""

        first, second = "a" * 64, "b" * 64
        mapping = ScreenshotPageMapping(
            page_id="page",
            screenshot_sha256s=[first, second],
            primary_screenshot_sha256=second,
            confidence=0.9,
            rationale="同页两个状态。",
        )
        prompt = json.loads(
            _visual_reference_text(
                mapping,
                [_analysis(first).observations[0], _analysis(second).observations[0]],
                "浅色界面",
            )
        )
        self.assertEqual(len(prompt["observations"]), 2)
        self.assertEqual(prompt["observations"][0]["screenshot_sha256"], second)
        self.assertTrue(prompt["observations"][0]["primary"])
        self.assertNotIn("geometry_contracts", prompt)
        self.assertIn("contentLocalPixels", prompt["observations"][0]["regions"][0])

    def test_generation_prompt_uses_geometry_as_style_guidance(self) -> None:
        """截图提示词应保留布局证据，但明确允许美观、响应式的合理差异。"""

        sha256 = "a" * 64
        analysis = _analysis(sha256)
        prompt = build_screenshot_render_prompt(
            {
                "pageId": "dashboard_page",
                "name": "项目概览",
                "path": "/page/dashboard",
                "description": "查看项目概览。",
                "actions": [],
                "information_items": [],
            },
            "DashboardPage",
            analysis.page_mappings[0],
            analysis.observations,
            analysis.global_style_summary,
        )

        self.assertIn('"contentLocalPixels"', prompt)
        self.assertIn('"x": 24', prompt)
        self.assertIn("Do not add a preview-state switcher", prompt)
        self.assertIn("not exact pixel coordinates", prompt)
        self.assertIn("font hierarchy", prompt)
        self.assertIn("palette", prompt)
        self.assertIn("does NOT load Tailwind", prompt)
        self.assertIn('"allowedActionIdsExactly": []', prompt)
        self.assertIn('"allowedInformationItemIdsExactly": []', prompt)
        self.assertNotIn("VERIFIED PRODUCT CONTRACT SCAFFOLD", prompt)

    def test_screenshot_runtime_rejects_unbundled_utility_css(self) -> None:
        """截图设计稿不能仅靠预览 iframe 不提供的原子 CSS 呈现布局。"""

        self.assertIn(
            "Tailwind",
            validate_screenshot_runtime_styles(
                '<main className="flex min-h-screen bg-white px-4 text-[#111827]">x</main>'
            ),
        )
        self.assertEqual(
            validate_screenshot_runtime_styles(
                '<main style={{display: "flex", padding: 16}}>x</main>'
            ),
            "",
        )
        self.assertEqual(
            validate_screenshot_runtime_styles(
                '<><style>{`.page { display: flex; }`}</style>'
                '<main className="page">x</main></>'
            ),
            "",
        )
        with patch(
            "app.agents.screenshot_ui_design.agent.validate_page_code",
            return_value=(True, ""),
        ):
            _, ok, error = _validate_with_static_fixes(
                "unused-project",
                '<main className="flex min-h-screen bg-white px-4">x</main>',
                {"actions": [], "information_items": []},
            )
        self.assertFalse(ok)
        self.assertIn("Tailwind", error)

    def test_truncated_repair_cannot_replace_complete_content_candidate(self) -> None:
        """语法完整但少数绑定待补的候选可复用，半页修复输出不能覆盖它。"""

        with tempfile.TemporaryDirectory() as tmp:
            complete = build_contract_scaffold(
                {"pageId": "bills", "name": "账单", "information_items": [], "actions": []},
                "Bills",
            )
            self.assertTrue(_usable_content_candidate(tmp, complete))
            self.assertFalse(
                _usable_content_candidate(
                    tmp,
                    "import React from 'react';\nexport default function Bills() {",
                )
            )

    def test_complete_candidate_uses_validated_local_contract_patch(self) -> None:
        """信息项漏标时局部修复通过完整校验，并保留原页面的排版。"""

        with tempfile.TemporaryDirectory() as tmp:
            source = "b" * 64
            page = {
                "pageId": "dashboard_page",
                "name": "项目概览",
                "actions": [],
                "information_items": [{"itemId": "summary", "label": "概览"}],
            }
            analysis = _analysis(source)
            reference = NormalizedReference(
                observations=tuple(analysis.observations),
                mappings={"dashboard_page": analysis.page_mappings[0]},
                global_style_summary=analysis.global_style_summary,
                unresolved_questions=(),
            )
            screenshot = SimpleNamespace(
                reference=SimpleNamespace(sha256=source),
                overview=SimpleNamespace(source_sha256=source),
                details=(),
            )
            patch_result = TsxRepairPatch.model_validate({"edits": [{
                "old": "<p>这是根据截图视觉结构生成的页面内容区域。</p>",
                "new": '<p data-information-item-id="summary" data-control-id="summary-display">这是根据截图视觉结构生成的页面内容区域。</p>',
            }]})
            with patch(
                "app.agents.screenshot_ui_design.agent.invoke_vision_json",
                return_value=patch_result,
            ), patch(
                "app.agents.screenshot_ui_design.agent.invoke_vision_text",
                side_effect=AssertionError("局部修复通过时不应重生成整页"),
            ):
                entry, _ = _generate_page_entry(
                    page=page,
                    pages=[page],
                    shell=derive_shared_shell(list(reference.observations), [page]),
                    page_key="DashboardPage",
                    project_dir=tmp,
                    screenshots=[screenshot],
                    reference=reference,
                    settings=_settings(),
                    initial_code=VALID_TSX,
                    run_visual_audit=False,
                )
            self.assertEqual(entry["status"], "confirmed")
            saved = Path(entry["code_path"]).read_text(encoding="utf-8")
            self.assertIn('data-information-item-id="summary"', saved)
            self.assertIn("fontFamily: 'Inter, PingFang SC", saved)

    def test_validated_candidate_survives_truncated_model_repair(self) -> None:
        """模型修复截断时重新复核已补齐的候选，不能把完整页面判为失败。"""

        with tempfile.TemporaryDirectory() as tmp:
            source = "a" * 64
            page = {"pageId": "dashboard_page", "name": "项目概览", "actions": [], "information_items": []}
            analysis = _analysis(source)
            reference = NormalizedReference(
                observations=tuple(analysis.observations),
                mappings={"dashboard_page": analysis.page_mappings[0]},
                global_style_summary=analysis.global_style_summary,
                unresolved_questions=(),
            )
            screenshot = SimpleNamespace(
                reference=SimpleNamespace(sha256=source),
                overview=SimpleNamespace(source_sha256=source),
                details=(),
            )
            original_validate = _validate_screenshot_content
            calls = 0

            def delayed_validation(*args):
                """模拟首次校验未接纳静态修复、复核时接受同一完整候选。"""

                nonlocal calls
                calls += 1
                if calls == 1:
                    return args[1], False, "首次校验结果待复核"
                return original_validate(*args)

            with patch(
                "app.agents.screenshot_ui_design.agent._validate_screenshot_content",
                side_effect=delayed_validation,
            ), patch(
                "app.agents.screenshot_ui_design.agent.invoke_vision_text",
                return_value="export default function Broken() {",
            ):
                entry, _ = _generate_page_entry(
                    page=page,
                    pages=[page],
                    shell=derive_shared_shell(list(reference.observations), [page]),
                    page_key="DashboardPage",
                    project_dir=tmp,
                    screenshots=[screenshot],
                    reference=reference,
                    settings=_settings(),
                    initial_code=VALID_TSX,
                    run_visual_audit=False,
                )
            self.assertEqual(entry["status"], "confirmed")
            self.assertEqual(entry["visual_verification"]["status"], "review_required")

    def test_sidebar_divider_uses_image_pixels_instead_of_inaccurate_model_width(self) -> None:
        """背景分界跨越多数扫描线时按原图比例确定侧栏宽度。"""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".xcodeagent/inputs/screenshots/run/shell.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            screenshot = Image.new("RGB", (1200, 700), (255, 255, 255))
            screenshot.paste((249, 250, 251), (0, 0, 240, 700))
            screenshot.save(path)
            raw = path.read_bytes()
            prepared = load_ui_reference_images(
                tmp,
                RequirementInput.model_validate(_requirement_input(path, Path(tmp), raw)),
            )[0]
            ratio = detect_sidebar_width_ratio(prepared.overview, hint_x=190)
            self.assertIsNotNone(ratio)
            self.assertAlmostEqual(ratio or 0, 0.20, delta=0.01)
            shell = derive_shared_shell(
                [_analysis(prepared.reference.sha256).observations[0].model_copy(
                    update={"layout_regions": [ScreenshotLayoutRegion(
                        name="Sidebar Navigation",
                        role="navigation container",
                        bounds=ScreenshotBounds(x=0, y=0, width=190, height=700),
                        layout="左侧导航。",
                        visual_style="浅色背景。",
                        typography="14px。",
                        visible_text=["应用", "项目概览"],
                    )]}
                )],
                [{"pageId": "dashboard_page", "name": "项目概览"}],
                [prepared],
            )
            self.assertAlmostEqual(shell.width_ratio, 0.20, delta=0.01)

    def test_literal_collection_rows_receive_real_static_bindings(self) -> None:
        """账单等列表可继续使用 map，但实际行分支必须含静态且可见的 itemId。"""

        page = {
            "pageId": "records",
            "actions": [],
            "information_items": [
                {"itemId": "record-a", "label": "记录 A"},
                {"itemId": "record-b", "label": "记录 B"},
            ],
        }
        code = """
const rows = [{id: 'record-a', name: 'A'}, {id: 'record-b', name: 'B'}];
const Records = () => <table><tbody>{rows.map((record) => (
  <tr key={record.id} data-information-item-id={record.id} data-control-id={`row-${record.id}`}>
    <td>{record.name}</td>
  </tr>
))}</tbody></table>;
export default Records;
"""
        fixed, count = repair_literal_collection_bindings(page, code)
        self.assertEqual(count, 2)
        self.assertNotIn("data-information-item-id={record.id}", fixed)
        self.assertEqual(validate_ui_design_code(page, fixed), [])
        self.assertIn('data-information-item-id="record-a"', fixed)
        self.assertIn('data-information-item-id="record-b"', fixed)

    def test_named_card_map_uses_unambiguous_visible_row_names(self) -> None:
        """任意名称的卡片数组只在逐行唯一对应产品项时静态补齐。"""

        page = {
            "information_items": [
                {"itemId": "invoice-april", "label": "账单：四月费用"},
                {"itemId": "invoice-may", "label": "账单：五月费用"},
            ]
        }
        code = """
const bills: Bill[] = [{id: 'apr', name: '四月费用'}, {id: 'may', name: '五月费用'}];
const Bills = () => <main>{bills.map((bill) => (
  <div data-information-item-id={`invoice-${bill.id}`} data-control-id={`bill-${bill.id}`}>
    {bill.name}
  </div>
))}</main>;
"""
        fixed, count = repair_mapped_card_bindings(page, code)
        self.assertEqual(count, 1)
        self.assertIn('"apr": {\'data-information-item-id\': "invoice-april"', fixed)
        self.assertIn("ScreenshotItemBindings1[bill.id]", fixed)
        self.assertEqual(validate_ui_design_code({**page, "actions": []}, fixed), [])
        self.assertEqual(repair_mapped_card_bindings(page, fixed), (fixed, 0))

        ambiguous = {
            "information_items": [
                {"itemId": "invoice-a", "label": "账单：同名"},
                {"itemId": "invoice-b", "label": "账单：同名"},
            ]
        }
        untouched, count = repair_mapped_card_bindings(ambiguous, code.replace("四月费用", "同名"))
        self.assertEqual((untouched, count), (code.replace("四月费用", "同名"), 0))

    def test_finite_inline_lists_bind_real_cards_and_actions(self) -> None:
        """内联列表中的条件 action 与部分卡片信息保持原布局并转成静态可见分支。"""

        page = {
            "pageId": "dashboard", "name": "总览", "information_items": [
                {"itemId": "dashboard-card-1", "label": "报告：第一份"},
                {"itemId": "dashboard-card-2", "label": "报告：第二份"},
            ],
            "actions": [
                {"actionId": "open_list", "name": "列表", "behavior": {"type": "navigation"}},
                {"actionId": "open_filter", "name": "筛选", "behavior": {
                    "type": "interface", "expectedResult": "显示筛选条件"}},
            ],
        }
        code = '''
const Dashboard = () => <main>
  <nav>{[{id:'list',label:'列表'},{id:'filter',label:'筛选'}].map(item =>
    <a data-action-id={item.id === 'list' ? 'open_list' : 'open_filter'}
       data-control-id={`nav-${item.id}`} href={`/${item.id}`}>{item.label}</a>)}</nav>
  <section>{[{id:'card-1',name:'第一份'},{id:'card-2',name:'第二份'},
    {id:'other',name:'其他'}].map(card =>
    <div data-information-item-id={`dashboard-${card.id}`} data-control-id={`card-${card.id}`}>
      <div>{card.name}</div>
    </div>)}</section>
</main>;
export default Dashboard;
'''
        fixed, count = repair_finite_collection_bindings(page, code)
        self.assertEqual(count, 2)
        self.assertIn('data-information-item-id="dashboard-card-1"', fixed)
        self.assertIn('data-action-id="open_filter"', fixed)
        self.assertIn('data-preview-only="true"', fixed)
        self.assertEqual(repair_finite_collection_bindings(page, fixed), (fixed, 0))
        with tempfile.TemporaryDirectory() as workspace:
            project_dir = str(setup_ui_design_project(workspace)["project_dir"])
            _, valid, error = _validate_with_static_fixes(project_dir, code, page)
        self.assertTrue(valid, error)

    def test_finite_named_lists_handle_filter_slice_and_same_callback_name(self) -> None:
        """过滤别名、数字 ID、同名回调分属不同数组时不能串线或依赖固定页面。"""

        page = {
            "information_items": [
                {"itemId": "files-card-1", "label": "文件：甲.txt"},
                {"itemId": "files-card-2", "label": "文件：乙.md"},
                {"itemId": "tools-gmail-card", "label": "Gmail 插件卡片"},
            ],
            "actions": [],
        }
        code = '''
const files = [{id:1,name:'甲.txt'},{id:2,name:'乙.md'},{id:3,name:'其他'}];
const tools = [{id:'gmail',name:'Gmail'},{id:'other',name:'其他'}];
const visibleTools = tools.filter(tool => tool.name.length > 0);
const Page = () => <main>
  {files.slice(0, 2).map((entry, index) => <div data-information-item-id={index === 0 ? 'files-card-1' : 'files-card-2'}><div>{entry.name}</div></div>)}
  {visibleTools.map(entry => <div data-information-item-id={`tools-${entry.id}`} data-control-id={`tool-${entry.id}`}>{entry.name}</div>)}
</main>;
export default Page;
'''
        fixed, count = repair_finite_collection_bindings(page, code)
        self.assertEqual(count, 2)
        self.assertIn('data-information-item-id="files-card-2"', fixed)
        self.assertIn('data-information-item-id="tools-gmail-card"', fixed)
        self.assertEqual(validate_ui_design_code(page, fixed), [])
        with tempfile.TemporaryDirectory() as workspace:
            project_dir = str(setup_ui_design_project(workspace)["project_dir"])
            _, valid, error = _validate_with_static_fixes(project_dir, code, page)
        self.assertTrue(valid, error)

    def test_finite_action_uses_explicit_conditional_ids_before_label_guessing(self) -> None:
        """控件文案与操作名称不同时只依据源码已有的有限条件映射绑定。"""

        page = {"information_items": [], "actions": [
            {"actionId": "go_alpha", "name": "查看第一页", "behavior": {"type": "navigation"}},
            {"actionId": "go_beta", "name": "查看第二页", "behavior": {"type": "navigation"}},
        ]}
        code = '''
const Page = () => <nav>{[{id:'alpha',label:'A'},{id:'beta',label:'B'}].map(link =>
  <a data-action-id={link.id === 'alpha' ? 'go_alpha' : 'go_beta'}
     data-control-id={`link-${link.id}`} href={`/${link.id}`}>{link.label}</a>
)}</nav>;
export default Page;
'''
        fixed, count = repair_finite_collection_bindings(page, code)
        self.assertEqual(count, 1)
        self.assertIn('data-action-id="go_alpha"', fixed)
        self.assertIn('data-action-id="go_beta"', fixed)
        self.assertEqual(validate_ui_design_code(page, fixed), [])

    def test_finite_collection_refuses_ambiguous_or_runtime_data(self) -> None:
        """信息归属不唯一或数据来自接口时不凭位置猜测产品事实。"""

        page = {"information_items": [
            {"itemId": "a-card", "label": "相同"},
            {"itemId": "b-card", "label": "相同"},
        ], "actions": []}
        ambiguous = "const rows=[{id:'x',name:'相同'}]; const Page=()=> <main>{rows.map(row => <div data-information-item-id={row.id}>{row.name}</div>)}</main>;"
        self.assertEqual(repair_finite_collection_bindings(page, ambiguous), (ambiguous, 0))
        runtime = "const Page=({rows}:any)=> <main>{rows.map((row:any) => <div data-information-item-id={row.id}>{row.name}</div>)}</main>;"
        self.assertEqual(repair_finite_collection_bindings(page, runtime), (runtime, 0))

    def test_exact_visible_action_can_also_express_information_item(self) -> None:
        """信息项与唯一可见操作完全同名时补同一控件标记，不新增假按钮。"""

        page = {"information_items": [{"itemId": "new-session-label", "label": "新会话"}],
                "actions": [{"actionId": "create_session", "name": "新会话"}]}
        code = '<button data-action-id="create_session" data-control-id="create-button">新会话</button>'
        fixed, count = repair_equivalent_visible_information(page, code)
        self.assertEqual(count, 1)
        self.assertIn('data-information-item-id="new-session-label"', fixed)
        self.assertEqual(repair_equivalent_visible_information(page, fixed), (fixed, 0))
        ambiguous = {**page, "information_items": [
            {"itemId": "label-a", "label": "新会话"},
            {"itemId": "label-b", "label": "新会话"},
        ]}
        self.assertEqual(repair_equivalent_visible_information(ambiguous, code), (code, 0))

    def test_inline_action_map_keeps_runtime_effect_and_static_contract(self) -> None:
        """内联循环中的界面行为效果与真实 actionId 对应，而非伪造隐藏控件。"""

        page = {
            "actions": [{
                "actionId": "show_filters", "name": "筛选",
                "behavior": {"type": "interface", "expectedResult": "显示筛选选项"},
            }],
            "information_items": [],
        }
        code = """
const Panel = () => <nav>{[{label: '筛选', actionId: 'show_filters'}].map((item) => (
  <button data-action-id={item.actionId} data-control-id={`nav-${item.label}`}
    data-ui-effect={item.label} onClick={() => undefined}>{item.label}</button>
))}</nav>;
"""
        fixed, count = repair_mapped_interface_effects(page, code)
        self.assertEqual(count, 1)
        self.assertIn("'data-ui-effect': \"显示筛选选项\"", fixed)
        self.assertIn("ScreenshotActionEffects1[item.actionId]", fixed)
        self.assertEqual(validate_ui_design_code(page, fixed), [])
        self.assertEqual(repair_mapped_interface_effects(page, fixed), (fixed, 0))

    def test_functional_sidebar_is_preserved_with_visible_information(self) -> None:
        """模型侧栏承载独有操作时不能直接删除，信息标记仍落在可见区域。"""

        pages = [
            {"pageId": "inbox", "name": "收件箱"},
            {"pageId": "archive", "name": "归档"},
        ]
        code = (
            '<div><aside><button data-action-id="open_archive" '
            'data-control-id="archive-nav">归档</button><span>收件箱</span></aside>'
            '<main>邮件内容</main></div>'
        )
        self.assertTrue(_sidebar_contains_unique_actions(code, pages, []))
        fixed = bind_existing_sidebar_information(
            code, [{"itemId": "inbox-sidebar", "label": "侧边栏"}]
        )
        self.assertEqual(fixed.count("<aside"), 1)
        self.assertIn('data-information-item-id="inbox-sidebar"', fixed)
        self.assertLess(fixed.index('data-information-item-id="inbox-sidebar"'), fixed.index("<aside"))

    def test_visual_gate_only_blocks_severe_core_dimension_drift(self) -> None:
        """总体高分时仍修复严重布局偏离，但不追逐每个维度的同一高阈值。"""

        audit = ScreenshotVisualAudit(
            overall_similarity=94,
            layout_similarity=71,
            component_similarity=92,
            color_similarity=90,
            typography_similarity=89,
            blocking_mismatches=[],
            repair_instruction="修正内容区比例。",
            explanation="布局偏差明显。",
        )

        failures = _audit_failure_reasons(audit, 88)
        self.assertEqual(failures, ["布局相似度 71 明显低于底线 78"])

    def test_visual_gate_allows_equivalent_component_implementation(self) -> None:
        """整体、布局、字体和配色良好时，组件实现差异不应触发重生成。"""

        audit = ScreenshotVisualAudit(
            overall_similarity=82,
            layout_similarity=80,
            component_similarity=58,
            color_similarity=79,
            typography_similarity=81,
            blocking_mismatches=["组件库实现与截图不同。"],
            repair_instruction="无需修复。",
            explanation="视觉角色一致，实现方式不同。",
        )

        self.assertEqual(_audit_failure_reasons(audit, 78), [])

    def test_failed_screenshot_manifest_is_not_reused_on_retry(self) -> None:
        """截图准备失败的 Manifest 必须重新执行视觉模型，不能被幂等恢复短路。"""

        manifest = {
            "product_plan_sha256": "digest",
            "pages": [
                {
                    "pageId": "dashboard_page",
                    "visual_source": "screenshot",
                    "status": "generation_failed",
                    "code": "",
                }
            ],
        }

        self.assertEqual(
            _reusable_manifest(
                {"ui_designs": manifest},
                "digest",
                ["dashboard_page"],
            ),
            {},
        )

    def test_retry_only_generates_failed_pages_and_reuses_successful_page(self) -> None:
        """同一 ProductPlan 重试时必须保留成功页，只调度失败页。"""

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            screenshot_path = workspace / ".xcodeagent/inputs/screenshots/batch/screen.png"
            screenshot_raw = _write_png(screenshot_path)
            requirement_input = _requirement_input(
                screenshot_path,
                workspace,
                screenshot_raw,
            )
            product_plan = {
                "schema_version": "product-plan.v5",
                "confirmation_status": "confirmed",
                "pages": [
                    {
                        "pageId": "usage_info",
                        "name": "用量信息",
                        "actions": [],
                        "information_items": [
                            {"itemId": "usage_balance", "label": "充值余额"}
                        ],
                    },
                    {
                        "pageId": "profile",
                        "name": "个人信息",
                        "actions": [],
                        "information_items": [
                            {"itemId": "profile_username", "label": "用户名"}
                        ],
                    },
                ],
            }
            product_hash = hashlib.sha256(
                json.dumps(product_plan, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            setup = setup_ui_design_project(str(workspace))
            project_dir = str(setup["project_dir"])
            screenshot_sha256 = requirement_input["screenshots"][0]["sha256"]
            usage_code = VALID_TSX.replace("DashboardPage", "UsageInfo").replace(
                "<p>这是根据截图视觉结构生成的页面内容区域。</p>",
                '<p data-information-item-id="usage_balance" '
                'data-control-id="usage_balance-display">¥ 1,286.50</p>',
            )
            usage_path = persist_page_code(project_dir, "UsageInfo", usage_code)
            usage_entry = build_ui_page_manifest(
                product_plan["pages"][0],
                page_key="UsageInfo",
                code_path=usage_path,
                code=usage_code,
                status="confirmed",
            )
            usage_entry["visual_source"] = "screenshot"
            usage_entry["screenshot_ui_pipeline_version"] = SCREENSHOT_UI_PIPELINE_VERSION
            failed_entry = build_ui_page_manifest(
                product_plan["pages"][1],
                page_key="Profile",
                status="generation_failed",
                error="previous failure",
            )
            failed_entry["visual_source"] = "screenshot"
            state = {
                "workspace": str(workspace),
                "project_id": "test-project",
                "requirement_input": requirement_input,
                "requirement_spec": {"confirmation_status": "confirmed"},
                "product_plan": product_plan,
                "ui_designs": {
                    "schema_version": "ui-manifest.v3",
                    "confirmation_status": "pending_user_confirmation",
                    "product_plan_sha256": product_hash,
                    "pages": [usage_entry, failed_entry],
                },
            }
            profile_code = build_contract_scaffold(product_plan["pages"][1], "Profile")
            profile_path = persist_page_code(project_dir, "Profile", profile_code)
            profile_entry = build_ui_page_manifest(
                product_plan["pages"][1],
                page_key="Profile",
                code_path=profile_path,
                code=profile_code,
                status="confirmed",
            )
            profile_entry["visual_source"] = "screenshot"
            reference = NormalizedReference(
                observations=(),
                mappings={},
                global_style_summary="",
                unresolved_questions=(),
            )

            with (
                patch(
                    "app.agents.screenshot_ui_design.agent.require_current_product_plan",
                    return_value=product_plan,
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent.Settings.from_env",
                    return_value=_settings(),
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent.load_ui_reference_images",
                    return_value=[],
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent._load_cached_reference",
                    return_value=reference,
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent._generate_page_entry",
                    return_value=(
                        profile_entry,
                        {"pageId": "profile", "status": "passed"},
                    ),
                ) as generate,
            ):
                result = prepare_screenshot_ui_designs(state)

            self.assertEqual(generate.call_count, 1)
            self.assertEqual(generate.call_args.kwargs["page"]["pageId"], "profile")
            self.assertEqual(
                [page["status"] for page in result["ui_designs"]["pages"]],
                ["confirmed", "confirmed"],
            )

    def test_regenerate_blocks_generic_fallback_after_visual_analysis_failure(self) -> None:
        """视觉模型失败的截图项目点击换一换时不得落入普通文字 UI 生成器。"""

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            specs = workspace / ".xcodeagent/specs"
            specs.mkdir(parents=True)
            (specs / "screenshot-ui-reference.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "screenshot-ui-reference.v1",
                        "analysisError": "vision provider unavailable",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "不能降级为普通 UI 生成"):
                regenerate_screenshot_ui_page(
                    workspace=str(workspace),
                    page={"pageId": "dashboard_page", "name": "项目概览"},
                    page_key="DashboardPage",
                    project_dir=str(workspace / ".xcodeagent/ui-design"),
                )

    def test_product_planning_route_only_inserts_node_for_screenshot_mode(self) -> None:
        """需求审阅优先，截图模式走准备层，文字模式保持原 UI 确认路由。"""

        self.assertEqual(
            _route_product_planning(
                {
                    "requirement_input": {"mode": "screenshot"},
                    "clarification": {"status": "clear"},
                }
            ),
            "screenshot_ui_preparation",
        )
        self.assertEqual(
            _route_product_planning(
                {
                    "requirement_input": {"mode": "text"},
                    "clarification": {"status": "clear"},
                }
            ),
            "ui_confirmation",
        )
        self.assertEqual(
            _route_product_planning(
                {
                    "requirement_input": {"mode": "screenshot"},
                    "clarification": {"status": "requires_user_input"},
                }
            ),
            "requirement_document_review",
        )

    def test_screenshot_node_is_projected_for_progress_and_recovery(self) -> None:
        """AG-UI 应展示截图准备节点，并在恢复与下一节点预测中保留真实路由。"""

        self.assertEqual(
            WORKFLOW_NODE_LABELS["screenshot_ui_preparation"],
            "截图 UI 设计准备",
        )
        self.assertEqual(
            _workflow_start_node(
                "screenshot_ui_preparation",
                "application_planning",
            ),
            "screenshot_ui_preparation",
        )
        self.assertEqual(
            _workflow_next_nodes(
                "product_planning",
                {
                    "status": "completed",
                    "requirement_input": {"mode": "screenshot"},
                },
            ),
            ["screenshot_ui_preparation"],
        )
        self.assertEqual(
            _workflow_next_nodes(
                "screenshot_ui_preparation",
                {"status": "completed"},
            ),
            ["ui_confirmation"],
        )

    def test_screenshot_progress_is_forwarded_as_ag_ui_frames(self) -> None:
        """截图准备 custom event 应投影为 AG-UI 运行快照和过程进度。"""

        async def collect() -> list[str]:
            """收集模拟截图节点生成的完整 SSE 帧。"""

            stream = build_workflow_ag_ui_stream(
                graph=_ScreenshotProgressGraph(),
                payload={
                    "threadId": "thread-screenshot-ui",
                    "runId": "run-screenshot-ui",
                    "messages": [{"role": "user", "content": "根据截图生成应用"}],
                    "forwardedProps": {
                        "workflowScope": "application_planning",
                        "resumeFrom": "screenshot_ui_preparation",
                    },
                },
                accept="text/event-stream",
            )
            return [frame async for frame in stream]

        runtime_settings = SimpleNamespace(
            langsmith_project="",
            langsmith_tracing_enabled=False,
            langsmith_endpoint="",
        )
        with (
            patch(
                "app.protocols.workflow.runtime.Settings.from_env",
                return_value=runtime_settings,
            ),
            patch(
                "app.protocols.workflow.runtime.cleanup_workflow_checkpoints",
                new=AsyncMock(return_value=None),
            ),
        ):
            frames = asyncio.run(collect())
        workflow_values = _decode_custom_frames(frames, "workflow-run")
        progress_values = _decode_custom_frames(frames, "agent-process")
        self.assertTrue(
            any(
                value.get("summary", {}).get("phase")
                == "screenshot_ui_preparation"
                and value.get("summary", {}).get("status") == "running"
                for value in workflow_values
            ),
            workflow_values,
        )
        self.assertTrue(
            any(
                value.get("nodeName") == "screenshot_ui_preparation"
                and value.get("status") == "running"
                for value in progress_values
            ),
            progress_values,
        )

    def test_preparation_writes_compatible_manifest_and_is_idempotent(self) -> None:
        """准备层应写兼容原确认流程的 TSX、三类参考产物和 UiDesign Manifest。"""

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            path = workspace / ".xcodeagent/inputs/screenshots/batch/screen.png"
            raw = _write_png(path)
            requirement_input = _requirement_input(path, workspace, raw)
            sha256 = requirement_input["screenshots"][0]["sha256"]
            product_plan = {
                "schema_version": "product-plan.v5",
                "confirmation_status": "confirmed",
                "pages": [
                    {
                        "pageId": "dashboard_page",
                        "name": "项目概览",
                        "path": "/page/dashboard",
                        "description": "查看项目概览。",
                        "actions": [],
                        "information_items": [],
                    }
                ],
            }
            state = {
                "workspace": str(workspace),
                "project_id": "test-project",
                "requirement_input": requirement_input,
                "requirement_spec": {"confirmation_status": "confirmed"},
                "product_plan": product_plan,
            }
            audit = ScreenshotVisualAudit(
                overall_similarity=91,
                layout_similarity=93,
                component_similarity=90,
                color_similarity=88,
                typography_similarity=89,
                blocking_mismatches=[],
                repair_instruction="无需修复。",
                explanation="主要视觉结构一致。",
            )
            with (
                patch(
                    "app.agents.screenshot_ui_design.agent.require_current_product_plan",
                    return_value=product_plan,
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent.Settings.from_env",
                    return_value=_settings(),
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent._analyze_references",
                    return_value=_analysis(sha256),
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent.invoke_vision_text",
                    return_value=VALID_TSX,
                ) as generation,
                patch(
                    "app.agents.screenshot_ui_design.agent._audit_code",
                    return_value=audit,
                ),
            ):
                result = prepare_screenshot_ui_designs(state)

            manifest = result["ui_designs"]
            self.assertEqual(manifest["schema_version"], "ui-manifest.v3")
            self.assertEqual(
                manifest["confirmation_status"], "pending_user_confirmation"
            )
            self.assertEqual(manifest["pages"][0]["status"], "confirmed")
            self.assertEqual(manifest["pages"][0]["visual_source"], "screenshot")
            self.assertEqual(generation.call_count, 1)

            specs = workspace / ".xcodeagent/specs"
            for name in (
                "screenshot-page-map.json",
                "screenshot-ui-reference.json",
                "screenshot-app-shell-reference.json",
                "screenshot-ui-verification.json",
                "ui-designs.json",
            ):
                self.assertTrue((specs / name).is_file(), name)
            persisted = json.loads((specs / "ui-designs.json").read_text("utf-8"))
            self.assertNotIn("code", persisted["pages"][0])
            hydrated = load_ui_designs_json(specs / "ui-designs.json")
            self.assertEqual(hydrated["pages"][0]["code"].strip(), VALID_TSX.strip())

            verified, verification_errors = _verified_ui_designs_for_confirmation(
                {**state, **result},
                hydrated,
            )
            self.assertEqual(verification_errors, [])
            self.assertEqual(verified["pages"][0]["visual_source"], "screenshot")

            replay_state = {**state, **result}
            with (
                patch(
                    "app.agents.screenshot_ui_design.agent.require_current_product_plan",
                    return_value=product_plan,
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent._analyze_references",
                    side_effect=AssertionError("幂等重放不应再次调用视觉模型"),
                ),
            ):
                replay = prepare_screenshot_ui_designs(replay_state)
            self.assertEqual(
                replay["ui_designs"]["product_plan_sha256"],
                manifest["product_plan_sha256"],
            )

    def test_balanced_visual_score_passes_without_pixel_level_repair(self) -> None:
        """整体风格合格时不应为细小差异触发第二次整页生成。"""

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            path = workspace / ".xcodeagent/inputs/screenshots/batch/screen.png"
            raw = _write_png(path)
            requirement_input = _requirement_input(path, workspace, raw)
            sha256 = requirement_input["screenshots"][0]["sha256"]
            product_plan = {
                "schema_version": "product-plan.v5",
                "confirmation_status": "confirmed",
                "pages": [
                    {
                        "pageId": "dashboard_page",
                        "name": "项目概览",
                        "path": "/page/dashboard",
                        "description": "查看项目概览。",
                        "actions": [],
                        "information_items": [],
                    }
                ],
            }
            state = {
                "workspace": str(workspace),
                "project_id": "test-project",
                "requirement_input": requirement_input,
                "requirement_spec": {"confirmation_status": "confirmed"},
                "product_plan": product_plan,
            }
            settings = _settings()
            audit = ScreenshotVisualAudit(
                overall_similarity=82,
                layout_similarity=85,
                component_similarity=78,
                color_similarity=86,
                typography_similarity=80,
                blocking_mismatches=["标题区域高度仍需人工核对。"],
                repair_instruction="把标题区域高度调整为 48px。",
                explanation="结构可渲染，但未达到自动视觉目标。",
            )
            with (
                patch(
                    "app.agents.screenshot_ui_design.agent.require_current_product_plan",
                    return_value=product_plan,
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent.Settings.from_env",
                    return_value=settings,
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent._analyze_references",
                    return_value=_analysis(sha256),
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent.invoke_vision_text",
                    return_value=VALID_TSX,
                ) as generation,
                patch(
                    "app.agents.screenshot_ui_design.agent._audit_code",
                    return_value=audit,
                ) as audit_call,
            ):
                result = prepare_screenshot_ui_designs(state)

            page = result["ui_designs"]["pages"][0]
            self.assertEqual(page["status"], "confirmed")
            self.assertEqual(page["visual_verification"]["status"], "passed")
            self.assertFalse(page["visual_verification"]["repairApplied"])
            self.assertEqual(
                page["visual_verification"]["scoreRepresents"],
                "current_code",
            )
            self.assertEqual(generation.call_count, 1)
            self.assertEqual(audit_call.call_count, 1)

    def test_audit_outage_keeps_statically_valid_design_for_review(self) -> None:
        """视觉审查暂不可用时应保留可渲染稿并明确标记人工复核。"""

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            path = workspace / ".xcodeagent/inputs/screenshots/batch/screen.png"
            raw = _write_png(path)
            requirement_input = _requirement_input(path, workspace, raw)
            sha256 = requirement_input["screenshots"][0]["sha256"]
            product_plan = {
                "schema_version": "product-plan.v5",
                "confirmation_status": "confirmed",
                "pages": [
                    {
                        "pageId": "dashboard_page",
                        "name": "项目概览",
                        "path": "/page/dashboard",
                        "description": "查看项目概览。",
                        "actions": [],
                        "information_items": [],
                    }
                ],
            }
            state = {
                "workspace": str(workspace),
                "project_id": "test-project",
                "requirement_input": requirement_input,
                "requirement_spec": {"confirmation_status": "confirmed"},
                "product_plan": product_plan,
            }
            with (
                patch(
                    "app.agents.screenshot_ui_design.agent.require_current_product_plan",
                    return_value=product_plan,
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent.Settings.from_env",
                    return_value=_settings(),
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent._analyze_references",
                    return_value=_analysis(sha256),
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent.invoke_vision_text",
                    return_value=VALID_TSX,
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent._audit_code",
                    side_effect=RuntimeError("temporary audit outage"),
                ),
            ):
                result = prepare_screenshot_ui_designs(state)

            page = result["ui_designs"]["pages"][0]
            self.assertEqual(page["status"], "confirmed")
            self.assertEqual(page["visual_verification"]["status"], "review_required")
            self.assertEqual(
                page["visual_verification"]["scoreRepresents"], "unavailable"
            )
            self.assertIn("人工检查", page["visual_verification"]["issues"][0])

    def test_generation_outage_never_confirms_generic_contract_scaffold(self) -> None:
        """视觉模型调用失败时必须显式失败，不能把通用契约骨架冒充截图设计稿。"""

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            path = workspace / ".xcodeagent/inputs/screenshots/batch/screen.png"
            raw = _write_png(path)
            requirement_input = _requirement_input(path, workspace, raw)
            sha256 = requirement_input["screenshots"][0]["sha256"]
            product_plan = {
                "schema_version": "product-plan.v5",
                "confirmation_status": "confirmed",
                "pages": [
                    {
                        "pageId": "usage_info",
                        "name": "用量信息",
                        "path": "/usage",
                        "description": "查看账户用量。",
                        "actions": [],
                        "information_items": [
                            {"itemId": "remaining_tokens", "label": "剩余 Tokens"}
                        ],
                    }
                ],
            }
            state = {
                "workspace": str(workspace),
                "project_id": "test-project",
                "requirement_input": requirement_input,
                "requirement_spec": {"confirmation_status": "confirmed"},
                "product_plan": product_plan,
            }
            with (
                patch(
                    "app.agents.screenshot_ui_design.agent.require_current_product_plan",
                    return_value=product_plan,
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent.Settings.from_env",
                    return_value=_settings(),
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent._analyze_references",
                    return_value=_analysis(sha256),
                ),
                patch(
                    "app.agents.screenshot_ui_design.agent.invoke_vision_text",
                    side_effect=RuntimeError("model temporarily unavailable"),
                ),
            ):
                result = prepare_screenshot_ui_designs(state)

            page = result["ui_designs"]["pages"][0]
            self.assertEqual(page["status"], "generation_failed")
            self.assertNotIn("code", page)
            self.assertIn("视觉稿生成失败", page["error"])
            self.assertNotIn("contractFallbackApplied", page)

    def test_text_mode_is_noop(self) -> None:
        """文字需求模式调用准备函数时不得触发任何截图模型或文件写入。"""

        self.assertEqual(
            prepare_screenshot_ui_designs(
                {"requirement_input": {"mode": "text", "screenshots": []}}
            ),
            {},
        )


if __name__ == "__main__":
    unittest.main()
