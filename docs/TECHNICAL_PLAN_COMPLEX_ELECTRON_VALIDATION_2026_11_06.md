# 复杂应用技术规划 Electron 验证（2026-11-06）

## 验证环境与边界

- 直接操作已经运行的 DevAgent Studio Electron，未使用浏览器中的 Vite 页面替代。
- 后端为本地 8000 端口的生产应用 Graph、AG-UI、持久化 checkpoint、lifecycle 与片段存储；实际调用当前配置的外部模型 `deepseek-v4.1-flash`。
- 测试上游是受控且已确认的 RequirementSpec、ProductPlan 与八页 UI TSX，所有页面均通过当前 UI 产物校验。没有把上游 UI 模型生成当成本次证据。
- 通过 Electron 的“进入计划阶段”启动生成；后续使用原确认卡的“修改”、原“结束任务／继续生成”和断线“重新同步／继续生成”。不直接调用生成节点，不人工修改 checkpoint、lifecycle、资源锁或正式 JSON。
- 测试工程首次导入时，Electron 生成应用索引 ID，与夹具预设 ID 不同。生成开始前重新准备了匹配索引 ID 的上游夹具；不把这次初始化处理计为恢复。
- 两份工程均保留在待确认阶段，没有确认保存、生成模板或执行业务系统。业务规则核对是规划内容审计，不是库存扣减等业务实现的运行验收。

证据根目录：`/Users/yifei/Coding/xcodeagent_test/technical-complex-electron-20261106/`。`prepare.py` 仅准备上游；`watch.py` 只读归档片段；`audit.py` 核对业务覆盖、结构与拼接；`audit-semantic.py` 独立核对审阅修正。

## 复杂度与生成结果

应用包含设备台账、工单列表、工单详情、备件库存、库存流水、采购审批、供应商、统计看板八页，40 个业务动作。创建工单包含保存、重新查询和本地通知，业务执行槽位共 41 个。

重点覆盖工单六态流转、预占与释放库存、验收只扣减一次、取消只释放未消耗预占、采购审批及同一申请只入库一次、只读流水、跨模块选项查询。登录、权限和外部服务均关闭。

| 工程 | 实体 | Contract | Endpoint | 业务执行槽位 |
| --- | ---: | ---: | ---: | ---: |
| baseline | 9 | 6 | 44 | 41 |
| recovery | 9 | 6 | 40 | 41 |

两个工程均保持按业务能力划分 Contract。工单的 18 个接口始终属于一个 Contract。baseline 分为 6/6/6；recovery 按估算成本分为 6/5/6/1，单批不超过 6。批次不是额外 Contract。

baseline 目录修复增加了详情及采购选项读取；recovery 复用已有库存、供应商和工单查询，为采购页提供选项，没有新增这些选项 Endpoint。它们是两次独立模型规划，接口数量不必相同。

## 实际发现的问题及处理

1. **冻结字段引用了不存在的审计字段。** 模型把 `created_at/updated_at` 映射到未声明的工单实体字段，三轮校验均被阻止。字段批次现在明确列出允许的实体引用，冻结目录不得凭空添加实体审计字段。
2. **批次输出格式错误。** 真实模型出现去掉最外层 `properties`、将 `error_codes` 返回为对象数组。局部校验阻止错误提交，原预算内重试当前片段；提示补充了明确格式，成功邻居保留。
3. **列表没有后续操作所需的 id。** 库存列表无法为路径 `{id}` 写操作提供选择身份。新增 `api_record_identity.py` 检查同一集合的列表身份；冻结 Schema 出错时重建所属 Contract 的 Schema 决策，缓存完整 Contract 校验失败按该模块缓存未命中处理，不逐个浪费外层预算。页面提示增加关联 GET 的响应字段摘要。
4. **内置失败重试覆盖原业务要求。** 原“重新生成”控制文案曾改变分段输入指纹。现在严格识别内置失败审阅答案，保留原业务要求；用户明确修改仍更新输入。
5. **最终物化猜错实体来源。** 同名 `id` 被补成另一实体的 id，采购选项 id 还被补成采购申请 id。自动引用推断现在仅用于平台默认契约且要求来源唯一；模型契约最终物化保留其明确引用。baseline 已通过原“修改”流程纠正四处错误引用，接口身份、方法和路径未改。
6. **首次生成停止没有继续入口。** 共用结束服务原先仅为正式修订保存结束凭据。`EndedPlanningExecution.formalRevision` 现在允许首次创建为 null；所有规划生成在真实 writer 退出后保存同一结束凭据，继续沿原内容指纹、ActionPlan、lifecycle CAS 和业务节点重试。首次生成保持原阶段 cancelled，不授予开发准入。
7. **跨模块身份类型不一致。** 实体 JSON 的隐式 id 约定是 string，模型却在不同 Contract 混用 integer/string。共用生成规则已与 `entity_json_schema` 对齐，关联实体字段使用 text、API 身份使用 string。通过原“修改”流程修正当前稿，独立核对 63 处 Schema／参数身份类型与全部关联实体字段。
8. **正整数只写进描述。** 部分预占／采购数量允许 number，缺少下界。共用规则增加正整数的 integer/minimum=1、非负数值的 minimum=0；当前稿沿原审阅流程补齐具体属性。最后的独立审计结果见下节证据。

第 7、8 项是生成提示与当前稿的审阅修正，没有引入通用自然语言语义判定器。结构校验通过仍不能证明任意应用的全部业务语义正确。

## 停止、继续与进程故障恢复

实际 PLAN 线程：`0a35038a-ad31-49a5-a0c4-da73c6e6b600`。

| 步骤 | 运行身份 | 已观察结果 |
| --- | --- | --- |
| 修复后开始生成并点击结束任务 | `c357584d-2361-447f-8844-31a974ee2efd` | Durable cancelled，释放占用，保存原阶段结束凭据；Electron 出现原“已停止生成／继续生成”卡片 |
| 点击原继续生成 | `retry-7cf7b46f8bcf49e099720ffa39a3cc97` | 同一线程，firstNode 为 `technical_planning_generate`；停止时 8 个目录片段引用和输入摘要全部复用 |
| 生成后续 Contract 时 SIGKILL 8000 后端 | 原 PID 90947 | Electron 显示后端不可用；25 个已完成引用保持不变，包含大工单 Contract 及其全部批次 |
| 重启后端，点击重新同步 | 同一来源 Run | 后端工作区对账把原 Run 标记 interrupted，既有投影签发继续生成动作 |
| 点击恢复卡继续生成 | `retry-bb23b773ee5444e487445df6f59f3fc6` | 同线程、同原业务节点，25 个引用和输入摘要全部复用，最终经过 commit/review 并停在 awaiting_user |

恢复模式仍为既有 `full-context-node-reexecution`。客户端使用后端签发的恢复动作，服务端 `prepare_business_node_retry` 读取完整现场并登记新 Run。没有新建技术规划专用恢复端点、策略、手工 checkpoint 恢复或锁清理。此模式不创建旧 checkpoint replay 的 `RecoveryAttempt` 记录；以真实 Durable 执行、ActionPlan 路由及节点启动日志核验。

证据文件：recovery 工程下的 `before-stop.json`、`after-stop.json`、`stop-quiescence.json`、`after-stop-resume.json`、`before-crash.json`、`crash-injection.json`、`after-crash-resume.json`、`execution-proof-after-crash.json`、`assembly-verification.json`。

## 拼接与内容审计

恢复结束、人工审阅修改前的拼接证据单独保存在 `assembled-technical-plan.json`：

- 6 个完整 Contract 与生成缓存深度相等。
- 8 页的全部技术引用与页面片段深度相等。
- 18 个工单 Endpoint 与各批次正文逐项相等，接口无重复或遗漏，Schema 引用闭合。
- 目录实体和接口身份与正式稿一致，41 个业务执行槽位均有唯一有效绑定，读取／写入方法符合业务用途。
- 首次加载读取、跨模块采购选项查询、组合动作业务步骤均核对。
- 正式 Markdown／内部 JSON 完整提交后才清理当前片段目录，Graph 停在 `technical_planning_review` 的真实 interrupt。

这些检查证明拼接没有悄悄改写或丢失模型片段。随后发现的身份类型与数量约束问题属于模型内容质量，审阅后的稿与原始缓存存在有意差异，不能继续宣称二者深度相等。

当前稿最终审计通过，证据为 `final-semantic-verification.json`、`final-reviewed-technical-plan.json` 与 `verification.json`：63 处身份类型、4 个正整数输入（共 6 处正整数声明）、6 处非负字段约束全部通过。原始拼接稿与审阅稿的 64 处值差异已逐项核对，仅涉及身份类型／缺失主键引用和指定数量约束。8 页引用、40 个接口的业务定义及必填性保持一致；最终完整 Graph 校验无错误，真实 interrupt 仍为 `technical_planning_review`，确认状态为 `pending_user_confirmation`。

人工逐项审阅还核对了：预占不改变现存数、提交验收同步扣减现存／预占且不重复、通过不再次扣减、退回不重扣旧预占、取消不回滚合法出库、采购到货只按原申请入库一次、无需额外表单的动作没有新增必填输入，以及原枚举未扩充。

## 自动验证与未覆盖项

- 最终专项套件：`PYTHONPATH=Backend Backend/.venv/bin/python /Users/yifei/Coding/xcodeagent_test/technical-complex-electron-20261106/run-focused.py`，442 项通过，checkpointer 连接关闭后正常退出。日志 `/tmp/complex-electron-latest-442-regression.log`。
- 期间完整套件曾因旧测试仍要求“首次生成停止后没有继续入口”失败；已将该测试更新为当前行为并验证原节点／明确点击／取消态，重新跑完整套件通过。
- 修改的 Python 文件通过 `py_compile`；`git diff --check` 通过。
- `/health` 在后端运行时健康。SIGKILL 期间不可连接符合注入预期；一次检查与受控重启重叠产生连接失败，重启后复检成功。
- 最终已重启后端加载最新代码，`/health` 为 ok；Electron 再次实际显示六个 API Contract、最终待确认技术规划及原“修改／确认保存”按钮，保留在该确认门。
- 外部模型出现过一次长时间等待后的 `APIConnectionError`，原统一重试可继续；这不构成模型服务稳定性保证。
- 现有共用离线卡副文案仍短暂显示 `Failed to fetch`；主动取消时后端曾记录 `ASGI callable returned without completing response`。停止回执、片段静止、锁释放及继续均验证通过，这两项作为现有诊断／文案观察记录，未在本轮改写共用传输组件。
- 本轮未修改前端源代码，因此没有重复前端构建；UI 证明来自真实 Electron。
- 未覆盖真实业务实现、数据库操作、模板／开发节点、打包安装、其他操作系统、锁屏自动解锁。锁屏曾暂停最后一次 UI 操作，用户手动解锁后继续。

## 本轮涉及的代码

- 生成和校验：`agents/main/technical_plan_contract_batch_prompts.py`、`technical_plan_contract_batches.py`、`technical_plan_generation.py`、`technical_plan_generation_prompts.py`；`services/api_record_identity.py`、`api_contract_validation.py`、`project_plan.py`。
- 失败要求与共用停止：`graph/application_planning_interrupts.py`、`domain/application_lifecycle.py`、`services/planning_execution_end.py`。
- 测试：`test_api_record_identity.py`、`test_technical_plan_contract_batch_recovery.py`、`test_ended_planning_continuation.py`、`test_technical_planning_stage_retry.py`。
- 文档：本报告、`CODEBASE_INDEX.md`、`PRODUCT_UI_TECHNICAL_PLANNING.md`。

工作区原有其他未提交改动保留，未提交或推送代码。
