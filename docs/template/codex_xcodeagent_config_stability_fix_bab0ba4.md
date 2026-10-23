# XCodeAgent 应用配置稳定性修复计划（Codex 实施版）

> 基线仓库：`YYYWYF/XCodeAgent`，提交 `bab0ba419826c6511a62d454cf74402c868c1693`（在此基线上检查实际代码后实施）。
>
> 依赖模板服务：`Hupy2118/springboot-template` 的 `1016` 分支，V4 Update 返回 `ENSURE_CAPABILITY_CONFIG`（`payloadRef` 指向 YAML/dotenv 片段，`parameters.format` 与 `capabilityIds`）。请以正在运行的服务构建版本和真实返回包为准，不要假定源码分支与运行进程一致。

## 0. 范围和硬约束

### 本期实施（P0 / P1）

1. **P0-1：当前数据模型与读写路径一致**。保证**按本次版本新建**的应用，其 `application.json` 在创建、配置提交、应用设置保存、Electron 再读取后都满足当前 Schema；不要误把临时表单或旧内存快照当磁盘完整配置。
2. **P0-2：配置卡不得白屏**。禁止在渲染和切换推断依赖时解引用不存在的 `auth`/`authorization`；数据异常时显示可操作的错误状态，不伪造应用真实状态。
3. **P0-3：V4 Ensure 完整性、提交门禁和正确增量合并**。新增 Capability 的受管字段必须在 WorkingCopy 中全部存在且结构正确，**然后**才能同批提交文件与 TemplateState；保留非受管内容及用户改动。
4. **P1-1：参数值更新路径精确**。使用完整 YAML 路径定位叶子，禁止同名叶子误写；dotenv/YAML 均只修改存在的键，不创建配置结构。
5. **P1-2：错误分类、阶段状态、失败恢复**。定位确切失败阶段、文件及配置路径，安全记录中不包含密钥值；能力安装完成而参数写入失败时，原操作可恢复、不会二次安装。
6. **P1-3：同一提案的安全重试**。同一 operationId 只关联同一提案，不允许用户修改字段后意外重放旧操作；处理成功后刷新磁盘态，防止 UI 持有旧配置。

### 明确不做

- **不兼容、迁移或修复旧 `application.json` / 旧 TemplateState / 旧版本已安装 Login/Authorization 配置。** 不增加历史文件回填流程，不对旧操作记录的 `changesDigest` 做兼容迁移；验收仅以本次版本全新创建的测试工作区为基准。
- **不做 P2**：不扩展自然语言可识别字段、别名及意图类型；只修现有自然语言配置卡的正确渲染、填写、提交与失败处理。
- 不修改数据库字段、登录前端 `REDIRECT_URI`/`LOGOUT_URI`/`RESPONSE_TYPE`/`ENV` 固定行为，不修改模板能力安装与参数修改的职责边界。
- 不使用 `REPLACE_MANAGED_FILE` 替换 `application.yml` / `application-dev.yml`；不将本地 `ApplicationParameterUpdater` 改为自动补键工具。
- 不默认清除当前工程 TemplateState、配置操作记录或重新 Generate。

## 1. 已核对的代码现状与问题

| 模块 | 当前实现 | 需要修复的事实/风险 |
| --- | --- | --- |
| `Frontend/src/main/managedWorkspace.ts` | `assertCurrentApplicationSchema()` 严格读 `authorization` 及 `api` | 新建/提交/重读的**完整结构**必须一致；错误不得被渲染层吞掉；不做历史兼容 |
| `Frontend/src/renderer/src/components/Welcome/utils.ts` | `buildApplicationSchema()` 为未启用权限时省略 `authorization.api`，启用时写入 | 这是合法的当前 Schema 形状；所有新建、设置及回读路径保持一致 |
| `Backend/app/services/application_config/schema.py` | Repository `load()` 经 `normalize_application_configuration()` 补字段；严格提交时校验 `authorization.api` | 不以读取归一化“偷偷迁移旧文件”；确保当前新建应用在提交时完整且一致 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/ApplicationConfigPrerequisiteCard.tsx` | `mergeSuggested()` 在 `useMemo` 渲染中直接访问 `result.auth.yht`、`result.authorization.api`；依赖确认回调用 `form.getFieldsValue(true) as ApplicationConfig` | 可能是**不完整的渲染视图或局部表单**；应以当前完整应用快照为基线，避免白屏与局部表单被误当全量配置 |
| `Backend/app/services/template_reconcile/service.py` | 只校验每个新增 Capability **至少关联一条** `ENSURE_CAPABILITY_CONFIG` | 未校验目标文件、所需键及 WorkingCopy 结果完整；可能先把 State 标记安装，再在后续参数写入失败 |
| `Backend/app/services/template_reconcile/yaml_config_merge.py` | 增量 YAML 节点文本合并 | 需加深层重复键、片段类型、注释/缩进、幂等与复杂结构冲突测试；拒绝不安全合并 |
| `Backend/app/services/application_parameter_updater/editors.py` | `edit_yht_yaml()` 先按完整路径验证，但**正则按叶子名全文件匹配并替换** | 例如其他树也有 `client-id` / `base-url` 会冲突；应按 YAML AST 完整路径和标量位置定点替换 |
| `Backend/app/services/application_config_orchestrator.py` | 先保存 Desired，再 V4 能力安装，后本地参数落盘；异常阶段和内部错误码可能退化为 `CONFIG_APPLY_FAILED` | 明确阶段、持久中间状态及重试；**不是**一个跨三阶段的原子事务，不得伪称全量回滚 |
| `Frontend/src/renderer/src/service/applicationConfigOperation.ts` | 本地 pending operation 键由 workspace/revision/requestId 构造；重试时不验证新表单提案是否与原提案相同 | 需要安全判等及恢复顺序，避免旧 operationId 与新 values 冲突 |

## 2. P0-1 当前 Schema 和应用读写一致性

修改范围：
- `Frontend/src/renderer/src/components/Welcome/utils.ts`
- `Frontend/src/renderer/src/components/SettingsPage/SettingsPage.tsx`
- `Frontend/src/renderer/src/components/SettingsPage/settingsDraft.ts`
- `Frontend/src/renderer/src/components/Welcome/applicationService.ts`
- `Frontend/src/renderer/src/service/applicationStorage.ts`
- `Frontend/src/main/managedWorkspace.ts`
- `Backend/app/services/application_config/schema.py` / `repository.py`（仅按需收敛当前 Schema，不做旧数据迁移）

实施：
1. 固定**当前新建项目**的 Schema 合同：`auth.enable`、`authorization.enabled` 均必填布尔；`authorization.initialAdministratorSubjects` 必填数组；**权限关闭时** `authorization.api` 可缺省；**权限开启时** `authorization.api.baseUrl`、`viewId` 必须是字符串并通过相应校验，`auth.enable=true`；启用 Login 的四个凭据必填。`schemaVersion` 暂保持当前 `6`，不为本期另起 Schema 或迁移历史。
2. 核查 `buildApplicationSchema()`、`saveWorkspaceApplicationConfig()` 和 Python Orchestrator `_merge()`，确保由新建/设置/配置卡提交产生的**持久**数据满足同一合同。已有的 `enablingAuthorization` 特判已会在首次启用时提交默认 API 值，**保留它，不重复添加**。
3. 由 Electron `workspace:read-application` 读取失败时，精确指出所缺键路径和配置错误类型。新版本合法配置必须通过；真正缺失根级 `authorization` 等应失败并提示，不自动创建假数据。验证 SettingsPage 保存后不通过过期草稿覆写已完成的认证/权限更新。
4. `saveApplication()` 中读取磁盘失败后回退内存写入的机制要加严格边界：仅允许**明确的新建工作区初始化过程**使用预期的首次写入行为；已存在工作区若读取失败，**不能**用过期内存快照覆盖磁盘，必须中止并报告错误。

验收：无 Login、Login、Login+Authorization 三类**全新**工程，新建→配置提交→设置保存→关闭重开→Electron 读取及 Python 读取均成功；任何一次编辑都不回退 `configRevision` / 已提交开关 / 管理员配置。

## 3. P0-2 配置卡白屏防护

修改：
- `Frontend/src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/ApplicationConfigPrerequisiteCard.tsx`
- 如需复用当前 Schema 校验，可增设一个**只读**的前端配置视图校验/转换函数（复用当前字段定义，不做持久迁移）。

实施：
1. `mergeSuggested()` / `mergeConversationConfigValues()` 不直接依赖 `application.auth` 和 `application.authorization` 存在。组件入口先判定根级必需对象；错误时显示 `<Alert>` / 有效的错误展示并禁用提交，而不是在 `useMemo` 里 `throw` 到全局。**不可**静默将非法持久配置伪造为合法配置。
2. 对当前 Schema 允许缺省的子对象（如关闭权限时 `authorization.api`）仅在表单**内存草稿**中补默认显示值；原始磁盘文件仍保持原有字段形式，真正启用时通过配置变更提交完整数据。
3. “确认推断依赖”回调不得使用 `form.getFieldsValue(true) as ApplicationConfig`。`getFieldsValue(true)` 只是局部表单数据：改用 `mergeConversationConfigValues(validatedCurrentApplication, form.getFieldsValue(true))` 构建完整草稿，再叠加已确认的推断项。
4. 避免 `useEffect(form.setFieldsValue)` 在推断确认/异步事件刷新时覆盖用户已输入的未提交凭据；仅对首次展示字段补缺值，对同一操作 ID 保留用户编辑。关闭/重开配置卡亦不能继续使用已过期配置快照。
5. 加最外层配置卡 ErrorBoundary 作为**最后**兜底，显示故障提示并保留主页面。根因修复仍是输入校验与正确数据合并，不是吞异常。

验收：当 `auth`、`authorization` 根对象缺失/类型错误时，配置卡可显示错误但整个应用不白屏；当 `yht`、`api` 属于合法的可选子对象缺失时，表单能显示对应默认值且不无故修改持久配置；勾选推断依赖、填写部分字段、再次确认不丢已填值。

## 4. P0-3 V4 ENSURE_CAPABILITY_CONFIG 完整性门禁

修改：
- `Backend/app/services/template_reconcile/service.py`
- `Backend/app/services/template_reconcile/protocol_v4.py` / `update_package_v4.py`（需时）
- `Backend/app/services/template_reconcile/executor_v3.py`
- `Backend/app/services/template_reconcile/yaml_config_merge.py`
- 相关测试 `Backend/tests/test_template_update_v4.py`

实施：
1. 将当前“`capabilityIds` 包含新增 Capability 即算通过”扩展为**Capability × 目标文件 × 受管路径**合同，不依赖模板 Engine 新增 HTTP 字段。使用现有 `payloadRef` 读取真实 YAML/dotenv 片段、校验且执行。
2. 新增 Login 必须确保：
   - `frontend/.env` 含 `PUBLIC_YHT_CLIENT_ID`、`PUBLIC_YHT_AUTHORIZE_URI`、`PUBLIC_YHT_END_URL`、`PUBLIC_YHT_LOGOUT_SHARE_JS`；
   - `application.yml` 含 `bee.auth.{client-id,private-key,public-key,center-public-key,center-uri}` 和 `bee.auth.login.{authorize-uri,redirect-uri,filter-patterns}`；
   - `application-dev.yml` 含 `yht.authentication.{client-id,private-key,public-key,center-public-key,center-uri,authorize-uri,redirect-uri}`。
3. 新增 Authorization 必须确保：
   - `application.yml` 含 `yht.api.user-query-path`；
   - `application-dev.yml` 含 `yht.api.{base-url,view-id}`；
   - 若 Authorization 依赖而自动安装 Login，还需满足 Login 的全部要求。
4. 校验操作目标白名单、`format`、`capabilityIds`、是否与 `newly_installed` 匹配；**验证 Payload 的实际节点路径**（不只看 Operation 声称的 Capability）。冲突、重复键、对象/标量类型冲突、不安全 YAML 一律拒绝。
5. 先在 `WorkingCopyStoreV3` 执行完整 Update 包；对合并后的 `frontend/.env`、`application.yml`、`application-dev.yml` **再次验证以上路径存在、类型正确**，并检查未知用户节点/值没有被覆盖；全部通过后，才向 WorkingCopy 写入 next TemplateState、调用 `apply_working_copy_v4()`。
6. 在合法的本次新生成工程中，“已有叶子值与模板默认值不同”需保留用户值；但父级结构不符、不可解析或重复键报 `MANAGED_SURFACE_CONFLICT`。Ensure 重复执行必须字节幂等。**不扩展为对历史已安装 Capability 的迁移或补齐**。
7. 校验三个 Ensure 目标的提交原子性：任意一个 Ensure/后验字段校验失败时，任何代码文件和 TemplateState 都不应被持久提交，`stateSequence` 不增加；模拟写盘故障时保留现有 V4 journal 恢复语义。

注意：模板服务当前 `1016` 的 V4 对新能力已会生成 `ENSURE_CAPABILITY_CONFIG`，且 Payload 是 `yaml`/`dotenv` 片段，不是完整配置文件。**不要把它误改成 `parameters.entries` 或 `REPLACE_MANAGED_FILE`**。本次原则上仅修改 XCodeAgent；如联调发现模板实际返回包缺路径，记录缺口后另行对模板服务做最小修复，不在 XCodeAgent 中硬编码补结构。

## 5. P1-1 参数编辑必须按完整路径更新

修改：
- `Backend/app/services/application_parameter_updater/editors.py`
- `Backend/app/services/application_parameter_updater/service.py`
- `Backend/tests/test_application_parameter_updater.py`

实施：
1. 保留 `_BACKEND = 'backend/src/main/resources/application-dev.yml'`；不回退到 `application.yml`。
2. 重写 `edit_yht_yaml()`：使用 `yaml.compose(..., Loader=yaml.SafeLoader)` / AST 节点定位每个 `yht.authentication.*`、`yht.api.*` **完整路径**及对应标量的 `start_mark`/`end_mark`；将更新先在内存生成非重叠文本替换区间，再从后往前应用。不要用 `key = path.rsplit('.',1)[-1]` 的全文件叶子正则更新。
3. 保留键、缩进、原注释、无关文本和换行；保留安全的行尾注释；不改其他路径的同名字段。遇到重复键、YAML anchor/alias/merge-key、复杂多行标量、非块式或无法唯一定位的内容时**明确报冲突**，禁止猜测或全文件 dump。验证更新后完整 YAML 路径值与目标值完全一致。
4. `edit_dotenv()` 继续遵守“目标键必须事先存在，禁止新增”；对重复键、非法值报明确路径错误。`_changed_leaf_keys()` 也应按完整路径而不是叶子名记录结果，避免变更展示错误。
5. 参数写入失败恢复继续使用现有 before-image/journal；日志、诊断与操作记录中只保存路径/错误码/阶段，不记录密钥值或 before/after 字节内容。

验收：文件中含非受管 `other.client-id`、`other.base-url`，同时修改 `yht.authentication.client-id`、`yht.api.base-url` 只影响后者；原注释和无关区块字节不变；缺键时报 `CONFIG_STRUCTURE_MISSING`，不新增节点。

## 6. P1-2 配置操作状态、错误分类和失败恢复

修改：
- `Backend/app/services/application_config_orchestrator.py`
- `Backend/app/protocols/application_config_operation.py`
- `Backend/app/services/template_reconcile/service.py`（仅关联错误结构，不更改 V4 职责）
- `Frontend/src/renderer/src/components/SettingsPage/settingsConfigChanges.ts`
- `Frontend/src/renderer/src/service/applicationConfigOperation.ts`

实施：
1. 保留既有 `status` 枚举 `APPLYING | FAILED | APPLIED`，增加非敏感进度 `progressStage`：`DESIRED_SAVED`、`CAPABILITY_APPLIED`、`PARAMETERS_APPLIED`；每完成一阶段立即持久化，失败时另写 `failureStage`、`failureCode`、`failureTarget`（仅合法相对路径/受管字段路径，不包含值）。
2. 不把上游所有异常一律降级 `CONFIG_APPLY_FAILED`；明确分组：
   - 配置文件/Schema：`CONFIG_SNAPSHOT_INVALID`、`CONFIG_VALUE_INVALID`；
   - V4 包下载/格式/确保结构：`TEMPLATE_ENGINE_UNAVAILABLE`、`TEMPLATE_PACKAGE_CONTRACT_INVALID`、`CONFIG_STRUCTURE_MISSING`、`MANAGED_SURFACE_CONFLICT`；
   - 参数预检/写盘：`CONFIG_STRUCTURE_MISSING`、`CONFIG_TARGET_CONFLICT`、`CONFIG_APPLY_FAILED`、`CONFIG_RECOVERY_REQUIRED`。
   若底层已有固定错误码，复用已有枚举；不要未经检查全局改错误码。外部文案以实际 `failureStage` 为准，不能把 `application_parameter_update` 错误标为“模板能力代码安装失败”。
3. 提交顺序保留 `Desired -> V4 Capability -> ParameterUpdater`。这三个阶段**不是全局 ACID**：若 V4 已安装成功、后续参数更新失败，记录 `FAILED` + `CAPABILITY_APPLIED`，保留现场供原操作恢复；禁止 UI 提示“所有工程修改已回滚”。
4. 同一个 operationId 恢复时：依据记录 `targetConfigRevision` 与当前磁盘 Desired/TemplateState 核对；如安装已完成且所需结构完整，**跳过 V4 网络调用**，重新执行已有参数叶子写入并完成 `APPLIED`。若安装尚未完成则按 V4 journal 恢复/重试；不得靠内存 UI 的旧 installed flag 决定。
5. 按“先验证再提交”原则执行 V4（见第 4 节）。如果文件后来被外部删除/修改且本地无法安全写入，仅报告 `CONFIG_STRUCTURE_MISSING` / `CONFIG_TARGET_CONFLICT` 并保留 Failed；不提供旧工程自动迁移，也不擅自覆盖用户文件。
6. `configOperationFailureMessage()` 根据结构化 `failureStage`/`failureCode` 展示本次*实际*失败阶段；只有提交进入相关阶段才提示“工程变更未完成”。去掉重复前缀、重复句号和没有证据的“登录/权限代码未完成”概括。API/前端不得展示私钥。

验收：分别注入 V4 下载失败、Ensure 验证失败、V4 原子写盘失败、参数写入失败、UI 读取失败；错误展示与实际阶段一致，状态可追踪；重复同一请求不再二次安装 Capability 或递增 stateSequence。

## 7. P1-3 前端 pending operation 与表单重试

修改：
- `Frontend/src/renderer/src/service/applicationConfigOperation.ts`
- `Frontend/src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/ApplicationConfigPrerequisiteCard.tsx`
- `Frontend/src/renderer/src/components/SettingsPage/SettingsPage.tsx`

实施：
1. `applyConfigChanges()` 保留 `get -> retry/apply` 幂等流程，但 pending 操作须绑定**稳定提案指纹**（与后端 `changesDigest` 口径一致）；浏览器本地只能存 `operationId`、版本、请求 ID、digest，**不能存原始 changes / 私钥**。
2. 如果 pending 操作存在且 proposed digest 一致：`get` 返回 `APPLIED` 时先从磁盘重新读取应用，再结束；`FAILED/APPLYING` 时使用同一 ID 恢复，不创建新操作。
3. 如果 pending 操作存在但用户修改了提案：**禁止用旧 operationId 发送新值**；先读取旧操作阶段。旧操作如果仍有未完成的 Desired，必须显式完成/恢复旧操作后刷新最新 `configRevision` 才允许提交新提案；不得简单删除 localStorage 记录后从旧 revision 发新请求。
4. 最小实现可在 pending 不匹配时给出明确、不会丢 ID 的恢复提示并禁止提交。若产品要求“修改参数后仍能继续恢复”，可在本次 P1 内新增显式 `resume` 动作：仅使用已持久化 Desired 与 `operationId`，不回传旧密钥，后端严格校验阶段和修订号。**禁止在未实现安全恢复前自动清除 pending 状态。**
5. 正常提交成功后重读磁盘 `application.json`、用最新 revision/值刷新设置页和对话卡；无需再次调用模板 Generate/Update。

验收：同一提案重试只恢复一次；用户编辑失败提案后不会误用旧 operationId；成功后 UI 显示更新后的字段与 revision；刷新页面再试不会重复装 Login/Authorization。

## 8. 测试矩阵（Codex 必须补齐）

| ID | 测试场景 | 必须断言 |
| --- | --- | --- |
| T01 | 全新 Base，无 Login/Authorization | 创建/读回结构合法，设置页能打开，不调用 V4 |
| T02 | Base 只启用 Login | 三处 Ensure 目标存在；正确写入四个前端变量与七个后端 `yht.authentication` 值 |
| T03 | Base 同时启用 Login + Authorization | 五组目标按正确路径出现；`yht.api` 完整；应用状态和 TemplateState 匹配 |
| T04 | 已安装 Login 后新增 Authorization | 两个 YAML 目标 Ensure；保留 Login 的值和用户改动；V4 只调用一次 |
| T05 | 纯修改 URL/密钥/主题色 | 不调用 V4；仅更改存在的目标值；不新增键 |
| T06 | V4 包仅有一条 Login Ensure、缺其他目标 | 包在 State 提交前被拒绝，文件与 stateSequence 不变 |
| T07 | Ensure Payload 包含错误路径、格式、重复键或冲突 | 拒绝并恢复 WorkingCopy；不覆写非受管内容 |
| T08 | YAML 同名叶子位于两个不同父路径 | 仅目标完整路径变化；保留注释与其他叶子 |
| T09 | V4 成功、参数预检/写入失败 | record=`FAILED`，progressStage=`CAPABILITY_APPLIED`；原 ID 重试成功且不再次安装 |
| T10 | V4 执行中异常退出 | V4 journal 恢复；代码文件与 TemplateState 不处于半提交 |
| T11 | `auth`/`authorization` 根数据异常的配置卡 | 显示错误，不白屏，不静默伪造持久根结构 |
| T12 | 局部表单、推断依赖勾选与重复刷新 | 不丢用户编辑，完整配置合并正确，不误提交无关字段 |
| T13 | 旧 pending ID + 用户修改提案 | 明确阻断/先恢复原操作，不用旧 ID 提交新值 |
| T14 | 正常完成后再读 application | Electron/Python 读同一新生成配置均成功，UI revision 与磁盘一致 |
| T15 | 失败错误文案 | 明确 stage/code/path，且不泄漏 `privateKey` 等敏感值 |

> 本测试矩阵**不包含**旧工程/旧 operation 格式兼容和 P2 自然语言识别扩展。

## 9. 推荐实施顺序与提交边界

**Commit 1 — P0 前端读写与渲染防护：** 先修当前 Schema 写读一致及配置卡局部表单/白屏，补 T01、T11、T12、T14。确认不会误改磁盘。

**Commit 2 — P0 V4 Ensure 门禁：** 校验操作目标和 Payload，WorkingCopy 后验检查，先过 T02、T03、T04、T06、T07、T10。**禁止在缺结构时提交 TemplateState**。

**Commit 3 — P1 YAML 定点更新：** 完整路径索引、只改已有叶子、冲突诊断，补 T05、T08。

**Commit 4 — P1 状态恢复与前端重试：** 细化阶段/错误码、原操作幂等恢复、pending 提案指纹、UI 成功刷新，补 T09、T13、T15。

**Commit 5 — 集成验证：** 用模板 `1016` 的**实际运行版本**创建全新工程，完成 Base→Login→Authorization 和 Base→Login+Authorization 两条链路；记录 Build、单测、集成测、失败注入结果。

## 10. Codex 执行要求（可直接复制到任务）

1. 先核查当前工作分支是否仍等于或包含基线提交 `bab0ba4`，若代码已演进，以现有代码为准逐条对照计划，**不覆盖未提交的用户修改**。
2. 按本文件 Commit 1～5 的顺序实施；每阶段先写失败用例，再改最小逻辑，执行受影响测试并给出结果。
3. **只处理新版本新生成的工作区**；不要添加 `application.json`/TemplateState/operation 的历史迁移逻辑或“自动修旧工程”分支。
4. 模板 V4 操作保持 `ENSURE_CAPABILITY_CONFIG`（`payloadRef` YAML/dotenv 片段），XCodeAgent 不得自行生成缺失 YAML/.env 结构；**只修改已有参数值**。
5. 严格遵守文件级最小修改：不改数据库、不改 Login 前端固定跳转配置、不做 P2、不回退 V4 架构、不通过关闭校验规避错误。
6. 测试结束提供：变更文件清单、根因与修复映射、运行命令和结果、未完成项、至少一次端到端新工作区的状态/文件路径验收。若没有真实模板服务，注明集成验证未执行，不能用 Mock 单测代替宣称通过。
7. **不要将真实密钥、运行时凭据、完整 application.json 或 before/after 配置写入日志或测试报告。**

### 交付验收（全部满足才算完成）

- 全新应用创建、启用 Login、启用 Authorization、配置修改、设置保存和重新读取成功。
- 无白屏；`workspace:read-application` 不会因本次新创建的合法配置报 `authorization` 结构错误。
- 新增 Capability 时必需 Ensure 内容全部落盘后才提交 TemplateState；YAML 用户修改不被覆盖。
- 参数编辑仅修改已有的目标完整路径，不产生无关文件变更。
- 能力安装成功但参数更新失败后，可以安全重试同一操作，V4 不重复调用。
- 失败提示准确反映 `failureStage`/`failureCode`；无密钥泄漏；测试矩阵全部通过。
