# Agent 三人协作与阶段分工

> 状态：团队执行计划  
> 更新时间：2026-09-10  
> 适用分支基线：`dev_agent_integration_0908`  
> 说明：本文只定义协作边界、近期交付和长期责任，不新增或修改 Agent Contract、AG-UI、Build DAG、模板或存储合同。

## 1. 目标

当前 Agent 开发已经完成 RequirementSpec、ProductPlan、TechnicalPlan、条件式 Agent Runtime
模板初始化、Agent Workbench 第一期、Agent Settings 第一批可视化编辑，以及业务 Agent Build
接入的主要骨架。下一阶段需要同时推进业务 Agent 代码生成、正式试聊和 Agent Runtime
运行闭环。

三人协作的目标是：

1. 每个人拥有一个长期稳定的责任边界，而不是按临时文件平均分配。
2. 并行任务通过稳定输入输出交接，不依赖其他人的内部实现。
3. 共享主流程文件只由当期集成人修改，避免多人同时调整 Workflow 和生命周期。
4. 所有 Agent 能力继续复用现有 AG-UI、Build、Testing、Launch 和 Acceptance，不建立平行流程。
5. 模板仓库的修改必须单独评审；未经确认，三条工作线都不得顺手修改模板目录或模板合同。

## 2. 三人长期责任

### 2.1 角色 A：Agent 后端生成负责人

当前负责人：Agent 后端代码生成与调试开发者。

长期负责：

- ProductPlan 业务 Agent 信息向 TechnicalPlan `agent_contracts[]` 的确定性编译。
- Agent Contract 和七段 `agentSettings` 的当前合同。
- Prompt、Model、Memory、Tools、Skills、Knowledge、Context 七模块任务编译。
- `agent:runtime` 与 `agent:<agentId>` Build Unit、任务范围和恢复策略。
- Agent Runtime 模板感知 CodeRunner、生成 Skill、写路径和新增路径授权。
- 生成代码的业务正确性、真实 Diff、失败续跑和“从 0 重新构建”。
- Agent Settings 修订后的 Contract 重编译和旧 Build 失效。

不负责：

- 正式试聊面板的交互实现。
- Agent Runtime 进程启动、端口、健康轮询和进程回收细节。
- Java Agent Gateway 的业务实现。

稳定输出：

- 已确认的 Agent Contract。
- 已确认且可执行的 Agent Build DAG。
- 完整的 `agent-runtime/` 生成工程。
- 每个 Agent Build Unit 的状态、Contract Hash、模板 commit、策略 Hash 和文件 Diff。
- TechnicalPlan 已声明的 required checks；运行负责人只执行，不自行发明检查项。

### 2.2 角色 B：Agent 试聊前端负责人

当前负责人：Agent 试聊前端框架开发者。

长期负责：

- 开发阶段 Agent 试聊入口和面板。
- AG-UI 消息、流式输出、工具调用、澄清、中断、取消、重试和错误状态展示。
- `applicationId`、`agentId`、`threadId`、`runId` 的会话隔离。
- 候选版本、当前生效版本和不可用状态的用户表达。
- Agent Settings、运行证据、试聊状态在工作台中的前端投影。
- 明暗主题、空状态、加载状态、失败状态和无障碍交互。

不负责：

- Python Agent Runtime 的业务代码生成。
- Java Gateway 或 Python Runtime 的服务端实现。
- 直接读取内部 JSON、物理 Runtime 地址、Token 或进程信息。
- 自定义 SSE 解析器或第二套聊天协议。

稳定输入：

- `applicationId`、`agentId`、`gatewayEndpointId` 和版本状态。
- Java Gateway 提供的正式 AG-UI Endpoint。
- 平台投射的 Runtime/Build/Launch/Acceptance 状态，不直接扫描工作区。

稳定输出：

- 用户试聊动作。
- 标准 AG-UI 会话请求。
- 对运行、工具调用、澄清和错误的可视化结果。

### 2.3 角色 C：Agent Runtime 运行基础设施负责人

当前负责人：新加入的协作开发者。

长期负责：

- Agent Runtime 本地启动、停止、复用、端口分配和进程回收。
- `/health` 轮询、启动超时、异常退出和有界日志摘要。
- Runtime required checks 的受控执行与证据收集。
- Agent Runtime 接入现有 Testing、Project Launch 和 Acceptance。
- Java Backend、Agent Runtime、Frontend Preview 的启动和停止协调。
- Runtime 运行环境的敏感信息保护、进程隔离和失败清理。

不负责：

- 判断业务 Agent 应生成哪些 Python 文件。
- 读取 Prompt、Tools、Skills 或 Knowledge 来推断启动方式。
- 修改 Agent Contract、七模块任务、CodeRunner 或生成 Skill。
- 修改 Agent Runtime 模板仓库。
- Java Agent Gateway 和前端试聊交互。

稳定输入：

- 工作区根目录。
- `agentRuntime.required`。
- 已确认模板 commit 和模板 readiness。
- required Agent Build Unit 是否全部成功。
- TechnicalPlan 中已经声明的 required checks。
- 启动时由平台提供的非敏感配置以及敏感环境变量引用。

稳定输出：

- `skipped`、`running`、`healthy`、`failed` 或 `stopped` 状态。
- 服务名、回环监听地址、端口、是否复用、健康结果和错误码。
- 内部进程记录和有界日志证据。
- Testing、Launch、Acceptance 可以消费的结构化检查结果。

## 3. 稳定交接边界

三条工作线之间只通过下列边界交接：

```text
角色 A：Agent Contract + Build DAG + 完整生成工程
  -> 角色 C：把生成工程作为黑盒 Runtime 启动、检查和停止
  -> Java Gateway：通过内部接口调用健康的 Agent Runtime
  -> 角色 B：只通过 Java Gateway 的 AG-UI Endpoint 发起试聊
```

角色 C 不得以生成文件名、Agent 类名或七模块实现方式作为启动条件。只要以下公共合同不变，
角色 A 调整生成逻辑时，角色 C 的实现和测试不应修改：

- 生成应用中的 Runtime 根目录为 `agent-runtime/`。
- Runtime 是带 `pyproject.toml` 的独立 Python 工程。
- Runtime 有统一的项目启动入口。
- Runtime 提供无敏感信息的 `GET /health`。
- Host、Port、Java Gateway 地址和内部认证通过启动环境注入。
- 具体检查命令来自当前 TechnicalPlan required checks，而不是运行模块硬编码业务文件。

角色 B 不得依赖 Python Runtime 的内部 URL。只要 Java Gateway 的 AG-UI 合同不变，角色 A 和
角色 C 的内部实现变化不应导致试聊 UI 重写。

## 4. 近期计划

### 4.1 里程碑 M0：形成可并行基线

共同完成：

1. 将 `dev_agent_integration_0908` 当前在途修改整理为一个明确的基线提交。
2. 在基线提交中冻结近期使用的公共边界：Runtime 根目录、启动入口、`/health`、
   `agentRuntime.required`、required checks 来源和 Java Gateway AG-UI 输入。
3. 每个人从同一个基线提交建立独立分支。
4. 在开始修改共享文件前，先在任务说明中列出预计修改路径。

建议分支：

```text
dev_agent_generation_<date>       # 角色 A
dev_agent_chat_<date>             # 角色 B
dev_agent_runtime_launch_<date>   # 角色 C
```

完成标准：三条分支具有相同基线，工作区无未归属修改，每条工作线的输入输出已经确认。

### 4.2 里程碑 M1：三条工作线独立开发

#### 角色 A：闭合真实代码生成

- 让七模块任务从已下载的干净模板开始执行。
- 优先修改或复用模板现有组合入口，只在缺失能力且任务授权时新增文件。
- 保证失败后从首个未完成模块恢复。
- 保留“从 0 重新构建”，并重新物化干净模板。
- 输出真实 Diff，不使用占位文件或无 Diff 结果冒充完成。
- 先完成不依赖 Java Gateway 的业务 Agent 构造和基础行为。

近期完成标准：至少一个真实 Agent Contract 能生成完整 Runtime 工程，Build Unit 状态、Diff
和失败信息可追溯。

#### 角色 B：完成试聊 UI 的协议无关主体

- 使用 Mock AG-UI Client 完成试聊面板主体。
- 支持流式文本、工具状态、澄清、中断、取消、重试和错误。
- 明确展示“Runtime 未启动”“网关未就绪”“候选版本不可试聊”等状态。
- 把真实 Transport 封装为独立 Adapter，暂不写死 Java Gateway 尚未交付的实现细节。
- 不修改主 Workflow，不让试聊进入 `/workflow/run`。

近期完成标准：在 Mock Transport 下完成一轮完整会话和异常状态演示，替换 Transport 时无需
重写面板。

#### 角色 C：完成独立 Runtime Launcher

第一批建议只新增独立模块和测试，不修改共享 `project_launcher.py`：

```text
Backend/app/services/agent_runtime_project_launcher.py
Backend/app/services/agent_runtime_process_registry.py
Backend/tests/test_agent_runtime_project_launcher.py
```

需要实现：

- required/skipped 判断。
- Runtime 工程和启动入口检查。
- 回环地址与端口分配。
- 启动环境构造，敏感值不进入结果和日志。
- 启动、复用、超时、异常退出、健康轮询和停止。
- 重复启动不产生多个失控进程。
- 停止后释放端口和进程记录。
- 使用独立最小 Fixture 或进程替身测试，不依赖角色 A 当前生成的业务文件。

近期完成标准：Launcher 可以针对符合公共合同的最小 Runtime 黑盒完成启动、健康检查、复用
和停止，并能稳定处理目录缺失、端口冲突、启动超时和进程异常退出。

### 4.3 里程碑 M2：小范围接入主流程

接入顺序：

1. 角色 A 提供一个已生成且 Build 成功的 Agent Runtime 工程。
2. 角色 C 用独立 Launcher 启动该工程，确认无需了解业务生成结构。
3. 角色 C 将 Launcher 接入现有 Project Launch；共享文件由角色 C 修改，角色 A 评审。
4. Java Gateway 完成内部调用和认证后，角色 B 替换 Mock Transport。
5. 角色 B 通过 Java Gateway 完成一次真实试聊。

启动顺序固定为：

```text
分配 Java Backend 与 Agent Runtime 端口
  -> 注入双方内部地址和认证配置
  -> 启动 Java Backend 与 Agent Runtime
  -> 分别完成基础健康检查
  -> 两边健康后启动或展示 Frontend Preview
```

停止顺序固定为：

```text
停止 Frontend Preview
  -> 停止 Agent Runtime
  -> 停止 Java Backend
  -> 清理本轮进程句柄
```

近期完成标准：任一 required 服务失败时不显示整体启动成功；停止或失败不会遗留 Runtime
进程；前端不直接连接 Python Runtime。

### 4.4 里程碑 M3：形成第一条端到端演示链路

联合验收场景：

1. 新建一个包含业务 Agent 的应用。
2. 确认 RequirementSpec、ProductPlan、UiDesign 和 TechnicalPlan。
3. 条件式下载 Agent Runtime 模板。
4. 执行目标 Agent 的七模块 Build 任务。
5. 查看真实代码 Diff。
6. 执行 Agent required checks。
7. 启动 Java Backend、Agent Runtime 和 Frontend Preview。
8. 在正式试聊面板发起一轮对话。
9. 查看流式输出、错误状态和运行证据。
10. 停止预览并确认所有进程已回收。

第一阶段允许 Java Tool Adapter 保持 fail-closed，但必须明确显示网关能力尚未交付，不能返回
伪造业务数据或伪成功结果。

## 5. 近期文件所有权

### 5.1 角色 A 优先所有

- `Backend/app/agents/agent_runtime/`
- `Backend/app/services/agent_build_tasks.py`
- `Backend/app/services/agent_runtime_template_policy.py`
- `Backend/app/services/build_task_planner.py` 中 Agent 任务编译部分
- `Backend/app/builtin_skills/agent-runtime-generate/`
- Agent Contract、Agent Build 和代码生成相关测试

### 5.2 角色 B 优先所有

- Agent 试聊专属组件、Hook、Service 和样式
- Agent 试聊 AG-UI Client Adapter
- Agent 试聊前端状态和主题测试

角色 B 应优先新增聚焦文件，避免继续向大型 `AiChatPanel.tsx` 堆积业务逻辑。

### 5.3 角色 C 优先所有

- 新增 Agent Runtime Launcher
- 新增 Agent Runtime Process Registry
- 新增 Agent Runtime required-check 执行适配器
- 对应 Backend 测试
- 接入阶段中 `Backend/app/services/project_launcher.py` 的 Agent Runtime 部分

### 5.4 共享文件

以下文件可能被多条工作线消费，但同一批次只能指定一名集成人修改：

- `Backend/app/graph/nodes/lifecycle.py`
- `Backend/app/graph/subgraphs/testing.py`
- `Backend/app/graph/subgraphs/acceptance.py`
- `Backend/app/services/project_launcher.py`
- `Backend/app/graph/state.py`
- `Backend/app/protocols/workflow/`
- `Frontend/src/renderer/src/typings/workflow.ts`
- `docs/CODEBASE_INDEX.md`
- `docs/AGENT_DEVELOPMENT_IMPLEMENTATION_STATUS.md`

需要修改共享合同或共享文件时，先由提出者写出字段、调用方和影响面，由当期集成人统一修改，
其他人通过评审提供意见。

## 6. 长期计划

### 6.1 角色 A 长期路线

1. 提升七模块生成质量和模板复用判断。
2. 完成 Java Tool Adapter 的真实生成与 Schema 约束。
3. 在平台正式支持后启用 Skills、Knowledge、Long-term Memory 和 Context Compression。
4. 建立 Contract/模块配置/模板 commit/文件证据驱动的增量复用。
5. 完成 Agent Settings 变更后的定向重建和失败恢复。
6. 支持多 Agent，但继续保持每个 Agent 独立 Unit、资源锁和验收证据。

### 6.2 角色 B 长期路线

1. 接入真实 Java Gateway AG-UI Transport。
2. 完善工具调用、审批、澄清和多轮恢复体验。
3. 展示候选、active 和历史只读版本。
4. 将 Runtime、required checks、Code Review 和验收证据投射到工作台。
5. 在 Skills 和 Knowledge Catalog 合同确定后完成正式选择器。
6. 支持多 Agent 会话隔离和按版本试聊。

### 6.3 角色 C 长期路线

1. 完成 Agent Runtime required checks 与现有 Unit/Integration Testing 的接入。
2. 完成 Java Backend、Agent Runtime、Frontend Preview 的多进程启动协调。
3. 将 Runtime 启动和健康证据接入 Code Review、Launch 与 Acceptance。
4. 完善崩溃恢复、重复启动、端口冲突、日志截断和进程回收。
5. 配合发布流程处理打包环境中的 Python Runtime 和依赖安装边界。
6. 在正式可观测合同确认后接入 trace、运行指标和审计摘要，不提前实现未确认字段。

### 6.4 外部依赖：Java Gateway

Java Gateway 保持独立责任边界：

- 对前端提供正式 Agent Chat AG-UI Endpoint。
- 对 Python Runtime 使用内部认证和受控网络地址。
- 注入可信 user、tenant、scope、thread、run 和 trace 上下文。
- Tool Adapter 通过 Java Gateway 调用已确认业务 API。
- 处理断流、超时、取消和错误映射。

三人工作线都不得在 Java Gateway 未交付时复制一套临时正式网关。Mock 只能用于本地 UI 或
自动化验证，必须明确标记，不能作为真实端到端完成证据。

## 7. 暂不分配的开放模块

以下内容尚未形成足够稳定的正式合同，暂不作为新同事的独立开发任务：

- Knowledge Catalog、知识权限和知识内容持久化。
- Long-term Memory 的存储、清理和租户隔离。
- Skills 安装到生成 Runtime 的正式快照与失效机制。
- Agent candidate/active 的独立持久化、发布和回滚。
- 完整可观测性、计费、审计和运行指标。
- 多 Agent 编排和 Agent-to-Agent 调用。

这些能力开始前必须先完成设计、确认 owner、当前 schema、失效规则和安全边界。

## 8. 协作与合并规则

1. 开发前从同一个已提交基线创建分支，不从带未提交修改的目录复制代码。
2. 每条工作线提交前列出修改文件，避免无意修改其他人的 owner 区域。
3. 不用同一提交同时重构共享文件和实现业务功能。
4. 需要公共字段时先确认生产者、消费者、默认值、错误语义和 AG-UI 投影，再修改代码。
5. 新增产品动作必须使用 AG-UI；基础进程管理可以保持内部服务调用，不新增平行产品 API。
6. 任一正式产物更新后继续执行显式确认门禁，不用调试入口绕过确认。
7. 合并顺序优先为角色 A 的稳定生成基线、角色 C 的独立 Launcher、角色 B 的 Transport
   Adapter，最后由集成人处理共享主流程文件。
8. 出现冲突时以当前已确认合同为准，不增加历史字段 fallback 或双写兼容。

## 9. 每人交付清单

每次提交或合并请求必须说明：

- 本次完成的用户能力。
- 修改文件和文件 owner。
- 消费的稳定输入及产出的稳定输出。
- 未触碰的其他工作线边界。
- 真实验证结果和未验证项。
- 已知依赖、风险和下一步联调条件。

不得使用“代码存在”“模型说完成”或“没有看到错误”代替真实完成证据。

## 10. 当前建议的首批任务

角色 A：

- 继续完成模板感知 CodeRunner 的真实生成调试。
- 固定生成工程对 Launcher 暴露的公共运行合同。
- 提供一个可重复生成的 Agent 示例工程用于后续联调。

角色 B：

- 使用 Mock AG-UI Transport 完成生产试聊面板主体。
- 把真实网关调用隔离在单独 Adapter 中。
- 完成不可用、运行中、取消和失败状态。

角色 C：

- 先实现独立 Agent Runtime Launcher 和 Process Registry。
- 用最小 Runtime Fixture 完成启动、健康检查、复用、停止和失败清理。
- 第一批不修改 Agent 生成、TechnicalPlan、Build DAG、前端和模板仓库。

三人首批任务完成后，再评审 M2 的共享主流程接入，不在独立开发阶段提前耦合。
