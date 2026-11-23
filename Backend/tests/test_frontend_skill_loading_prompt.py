"""静态核对页面 Skill 启动顺序和 API 轻量模式的提示边界。"""

import json
import re
import tempfile
import unittest
from unittest.mock import patch

from deepagents.backends import StateBackend

from app.agents.frontend.agent import create_frontend_agent
from app.agents.frontend.execution_scope import FRONTEND_CORE_DOCS, FrontendTaskExecutionScope
from app.agents.frontend.generator import _scoped_generation_prompt
from app.agents.frontend.task_packet import API, PAGE, _standard_rules
from app.services.builtin_skills import resolve_builtin_skills_root


class FrontendSkillLoadingPromptTests(unittest.TestCase):
    """检查实际构造边界的文本，不将脚本调用视为模型行为保证。"""

    def test_page_and_api_prompt_boundaries(self):
        """页面先启动再读规范，API 保留原读取方式和无步骤执行。"""
        with tempfile.TemporaryDirectory() as workspace:
            for kind in (PAGE, API):
                with self.subTest(kind=kind):
                    packets = [{"id": "task", "kind": kind, "implementation_contract": {
                        "frontend_standards": ["FE-API-BOUNDARY"],
                    }}]
                    scope = FrontendTaskExecutionScope(workspace, packets)
                    with patch("app.agents.frontend.agent.create_deep_agent") as create:
                        create_frontend_agent(
                            object(), workspace, user_skills_backend=StateBackend(),
                            agent_memory_backend=StateBackend(), build_scope=scope,
                        )
                    system = create.call_args.kwargs["system_prompt"]
                    with patch("app.agents.frontend.generator.record_prompt") as record:
                        prompt = _scoped_generation_prompt(packets)
                    task_text = json.dumps(packets, ensure_ascii=False, separators=(",", ":"))
                    record.assert_called_once_with(prompt, task_context=task_text)
                    self.assertTrue(prompt.endswith(task_text))
                    for text in (system, prompt):
                        self.assertIn("implementation_contract", text)
                        self.assertIn("task_results", text)
                    for path in FRONTEND_CORE_DOCS:
                        self.assertEqual(prompt.count(path), 1)
                    self.assertNotIn("frontend-template-modification-boundary", prompt)
                    self.assertIn("同一任务每份最多成功读取一次", prompt)
                    self.assertIn("单次完整读取（offset=0，limit=2000）", prompt)
                    self.assertIn("仅按需阅读示例", prompt)
                    self.assertIn("不搜索替代 AGENTS.md、不回退旧边界 Skill", prompt)
                    self.assertIn('最终输出单一 JSON 对象 {"task_results":[...]}', prompt)
                    self.assertIn("status（completed、already_satisfied 或 failed）", prompt)
                    self.assertIn("失败提供 failure_category、failure_reason", prompt)
                    self.assertIn("failure_category=tool_error", prompt)
                    self.assertIn("不将读取失败归类为契约或计划冲突", prompt)
                    self.assertIn("必须提供非空且具体说明所需调整的 change_request", prompt)
                    self.assertIn("其他失败分类不得携带 change_request", prompt)
                    if kind == PAGE:
                        self.assertIn("references/component-patterns.md 对应章节", prompt)
                        self.assertIn("references/page-structure-examples.md 对应章节", prompt)
                        self.assertIn("不默认完整读取全部 references", prompt)
                        self.assertNotIn("不搜索其他业务 API 文件获取契约", prompt)
                        self.assertIn("第一次工具调用必须单独调用 begin_frontend_task", system)
                        self.assertIn("等待其成功返回 Step 1 后", system)
                        self.assertIn("不能将 begin_frontend_task 与其他工具并行调用", system)
                        self.assertIn("等待成功返回 Step 1", prompt)
                        self.assertIn("不得与任何其他工具并发或同批发送", prompt)
                        self.assertIn("仅在进入 Step 1 后，读取", prompt)
                        self.assertIn("Step 1 之前不得尝试读取 Skill 或业务文件", prompt)
                        self.assertIn("不因切换步骤重复读取", prompt)
                        for path in FRONTEND_CORE_DOCS:
                            self.assertIn(path, prompt)
                        for step in ("Step 1 只读", "Step 2/3 复用核对结果", "Step 4 只读"):
                            self.assertIn(step, prompt)
                        for boundary in ("不要求读完全部文件", "定向补读已授权精确文件",
                                         "读取失败不得猜测接口或扩大搜索范围"):
                            self.assertIn(boundary, prompt)
                        self.assertLess(prompt.index("等待成功返回 Step 1"),
                                        prompt.index("frontend-engineering-standards/SKILL.md"))
                    else:
                        self.assertIn("平台已初始化任务级权限", system)
                        self.assertIn("开始实施时读取", prompt)
                        self.assertIn("后续任务复用规范", prompt)
                        for guidance in (
                            "直接使用 implementation_contract 中已注入的正式 API 契约",
                            "不搜索其他业务 API 文件获取契约",
                            "按需读取已授权的统一请求封装 src/apis/service.ts 和当前目标文件",
                            "已有目标文件优先保留符合契约的实现并增量修改",
                            "必要示例仅用于编码参考，不覆盖业务契约",
                            "模板 AGENTS.md 中列出的工程文件仅用于说明结构，不是逐一读取清单",
                            "无当前任务明确依赖时，不读取相邻 API、登录或权限模块",
                            "状态足以实施时，立即实现并做必要自检",
                            "不再为了寻找参考而探索其他文件",
                            "无关文件读取被拒绝时不得转向目录遍历或无关的 grep、Code Graph 搜索",
                            "若存在真实契约冲突或必要信息缺失，只能在既有授权范围内定向核对",
                            "无法完成时如实返回 failed，不得虚报完成或请求扩大权限",
                            "需核对时可正常重读",
                            "已有 API 文件使用 edit_file 增量实现",
                        ):
                            self.assertIn(guidance, prompt)
                        for control in ("begin_frontend_task", "advance_frontend_step", "complete_frontend_task"):
                            self.assertNotIn(control, system)
                            self.assertNotIn(control, prompt)
                        for step in ("四步", "Step 1", "Step 2", "Step 3", "Step 4"):
                            self.assertNotIn(step, prompt)

    def test_required_rules_exist_and_match_task_responsibilities(self):
        """逐项核对规则声明与适用职责，不给 API 模块下发页面交互规则。"""
        root = resolve_builtin_skills_root() / "frontend-engineering-standards"
        standard = (root / "SKILL.md").read_text(encoding="utf-8")
        rows = dict(re.findall(r"\| `(FE-[A-Z-]+)` \| ([^|]+) \|", standard))
        for kind in (API, PAGE):
            rules = _standard_rules(kind)
            self.assertEqual(len(rules["required_rule_ids"]), len(set(rules["required_rule_ids"])))
            for rule in rules["required_rule_ids"]:
                self.assertIn(rule, rows)
                self.assertIn("页面" if kind == PAGE else "API 模块", rows[rule])
            for reference in rules["example_refs"]:
                self.assertTrue((root / "references" / f"{reference}.md").is_file())
        self.assertEqual(set(_standard_rules(PAGE)["required_rule_ids"]), set(rows))
        self.assertEqual(set(_standard_rules(API)["required_rule_ids"]), {
            "FE-API-BOUNDARY", "FE-TYPES", "FE-CODE-LAYERING", "FE-DATA-PRECISION", "FE-NAMING",
        })
        for relative in re.findall(r"\]\((references/[^)]+)\)", standard):
            self.assertTrue((root / relative).is_file())

    def test_reference_examples_do_not_reintroduce_request_coupling(self):
        """核对示例中 JSX 请求边界和废弃重复查询状态，不冒充真实模型生成验证。"""
        root = resolve_builtin_skills_root() / "frontend-engineering-standards" / "references"
        for path in root.glob("*.md"):
            for code in re.findall(r"```tsx?\n(.*?)```", path.read_text(encoding="utf-8"), re.S):
                self.assertNotRegex(code, r"\brequest\s*=")
                self.assertNotIn("requestQuery", code)
                self.assertNotRegex(code, r"\bvisible[=:\s]")
                if path.name != "api-request-patterns.md" and "// src/apis/" not in code:
                    self.assertNotRegex(code, r"\b(?:service|axios|fetch)\s*[.(]")
                    self.assertNotIn("from 'mockjs'", code)
                    self.assertNotIn("from 'xlsx'", code)
