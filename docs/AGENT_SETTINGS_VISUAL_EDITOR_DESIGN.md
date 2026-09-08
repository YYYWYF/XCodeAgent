# Agent Settings 可视化编辑与正式契约修订设计

> 状态：第一批已实现（七段只读可视化 + Prompt/Temperature 正式修订）
>
> 适用范围：开发阶段 Agent 详情中的七类 Settings 展示、编辑、预览、确认、正式 TechnicalPlan 更新与后续 Build 失效
>
> 不在本设计范围：ProductPlan Agent 事实编辑、Runtime 热更新、独立 Agent 配置发布系统、历史 Contract 兼容

## 1. 设计目标

开发阶段不再把 `agentSettings` 作为可直接编辑的 JSON 暴露给用户，而是提供结构化、能力感知的可视化配置界面。用户只修改平台允许的关键参数；平台根据已确认 ProductPlan、当前 TechnicalPlan、API Contract、Runtime 能力和安全策略重新编译完整 Agent Contract。

最终仍由 `.xcodeagent/plans/technical-plan.json` 中的 `agent_contracts[].agentSettings` 保存正式配置，不新增 `agent-settings.json`，也不把生成的 Python 代码作为配置事实来源。

本设计需要同时满足：

1. 用户不需要理解或维护 JSON 结构。
2. ProductPlan 继续是 Agent 身份、职责、能力、入口和业务边界的唯一事实来源。
3. 用户只能修改允许编辑的 Agent Settings，不能覆盖平台派生字段。
4. 任何正式 Agent Contract 更新都经过修改预览和明确确认。
5. 前端不直接写 TechnicalPlan 文件，不提交整份旧 Contract。
6. 配置变化只使受影响的 Agent 实现与证据失效，不扩大到无关页面、Endpoint、实体或 Agent。
7. 未实现的 Runtime Adapter 只读展示为“当前版本未启用”，不提供无法生效的假开关。

## 2. 核心结论

### 2.1 用户体验是表单，系统语义是正式修订

界面可以使用“编辑配置”和“保存”这类用户语言，但系统动作固定分为两步：

```text
编辑 Agent Settings
        ↓
生成修改预览（尚未修改正式 JSON）
        ↓
平台校验并重新编译完整 Agent Contract
        ↓
用户确认应用
        ↓
原子更新 TechnicalPlan Markdown + JSON
        ↓
重新投影开发阶段并标记受影响证据失效
```

主按钮建议分别使用：

- 编辑态：`生成修改预览`
- 预览态：`确认并应用`
- 辅助动作：`继续修改`、`放弃修改`

不得把“保存表单”实现为 Renderer 直接覆盖 JSON。

### 2.2 不新增平行配置事实

第一版不实现独立的 `draft/candidate/active` Agent 配置文件：

- 未提交的表单值只属于当前界面的临时状态。
- 生成预览后，复用现有正式 Application Revision 的 TechnicalPlan draft、`changeId`、资源锁和确认机制。
- 确认后只有当前 TechnicalPlan 是正式事实。
- 放弃后删除当前 revision draft，不影响已确认 TechnicalPlan。
- 未来只有在需要“已上线配置继续运行、候选配置独立 Build/试聊”时，才单独设计 candidate/active 发布层。

### 2.3 完整 Contract 必须由平台重新编译

用户提交的是严格类型化的 Settings Patch。服务端必须重新执行：

```text
已确认 ProductPlan Agent
+ 当前 API Contract / Endpoint
+ 当前平台 Model / Memory / Skill / Knowledge Catalog
+ 用户允许修改的 Settings Patch
+ Runtime 能力清单
+ 平台安全策略
= 新的完整 Agent Contract
```

平台重新生成 ProductPlan Hash、Endpoint 快照、Runtime、Invocation、Security、Artifacts、Required Checks 和 Contract Hash。前端传入这些字段时必须拒绝，而不是忽略后继续保存。

## 3. 页面信息架构

Agent 详情继续保留“概览、Agent Settings、依赖、实现与证据”四个区域。Agent Settings 改为七个结构化分组，不再默认渲染 `<pre>{JSON.stringify(...)}</pre>`。

```text
Agent Settings                         [来源：TechnicalPlan] [编辑配置]

  人设与 Prompt        已配置       角色、语气、Prompt 摘要
  模型配置             项目默认     模型、生成参数、能力检查
  记忆模块             SQLite       短期 / 长期 / 归档状态
  工具配置             3 个工具     名称、Endpoint、读写和审批
  Skills               未启用       已批准 Skill 绑定
  知识库配置           未启用       Knowledge Source 与检索策略
  上下文配置           默认策略     来源、预算与压缩策略
```

每个分组统一显示：

- 状态：已启用、未启用、部分可用、当前版本不支持。
- 来源：ProductPlan 推导、用户可配置、平台派生。
- 当前值的用户可读摘要。
- Runtime 能力与阻塞原因。
- 编辑态中的字段控件及就地校验信息。

只读模式不显示禁用输入框堆叠，而使用描述列表、标签和摘要。这样用户能清楚区分“信息展示”和“当前可以操作”。

## 4. 页面状态与动作

### 4.1 状态机

| 状态 | 页面表现 | 可用动作 |
| --- | --- | --- |
| `confirmed` | 展示已确认 Contract | 编辑配置、进入 Build |
| `editing_local` | 展示本地表单和未保存标记 | 生成修改预览、取消 |
| `compiling_preview` | 服务端校验并编译 | 取消当前请求 |
| `awaiting_confirmation` | 展示字段 Diff 和影响范围 | 确认并应用、继续修改、放弃 |
| `applying` | 原子写入正式 TechnicalPlan | 无重复提交 |
| `requires_rebuild` | 展示新 Contract 和旧证据失效提示 | 重新 Build |
| `conflict` | Contract 已被其他修订更新 | 重新加载，不自动覆盖 |
| `error` | 保留本地输入并展示字段错误 | 修正后重试 |

### 4.2 进入编辑

点击“编辑配置”时必须记录：

- `agentId`
- `basedOnContractHash`
- `basedOnTechnicalPlanSha256`
- 当前 lifecycle revision
- 七段 Settings 的可编辑投影
- 当前 Runtime capability flags

已经存在同一应用的正式写修订时，不允许开启第二个写事务。界面展示当前修订的阶段和返回入口，不用前端布尔值模拟锁。

### 4.3 离开保护

存在未生成预览的本地修改时，切换 Agent、会话或工作台阶段需要提示“有尚未提交的 Agent 配置修改”。关闭提示只保护用户输入，不代表持久化事实。

## 5. 七类 Settings 字段设计

### 统一展示约定

- `enabled`、`requiredCapabilities`、Context Source 等布尔状态统一使用只读复选框表达；勾选代表开启或需要，未勾选代表关闭或不需要，不再为每个布尔值堆叠彩色状态标签。
- 用户配置、项目模型策略、权限和写操作审批等对用户有直接解释价值的非布尔语义继续使用小型标签；“平台派生”“Endpoint 派生”“平台能力”等内部实现来源不在界面展示。
- 七个分组使用独立折叠卡片和一致的标题、内容间距；默认展开 Prompt 与模型配置，其余按需查看。

## 5.1 人设与 System Prompt

### 可编辑字段

- `prompt.persona.role`：角色描述，单行文本。
- `prompt.persona.tone`：表达风格，单行文本或预置选择。
- `prompt.systemPrompt`：完整业务 System Prompt，多行编辑器。
- `prompt.constraints[]`：业务级约束，可添加、删除、排序。

### 只读信息

- Agent 名称、用途、Capabilities、Boundaries。
- ProductPlan 派生的身份摘要。
- 平台安全前缀状态。
- Tool、Skill、Knowledge 的权限边界摘要。

### 校验规则

- Role、Tone、System Prompt 必须非空。
- 不允许出现密钥、连接串、宿主绝对路径或明显的凭据内容。
- Prompt 不能声明不存在的 Tool、Skill、Knowledge Source。
- Prompt 不能授权绕过审批、安全前缀或 Java Gateway。
- ProductPlan Boundaries 即使没有写在用户 Prompt 中，也必须由平台重新注入。

平台安全 Prompt 永不作为可编辑文本返回给 Renderer。

## 5.2 模型配置

### 可编辑字段

- 模型选择：从平台 Model Registry 提供的可用项中选择。
- `model.generation.temperature`：使用带范围说明的滑块与数字输入框。

### 能力感知规则

- 当前只支持 `project_default` 时，模型选择只读展示“跟随项目默认模型”。
- 只有 Runtime 和 Model Registry 同时声明支持单 Agent override 时，才允许选择具体 `modelRef`。
- 模型供应商、模型名和能力来自 Registry，用户不能输入任意自由字符串。
- `requiredCapabilities` 完全只读，由 ProductPlan、Tools 和输出要求推导。
- `streaming` 与 `observability` 都由 TechnicalPlan 默认写入 `requiredCapabilities` 并固定为 `true`；开发阶段只读展示，用户不能关闭。当前仅完成 Contract 声明与校验，不扩展 Runtime 可观测实现。
- API Key、Base URL、代理和其他凭据不进入 Agent Contract，也不在此页面编辑。
- “是否允许追问”不属于模型能力，继续由 ProductPlan `interaction.clarification` 管理并只读展示。

### 校验规则

- Tools 非空时模型必须支持 Tool Calling。
- 当前交互要求 Streaming 时模型必须支持 Streaming。
- 结构化输出、Vision 等能力由正式需求决定，用户不能通过关闭开关规避。
- 不满足能力要求的模型在下拉列表中禁用并展示原因。

## 5.3 Memory

### 短期记忆

可编辑项：

- 启用状态，但 `supportsMultiTurn=true` 时固定开启。
- Store：只展示 Runtime 已实现的 `sqlite` 或 `mysql`。
- Connection Ref：从平台已登记连接中选择，不允许自由输入。

只读项：

- Scope 固定为 `thread`。
- Retention 的平台策略摘要。
- Checkpoint Adapter 就绪状态。

### 长期记忆

可编辑项仅在正式 Adapter 可用时出现：

- 启用状态。
- Store：`mysql` 或 `oss` 中当前可用项。
- Connection Ref。
- Scope。
- Write Policy，默认 `explicit`。

### Archive

只有 OSS Archive Adapter、连接引用、Retention 和删除能力均就绪时才允许启用。OSS 不得作为短期 Checkpointer。

### 当前版本策略

当前 Runtime 只实现 SQLite checkpoint 时：

- Short-term 显示为 SQLite，可按 ProductPlan 多轮需求固定启用。
- MySQL、Long-term 和 Archive 显示“当前版本未启用”。
- 不渲染可点击但不会进入 Runtime 的开关。

## 5.4 Tools

工具区使用可选择列表和详情抽屉，而不是直接编辑 bindings JSON。

### 用户可操作内容

- 从当前 TechnicalPlan 已确认 Endpoint 候选中绑定或移除 Tool。
- 编辑 Tool 的业务名称和使用时机描述。
- 查看每个 Capability 当前绑定的 Tool。

### 平台只读内容

- `endpoint.apiContractId`、`endpoint.endpointId`。
- Method、Path、Request/Response Schema。
- `accessMode` 的最终判定。
- `approvalPolicy=platform_managed`。
- Java Tool Adapter 与 EntitySourceBinding readiness。

### 校验规则

- Gateway Endpoint 不能注册为 Tool。
- Endpoint 必须唯一存在且 Schema 引用闭合。
- 写 Endpoint 必须显示“运行时需要审批”，不能由用户关闭审批。
- ProductPlan 必需 Capability 不能因为移除 Tool 变成不可实现；存在缺口时阻止预览并指出 Capability。
- Skill 或 Knowledge 不能隐式增加 Tool。
- 用户不能输入任意 URL、Method、Schema 或数据库连接。

## 5.5 Skills

### 可编辑字段

- 启用状态。
- 从当前应用已批准 Skill Catalog 中选择 Skill。
- 每个 Skill 的业务用途说明。

### 只读字段

- Stable `skillId`。
- Revision/hash。
- 发布者、权限摘要和 Runtime Loader 状态。

当前 Runtime 没有正式 Skill Loader 时，整个分组只读显示“当前版本未启用”。不得接受本地路径、Git 地址或自由文本作为 Skill 引用。

第一批仍在 Skills 标题最右侧展示 `+` 占位入口，悬停明确提示“Skill 市场接入功能还在开发中”；按钮不写状态、不打开空弹窗，也不伪造 Catalog 数据。

## 5.6 Knowledge

### 可编辑字段

- 启用状态。
- 从已批准 Knowledge Source 中选择数据源和 Collection。
- Retrieval Strategy。
- `topK`。
- `scoreThreshold`。
- Rerank 开关。
- Citation Policy。

### 只读字段

- Knowledge Source ID、revision/hash、可用状态。
- 当前用户与 Source 权限交集。
- Retriever Adapter 状态。

没有正式 Knowledge Artifact、权限或 Runtime Retriever 时，此分组只读关闭。用户不能填写文件路径、URL、Bucket 名、Token 或连接串。

第一批仍在知识库标题最右侧展示 `+` 占位入口，悬停明确提示“知识库接入功能还在开发中”；按钮不写状态、不接受配置，也不创建平行知识库事实。

## 5.7 Context

### 可编辑字段

- `budget.maxInputRatio`。
- `budget.reserveOutputRatio`。
- Compression Strategy：只展示当前 Runtime 支持的策略。
- `compression.triggerRatio`。
- `compression.preserveRecentTurns`。

### 只读字段

- Context Source 类型、Trust 类型和来源说明。
- `budget.strategy=model_window`。
- `preserveSystemPrompt=true`。
- `preserveToolCallPairs=true`。
- 模型窗口和平台最终计算出的 Token 预算。

Knowledge 未启用时 `knowledge_results` 必须关闭；Tool 未启用时 Tool Result Source 可以只读关闭。Summary Adapter 尚未实现时，只允许 `none`，不显示可用的 Summary 开关。

## 6. 不属于 Agent Settings 编辑器的字段

以下内容继续只读，并提供“前往正式设计修改”提示：

| 字段 | 事实来源 | 修改入口 |
| --- | --- | --- |
| 名称、用途、角色身份 | ProductPlan | Product/Technical 正式修订 |
| Capabilities 与预期结果 | ProductPlan | Product/Technical 正式修订 |
| 页面入口与 Action | ProductPlan + UiDesign | 正式设计修订 |
| Interaction 与追问 | ProductPlan | Product/Technical 正式修订 |
| Boundaries、产品验收 | ProductPlan | Product/Technical 正式修订 |
| Gateway | TechnicalPlan 平台派生 | 修改 API/Agent 设计后重编译 |
| Runtime、Invocation、Security | 平台能力和安全策略 | 不允许页面直接修改 |
| Artifacts、Required Checks | 平台 Build 规则 | 不允许页面直接修改 |
| Endpoint Method、Path、Schema | TechnicalPlan API Contract | Endpoint 正式修订 |

## 7. 修改请求合同

前端只提交当前 Agent 和被修改的七段 Settings Patch。示意结构如下；正式实现必须使用严格 Pydantic 模型并禁止未知字段：

```json
{
  "action": "prepare_agent_settings_revision",
  "workspaceRoot": "/resolved/workspace",
  "agentId": "inventory_assistant",
  "basedOnContractHash": "sha256:...",
  "basedOnTechnicalPlanSha256": "sha256:...",
  "changedSections": ["prompt", "model"],
  "settingsPatch": {
    "prompt": {
      "persona": {
        "role": "库存业务助手",
        "tone": "专业、简洁"
      },
      "systemPrompt": "...",
      "constraints": ["..."]
    },
    "model": {
      "selection": "project_default",
      "modelRef": "project_default",
      "generation": {
        "temperature": 0.2
      }
    }
  }
}
```

约束：

- `settingsPatch` 只能包含 `changedSections` 声明的段。
- Patch 使用每段严格 DTO，不采用通用 JSON Patch 路径。
- 请求不能包含完整 Contract、ProductPlan 投影或平台派生字段。
- `workspaceRoot` 仍按现有受控工作区规则解析。
- 基于 Hash 的比较失败时返回冲突，不能自动把 Patch 套到新 Contract。
- Prompt 和描述等自由文本执行长度、敏感信息和安全语义校验。

## 8. AG-UI 与正式修订流程

### 8.1 协议选择

该能力属于正式产品配置变更，必须使用 AG-UI。为避免第二套修订系统，复用现有 `/application-page-planning/run`、Application Revision lifecycle、独立 planning thread、资源锁、TechnicalPlan draft、确认和 continuation。

在现有 action 集合中增加窄范围动作：

- `prepare_agent_settings_revision`
- `confirm_agent_settings_revision`
- `abandon_agent_settings_revision`

这些动作只负责 Agent Settings 定向修订，不允许客户端指定 Graph node、任意 artifact、待替换 run 或失效范围。

### 8.2 完整事件生命周期

每个动作继续发出：

1. Run Started。
2. User/Assistant Message。
3. `application-revision` 状态事件。
4. `revision-draft` 修改预览或结构化错误。
5. 完整 State Snapshot。
6. Run Finished。

优先扩展现有 `revision-draft` 公开投影，增加 Settings 的用户可读结构化 Diff；不为每一个表单字段创建自定义事件，也不在 AG-UI 事件中发送完整内部 JSON。

### 8.3 Preview 结果

修改预览至少包含：

```json
{
  "changeId": "chg_...",
  "agentId": "inventory_assistant",
  "basedOnContractHash": "sha256:old",
  "candidateContractHash": "sha256:new",
  "changedSections": ["prompt", "model"],
  "fieldDiffs": [
    {
      "field": "prompt.persona.tone",
      "label": "表达风格",
      "before": "克制",
      "after": "专业、简洁"
    }
  ],
  "impact": {
    "agentBuild": true,
    "toolAdapterBuild": false,
    "integrationTest": true,
    "launchEvidence": true,
    "unrelatedAgents": false
  },
  "warnings": []
}
```

`fieldDiffs` 是服务端从旧正式 Contract 和新编译 Contract 计算的公开摘要，不能相信客户端声明的 Diff。

## 9. 服务端重编译与原子保存

### 9.1 Prepare

服务端依次执行：

1. 读取当前 Application Lifecycle、ProductPlan 和 TechnicalPlan。
2. 校验当前应用允许正式修订且不存在冲突写事务。
3. 按 `agentId` 精确找到 ProductPlan Agent 和 Agent Contract。
4. 校验两个 based-on Hash。
5. 对 Settings Patch 执行字段级校验。
6. 解析当前 Model、Memory、Skill、Knowledge 和 Runtime Catalog。
7. 重新编译目标 Agent 的完整 Contract。
8. 对完整 TechnicalPlan 执行确定性校验。
9. 保持所有无关 Contract 和其他 TechnicalPlan 区域语义不变。
10. 生成 TechnicalPlan revision draft 和公开 Diff，等待确认。

Prepare 不覆盖正式 TechnicalPlan。

### 9.2 Confirm

确认时必须再次校验：

- `changeId` 和 lifecycle revision。
- 当前正式 TechnicalPlan SHA-256。
- 原 Contract Hash。
- Draft Hash。
- 当前 Catalog/Runtime capability revision。
- 当前用户仍持有写权限。

全部通过后，使用现有 TechnicalPlan 文档服务原子写入 Markdown 和 JSON。JSON 是内部状态，Markdown 是用户可读正式产物，两者必须来自同一次编译结果。

### 9.3 Abandon

放弃操作删除当前 revision draft、释放资源锁并回到原已确认 Contract。放弃不恢复历史版本，也不修改已确认 TechnicalPlan。

## 10. 影响范围与失效规则

| 修改分类 | 必须失效 | 不应失效 |
| --- | --- | --- |
| Prompt | 当前 Agent Build、单测、试聊、审查和验收证据 | 无关 Agent、页面和 Endpoint |
| Model | 当前 Agent Build/启动/试聊/测试证据 | 无关 Agent 与实体绑定 |
| Memory | 当前 Agent、Runtime 配置、启动和多轮测试证据 | 无关页面实现 |
| Tools | 当前 Agent、相关 Tool Adapter、相关集成测试和验收证据 | 未被绑定的 Endpoint、无关 Agent |
| Skills | 当前 Agent、Skill Loader、测试和审查证据 | 无关 Tool 与页面 |
| Knowledge | 当前 Agent、Retriever、权限测试、引用测试和验收证据 | 无关实体和 Agent |
| Context | 当前 Agent、多轮/长上下文测试和试聊证据 | 无关 API 实现 |

确认后：

- 更新目标 Agent `contractHash`。
- 废弃仍绑定旧 Contract Hash 的当前 Agent Build DAG、Build Run 和质量证据。
- 保留旧证据作为历史 Run 记录，但不能继续显示为当前版本通过。
- 重新投影 Agent 状态为“配置已更新，待重新 Build”。
- 不直接自动执行 Build；用户仍需进入现有 Build DAG 生成和确认流程。
- 如果现有某项证据只能绑定整个 TechnicalPlan Hash，实施前必须先明确其真实失效范围，不能用前端状态假装实现局部保留。

## 11. 并发、幂等与恢复

- 同一应用同时只能有一个正式写修订。
- 同一个 `changeId + draftHash` 的 Confirm 必须幂等。
- 重复 Prepare 相同 Patch 可以返回同一候选摘要，不能重复创建不同正式写事务。
- App 重启后，只恢复服务端已经生成的 revision draft；纯前端未提交表单不作为正式恢复事实。
- 旧页面、旧会话和旧卡片提交的 based-on Hash 不一致时返回 `conflict`。
- 冲突后用户必须重新加载最新配置，再主动重新编辑；不自动三方合并 Prompt、Tool 或 Memory 配置。
- 同一 Agent 的 Build 正在执行时，配置修订必须通过现有资源锁阻止或先安全结束，不能一边 Build 旧 Contract 一边覆盖正式 Contract。
- 本地表单编辑不获取资源锁；Prepare 创建正式 TechnicalPlan revision draft 前读取 Agent 资源锁。任务仍在运行时只允许打开原任务并由用户停止；任务处于等待确认、失败或已停止状态时，用户可二次确认后复用现有 AG-UI `planControlAction=end` 显式结束任务并释放锁。
- 不允许为了修改配置自动中断正在运行的 Workflow。资源锁指向不存在的 active execution 时属于 lifecycle 不变量破坏，服务端可在同一次 CAS 中只清理该 Agent 的孤立锁。

## 12. 错误与空状态

界面至少区分：

- 正式 Contract 缺失：提示返回规划阶段修复，不显示空白表单。
- ProductPlan Hash 不一致：提示 Contract 已失效，禁止编辑和 Build。
- Runtime 能力未实现：显示能力说明，不渲染假开关。
- Catalog 不可用：保留当前只读值，禁止新增绑定。
- 字段校验失败：定位到具体分组和字段，保留用户输入。
- Hash 冲突：提示配置已被更新，提供重新加载。
- Preview 生成失败：正式 Contract 不变，允许修正后重试。
- Confirm 写入失败：不展示成功状态；重新读取服务端状态后决定重试或冲突。
- 保存成功但开发投影刷新失败：提示重新加载，不使用本地候选冒充正式值。

## 13. 安全与隐私

1. Renderer 永远不接收平台安全 Prompt 原文。
2. System Prompt 只在有权限的详情和修订界面展示，不进入默认日志或无关会话摘要。
3. Contract 和 Patch 不保存模型 Key、数据库密码、连接串、Token、Bucket 物理地址或宿主绝对路径。
4. Connection 只使用平台登记的 `connectionRef`。
5. Tool Endpoint、权限、access mode 和 approval policy 由服务端重算。
6. Skill 和 Knowledge 内容视为不可信输入，不能扩大工具、网络、文件或审批权限。
7. AG-UI 错误事件只返回字段和用户可操作说明，不返回密钥、内部 Prompt 或完整堆栈。
8. Prompt 预览按普通文本渲染，不执行 HTML。

## 14. 前端组件边界

建议把当前 `AgentSettingsView` 拆为独立小组件，避免在详情页继续累积条件分支：

```text
AgentSettingsEditor/
├── index.tsx                       # 只读 / 编辑 / 预览状态编排
├── AgentSettingsSummary.tsx        # 七段只读摘要
├── AgentSettingsEditForm.tsx       # 表单容器、dirty 和跨段校验
├── AgentSettingsRevisionPreview.tsx# 字段 Diff 与影响范围
├── sections/
│   ├── PromptSettingsSection.tsx
│   ├── ModelSettingsSection.tsx
│   ├── MemorySettingsSection.tsx
│   ├── ToolSettingsSection.tsx
│   ├── SkillSettingsSection.tsx
│   ├── KnowledgeSettingsSection.tsx
│   └── ContextSettingsSection.tsx
├── agentSettingsForm.ts            # 表单值与严格 Patch 的转换
└── index.less                       # 亮色 / 暗色主题样式
```

规则：

- 继续使用 React 18、Ant Design v4 和现有 `--wb-*` 主题变量。
- 不引入 JSON Editor 或新的 UI 框架。
- 大型 Tool/Knowledge 选择使用抽屉或弹窗，主页面保留摘要。
- 所有加载、空、禁用、冲突和错误状态同时支持亮暗主题。
- 键盘操作、焦点回退、表单错误关联和长 Prompt 阅读必须可用。

## 15. 后端职责边界

建议保持职责拆分：

| 层 | 职责 |
| --- | --- |
| AG-UI Protocol Adapter | 严格解析 action、输出完整生命周期和公开投影 |
| Application Revision Service | changeId、资源锁、CAS、draft、确认、放弃和 continuation |
| Agent Settings DTO | 七段可编辑 Patch 的严格类型和字段限制 |
| Agent Contract Compiler | 从 ProductPlan、API Contract、Catalog 和 Patch 编译完整 Contract |
| Contract Validator | ProductPlan 闭合、能力支持、Endpoint、权限、安全和 Runtime 校验 |
| Plan Document Service | 同一次编译结果原子写 TechnicalPlan Markdown + JSON |
| Invalidation Service | 按目标 Agent 和 Contract Hash 废弃 Build/测试/审查/验收证据 |

不得把编译、权限判定或失效范围放到 Renderer，也不得在 Electron main 中新增直接写 TechnicalPlan 的 IPC。

## 16. 与现有流程的关系

- 规划阶段：继续负责首次生成和确认完整 TechnicalPlan。
- 开发阶段：允许以可视化表单发起目标 Agent 的 TechnicalPlan 正式修订。
- Build：确认新 Contract 后仍进入现有 `/workflow/run` readiness、DAG 生成和 DAG 确认。
- Testing、Review、Acceptance：继续复用现有阶段和证据，不新增 Agent 专属平行阶段。
- 试聊：只能使用已 Build 的候选代码并通过 Java Gateway；不能把未确认的表单值注入当前 Runtime。
- 页面、Endpoint 和实体：本功能只读取其正式事实，不修改它们的 JSON 设计。

## 17. 分阶段实施建议

### 第一批：只读可视化

- 用七段用户可读摘要替换原始 JSON。
- 展示字段来源、Runtime 支持状态和只读原因。
- 不增加保存动作。

### 第二批：Prompt 与生成参数正式修订

- 增加 Prompt 和 Temperature 表单。
- 接入 Prepare、Diff、Confirm、Abandon。
- 完成 CAS、TechnicalPlan 重编译和目标 Agent 失效。
- 这是第一条端到端可交付的编辑闭环。

### 第三批：Tools

- 增加 Endpoint 候选、Capability 覆盖、读写审批和 EntitySourceBinding readiness。
- 补齐 Tool Adapter 的定向失效和 Build 测试。

### 第四批：能力驱动配置

- Runtime 真正实现后再开放 MySQL/Long-term、Skills、Knowledge 和 Compression。
- 每开放一个开关，必须同时具备 Catalog、Compiler、Runtime Adapter、权限、启动和测试证据。

### 后续：独立候选发布

只有明确需要 active 配置继续服务、candidate 独立 Build/试聊、验收后再激活时，才引入 candidate/active 发布模型。该能力不应成为第一版可视化编辑的前置条件。

## 18. 验证清单

### Contract 与服务端

- 只允许七段白名单字段进入 Patch。
- 平台派生字段、未知字段和凭据被拒绝。
- ProductPlan、TechnicalPlan 或 Contract Hash 冲突被拒绝。
- Preview 不修改正式 TechnicalPlan。
- Confirm 同步更新 Markdown、JSON 和 Contract Hash。
- Abandon 保持正式文件不变。
- 无关 Agent Contract 保持语义和顺序不变。
- 未实现 Runtime Adapter 不能被启用。

### 前端

- 默认页面不显示原始 JSON。
- 只读摘要、编辑表单和 Diff 均可理解。
- 字段错误定位到对应分组。
- 未实现能力没有假开关。
- 未提交修改离开时有提示。
- 冲突后不会覆盖最新配置。
- 亮色和暗色主题均可读。

### 工作流

- 所有动作使用 AG-UI 完整生命周期。
- 正式 Artifact 更新前存在明确确认。
- Confirm 后目标 Agent 进入待重新 Build。
- 旧 Contract Hash 的 DAG、执行或 continuation 被拒绝。
- 无关页面、Endpoint、实体和 Agent 不被错误失效。

## 19. 审核决策

第一批实施采用以下已确认产品决策：

1. 第一条编辑闭环是否只开放 Prompt 和 Temperature，其他分组先完成只读可视化。
2. “生成修改预览”是否保留在当前 Agent 详情中完成，还是跳转到独立规划会话展示确认卡；底层都使用独立 planning thread。
3. 修改确认后是否只显示“重新 Build”按钮，还是自动打开现有 DAG 生成入口；本设计默认不自动执行 Build。
4. 单 Agent 模型 override 是否属于近期能力；若否，模型选择继续只读跟随项目默认模型。
5. System Prompt 是否需要显示字符数和敏感内容检测提示；本设计建议显示但不自动改写用户文本。

其中第一批只开放 Prompt 和 Temperature，修改预览留在当前 Agent 详情中，确认后不自动 Build；模型继续跟随项目默认配置。其余问题随对应 Runtime 能力切片再次确认。

## 20. 最终设计结论

1. 开发阶段 Agent Settings 使用七段结构化可视化，不向普通用户开放原始 JSON 编辑。
2. ProductPlan 事实、平台安全字段和派生字段保持只读。
3. 用户只提交严格类型化的 Settings Patch，服务端重新编译完整 Contract。
4. “生成修改预览”不修改正式文件；“确认并应用”才原子更新 TechnicalPlan Markdown 和 JSON。
5. 第一版复用正式 TechnicalPlan revision，不新增独立 Agent Settings Artifact 或 candidate/active 配置库。
6. 配置确认后只使目标 Agent 的相关 Build 和质量证据失效，并重新进入现有 Build 主流程。
7. 所有产品动作使用 AG-UI、显式确认、CAS/hash、资源锁和完整生命周期。
8. 当前 Runtime 未实现的 Memory、Skills、Knowledge 和 Compression 能力继续明确关闭，直到实现和验证完整闭环。
