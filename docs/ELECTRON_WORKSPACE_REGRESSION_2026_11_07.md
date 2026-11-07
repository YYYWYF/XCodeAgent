# 2026-11-07 工作区 Electron 实机回归

验收对象为当前源码启动的 macOS Electron。操作通过原生窗口、原按钮与确认门完成；没有用 Vite 浏览器页面代替 Electron。除了状态和文件证据，还逐次核对了卡片、重试入口、中文名称、窄分栏、明暗主题、右侧文档和实际页面预览。

## 隔离与证据边界

- 原工作区：`/Users/yifei/Coding/xcodeagent_test/technical-complex-clean-20261106-02`。业务生成、源码修改、故障注入均在独立测试工作区进行。
- 全新应用：`/Users/yifei/Coding/xcodeagent_test/electron-regression-20261107/fresh-app`，从 Electron 新建并通过真实模型生成需求、产品规划、UI、技术规划，再逐门确认和初始化模板。无远程仓库、没有推送。
- 大 Contract：同根目录 `contract-batch/workspace`。已确认上游与本地会话是受控夹具，技术目录、Schema、分批接口、页面绑定由真实模型生成；不能把它算作第二次全新应用端到端证据。
- 网络代理只在 HTTP 边界注入明确的 503、延迟、暂停或供应商格式合法但业务正文无效的响应。不改业务节点、校验器和恢复状态；日志不记录密钥与认证头。
- 进程崩溃使用真实进程退出。窄窗口测试只读 SQLite，在原 checkpoint 已保存 `repair/input_committed` 时冻结并终止后端，没有人工构造恢复 checkpoint。
- 证据目录：`/Users/yifei/Coding/xcodeagent_test/electron-regression-20261107/`，包括 `network-events.jsonl`、`coverage.json`、`findings.json`、各阶段捕获及测试日志。
- 早期 `capture_state.py` 以相对工作区调用时 Graph 查询为空，不能据此证明 checkpoint 完全相同；生命周期身份与文件哈希比较仍有效。后续已改为绝对路径，分批崩溃和窄窗口证据读取了实际 Graph。

## 已完成的实机检查

| 场景 | 实际结果 |
| --- | --- |
| 二次修改分类断连 | 注入 503 后显示失败卡和红色失败节点，没有退化成“补充修改信息”。Electron 冷启动后请求与重试卡仍在。 |
| 前端二次修改重试 | 在前端执行节点注入 503，原重试回到前端修改；真实修改 Notice 页面，类型检查、当前工程 build 脚本、重启三项通过，Electron 预览实际显示目标标记。没有改错 Home。 |
| 后端二次修改重试 | 后端执行节点 503 后原按钮恢复该节点，真实修改 Java `ResponseEntity.toString`，独立 `mvn -q -DskipTests compile` 成功。此应用无业务 API，不将此项扩大为业务接口端到端验证。 |
| 技术规划三轮失败 | 三次无效正文触发原业务校验和底部重试，恢复真实模型后重新生成待确认技术规划；右侧显示当前文档。新重试留痕显示“技术规划重试”，不露出内部字段。 |
| 冷启动 | 完整退出并重新启动 Electron，分别核对分类失败恢复卡、待 UI 确认及正式修订交接后的生命周期身份、产物哈希与右侧内容。未替用户消费原工作区的确认门。 |
| 停止后继续 | 真实结束技术生成；源码未变时原“继续生成”重新完成技术规划并等待确认。 |
| 停止态读取失败 | Lifecycle 读取 503 时显示检查失败，隐藏继续按钮，不放行执行；恢复读取后重新出现继续资格。 |
| 后台源码变化 | 隐藏 Electron，修改隔离应用源码，再重新聚焦。自动改为“无法继续生成 / 工作区已变化”，只提供返回开发；点击返回前后模型请求数均为 72，没有启动业务。 |
| 文档失败与重试 | 冷启动时确认资料与右侧文档均读取失败。分别点击两处原“重新读取”，确认卡和当前 18 接口文档恢复。加载时不展示可消费的确认操作。 |
| 迟到文档隔离 | 文档读取延迟 18 秒，读取中切换到另一个隔离工作区；旧订单契约未覆盖新工作区。另已核对设计/计划切换时的文档隔离。 |
| 新建应用完整流程 | 从 Electron 新建静态公告应用；真实需求与 ProductPlan 联合确认、真实 UI 确认、技术规划确认、模板初始化、正常开发准入。 |
| 已完成页面保留 | 新增帮助页 Build 503 时，首页仍显示已完成，总进度 1/2；首页生命周期记录与源码哈希完全保留。原重试完成帮助页，最终界面及 Lifecycle 均为 2/2，底部旧失败卡撤下。 |
| Contract 分批崩溃 | 单 Contract 18 接口，第 1 批 6 个接口已落盘、第 2 批暂停时终止后端；重启后 Electron 原卡继续，最终右侧显示 18 接口并待确认。首批内容和哈希不变，未完成前没有正式技术规划文件。证据：`contract-batch/recovery-result.json`。 |
| REPAIR 入场窄窗口（后续修复闭环） | 原底部重试在真实 `repair/input_committed` 终止后端，重启后原卡继续回原请求。首次暴露“仅复核”无法无变更结束；后续修复后同一原请求真实读取5个窗口并显式结束复核，四段内容全部不变、18接口完整，重新待确认。证据：`repair-input-crash-observation.json`、`followup-review-result.json`。 |
| 明暗主题与布局 | UI 0/N、旧设计失效、生成中、确认与文档错误态；39–40% 窄分栏和约 67% 原宽度均肉眼核对。已修复页面名消失、状态按钮重叠、深色空态及底部运行提示覆盖图标。 |

## 实机发现并修复

1. UI 0/N 仍能确认并进入计划：前后端同时按当前 Manifest 和实际代码验证，未齐备禁用下一步。
2. UI 确认后中文页名变为内部 ID：公开页面投影补齐当前产品页面名称与路径。
3. 确认资料失败无重读：沿原卡增加当前读取轮次的重试，迟到回执继续隔离。
4. 分类失败节点误显绿色：原失败终态携带实际节点的 failed 步骤和事件。
5. 二次修改模型错误被吞：写入前无差异时重抛原异常，保留原节点与具体原因。
6. Rsbuild 应用被固定 Vite 校验、修复后验收不完整：运行当前工程 build 脚本；每次修复重新完成类型检查、构建与重启。
7. 当前页身份未传给写入 Agent：传递 `change_target`，要求核对实际路由和组件。
8. 旧 UI 代码误显本轮绿色成功：当前 confirmed 事实决定状态和数量，旧代码仅供预览。
9. 窄分栏页面名称和操作重叠：按卡片宽度换行，保留宽布局及现有主题。
10. 窄分栏运行提示覆盖 Trace 图标：提示可换行，工具与停止按钮不被压缩。
11. 崩溃恢复后再次修改技术规划失败无底部错误卡：同线程新 Run 的正式快照撤下旧 Run 恢复源；同轮增量和只读问答不改恢复身份。实机已重新看到具体原因及原重试按钮。
12. 原复杂工作区的 API 能力卡长实体名覆盖相邻卡片：新增 `DocPanel/TechnicalPlanContractResponsive.less`，约束卡片内部 Grid 并允许长标签换行。已在原工作区同一待确认文档、明暗主题下实机复核，未消费确认门或修改业务产物。

主要改动位于 Backend 的 `agents/direct_modification.py`、`graph/nodes/{direct_modification,ui_confirmation}.py`、`protocols/{direct_modification,workflow/presentation}.py`、`services/direct_validation.py`；Frontend 的 `service/activeApplicationPlanning.ts`、`AiChatPanel` 原错误/文档入口、`Welcome/UiDesignConfirmationPanel` 和 `ChatComposer` 样式。对应聚焦测试与本索引一同更新；工作区其他原有修改不属于这次实机修补。

## 验证命令

- Backend：`.venv/bin/python -m unittest tests.test_direct_validation tests.test_direct_modification tests.test_direct_classification_failures tests.test_ui_design_confirmation_gate tests.test_workflow_projection tests.test_business_node_retry`，131 项通过。
- Frontend：`pnpm test:workflow-preview`，115 项通过；`pnpm test:workflow-documents`，通过；`pnpm test:application-planning-runtime`，通过（含新 Run 撤下旧恢复源、同 Run 保留来源）。
- Frontend：`pnpm build`，最后一次见 `build-final-contract-layout.log`，类型检查与构建成功。
- 隔离生成应用：`mvn -q -DskipTests compile`，通过。
- 每次产品代码改动后检查 `/health`，均返回 `status: ok`；最终直连恢复后的检查见收尾记录。

应用开发流程内的可选单元测试由原 UI 明确选择跳过；该选择是应用业务流程测试，不等于仓库测试跳过。

## 后续修复与实机复测

上一轮留下的两项已继续修复：

- 阶段交接：`AiChatPanel.tsx` 将在途状态绑定当前工作区及交接入口，`planningStageAccess.ts` 在权威阶段/线程尚未换绑时只显示同步态；`PlanningHistoryNotice.tsx` 复用原底栏展示“正在进入计划阶段”。右侧沿 DocPanel 加载态，成功或回滚后撤下，真正历史会话门禁不放开。Electron 中将实际 `planning_stage_entry` 请求延迟18秒，明暗主题均核对提示、右侧加载及无错误历史跳转；恢复后正常显示当前技术文档和修改/确认按钮。正式修订的新帮助页标题亦已在需求页面信息和实际 UI 稿中核对。
- 技术复核：`technical_plan_review.py` 为内存编辑器增加显式 `finish_review`；实际读取完整候选并核对四段、没有编辑或未解决工具错误时才可结束无变更复核。普通回复不能替代该动作，结果仍经过完整物化校验和新的确认门。`technical_plan_file_tools.py` 提供总行数、EOF 和进度；重复无进展读取显式结束本次尝试，沿原重试预算收口。原18接口请求只读取5个窗口后结束，架构、实体、契约和页面四段逐项完全一致，无伪造编辑，状态 `pending_user_confirmation`。Electron 已核对当前确认卡、可操作按钮和右侧18接口。

新增聚焦验证：Backend `.venv/bin/python -m unittest tests.test_technical_plan_editor_tools tests.test_technical_plan_fragment_editor tests.test_technical_plan_modular_workflow tests.test_technical_planning_stage_retry`，41项通过；Frontend `pnpm test:workbench-phase`，23项通过；`pnpm build` 通过（`followup-build.log`）；每次代码改动后 `/health` 均为 `ok`。

后续证据：`followup-review-before.json`、`followup-review-result.json`、`followup-review-running.json`、`followup-handoff-delay.json`、`followup-handoff-complete.json`、`followup-backend-live.log`。模型内容和真实界面分别作为生成与显示证据；延迟仅发生在网络边界，没有修改业务 checkpoint。

## 四项补测（本轮闭环）

继续使用源码 macOS Electron 原生窗口与原按钮。第2、3项在 `contract-batch/workspace`，第1、4项在新的 `fourcases/workspace`；后者在测试前登记应用身份并准备当前合同的已确认上游与设计会话。准备阶段的占位归属已归档，故障发生后没有改写 checkpoint、生命周期或会话来制造恢复。

| 补测 | 实际结果与证据 |
| --- | --- |
| 1. 阶段交接断网及中途退出 | 交接已提交、入口请求503时暴露空计划会话、错误历史只读与空文档。修复后当前门可操作且右侧保留已确认需求；再次暂停入口请求并完整退出 Electron，重启后原门、文档与明暗主题正常。原入口继续真实生成，全程只有1个 PLAN 会话、权威线程未改变；旧设计来源仍只读。`fourcases-entry-failed.json`、`fourcases-entry-recovery-result.json`、`fourcases-electron-cold.log`。 |
| 2. 复核后真实字段修改 | 真实模型将共享响应字段 `order_number` 改为 `orderNo` 并同步 required，保留 `entity_field_ref=order.order_number`。完整JSON严格对比只有这两处业务差异，18接口和其他所有字段均未变化。Electron 右侧显示新字段及必填映射，当前修改/确认可用。`fourcases-before-edit.json`、`fourcases-after-edit.json`、`fourcases-edit-result.json`。 |
| 3. 重复读取失败与原按钮重试 | 网络边界返回格式合法的重复 `read_file` 工具调用，真实编辑器的无进展守卫及原重试预算触发失败。Electron 显示具体重复读取原因和底部重试，旧确认失效；移除注入，原重试让真实模型读取完整候选并显式结束复核，旧错误撤下，新确认可用，文档与第2项修改结果严格相同。`fourcases-backend.log`、`network-events.jsonl`。 |
| 4. 大Contract主动停止与独立断网 | 首批6接口保存、第二批暂停时点击原“结束任务”；停止卡与继续按钮正常，分批清单完全保留。原“继续生成”保存第二批，共12接口；第三批暂停时只终止8000网络代理，8001后台及模型代理不退出，后台实例身份未变。Electron 显示连接失败；恢复网络并原“重新同步”后出现中断重试卡，原重试补齐第三批和页面绑定。最终18个唯一ID/方法路径，首两批12个接口逐项与哈希均不变；此前没有正式技术文件，完成后重新待确认。`fourcases-before/after-active-stop.json`、`fourcases-before/after-network-loss.json`、`fourcases-complete.json`、`fourcases-batch-result.json`。 |

本轮修补文件：`AiChatPanel.tsx`、`planningStageAccess.ts`、`MessageList/index.tsx`、`MessageList/planningMessageWorkflow.ts`；回归 `workbenchPhaseNavigation.test.tsx`。当前线程与服务端交接回执共同决定入口归属，重试直接消费已交接的当前门，不重复建立线程；正式确认与真实历史门禁不变。技术生成尚无完整文档时显示原加载态，避免错误提示“未读取到结构化数据”。索引同步更新。

验证命令：`pnpm test:workbench-phase` 24项、`pnpm test:design-conversation` 4项、`pnpm test:workflow-preview` 115项通过；最后 `pnpm build` 通过，分别见 `fourcases-phase-tests-final.log`、`fourcases-design-tests.log`、`fourcases-workflow-preview-tests.log`、`fourcases-build-final.log`。产品代码修改后及恢复正常直连后 `/health` 均为 `ok`。本轮没有修改Backend产品Python，未额外运行Python编译与全量后端测试。

本轮收尾：代理及8001临时后端停止，8000恢复正常直连；密钥未改动。原复杂工作区保留待确认门，6份配置及正式产物哈希均未变（`fourcases-original-preservation.json`）；测试工作区和证据保留。未提交或推送。

## 未计为通过的范围

- 未覆盖 Windows Electron、安装包启动与升级。本机源码版证据不替代这些场景。
- 未覆盖复杂业务应用真实 CRUD/API、全量单元/集成测试与大规模压力测试。静态公告应用和受控 Contract 上游不证明这些范围。

## 收尾

测试代理与临时后端已停止，`MODEL_BASE_URL` 恢复原直连地址，密钥字段未改动；正常 8000 后端 `/health` 返回 `ok`。Electron 已恢复原复杂工作区、浅色主题、67% 分栏与待确认技术规划；6 份原配置及正式产物哈希均未变化。测试工作区及证据保留便于复核。本轮没有提交或推送仓库修改。

## 本地提交边界

阶段交接中断恢复、消息阶段归属、右侧文档入口与加载态及对应测试单独提交；本报告记录的其他工作区修复保留各自原有未提交状态。提交前从 HEAD 与暂存内容独立导出 Frontend，不带入其他工作区改动：`pnpm test:workbench-phase` 23项与 `pnpm build` 通过，日志见证据目录 `commit-selection/staged-snapshot/`。该验证与前述完整工作区143项验证范围分别记录。
