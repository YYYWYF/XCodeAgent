# XCodeAgent Codex 实施方案 — 11 个配置字段、三入口统一与 Generate / V4 Update 衔接

## 0. 前置条件、基线及范围

- 仓库：`https://github.com/YYYWYF/XCodeAgent.git`，目标分支 `dev_agent`。核查时 tree SHA `8524e9b40084b68c9ca12d2bb573b61081fc6b72`；Codex 首先核对实际 HEAD。
- **先完成并验收模板服务的 `template_service_1016_codex_plan.md`**，随后按已确认的 Generate/V4 Operation 契约改造本项目；不要猜测或自行重新定义 Engine 合约。
- 必须同时覆盖三入口：①新建应用；②应用设置；③自然语言配置卡与提交链路。三条路径用同一组配置字段、同一套默认值和同一提交/校验规则，不得只改前端表单。
- 保留 `schemaVersion=6` 作为本期兼容路线，新增字段用 optional+default 迁移旧快照；如果现有 Schema 约束导致无法兼容，再统一上调版本并实现迁移，禁止悄悄拒绝旧 Workspace。
- 本期不处理 DB/数据源环境变量，不改 Profile 激活方式，不开放前端 `REDIRECT_URI`、`LOGOUT_URI`、`RESPONSE_TYPE`、`ENV` 的输入。
- 不支持已安装 Capability 的卸载、版本升级；配置值更新不得直接调用模板 Update。
- 已加密 `privateKey` 只作为字符串原样写最终开发工程，不重复加密；预览、日志、报错、配置事务摘要不输出敏感值。

## 1. 最终字段与默认值：固定 11 项

| application.json 字段 | 是否新增 | 使用端 | 工程投影 |
|---|---|---|---|
| `auth.yht.clientId` | 否 | 双端 | `.env:PUBLIC_YHT_CLIENT_ID` + `application-dev.yml:yht.authentication.client-id` |
| `auth.yht.privateKey` | 否 | 后端 | `yht.authentication.private-key` |
| `auth.yht.publicKey` | 否 | 后端 | `yht.authentication.public-key` |
| `auth.yht.centerPublicKey` | 否 | 后端 | `yht.authentication.center-public-key` |
| `auth.yht.centerUri` | 是 | 后端 | `yht.authentication.center-uri` |
| `auth.yht.authorizeUri` | 是 | 双端 | `.env:PUBLIC_YHT_AUTHORIZE_URI` + `yht.authentication.authorize-uri` |
| `auth.yht.redirectUri` | 是 | 后端 | `yht.authentication.redirect-uri`，**与前端动态 REDIRECT_URI 无关** |
| `auth.yht.endUrl` | 是 | 前端 | `.env:PUBLIC_YHT_END_URL` |
| `auth.yht.logoutShareJs` | 是 | 前端 | `.env:PUBLIC_YHT_LOGOUT_SHARE_JS` |
| `authorization.api.baseUrl` | 是 | 后端 | `yht.api.base-url` |
| `authorization.api.viewId` | 是 | 后端 | `yht.api.view-id` |

默认值（模板 `1016` 原样）：

```typescript
export const LOGIN_AUTHORIZATION_DEFAULTS = {
  centerUri: 'http://one-account-gateway.paasuat.cmbchina.cn/auth-server',
  authorizeUri: 'https://one-account-gateway.paasuat.cmbchina.cn/auth-server/auth',
  redirectUri: 'http://localhost',
  endUrl: 'https://one-account-gateway.paasuat.cmbchina.cn/auth-server/endsession',
  logoutShareJs: 'https://one-account-gateway.paasuat.cmbchina.cn/logout-share/js/logoutShare.js',
  baseUrl: 'http://one-account-gateway.paasuat.cmbchina.cn/api-server',
  viewId: '2c9f50c875c0dfce0175de6a4df1057e'
} as const
```

建议在前端集中维护这份默认值；后端为无前端运行时的工作区校验设置对应受测常量。新增字段默认值可被用户更改；旧的四个标识/凭据仍无真实可用默认值，启用登录时继续必填。

`authorization` 从现有 `{ enabled, initialAdministratorSubjects }` 扩展为 `{ enabled, initialAdministratorSubjects, api?: {baseUrl,viewId} }`；后端现有 `application_config/schema.py` 对 `authorization` key set 完全严格，必须同步修改，否则保存即失败。

## 2. 实施顺序（Codex 逐项完成、每项附测试）

### X1 — 统一配置 Schema 与兼容默认回填

涉及：
- `Frontend/src/renderer/src/typings/application.ts`：扩展 `auth.yht` 与 `authorization.api` 类型。
- `Frontend/src/renderer/src/components/Welcome/constants.ts`：7 个统一默认值，更新 `initialApplicationDraft`。
- `Frontend/src/renderer/src/components/Welcome/utils.ts`、`applicationService.ts`、配置加载/保存/创建适配逻辑：查找所有从草稿映射至 `application.json` 的地方，确保新增字段持久化。
- `Backend/app/services/application_config/schema.py`：扩展 `authorization` 允许的字段，严格校验属性类型与 URL（允许内网 http/https），继续校验启用能力依赖。
- `Backend/app/services/application_config/repository.py`、`service.py`：旧 `application.json` 缺省 7 个字段时提供读取兼容、提交时规范化；仅补充业务配置对象，不触碰工程 YAML/.env。

验收：旧 v6 JSON 可加载，创建/保存后字段完整；用户自定义值不被默认值重置；Authorization 未启用仍可不写 `api`，但在 UI 启用后默认值齐全。

### X2 — 三入口前端同步，重点防遗漏

**共用组件**：`Frontend/src/renderer/src/components/Welcome/ApplicationConfigEditor.tsx`。在 `ApplicationConfigFieldPath` 中添加 7 个路径，Form.Item 将所有新增字段按能力状态展示；`authorizeUri` 一项覆盖双端，不提供两个输入框。适度使用高级配置折叠，保持原有认证凭据区、权限开关和初始管理员语义。

**① 新建应用**：
- `Welcome/constants.ts#initialApplicationDraft`：默认地址预填。
- `Welcome/ApplicationForm.tsx`：`ApplicationConfigEditor.visiblePaths` 明确加上 7 个新字段。
- 新建时草稿 → `application.json` → Backend `compile_template_requested_config(include_application_config=True)` → Generate 的映射，逐段验证不丢字段。
- 默认值来自初始草稿，不只在 placeholder 展示，否则用户未修改时提交可能是 `undefined`。

**② 应用设置**：
- `SettingsPage/SettingsPage.tsx#createSettingsDraft`：完整回填，切换 Login/Authorization 开关不清空未修改值。
- `SettingsPage/settingsConfigChanges.ts`：包含 7 条差异路径；Login 关闭时不错误提交登录值，Authorization 关闭时不提交 API 变更。
- `service/applicationConfigOperation.ts`：沿用 `preview→apply→get` + `configRevision` CAS 和相同 `operationId` 重试。

**③ 自然语言 / 需求对话卡**：
- `AiChatPanel/components/WorkflowRunCard/ApplicationConfigPrerequisiteCard.tsx`：更新 `isApplicationConfigFieldPath`；`mergeConversationConfigValues` 对 `auth.yht` 和 `authorization.api` 采用嵌套 merge，不能用浅层覆盖丢掉已存在的 sibling 配置。
- `Backend/app/services/application_config_prerequisite.py`：扩展显式值设置意图识别，至少识别“将认证中心地址修改为 URL”“将登录授权地址改为 URL”“将权限用户服务地址改为 URL”“将用户视图 ID 改为 ...”以及前端登出、同步脚本等同义表达；输出完整明确的 `configIntents` / `suggestedChanges`，未知/模糊值弹配置卡选择，不擅自推断地址。
- `Backend/app/services/application_config_prerequisite.py#_has_config_request`、`business_request_for_routing`：将新增语句按配置意图正确路由，纯配置请求完成后不进入普通前端代码生成（避免兜底 grep）；混合请求先确认配置再继续业务要求。
- `Backend/app/services/application_config_change_resolver.py` 与 `Backend/app/domain/application_config_change.py` 主要保留旧布尔 Capability Change 契约；新标量配置值通过 `ConfigPrerequisiteAnalysis` + AG-UI 事务处理，避免硬塞进 `StrictBool` 的模型。
- 新增配置建议不包含敏感 privateKey 正文的日志/状态；如用户在自然语言中明示私钥，应以安全表单确认，不在 AI 普通消息里重复输出。

**三入口覆盖测试矩阵**：每新增字段至少 ①新建默认/手填，②设置差异/保存，③对话显式修改/卡片确认各一项；`authorizeUri` 再测双端同时变化。

### X3 — 后端配置变更白名单 / Generate 编译

- `Backend/app/services/application_config_orchestrator.py#_PATHS`：增加全部 7 个新路径；`_merge()` 保留类型校验、能力依赖、revision CAS 与幂等。
- `Backend/app/services/template_reconcile/desired.py`：扩展 `_YHT_CONFIG_FIELDS` 与 `compile_template_application_config()`，为 Generate 投影 5 个新增 `auth.yht` 字段与 `authorization.api` 2 字段；向模板 Generate 发送纯粹投影数据，不发送 `authorization.enabled`、管理员种子或无关数据库对象。
- `Backend/app/services/workspace_bootstrap/requested_config.py`：确认只有首次 Generate 使用 `include_application_config=True`，V4 Update 只发送 Capability。
- `Backend/app/services/application_config/schema.py`：同步新版后端 schema，兼容旧配置值。

接口目标：Generate 请求参照模板服务文档中 4.1 的 JSON；V4 更新请求仍严格 `{ protocolVersion:'4', currentTemplateState, requestedConfig:{capabilities}, mode:'APPLY' }`。

### X4 — 本地 ApplicationParameterUpdater 只修改值

文件：
- `Backend/app/services/application_parameter_updater/service.py`
- `Backend/app/services/application_parameter_updater/editors.py`
- 相关测试。

**目标文件**：
```
frontend/.env
backend/src/main/resources/application-dev.yml
```

.env 映射：
```python
_ENV_PATHS = {
    "PUBLIC_APP_NAME": "appName",
    "PUBLIC_APP_ICON": "appIcon",
    "PUBLIC_THEME_PRIMARY_COLOR": "theme.primaryColor",
    "PUBLIC_YHT_CLIENT_ID": "auth.yht.clientId",
    "PUBLIC_YHT_AUTHORIZE_URI": "auth.yht.authorizeUri",
    "PUBLIC_YHT_END_URL": "auth.yht.endUrl",
    "PUBLIC_YHT_LOGOUT_SHARE_JS": "auth.yht.logoutShareJs",
}
```

后端 YAML 使用完整路径映射：
```python
_YAML_PATHS = {
    "yht.authentication.client-id": "auth.yht.clientId",
    "yht.authentication.private-key": "auth.yht.privateKey",
    "yht.authentication.public-key": "auth.yht.publicKey",
    "yht.authentication.center-public-key": "auth.yht.centerPublicKey",
    "yht.authentication.center-uri": "auth.yht.centerUri",
    "yht.authentication.authorize-uri": "auth.yht.authorizeUri",
    "yht.authentication.redirect-uri": "auth.yht.redirectUri",
    "yht.api.base-url": "authorization.api.baseUrl",
    "yht.api.view-id": "authorization.api.viewId",
}
```

**执行约束**：
1. `preview(..., login_applied, authorization_applied)`，或在方法签名中显式传已安装 Capability set；由实际 State 判断，不是只看 `application.json` Desired。
2. Base-only 只修改应用基础 env；Login 安装后增加 Login 前后端值；Authorization 安装后再加 `yht.api.*`。
3. `edit_dotenv` 不得追加缺失 key；YAML 编辑器仅修改已存在的叶子值，父节点或叶子缺失返回 `CONFIG_STRUCTURE_MISSING` 类错误，不由本地编辑器创造结构。
4. 必须尽量保留 YAML 注释、缩进、其他 key、quote 风格和换行（可使用有范围的定点编辑，严禁序列化重写整份 YAML）。值含引号、冒号、反斜杠需正确转义。
5. `authorizeUri` 一次修改同时修改 `.env` 和 `application-dev.yml`；沿用 existing before/after journal 和 CAS，失败回滚。
6. journal 的密文/after-image 可能含凭据。保持 0600 权限，不在 UI/日志/操作摘要输出值，成功后清理；验证 recovery 正确。
7. 对模板服务旧版本输出的历史工程（缺 `application-dev.yml` 中的新结构）明确报兼容错误/引导通过受控迁移；**不要暗中生成新节点**。

### X5 — 适配 V4 Capability config Operation 与预览顺序

现状：`Backend/app/services/template_reconcile/protocol_v4.py#OperationV4` 只接收四种 Operation，`executor_v3.py` 不支持 YAML/env 配置 Ensure。

改造：
1. 增加模板文档所定义的 `ENSURE_CAPABILITY_CONFIG` Pydantic Operation，精准验证 target、payloadRef、format、capabilityIds、字节大小及本次新安装归属。
2. `Backend/app/services/template_reconcile/update_package_v4.py` 校验新 Operation；`executor_v3.py` 对内存工作副本执行 YAML 受管路径合并或 dotenv 确保键存在（既有值不覆盖）。
3. YAML 仅处理 Extension 声明的受管叶子；不覆盖其他配置、不改写整个文件、不改变非目标键值；重复 YAML key/父节点类型冲突拒绝；dotenv 同理。
4. 与现有 `transaction_v4.py` 统一提交，失败回滚所有受管文件及 State。
5. **修正 `ApplicationConfigOrchestrator.preview`**：目前在 `toInstall` 未执行时直接尝试本地 ParameterUpdater.preview，若目标节点尚未存在会报冲突。预览时先得出 `toInstall` 并跳过/延期缺失能力对应参数检查；安装成功后再进行严格文件参数预览与写入。
6. `apply` 继续顺序：保存 Desired → V4 Capability 更新（如需）→ 在新 State 中核对已安装集合 → 本地参数更新 → 状态完成；仅参数值修改不得发 V4 请求。
7. 已存在 `operationId`/`configRevision` 幂等逻辑保持；新装能力成功而参数值写入失败时重试应跳过 Capability Update，只恢复参数值更新。
8. XCodeAgent 配置提交和模板 V4 的事务跨阶段恢复按现有 operation record 设计；失败不报告成功，不产生 500/误判无变更。

### X6 — 测试、回归与交付

- 前端：复用已有 `Frontend/tests/applicationConfigCard.test.tsx`，新增三个入口 7 字段完整覆盖；按实际仓库脚本运行 `pnpm test`/typecheck，不假定依赖可访问。
- 后端：扩展 `Backend/tests/test_application_config_orchestrator.py`、`test_application_config_prerequisite.py`、`test_application_config_change_resolver.py`，并补充 ParameterUpdater 与 V4 executor 集成测试；命令按仓库实际 pytest 环境运行。
- 工程：先 Generate (Base-only) → 本地新增 Login → 更新登录 URI → 再新增 Authorization → 更新 viewId → 重试；断言只修改 `.env` 和 `application-dev.yml` 的指定叶子，且 `application.yml` 的公共值不动。
- 检查只有已有能力时调用参数值更新 **不会访问模板服务**。
- 断开模板服务验证纯参数值更新仍能成功（测试中 mock Client/网络调用）。
- 服务返回 V4 包但少了 YAML/env Operation 时不能默默记为成功；安装后必须校验必需的配置结构。

## 3. XCodeAgent 接口调试手册

### 3.1 新建应用 → Template Generate

XCodeAgent 在新建表单保存 `application.json` 并确认 `technical-plan.json` 后调用：

```python
requested = compile_template_requested_config(workspace_root, include_application_config=True)
await TemplateEngineClient(...).generate(requested)
```

**发给模板服务**：`POST /v1/generate`，HTTP body 就是模板文档 4.1 的请求。XCodeAgent `application.json` 的 `authorization` 是 `{ enabled, initialAdministratorSubjects, api }`，转换后 Engine 的 `applicationConfig.authorization` **只有 `{api:{baseUrl,viewId}}`**。

### 3.2 配置修改 → AG-UI Application Config

入口：服务配置 `/application-config/run`（前端无 `agentBaseUrl` 则 `/api/agent/application-config/run`），通过 `@ag-ui/client` 包装完整 AG-UI 请求；不是直接将以下片段当作完整 POST body。

```json
{
  "forwardedProps": {
    "applicationConfig": {
      "action": "preview",
      "workspaceRoot": "/path/to/workspace",
      "expectedConfigRevision": 6,
      "changes": {
        "auth.yht.authorizeUri": "https://one-account-gateway.paasuat.cmbchina.cn/auth-server/auth",
        "authorization.api.viewId": "2c9f50c875c0dfce0175de6a4df1057e"
      }
    }
  }
}
```

随后 `action=apply` 使用同一 `changes` 与 `expectedConfigRevision`，加入 `operationId`（8–128 字符）和可选 `requestId`；超时重试同一个 operationId。完成后可用 `action=get` + `operationId` 查询。这种业务值变更只调用本地 Updater，无模板 Update。

### 3.3 新安装 Login 或 Authorization → Template V4 Update

XCodeAgent 从 `application.json` 编译 Capability，只有本地已安装集合与 Desired 集合不同才调用：

```python
requested = {"capabilities": {"login": {"enabled": True, "config": {}}}}
await TemplateEngineClient(...).update_v4(current_state, requested)
```

经 Engine `POST /v1/update` 的完整 body：

```json
{
  "protocolVersion": "4",
  "currentTemplateState": {
    "schemaVersion": 3,
    "templateRevision": "2026.10.22.1",
    "requested": {},
    "effective": {},
    "installedArtifacts": {},
    "managedContributions": {},
    "stateSequence": 0
  },
  "requestedConfig": {
    "capabilities": {"login": {"enabled": true, "config": {}}}
  },
  "mode": "APPLY"
}
```

实际 `currentTemplateState` 从 Workspace 的 `.devagentstudio/template-state.json` 原样读取，**不可使用示例空 State 覆盖已安装状态**；新增 Authorization 时请求还必须包含已经安装的 Login。V4 不得携带 `applicationConfig`，返回包中的配置 Ensure 仅负责补齐结构，再由 Updater 写实际值。

## 4. 三入口遗漏扫描清单

Codex 对代码进行全文检索，并逐一修订/验证：

```bash
rg -n 'auth\.yht\.(clientId|privateKey|publicKey|centerPublicKey)|authorization\.initialAdministratorSubjects' Frontend Backend
rg -n 'ApplicationConfigFieldPath|visiblePaths|isApplicationConfigFieldPath|mergeConversationConfigValues|settingsConfigChanges' Frontend
rg -n '_PATHS|_YHT_CONFIG_FIELDS|_ENV_PATHS|_YAML_PATHS|compile_template_application_config' Backend
rg -n 'application-dev\.yml|application\.yml|PUBLIC_YHT_' Backend Frontend
```

至少核查：
1. 新建：`constants.ts` / `ApplicationForm.tsx` / `ApplicationConfigEditor.tsx` / 草稿编译 / Generate。
2. 设置：`SettingsPage.tsx` / `settingsConfigChanges.ts` / 前端服务 / Orchestrator。
3. 自然语言：`application_config_prerequisite.py` / `ApplicationConfigPrerequisiteCard.tsx` / `mergeConversationConfigValues` / 路由恢复。
4. 后端合约：`schema.py` 严格授权字段结构 / `desired.py` Generate 编译 / `_PATHS` 白名单。
5. 工程投影：`ApplicationParameterUpdater` / V4 `protocol_v4.py` + `update_package_v4.py` + `executor_v3.py`。

## 5. 必须通过的验收场景

| ID | 操作 | 预期 |
|---|---|---|
| C01 | 新建 Login 默认地址 | 5 个新 Login 配置预填正确；后端 YAML 和前端 env 完整 |
| C02 | 新建 Authorization 默认值 | 2 个 API 字段正确，管理员标识保留 |
| C03 | 修改 `authorizeUri` | 双端两个文件同步更新且不增加 key |
| C04 | 修改 `endUrl` | 仅 `.env` 对应叶子变化 |
| C05 | 修改 `centerUri` | 仅 `application-dev.yml` 对应叶子变化 |
| C06 | 修改 `viewId` | 仅已安装 Authorization 的后端叶子变化 |
| C07 | 自然语言“将登录授权地址改为...” | 命中配置确认卡，提交后不走前端 grep/代码兜底 |
| C08 | 自然语言“把应用名称改为...，并新增人员页面” | 配置先确认，之后业务任务继续；不重复触发模板安装 |
| C09 | Base → Login → Authorization | V4 各补齐 YAML/env 结构，然后值更新成功 |
| C10 | Login V4 成功、参数写入失败后重试 | 不重复装 Capability，不报 500，继续参数写入 |
| C11 | 纯配置值变更时模板服务不可用 | 更新仍成功、无需 Engine 调用 |
| C12 | Legacy 工程缺失新节点 | 明确迁移/不兼容提示，Updater 不新增节点 |
| C13 | YAML/env 有非受管注释、其他值 | 精确保留，无整体重写 |
| C14 | Login disabled | 不生成或修改 Login 参数；前端固定四字段行为不变 |

## 6. Codex 最终交付

按 X1→X6 分批提交，逐批列出修改文件、实现说明、运行测试/失败原因和覆盖的验收编号；提供可重放的 Generate、V4 Update、AG-UI 配置变更最小调试样例。不得自行改变模板服务协议或业务默认值。完成后给出“新增字段 x 三入口 x 工程投影”覆盖表，若某项未实现须明确列为阻断项，不得仅以 UI 可以录入为验收通过。
