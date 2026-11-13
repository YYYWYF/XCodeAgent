# za38 Agent UI 与 Agent Runtime 适配问题记录

记录日期为 2026-11-13。当前状态为部分修复，其余问题仍待处理；各项状态以对应章节为准。

本文记录当前 za38 前端接入与 Python Agent Runtime 的协议差异、能力边界和后续验收条件，供后续修复使用。文中的修复方向及顺序为建议，具体方案在实施时确定。

## 1. 审查基线

| 对象 | 本次读取范围 |
| --- | --- |
| Studio 仓库 | `dev_agent_integration_1103`，HEAD 为 `8d7865ab`，工作区同时存在本地未提交修改 |
| za38 发布包 | 六个 `@za38.03/agent-ui-*` 包，版本均为 `0.0.1` |
| 包来源清单 | `vendor/agent-ui/manifest.json`，来源提交为 `71ebfaf975a6079102408e4f9b584da77f7205c7` |
| 实际工具安装 | `tools/agent-ui/node_modules` |
| 前端生成种子 | `tools/agent-ui-design/` |
| 前端接线与门禁 | `Backend/app/services/agent_ui_host_integration.py`、`agent_ui_connection_requirements.py`、`agent_ui_plain_text_preview.py` |
| 后端执行能力 | `Backend/app/domain/runtime_endpoint_contract.py`、`docs/AGENT_CAPABILITY_RUNTIME.md` |
| 本机后端模板源码 | `/Users/frank/IdeaProjects/springboot-template/template-source/code/agent-runtime/base/src/app/` |

当前集成只开放匿名、单 Agent、无业务输入和结构化结果、无 Tool/技能/知识及用户交互的纯文本会话连接。后端合同已支持更广的任务、结果、交互和多 Agent 能力。

本次没有对用户当前生成的应用执行真实对话、审批或多 Agent 联调。代码索引记载了部分工程的纯文本联调与请求格式修复，其结论需要按实际工程和模板交付版本核对。本机模板源码的差异仍需检查是否进入当前模板发布包及目标工程。

## 2. 当前能力对照

| 能力 | 后端合同与实现 | 当前 za38 集成状态 |
| --- | --- | --- |
| 纯文本多轮对话 | 支持 AG-UI/SSE、线程及可信历史 | 已开放受限匿名场景，仍需处理下文请求与结果差异 |
| 同一 Agent 的独立页和浮窗 | 支持页面绑定 | 共享 Host 常驻复用同一会话，页面及浮窗控制已有接线 |
| 多 Agent | 正式合同、注册和开发任务支持多个 Agent | 设计生成及正式共享 Host 限定唯一 Agent |
| 任务与结构化输入输出 | 支持 `task`、`inputSchema`、`resultSchema` | 正式连接仍受纯文本门禁限制 |
| 页面业务上下文 | 支持 `pageId`、`actionId`、`contextItems` | 当前 Host 尚未完成业务映射 |
| 澄清、确认与审批 | 支持 pending interaction 和显式续接 | 待交互展示及 SDK 能力存在，Python 续接合同尚未贯通 |
| Tool、技能和知识 | Agent 合同允许按配置启用 | 当前真实纯文本接线要求这些模块关闭 |
| 登录与授权 | 当前 Direct Runtime 限定匿名模式 | 本次真实连接范围要求两项配置明确关闭 |

## 3. 问题与修复状态

### 01. 请求版本与 state 镜像不一致

状态为本机模板源码已发现差异，目标工程及已发布模板待核对。建议优先处理。

- za38 使用的 `@ag-ui/client@1.0.3` 会按协议版本构造 `protocolVersion`，并把当前 state 镜像放入后续请求。
- 本机模板 `interaction/schemas.py::parse_run_request` 的顶层白名单缺少 `protocolVersion`，且要求 `state` 为 `{}`。
- 模板 `server/agui.py` 又会发布包含 `agentRuntime` 的 `STATE_SNAPSHOT`，后续镜像可能因此触发拒绝。
- 可能表现为请求返回 `undeclared_input`，或首轮正常后下一轮失败。

修复涉及包请求合同、本机模板解析器、模板发布产物和实际生成工程。需要使用当前明确支持的协议校验请求，并明确公开 state 镜像的校验和处理方式。可信图状态、消息历史及权限继续由服务端控制。

验收条件为实际成品包发起首轮及第二轮请求均通过校验，同时非法协议版本、伪造状态和扩权字段按约定拒绝。已有工程的修复记录需要与当前模板产物交叉确认。

### 02. 结果解析器漏掉后端的 result 字段

状态为生成种子与测试夹具已修复，实际生成工程及真实运行验收待确认。

- 本机模板 `server/agui.py::_result_payload` 返回 `schemaVersion`、`agentId`、`threadId`、`runId`、`status`、`pendingInteraction` 和 `result`；生产者无需改动。
- `tools/agent-ui-design/runtimeResults.ts::decodeRuntimeResult` 已将 `result` 纳入成功及待交互回执的必填字段，接受 `null` 及 JSON 业务结果，继续拒绝缺字段、多余字段与错误身份。
- 业务结果的正式 Schema 校验仍由后端负责；当前观察器仅提取完成/待交互状态，不保存业务正文，也不新增结构化结果展示或页面映射。
- `tools/agent-ui-consumer/tests/runtimeResults.test.mjs` 已使用含 `result: null` 的当前回执夹具，并增加 JSON 结果、缺失字段和完成/待交互状态交付用例；原业务失败、错误身份及未知状态用例保留。
- 生成器从该种子装配共享 Host，转入工具将其作为正式设计文件处理。已有设计和生成工程不会被自动改写，须沿原生成、确认及转入流程更新。

本项修复不解决下节公开 Run 身份缺失；真实结果状态仍受该独立问题限制。验证命令和结果见本次任务回执。

### 03. za38 公开运行快照缺少 run.id

状态为已安装包实现已确认。建议与结果解析一起处理。

- 已安装包 `@za38.03/agent-ui-runtime/dist/compatibility/conversation-runtime-bridge.js::projectSnapshot` 只投影 `run.status` 和错误信息，没有填入 `run.id`。
- `runtimeResults.ts::observeRuntimeResults` 使用 `snapshot.runtime.run.id` 核验当前结果归属。
- 运行 ID 缺失时，观察器会排除结果候选，并可能发布 `unknown / run_identity_unavailable`。
- 后端事件里的 `runId` 已存在，但当前公开快照无法与它完成归属核验。

修复需要在 za38 包维护端贯通实际发送 Run 的身份，或使用包提供的等价可信关联能力，再更新本仓库的成品包和来源记录。直接编辑 `node_modules` 会在下一次安装时丢失，应将变更交付为新的成品包。

验收条件为同一 Run 的请求、公开快照、结果和结束事件身份一致。新请求、切线程、取消以及迟到旧事件都需要覆盖，避免结果污染下一次运行。

### 04. 任务、页面上下文和发送准入尚未贯通

状态为当前接入边界已确认。建议在基础协议和运行身份对齐后处理。

- 后端接受 `forwardedProps.input` 作为业务输入，并按正式 `inputSchema` 校验。
- 页面上下文使用 `forwardedProps.pageId/actionId/contextItems`。
- za38 提供 `getRunContext`，当前生成种子保留了这个参数，但正式 Provider 没有按后端业务合同装配它。
- 通过 AG-UI `context` 发送业务上下文会与本机模板要求 `context=[]` 的当前校验冲突，需要明确字段映射。
- `agent_ui_plain_text_preview.py` 会限制任务、业务输入、结构化结果和实际业务上下文的连接。Tool、技能、知识开启时同样不满足当前纯文本条件。
- `agent_ui_connection_requirements.py` 已记录发送准入缺口。包内 `ConversationRuntimeProvider.js` 对 `getRunContext` 报错进行展示，但该错误本身不会阻止底层请求发送。

修复需要定义经过正式确认的输入和上下文映射，并让所有实际发送与恢复入口使用同一准入判断。扩展能力后同步现有连接门禁、构建合同及验收，而不是仅删除限制判断。

验收至少覆盖任务提交、必填输入、结构化结果、页面切换、上下文读取失败和缺少业务参数。准入失败时应在发送前返回明确反馈。各模块启用后的运行证据按对应能力补齐。

### 05. 审批与中断续接格式不一致

状态为当前合同及集成缺口已确认。建议在可信 Run 身份贯通后处理。

- Python 当前通过 `agent_runtime_result.v1` 的 `pendingInteraction` 表达澄清、确认或审批等待。
- 续接请求使用 `forwardedProps.interactionResponse = { interactionId, value }`，只读查询使用 `forwardedProps.inspectInteraction`。
- za38 包具有原生 interrupt 响应及 command decision 能力，其路径包含 SDK 的 interrupt resume 和 `forwardedProps.command`。
- 本机后端模板当前白名单没有接受 `command` 或顶层 `resume`，两侧合同需要显式映射。
- 当前生成种子 `useRuntimeResult.ts` 对待交互状态的文案明确提示“当前入口尚未接通恢复操作”。

修复需要把实际 pending interaction 映射到交互控件，并按当前 thread、run 和 interaction 身份提交响应。明确拒绝、取消、只读查询和执行续接各自的行为，继续使用官方 AG-UI 客户端。

验收至少覆盖澄清补充、确认批准、明确拒绝、过期 interaction、刷新后查询，以及重复点击。写入 Tool 的拒绝路径应验证没有执行写入，未知结果保持显式状态，重试须由用户决定。

### 06. 多 Agent 后端与单 Agent 前端生成范围不一致

状态为当前代码限制已确认。

- 后端 `agent_contracts` 为数组，每个 Agent 有独立身份、七模块配置、入口合同和开发 Unit。
- `ui_design_agent_surfaces.py::project_agent_design_pages` 要求参与设计的 Agent 集合大小为 1，每页最多一个 Agent Surface。
- `agent_ui_host_integration.py::plan_host_integration` 要求全部正式 Agent 数量为 1。
- `agent_ui_build_evidence.py` 和 `agent_ui_runtime_connection.py` 也消费唯一 Agent 假设，需要一并调整。
- 因此，即使后台 Agent 没有直接页面入口，也可能因为正式 Agent 总数超过 1 而阻断共享 Host 接线。

修复前需要确定多个独立会话在页面间如何绑定、切换和保持状态，以及后台 Agent 如何参与入口判定。该范围与 Agent 之间的自动协作编排需要分别定义。

验收至少覆盖两个公开 Agent 分别绑定页面、一个公开 Agent 加一个内部 Agent、跨页切换，以及线程和草稿隔离。修改多 Agent 规则后，要复核生成、转入、构建证据和实时连接诊断的所有消费者。

## 4. 建议修复顺序

1. 核对实际目标工程和模板发布包，处理请求版本及 state 镜像。
2. 对齐结果 envelope，并贯通 za38 公开 Run 身份。
3. 完成一次真实纯文本双轮对话，检查取消、失败及未知结果展示。
4. 接入任务输入、结构化结果、页面上下文与发送准入。
5. 接通澄清、确认、审批和只读查询。
6. 按产品目标扩展多 Agent 前端生成及共享 Host。

每项修复都应更新当前生产者、消费者、必要的回归和文档。涉及正式产物或配置变化时，沿用仓库现有的修订与确认流程。

## 5. 本次记录的验证边界

- 初次审查只编写问题记录并补充代码索引；后续代码修复状态见各问题章节。
- 上述结论来自当前源码和已安装成品包读取。
- 用户当前生成工程的模板版本及实际网络请求尚未核验。
- 本次文档任务未执行测试、构建、health、Python 编译、Electron 操作或真实模型调用。
