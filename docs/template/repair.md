# XCodeAgent 自然语言调整应用配置修复方案

## 1. 问题背景

当前 `application.json` 中保存了应用级配置，例如认证、权限、数据源及其他模板能力配置。

应用创建时，这些配置由用户在创建表单中填写并写入 `application.json`。但应用进入工作台后，用户仍可能通过自然语言修改这些配置，例如：

* “给应用增加登录功能”
* “开启权限管理”
* “关闭埋点”
* “改用数据库”
* “启用审计能力”
* “这个应用不再需要登录”
* “增加工作流能力”

当前二次修改流程已经能够识别这些请求属于正式语义修改，并进入：

```text
formal_revision
    ↓
design_stage_revision / workbench_plan_revision
```

因此不需要重新增加一套新的自然语言路由。

当前真正的问题是：

> 二次修改流程只能识别“需要修改正式产物”，但没有统一表达“本次修改同时改变 application.json 中的应用级配置”。

目前登录、权限等配置只能通过 RequirementSpec、历史 request、模型输出等信息再次推导，导致多个事实源之间出现覆盖关系，例如：

```text
application.json             login=false
历史 request                 login=false
当前用户自然语言              login=true
RequirementSpec              login=true
```

最终不得不依赖复杂的覆盖规则判断谁具有更高优先级。

该机制需要调整。

---

# 2. 修复目标

本次改造目标是：

> 将 `application.json` 明确定义为“当前应用级配置的唯一事实来源”，并允许现有二次修改流程通过自然语言产生 Application Config Change，在正式修改确认过程中确定性更新 `application.json`。

本次改造不重新设计现有二次修改 Router。

保持现有：

```text
casual_chat
workspace_question
clarification
implementation_fix
formal_revision
```

以及：

```text
formal_revision
├── design_stage_revision
└── workbench_plan_revision
```

应用配置修改仍然属于 `formal_revision`。

只是在正式修改中增加：

```text
Application Config Change
```

作为本次 Revision 的一个影响结果。

---

# 3. 核心设计原则

## 3.1 application.json 是应用级配置唯一事实源

应用级配置不再由 RequirementSpec、TechnicalPlan 或历史自然语言独立决定。

例如：

```json
{
  "auth": {
    "enable": true
  },
  "authorization": {
    "enabled": true
  }
}
```

这些字段的最终状态只来源于：

```text
.xcodeagent/application.json
```

其他正式产物只能消费或投影这些状态。

例如：

```text
application.json
      │
      ├── RequirementSpec
      │      读取应用能力状态
      │
      ├── TechnicalPlan
      │      编译 template_capabilities
      │
      └── Template Engine
             根据能力变化更新模板
```

禁止再从历史 `source_request` 等文本反向覆盖当前应用配置。

---

## 3.2 自然语言可以修改应用配置，但不能直接修改 application.json

用户自然语言首先经过现有二次修改流程：

```text
用户自然语言
     ↓
DirectModificationClassifier
     ↓
formal_revision
```

在 Formal Revision 分析过程中识别：

```text
本次正式修改是否包含 Application Config Change
```

例如：

```text
“给应用增加登录功能”

↓

Application Config Change

auth.enable:
false → true
```

自然语言只产生结构化 Delta。

真正修改 `application.json` 必须由平台确定性代码完成。

禁止模型直接生成或重写完整 `application.json`。

---

## 3.3 Application Config Change 不是新的 Route

不要新增：

```text
application_config_change
```

作为顶层 Router 类型。

也不需要增加新的 Formal Revision Branch。

现有路由继续负责回答：

> 本次修改属于代码修复还是正式语义修改？

Application Config Change 回答的是另一个问题：

> 这次 formal revision 是否同时修改应用级配置？

因此两者是正交关系：

```text
Revision Route
+
Application Config Delta
```

例如：

```json
{
  "route": "formal_revision",
  "formalBranch": "design_stage_revision",
  "revisionType": "requirement_scope_change",
  "earliestArtifact": "requirement-spec",
  "applicationConfigChanges": [
    {
      "path": "auth.enable",
      "operation": "set",
      "from": false,
      "to": true
    }
  ]
}
```

---

# 4. 总体处理流程

自然语言二次修改流程调整为：

```text
用户自然语言
     │
     ▼
现有 DirectModificationClassifier
     │
     ▼
formal_revision
     │
     ▼
正式修改影响分析
     │
     ├─────────────────────────────┐
     │                             │
     ▼                             ▼
Formal Artifact Change      Application Config Change
RequirementSpec /           application.json Delta
ProductPlan / TP                   │
     │                             │
     └──────────────┬──────────────┘
                    ▼
             Revision Confirmation
                    │
                    ▼
             正式修改 Workflow
                    │
                    ▼
           正式产物最终确认
                    │
                    ▼
          Commit Application Config
                    │
                    ▼
             application.json
                    │
                    ▼
        TechnicalPlan / Template Engine
```

Application Config Change 必须跟随当前正式 Revision 生命周期。

---

# 5. Application Config Change 数据结构

建议定义统一的数据结构，而不是分别为登录、权限等能力写特殊逻辑。

例如：

```json
{
  "path": "auth.enable",
  "operation": "set",
  "from": false,
  "to": true,
  "reason": "用户要求增加登录功能",
  "evidence": "我想添加登录模块"
}
```

推荐最小字段：

```text
path
operation
from
to
reason
evidence
```

其中：

### path

对应 application.json 中允许自然语言修改的配置路径，例如：

```text
auth.enable
authorization.enabled
track.enable
apiTrack.enable
datasource.type
...
```

### operation

V1 建议只支持：

```text
set
```

避免模型执行任意 JSON Patch 操作。

### from / to

必须由平台读取当前 `application.json` 后确定。

模型不能自行声明当前值。

### evidence

保存触发本次变化的当前用户自然语言，用于审计和 Debug。

---

# 6. 增加 Application Config Change Resolver

现有自然语言 Router 不负责生成具体配置修改。

新增一个窄职责组件，例如：

```text
ApplicationConfigChangeResolver
```

职责：

```text
latest user request
+
current application.json
        ↓
ApplicationConfigChange[]
```

它只负责识别：

> 用户是否明确要求改变应用配置，以及期望状态是什么。

例如：

### 登录

用户：

```text
给应用增加登录功能
```

当前：

```text
auth.enable=false
```

输出：

```json
[
  {
    "path": "auth.enable",
    "operation": "set",
    "from": false,
    "to": true
  }
]
```

### 权限

用户：

```text
开启权限管理
```

输出：

```json
[
  {
    "path": "auth.enable",
    "operation": "set",
    "from": false,
    "to": true
  },
  {
    "path": "authorization.enabled",
    "operation": "set",
    "from": false,
    "to": true
  }
]
```

### 关闭登录

用户：

```text
这个应用不需要登录了
```

输出：

```json
[
  {
    "path": "auth.enable",
    "operation": "set",
    "from": true,
    "to": false
  }
]
```

如果当前仍启用了 authorization，则不能直接接受该 Delta，而必须由应用配置约束校验拒绝或要求同时关闭 authorization。

---

# 7. 改造现有 capability intent 逻辑

当前已有：

```text
resolve_capability_intents()
```

已经能够识别：

```text
增加登录
启用登录
开启权限管理
增加权限控制
```

这部分逻辑不应该废弃。

建议将其从：

```text
RequirementSpec 状态覆盖辅助逻辑
```

调整为：

```text
Application Config Change 识别能力
```

即：

```text
resolve_capability_intents()
        ↓
Capability Intent
        ↓
ApplicationConfigChangeResolver
        ↓
ApplicationConfigChange[]
```

同时需要调整当前状态来源。

当前 Resolver 不应该再依据：

```text
RequirementSpec.authentication_requirements.enabled
RequirementSpec.authorization_requirements.enabled
```

判断能力当前是否开启。

应该直接读取：

```text
application.json
```

例如：

```text
application.auth.enable
application.authorization.enabled
```

从而真正保证 application.json 是唯一事实源。

---

# 8. Formal Revision 中保存 Pending Config Change

Application Config Change 在用户正式确认之前不能立即写入 application.json。

现有 Formal Revision 明确要求：

```text
用户确认 formal revision 前
不能修改 canonical
```

application.json 同样应该遵守这一原则。

因此需要在 Revision 生命周期中暂存：

```text
pendingApplicationConfigChanges
```

例如：

```json
{
  "pendingApplicationConfigChanges": [
    {
      "path": "auth.enable",
      "operation": "set",
      "from": false,
      "to": true,
      "reason": "增加登录功能"
    }
  ]
}
```

该数据：

* 属于当前 Revision；
* 不属于 application.json；
* 不属于 RequirementSpec；
* 不属于新的长期事实源；
* Revision 完成或取消后即删除。

---

# 9. Application Config 的提交时机

不建议在 Router 判断出变化后立即修改 application.json。

推荐提交时机：

```text
formal revision 已确认
        ↓
对应正式产物完成修改
        ↓
最早承载该语义的正式产物确认
        ↓
提交 Application Config Change
```

对于 `design_stage_revision`，通常可以在：

```text
RequirementSpec 最终确认
```

之后提交 application config。

例如：

```text
用户：
增加登录功能
    ↓
formal_revision
    ↓
pending:
auth.enable=false → true
    ↓
RequirementSpec 修订
    ↓
用户确认 RequirementSpec
    ↓
原子更新 application.json
    ↓
auth.enable=true
    ↓
继续 ProductPlan / TechnicalPlan
```

这样：

* 用户取消 Formal Revision → application.json 不变；
* 用户放弃 RequirementSpec 草稿 → application.json 不变；
* RequirementSpec 正式确认 → Application Config 同步提交。

---

# 10. ApplicationConfigMutationService

建议把 application.json 的写操作统一收敛到一个服务：

```text
ApplicationConfigMutationService
```

职责：

1. 读取当前 application.json；
2. 校验 schemaVersion；
3. 校验允许修改的字段白名单；
4. 校验 `from` 是否仍等于当前值；
5. 校验字段之间的约束；
6. 应用 Delta；
7. 完整 Schema Validation；
8. 同目录临时文件写入；
9. `fsync + atomic replace`；
10. 返回更新后的 Application Config。

禁止不同 Workflow 自己操作 application.json。

---

# 11. 应用配置必须有字段白名单

自然语言不能修改 application.json 中任意字段。

建议定义：

```text
NATURAL_LANGUAGE_MUTABLE_APPLICATION_CONFIG
```

例如 V1：

```text
auth.enable
authorization.enabled
track.enable
apiTrack.enable
```

以后根据需求逐步增加：

```text
datasource.*
workflow.*
audit.*
notification.*
```

应用名称、内部 ID、schemaVersion 等系统字段不能通过该机制任意修改。

Resolver 即使返回了非法路径，Mutation Service 也必须拒绝。

---

# 12. 配置之间的约束必须由平台确定性维护

Resolver 只表达用户目标。

真正的配置约束由 Mutation Service 处理。

例如当前已经存在：

```text
authorization.enabled=true
        ↓
auth.enable 必须=true
```

同时：

```text
authorization.enabled=true
        ↓
必须使用支持权限体系的数据源

authorization.enabled=true
        ↓
必须存在 initialAdministratorSubjects
```

因此用户说：

```text
开启权限管理
```

不能简单只产生：

```text
authorization.enabled=true
```

平台需要扩展成：

```text
auth.enable=true
authorization.enabled=true
```

并检查其他必要条件。

这种依赖属于 Application Config Schema / Policy，不应该由 LLM 推理。

---

# 13. RequirementSpec 职责调整

改造后需要进一步明确：

## application.json 保存

应用是否启用某项基础能力：

```text
login
authorization
tracking
audit
workflow
...
```

## RequirementSpec 保存

这些能力在业务中的使用语义。

例如 Authorization：

application.json：

```json
{
  "authorization": {
    "enabled": true
  }
}
```

RequirementSpec：

```text
哪些角色存在
哪些页面受控
哪些操作受控
哪些角色能够访问
业务访问规则是什么
```

因此：

```text
authorization.enabled
```

只由 application.json 决定。

但：

```text
restrictedPages
restrictedOperations
role access rules
```

仍由 RequirementSpec 决定。

二者不能混在一起。

---

# 14. TechnicalPlan 调整

TechnicalPlan 中：

```text
template_capabilities
```

应该统一从 application.json 编译。

例如：

```text
application.json
auth.enable=true
        ↓
template_capabilities.login.enabled=true
```

```text
application.json
authorization.enabled=true
        ↓
template_capabilities.authorization.enabled=true
```

RequirementSpec 不再作为能力 enabled 状态的最终来源。

RequirementSpec 只负责提供权限业务规则等详细语义。

---

# 15. 删除现有复杂覆盖逻辑

完成改造后，可以逐步删除以下模式：

```text
历史 source_request
        ↓
解析 “认证：启用 / 不启用”
        ↓
覆盖 RequirementSpec.authentication.enabled
```

以及：

```text
agent_spec
existing RequirementSpec
initial application config
historical request
capability intent
```

之间复杂的 precedence。

目标收敛为：

```text
Application Capability State
        =
application.json
```

而自然语言只负责产生：

```text
application.json 的下一次状态变化
```

---

# 16. 与当前 authorization 特殊逻辑的关系

当前已经存在：

```text
authorization_config_conflict
```

以及：

```text
persist_authorization_configuration()
```

实际上已经实现了：

```text
业务权限需求
    ↓
application.json 权限配置更新
```

因此本次改造不应该再为 login 复制一套类似机制。

应该把现有 Authorization 专用实现逐步收敛到：

```text
ApplicationConfigMutationService
```

其中 Authorization Enable 只是一个配置 mutation 场景。

现有：

```text
persist_authorization_configuration()
```

可以逐步迁移成：

```text
apply_application_config_changes(...)
```

权限初始化管理员收集仍然保留为 Authorization 特有的前置条件。

---

# 17. 推荐的最终架构

```text
                    用户自然语言
                         │
                         ▼
             DirectModificationClassifier
                         │
                         ▼
                  formal_revision
                         │
                         ▼
               Formal Revision Analysis
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
       Artifact Changes     Application Config Changes
              │                     │
              │                pending only
              │                     │
              └──────────┬──────────┘
                         ▼
                用户确认正式修改
                         │
                         ▼
            design / planning workflow
                         │
                         ▼
                正式产物最终确认
                         │
                         ▼
          ApplicationConfigMutationService
                         │
                         ▼
                 application.json
                  CURRENT STATE
                         │
              ┌──────────┴───────────┐
              ▼                      ▼
       RequirementSpec         TechnicalPlan
       业务使用语义          template_capabilities
                                      │
                                      ▼
                               Template Engine
```

---

# 18. 典型场景

# 19. 实施步骤

## 19.1 第 1 步：事实源契约（已明确）

本步骤确立后续实现必须遵守的契约；仅完成文档和项目规范更新，不表示第 2–10 步的运行时代码已经完成。

| 数据 | 职责与权威边界 |
| --- | --- |
| `.xcodeagent/application.json` | 当前已提交的应用级配置唯一事实源，包括 `auth.enable`、`authorization.enabled` 等当前 Schema 已定义的配置。 |
| `pendingApplicationConfigChanges` | 当前 Revision 的待确认变更提案；不构成另一份当前配置。确认前不得覆盖 application.json，取消未提交修订时丢弃。 |
| RequirementSpec | 保存角色、受控页面、受控操作及其他业务使用语义；能力 enabled 字段若保留，只能作为应用配置的投影。 |
| TechnicalPlan | 消费应用配置并派生 `template_capabilities`；不得从需求文本或模型输出独立决定能力开关。 |
| TemplateState | 记录 Template Engine 已请求、已生效及已应用的模板事实；不负责决定用户期望的应用配置。 |
| 历史 request、`source_request`、`agent_note` | 提供上下文、来源证据和调试记录；不得作为配置回填或覆盖来源。 |

规划当前修订时，可用 application.json 加当前 Revision 的有效 pending delta 计算候选配置视图；该视图不作为另一份长期配置持久化，也不代表配置已提交。正式下游执行消费已提交的应用配置。

创建表单负责提供初始配置；后续自然语言只产生变更提案。配置提交必须由平台确定性代码在对应正式产物确认边界执行，并遵守当前 RequirementSpec 与 ProductPlan 的联合确认门。澄清回答、模型生成完成以及路由命中均不等于正式确认。

本契约不扩大自然语言可修改字段的范围，不将独立数据源目录、实体绑定等业务存储并入 application.json，也不新增路由、兼容读取或双写机制。字段白名单、提交服务、失败与重试语义由后续实施步骤落实。

## 19.2 第 2 步：ApplicationConfigChange Schema（已定义）

当前定义位于 `Backend/app/domain/application_config_change.py`，采用 Pydantic 严格模型，禁止额外字段。六个字段均为必填：

| 字段 | 当前约束 |
| --- | --- |
| `path` | 仅允许 `auth.enable`、`authorization.enabled`、`track.enable`、`apiTrack.enable`。同一类型定义导出 `NATURAL_LANGUAGE_MUTABLE_APPLICATION_CONFIG` 白名单供后续服务复用。 |
| `operation` | 仅允许 `set`，必须显式提供。 |
| `from` / `to` | 严格布尔值，不接受数字、字符串或 null；两者必须不同，已满足的目标不生成 Delta。Python 属性名为 `from_value` / `to_value`，对外序列化使用 `by_alias=True`。 |
| `reason` | 必填且不能只有空白的字符串，说明变更原因。 |
| `evidence` | 必填且不能只有空白的字符串，保留原始证据文本。 |

`ApplicationConfigChange.model_json_schema(by_alias=True)` 可导出字段结构的 JSON Schema；非空白和前后值不同等语义约束还需通过 Pydantic 模型校验。模型实例不可变，防止构造后绕过正常赋值校验。

Schema 通过不等于获准写入：`from` 与真实配置一致性、目标值的意图来源、权限依赖和管理员前置条件仍须由后续 Resolver / Mutation Service 结合 application.json 验证。本步骤未接入路由、Revision 存储或配置写入，未新增产品 API。边界测试位于 `Backend/tests/test_application_config_change.py`。

## 19.3 第 3 步：通用 Application Config Change Resolver（已实现解析核心）

`Backend/app/services/access_control_intent.py::resolve_capability_intents` 现在只接收当前用户请求，返回不可变的 `CapabilityIntent(path, enabled, evidence)`；不再接收 RequirementSpec 或读取其中的 enabled。识别覆盖登录、权限管理、普通埋点与接口埋点的明确启停表达，并排除已覆盖的否定命令、疑问、普通流程和界面细节修改。该组件采用确定性词法规则，不承诺识别任意自然语言；未命中的业务访问规则仍由已有业务权限分析处理。

新增 `Backend/app/services/application_config_change_resolver.py::resolve_application_config_changes(request, workspace_root=...)`：

- 只读该工作区 `.xcodeagent/application.json`，校验当前 schemaVersion 和目标路径的严格布尔值；不读历史请求或 RequirementSpec，不修改输入或文件。
- 以快照真实值生成 `ApplicationConfigChange`，过滤无变化项和重复目标，保留本轮原始证据。
- 启用权限时确定性补全登录目标；同一路径的相反指令、启用权限同时停用登录，均明确报错，不按匹配顺序覆盖。
- 数据源、初始管理员以及既有权限开启时关闭登录的最终组合校验，仍由第 6 步的 Mutation Service 负责；此处生成提案不表示允许提交。

本步复用原有 `has_explicit_capability_change` 路由判断，不新增 Route 或 Formal Branch。RequirementSpec 的调用点已适配新的意图类型；其现有启用投影与历史 request 覆盖逻辑仍待第 10 步统一删除。第 4 步已接入工作区 application.json 的实际读取；第 5 步已接入 Revision 暂存，第 6–8 步仍待实现。本步不将解析结果写入 application.json。

验证：`tests.test_application_config_change_resolver` 覆盖工作区 canonical 配置读取、启停、误判边界、无变化、冲突、依赖补全、非对象/损坏配置和 RequirementSpec 投影字段不参与当前值判定。扩展执行 `tests.test_application_revision`、`tests.test_requirement_authorization_contract`、`tests.test_requirements_confirmation` 共 80 项，其中需求确认模块有 10 项失败；以修改前 HEAD 的 access_control_intent / requirement_spec 模块在独立进程重跑，复现相同 10 项失败，集中于 `clear` / `requires_user_input` 状态及权限提示模式差异。本步骤不修改该既有确认行为。

## 19.4 第 4 步：Resolver 读取当前 application.json（已实现）

公开 Resolver 只接收当前用户请求与工作区根目录。它从唯一 canonical 路径读取 `application.json` 后，交由私有快照解析逻辑生成 Delta；调用方不能传入 RequirementSpec、历史 request 或伪造的当前配置快照。

缺失、无法解析、非对象 JSON、非当前 `schemaVersion`，以及白名单目标缺失或不是严格布尔值，都会以 `ApplicationConfigChangeResolutionError` 阻止生成提案。Resolver 始终只读文件，不写入 application.json；Revision 暂存和正式确认后的提交仍属于后续步骤。

## 19.5 第 5 步：Formal Revision 暂存配置 Delta（已实现）

`register_revision_impact` 仅在当前请求包含明确能力启停目标时，才从该工作区的 canonical `application.json` 解析 Delta，并以服务端计算结果写入 `PendingRevisionImpact.pendingApplicationConfigChanges`。影响确认批准后，`submit_revision_impact` 将同一组不可变 Delta 转入 `ActiveFormalRevision.pendingApplicationConfigChanges`；客户端或模型提供的 impact 内容不能伪造该集合。

这组数据仅属于当前 Revision 生命周期，既不是 application.json 的副本，也不触发任何配置写入。拒绝 impact、放弃 active Revision 或完成 Revision 时，相应 lifecycle 记录被清空，Delta 同步消失。第 6 步的 Mutation Service 才会在正式产物确认边界校验并提交它。

验证扩展至 `tests.test_application_revision`：明确“增加登录功能”的影响确认保存 `auth.enable: false → true`；批准后 active revision 保留同一提案；放弃 Revision 后 application.json 仍为 `false` 且不再存在 active proposal。

## 19.6 第 6 步：ApplicationConfigMutationService（已实现）

新增 `Backend/app/services/application_config_mutation.py::apply_application_config_changes(workspace_root, changes=...)`，作为 application.json 的集中 mutation 核心。它只接收严格的 `ApplicationConfigChange` 实例，校验 schema v5、自然语言字段白名单、重复路径、每项 `from` 与当前文件值的一致性，以及四项当前能力字段的严格布尔结构。

在复制并应用全部 Delta 后，服务确定性校验 `authorization.enabled → auth.enable`、权限启用所需数据库数据源及真实初始管理员 subjectId；任一失败都不会写入文件。成功时通过同目录临时文件、`fsync` 和 `os.replace` 原子替换 application.json。空 Delta 是只读 no-op。该服务尚未连接 Revision 确认边界，也尚未替换现有 authorization 专用服务，分别留待第 8、7 步。

验证新增 `tests.test_application_config_mutation`：覆盖白名单字段提交、陈旧 from 或重复路径拒绝且文件不变、以及权限依赖/管理员前置失败与成功的原子性。

## 19.7 第 7 步：迁移 authorization 专用写入（已实现）

`persist_authorization_configuration()` 保留现有权限初始化调用面，但已不再自行复制或写入 application.json。它只读取当前 auth/authorization 状态以构造必要的 typed Delta，并把初始管理员输入交给 `apply_application_config_changes()`；所有 schema、数据源、管理员、from 快照和原子替换语义均收敛到 Mutation Service。

既有权限澄清流程及其调用时机保持不变。本迁移不意味着自然语言 Revision 已会提交 pending Delta；该确认边界仍是第 8 步。

## 19.8 第 8 步：正式产物确认后提交 pending Delta（已实现）

design-stage revision 的 `product_planning` 节点只有在 RequirementSpec 与 ProductPlan 联合确认完成后才调用 `commit_active_revision_application_config_changes()`。该服务将 active Revision 的 pending Delta 交给 Mutation Service 提交，并仅在成功后以 lifecycle CAS 清空 `pendingApplicationConfigChanges`；Mutation 失败会阻断进入 UI 设计，保留 pending 供用户处理或放弃。

workbench-plan revision 不经过该需求文档联合确认边界，仍不提交配置。本步未改变后续 Template Reconcile 或 TechnicalPlan 流程。

## 19.9 实施顺序

建议按以下顺序实施：

1. 明确 `application.json` 为应用配置唯一事实源；
2. 定义 `ApplicationConfigChange` Schema；
3. 将现有 capability intent 识别调整为通用 Application Config Change Resolver；
4. Resolver 改为读取当前 `application.json`，不再读取 RequirementSpec enabled 状态；
5. Formal Revision 增加 `pendingApplicationConfigChanges`；
6. 增加 ApplicationConfigMutationService；
7. 把现有 authorization configuration 写入逻辑迁移到该 Service；
8. 在 RequirementSpec / 正式产物确认后的事务点提交 pending config changes；
9. TechnicalPlan 的 `template_capabilities` 改为从 application.json 编译；
10. 删除 RequirementSpec 中基于历史 request 覆盖登录/权限 enabled 状态的逻辑；

---

# 20. 核心验收案例

至少覆盖以下测试：

```text
初始 login=false
→ “添加登录模块”
→ formal_revision
→ pending auth.enable=true
→ RequirementSpec 确认
→ application.json auth.enable=true
→ TechnicalPlan login=true
→ Template Engine 有实际变化
```

```text
初始 authorization=false
→ “人员页面只有管理员可以访问”
→ auth=true + authorization=true
→ 收集必要管理员信息
→ application.json 更新
→ RequirementSpec 保存业务权限规则
```

```text
login=true
→ “登录页按钮改成蓝色”
→ implementation_fix
→ application.json 不变
```

```text
login=true
→ “不需要登录了”
→ formal_revision
→ pending auth.enable=false
→ 若 authorization=true，配置校验拒绝直接关闭并要求处理依赖
```

```text
用户进入 formal_revision 后取消
→ pending config change 丢弃
→ application.json 不发生变化
```

---

# 21. 最终结论

本次修复不重新设计 XCodeAgent 的二次修改 Router。

现有：

```text
formal_revision
→ design_stage_revision / workbench_plan_revision
```

继续保持不变。

真正需要补充的是：

> **Formal Revision 除了描述正式产物变化之外，还能够携带 Application Config Change。**

自然语言通过现有 Revision 流程识别应用配置变化，配置变化先作为 Pending Delta 存在，用户确认正式修改并完成对应正式产物确认后，再由确定性服务原子更新 `application.json`。

最终形成：

```text
自然语言
→ Formal Revision
→ Application Config Delta
→ application.json
→ RequirementSpec / TechnicalPlan / Template Engine
```

并确立：

> **application.json 是应用级配置状态唯一事实源；RequirementSpec 保存业务需求语义，TechnicalPlan 消费当前应用配置并完成技术实现规划。**

这样不仅解决当前 Login / Authorization 的问题，也为后续 Tracking、Audit、Workflow、Notification 等应用级能力提供统一的自然语言变更机制。
