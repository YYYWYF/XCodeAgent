# 开发预览服务状态与诊断修复

## 产品行为

预览工具栏最左侧的“服务状态”替代装饰性的红黄绿圆点。侧边抽屉显示前后端服务状态、实际服务端口、失败摘要，以及“前端 / 后端”日志 Tab。状态文字和主题语义色同时表达运行中、启动中、失败、未启动及无需启动。抽屉宽度为 520px，最大不超过预览区域。

“重启服务”先停止生成项目的标准前后端服务，再按后端→前端顺序启动，不修改代码。页面刷新与服务重启是独立动作。成功后刷新当前页面路径；失败时保留日志。缺少后端工程时后端显示无需启动。进程退出或当前就绪探测失败不能继续显示历史成功。

只有当前启动失败记录允许“诊断并修复”；运行中的页面业务错误不属于此入口。点击后创建开发阶段历史会话“诊断并修复”，保持右侧预览，后续计划确认、调整、停止、代码变更和结果均属于该会话。打开已有修复会话通过其 thread 读取当前修复状态，不借用原开发或测试会话。

## AG-UI 契约

`/preview-runtime/run` 使用 `forwardedProps.previewRuntime`，字段包括 `workspace`、`action` 和动作所需的 `attemptId`、`planId`、`feedback`。动作是 `get/watch/start/restart/stop/diagnose/confirm/revise/cancel/leave`。所有字段在 Pydantic 边界校验；不接受日志文件路径。

所有响应均使用标准 AG-UI 生命周期、助手消息、`preview-runtime` CustomEvent、`previewRuntime` StateSnapshot 和 RunFinished，包括业务失败。`runtime.frontend` 与 `runtime.backend` 各自包含状态、可选 `port`、兼容保留的 URL 及失败摘要；操作失败含结构化 error。状态包含 runtime、blockedBy、当前会话 repair。`/health` 发布该能力。旧预览 REST 启停路由已移除；renderer 和 Electron 退出清理均通过 AG-UI 客户端调用。

打开抽屉时前端先通过 `get` 读取当前快照，再进入 `watch` 增量订阅，避免长连接首包延迟让未启动服务显示为不可操作。初始快照尚未返回且没有本地占用时仍可提交重启，最终由服务端原子维护栅栏校验是否允许执行。`watch` 在一次有界订阅内仅在状态或日志变化时发布快照，前端在抽屉打开期间续订；关闭抽屉中止读取。服务端维护任务不随抽屉订阅结束而取消。日志只读取标准运行目录的白名单文件、有界尾部，显示截断标记，并脱敏连接凭据、授权令牌和私钥。安装、构建、启动及 stdout/stderr 都有明确标识。

## 确认与修复

RepairPlanner 根据当前启动失败及脱敏日志生成精确文件范围，SmallTask 执行已确认计划。RepairPlanner 使用工作区虚拟绝对路径（如 `/frontend/src/index.tsx`）；预览修复在安全校验后统一转换为工作区相对路径，并继续拒绝宿主机绝对路径、路径穿越、通配符、敏感文件和越界符号链接。修复计划中的策略、原因、任务标题、任务描述等用户可见自然语言统一要求使用简体中文；路径、代码标识符和必要的原始错误片段可保留原样。修复文档保存在当前运行目录的会话摘要命名文件中，Markdown 是用户可读计划，JSON 是内部状态。计划 ID、启动尝试 ID 和涉及文件内容摘要绑定本次确认；计划或代码被编辑时需要重新诊断和确认。

每次实际派发前计入修复预算，最多 3 轮；异常也消耗本轮额度。重新生成计划不代表确认。执行后运行受影响层构建与静态检查，再重启服务。失败重新诊断并等待下一轮确认；无进展、额度耗尽、环境不可自动处理或越界时停止。需要正式产品或合同调整时提供转入正式修订对话的入口。

停止操作以安全边界收口：renderer 先中止当前 AG-UI 读取并立即收起本地会话交互，服务端在后台停止派发后续工具及后续验证/重启，等待当前同步操作退出后才释放维护占用。停止保留已有修改。此流程不调用集成测试 Graph 节点、不生成测试通过结论、不增加开发完成计数，也不推进测试或验收阶段。

返回欢迎页会调用 `leave`：它先同步释放当前应用的维护占用、停止等待确认的修复，再立即返回；前后端服务关闭在后台完成，并在旧维护线程退出后再次确认停止。Renderer 不等待关闭过程即可显示欢迎页和打开其他应用。生命周期的只读 `get` 与入口 `workspace_attach` 不参与预览维护互斥，预览任务不能再阻塞工作台导航。普通规划和开发工作流仍按各自后台运行规则处理。

## 互斥与运行事实

工作区级维护栅栏与工作流运行登记、workspace lease 和 lifecycle 执行登记共享原子锁。任意阶段的执行、停止中或待确认任务都会阻止维护；维护反向阻止其他任务启动或恢复。等待修复计划确认仍占用工作区，自身确认/修订/停止允许继续。不同应用互不阻塞。

当前维护身份保存在 `.xcodeagent/runtime/preview-maintenance.json`，终态清空；它只表达维护占用，不是应用配置或阶段完成来源。标准预览当前尝试记录在 `.xcodeagent/runtime/launch/preview-runtime.json`。生命周期和独立对话的待确认投影共同用于阻塞提示。维护任务登记到现有取消注册表，删除工作区前必须等待同步工作实际退出。

## 验证

- Backend：`python -m unittest tests.test_preview_runtime tests.test_project_launcher tests.test_ag_ui_action_stream tests.test_application_lifecycle tests.test_application_lifecycle_protocol tests.test_workspace_run_lease`。
- Frontend：`pnpm test:preview-runtime`、`pnpm test:stage-sessions`、`pnpm test:preview-inspector`、`pnpm test:workflow-preview`、`pnpm lint`、`pnpm build`（包含 typecheck）。
- 运行时：检查 `/health`；在实际 Electron 中验证抽屉、明暗主题、日志滚动跟随、窄窗口和当前页面重启刷新。浏览器中的 Vite 页面不能替代此项。
