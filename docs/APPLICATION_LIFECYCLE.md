# 应用生命周期状态文件

## 参考架构映射

- `learn-coding-agent`：采用“收集上下文、执行、验证”的紧凑循环，以及关键恢复输入同步落盘、HITL 可恢复、任务图单独持久化的边界。其当前公开提交只包含研究文档，没有 README 所列 `src/*` 源码，因此原子写入和稳定交互 ID 是 XCodeAgent 的明确自有设计。
- OpenCode：采用稳定 session/run 引用、可恢复持久化状态和事件与业务投影分责；`activeThreadId`、`activeRunId` 只引用技术执行，不成为业务阶段真相。
- Deep Agents：沿用外层确定性状态机和人机确认门禁；LangGraph checkpoint 继续保存技术执行断点，不能替代业务生命周期文件。
- 128k 上下文：状态文件只保存阶段、revision、引用、待交互和短错误摘要。正式 Markdown/JSON、Build DAG、测试日志和会话历史按需从各自文件加载，不复制进生命周期或模型上下文。

## 权威边界

`.xcodeagent/application-lifecycle.json` 是用户可见、跨会话业务生命周期与待处理交互的唯一权威来源。`checkpoints.sqlite` 继续负责 LangGraph 技术断点；RequirementSpec 和 ProjectPlan JSON 继续负责文档内容及确认状态；Build DAG、ExecutionRun 和 TestReport 继续负责执行和测试事实。

## Schema 与一致性

当前 `schemaVersion` 为 `1.0.0`。顶层保存 application/project 标识、UTC `updatedAt`、单调递增 `revision`、`lifecycle`、活动 thread/run 引用、`pendingInteraction`、错误和扩展容器。待交互以稳定 `id`、辨识 `type`、`basedOnRevision`、小型 payload、产物引用和创建/提交时间表示，不保存正式文档正文。

写入使用同目录临时文件、文件 fsync、原子替换和目录 fsync。未知版本或损坏文件会显式拒绝读取，不根据旧索引、localStorage、checkpoint 或正式文档反向生成 lifecycle。所有状态文件和动作输入先经过 Pydantic 校验。

## 新建应用状态机

```text
collecting_requirement
  -> analyzing_requirement
  -> awaiting_requirement_clarification -> analyzing_requirement
  -> generating_requirement_spec
  -> awaiting_requirement_confirmation -> generating_requirement_spec
  -> generating_project_plan
  -> awaiting_project_plan_confirmation -> generating_project_plan
  -> generating_application_template_files
  -> application_template_generation_failed -> generating_application_template_files
  -> ready_for_workbench
```

同一阶段允许更新运行状态或活动 run 引用；跨阶段只允许图中边。重复交互提交使用 `id + basedOnRevision` 校验，已提交的同一交互幂等返回，过期提交显式冲突。

早期 v1 实现曾把 lifecycle 自身 payload 中的 `ask_user_question` 错写成需求文档确认。读取该精确不一致组合时会通过原子 CAS 自愈为需求澄清；真正的 `requirement_spec_confirmation` 不受影响，也不会读取正式产物或 checkpoint 反推阶段。

新应用创建时必须通过 AG-UI `applicationLifecycle.action = create` 显式创建状态文件。客户端重启后只使用 `get` 读取已有状态；缺失状态不会触发旧数据兼容或阶段推断。ProjectPlan 确认后进入“生成应用模板文件”阶段，前端完成真实文件写入后通过 `complete_template_generation` 提交结果。
