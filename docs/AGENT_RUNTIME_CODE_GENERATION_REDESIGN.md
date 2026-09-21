# Agent Runtime 代码生成设计

> 状态：当前合同；模板结构以 `agent-runtime-template` 的简化目录为准

## 1. 目标

XCodeAgent 在开发阶段把已确认 Agent Contract 编译为七个可恢复的 `agent.code`
任务，并在独立可运行的 Python 3.12 + DeepAgents 模板上完成最小修改。

生成结果必须是一份完整工程。代码生成从已下载模板开始，优先复用或修改现有文件；
只有 Contract 要求的能力在模板中确实缺失、且当前任务明确授权新增目录时，才增加文件。

## 2. 当前模板合同

模板仓库不再包含以下平台实现细节：

- `agent-runtime-capabilities.json`；
- `src/app/agent/definition.py`；
- `src/app/agent/tools.py`；
- `config/agents/<agent_id>.json` Definition。

XCodeAgent 不得重新要求或生成这些文件。模板最小入口包括：

```text
src/app/main.py
src/app/settings.py
src/app/agent/factory.py
src/app/agent/context.py
src/app/models/factory.py
src/app/persistence/checkpointer.py
src/app/interaction/schemas.py
src/app/interaction/service.py
src/app/server/app.py
src/app/server/agui.py
src/app/server/health.py
src/app/tools/__init__.py
tests/
```

`factory.py` 中的显式 Builder 注册表是业务 Agent 的组合入口。简单 Agent 应直接新增
Builder 并注册，不按惯例机械创建 `<agent_id>.py`。

## 3. 所有权

| 内容 | 所有者 |
| --- | --- |
| 模板仓库、目录结构和基础 Runtime | 模板维护者 |
| 模板下载记录、Git 来源/commit、readiness | XCodeAgent |
| 七模块物理路径策略 | XCodeAgent 平台 |
| Agent Contract | ProductPlan + TechnicalPlan + 平台编译 |
| 七模块业务实现 | Agent CodeRunner |
| Java Gateway 和 Java 代码 | Backend 同事/对应 owner |

模板不需要理解平台的七层设计。XCodeAgent 通过
`Backend/app/services/agent_runtime_template_policy.py` 持有当前模板路径策略，并在每个
任务中同时绑定模板 commit 和策略 Hash。

## 4. Readiness

当已确认 TechnicalPlan 含 `agent_contracts[]` 时，模板准备必须满足：

- `agentRuntime.required=true` 且下载状态为 `succeeded`；
- 仓库地址、`master` 分支和真实 Git commit 可验证；
- `agent-runtime/` 不是符号链接；
- 当前最小入口文件和目录存在；
- 平台路径策略依赖的已有文件存在且不是符号链接；
- 模板中没有真实 `.env`。

Readiness 不检查 Manifest、Definition Loader 或 `agent/tools.py`。

## 5. Agent Contract 产物信息

Contract 不再声明 Definition 路径，只描述真实复用入口：

```json
{
  "artifacts": {
    "compositionPath": "agent-runtime/src/app/agent/factory.py",
    "testRoot": "agent-runtime/tests"
  },
  "requiredChecks": [
    "uv run --project agent-runtime pytest -q"
  ]
}
```

完整业务事实仍保存在 Agent Contract 的 identity、capabilities、interaction、
agentSettings、invocation、runtime、security 和 evaluation 中，不另复制一份运行时 JSON。

## 6. 七模块任务

平台按固定顺序编译 Prompt、Model、Memory、Tools、Skills、Knowledge、Context。

每个任务具有：

- `owner=agent`、`task_type=agent.code`；
- `source_refs.agent_module`；
- Agent Contract Hash 和当前模块配置 Hash；
- 模板 commit 和平台路径策略 Hash；
- `read_paths`、`modify_paths`、`add_roots`、`test_roots`；
- 仅位于 `agent-runtime/**` 的 `allowed_paths` 和锁范围。

Tools、Skills、Knowledge 在 Contract 明确关闭时可以独立 `skip`；其他模块仍正常执行。
七任务第一版串行，避免多个任务同时修改 `factory.py`。

## 7. 实现决策

每个模块只选择一个动作：

- `skip`：仅用于 Contract 明确关闭的可选模块；
- `reuse`：模板现有实现已完全满足当前模块；
- `modify`：对授权的现有文件做最小修改；
- `add`：现有结构无法承载，并且任务提供合法新增根。

Prompt、Model、Memory 和 Context 优先修改或复用现有 Factory/Factory dependency。
Tools 优先复用 `src/app/tools/`；Skills/Knowledge 未启用时不创建占位目录或伪实现。

Java Gateway 尚未交付时，Tools 可以生成类型和声明，但真实传输必须 fail-closed，不能
猜测 URL、Header、Token 或成功返回。

## 8. 恢复与重新构建

模块终态和证据归属于现有 Build DAG。失败后从首个未完成模块继续；已完成且 Contract、
模块配置、模板 commit、策略 Hash 与文件证据均未漂移的模块可复用。

“从 0 重新构建”必须重新物化干净模板，再执行七模块任务，不能在上一次生成目录上继续
叠加。普通重试不得清理用户未授权的工程文件。

## 9. 安全和验收

- Renderer 只通过 Java Gateway 调用 Agent Runtime；
- Runtime 保持现有 AG-UI SSE 生命周期，不增加平行传输；
- 模型密钥、内部 Token、用户身份和权限不进入 Contract、Prompt 或日志；
- Agent 任务不能修改 frontend、Java backend、`.xcodeagent/` 或正式规划产物；
- 工程验收校验 Agent/模块/Contract Hash/模板 commit/策略 Hash 的一致性；
- 文件 Diff、测试、启动、健康检查和最终验收继续由现有 Workflow 负责。

## 10. 当前实施边界

已实现：简化模板 readiness、平台内置七模块路径策略、七模块确定性任务编译与身份验收、
TechnicalPlan 真实组合入口/测试目录投影，以及生成 Agent/Skill 不再读取 Manifest 或
Definition。

已实现：现有 Testing 质量门固定执行 Contract 声明的 Runtime pytest，记录真实命令证据，
并把代码失败以 `owner=agent` 路由到受 `agent-runtime` 限制的现有 Repair。

尚未实现：模板感知 CodeRunner 的真实文件修改、Java Gateway、真实业务 Agent 的端到端
Chat/Tool 验收，以及 Agent Runtime 的 Launch 和 Acceptance 闭环。

未实现项必须显式失败或保持不可用，不能用占位文件或无 Diff 结果冒充完成。

## 11. CodeRunner 与生成验收待办

下一阶段以“单个业务 Agent 从干净模板生成为可运行工程”为最小闭环：

- CodeRunner 读取当前任务的 Agent Contract、模块配置、模板 commit 和路径策略，不读取隐式历史格式；
- 执行前先判定 `skip/reuse/modify/add`，并把决策、目标文件和原因写入任务证据；
- 每个任务只能修改其 `allowed_paths`，实际工具写路径与 Git Diff 必须同时满足边界；
- `reuse` 必须提供当前文件和行为证据；`modify/add` 必须有真实 Diff；`skip` 只允许用于 Contract 明确关闭的可选模块；
- 单模块失败只使后续依赖保持 pending，已完成模块按 Contract Hash、配置 Hash、模板 commit、策略 Hash 和文件证据决定是否可复用；
- 单模块重生成不清理其他模块或用户未授权文件；“从 0 重建”才使用干净模板重新物化；
- 每个 Agent 的最小生成验收覆盖 Agent 构造、Prompt 加载、模型配置、Tool Schema、Checkpoint 隔离和一个 AG-UI 对话路径；
- 所有 required Agent 完成后执行 `uv run --project agent-runtime pytest -q`，并将真实命令、退出码和有界日志作为 Testing 证据。

CodeRunner 完成不等于前端集成完成。Java Gateway、双向认证、Tool Adapter、`agent_integration=verified` 和前端真实 Chat 的闭环要求继续以 `AGENT_RUNTIME_TEMPLATE_AND_INITIALIZATION.md` 第 21 节为准。
