# 应用生命周期状态文件

## 设计边界

- 生命周期文件只保存当前阶段、revision、引用、待交互和短错误摘要；正式 Markdown/JSON、Build DAG、测试日志和会话记录按需从各自文件加载，不复制进生命周期或模型上下文。

## 权威边界

`.xcodeagent/application-lifecycle.json` 是用户可见、跨会话应用初始化、工作台执行和资源锁的持久化权威来源。应用索引中的 `planningConfirmedAt` 是“初始化已完成、允许永久进入工作台”的不可逆准入事实。`checkpoints.sqlite` 继续负责 LangGraph 技术断点；RequirementSpec、ProductPlan、UiDesign 和 TechnicalPlan 各自负责正式内容与确认状态；Build DAG、ExecutionRun 和 TestReport 继续负责执行和测试事实。

生命周期文件只承担冷启动、断线重连和显式校准，不作为渲染进程的实时轮询源。后端每次原子写入成功后，必须先通过主 Workflow AG-UI 流发送独立的 `application-lifecycle` 自定义事件，再继续投影对应节点或控制动作。工作台顶层 application store 按应用标识与单调 `revision` 合并冷启动读取、重连校准和实时事件；页面控制栏、应用大纲及 API 大纲只消费这一个 store。较旧的文件读取结果不得覆盖更新的实时 revision。

## Schema 与一致性

当前 `schemaVersion` 为 `1.3.0`。顶层只保存 application 标识、UTC `updatedAt`、单调递增 `revision`、`initialization`、活动 run 引用、按 runId 索引的 `activeExecutions`、按稳定业务标识索引的 `resourceLocks`、错误和扩展容器。应用标识同时作为当前工作区业务身份，不再重复保存同值的 project 标识。`initialization.threadId` 仅在初始化期间定位同一 LangGraph checkpoint，进入 `ready_for_workbench` 时清空。工作台待交互与错误只嵌入各自 execution，避免一个页面的确认覆盖另一个页面。该版本不兼容旧 schema，未知版本或旧字段会被严格拒绝。

写入使用同目录临时文件、文件 fsync、原子替换和目录 fsync。未知版本或损坏文件会显式拒绝读取，不根据旧索引、localStorage、checkpoint 或正式文档反向生成状态文件。所有状态文件和动作输入先经过 Pydantic 校验。

## 新建应用状态机

```text
collecting_requirement
  -> analyzing_requirement
  -> awaiting_requirement_clarification -> analyzing_requirement
  -> awaiting_requirement_confirmation
       ├─ revise -> analyzing_requirement
       └─ confirm -> generating_requirement_spec
  -> generating_product_plan
  -> awaiting_product_plan_confirmation -> generating_product_plan
  -> generating_ui_designs
  -> awaiting_ui_design_confirmation -> generating_ui_designs
  -> generating_technical_plan
  -> awaiting_technical_plan_confirmation -> generating_technical_plan
  -> generating_application_template_files
       ├─ success -> ready_for_workbench
       └─ failure -> application_template_generation_failed（终止）
```

模板生成只由用户确认 TechnicalPlan 后的确认回调启动。失败、应用重启、再次打开和进入工作台都不会重新启动模板生成；任何新一轮生成都必须重新完成规划并确认 TechnicalPlan。

## 全应用生命周期与计划执行模式

创建流程与工作台执行是互不覆盖的两套状态。创建流程完成后，应用索引持久化
`planningConfirmedAt` 并永久放行工作台；顶层 `initialization.stage/status` 固定保持
`ready_for_workbench/completed`，用于创建状态校验；
页面设计、代码生成、等待确认、失败、停止和验收记录在 `activeExecutions`，
不得反向把初始化改回待确认或运行中。旧 schema 和旧阶段不做迁移或兼容，
读取时直接拒绝。

主 Workflow 获取范围登记时在 `activeExecutions` 中创建运行，并由后端从正式 ProjectPlan 计算资源集合。页面主目标、导航关联页、直接使用的 API 契约及其数据源，以及共享这些 API/数据源的其他页面和契约会原子写入 `resourceLocks`。当前阶段 `resourceLocks` 仅作为可观测、可持久化的资源元数据，不参与启动门禁：同工作区、同页面、共享 API/数据源或应用级范围均不会因为已有登记被拒绝；同一资源键以最近一次运行记录为准。等待授权、修复确认、验收、失败和停止仍保留登记，只有 `finalize_project` 成功或用户明确“结束计划”才清理该 run 当前拥有的登记。已停止或失败的执行继续运行时，前端只提交旧 runId 作为同一执行的恢复令牌；后端仍验证同一 thread、scope 和 target，并在一次写入中转移旧 run 当前可见的资源记录。结构化测试阶段确认是唯一可跨 thread 转交 execution 的恢复动作，用于从开发会话进入空白测试会话；scope 与 target 校验不放宽。该令牌不参与 Graph 状态重建。

当前实现只关闭业务资源集合的互斥执法，不放宽文件、命令、敏感操作或 Agent 工具权限。`resourceLocks` 只保存稳定资源键和紧凑 owner 元数据，恢复业务互斥前应重新引入显式策略开关和冲突 UX，而不是让持久化字段隐式阻断。

资源登记成功产生的 lifecycle revision 必须立即到达 application store，因此当前页面控制栏在同一 React 提交中更新，不等待 Graph 首节点完成，也不通过重新读取文件猜测状态。页面与 API 目录暂不展示资源占用标识，后续统一设计只读取现有 `resourceLocks` 投影。

底部计划执行模式只按当前页面或当前 Workflow 身份读取 execution，并由 `execution.status + execution.pendingInteraction.type` 派生；`resourceLocks` 不参与输入或操作门禁，关联页面不会因资源登记进入只读状态。控制栏开放停止/结束、查看计划、Agent 授权、RepairPlanner 范围确认、失败后的重试/调整/结束，以及页面预览验收。最终 `page_acceptance=accepted` 是结构化动作；普通文本和澄清回答不能冒充验收通过。

Build 完成后，工作台 execution 会以 `pendingInteraction.type=test_phase_confirmation` 等待用户确认。该交互的 payload 携带 `mode=test_phase_confirmation` 和稳定 `testTarget`；前端创建新的测试会话/thread，并提交结构化 `clarificationAnswers.test_phase_confirmation={action:"confirm"}`，以 `resumeExecutionRunId` 替换原 execution。确认节点完成后 execution 的 phase 立即更新为 `integration_test`，顶部阶段与实际测试同步。旧 schema 不做迁移或兼容读取。

示意快照（省略无关字段）：

```json
{
  "schemaVersion": "1.3.0",
  "revision": 27,
  "initialization": {
    "stage": "ready_for_workbench",
    "status": "completed",
    "threadId": null
  },
  "activeExecutions": {
    "run-orders-7": {
      "scope": "page",
      "targetId": "orders",
      "pageId": "orders",
      "threadId": "thread-orders",
      "runId": "run-orders-7",
      "phase": "build",
      "status": "awaiting_user",
      "resourceKeys": [
        "page:orders",
        "page:order-detail",
        "api_contract:orders-api",
        "data_source:commerce-db"
      ],
      "pendingInteraction": {
        "id": "interaction-id",
        "type": "test_phase_confirmation",
        "basedOnRevision": 27,
        "payload": {
          "mode": "test_phase_confirmation",
          "testTarget": { "type": "page", "id": "orders", "label": "订单页" }
        },
        "artifactRefs": [],
        "createdAt": "2026-07-23T08:00:00Z"
      },
      "startedAt": "2026-07-23T07:50:00Z",
      "updatedAt": "2026-07-23T08:00:00Z"
    }
  },
  "resourceLocks": {
    "application": null,
    "pages": {
      "orders": {
        "runId": "run-orders-7",
        "ownerPageId": "orders",
        "mode": "exclusive",
        "role": "primary",
        "reason": "primary_target",
        "acquiredAt": "2026-07-23T07:50:00Z"
      },
      "order-detail": {
        "runId": "run-orders-7",
        "ownerPageId": "orders",
        "mode": "exclusive",
        "role": "dependency",
        "reason": "plan_dependency",
        "acquiredAt": "2026-07-23T07:50:00Z"
      }
    },
    "apiContracts": {
      "orders-api": {
        "runId": "run-orders-7",
        "ownerPageId": "orders",
        "mode": "exclusive",
        "role": "dependency",
        "reason": "plan_dependency",
        "acquiredAt": "2026-07-23T07:50:00Z"
      }
    },
    "dataSources": {
      "commerce-db": {
        "runId": "run-orders-7",
        "ownerPageId": "orders",
        "mode": "exclusive",
        "role": "dependency",
        "reason": "plan_dependency",
        "acquiredAt": "2026-07-23T07:50:00Z"
      }
    }
  }
}
```

RepairPlanner 若请求扩大业务资源范围，必须在确认载荷中给出 `requestedResources`，不能从文件路径猜测页面或 API。拒绝时不新增登记；批准时把新增资源与旧 run 当前记录一起写入，新资源与已有运行重叠也不阻断恢复。

参考架构映射：OpenCode 把 session 的 busy/idle 状态独立投影，不覆盖项目是否可打开的稳定事实；learn-coding-agent 的关键消息持久化与独立进度边界用于分离初始化门禁和执行恢复；Deep Agents 的 HITL 门禁只暂停对应执行。XCodeAgent 因此把初始化完成事实固定在顶层 `initialization`，把产品级授权、RepairPlanner 确认和交付验收限定在 execution 投影中，以支持桌面端重启恢复；它不会把完整事件流、DAG 或工具结果复制进该文件，继续满足 128k 上下文预算。

同一初始化阶段允许更新运行状态或活动 run 引用；跨阶段只允许图中边。初始化确认和澄清由对应 thread 的 Graph checkpoint 与历史 Workflow 快照恢复，不在状态文件根节点重复保存 pending interaction。工作台 execution 的待处理交互仍使用 `id + basedOnRevision` 校验，过期提交显式冲突。

新应用创建时必须通过 AG-UI `applicationLifecycle.action = create` 显式创建状态文件。客户端重启后只使用 `get` 读取当前状态；缺失状态不会触发历史数据推断。TechnicalPlan 经开发确认后进入“生成应用模板文件”阶段，前端完成真实文件写入后通过 `complete_template_generation` 提交结果；后端复核 RequirementSpec、ProductPlan、TechnicalPlan 已确认，且 UiDesign 已确认或明确跳过后，才允许进入工作台。
