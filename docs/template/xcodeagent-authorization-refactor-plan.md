# XCodeAgent Authorization 配置与角色模型收敛修复方案

> 目标：同时修复首次 Application Planning 中 `authorization.enabled` 未正确写入 `application.json`，以及 `SYSTEM_ADMIN`、业务管理员角色、初始管理员 Subject 被 RequirementSpec 混用的问题。  
> 原则：**应用能力归 ApplicationConfig，业务授权归 RequirementSpec，系统权限初始化归平台确定性规则。**

---

# 第一章 方案设计

## 1.1 问题定义

当前存在两个相互关联的问题。

### 问题一：首次 Application Planning 缺少统一的 Capability Intent → Config Delta 链路

用户在首次创建应用时明确输入：

```text
启用权限控制
```

虽然现有 `access_control_intent.py` 可以识别：

```text
authorization.enabled = true
```

但首次 `application_planning` 没有统一消费 `resolve_application_config_changes()`，仍主要依赖 RequirementSpec 中是否出现 `restrictedPages / restrictedOperations` 反向判断是否需要权限能力。

结果可能出现：

```text
用户明确开启权限
        ↓
RequirementSpec 进行了权限相关澄清
        ↓
application.json 未修改
        ↓
configRevision 仍为旧版本
        ↓
TechnicalPlan 读取旧 application.json
        ↓
authorization_manifest 为空
```

### 问题二：系统权限角色与业务角色混入 RequirementSpec

当前 RequirementSpec/authorization contract 中仍存在：

```text
user_roles[].isSystemRole
user_roles[].isInitialAdminRole
authorization_requirements.initialAdminRoleId
```

导致三个不同概念混在一起：

```text
SYSTEM_ADMIN            系统权限管理员角色
Business Role           用户业务角色，例如人员管理员
Initial Admin Subject   首次启动时真实管理员用户
```

业务需求中的“管理员”可能被提升为系统管理员；同时需求阶段还需要选择“初始管理员角色”，与 `application.json.initialAdministratorSubjects` 重复表达同一个初始化目的。

---

## 1.2 目标边界

修复后明确四个职责边界。

### ApplicationConfig：应用能力事实

唯一事实源仍为：

```text
.xcodeagent/application.json
```

权限相关核心字段保持：

```json
{
  "auth": {
    "enable": true
  },
  "authorization": {
    "enabled": true,
    "initialAdministratorSubjects": ["user001"]
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `auth.enable` | 是否启用认证基础能力；权限开启时由平台依赖规则自动开启 |
| `authorization.enabled` | 应用是否启用 RBAC 权限能力 |
| `authorization.initialAdministratorSubjects` | 首次启动时绑定到系统权限管理员角色的真实 Subject |

RequirementSpec 不得覆盖这些字段。

### RequirementSpec：业务授权事实

RequirementSpec 只描述：

```text
有哪些业务角色
哪些页面受控
哪些操作受控
哪些业务角色默认拥有这些业务资源
```

目标结构：

```json
{
  "user_roles": [
    {
      "id": "personnel_admin",
      "name": "人员管理员",
      "description": "负责人员信息维护"
    }
  ],
  "authorization_requirements": {
    "restrictedPages": [],
    "restrictedOperations": []
  }
}
```

删除 RequirementSpec 中：

```text
isSystemRole
isInitialAdminRole
initialAdminRoleId
```

业务角色中的“管理员”默认仍是普通 Business Role，不自动提升为 `SYSTEM_ADMIN`。

### 平台固定规则：权限控制面初始化

平台固定提供一个系统权限管理员角色。

逻辑名：

```text
SYSTEM_ADMIN
```

建议持久化 seed key：

```text
system_admin
```

固定职责：

```text
管理角色
管理成员与角色绑定
管理角色与资源绑定
访问权限管理页面及对应管理接口
```

明确禁止：

```text
SYSTEM_ADMIN 不默认获得任何业务 Page/Action/Endpoint 资源
新增业务资源也不得自动授权给 SYSTEM_ADMIN
```

平台只做：

```text
initialAdministratorSubjects
        ↓
role_member
        ↓
SYSTEM_ADMIN
        ↓
system_authorization_management
```

业务资源由用户后续通过业务角色在运行态配置。

### Subject：真实身份

`Subject` 是认证系统中的真实人员/账号，例如：

```text
user001
zhangsan
zhangsan@example.com
```

初始管理员不是一个新角色，而是：

```text
一个或多个 Subject
        ↓
初始化绑定
        ↓
SYSTEM_ADMIN
```

同一个 Subject 可以同时拥有系统角色和业务角色，但角色本身不合并：

```text
zhangsan
 ├── SYSTEM_ADMIN
 └── personnel_admin
```

---

## 1.3 目标权限模型

```text
                         Subject
                            │
                     role_member
                            │
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼
        SYSTEM_ADMIN                 Business Role
       平台固定系统角色               RequirementSpec
              │                           │
              │ role_resource             │ role_resource
              ▼                           ▼
 权限管理控制面 System Resource       Business Resource
                                  Page / Action / Endpoint
```

其中：

```text
SYSTEM_ADMIN → 仅系统权限管理资源
Business Role → 仅显式授权的业务资源
```

---

## 1.4 Capability Intent 与配置提交设计

首次创建和 Formal Revision 必须统一使用同一套“配置目标解析”能力。

统一逻辑：

```text
用户输入
   ↓
Capability Intent Resolver
   ↓
ApplicationConfigChange[]
   ↓
补齐配置前置参数
   ↓
ApplicationConfigService.apply()
   ↓
application.json / configRevision++
```

### 首次 Application Planning

首次创建不创建 Formal Revision，但必须复用：

```text
resolve_capability_intents()
resolve_application_config_changes()
ApplicationConfigService
```

建议在 Graph State 中增加一个临时配置目标：

```json
{
  "pending_application_config_target": {
    "changes": [
      {
        "path": "authorization.enabled",
        "from": false,
        "to": true
      }
    ],
    "requiresInitialAdministratorSubjects": true
  }
}
```

该对象仅用于当前 planning checkpoint，不是新的权威配置文件。

当缺少管理员 Subject 时：

```text
authorization.enabled=true
        ↓
询问 initialAdministratorSubjects
        ↓
一次性提交 changes + subjects
        ↓
application.json
```

禁止先写 `authorization.enabled=true`、后补 Subject，避免产生不完整配置。

### RequirementSpec 推导出的权限需求

RequirementSpec 如果已经确认存在：

```text
restrictedPages 非空
或
restrictedOperations 非空
```

但 `application.json.authorization.enabled=false`，只能产生：

```text
requiresAuthorizationCapability = true
```

然后交给同一 ApplicationConfig 处理链。

RequirementSpec 自己不得直接修改 `application.json`。

如果用户明确关闭权限，但 RequirementSpec 又存在权限规则，则作为配置冲突阻断，不允许进入 ProductPlan。

---

## 1.5 初始管理员澄清设计

需求阶段只保留一个用户可见问题：

```text
初始权限管理员

请输入认证系统中真实存在或可预配置的管理员 subjectId；
该用户首次启动后可进入权限管理页面配置角色、成员和业务资源权限。
```

删除：

```text
authorization_initial_admin_role
“哪个业务角色作为初始系统管理员”
“是否将业务管理员与系统管理员放在一起”
```

不再询问业务角色是否与系统管理员合并。

---

## 1.6 authorization_manifest 目标结构

当前 manifest 将 `isSystemRole / isInitialAdminRole / initialAdminRoleSeedKey` 混入业务角色合同，建议升级 manifest schema。

建议：

```text
authorization-manifest.v3
```

核心结构：

```json
{
  "schema_version": "authorization-manifest.v3",
  "resources": [],
  "bindings": {
    "pages": [],
    "actions": [],
    "endpoints": []
  },
  "systemAuthorization": {
    "adminRoleSeedKey": "system_admin",
    "managementResourceKey": "system_authorization_management"
  },
  "defaultRoleAuthorization": {
    "roles": [
      {
        "roleSeedKey": "personnel_admin",
        "name": "人员管理员",
        "description": "负责人员信息维护"
      }
    ],
    "roleResourceGrants": []
  }
}
```

字段职责：

| 字段 | 职责 |
| --- | --- |
| `systemAuthorization.adminRoleSeedKey` | 固定系统权限管理员 seed key |
| `systemAuthorization.managementResourceKey` | 固定权限管理控制面资源 |
| `defaultRoleAuthorization.roles` | RequirementSpec 中的业务角色 |
| `defaultRoleAuthorization.roleResourceGrants` | 业务角色到业务资源的默认授权 |
| `initialAdministratorSubjects` | **不复制到 manifest**，继续只由 `application.json` 持有 |

`SYSTEM_ADMIN` 的管理资源授权由 manifest compiler/platform 确定性产生，不允许模型生成。

---

## 1.7 必须建立的硬约束

进入 ProductPlan 前必须满足：

```text
不存在 pending_application_config_target
```

若本轮目标要求：

```text
authorization.enabled=true
```

则必须满足：

```text
application.authorization.enabled == true
application.auth.enable == true
application.authorization.initialAdministratorSubjects 非空
```

TechnicalPlan 必须满足：

```text
technicalPlan.sourceConfigRevision
==
application.json.configRevision
```

权限开启时：

```text
SYSTEM_ADMIN 只拥有 system_authorization_management
SYSTEM_ADMIN 不出现在任何业务资源默认 grant 中
```

---

# 第二章 按阶段实施步骤

## 阶段一：收敛 Authorization Domain Contract

### 目标

先切清系统角色、业务角色、Subject 三类概念，避免后续 Config 流程继续依赖错误字段。

### 主要改动

重点文件：

```text
Backend/app/services/requirement_spec.py
Backend/app/agents/main/requirements_analyzer.py
Backend/app/graph/nodes/requirements.py
Backend/app/workspace/spec_documents.py
docs/AUTH.md
```

调整：

```text
user_roles[]
  保留：id / name / description
  删除：isSystemRole / isInitialAdminRole

authorization_requirements
  保留：restrictedPages / restrictedOperations
  删除：initialAdminRoleId
```

删除需求澄清：

```text
authorization_initial_admin_role
```

删除“业务管理员是否作为系统管理员”的逻辑。

`SYSTEM_ADMIN` 改为平台常量，不进入 RequirementSpec。

### 验收标准

1. RequirementSpec 校验不再要求 `isSystemRole / isInitialAdminRole / initialAdminRoleId`。
2. 用户需求存在“人员管理员”等业务角色时，生成结果只是 Business Role。
3. 需求阶段不再出现“选择初始系统管理员角色/是否合并管理员角色”的问题。
4. RequirementSpec JSON/Markdown 中不再出现上述三个旧字段。
5. 现有 RequirementSpec 权限规则仍能校验 `defaultGrantedRoleIds` 必须引用真实业务角色。

---

## 阶段二：统一首次 Application Planning 的 Capability Intent → Config Target

### 目标

解决本次 `authorization.enabled` 未写入的核心问题。

### 主要改动

重点复用：

```text
Backend/app/services/access_control_intent.py
Backend/app/services/application_config_change_resolver.py
Backend/app/services/application_config/service.py
```

接入位置重点检查：

```text
Backend/app/graph/application_planning_workflow.py
Backend/app/graph/nodes/requirements.py
Backend/app/graph/state.py
```

首次 Application Planning 在进入/执行 Requirements 时解析原始请求：

```text
“启用权限控制”
        ↓
ApplicationConfigChange(
  path = authorization.enabled,
  to = true
)
```

将未提交目标放入 checkpoint state：

```text
pending_application_config_target
```

Formal Revision 保留现有 `pendingApplicationConfigChanges`，两者底层共用同一 Resolver 和 `ApplicationConfigService`，但生命周期对象不混用。

### 验收标准

1. 新建应用输入“启用权限控制”时，能够稳定生成 `authorization.enabled: false -> true` target。
2. 不要求 RequirementSpec 先出现 `restrictedPages/restrictedOperations` 才识别权限开关。
3. target 在 ask_user 中断/恢复后仍存在，不因 checkpoint 恢复丢失。
4. “关闭权限控制”等显式反向表达同样生成正确 target。
5. Formal Revision 原有配置变更单测保持通过。

---

## 阶段三：统一初始管理员 Subject 收集与原子配置提交

### 目标

把“初始管理员”彻底变成 ApplicationConfig 的缺失参数，而不是业务角色设计。

### 主要改动

权限开启 target 存在且当前未配置真实 Subject 时，仅询问：

```text
authorization_initial_admin_subjects
```

用户提交 Subject 后调用统一配置服务，一次提交：

```text
authorization.enabled = true
auth.enable = true       # 由既有依赖展开
initialAdministratorSubjects = [...]
configRevision + 1
```

首次创建直接调用 `ApplicationConfigService.apply()` 或现有等价封装。

Formal Revision 继续通过 revision lifecycle 提交，但最终语义一致。

删除首次创建对 `initialAdminRoleId` 的依赖。

### 验收标准

给定初始配置：

```json
{
  "configRevision": 1,
  "auth": {"enable": false},
  "authorization": {
    "enabled": false,
    "initialAdministratorSubjects": []
  }
}
```

用户输入：

```text
启用权限控制
```

并提交：

```text
user001
```

必须得到：

```json
{
  "configRevision": 2,
  "auth": {"enable": true},
  "authorization": {
    "enabled": true,
    "initialAdministratorSubjects": ["user001"]
  }
}
```

同时满足：

1. 不允许产生 `enabled=true` 但 Subject 为空的中间正式配置。
2. Subject 不写入 RequirementSpec。
3. 多 Subject 输入能去重并保持合法值。
4. `current-user` 等占位值继续被拒绝。

---

## 阶段四：增加 ProductPlan 前配置硬门禁

### 目标

禁止“配置未提交但规划继续向下执行”。

### 主要改动

在 Requirements → ProductPlan 路由前增加确定性检查。

阻断条件包括：

```text
pending_application_config_target 未清空
```

或：

```text
RequirementSpec 存在业务权限规则
AND application.authorization.enabled == false
```

或：

```text
authorization.enabled == true
AND initialAdministratorSubjects 为空
```

发生上述状态时，必须回到配置澄清/配置提交，不允许进入 ProductPlan。

### 验收标准

1. 模拟 config target 尚未提交时，Graph 不得执行 ProductPlan。
2. `authorization.enabled=false` 且 RequirementSpec 含业务权限规则时必须阻断。
3. Config 提交完成后才能继续 ProductPlan。
4. 后续 TechnicalPlan 的 `sourceConfigRevision` 必须等于最新 `application.json.configRevision`。
5. 不再出现“TechnicalPlan 已生成但 application.json 仍是旧 revision”的场景。

---

## 阶段五：重构 authorization_manifest 与系统权限初始化

### 目标

移除 manifest 对 RequirementSpec 系统管理员字段的依赖，并落实“SYSTEM_ADMIN 只管理权限控制面”。

### 主要改动

重点文件：

```text
Backend/app/services/authorization_manifest.py
Backend/tests/test_authorization_manifest.py
TechnicalPlan Authorization 前端展示相关组件
```

建议 schema：

```text
authorization-manifest.v2 → authorization-manifest.v3
```

调整：

```text
删除：
defaultRoleAuthorization.roles[].isSystemRole
defaultRoleAuthorization.roles[].isInitialAdminRole
defaultRoleAuthorization.initialAdminRoleSeedKey

新增：
systemAuthorization.adminRoleSeedKey
systemAuthorization.managementResourceKey
```

平台固定：

```text
system_admin
    ↓
system_authorization_management
```

业务 role grants 只来自 RequirementSpec 的业务权限规则。

严禁：

```text
system_admin → 所有业务 resources
```

### 验收标准

1. manifest 可以在 RequirementSpec 完全不存在系统角色字段的情况下确定性编译。
2. `system_admin` 固定存在于系统权限初始化合同。
3. `system_admin` 只绑定 `system_authorization_management`。
4. 新增业务 Page/Action/Endpoint 后，不自动加入 SYSTEM_ADMIN grant。
5. Business Role 的 resource grant 与现有 RequirementSpec 权限规则一致。
6. manifest 重编译严格比较仍能发现漂移。

---

## 阶段六：运行态初始化与模板约束同步

### 目标

保证生成应用中的数据库初始化逻辑与新的权限合同一致。

### 主要改动

模板初始化规则统一为：

```text
1. 创建/确认固定 SYSTEM_ADMIN role
2. 创建/确认 system_authorization_management resource
3. SYSTEM_ADMIN → system_authorization_management
4. initialAdministratorSubjects → SYSTEM_ADMIN
5. 初始化业务角色
6. 按 manifest 初始化业务 role_resource
```

不得：

```text
给 SYSTEM_ADMIN 自动绑定业务资源
```

### 验收标准

1. 空数据库首次启动后固定存在 SYSTEM_ADMIN。
2. `initialAdministratorSubjects` 中每个 Subject 都绑定 SYSTEM_ADMIN。
3. SYSTEM_ADMIN 可进入权限管理页面。
4. SYSTEM_ADMIN 默认不能访问未额外授权的业务页面。
5. 业务管理员角色与 SYSTEM_ADMIN 可以绑定同一 Subject，但数据库中仍是两条独立 `role_member` 关系。
6. 重复执行初始化脚本保持幂等。

---

## 阶段七：前端与旧字段清理

### 目标

去掉用户可见的旧角色概念，确保编辑器、摘要、TechnicalPlan 展示与新合同一致。

### 重点文件

```text
Frontend/src/renderer/src/components/Welcome/RequirementSpecEditor.tsx
Frontend/src/renderer/src/components/Welcome/RequirementAuthorizationEditor.tsx
Frontend/src/renderer/src/components/Welcome/RequirementSpecSummary.tsx
Frontend/src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanAuthorization*.tsx
```

删除展示/编辑：

```text
isSystemRole
isInitialAdminRole
initialAdminRoleId
初始系统管理员角色
管理员角色合并
```

保留用户可见概念：

```text
业务角色
业务权限规则
初始权限管理员 Subject
```

### 验收标准

1. RequirementSpec 编辑器不再允许将业务角色标记为系统角色。
2. UI 不再展示“初始系统管理员角色”标签。
3. 初始管理员输入明确描述为真实 Subject/账号，而非业务角色。
4. TechnicalPlan 权限展示中清楚区分系统权限管理资源和业务资源。
5. 前后端 schema/类型检查全部通过。

---

## 阶段八：兼容清理与端到端回归

### 目标

删除旧合同残留，完成创建、修订、TechnicalPlan、运行态初始化的完整回归。

### 清理范围

全仓搜索并处理：

```text
isSystemRole
isInitialAdminRole
initialAdminRoleId
authorization_initial_admin_role
initialAdminRoleSeedKey
```

旧 RequirementSpec/TechnicalPlan 若需要兼容读取，只允许在边界层做一次迁移，不允许旧字段继续进入新的核心模型。

建议测试场景至少覆盖：

```text
A. 首次创建明确“启用权限控制”
B. 首次创建通过业务权限规则隐含需要权限能力
C. 首次创建明确关闭权限
D. Formal Revision 从关闭改为开启
E. 业务角色名包含“管理员”
F. 同一 Subject 同时绑定 SYSTEM_ADMIN 和业务管理员
G. 新增业务资源不自动授权 SYSTEM_ADMIN
H. TechnicalPlan sourceConfigRevision 与 application.json 一致
```

### 最终验收标准

端到端场景：

```text
新建应用
→ 用户输入“启用权限控制”
→ 输入初始管理员 user001
→ application.json configRevision: 1 → 2
→ authorization.enabled=true
→ auth.enable=true
→ initialAdministratorSubjects=["user001"]
→ RequirementSpec 仅生成业务角色/业务权限规则
→ TechnicalPlan.sourceConfigRevision=2
→ manifest 包含 system_admin → system_authorization_management
→ system_admin 不包含任何业务资源默认授权
→ 生成应用首次启动后 user001 可进入权限管理
→ user001 未额外绑定业务角色时不能访问受控业务资源
```

以上全部满足后，本次修复完成。

---

## Codex 实施约束

实施过程中统一遵守：

```text
1. 不让 LLM 直接修改 application.json。
2. application.json 仍是应用能力唯一事实源。
3. 首次创建与 Formal Revision 共用 Capability Resolver / ApplicationConfigService。
4. RequirementSpec 不保存系统角色与管理员 Subject。
5. SYSTEM_ADMIN 由平台确定性创建，不从业务角色推断。
6. SYSTEM_ADMIN 只获得权限管理控制面资源，不默认获得业务资源。
7. ProductPlan / TechnicalPlan 不得消费尚未提交的配置 target。
8. 每个阶段先补/改测试，再进入下一阶段；阶段验收失败不得继续叠加后续改造。
```
