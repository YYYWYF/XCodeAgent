# 清理五类代码后的两轮 Electron 回归

状态：进行中。只有两轮全部执行完毕后才形成最终结论。

## 清理范围

删除额外的集成测试跳过功能及状态、路由、UI、专属测试和索引说明；删除规划同步错误旧宿主函数；删除 AgentErrorCard 的未使用历史参数、错误码参数和旧文案兼容分支；删除前端旧会话清理包装函数及其测试；删除 execution_recovery_executor 的未使用 Path 导入。保留底部兜底、节点内部恢复、正常质量门禁和实际使用的后端会话清理。

## 自动检查

- `pnpm build`、`git diff --check`、Backend `/health`、七个清理涉及 Python 文件的 `py_compile`：通过。
- `pnpm test:project-deletion`：6 项通过；`pnpm test:execution-recovery`：35 项通过；`pnpm test:connection-recovery`：76 项通过。
- Backend 集成重入、集成 Native Recovery、审查重入、审查 Native Recovery、Workflow 投影、重入合同：46 项通过。
- `pnpm test:design-conversation`：失败。既有断言期待技术规划确认后为 `template_preparation`，实际 `technical_planning`。该输入与断言未由本次清理改变，负责 `planningWorkflowPhase` 的 `applicationPlanningWorkflowState.ts` 与 HEAD 完全相同；保留问题，不调整正常阶段逻辑。
- Backend `tests.test_workflow_request tests.test_workflow_routing tests.test_workflow_test_phase_confirmation tests.test_testing_subgraph_events`：178 项中 177 项通过，1 项失败。`test_prepare_build_tasks_enters_authorization_bootstrap_when_enabled` 仅传 TechnicalPlan 投影而无 workspace，期待 `authorization_bootstrap`，实际 `build`；负责路由及配置判定的两个函数与 HEAD AST 完全相同。保留问题，不改变 application.json 配置权威规则。
- 清理后 Electron 快速检查：静态页测试项目可打开；真实停止 Backend 后浅色/深色均有底部连接重试入口，恢复 Backend 后入口清除。仅为预检查，不计作以下两轮完成。

## 第一轮：正常流程

工作区：`/Users/yifei/Coding/xcodeagent_test/cleanup-normal-20261006`；应用：清理后正常流程验证；规划 Thread：`12f53fa7-0d49-43f1-bddb-2050e67e5ebb`。

新建项目正常进入 requirements（Run `3c57e0cc-d56a-4962-b404-6c9991cda7cf`），真实模型要求补充角色和业务流程。Electron 选择“浏览者（访问页面查看内容）”与“打开页面查看静态内容”并提交，回到需求分析；旧澄清卡自动失效，未把澄清当作正式产物确认。

需求与产品计划：通过。第二次 Run `abdf1236-c478-458a-b6dc-cefc06950632` 停在 product_planning 的联合确认门，两个 Markdown 草稿均已生成。Electron 点击“确认并继续规划”后才进入 UI 确认门。

UI：通过。真实生成一页设计稿，Electron 右侧渲染标题“正常流程测试”和说明“用于验证清理后正常流程”；显示 1/1 后仍待手动“进入计划阶段”。点击 UI 确认后还有独立的计划阶段交接门，第二次点击该门才启动技术规划；未跳过 UI。

技术规划与骨架：通过。技术规划 Run `0fb74fcd-7831-4ab5-9b2c-ca96020cdf89` 停在确认门；Electron 点击“确认保存”后生成应用骨架，lifecycle 到 `ready_for_workbench/completed`。从正常按钮进入开发阶段，再点击页面快捷开发，开始生成 DAG。

非阻断问题：首次骨架生成显示“正在重试应用模板生成”；远端分支提交因环境未配置 Git 凭证而未通过，UI 保留“重试提交”，不影响本地工作区进入开发。本轮不写入凭证或推送远端。

DAG：通过。Run `fdb48e52-2738-40ad-bcb8-465a3eede796` 生成 1 个页面任务并停在 DAG 确认门，Electron 点击“确认并进入 Build”后才执行代码生成。

Build：原始正常流程未通过。Run `abee7c81-69f9-4df1-a79a-a412ceaea69e` 已完成 1 个任务并写入 1024 字节的真实页面代码，但收尾报“Route Projection Finalization 失败：模板缺少 Route Projector Descriptor”。lifecycle/recovery 真实为 failed，底部错误与原因一致，有“重试”入口；页面进度尚未完成，测试阶段仍禁用。

为覆盖后续阶段，只在本隔离夹具补入此前静态页测试使用的当前 v2 Route Projector 描述、输入/输出 schema 和确定性菜单投影脚本，实际写入菜单路由；本轮 page ID 带 page_ 前缀，投影脚本移除该前缀以生成 /page/cleanup-normal。没有更改产品实现、伪造检查通过或编辑 lifecycle/恢复状态。后续结果均以“补齐模板夹具后”为前提，不能据此将原始 Build 判为通过。

补齐模板后的 Build：通过。Electron 从底部重试进入 `recovery-55c09e6d056a`，复用完成的 1 个任务，收尾后停在单测选择门。

单测：未通过。Electron 选择“否，继续执行”，Run `a9a8521f-101a-4083-a59c-050de2f8954e` 真正进入 unit_test 并调用 TestGeneration Agent。生成期间曾锁屏，解锁后确认真实错误为“测试生成 Agent 检测到测试目录外实际写入：.devagentstudio/recovery/execution-recovery.sqlite”。恢复数据库运行期写入触发生成器外部变更检查；底部显示该真实原因和重试入口。没有进入 unit_test_repair，不能将该分支记为通过。

为覆盖后续阶段，Electron 正常 End 并确认，旧兜底清除；新建会话、页面快捷开发、确认复用 DAG，再在原有单测选择门明确选择跳过。现有页面代码未重写，开发进度到 1/1，随后正常点击“进入测试阶段”。后续结果以单测跳过为条件。

集成测试首轮：未通过。真实依赖安装和构建均因 pnpm 的 esbuild 构建许可失败，性能检查因此跳过。主图自动进入 small_task_repair（Run `cbf943bc-95aa-4470-91f9-60e31a1bff19`），实际生成两个修复任务并把 `frontend/pnpm-workspace.yaml` 的 `allowBuilds.esbuild` 从占位文本改为 true。但约 10 分钟观察窗口内未返回集成复测，durable 节点仍 running；记为本轮未完成，不声称死锁或修复通过。

新增控制状态不一致：内部修复 running 时，lifecycle 仍保存 integration_test/failed，界面计划控制显示“计划执行失败”，仅有 End 而无 Pause。End 点击与确认曾尝试，但与再次锁屏重叠，未观察到收口，不能直接判定产品 End 失败；该操作记为待复核。静态检查发现 hook 有 loading guard，此事实也不能代替真实点击验证。为继续测试，使用既有 `/workflow/run` AG-UI cancelRunId 按准确 Run/Thread/workspace 清理，返回 cancelled，恢复记录 cancelled、lifecycle stopped，单独标注为测试维护动作，不算 UI 验收通过。

取消后曾看到“Backend 暂时不可用 / network error”，此时 `/health` 为 ok。再次解锁后实际点击底部同步重试，连接错误变为“已同步后端状态，但当前会话没有可验证的恢复入口”，而计划控制显示 stopped/继续执行，存在当前底部归属与控制状态不一致，保留记录。随后正常 End：实际弹层确认后显示“计划已结束”、清除旧兜底并恢复自由输入，End 此场景通过；此前与锁屏重叠的尝试不计为确定失败。

继续覆盖：新建正常开发会话、确认复用 DAG、明确跳过已记录失败的单测，再正常确认进入测试阶段。复测实际通过前端安装和构建，停在性能测试选择门；明确选择继续执行 Lighthouse 后性能测试也通过，无实体/API的三项后端检查按既有规则跳过。Run `3d04837b-a474-43d4-be93-6cd811ed05ef` 停在 review_phase_confirmation。Electron 选择 Diff 审查后才进入代码审查；未使用调试跳阶段。

审查：通过。Diff 读取 1 个当前变动文件，模型报告 0 个问题，停在 acceptance_phase_confirmation；Electron 明确点击“进入验收阶段”后才启动项目。

验收启动与预览通过，原生验收收尾未通过。实际启动 localhost:3000，Electron 嵌入式预览访问 /page/cleanup-normal，显示标题“正常流程测试”和说明“用于验证清理后正常流程”。点击“验收通过”后 UI 显示“验收已通过，可以提交并推送”；但 recovery Run `180aa15f-5e19-4ec2-9b2d-1efbf56ca04a` 与磁盘 lifecycle 均仍 acceptance/awaiting_user，没有进入 finalize_project。静态核对现有 handleAcceptanceApprove 只通过 onApplicationLifecycleChange 更新前端扩展 acceptanceStatus，未提交 AG-UI 原生验收决定。保留独立业务问题，不在本轮修改。提交弹层已取消，未推送远端。平台在待验收时已自动保存夹具代码（16026dad），没有修改主仓库提交。初始预览打开 /page，需从地址栏进入具体页；初始应用名仍为模板的“测试应用4”，也记录为模板投影不完整，非兜底缺失。

第一轮主阶段已从新建实际测试到验收决定及收尾检查，整体未通过：原始模板 Build 失败、单测生成失败、集成首次依赖失败、内部修复观察窗口未完成，以及原生验收未收尾均保留；unit_test_repair、审查修复及实体/API分支未自然触发。后续通过建立在补齐模板 Route Projector、原有单测跳过选择及内部修复已写入配置上。

## 第二轮预检查：失败兜底

时机审计纠正：以下预检查最初等待的 `[model-output]` 来自 ModelOutputLogger._start_run，仅证明调用已开始，不证明实际内容已生成。因此以下“通过”只描述该较早中断时机，不能算用户指定的生成后中断验收。停服脚本已调整为等待实际 JSON/代码内容（至少 80 字符）或已完成 model-tool-calls 输出；将从另一个独立项目重做正式第二轮，不覆盖这些原始证据。

工作区：`/Users/yifei/Coding/xcodeagent_test/cleanup-fallback-20261006`；应用：清理后兜底全阶段验证；规划 Thread：`43a6bf6a-43f2-41c9-a106-3683fd486269`。独立从新建开始，使用真实模型；证据保存到 `/tmp/xcodeagent-two-round-20261006`。

需求中断：通过本轮路径。requirements 出现真实 `[model-output]` 后 SIGKILL Backend supervisor/worker，离线 Electron 底部 Backend 不可用/重试可见。恢复 `/health=ok` 后点击该底部入口，既有动作同步当前中断并自动启动 `recovery-5473cf9b47b0`，实际重入 requirements；源 Run `572442f1-4af1-4c6b-b6ae-a15a9633b082` 为 interrupted。运行中恢复卡按钮禁用，停止生成可用。恢复完成后生成需求草稿并正常进入 product_planning，没有绕过联合确认门。

产品规划中断：通过。product_planning 出现真实模型输出后再次 SIGKILL Backend，需求草稿保持，离线底部重试可见；恢复服务后实际点击底部重试，`recovery-503c45db204b` 重入 product_planning，停在联合确认门；手动确认后才进入 UI。

UI 生成池中断：入口保持未通过，恢复链待最终结果。ui_confirmation 的真实 UI 模型输出后 SIGKILL，离线底部重试可见；重启健康后底部入口消失，UI 和磁盘仍 generating（page_cleanup_fallback），Native Run 已 awaiting_user，无运行中的 Backend 池。实际点击正常“刷新”后才显示“UI 设计生成已中断，请重试”的底部入口；因此健康重连后的自动投影刷新有缺口。随后实际点击底部重试，走既有 UI 恢复逻辑，生成结果待观察。该场景不记为全通过。

上述均为调用开始时机预检查，不计作正式第二轮完成。

## 正式第二轮：实际生成后停服

独立工作区：`/Users/yifei/Coding/xcodeagent_test/cleanup-generated-recovery-20261006`；应用：生成后兜底全阶段验证。停服条件等待实际 JSON/代码响应或 model-tool-calls，不能只凭调用开始标记。确定性阶段按实际执行方式测试；不可达分支明确记录，不记为通过。

需求：通过。真实 JSON 生成中 SIGKILL，源 Run `5df74449-d408-4747-85f5-ee2da59638d0`，停服时实际 current_node=requirements/running。离线底部连接重试可见；恢复 `/health=ok` 后实际点击底部重试，自动启动原生 `recovery-8026cc6f7f30` 重入 requirements；运行中继续动作禁用、停止生成可用。恢复完成后生成需求草稿并进入产品规划。证据 `generated-requirements-before-stop.json`，实际日志已核对含 JSON version 字段输出。

产品规划：通过。实际 JSON 输出后停服，需求草稿保持、底部入口可见；恢复后点击底部重试，`recovery-f3760457d46b` 精确重入 product_planning 并停在联合确认门。Electron 手动确认两个草稿后才进入 UI 门，未越过确认。

UI：入口保持未通过（正式时机复现），节点恢复通过。实际生成内容后停服，离线底部入口可见；重启 `/health=ok` 后 UI 与磁盘仍为 page_generated_recovery/generating、code_path 空，底部入口消失。点击正常“刷新”才投影出 UI 中断并恢复底部入口；随后实际从底部重试进入既有 ui_confirmation（Run d8e3ed9d-73ee-428d-ada9-b62a50db1735）恢复逻辑，页面 confirmed，仍需手动通过 UI 与计划交接门。证据 `generated-ui-generation-before-stop.json`。此问题属于兜底健康重连后未自动刷新当前规划状态的缺口。

技术规划：通过。实际响应内容生成后停服，源 Run `142b5523-aacf-4b98-a075-0fa3d20a4900` 为 technical_planning/running；离线底部入口可见。恢复后从底部重试，`recovery-f7c483e87f19` 重入规划生成逻辑并停在技术规划确认门，四个产物展示完整，未自动创建骨架。Electron 后续明确确认才测试 Bootstrap。

骨架初始化：通过。确定性 Bootstrap 在 frontend/package.json 与 backend/pom.xml 实际出现、initialization=generating_application_template_files/running 后停服（源 Run 0689e0dd-99ed-4eb4-b323-c672473932ff）。离线底部连接重试与原有“继续准备模板”均可见；恢复健康后只点击底部重试，既有 Workspace Bootstrap 重新执行并到达“应用模板已就绪”，开发入口解锁、TechnicalPlan 保持确认。远端提交因未配置 Git 凭证失败，保留原有提交重试；本地初始化已保存 68cdc005。证据 generated-bootstrap-before-stop.json。

DAG：通过。prepare_build_tasks 的模型已生成完整工具调用输出后停服，源 cb50a84d-00e3-484b-8fc6-2377de398b5f/running。离线底部连接入口可见；健康恢复后自动投影为“工作台执行需要恢复 / 继续执行”，实际点击该底部入口，recovery-c1b4211971be 重入 prepare_build_tasks，源记录 interrupted，最终生成 1 个任务并停在确认门。没有自动进入 Build。证据 generated-dag-before-stop.json。

Build：恢复链通过，业务收尾失败。模型实际工具调用输出后停服，源 376d2479-bd31-45a1-828d-a8e3661e9369/build/running；离线底部入口可见，健康恢复后自动显示“继续执行”。实际点击底部入口进入 recovery-e3aa1f345f4e/build，重建任务后实际验收完成 1 个任务（复用真实满足的代码），随后与正常轮相同因模板缺少 Route Projector Descriptor 失败。底部保留真实错误及“重试”，没有把成功摘要当作错误原因。证据 generated-build-before-stop.json。为继续后续覆盖，仅向本隔离夹具补入与第一轮相同的 4 个当前 v2 投影文件；原始模板问题仍记失败。

单测：入口与重入通过，最终结果观察中。unit_test 实际模型工具调用输出后停服，源 431cd47a-dfa0-4801-9fbb-3b6e510b2186/running。离线底部入口可见，健康恢复后自动投影“继续执行”；点击该底部入口启动 recovery-ba70dbed37df/unit_test，源 interrupted，重新调用 TestGeneration Agent，未绕过单测检查。证据 generated-unit-test-before-stop.json。最终业务结果与 unit_test_repair 可达性尚待观察，不记为全通过。

单测最终业务结果：失败。恢复生成后再次明确报测试目录外实际写入 .devagentstudio/recovery/execution-recovery.sqlite，与第一轮相同。底部保留真实原因与重试按钮；未自然进入 unit_test_repair，不能记该分支通过。Electron 正常 End 并确认，显示计划已结束、自由输入恢复、旧兜底消失；随后用正常新会话和原有跳过单测选择覆盖后续阶段。

集成流程：入口与内部重入通过，修复结果观察中。正常进入集成检查，依赖安装/构建实际失败（esbuild 构建许可），在失败分析模型完整工具调用输出后停服。源 d16c7e7f-d03b-41a9-95bd-ff83c66aee48 的 durable current_node=test_phase_confirmation/running（该入口内执行集成检查），证据 generated-integration-before-stop.json 按实际归属保存。离线底部入口可见，恢复健康后自动显示“继续执行”；点击后 UI 明确重新执行“集成测试与质量门禁”，随后进入原有局部修复分析，没有越过质量门禁。后续观察 small_task_repair 与最终结果。

局部修复：入口及精确重入通过，业务结果观察中。recovery-c34523fd92f6 进入 small_task_repair/running，在修复模型实际工具调用输出后停服（generated-small-repair-before-stop.json）。离线底部重试可见；恢复健康后实际点击底部入口，UI 明确首先执行“局部修复任务”，重新扫描当前工作区后继续原有修复逻辑。先前 running 时显示计划失败/仅 End 的控制状态偏差也再次观察到；重入后 Pause 正常可见。最终修复是否收口待观察。

局部修复最终观察：约 10 分钟内未返回集成复测，durable recovery-89beab5a1a82/small_task_repair 仍 running；实际已写入 frontend/pnpm-workspace.yaml 的 allowBuilds.esbuild=true 和 onlyBuiltDependencies=[esbuild]，证明内部修复执行继续，但不等于业务收口通过。不声称死锁。Electron 正常 Pause 显示已停止生成、随后 End 弹层确认显示计划已结束并恢复自由输入，两个控制动作通过；本轮不需要维护 API 取消。后续集成复测以该真实修复配置为前提。

集成复测：在真实修复配置基础上通过。正常新开发会话复用 DAG、跳过已记录失败的单测并确认测试，实际依赖安装/前端构建通过；明确继续执行 Lighthouse，最终停在审查范围确认门。后端三项按无实体/API规则跳过，无伪造质量状态。

审查：通过。code_review 实际模型工具调用输出后停服，源 3365c21a-8954-44ab-ab6e-817e0e8ad58c/running，离线底部入口可见。恢复健康自动投影继续执行；实际点击后 recovery-bedca757820c 精确重入 code_review，最终审查无问题，停在 acceptance_phase_confirmation/awaiting_user，旧兜底清除，需要手动进入验收。证据 generated-review-before-stop.json。审查修复分支未自然触发，不能记通过。

验收：实际启动后的待确认连接恢复未通过；原生完成通过。确定性 launch 在实际 frontend PID 63575/localhost:3000 已就绪后停服，源 c143f37b-0d14-4114-835b-9b1ff1bc8e2f 已 acceptance/awaiting_user（不算 running 中断重入，generated-acceptance-before-stop.json）。离线底部连接重试与验收决定入口都存在；健康恢复后点击底部同步却变为“已同步后端状态，但当前会话没有可验证的恢复入口”，实际 pendingInteraction=page_acceptance 完整，属于等待确认时的兜底错误归因问题。

Electron 嵌入预览 /page/generated-recovery 显示真实标题和说明。右侧“验收通过”更新版本验收显示并打开提交弹层，已取消推送；durable 仍等待确认。随后实际使用底部“验收通过并完成”及确认弹层，正常 AG-UI 提交原生决定，625c56da-53fe-4ab5-9fb5-85c325b37db1 进入 finalize_project/completed，界面显示完成、自由输入恢复。但上述同步制造的“没有可验证的恢复入口”底部错误仍未清除，形成完成后残留错误的兜底问题。

第一轮验收结论更正：此前只测了右侧版本验收按钮，不能据此认定原生验收无法完成或没有原生提交入口。本轮实际发现并验证底部完成按钮；拥有者 useWorkflowConversation.ts 的 handleCompleteAcceptance 正常提交 page_acceptance=accepted。第一轮所观察的右侧显示通过而 durable 仍 awaiting_user 保留为按钮行为区别/用户流程问题，撤销“原生验收收尾实现缺失”的推断。

第二轮主阶段已覆盖到实际原生完成。确认失败点：UI 健康重连自动刷新缺口；等待验收同步误报无恢复入口及完成后残留；局部修复运行时控制状态偏差。业务失败保留模板 Descriptor 缺失、单测恢复数据库被外部写入检查误判、首次 pnpm 许可失败，以及修复 10 分钟内未收口。unit_test_repair、审查修复、实体/API分支未自然触发；验收 launch/running 中断未命中，只验证实际启动后 awaiting_user 停服，不将不可达/未命中情况记通过。

第一轮重新打开后的收尾复核：欢迎页打开 cleanup-normal 项目默认恢复开发历史会话，底部显示“验收通过并完成”；实际确认后未产生新 Run，弹层仍留，未提交原生决定。关闭弹层后正常切到验收阶段，真实验收历史显示启动完成/待确认，却没有底部完成控制。durable 180aa15f-5e19-4ec2-9b2d-1efbf56ca04a 仍 acceptance/awaiting_user。因此第一轮收尾仍未通过，但具体问题修正为重新打开后待验收控制归属/会话恢复不一致，不能再推断整个原生验收实现缺失。未通过调试或手写决定绕过此失败。

测试结束状态：本轮只补充报告与隔离测试夹具，没有继续修改产品代码或推送远端。第一轮与正式第二轮主流程均从新建执行到验收决定及后端收尾核查，失败与条件通过单独保留。正常轮最终待验收控制恢复未通过；正式停服轮实际进入 finalize_project/completed，但残留兜底错误未清除。未自然进入/未命中的分支按上述范围明确标注，不能称所有分支均通过。

## 2026-10-07：仅修复明确属于兜底的 1、3

范围：不修改节点内部恢复、质量检查、修复预算、验收提交或会话恢复逻辑；2（内部修复运行/控制状态偏差）、4（恢复会话的验收控制归属）没有继续修改。它们的来源尚不足以认定仅由兜底接入导致，保留原记录。

1 的实际来源：UI 生成池写动作已经结束，停服提示可能来自独立预览 watch，planningState.connection 仍 healthy。因此只监听 Planning connection 的临时补丁未通过 Electron，已撤掉（useApplicationPlanningRuntimes 无本轮改动）。最终在 AiChatPanel 同工作区/Planning thread 的底部实际连接 unavailable→healthy 边界，经 AppEntryPage/WorkbenchPage/LeftPanel 的 onReconcilePlanning 转交既有 Runtime single-flight AG-UI Get，自动投影 UI 池中断；不增加轮询或自动执行恢复。

UI Electron 正式回归夹具：/Users/yifei/Coding/xcodeagent_test/fallback-ui-reconnect-20261007。从真实需求/产品规划联合确认开始，UI 生成实际模型内容后停服。最终补丁加载后再次从原底部入口恢复 UI 并生成内容，再停服；证据 fixed-ui-reconnect-final-before-stop.json，源 4f9ad057-90a4-4c59-aca0-92fe0d4fb346/ui_confirmation/awaiting_user（池在后台运行）。保持同一 Electron 会话恢复服务，不点击卡片刷新、不重新打开项目，底部自动显示 UI 生成中断与重试。实际点击底部重试进入既有 ui_confirmation，Run 610a33ae-b7e3-4109-bd4f-8ab1035dcda3 恢复页面生成；最终 1/1，仍需手动进入计划阶段。自动投影与原节点恢复通过。

3：retryCurrentRecovery 成功同步且无失败候选时，同会话正常 running/awaiting_user 或无新执行的 completed 回执不再制造 NO_RECOVERY_ENTRY_ERROR。当前会话 completed 且没有新持有执行时清理该合成错误；其它恢复错误、业务失败和其它会话保留。新增真实 Hook/AG-UI 客户端回归覆盖待验收同步、其它 owner 拒绝、已完成同步，以及新失败不得借旧 completed 放行；仅既有 Attach/Get，无恢复写动作或验收提交。Electron 在 cleanup-normal 的真实待验收会话停服、恢复后连接入口清除，没有再次误报缺少恢复入口。完成后合成错误清理已实现；没有为验证它而修改原生验收/会话归属，完成残留错误的完整 Electron 路径本轮未重跑。

最终验证：pnpm build、pnpm test:connection-recovery（77 项）、pnpm test:application-planning-runtime、git diff --check、/health=ok 均通过。只改前端，不需 Python 编译；本轮未重跑完整两轮主流程，针对本次兜底改动验证。后端已恢复，停服观察脚本均已结束。产品文件：AiChatPanel.tsx、hooks/useWorkflowConversation.ts、pages/AppEntryPage.tsx、pages/WorkbenchPage.tsx、components/LeftPanel/LeftPanel.tsx；测试 workflowConversationRuntime.test.ts，文档 CODEBASE_INDEX.md 与本报告。

## 2026-10-07：完成后错误清理的 Electron 补测

按用户授权，只补“停服→恢复同步→正常验收完成→底部错误清理”，若遇到会话控制问题只记录，不修改。打开 cleanup-normal-20261006，当前实际验收阶段；只读核对 Run 180aa15f-5e19-4ec2-9b2d-1efbf56ca04a 为 acceptance/awaiting_user，lifecycle 持有 page_acceptance 待确认。实际停止 Backend supervisor/worker，Electron 底部 Backend 不可用/重试正常出现。恢复 Backend 后 /health=ok，连接入口自动清除，无 NO_RECOVERY_ENTRY_ERROR 误报。

完整收尾补测被问题 4 阻挡：恢复后的真实验收会话仅显示项目启动完成/待确认、自由输入及调试控件，没有正常“验收通过并完成”控制；AX 与实际截图一致。后端仍 awaiting_user，无法通过当前正常 UI 产生 finalize_project/completed。因此本次仅确认断服入口和重连错误清除通过，不能将“正常完成后清除残留错误”的完整路径判为通过。没有使用调试入口、手写 AG-UI 验收决定、编辑状态或修改会话归属绕过阻挡；没有修改产品代码。后端已恢复健康，保留待验收记录。此次只追加本报告，不重复代码构建和测试。

### 验收入口来源排查更正

磁盘核对：当前 acceptance 会话 fa164f4d-cbeb-4adc-b40f-c11bdf7e3abe，最后消息 Workflow runId=180aa15f-5e19-4ec2-9b2d-1efbf56ca04a、Graph thread=3c0daeca-2c7d-437e-90da-8f6c1b64dd1c，与 lifecycle 的 ownerSessionId/Run/Thread 全部一致；pendingInteraction=page_acceptance，extensions 尚无 acceptanceStatus=passed。最后消息没有 End 回执，也不是 conversation。不能继续称该验收会话归属错误。

来源是现有验收 UI 的显示/提交分离：derivePlanExecutionMode 对 page_acceptance 得到 awaiting_acceptance；shouldShowAcceptanceDecisionDock 在验收阶段且尚未版本验收通过时得到 true；AiChatPanel 的 !acceptanceAwaiting 条件隐藏聊天区 PlanExecutionDock（原生 onAccept=handleAcceptPreview）。另一套 AcceptanceDecisionDock 只在右侧 preview 面板内挂载，因此截图右侧为开发产物时没有验收按钮。其 handleAcceptanceApprove 仅修改前端 extensions.acceptanceStatus=passed，未提交 page_acceptance=accepted；后者由原生 handleAcceptPreview 负责。版本验收标记通过后，acceptanceAwaiting 变 false 才可能再次显示聊天区原生完成按钮，解释第二轮两步按钮操作可完成，而原补测仅看开发产物面板误判无入口。

上述函数/条件存在于改动前 HEAD，utils.ts、planExecutionMode.ts 相对 HEAD 无 diff，handleAcceptanceApprove 与 acceptanceAwaiting 定义逐段比较相同；本轮 restoreWorkbenchPresentation 对 awaiting_user 不替换 Workflow。故已定位的入口分布/两套确认行为不是本轮兜底接入新增，不修改产品代码。撤销此前“正确会话缺按钮即会话归属错误、无法通过正常 UI 完成”的确定归因；应先打开预览并继续原正常步骤再验证是否真正阻挡。再次现场复核遇到 Mac 锁屏，未能完成打开预览及收尾；完成后兜底错误清理的完整 Electron 证据仍未补齐。

### 解锁后正常验收收尾补测：通过

用户继续后，在真实 Electron 打开同一 cleanup-normal 项目及验收会话。启动卡“打开预览”在预览服务尚未运行时打开空白全屏窗口；通过 Window 菜单返回工作台，改用右侧既有“预览 → 服务状态 → 启动服务”，受控前端在 localhost:3000 就绪。内嵌预览访问 /page/cleanup-normal，实际显示“正常流程测试”和“用于验证清理后正常流程”。没有修改代码或写入工作流状态来绕过启动。

实际点击预览内“验收通过”，取消随后出现的提交推送弹层；关闭右侧面板后，聊天区现有“验收通过并完成”正常出现。点击并确认弹层，原生 AG-UI 验收继续执行，Run 5ff5b78a-6d20-450f-b912-2988eb967ea1 的 durable 终态为 finalize_project/completed。Electron 显示“已完成 用户验收”“已完成 完成项目”“工作流执行 完成”，自由输入恢复，底部没有连接失败、NO_RECOVERY_ENTRY_ERROR 或其他残留兜底错误。此次完成路径接续上一节已实际执行的停服与恢复，不额外制造合成错误；因此证明修复后该停服→恢复→正常完成路径通过，不声称现场再次验证了旧合成错误已存在时的清理分支（该分支由前述回归测试覆盖）。

以本节结果取代先前“正确验收会话没有正常完成入口/收尾被问题4阻挡”的结论；该入口的既有展示条件已查清，正常两步验收实际可完成。其他开发历史会话的控制表现和节点内部业务失败保留原观察，不扩大修复范围。本次仅追加本报告；git diff --check 通过，Backend /health=ok。未修改产品代码，不重复 build 或自动化测试，未推送远端。

## 2026-10-07：剩余三个兜底场景补测（恢复链均已覆盖）

范围：单测修复、审查内部修复、验收启动仍在执行时的停服；不测实体/API，不修改节点内部产品逻辑。使用 /Users/yifei/Coding/xcodeagent_test/fallback-remaining-20261007 隔离夹具。复制正式产物后重新经过需求联合确认、原有跳过 UI 选项、真实技术规划生成与确认、正常 Bootstrap、DAG 确认和 Build；复制工程缺少匹配模板状态时触发原门禁，工程及旧模板状态已备份至 /tmp/fallback-remaining-prebootstrap-20261007，再正常初始化，不伪造模板成功。

夹具条件：恢复目录移至 /tmp/fallback-remaining-recovery-20261007，并以目录链接提供原路径；真实恢复数据库和执行身份继续正常写入，仅使测试生成器 os.walk 正式产物快照不捕获其运行期写入，避开已记录的独立误判。此条件必须保留，不能将本轮称为原始单测业务通过。加入 recovery-repair.test.ts 的可修复错误期望以准备测试失败。最初 node_modules 链接被工作区边界拒绝，随后已替换为独立实际依赖目录；该失败属于夹具准备，不算目标场景覆盖。

单测修复兜底链：通过。真实测试生成检查通过，实际 pnpm 单测命令因 esbuild 构建许可失败，原流程进入 unit_test_repair。源 recovery-16e7c3f58d76/unit_test_repair/running 在修复模型实际输出后停服，证据 /tmp/xcodeagent-electron-recovery-20261006/unit_test_repair_inflight-before-stop.json。Electron 底部 Backend 不可用/重试可见；恢复健康后自动投影“工作台执行需要恢复/继续执行”。只点击该底部按钮，recovery-4a956a8d9e50 精确重入 unit_test_repair/running，源记录 interrupted，界面第一步为“正在执行 单元测试局部修复”，恢复模型实际继续读取当前测试、源码与失败日志。未使用调试节点或手写工作流状态。单测修复最终业务结果仍在观察，不把 running 记为测试通过。

单测修复收尾补充：确认恢复后的真实模型继续读取当前测试、源码和失败日志后，Electron 正常 Pause、End 并确认，显示计划已结束及自由输入恢复；未观察到单测最终通过，不能声称业务修复通过。独立本轮停服证据另存 /tmp/xcodeagent-two-round-20261006/remaining-unit-repair-before-stop.json。为覆盖后续阶段，仅将夹具 pnpm-workspace.yaml 设置为实际 esbuild 构建许可，原文件保留备份；新正常页面会话经过 DAG/Build 确认、原有跳过单测选项、实际集成依赖安装与构建通过、原有跳过性能测试选项，选择全量审查。

审查内部修复兜底链：通过。全量审查真实发现 axios 声明版本不满足本项目内置扫描规则的 1 个问题（锁定实际版本已满足规则）；Electron 点击“一键修复全部 1 项”后进入真正的修复执行。源 b2b413e5-30a0-49fe-9d8f-3d51539d349b/code_review/running 在修复模型实际输出后停服，证据 /tmp/xcodeagent-two-round-20261006/remaining-review-repair-before-stop.json。离线底部 Backend 不可用/重试可见，健康重连自动显示工作台恢复入口。只点击底部“继续执行”，recovery-33420deec95c/code_review/running，源 interrupted；保持已确认问题与修复动作，界面明确“正在修复审查问题”“不会重复执行代码扫描”。模型实际继续原修复逻辑，最终修复执行完成、前后端构建/重启检查通过，并停在进入验收的人工确认门；旧兜底清除。此场景既验证恢复链，也观察到原修复业务收尾。

验收启动中断：仍未完成。第一次点击进入验收前给夹具 dev 脚本加入 15 秒启动等待，但审查修复后的原有重启检查已经启动了前端；验收复用了真实就绪进程，Run 3b7e9b18-3bf0-41c7-93dc-3256b0337f1d 很快成为 acceptance/awaiting_user。观察脚本要求 acceptance/running 且当前启动 PID 的 stdout 明确处于等待、尚无 Local 就绪输出，因此没有停服，未误记为启动中断通过。该未命中观察脚本已停止。复制夹具重新 Bootstrap 后实际页面与菜单缺失，已从原正常夹具恢复两份真实源码（PageCleanupNormal/index.tsx、constants/menus.ts）再检查内嵌预览，标题与说明正确；该夹具补齐条件不能被算作原始 Build 通过。正常两步验收并取消推送，已完成当前计划。

下一次启动准备：通过现有 stop_frontend_project 基础设施 helper 精确停止本夹具 PID 86644，成功清理 PID 文件；不停止其它项目进程。夹具 dev 仍为 scripts/recovery-start-delay.mjs，实际脚本等待 15 秒后运行本工程 Vite，原 package.json 已备份；不修改后端启动实现或工作流状态。准备新正常会话继续时 Mac 再次锁屏，CUA 要求用户手工解锁；第三项未测完。后端 /health=ok，所有停服观察脚本已结束。本次未修改主项目产品代码，只追加报告与隔离夹具/观察脚本；未重复主项目 build、未推送远端。

### 解锁后的验收启动中断补测：通过

新正常会话再次经过正式 DAG/Build 确认、原有跳过单测选择、实际集成依赖安装与前端构建通过、原有跳过性能测试选择、Diff 审查通过。停在进入验收确认门时，只读确认 dev 脚本仍有 15 秒等待、旧 frontend.pid 不存在；本次没有审查修复后的启动进程可复用。Electron 正常点击“进入验收阶段”，观察脚本在 source Run 9a2b118d-9b40-417d-9862-ce811329b8a1/acceptance/running、真实前端 PID 89184 已出现、stdout 为 `[fallback-acceptance-start] waiting before vite` 且无 Local 就绪输出时停止 Backend supervisor/worker。界面同时显示“正在执行 启动本地预览”“启动前端服务 运行中”，没有进入待验收。证据：/tmp/xcodeagent-two-round-20261006/remaining-acceptance-starting-run.json 与 remaining-acceptance-starting-before-stop.json。

停服后 Electron 底部 Backend 不可用/重试可见。恢复健康后自动显示“工作台执行需要恢复/继续执行”；仅点击该底部按钮，recovery-a2252d94e917 精确重入 acceptance，源 durable 为 interrupted。既有启动器重新运行结构与服务检查；停服期间原前端子进程已自行就绪，启动器按原规则复用该真实健康进程（PID 89184），并产生新的“项目启动完成/已就绪”回执，最终 acceptance/awaiting_user。这里验证的是重新进入原启动检查，不声称强制重建前端进程；没有直接使用旧 preview_url 跳过启动器，也没有自动提交验收决定。

Electron 内嵌预览 /page/cleanup-normal 实际显示“正常流程测试”和“用于验证清理后正常流程”；正常版本验收通过、取消提交推送弹层、原生“验收通过并完成”及确认后，Run 1479fd29-48da-4e62-b231-4e5935a0b0cc/finalize_project/completed。界面显示用户验收、完成项目均已完成，自由输入恢复，底部无残留连接/恢复错误；lifecycle.activeExecutions 为空。

本轮收尾：三个此前未覆盖场景的停服入口、底部重试节点定位、原节点内部恢复链均已实际覆盖；审查修复及验收同时观察到正常收尾。单测修复仅确认重入与模型继续工作，随后正常 Pause/End，最终单测业务通过仍未验证；因此不能据本轮声称全部业务检查通过。实体/API 按用户要求不测。已恢复夹具原 dev 脚本，延迟脚本和人为失败测试移到 /tmp/fallback-remaining-prebootstrap-20261007 保留备份；仅停止本夹具前端进程，Backend 保持健康，停服观察脚本已结束。仅更新主仓库本报告，git diff --check 与 /health=ok 通过；未改产品代码，未重复主项目 build/自动化测试，未推送远端。
