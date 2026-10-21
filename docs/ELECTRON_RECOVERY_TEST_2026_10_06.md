# Electron 全阶段停服恢复验证（2026-10-06）

状态：**主阶段逐项实测已到验收，存在已记录失败；条件修复分支覆盖未完成**。单测修复被 E04 阻塞，不能宣称全覆盖通过。不得据此宣称全阶段通过或没有遗漏。

## 修复后真实 Electron 复验：E01/E03/E05/E06/E07

本轮使用最新代码、真实模型和实际停服，逐项复验以下已修复问题。主阶段审计的 E02/E04 与其它条件分支限制不因此变为通过。

| 项目 | 本轮结果 | 实际证据 |
| --- | --- | --- |
| E01 UI 生成池中断 | 通过 | 在 UI恢复验证项目点击换一换，真实生成中 SIGKILL Backend；离线底部重试可见，重启后底部恢复进入 ui_confirmation（Run 0eb63d33-da57-4eba-ad88-77a2ae840007），真实模型完成页面；仍停在 UI 确认门，随后手动确认再进入技术计划。 |
| E03 Build 真实错误文案 | 通过 | 正常创建骨架、页面开发与 DAG 确认；Build 完成 1 个任务，真实收尾报 Route Projection Finalization 失败：模板缺少 Route Projector Descriptor。lifecycle error 与底部错误一致，没有扫描成功文案；底部业务重试实际复用 1 个完成任务，再次展示同一真实原因。 |
| E05 恢复运行暂停 | 修正后通过 | 原修复首次实测仍不发取消。进一步修正底部 retry 的异步 finally 提前清理身份，以及独立 Recovery 流落到普通 transport 的停止路由。重载最新 Electron 后再次真实恢复 Build，点击暂停经 /workflow/run 发取消，recovery-afcb08c97e36 变为 stopped；UI 显示已停止和继续/结束。 |
| E06 结束后旧入口 | 通过本轮失败/重试/结束场景 | E03 失败后从底部重试，再点击结束并确认；即时显示计划已结束、恢复自由输入，底部错误和重试消失，无需 Reload；磁盘 activeExecutions 为空。此前长时间单测场景的那条特定临时错误没有再次制造，本轮验证的是实际失败恢复后的结束清理。 |
| E07 验收预览隐藏兜底 | 通过 | 静态页项目从服务状态正常启动真实预览，Electron 嵌入 localhost:3000 页面；验收预览全宽时 SIGKILL Backend，读取服务状态失败后自动恢复分栏，预览与底部重试同时可见；浅色和深色实际截图确认。重启健康后底部消失、预览全宽恢复，两项验收决定按钮保留，未自动提交决定。 |

本轮测试工作区：`/Users/yifei/Coding/xcodeagent_test/ui-recovery-fix-20261006` 与 `recovery-static-20261006`。Build Graph thread `f7d817ac-a05c-4e16-89a3-b0fe4ab27f57`，真实失败 Run `82c1372c-4a20-493d-a888-4b3f19167844`，业务重试 `recovery-c37ebbdbde9b`。证据目录 `/tmp/xcodeagent-electron-recovery-20261006` 的 backend.log、e3-final-real-failure.json、e6-final-ended.json、e1_final_regression-before-stop.json、e7_final_real_preview-before-stop.json；原生 Electron 截图/AX 观察附于本轮工具记录。

本轮只追加修正恢复控制路由（AiChatPanel.tsx、useWorkflowConversation.ts）与异步恢复防重入回归（workflowConversationRuntime.test.ts）；没有改 Build 内部生成/修复、模板、确认门或普通 Workflow 停止实现。78 项 connection/runtime 测试、pnpm build、git diff --check 和 /health 通过。Backend 已恢复健康，主题恢复浅色；UI恢复验证的测试执行已结束，真实生成文件保留未提交。此前正常模板初始化在隔离应用内自动创建本地提交 edb111fe；没有提交或推送主仓库。

## 方式与边界

由 Codex 直接操作已经运行的 Electron 应用；所有模型任务由正常界面入口触发。使用真实模型，实际 SIGKILL Backend 的 Uvicorn supervisor/worker，再按原命令启动同一端口。没有用浏览器页面替代 Electron，没有伪造 AG-UI 生命周期或通过调试入口跳过业务确认门。需求、UI、技术计划和 DAG 的推进均点击现有确认入口。

测试目录：
- `/Users/yifei/Coding/xcodeagent_test/recovery-global-20261006`：计数器场景，发现 UI 生成池恢复问题；技术规划的原生恢复及内部修复已验证，但纯前端业务动作映射校验阻塞后续。
- `/Users/yifei/Coding/xcodeagent_test/recovery-static-20261006`：无业务动作的单个静态页面，继续覆盖其余阶段。

## 本次修复

Recovery 投影新增 `RECOVERY_PROJECTION_READ_FAILED` 显式错误。候选查询、候选解析、外层投影失败不再冒充成功空列表。Frontend 保留仍被 lifecycle 持有的旧候选证据，但禁止用它们执行恢复；底部错误重试仅重新读取 Attach/Get，不误路由到其他独立节点或执行旧 action。成功空投影、正常完成与 End 的清除逻辑保留。

验证：后端 21 项、连接恢复 74 项、恢复状态/卡片 37 项通过；pnpm build、Python compile、git diff --check、/health 通过。

## 已测记录

| 测试点 | 停服时权威状态 | 结果 | 重连结果 |
| --- | --- | --- | --- |
| requirements | awaiting_requirement_document_confirmation/awaiting_user | passed_confirmation_boundary | 自动读取恢复到原确认门，未绕过需求/ProductPlan联合确认 |
| ui_design | awaiting_ui_design_confirmation/awaiting_user | passed_confirmation_boundary | 返回已生成UI确认状态 |
| ui_design_inflight | awaiting_ui_design_confirmation/awaiting_user | failed | 连接健康后底部消失，UI pool仍生成中；点击刷新仍生成中，换一换禁用 |
| technical_planning_inflight | generating_technical_plan/running | reentry_and_internal_repair_verified | 继续等待技术产物确认；恢复链与内部修复已有真实日志证据 |
| requirements_inflight | analyzing_requirement/running | passed | 模型继续生成，回到 RequirementSpec/ProductPlan 联合确认门 |
| product_planning_inflight | generating_requirement_document/running | passed | 模型继续生成，回到 RequirementSpec/ProductPlan 联合确认门 |
| technical_planning_confirmation | awaiting_technical_plan_confirmation/awaiting_user | passed_confirmation_boundary | 保持技术计划明确确认门；没有自动写正式产物 |
| template_bootstrap | generating_application_template_files/running | passed | ready_for_workbench/completed，确认的正式需求与技术计划保留 |
| prepare_build_tasks_inflight | ready_for_workbench/completed | passed | 恢复到 prepare_build_tasks，完成 DAG 并显示任务确认门；显式确认后才进入 Build |
| build_inflight | ready_for_workbench/completed | reentry_verified | 解锁后底部继续执行进入 recovery-b0d02e23a533；原停服画面未观察，另作复测 |
| build_retest_inflight | Build 页面任务生成中 | reentry_verified | 停服显示底部重试；恢复后继续执行 → recovery-42a76d159ded → build → 当前工作区扫描 → 页面任务重新生成 |
| unit_test_inflight | TestGeneration Agent 模型生成中 | reentry_verified | 停服底部重试，重启继续执行 → recovery-3abf390e6dda → unit_test；后续被 E04 阻塞 |
| integration_checks_and_planner_inflight | integration_test/running，真实检查失败并进入模型修复规划 | reentry_verified | 底部重试 → recovery-2b1c189b447f → integration_test → 真实复测 → 修复规划 |
| small_task_repair_inflight | small_task_repair 真实模型 callback | reentry_verified | 底部重试 → recovery-a070866d1376 → small_task_repair → 当前源码扫描 → 原待修任务；内部完成未验证 |
| code_review_inflight | Diff审查真实模型生成中 | passed | 底部重试 → recovery-eeb7c752bc91 → code_review，重新读取同1文件，0问题，停在验收确认门 |
| acceptance_inflight | acceptance/running，确定性项目启动中 | passed | 底部重试 → recovery-ec15358482ba → acceptance，重启当前工程，等待明确用户验收 |
| acceptance_confirmation_boundary | acceptance/awaiting_user，预览展开 | failed_visible_fallback_hidden | 底部入口被隐藏；关闭预览恢复可见；重启后仍保留page_acceptance确认门 |

结果中 `passed_confirmation_boundary` 仅代表生成后确认边界的停服表现，不等同于模型生成中断。

技术规划计数器场景：`/execution-recovery/execute` → `technical_planning_generate`，恢复 Run `recovery-67279899d30a`；随后真实出现 `technical_plan_generation_retry`（1/3、2/3、3/3）和 `technical_plan_repair_route`。内部修复原流程确实执行，但模型绑定不存在的 API/实体，最终回到等待用户处理的校验门。

需求原生恢复 Run：`recovery-5cac80bf5c0a` → `requirements`，产生角色澄清，正常回答后继续，保持需求/ProductPlan 联合确认。
ProductPlan 原生恢复 Run：`recovery-e96aaccae3b0` → `product_planning`，完成后恢复联合确认门。
DAG 原生恢复 Run：`recovery-a459e1b8f404` → `prepare_build_tasks`，完成后显示 DAG 确认门。
Build 被中断的 source Run：`48a700b3-b7c8-4c37-892c-17d343540e9b`，thread `db2b2b58-7547-4f57-985f-e85f14e4b328`；日志已确认 `node=build`、真实模型 callback 和重启后的 `recovery.execution.interrupted`。解锁后原生恢复已验证，并另行实际停服复测观察了底部入口；等待 Build 完成。

## 发现的问题（先记录，未修复）

### E01：UI 生成池在 Backend 重启后仍 running，健康连接清掉底部入口

复现：在 Electron 点击页面 UI“换一换”，观察“生成中”，实际停止 Backend；底部出现“Backend 暂时不可用/重试”。重新启动 Backend 后连接自动恢复，底部入口消失；页面仍“生成中”，点击“刷新”后仍未识别中断，正常重新生成入口不可用，仅节点“停止”可解除。明确停止后可重新生成；随后正常生成并重入工作台可以继续推进。

影响：底部连接恢复不代表独立 UI 生成池已恢复；可能失去正常重试路径。此处需要单独接入生成任务的中断校准，不能把“健康连接”当成任务成功。

修复复验（2026-10-06）：E01 已通过。新建真实 Electron 测试应用 `/Users/yifei/Coding/xcodeagent_test/ui-recovery-fix-20261006`，正常生成 RequirementSpec/ProductPlan 并联合确认，在 UI 页面真实生成中停止 Backend supervisor/worker。离线底部显示重试；重启并同步后连接健康，底部仍显示“UI 设计生成已中断”。点击底部重试后，先只读对账，再提交当前 UI 门 `ui_action/refresh`，进入原 `_latest_ui_designs` 自愈，实际模型生成成功。磁盘 `ui-designs.json` 页面为 confirmed，但整体 `confirmation_status` 仍为 pending_user_confirmation；Electron 显示 1/1、进入计划阶段按钮，仍停在设计阶段，未绕过正式确认。

证据：planning thread `10eeb51a-c84a-40e5-803a-2d2e1ded93bd`，重试执行 `af1a1075-1c36-46d1-848a-1dfad1edd1e9` 的 firstNode/node 均为 `ui_confirmation`；`/tmp/xcodeagent-electron-recovery-20261006/backend.log` 与 `ui_recovery_fix_inflight-before-stop.json`。首次复验发现请求校验不接受 refresh，已补齐协议并再次实测成功。Backend 当前健康。

验证：92 项 Backend UI/投影/请求测试通过；Frontend planning runtime、planning recovery、74 项 connection recovery 和 pnpm build 通过；git diff --check 通过。生成池旧测试另有 5 项失败，原因是旧 mock 不接受已有 should_cancel 参数；原生成池及其测试与 HEAD 完全一致，使用 HEAD 测试副本复现同样 5 项失败。本次不改生成池内部正确的取消/生成机制。其它问题状态不变。

源码核对：`Backend/app/graph/nodes/ui_confirmation.py` 的恢复函数已有正确的孤立 queued/generating 重新入队机制（约 593–609 行），`services/ui_design_generation_pool.py` 的队列是进程内状态。`Frontend/.../Welcome/UiDesignConfirmationPanel.tsx` 在 IPC `readUiDesigns` 可用时不走 Graph fallback 轮询（约 667–681 行）。因此不能把此问题描述为“节点没有恢复机制”；实测说明现有自愈机制没有在这次普通重连/刷新中有效进入，后续应重点核对恢复动作路由，保留节点内部正确逻辑。

### 场景限制：纯前端业务动作的 TechnicalPlan 绑定校验

计数器 ProductPlan 把加一/重置标为 business action，TechnicalPlan 则必须绑定合法 Endpoint；模型连续修复产生不存在的实体/接口。这是本轮场景推进的业务校验阻塞，不计为停服恢复失败；未改产品规则，另建静态场景继续测试。

### E02：Git fallback 模板缺少当前 Route Projector 契约

Build 页面任务生成成功后，收尾报“模板缺少 Route Projector Descriptor”。底部重试存在，正常完成被阻塞。当前 fallback 包仅有 frontend/backend 和 TemplateState，没有交付当前 v2 描述符与 Schema。此问题未修。为继续阶段验证，仅在静态测试目录补充真实写入 BIZ_MENUS 的当前 v2 测试投影器，没有伪造成功、修改正式 DAG 或绕过确认。后续结果以补齐此测试夹具为前提。底部业务重试成功复用已完成任务，并完成实际路由投影，进入单测明确选择门。

### E03：Build 收尾真实错误被扫描成功文案掩盖

真实日志为 Route Projector 缺失，但 lifecycle error.message 和底部错误说明为“代码扫描完成，已建立工作区代码索引。”；页面任务已完成，Build 仍正确失败且重试入口存在。未修。

E03 修复（2026-10-06）：仅错误投影链路采用当前失败 Build 已记录的 build_summary.finalization_error，覆盖旧扫描摘要，贯穿 lifecycle error、handle_failure 和最终 AG-UI summary。未修改 Build 调度、Route Projector、模板 fallback 或任务结果；其他节点不读取旧 Build 失败。测试验证 lifecycle 持久化错误与最终摘要一致。未再次触发真实缺失模板的 Build 失败，不将此修复标记为 Electron 全链路复验完成。

### E04：单测生成把平台 Recovery SQLite 写入误判为 Agent 越界

恢复后的 TestGeneration Agent 最终报告“测试目录外实际写入：.devagentstudio/recovery/execution-recovery.sqlite”。该 SQLite 是平台运行恢复事实，正常心跳/进度会更新。单测生成门禁因此失败，未进入 unit_test_repair；底部重试入口存在且真实原因显示正确。产品代码未修改，后续需核对实际写入快照的排除规则，不能放宽 Agent 生产源码写入限制。

### E05/E06：暂停与结束的异常

E05：单测原生 Recovery Run 运行中，两次点击“暂停执行”后 AX、lifecycle 仍 running，日志未见停止请求。停止本次 Backend 后正常恢复资格出现，使用普通 End 结束用例。源码核对：`AiChatPanel` 的 Recovery Run 暂停会调用 `handleStopPlan`；`useWorkflowConversation::handleStopPlan` 在 `loading || workspaceBusy` 时直接 return，运行中的恢复任务因此无法发出 Stop 请求。保留正常 Workflow 的正确停止逻辑，未修改。

E05 修复（2026-10-06）：当前会话的本地恢复流或无本地 SSE 的冷重入运行，通过已有 cancelWorkflowRun 调用 `/workflow/run` AG-UI cancellation，目标来自 lifecycle 精确 Run/Graph thread，取消后只读同步。取消未确认时保留错误，不乐观写 stopped；本地恢复流沿用主动停止标记，避免取消回执变成新失败。普通本地 Workflow 的 handleStopGenerating 及等待确认时的 planControl Stop 不变。实际 Hook+AG-UI 客户端回归验证忙碌锁不阻止当前运行取消、错误会话/旧 Run 不发送取消，并且不登记第二个生成运行。未重新启动长时间单测模型 Recovery 运行做 Electron 点击暂停复验。

E03/E05 定向验证：44 项 Backend failure/projection/run-control 测试与 77 项 Frontend connection/runtime 回归通过，pnpm build、git diff --check、Backend /health 通过。Electron 当前正常验收工作台无错误兜底卡；长时间模型中断链路尚未再跑。

E06：普通 End 已返回“计划已结束，工作区已恢复自由输入”，但当时底部仍显示“无法安全执行当前恢复操作”重试卡；Electron Reload 后消失。在局部修复用例正常 End 后再次复现；可能是旧失败状态泄漏，待源码核对。

E06 修复（2026-10-06）：确认原因是旧会话 recoveryError 仍传入 globalFallbackState。成功且请求身份仍匹配的 End 回执后清除相关会话的 recoveryErrors，并在已结束的展示投影中排除旧 recoveryError。结束失败不会清除错误，连接故障和独立流程错误仍保留入口。新增回归覆盖这些边界，76 项 connection recovery 测试、pnpm build 通过。未重新制造之前长时间单测/局部修复场景的旧错误，因此该原始 End 场景尚未完成第二次 Electron 复验；不将其标为全量行为验收通过。E05 未修改。

后续阶段使用正常“新建对话 → 页面快捷开发 → DAG 明确确认 → 明确跳过单测 → 测试阶段明确确认”；未通过调试跳过门禁。新 Build 复用已完成任务。单测修复分支因 E04 未触发，不算已覆盖。

### E07：验收预览视图隐藏统一底部重试

正常启动成功进入用户验收后，预览展开状态占满工作区。实际停止Backend，默认界面没有Backend不可用或统一重试；打开“服务状态”抽屉显示 `TypeError: Failed to fetch`，仅有普通“启动服务”按钮。关闭抽屉和右侧预览后，底部 `Backend 暂时不可用/重试` 立即出现。

源码：`AiChatPanel.less` 中 `acceptance-preview-focus` 把整个 `ai-chat-assistant` 设为 `display:none`，统一兜底卡一起被隐藏。因此不是完全无恢复路径，但正常验收视图没有可见的统一入口，用户必须先退出预览。服务恢复后仍保持原用户验收门，无自动通过。未修改产品逻辑。

E07 修复复验（2026-10-06）：通过。验收嵌入预览正常全宽时实际停止 Backend（case acceptance_preview_e7_fix），读取服务状态触发真实 Failed to fetch，主界面自动恢复原分栏，预览保持打开且左侧底部 Backend 不可用/重试可见，不必关闭预览。浅色 AX 与深色实际截图均确认入口可见；Backend 重启后健康状态自动恢复，旧连接错误消失且验收预览重新全宽。未点击验收通过/不通过，原用户决定门保持。恢复原浅色主题，Backend 当前健康。没有新增颜色或修改节点恢复、预览维护动作。

## 未完成条件分支与覆盖限制

- Build：中断恢复已验证；补齐隔离路由投影夹具后业务重试成功、复用完成任务并进入单测确认门。
- 单元测试 `unit_test`：停服/底部重试/原生重入与业务失败重试已验证（recovery-3abf390e6dda、recovery-87144452b919）。`unit_test_repair` 因 E04 生成失败未进入，仍未测；另建正常开发会话并明确跳过单测以继续主阶段覆盖。
- 集成测试 `integration_test` 与自然进入的 `small_task_repair`：两处真实中断、底部入口和原生重入均已验证。局部修复重入后超过20分钟仍反复读取配置，实际修改过pnpm配置但未交回根节点；主动中断并正常End，保留完成未验证的结果。随后只在隔离目录将 `frontend/pnpm-workspace.yaml` 配为 `allowBuilds.esbuild: true`，真实 `pnpm install`、`pnpm build` 通过。再次解锁后已通过正常工作流复跑：真实前端依赖安装、构建与Lighthouse性能检查均通过；无实体/API的后端检查按原节点规则跳过。随后明确选择Diff审查并进入验收，未调试跳门。
- 代码审查 `code_review`：已完成真实模型中断、底部入口、原生重入和Diff结果确认。审查0问题，修复分支未自然触发，不算已测。
- 验收 `acceptance`：启动中断恢复已通过；等待验收时的预览视图停服发现 E07，关闭预览可找回底部。Backend重启后durable状态仍为awaiting_user/page_acceptance，submittedAt=null，未自动通过。启动器本身没有模型生成，按实际确定性启动节点测试；启动业务失败分支未自然触发。

未进入的条件修复分支不得标为已测试。若正常生成直接成功，需要明确记录“未触发”，再确定是否另设隔离测试用例覆盖。

## 证据文件

- 实际日志：`/tmp/xcodeagent-electron-recovery-20261006/backend.log`
- 结构化记录：`/tmp/xcodeagent-electron-recovery-20261006/report.json`
- 每次停服前权威 lifecycle 快照：同目录 `*-before-stop.json`
- Electron 画面与可访问性观察保存在本聊天的工具记录中；锁屏后没有继续假装操作界面。

后端启动方式：`Backend/.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`。目前服务已恢复。测试使用的观察脚本只读取状态和停止本次 Backend；未手动提交工作仓库、未推送或修改模型配置；验收阶段的产品自动本地快照见下文。

## 本次续测结束状态

再次解锁后审查和验收已实测；Backend保持健康，无本次运行中的模型任务或停服观察脚本。当前durable Recovery Run `recovery-ec15358482ba` 停在用户验收确认门，不提交验收决定。产品代码未继续修改，仅更新本报告及前述隔离夹具。单测修复被E04阻塞、审查修复和验收启动业务失败未自然触发；不能宣称全部条件分支通过。

正常验收页面中的 `AcceptanceCommitDock` 自动为隔离测试应用创建本地快照 `00fab99`（chore: 验收通过，保存当前版本代码）；这是产品现有行为，未手动提交工作仓库、未推送。
