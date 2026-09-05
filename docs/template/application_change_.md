# XCodeAgent Application Config 单一事实源重构方案

## 一、目标与原则

本次改造不再针对登录、权限等单个能力修补，而是统一解决应用配置在不同阶段重复保存、重复判断和相互覆盖的问题。

核心规则：

> **所有“应用级配置”必须且只能存在 `.xcodeagent/application.json`；所有“规划产物”不得反过来成为应用配置。**

由此形成四条强约束：

1. `application.json` 是应用级配置唯一事实源。
2. RequirementSpec、ProductPlan、TechnicalPlan 等只保存规划结果，不复制应用配置。
3. 自然语言、UI、系统自动补齐等所有配置修改统一写入 `application.json`。
4. Planning、Template、DAG、Build 需要应用配置时直接读取 `application.json`，不得通过其它产物间接获取。

---

# 二、配置边界

## 2.1 application.json 应保存的内容

仅保存应用本身的配置，例如：

```text
基础信息
- appName
- appIcon
- terminal

界面配置
- layout
- theme
- menus.enable / rootPath 等应用骨架配置

能力配置
- auth
- authorization
- track
- apiTrack

环境与基础设施配置
- environment
- database
- datasource 基础配置
```

例如：

```json
{
  "schemaVersion": 6,
  "configRevision": 12,

  "auth": {
    "enable": true,
    "authnSource": "yht"
  },

  "authorization": {
    "enabled": true,
    "initialAdministratorSubjects": ["user-001"]
  },

  "track": {
    "enable": false
  }
}
```

---

## 2.2 application.json 不应保存的内容

以下属于规划或设计结果，应逐步迁出：

```text
productDefinition
apis
schemas
dataSources
menus.items 中的业务页面规划
页面设计
实体设计
API Contract
权限 Resource / Binding
```

这些分别进入：

```text
RequirementSpec
ProductPlan
TechnicalPlan
Detail Design
AuthorizationManifest
Build DAG
```

因此最终需要明确：

```text
application.json = Configuration

plans/* = Planning Artifacts
```

两者不能混用。

---

# 三、目标架构

统一链路：

```text
自然语言 / 设置页面 / 系统规则
              │
              ▼
    ApplicationConfigChange
              │
              ▼
     ApplicationConfigService
              │
              ▼
 .xcodeagent/application.json
        configRevision + 1
              │
      ┌───────┼─────────┐
      │       │         │
      ▼       ▼         ▼
 Revision   Planning   Template
 Impact               Reconcile
      │       │         │
      ▼       ▼         ▼
重新规划   补充性产物   RequestedConfig
      │
      └──────────┐
                 ▼
              DAG / Build
```

关键点：

```text
application.json
      ↓
所有消费者
```

禁止：

```text
application.json
      ↓
RequirementSpec.enabled
      ↓
TechnicalPlan.enabled
      ↓
Template Engine
```

---

# 四、实施计划

## 阶段一：建立统一 ApplicationConfigService

### 目标

先统一所有应用配置的读写入口。

建议新增：

```text
Backend/app/services/application_config/
├── repository.py
├── service.py
├── schema.py
└── change.py
```

职责：

```text
Repository
- load application.json
- atomic save

Schema
- application.json 完整校验

Service
- apply config changes
- dependency validation
- configRevision 管理

Change
- 配置变更模型
```

现有：

```text
application_config_mutation.py
application_authorization_config.py
```

逐步收敛进入该服务。

### 验收

所有后端代码读取或修改应用级配置都通过统一 Service。

---

## 阶段二：统一所有配置修改入口

### 目标

自然语言和人工配置修改完全统一。

统一模型：

```text
ApplicationConfigChange
```

不再只支持：

```text
auth.enable
authorization.enabled
track.enable
apiTrack.enable
```

而应支持所有允许动态修改的应用配置。

例如：

```json
{
  "path": "authorization.enabled",
  "operation": "set",
  "from": false,
  "to": true
}
```

或者：

```json
{
  "path": "layout.useHeader",
  "operation": "set",
  "from": true,
  "to": false
}
```

流程统一为：

```text
自然语言
   ↓
Config Resolver
   ↓
ApplicationConfigChange
```

和：

```text
设置页面
   ↓
ApplicationConfigChange
```

最终统一：

```text
ApplicationConfigService.apply()
        ↓
application.json
```

### 同时处理配置依赖

例如：

```text
authorization.enabled=true
        ↓
要求 auth.enable=true
```

统一由 Config Service 校验或补齐。

规划阶段不得再处理这种配置依赖。

### 验收

不存在某个 Capability 单独拥有自己的正式配置写入入口。

---

## 阶段三：修正 Formal Revision 时序

### 当前问题

目前配置 Change 可以先暂存在 Formal Revision 中，直到 ProductPlan 完成后才真正写入 `application.json`。

这样会导致：

```text
RequirementSpec
ProductPlan
```

基于旧配置运行。

### 调整后

配置修改流程改为：

```text
识别配置变化
      ↓
收集必要参数
      ↓
用户确认
      ↓
提交 application.json
      ↓
configRevision + 1
      ↓
Capability / Config Impact 分析
      ↓
从受影响的最早阶段重新规划
```

即：

> **先修改配置，再基于新配置重新规划。**

Formal Revision 可以保留“待确认 Change”，但一旦进入正式重新规划，配置必须已经提交。

### 验收

Requirement / Product / Technical Planning 启动时读取的一定是当前最新 `application.json`。

---

## 阶段四：清除规划产物中的应用配置副本

### RequirementSpec

删除：

```text
authentication_requirements.enabled
authorization_requirements.enabled
```

只保留：

```text
角色
受控页面
受控操作
业务约束
```

是否启用认证、权限：

```text
直接读取 application.json
```

---

### ProductPlan

不得保存：

```text
loginEnabled
authorizationEnabled
trackingEnabled
```

只保存这些能力开启后的规划结果。

---

### TechnicalPlan

删除：

```text
template_capabilities.*.enabled
```

TechnicalPlan 只保存：

```text
技术设计
API Contract
Entity
技术 Binding
Capability 补充参数
```

不得保存 Capability 是否开启。

---

### AuthorizationManifest

删除：

```text
enabled
```

只保存：

```text
roles
resources
page bindings
action bindings
endpoint bindings
```

是否需要生成 Manifest：

```text
application.json.authorization.enabled
```

决定。

### 验收

规划文件中的任何字段都不能覆盖或改变 Application Config。

---

## 阶段五：Template / DAG / Build 全部直接读取 application.json

### Template Reconcile

当前：

```text
TechnicalPlan.template_capabilities
        ↓
RequestedConfig
```

调整为：

```text
application.json
        ↓
TemplateCapabilityCompiler
        ↓
RequestedConfig
```

例如：

```text
auth.enable
→ login capability

authorization.enabled
→ authorization capability

track.enable
→ tracking capability
```

TechnicalPlan 只能提供 Capability 需要的补充技术参数。

---

### DAG / Build

生成 DAG、执行 Build 时同样遵循：

```text
Application Config
+
Planning Artifact
```

而不是：

```text
Planning Artifact
→ 推断 Application Config
```

例如权限：

```text
application.json.authorization.enabled
        +
authorization_manifest
        ↓
Build DAG
```

前者决定“有没有权限能力”，后者决定“权限具体怎么实现”。

### 验收

即使 TechnicalPlan 是旧文件，只要 `application.json` 已经改变，Template / Build 不允许把旧 TechnicalPlan 中的配置状态当成当前状态。

---

## 阶段六：清理前端第二份 Application Config

当前前端应用对象还存在：

```text
enableAuth
enableTracking
schema
```

以及 Electron：

```text
applications.json
```

这些都可能形成第二份配置状态。

调整原则：

```text
applications.json
```

只作为应用索引，建议只保存：

```json
{
  "id": "...",
  "workspaceRoot": "...",
  "name": "...",
  "lastOpenedAt": "..."
}
```

不再保存：

```text
auth
authorization
track
apiTrack
layout
theme
database
```

打开应用时：

```text
applications.json
      ↓
获得 workspaceRoot
      ↓
读取 .xcodeagent/application.json
      ↓
生成当前 Application View
```

`enableAuth`、`enableTracking` 等字段删除或仅作为运行时派生值：

```text
enableAuth = application.auth.enable
```

禁止持久化。

设置页面保存时也必须修改：

```text
workspace/.xcodeagent/application.json
```

而不是只修改 Electron 的 `applications.json`。

### 验收

关闭并重新打开应用后，所有设置均完全由工作区 `application.json` 恢复。

---

# 五、配置版本机制

建议 `application.json` 增加：

```json
{
  "configRevision": 12
}
```

每次正式配置修改：

```text
configRevision + 1
```

规划产物记录：

```text
sourceConfigRevision
```

例如：

```text
application.json
configRevision = 13

TechnicalPlan
sourceConfigRevision = 12
```

表示 TechnicalPlan 是基于旧配置生成的。

但需要注意：

```text
configRevision
```

只负责判断新旧。

具体哪些产物需要重新生成，由：

```text
ApplicationConfigImpactPolicy
```

决定。

例如：

```text
auth.enable
→ technical-plan

authorization.enabled
→ requirement-spec

theme.primaryColor
→ UI Design

track.enable
→ technical-plan
```

---

# 六、最终硬约束

重构完成后必须满足：

```text
1. application.json 是唯一 Application Config。

2. applications.json 只保存应用索引，不保存配置副本。

3. 所有配置修改统一经过 ApplicationConfigService。

4. 自然语言与 UI 修改使用相同 ApplicationConfigChange。

5. 配置先提交，再启动受影响的重新规划。

6. RequirementSpec / ProductPlan / TechnicalPlan 不保存 Application Config。

7. Planning Artifact 永远不能反向修改 Application Config。

8. Template / DAG / Build 直接读取 application.json。

9. Planning Artifact 只保存 application.json 无法表达的业务和技术补充事实。

10. 新增任何应用级配置时，只需要增加：
    Schema
    Mutation Policy
    Impact Policy
    Consumer Mapping
   不再修改一套跨阶段状态同步逻辑。
```

最终应形成严格的单向关系：

```text
                 application.json
                 唯一配置事实源
                       │
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
   Planning         Template          Build
       │
       ▼
Planning Artifacts
       │
       └──── 只描述设计结果

Planning Artifacts ──X──> application.json
```

这样后续新增登录、权限、埋点、审计或其它 Application Capability 时，都不再需要解决一次“配置如何在 Requirement → Product → Technical → Template 之间同步”的问题。
