# 权限体系改造计划（修订版）

## 核心流程与事实来源

权限需求前移为：

```text
新建应用权限开关
→ RequirementSpec 确认权限业务逻辑
→ ProductPlan 建立业务 action
→ UiDesign 只设计业务页面
→ TechnicalPlan 将权限逻辑编译为资源点
→ 模板初始化自动生成固定权限管理页
→ EndpointDetail 细化数据权限
→ Build 生成授权运行时和业务守卫
```

事实归属固定为：

- 新建应用表单：声明是否涉及权限、是否需要运行态权限管理页，仅作为规划初始输入。
- RequirementSpec：权限业务逻辑的唯一产品事实来源。
- ProductPlan：保持产品行为，不定义资源键；授权相关 action 沿用 RequirementSpec 的稳定操作 ID。
- TechnicalPlan：把已确认权限规则技术化为资源点、角色模板和 endpoint 绑定，不得改变权限业务含义。
- 固定权限管理页：系统拥有，不属于业务页面，不进入 ProductPlan、UiDesign、EndpointDetail 或普通页面 Build。
- EndpointDetail：只细化 TechnicalPlan 已声明的数据权限执行算法。
- 生成应用后端：最终授权裁决者。

## 公共契约调整

### 新建应用配置

应用配置升级为当前 `schemaVersion: 3`，新增：

```ts
type ApplicationAuthorizationSeed = {
  enabled: boolean;
  runtimeManagementPageEnabled: boolean;
};
```

表单在“认证”区域后增加独立“权限控制”区域：

- “涉及权限控制”开关。
- “生成运行态权限管理页面”开关。
- 第二个开关只有在涉及权限时可选。
- 启用权限会自动启用身份认证；权限开启期间不能单独关闭认证。
- 关闭权限时同步关闭运行态权限管理页。
- 只启用认证、不启用权限仍是合法组合。

应用创建后，表单值写入 `application.json.authorization`，并写入首次规划请求。规划开始后，以已确认 RequirementSpec 为准，后续阶段不再从表单值推断权限逻辑。

### RequirementSpec 权限需求

新增正式字段：

```ts
type AuthorizationRequirements = {
  enabled: boolean;
  runtimeManagementPageEnabled: boolean;

  unauthorizedBehavior: {
    unauthenticated: "redirect_to_login" | "show_unauthorized";
    unauthorizedPage: "show_forbidden" | "redirect_to_home";
    unauthorizedOperation: "hide" | "disable";
  };

  pageRules: Array<{
    pageId: string;
    access: "public" | "authenticated" | "role_restricted";
    allowedRoleIds: string[];
  }>;

  operationRules: Array<{
    operationId: string;
    pageId: string;
    name: string;
    description: string;
    allowedRoleIds: string[];
  }>;

  dataRules: Array<{
    dataRuleId: string;
    entityId: string;
    name: string;
    description: string;
    roleScopes: Array<{
      roleId: string;
      scope: "all" | "own" | "organization" | "custom";
      ruleDescription: string;
    }>;
  }>;
};
```

约束：

- 未启用权限时，规则数组必须为空，不能生成资源授权。
- 启用权限时，每个页面必须明确 public、authenticated 或 role_restricted。
- `role_restricted` 页面和操作必须引用已存在的角色。
- 数据范围必须用产品语言说明，RequirementSpec 不出现字段名、SQL、`resourceKey` 或 `policyKey`。
- 角色的权限不再散落在 `user_roles[].permissions`，统一保存在 `authorization_requirements`。
- 运行态权限管理页开启时，角色模式确定为动态业务角色；未开启时使用固定角色模板。

### 固定权限管理页

当 `runtimeManagementPageEnabled=true` 时，模板确定性增加：

```text
pageId: system_authorization_management
pageKey: AuthorizationManagementPage
name: 权限管理
path: <menus.rootPath>/system/authorization
```

固定页面包含：

- 角色列表、创建、修改和停用。
- 页面、操作、数据资源矩阵。
- 成员列表和成员角色设置。
- 成员最终有效权限。
- 授权审计记录。
- 系统角色和系统资源只读保护。
- 明暗主题完整状态。

固定资源键由平台确定性注入：

```text
system.authorization.page
system.authorization.roles.read
system.authorization.roles.write
system.authorization.members.read
system.authorization.members.write
system.authorization.audit.read
```

该页面及资源只允许系统超级管理员和权限管理员访问。

页面所有权规则：

- 不加入 RequirementSpec `pages`，仅在权限需求章节展示为“系统固定页面”。
- 不加入 ProductPlan `pages`。
- 不生成 UiDesign。
- 不出现在业务页面设计入口和开发任务规划中。
- 不生成普通 PageImplementationContract 或 EndpointDetail。
- 模板初始化直接复制完整默认实现，不创建待 Agent 填充的占位页。
- 页面目录和固定 API client 被写入权限基础设施 manifest 的只读路径。
- Build、SmallTask、快速修改和普通 Agent 工具拒绝改动这些路径。
- 若人工修改导致文件哈希与系统模板不一致，Build 门禁停止并提示恢复默认模板，不静默覆盖用户文件。

### ProductPlan 与 TechnicalPlan 衔接

ProductPlan 保持 `product-plan.v4`，不增加资源键或角色矩阵。

确定性约束：

- RequirementSpec 中的 `operationRule.operationId` 必须成为对应 ProductPlan 业务 action 的 `actionId`，或 sequence 业务 step 的 `stepId`。
- ProductPlan 可以保留 `allowed_roles` 作为产品展示，但必须与 RequirementSpec page rule 一致。
- ProductPlan 不包含固定权限管理页。
- UiDesign 只处理 ProductPlan 业务页面。

TechnicalPlan 的资源点和绑定增加 `sourceRuleIds`：

```ts
type PermissionResourcePoint = {
  resourceKey: string;
  type: "page" | "operation" | "data";
  name: string;
  description: string;
  semanticDefinition: string;
  sourceRuleIds: string[];
  targetResourceRef?: string;
  policyKey?: string;
};
```

TechnicalPlan 校验必须证明：

- 每个 `role_restricted` page rule 产生一个页面资源点。
- 每个 operation rule 产生或复用一个操作资源点。
- 每个 data rule 产生一个数据资源点。
- 每条 RequirementSpec 权限规则至少被一个资源或绑定覆盖。
- 不允许产生无法追溯到 RequirementSpec 的业务资源。
- 固定权限管理资源只能追溯到 `runtimeManagementPageEnabled`，由平台注入，不能由模型改名或删除。
- TechnicalPlan 不能改变 allowed roles、数据范围含义或无权限行为。

## 分阶段实施与启动验收

### 1. 新建应用暴露权限选项

改造应用类型、默认值、创建表单、持久化和规划请求：

- 增加两个权限开关及联动校验。
- 将值写入 `application.json.authorization`。
- 规划请求明确携带“是否涉及权限”和“是否生成运行态权限管理页”。
- 打开已有工作区时只接受当前 schemaVersion 3，不添加旧结构兼容读取。

产物：

- 新建应用表单出现权限控制区域。
- 新建工作区的 `application.json` 出现 authorization 配置。
- 规划首轮消息可看到两个明确事实。

启动验收：

- 认证关闭时不能单独开启权限后再关闭认证。
- 权限关闭时运行态页面开关自动关闭。
- 认证开启、权限关闭的应用仍可创建和进入规划。
- `pnpm build`、后端 `/health` 和桌面开发态通过。

### 2. RequirementSpec 生成并确认权限逻辑

改造需求分析提示词、结构校验、Markdown、草稿编辑和确认 UI：

- 权限开启时，需求分析必须检查角色、页面访问、业务操作、数据范围和无权限行为。
- 信息不足时，将权限问题并入一次性澄清问题，不允许模型自行猜测高权限角色或数据范围。
- RequirementSpec Markdown 新增“权限需求”章节和“系统固定页面”说明。
- 需求确认卡增加结构化权限摘要，展示角色与页面、操作、数据规则。
- Markdown 或结构化编辑后重新执行权限规则闭合校验。
- `runtimeManagementPageEnabled` 可以在 RequirementSpec 确认前修订；确认后的值是后续唯一依据。

产物：

- `.xcodeagent/specs/requirement-spec.json|md` 包含完整 `authorization_requirements`。
- 勾选运行态页面时，文档显示固定权限管理页及其不可定制边界，但不把它写入业务页面清单。

启动验收：

- 权限开启但未说明数据范围时，流程停留在需求澄清。
- 未知角色、未知页面、重复 operationId 和空数据规则不能确认。
- 权限关闭时文档明确显示“不涉及应用级资源授权”。
- 保存草稿不等于确认，只有确认后的权限规则能进入 ProductPlan。

### 3. 固化 ProductPlan 和 UiDesign 边界

改造 ProductPlan 生成、校验和 UI 设计输入：

- 授权相关业务 action/step 使用 RequirementSpec 中的稳定 operationId。
- 页面 `allowed_roles` 必须与 RequirementSpec page rule 一致。
- ProductPlan 禁止出现固定权限管理页、资源键、策略键或技术绑定。
- UI 设计生成池只读取 ProductPlan 业务页面，因此固定权限管理页不会产生 UI 设计任务。
- UI skip 与普通 UI 确认都不影响固定权限管理页生成。

产物：

- ProductPlan 可以清晰展示业务页面的角色可见结果。
- `ui-designs.json` 中永远没有 `system_authorization_management`。

启动验收：

- 修改 ProductPlan actionId 导致 RequirementSpec operation rule 无法映射时，ProductPlan 不能确认。
- 勾选运行态权限页的应用仍只展示业务页面 UI 确认任务。
- UI 全部跳过后，权限管理页仍能在模板阶段生成。

### 4. TechnicalPlan 根据需求规则生成资源点

原子切换 TechnicalPlan 权限契约、API authentication 和 PageImplementationContract v2：

- TechnicalPlan 提示直接注入已确认的 `authorization_requirements`。
- 先由模型生成业务资源建议，再由确定性服务校验并注入固定系统资源。
- 删除 `permission_model`、`authentication.roles` 和角色型 `permissionBindings`。
- API Contract 的 authentication 只保留 `required`。
- PageImplementationContract v2 使用 page/operation 资源绑定。
- TechnicalPlan Markdown 展示“需求规则 → 资源点 → 页面/action/endpoint”的追踪关系。
- Markdown 同步后重新验证 RequirementSpec 覆盖率。
- 外部 API 模式在 authorization provider 中确认完整的资源、能力、角色、成员和审计 endpoint 映射。

产物：

- `.xcodeagent/plans/technical-plan.json|md` 包含业务资源、固定系统资源、`sourceRuleIds` 和覆盖摘要。
- 运行时编译的 PageImplementationContract v2 使用同一批 `resourceKey`。

启动验收：

- 删除任一需求规则对应的资源或绑定后，TechnicalPlan 不能确认。
- 模型尝试改变角色授权逻辑时，被 RequirementSpec 一致性校验拒绝。
- 未勾选运行态页面时，不生成系统管理资源和管理 API 绑定。
- 勾选后固定系统资源必然存在，模型不能改名或删除。

### 5. EndpointDetail 细化数据权限

- EndpointDecision 只接收 TechnicalPlan 已声明的数据资源和已确认 EntityDesign。
- 生成 collection filter、object check、执行点、字段/关系引用、失败模式和测试向量。
- 不允许新建资源、修改 policyKey、修改角色数据范围或 API Schema。
- 同一 policyKey 的多 endpoint 实现发生语义冲突时阻止 Build。
- 固定权限管理 API 属于平台运行时，不进入普通 EndpointDetail。

产物：

- endpoint JSON/Markdown 显示来源 dataRuleId、数据资源键、执行算法和正反测试。
- TechnicalPlan 数据规则变化能精确使相关 EndpointDetail 失效。

启动验收：

- 列表过滤位于分页后、对象修改发生在校验前、引用未知字段等方案不能确认。
- 没有数据规则的 authenticated endpoint 可以明确使用 enforcement=none 并填写原因。

### 6. 模板自动加入不可修改的权限管理页

模板初始化改为读取确认后的 RequirementSpec、ProductPlan、UiDesign 和 TechnicalPlan：

- 先按 ProductPlan 初始化业务页面占位。
- 当权限管理页开启时，再确定性加入固定页面源码和菜单项。
- 固定页面不使用页面模板选择，不读取 UiDesign，不创建业务开发任务。
- 生成权限资源 manifest、共享资源键、PermissionProvider 骨架、后端 provider 骨架和固定 API client。
- 数据库模式生成版本化 MySQL 权限 DDL。
- 外部 API 模式生成 provider adapter 骨架，不生成权限表。
- template-generation-manifest 增加权限页面文件、只读路径、模板哈希和 TechnicalPlan 权限指纹。

产物：

```text
frontend/src/pages/AuthorizationManagementPage/
.xcodeagent/authorization/permission-resources.json
.xcodeagent/authorization/foundation-manifest.json
backend/.../authorization/ provider 骨架
```

启动验收：

- 模板完成后立即可启动前后端。
- 菜单中出现“权限管理”，业务页面设计列表中不存在该页面。
- 页面在后端权限运行时尚未 Build 时展示固定“权限服务尚未就绪”状态，而不是崩溃。
- 修改固定页面后，模板完整性检查阻止后续 Build。
- 未勾选时不产生页面、菜单或管理资源。

### 7. Build DAG 和授权运行时

固定注入权限任务链：

```text
authorization.storage
→ authorization.core
→ business.authorization
→ authorization.runtime-api
→ frontend.authorization.runtime
→ frontend.authorization.guards
→ authorization.verification
```

运行态权限管理页已由模板生成，因此不再存在可修改该页面的 Build 任务。

数据库模式：

- 权限 DDL使用用户选择的数据库。
- 生成角色、资源、关系、审计和 revision 表。
- 生成 JIT 用户、角色并集、资源守卫、数据策略、缓存失效和管理 API。
- 初始业务角色来自 TechnicalPlan roleTemplates。
- 系统角色、系统资源和固定页面受只读保护。

外部 API 模式：

- 外部授权提供方是资源、能力、角色、成员和审计的事实来源。
- 生成无状态 adapter、revision 缓存、超时处理和后端守卫。
- 不生成权限表、本地角色计算或 JIT 成员表。
- 外部资源目录必须与 TechnicalPlan manifest 一致，否则默认拒绝。

产物：

- build-task-plan.json 显示固定权限任务及业务任务依赖。
- 生成应用出现完整后端权限核心、业务 endpoint 守卫和前端业务页面权限控制。
- 固定权限管理页只通过稳定 API 契约获得数据，不需要后续改代码。

启动验收：

- 数据库示例验证 401、403、页面/操作权限、多角色并集、数据过滤和授权 revision。
- 外部 API 示例验证资源同步、能力、角色/成员管理、审计、超时和未知资源拒绝。
- 无权访问权限管理页时菜单隐藏、路由 403、直接调用管理 API 仍返回 403。
- Build 或 SmallTask 尝试修改固定页面路径时任务校验直接失败。

### 8. 失效、版本提醒和完整回归

失效规则补充：

- 新建表单权限值只影响首次 RequirementSpec，RequirementSpec 确认后不再作为事实来源。
- RequirementSpec 权限开关、页面规则、operation rule 或 data rule 变化，使 TechnicalPlan 和相关下游失效。
- 运行态权限管理页从关闭改为开启，使 UiDesign 保持有效，但使 TechnicalPlan、模板权限基础、DAG 和测试失效。
- ProductPlan actionId 变化使对应 TechnicalPlan action binding 失效。
- TechnicalPlan resourceKey 或 policyKey 变化使相关 PageImplementationContract、EndpointDetail 和 DAG 失效。
- 固定页面模板版本变化使模板权限基础和授权前端验证失效。
- 纯业务代码错误进入 SmallTask，不回退权限需求。

版本控制里程碑：

- 固定权限管理页和权限骨架随模板基线一起审阅。
- 权限运行时 Build 完成后建议 `feat(auth): initialize application authorization foundation`。
- 权限业务模块验收使用独立模块提交建议。
- 所有提交仍需用户选择文件并确认，不自动 `git add .`、push、stash 或 amend。

完整测试样例：

- 固定角色、无管理页的数据库应用。
- 动态角色、默认权限管理页的数据库应用。
- 动态角色、完整外部授权提供方的外部 API 应用。
- 认证开启但资源权限关闭的应用。
- 静态应用申请动态权限时的前置拒绝。
- UI 确认和 UI skip 两条模板生成路径。

每个工作包均执行：

- 后端相关单元测试和 `py_compile`。
- `cd Frontend && pnpm build`。
- `scripts/start-backend.sh` 后检查 `/health`。
- `pnpm dev` 验收新建、需求确认或工作台界面。
- 从模板阶段开始，通过 `/api/projects/launch` 启动生成应用验收。
- 涉及目录、API、存储格式或边界变化时，同一工作包更新 `docs/CODEBASE_INDEX.md`。

## 默认决策

- 权限控制必须依赖身份认证。
- 未勾选运行态权限管理页时使用固定业务角色，不生成角色/成员管理 CRUD。
- 勾选后使用动态业务角色，并自动生成唯一的系统权限管理页。
- 固定权限管理页的布局、交互、路由、资源键和 API 契约均由平台拥有，业务规划和 Agent 不可定制。
- RequirementSpec 是权限业务逻辑唯一来源；TechnicalPlan 只做资源化和技术绑定。
- ProductPlan 保持 product-plan.v4，不保存 `resourceKey` 或 `policyKey`。
- 不读取旧权限字段或旧 application schema，不实现兼容分支。
