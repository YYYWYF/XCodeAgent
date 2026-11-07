# XCodeAgent 数据源配置 → 字段映射前置引导优化（Codex 实施方案）

> **目标仓库**：https://github.com/YYYWYF/XCodeAgent.git  
> **目标分支**：`dev_agent`  
> **分析基线**：`8307f708734c814342f6c2292947de2c2ee70fa8`（2026-11-07）。实施时先核对实际 HEAD。  
> **改造原则**：**仅补齐字段映射前置条件提示、定向导航和来源候选刷新**。**强制复用已有前端组件及其交互和调用链，不复制或重新实现“新增/编辑数据库连接”“检测连接”“添加数据表”“实体创建表”等功能**；不新增建表服务、配置向导或工作流节点。  
> **最终用户体验**：用户进入“字段映射”，选择“数据表”后，系统根据当前应用的数据库模式及真实状态，**只呈现一个下一步引导**；完成必要操作后回到原接口、刷新表和字段候选，继续原草稿。

## 1. 现状与根因（先回检）

| 文件/能力 | 已有实现 | 当前旅程断点 |
| --- | --- | --- |
| `Frontend/src/renderer/src/components/AiChatPanel/components/FieldMapping/index.tsx` | 来源类型 `database/external_api/static`；从 `tables` 生成数据表下拉候选；无候选时仅有普通文案 | 未区分内置/非内置和“没连接/没添加表”，没有操作入口 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/FieldMapping/useBindingWorkspace.ts` | 加载 `catalog`、`tables`、元数据；`refreshSourceCandidates()`；保留按接口区分的映射草稿 | 页面没有把“前置操作完成”接成清晰的跳转返回旅程 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/FieldMapping/bindingSources.ts` | 通过 `observeBindingSources` 订阅数据源变更并只读刷新 | 已有刷新能力，可直接复用 |
| `Frontend/src/renderer/src/service/dataSourceEvents.ts` | 同工作区 `notifyDataSourcesChanged`/`subscribeDataSourcesChanged` | 无需另建事件总线 |
| `Frontend/src/renderer/src/components/DataSourcesPage/DataSourcesDrawer.tsx` | 外部数据库连接设置、新增连接、`添加数据表`；内置库展示实际表且只允许刷新 | 缺少来自字段映射的定向打开上下文 |
| `Frontend/src/renderer/src/components/DataSourcesPage/DatabaseTables.tsx` | `AddDatabaseTables` 发现真实外部表并加入应用候选，非创建物理表 | 外部数据库必须先添加表才能映射 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/EntityTablePanel/` | 内置库实体的“模拟数据表”页签提供 `创建表/刷新结果/重建表`、状态检查 | 字段映射不能直达相关实体和页签 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/EntityInfoPanel/EntityContentTabs.tsx` | 实体页签：字段设计、模拟数据表、建表 DDL；默认 `fields` | 无外部指定初始页签入口 |
| `Frontend/src/renderer/src/components/AiChatPanel/AiChatPanel.tsx` | `activeView='dataSources'` 控制已有抽屉；右侧 `field-mapping`/`outline` 面板；实体选择回调 | 尚未把“映射 → 配表/建表 → 返回映射”串成上下文导航 |
| `Backend/app/services/data_sources.py` | `DatabaseSourcePublic.mode='builtin' | 'dbid' | 'direct'`；非内置的 `selected_tables` 来自已添加表；内置的来自 SaaS 实际表目录 | 后端基本无需改造 |

**注意当前两个事实**：

1. **内置数据库与非内置数据库互斥**。`builtin` 分支绝不显示“去添加数据表”；因为内置库不允许通过 `change_selected_tables()` 管理应用表清单，而是从真实 SaaS 表目录读取。
2. **非内置数据库下“未配置连接”与“已配置连接但未添加表”是连续的两种状态**。不应做三个并列的用户可选 Tab 或单选项。

## 2. 最终用户旅程

```text
字段映射 → 数据来源 → 选择“数据表”
    ↓
读取当前应用数据库模式与当前来源目录（已有接口）
    ├─ builtin
    │    ├─ 已有可绑定真实表 → 显示原下拉 → 字段映射
    │    └─ 尚无真实表 → 提示「前往实体建表」
    │                         → 开发产物/目标实体/模拟数据表
    │                         → 用户确认创建表（现有接口）
    │                         → SaaS 实际结构核验成功
    │                         → 返回原字段映射 + 自动刷新候选
    │
    ├─ dbid / direct
    │    ├─ 没有有效数据库连接配置 →「去配置连接」
    │    │                             → 数据源抽屉/现有连接配置
    │    ├─ 有连接、没有已添加表 →「去添加数据表」
    │    │                             → 数据源抽屉/已有添加表功能
    │    └─ 已添加表 → 显示原下拉 → 字段映射
    │
    └─ 静态数据/外部 API/数据源元数据读取失败
         → 维持原对应功能或显示加载/错误态，不误报为未配置
```

**禁止**自动创建表、自动选表、自动保存/确认映射、自动重建数据库表。外部数据表添加只是纳入应用绑定候选，不是执行 SQL。内置建表优先使用实体页签已有的 `创建表` 按钮和确认流程；建表 DDL 页签继续只读。

## 3. P0：字段映射空状态自动识别与提示

### 3.1 增加纯函数判断器（便于单测）

建议在 `FieldMapping/` 内新增 `databasePrerequisite.ts`，只返回 UI 语义，不产生副作用：

```ts
type DatabaseGuideState =
  | 'hidden'                 // 当前不是选择数据表
  | 'loading'
  | 'load_error'
  | 'builtin_create_table'
  | 'external_config_connection'
  | 'external_add_table'
  | 'ready';

// 参数是已有 application 数据源模式（优先）/ catalog 的 mode、
// catalog 请求状态、selected_tables、当前数据表选择等。
// 不从表数量反推数据库模式。
```

**具体判定顺序（必须按序，避免误判）**：

1. 当前来源类型不为 `database`：`hidden`，完全不影响外部 API 和静态数据。
2. 来源目录正在加载：`loading`；发生目录/表查询错误：`load_error`（提供 `重试读取`）。**不得把失败时的空数组当成“无连接/无表”**。
3. 模式取值：
   - 优先使用当前应用权威配置所对应的数据库模式，并与已经加载的 `catalog.sources[].mode` 核对；若现有前端已经可靠提供 `mode`，可直接复用。
   - `builtin | dbid | direct` 之外或模式不一致：展示“数据源状态无法确认，请检查配置/重新读取”，不可默认归类为外部库。
   - 当前应用只允许一个数据库来源（后端现有限制）；匹配对应数据库 `sourceId`，不能把外部 API source 当连接。
4. `builtin`：如果当前 SaaS 实际表目录中该数据库有可绑定表则 `ready`；否则 `builtin_create_table`。
5. `dbid/direct`：如果尚未保存当前数据库连接配置，则 `external_config_connection`；否则如当前库已添加应用候选表则 `ready`，没有则 `external_add_table`。
6. **连接配置存在 ≠ 连接已经可达**：若已有连接但表发现/连接检测失败，展示真实错误及“检查连接/重试”，不要引导重复新建连接。
7. 对已有绑定但表从候选中消失的情况，保留现有 `unavailableTable` 警告及原草稿，不要把它当作从未选择过表而自动清空映射。

实现时应复用 `useBindingWorkspace` 已返回的 `catalog`、`tables`、`loading/error`；需要的话增加明确的 `sourcesLoading/sourcesError` 只读字段，**不要用 Endpoint `preparation` 的加载状态代替数据源目录加载状态**。若现有字段无法区分“空目录”和“读取失败”，优先在 Hook 内补足这两个状态，不新建后端接口。

### 3.2 替换单句无候选提示，保留现有主题

修改 `FieldMapping/index.tsx` 的“数据来源”区（当前 `kind === 'database' && !candidates.length` 位置）。仅新增与原页面一致的轻量空状态：`Alert` 或现有 `binding-section` 内容；沿用 Ant Design 与 `FieldMapping/index.less` 的 `--wb-border`、`--wb-canvas`、`--wb-text-*` 主题变量，不额外增加整屏向导或新页面。

| 展示条件 | 提示标题 | 说明 | 主按钮 |
| --- | --- | --- | --- |
| `builtin`，无可绑定实际表 | 尚未创建模拟数据表 | 请先在“开发产物 → 实体 → 模拟数据表”中创建数据表，完成后即可进行字段映射。 | **前往实体建表** |
| `dbid/direct`，无已配置连接 | 尚未配置数据库连接 | 请先配置数据库连接，连接成功后再添加需要绑定的数据表。 | **去配置连接** |
| `dbid/direct`，已配置但未添加应用表 | 尚未添加数据表 | 请从已配置数据库中选择需要参与当前应用字段映射的数据表。 | **去添加数据表** |
| 已有可绑定表 | 不展示引导 | 沿用原数据表选择框 | 无 |
| 正在加载 | 正在读取可绑定数据表 | 不显示引导按钮 | 无 |
| 请求失败/模式不确定 | 无法读取数据源信息 | 展示真实的脱敏错误原因 | **重试读取** / 检查配置 |

**UI 约束**：一次只出现一个提示；不添加“内置/非内置”切换组件；按钮仅定位现有功能，不执行服务端写操作。与已有“更换数据源”、`暂存`、`保存并确认` 共存，但前置条件缺失时仍受现有 `canConfirm` 门禁约束。

## 4. P0：前置操作跳转与返回，保留原草稿

### 4.1 FieldMapping → 数据源抽屉（非内置）

建议扩展 `FieldMappingWorkspace` 回调：

```ts
onOpenDatabaseSources?: (intent: 'configure_connection' | 'add_tables') => void;
```

在 `AiChatPanel.tsx` 中接收：

- `configure_connection`：设置已有 `activeView='dataSources'`，打开 `DataSourcesDrawer`；已有连接配置/新增连接表单由抽屉维护。
- `add_tables`：同样打开现有抽屉，定位当前数据库来源；最低成本方案先让用户点击现有“添加数据表”。若改造成本很低，可向抽屉传入一次性 `initialIntent='add_tables'` 并自动打开现有 `AddDatabaseTables`，**前提是来源已加载且有效**。
- 当前 `DataSourcesDrawer` 内部已有 `onNavigationGuard`/未保存内容保护，必须继续生效；不得通过直接卸载抽屉绕过 guard。
- 已有外部库数据源保存/添加表后会发 `notifyDataSourcesChanged(workspaceRoot)`；字段映射继续通过 `observeBindingSources` 刷新目录。
- 关闭抽屉返回原字段映射面板与原 Endpoint 选择；如面板实际曾卸载，必须明确保存草稿或提示用户确认，不允许导航时静默丢弃内存草稿。

**P0 推荐实现**：仅接入两种 intent 引导，复用抽屉现有按钮，不强求新增从映射自动打开二级 Modal 的复杂逻辑。

### 4.2 FieldMapping → 开发产物/实体/模拟数据表（内置）

建议扩展：

```ts
onOpenBuiltinEntityTable?: (entityId: string) => void;
```

- 在 `AiChatPanel.tsx` 利用现有 `developmentPlanningApiContracts[].entityIds` 寻找与当前 Endpoint 所属 API Contract 相关的实体；**不能拿任意第一个实体冒充目标表**。
- 一个关联实体：直接定位“开发产物 → 该实体 → 模拟数据表”页签。
- 多个关联实体：在字段映射空状态中显示轻量实体下拉选择，用户选中后再进入；不自动挑选首项。
- 无关联实体或实体已不存在：显示“未能确定关联实体，请先核对技术规划”并允许打开开发产物列表；不自动执行建表。
- 导航复用 `useDevelopmentArtifactDetail().artifactOutlineProps.onEntitySelect` 与 `setRightPanel({ type: 'outline' })`；右侧如果隐藏则恢复分栏。
- `EntityContentTabs.tsx` 支持可选 `initialTab='database'`（或等价导航意图）直达已有“模拟数据表”页签；这一步只改变激活 Tab，**不触发建表**。
- 既有 `EntityTablePanel`/`useEntityTable` 继续负责 `prepare/execute/refresh/rebuild`、用户确认和 SaaS 状态核验；保留 `UNKNOWN`、`RUNNING` 不重提的安全约束。
- 在实体页签增加一个轻量 **“返回字段映射”** 操作（只在来自字段映射的导航上下文下出现）；返回前恢复原 `rightPanel='field-mapping'` 和之前的 `ApiDesignConfigTarget`。无需新建独立页面或弹窗。

**重要边界**：`EntityContentTabs` 的页签切换不应修改当前工作流、技术规划、已确认 Endpoint 或任何实体定义。只有用户明确点击已有“创建表”且二次确认后才执行建表。

### 4.3 数据刷新与草稿保留

- **外部库**：保存连接/添加数据表 → 现有 `dataSourceEvents` 通知 → 字段映射 `observeBindingSources` 更新 `catalog/tables` → 下拉自动显示新候选。
- **内置库**：实体建表 **SaaS `SUCCEEDED` + 结构核验成功** 后，才认为表可绑定；已有 `useEntityTable.ts` 的终态通知与 `dataSourceEvents` 可复用。返回字段映射时额外调用 `state.refreshSourceCandidates()` 做兜底，只读刷新 `tables` 和元数据。
- **保留草稿**：不调用 `state.reload()`、`discard()`、`changeSelection(null)`，不自动保存/确认；原 Endpoint、`kind`、`pendingSelection`、查询/写入/返回映射草稿保留。
- **避免迟到数据覆盖**：复用 `bindingSources.ts` 的 revision 防护；导航上下文至少绑定 `workspaceRoot + apiContractId + endpointId`，切应用时废弃返回目标。
- 内置表仍处于 `RUNNING/UNKNOWN`、结构冲突或列元数据不可读取时，保留上一步信息和只读核验入口，不能用设计预览字段冒充真实表结构，也不能将列表空白渲染为建表成功。
- 如果要跨真正卸载维持未保存内容，优先沿用当前工作台的驻留挂载机制；如果该机制不足，增加受控“保存草稿后跳转”提示，而非扩张到全局持久化重构。

## 5. 强制要求：全面复用现有前端组件（新增）

### 5.1 核心要求

**本节是 Codex 必须执行的硬约束，不是实现建议。** “去配置连接”“去添加数据表”“前往实体建表”只是**新的导航入口**，实际配置和写操作必须交由已有组件处理。

1. **不能在 `FieldMapping` 下新建数据库连接表单、连接检测弹窗、数据表选择弹窗、建表 DDL 执行 UI 或 SQL 确认框。** 不得拷贝原组件 JSX、复制校验逻辑、抽取第二份接口调用链，或通过重命名方式伪装成复用。
2. 对现有组件尽量仅增加**可选的导航参数/回调**，例如 `initialIntent`、`initialSourceId`、`initialTab`、`onBackToFieldMapping`；保持从左侧菜单正常进入时的默认行为不变。
3. 复用原组件**现有校验、加密、脏数据离开确认、数据表候选查询、保存、事件发布、远端建表确认与幂等保护**。字段映射页本身仅展示轻量引导信息，并调用导航回调。
4. 如果复用组件目前只通过父组件的内部 state 打开，就在**原父组件**增加公开的可选意图入口，转调其现有打开方法；不要在字段映射处绕开父组件直接挂载第二份 `Modal`。
5. 修改前须再次核对最新 HEAD 与下表 import/调用关系；组件已有可复用能力时，**禁止创建并行实现**。仅允许新增一个纯函数 `databasePrerequisite.ts`，以及必要的轻量提示 JSX/导航状态。

### 5.2 数据源连接及外部数据表：必须复用的现有组件

| 已有组件 | 仓库路径（均在 `Frontend/src/renderer/src/components/DataSourcesPage/` 下） | 现有职责 | 本次如何复用 / 允许的最小改动 |
| --- | --- | --- | --- |
| **`DataSourcesDrawer`** | `DataSourcesDrawer.tsx` | 当前工作台的数据源抽屉；管理连接页签、连接设置、添加表、详情、异常提示；挂载下面各弹窗 | **唯一推荐入口**。从字段映射设置 `activeView='dataSources'`，可选传入 `initialIntent`/`initialSourceId`；仍由抽屉控制其内部表单和表选择器 |
| **`DataSourceEditorModal`** | `DataSourceEditorModal.tsx` | 新增/编辑数据库连接，执行字段校验、检测连接、保存和离开确认；现有父级负责 `onSave/onValidate` | **必须复用原弹窗**，不在字段映射新建连接配置 Modal。由 `DataSourcesDrawer` 设置现有 `editor` 状态打开；原则上不改这个组件业务逻辑 |
| **`DatabaseConnectionForm`** | `DatabaseConnectionForm.tsx` | DBID/直连模式、地址、端口、Schema、用户名、密码/DBID 的完整输入与校验状态 | **必须由 `DataSourceEditorModal` 继续复用**；不复制到字段映射。保留模式判断及现有输入校验 |
| **`AddDatabaseTables`** | `DatabaseTables.tsx` 中导出的 `AddDatabaseTables` | 从外部数据库真实发现表，勾选多张表，将其加入应用绑定候选；提供搜索、已添加态、保存与取消确认 | **必须复用原选择弹窗**。从 `DataSourcesDrawer` 现有 `addingTables` 打开，不新造表选择器。可选支持由 `initialIntent='add_tables'` 自动打开；不得把“添加应用候选”误写成物理 CREATE TABLE |
| **`DatabaseTableDetail`** | `DatabaseTables.tsx` 中导出的 `DatabaseTableDetail` | 查看真实表字段，外部库可移除应用候选 | 表添加成功后仍使用原表详情组件核验候选；本次不开发独立表详情/列展示 |
| **`DatabaseTableTabs`** | `DatabaseTableTabs.tsx` | 内置已建表的表结构、表数据、操作记录页签 | 内置表查询/结果查看继续走该组件；不在字段映射额外实现内置库表详情 |

**实际调用链必须保留：**

```text
FieldMapping 轻量引导按钮
  → AiChatPanel.activeView='dataSources'
  → DataSourcesDrawer
      ├─ editor.open  → DataSourceEditorModal → DatabaseConnectionForm
      │                  ├─ onValidate → 现有 validateDataSource
      │                  └─ onSave     → 现有 createDataSource / updateDataSource
      └─ addingTables → AddDatabaseTables → 现有 changeSelectedTables
                                       → notifyDataSourcesChanged
```

**注意：** 仓库也存在 `DataSourcesPage.tsx`（独立目录页面），但**当前工作台已采用 `DataSourcesDrawer`**。本次禁止为了导流字段映射再实例化 `DataSourcesPage` 或创造第三种数据源管理入口；以 `AiChatPanel` 当前挂载的抽屉为准。

### 5.3 内置数据库实体建表：必须复用的现有组件

| 已有组件/Hook | 仓库位置（相对 `Frontend/src/renderer/src/components/AiChatPanel/components/`） | 已有职责 | 本次复用方式 |
| --- | --- | --- | --- |
| **`DevelopmentArtifactsPanel`** | `DevelopmentArtifactsPanel/index.tsx` | “开发产物”内容区，统一展示实体/API 详情 | 复用现有实体选择与详情，不增加专门的建表页面 |
| **`ApplicationOutline`** | `ApplicationOutline/index.tsx` | 开发产物菜单中的实体、API 选择 | 用既有选择行为定位目标实体，不另造实体目录 |
| **`EntityInfoPanel`** | `EntityInfoPanel/index.tsx` | 当前实体信息与多个页签的入口 | 最多透传 `initialTab`、返回回调，保持现有详情布局 |
| **`EntityContentTabs`** | `EntityInfoPanel/EntityContentTabs.tsx` | “字段设计 / 模拟数据表 / 建表 DDL”三页签 | 增加可选初始选中 `database` 的能力；普通进入仍默认 `fields` |
| **`EntityTablePanel`** | `EntityTablePanel/index.tsx` | 内置实体表名、结构、真实字段、状态及建表动作入口 | **必须复用原建表操作面板**，不得在字段映射自行执行 DDL |
| **`EntityTableStatus`** | `EntityTablePanel/EntityTableStatus.tsx` | `NOT_CREATED/SYNCED/STRUCTURE_MISMATCH`、运行中/结果未知状态及“创建表/重建表/刷新结果” | 复用原状态与按钮；无可用表时先去该页签，不复写状态机 |
| **`EntityTableConfirmation`** | `EntityTablePanel/EntityTableConfirmation.tsx` | 创建表二次确认，以及清空重建时的数据丢失确认 | **必须复用原确认弹窗**；禁止在字段映射中新增“直接建表”或忽略重建风险的按钮 |
| **`EntityTableFields`** | `EntityTablePanel/EntityTableFields.tsx` | 展示实时获取的数据库字段 | 使用已有真实元数据展示，不用模型设计字段冒充数据库结构 |
| **`useEntityTable`** | `EntityTablePanel/useEntityTable.ts` | `prepare / execute / rebuild / refresh`，运行态检查、结果读取、成功事件通知 | **原样复用执行链**；除必要通知修复外，本次不改其请求合同或安全判断 |
| **`EntityDdlPreview`** | `EntityInfoPanel/EntityDdlPreview.tsx` | 只读显示/复制建表 DDL | 继续只读；**不是**内置库创建表的调用入口，不增加执行按钮 |

**内置库复用链路：**

```text
FieldMapping「前往实体建表」
  → AiChatPanel 原有右侧 outline 导航
  → ApplicationOutline 选择关联实体
  → DevelopmentArtifactsPanel / EntityInfoPanel
  → EntityContentTabs(initialTab='database')
  → EntityTablePanel
      ├─ EntityTableStatus「创建表」
      ├─ EntityTableConfirmation「确认创建」
      └─ useEntityTable.execute + refresh + 真实结构核验
         → notifyDataSourcesChanged
  → 返回原 FieldMapping，触发只读候选刷新
```

### 5.4 字段映射与导航：继续复用的组件和前端能力

| 已有组件 / 模块 | 现有职责 | 本次复用 / 可修改边界 |
| --- | --- | --- |
| `FieldMapping/index.tsx`（`FieldMappingWorkspace`） | 数据来源类型选择、现有数据表下拉、映射编辑 | 仅加入互斥空状态与导航按钮；保留原 `Select`、`Radio.Group`、暂存及保存确认按钮 |
| `FieldMapping/useBindingWorkspace.ts` | 按 Endpoint 维护草稿、读取 catalog/tables/元数据、刷新来源 | 继续使用 `refreshSourceCandidates()`，只补必需的加载/错误信号；不得导航时调用 `reload()` 或 `discard()` |
| `FieldMapping/bindingSources.ts` | `observeBindingSources()` 按工作区订阅并刷新数据源候选 | 继续复用旧请求失效机制；不再写一套候选获取 Hook |
| `FieldMapping/DatabaseMapping.tsx` | 数据库查询、写入、返回字段映射表单 | 现有表出现后正常渲染；不改现有 CRUD 与字段映射编辑器 |
| `AiChatPanel.tsx` | 当前 `activeView`、`rightPanel`、已选 Endpoint、数据源抽屉挂载 | 只增加导航回调/返回意图，不新增工作台路由或独立弹窗 |
| `useDevelopmentArtifactDetail.ts` | 定位实体并切换 `rightPanel='outline'` | 复用 `onEntitySelect`，不重新构建实体导航体系 |
| `service/dataSources.ts` | `requestDataSources`、`requestSelectedTables`、`requestApiDesignDatabaseColumns`、`create/update/validateDataSource`、`changeSelectedTables` | 复用现有 AG-UI 服务函数，不在组件中重新组装后端写请求 |
| `service/dataSourceEvents.ts` | `notifyDataSourcesChanged` / `subscribeDataSourcesChanged` | 复用工作区作用域事件，不再增加自定义刷新事件总线 |
| `service/entityTables.ts` | 内置实体表的 `prepare/execute/rebuild/refresh` 与 DDL 请求 | 复用原请求函数，不直接从引导卡发远端 CREATE |
| `workspaceDialogs` 中的 `confirmWorkspaceAction` | 脏数据离开、表移除等确认 | 沿用已有导航保护和用户确认，不新建一套 `Modal.confirm` 边界 |

### 5.5 唯一允许新增的前端内容

- `FieldMapping/databasePrerequisite.ts`：纯状态判断函数，明确当前引导类型；**不可放数据库配置逻辑或发起网络请求**。
- 在 `FieldMapping/index.tsx` 原“数据来源”区添加轻量引导（推荐复用现有 `Alert`、`Button` 和主题 class），不抽出与已有表单同构的大组件。
- 原组件的**可选参数/导航回调**：`onOpenDatabaseSources`、`onOpenBuiltinEntityTable`、`initialIntent`、`initialSourceId`、`initialTab`、`onBackToFieldMapping`。按实际需要最少增加，不要求每个都落地。
- 必要的组件交互测试和状态判断单测。

**禁止新增**：`DatabaseConnectionModalV2`、`DatabaseSourceQuickForm`、`TablePickerForMapping`、`BuiltinTableCreateDialog`、`FieldMappingDataSourceDrawer` 或任何功能等价的平行实现。

### 5.6 组件复用验收方法

1. **连接引导**：点击“去配置连接”后实际挂载组件仍为 `DataSourceEditorModal` / `DatabaseConnectionForm`，原“检测连接”、密码加密、保存及取消确认均有效；调用链沿用已有 `DataSourcesDrawer`。
2. **添加表引导**：点击“去添加数据表”后实际使用 `AddDatabaseTables`，已有表禁选、筛选、多选、取消确认、`changeSelectedTables` 均有效；不出现第二个选表 Modal。
3. **内置建表引导**：点击“前往实体建表”后实际使用 `EntityTablePanel`、`EntityTableStatus`、`EntityTableConfirmation`；未确认不会执行 CREATE；重建确认保留数据丢失警告。
4. **映射返回**：导航往返后仍是同一 `FieldMappingWorkspace` 与当前 Endpoint；草稿内容不丢失，数据源列表和真实字段更新；不自动提交映射。
5. **静态检查**：Codex 在交付说明中列明上述实际 import/挂载链路及修改 diff，确认未增加任何重复连接表单、选表器、建表弹窗或第二套 HTTP/AG-UI 服务。

## 6. 最小修改文件清单

| 优先级 | 文件 | 改造点 |
| --- | --- | --- |
| P0 | 新增 `Frontend/src/renderer/src/components/AiChatPanel/components/FieldMapping/databasePrerequisite.ts` | 数据库模式和前置状态纯函数，明确定义异常、加载与无表状态 |
| P0 | `Frontend/src/renderer/src/components/AiChatPanel/components/FieldMapping/index.tsx` | 替换无表提示；根据模式与状态显示唯一入口；相关实体选择 |
| P0 | `Frontend/src/renderer/src/components/AiChatPanel/components/FieldMapping/useBindingWorkspace.ts` | 必要时补充数据源读取状态；复用只读候选刷新，不改保存合同 |
| P0 | `Frontend/src/renderer/src/components/AiChatPanel/AiChatPanel.tsx` | 接入打开数据源抽屉、跳转关联实体、返回原字段映射回调；保留导航上下文 |
| P0 | `Frontend/src/renderer/src/components/AiChatPanel/components/EntityInfoPanel/EntityContentTabs.tsx` | 支持 `initialTab='database'`（仅 UI 导航） |
| P0 | `Frontend/src/renderer/src/components/AiChatPanel/components/EntityInfoPanel/index.tsx`、`DevelopmentArtifactsPanel/index.tsx` | 向实体详情透传目标页签、可选返回入口 |
| P0 | `Frontend/src/renderer/src/components/AiChatPanel/components/FieldMapping/index.less` | 复用原主题增加轻量引导布局样式 |
| P1 可选 | `Frontend/src/renderer/src/components/DataSourcesPage/DataSourcesDrawer.tsx` | 支持 `initialIntent='add_tables'`，进入抽屉后自动打开已有添加表弹窗 |
| 不改 | Backend `data_sources.py`、`entity_tables.py`、`managed_database_metadata.py` | 已有目录、建表与元数据能力可复用 |
| 不改 | SaaS、模板服务、Graph、Build、正式 Endpoint 映射保存/确认协议 | 不属于本次前置旅程优化 |

> P0 完成标准是**有正确提示 + 可跳转 + 返回后刷新并保留草稿**；“自动直达添加表二级弹窗”可以作为 P1 增强，不是交付阻断项。

## 7. 供 Codex 直接执行的实施顺序

### PR1 — 状态判断与轻量提示

1. 回检最新分支的 `FieldMapping/index.tsx`、`useBindingWorkspace.ts` 与数据库来源类型；确认 `catalog` 是否有真实加载/错误状态，必要时补充。
2. 抽取纯函数 `resolveDatabasePrerequisite()`；确保只根据实际数据库模式、连接目录和已选表判定，不根据候选表数量反推模式。
3. 替换现有静态提示为三种互斥提示及相应按钮；静态来源、外部 API 来源、已有映射保持原样。
4. 编写状态分支单测。

### PR2 — 复用导航与数据刷新

1. `FieldMapping` 添加打开数据源抽屉回调，接到 `AiChatPanel` 原有 `activeView` 与 `DataSourcesDrawer`；其内部**必须沿用 `DataSourceEditorModal` → `DatabaseConnectionForm` 及 `AddDatabaseTables`**。不动连接校验、密码加密和添加表 API。
2. 增加实体关联目标解析与选择。针对 builtin，透传 `initialTab='database'`，**必须调用现有 `EntityTablePanel`/`EntityTableConfirmation`**，不自行构造执行建表交互。
3. 增加来自字段映射时的“返回字段映射”入口；恢复原 Endpoint 和右侧 Tab，保留未确认草稿。
4. 返回时调用现有只读 `refreshSourceCandidates()`；测试事件刷新、迟到请求及跨工作区隔离。

### PR3 — 验证与文档

1. 补充组件交互测试、无表/加载/失败/恢复测试。
2. 用可销毁的内置数据库和可控外部库测试走一遍完整旅程。
3. 更新相应 `docs/`：首次字段映射遇到空表时系统自动提示下一步；用户仍需主动确认建表、添加表和保存映射。
4. 提供最终改动清单、测试命令及结果；说明未涉及 SaaS、模板或 Build 逻辑。

## 8. 必须覆盖的验收场景

| 场景 | 预期 |
| --- | --- |
| `builtin` 且 SaaS 表目录为空 | 仅显示“前往实体建表”，不出现连接或添加表按钮 |
| `builtin` 已建表且可查实际字段 | 正常显示表下拉/字段映射，无前置提示 |
| `builtin` 建表提交后 `RUNNING` / `UNKNOWN` | 维持待核验，不重复提交；不可冒充已可绑定 |
| `builtin` 关联 1 个实体 | 一键打开该实体的“模拟数据表”页签 |
| `builtin` 关联多个实体 | 需选择实体，不默认首个；返回保持原接口 |
| `builtin` 无法确认关联实体 | 明确说明并允许去开发产物列表，不伪造实体 ID |
| `direct` 没有已保存连接 | 仅显示“去配置连接” |
| `dbid` 没有已保存连接 | 同上；复用 DBID 原表单 |
| `direct/dbid` 已有连接、无已添加表 | 仅显示“去添加数据表” |
| `direct/dbid` 已添加表 | 正常显示原候选，不额外插入导航 |
| 已配置连接但真实查询失败 | 显示连接或查询错误与重试，不错误引导新增连接 |
| 源目录仍在加载中 | 显示加载，不短暂闪现“无连接/无表” |
| 外部库添加表后 | 通过现有变更事件自动刷新字段映射候选 |
| 内置库建表核验成功后返回 | 无需手动重新进入字段映射；刷新可选表和真实字段 |
| 进入前有未保存映射草稿 | 导航往返后内容、当前 Endpoint、字段规则不丢失，不自动保存 |
| 已选表被删除或列类型变化 | 保留现有无效引用警告与重新确认要求，不静默清空 |
| 切换到其他应用或其他 Endpoint | 异步结果与返回动作不串台 |
| `external_api` / `static` | 完全维持现有行为 |
| 用户只是点引导按钮 | 不触发 CREATE / DML / 保存确认接口 |

## 9. Codex 交付约束与完成标准

- **前端最小成本 + 强制组件复用**：集中修改 FieldMapping 空状态、AiChatPanel 导航，以及现有抽屉/实体 Tab 可选参数传递；严格按第 5 节逐项复用已存在的连接表单、添加表弹窗和建表组件，不允许复制相同的前端交互；不新增一个独立数据源配置流程或后端状态机。
- **模式权威性**：数据库模式由应用的真实配置/已加载的数据库目录决定，不由用户在引导卡切换，更不能因表候选为空就猜测为内置库。
- **保护业务数据**：内置建表必须使用现有 `EntityTablePanel` 用户确认；重建仍需数据丢失二次确认；不执行 DDL 页签复制的 SQL。
- **只读刷新**：进入/返回字段映射仅刷新元数据；不隐式提交 API 映射，现有确认门禁不变。
- **草稿不丢**：保持同一个 Endpoint 的草稿状态；任何可能卸载并丢失草稿的导航必须先保护用户输入。
- **异常不误导**：请求失败、模式未知、`RUNNING/UNKNOWN` 与确实无表要明显区分；失败保留“重试读取/刷新结果”等已有入口。
- **回归测试**：遵循仓库已有前端测试框架运行；输出测试清单、通过结果和联调局限，不捏造成功结果。

**组件复用验收**：必须在交付报告中标注 `DataSourcesDrawer → DataSourceEditorModal → DatabaseConnectionForm`、`DataSourcesDrawer → AddDatabaseTables`、`EntityContentTabs → EntityTablePanel → EntityTableConfirmation` 三条真实复用链路；出现平行连接/选表/建表表单则视为未通过。

**最终验收一句话**：字段映射选择“数据表”时，内置数据库只引导实体建表，非内置数据库按“先连接、后添加表”引导；前置操作完成后自动更新候选、保留原草稿和接口定位，用户无需自行摸索页面切换。 
