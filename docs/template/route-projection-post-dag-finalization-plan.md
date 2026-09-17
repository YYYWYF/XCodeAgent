# XCodeAgent 与模板工程 Route Projection v2 改造实施方案

## 一、方案设计与契约

### 1.1 改造目标

当前 Route Projection 作为 Build DAG 中的确定性 Task 执行，并依赖历史 `routeFacts` 判断是否需要再次投影。

本次改造将 Route Projection 从 Build DAG 中移出，调整为 **DAG 成功完成后的固定 Post-DAG Finalization 阶段**。

空 DAG 视为成功完成；任一业务 Task 失败、取消、等待确认或等待修复时，不得进入 Finalization。每次成功 DAG 都执行 Finalization，但 Route Projection 仅在本轮冻结 Build scope 具有 Route Impact 时执行：`application`、`page` 为 true，`endpoint`、`data_source` 为 false。

整体职责收敛为：

- **XCodeAgent**：提供已确认的页面设计事实，负责 Build 编排，并观测 Projector 对真实 Workspace 造成的文件变化。
- **模板工程**：根据设计页面和当前 Workspace 中实际存在的页面，确定最终业务路由并原子写入。
- **Route Projection**：无历史状态、可重复执行、全量 reconcile。
- **Workspace**：生成代码与 Route Projection 的唯一运行事实源。

最终路由集合定义为：

```text
最终业务路由
=
Confirmed ProductPlan.pages
∩
Workspace 中实际存在的合法页面入口
```

其中：

- ProductPlan 决定“设计上应该有哪些业务页面”；
- Workspace 决定“当前实际已经有哪些页面”；
- 模板 Projector 决定“当前最终应该注册哪些业务路由”。

---

### 1.2 六项统一设计原则

本次改造统一遵循以下 6 条原则。

#### 原则一：双方只实现 Route Projector v2

模板工程与 XCodeAgent 只支持：

```text
route-projector-contract.v2
route-projector.v2
```

不兼容 `route-projector.v1`。

任一侧检测到非 v2 契约时直接 fail-closed。

---

#### 原则二：页面存在性完全以真实 Workspace 为准

模板只根据真实 Workspace 判断：

```text
frontend/src/pages/<PageDirectory>/index.tsx
```

是否存在且为普通文件。

结果只分为：

```text
存在  → appliedPageIds
不存在 → skippedPageIds
```

XCodeAgent 不再结合 Page Unit、Task 类型或历史 Build 状态二次解释 `skippedPageIds`。

---

#### 原则三：模板内部拥有 pageId 平台规则

Base 页面、Capability 页面以及其他模板平台保留的 `pageId`，全部由模板内部管理和校验。

XCodeAgent：

- 不维护 `welcome` 等保留 ID；
- 不解析 Base / Capability 路由实现；
- 不根据模板内部状态推断 pageId 所有权。

如业务 `pageId` 与模板保留 ID 冲突，由 Projector 直接失败。

---

#### 原则四：双方输入结构与通用校验规则统一

Route Projector v2 必须定义正式 Input / Output JSON Schema。

双方统一遵守同一套输入结构与通用校验规则，包括：

- `protocol`
- `pages`
- `pageId`
- `name`
- `resourceKey`
- 唯一性
- 必填性
- 字符串格式

其中：

- XCodeAgent 在调用 Projector 前执行输入 Schema 校验；
- 模板在执行 Projector 时再次校验；
- 模板内部专属的 reserved pageId 冲突不进入 XCodeAgent Schema。

---

#### 原则五：文件是否发生变化只以 Workspace 为准

`route-projector.v2` 输出中不再包含：

```text
changed
```

Projector 只返回业务投影结果。

XCodeAgent 使用：

```text
capture_workspace_changes()
```

观测 Projector 执行前后的真实 Workspace，并以：

```text
captured.code_change_set
```

作为唯一文件变化事实源。

---

#### 原则六：Projector 失败不得留下部分修改

Projector 必须遵循：

```text
完整校验
→ 计算结果
→ 内存生成最终 routes.tsx
→ 原子写入
```

任何输入错误、pageId 冲突、marker 错误或文件写入错误，都不得在 Workspace 中留下部分路由修改。

---

### 1.3 双方职责边界

#### XCodeAgent 负责

- 从 `ProductPlan.pages` 获取业务页面设计事实。
- 从 `authorization_manifest` 获取页面权限绑定。
- 构造 `route-projector.v2` 输入。
- 按 v2 Input Schema 校验输入。
- 仅在具有 Route Impact 的成功 DAG Finalization 中调用 Route Projector。
- 空 DAG 的 `application` scope 也进入 Finalization 并调用 Projector；`endpoint` 和 `data_source` scope 继续其他收尾步骤但不调用 Projector。
- 校验 v2 Output Schema 和结果集合关系。
- 使用 Workspace Change Capture 捕获真实文件变化。
- 将 Route Projection 结果和真实 `code_change_set` 纳入本次 Build Summary / Evidence。

#### XCodeAgent 不负责

- 判断页面目录结构。
- 判断页面入口文件位置。
- 理解 `routes.tsx`。
- 理解路由 marker。
- 判断具体页面是否应该被 `skipped`。
- 维护模板 reserved pageId。
- 判断 Base / Capability 页面所有权。
- 维护 Route 历史状态。
- 根据历史状态决定是否执行 Projector。

---

#### 模板工程负责

- 定义 Route Projector v2 Descriptor。
- 提供 v2 Input / Output JSON Schema。
- 定义 `pageId → 页面目录 / 路由段` 的映射规则。
- 校验模板内部 reserved pageId 冲突。
- 根据真实 Workspace 判断页面入口是否存在。
- 计算 `appliedPageIds / skippedPageIds`。
- 全量 reconcile 受管业务路由区域。
- 原子写入路由文件。
- 保证重复执行幂等。
- 返回 v2 Output。

#### 模板工程不负责

- 读取 ProductPlan。
- 读取 TechnicalPlan。
- 判断 Build Run 状态。
- 判断 Build Task 是否成功。
- 保存历史 Route 状态。

---

### 1.4 v2 Descriptor 契约

模板源码中维护：

```text
template-source/base/contracts/
├── route-projector.json
├── route-projector-input.schema.json
└── route-projector-output.schema.json
```

Descriptor：

```json
{
  "schemaVersion": "route-projector-contract.v2",
  "protocol": "route-projector.v2",
  "inputSchema": "route-projector-input.schema.json",
  "outputSchema": "route-projector-output.schema.json",
  "command": [
    "node",
    "frontend/scripts/xcodeagent/route-projector.mjs",
    "apply"
  ]
}
```

Template Engine 将三份契约一并投影到生成 Workspace：

```text
.xcodeagent/template-contracts/
├── route-projector.json
├── route-projector-input.schema.json
└── route-projector-output.schema.json
```

XCodeAgent 只通过该目录读取模板公开契约，不硬编码模板源码路径。

---

### 1.5 Route Projector v2 输入契约

输入示例：

```json
{
  "protocol": "route-projector.v2",
  "pages": [
    {
      "pageId": "asset_list",
      "name": "资产管理",
      "resourceKey": "PAGE.ASSET_LIST"
    },
    {
      "pageId": "portal_home",
      "name": "门户首页"
    }
  ]
}
```

字段定义：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `protocol` | 是 | 固定为 `route-projector.v2` |
| `pages` | 是 | 当前已确认 ProductPlan 中全部业务页面 |
| `pageId` | 是 | 页面稳定身份，遵循统一格式规则 |
| `name` | 是 | 页面显示名称 |
| `resourceKey` | 否 | 页面有权限控制时提供 |

输入表达的是：

> 当前设计上应该存在的业务页面全集。

它不声明页面是否已经生成。

---

### 1.6 Route Projector v2 输出契约

输出统一为：

```json
{
  "status": "applied",
  "requestedPageIds": [
    "asset_list",
    "portal_home",
    "report_center"
  ],
  "appliedPageIds": [
    "asset_list",
    "portal_home"
  ],
  "skippedPageIds": [
    "report_center"
  ]
}
```

字段语义：

- `requestedPageIds`：Projector 收到的业务页面 ID，保持输入顺序。
- `appliedPageIds`：Workspace 中实际存在合法页面入口、已进入业务路由的页面。
- `skippedPageIds`：输入中存在，但 Workspace 当前没有合法页面入口的页面。
- `status`：成功时固定为 `applied`。

v2 输出不包含：

```text
changed
pages
```

---

### 1.7 XCodeAgent 输出校验

XCodeAgent Adapter 必须 fail-closed 校验：

```text
status == "applied"
```

三个 ID 数组必须：

- 均为 `string[]`
- 不包含空字符串
- 不包含重复值
- `requestedPageIds` 顺序与输入 `pages[].pageId` 完全一致

同时满足：

```text
applied ∩ skipped = ∅
```

```text
applied ∪ skipped = requested
```

并保持相对顺序：

- `appliedPageIds` 按其在 `requestedPageIds` 中的顺序返回；
- `skippedPageIds` 按其在 `requestedPageIds` 中的顺序返回。

任一条件不满足：

```text
raise TemplateRouteProjectorError
```

不得继续后续 Platform Projection / EDD。

---

### 1.8 pageId 规则

双方统一校验通用格式，例如：

```text
^[a-z0-9]+(?:_[a-z0-9]+)*$
```

如：

```text
asset_list
order_detail
portal_home
```

模板在通用格式校验之后，再执行模板内部 reserved pageId 校验：

```text
business pageId
∩
template reserved pageId
=
∅
```

冲突时 Projector 失败。

reserved pageId 只属于模板内部知识，不进入 XCodeAgent 的业务逻辑。

---

### 1.9 页面存在性规则

模板内部统一按照：

```text
pageId
  ↓
pageDirectoryFromId(pageId)
  ↓
frontend/src/pages/<PageDirectory>/index.tsx
```

判断页面。

例如：

```text
asset_list
    ↓
AssetList
    ↓
frontend/src/pages/AssetList/index.tsx
```

只有当入口：

```text
存在
且
是普通文件
```

才进入 `appliedPageIds`。

以下情况统一进入 `skippedPageIds`：

- 文件不存在；
- `index.tsx` 是目录；
- 路径存在但不是普通文件。

Projector 不负责判断：

- TSX 是否可编译；
- 是否存在默认导出；
- `import.meta.glob` 是否最终可加载。

这些由后续前端构建 / EDD 负责。

---

### 1.10 路由 reconcile 规则

模板不能直接扫描：

```text
frontend/src/pages/**
```

然后全部注册。

因为 Workspace 中可能存在：

- 已从 ProductPlan 删除但未清理的旧页面；
- Base 页面；
- Capability 页面；
- 系统页面；
- 历史残留文件。

Route Projector 必须始终以输入 `pages` 为业务范围：

```text
requestedPages
    ↓
逐项检查 Workspace
    ↓
appliedPages
    ↓
全量 reconcile
```

只覆盖业务受管区域：

```text
// XCODEAGENT_BUSINESS_ROUTES_START

当前 appliedPages

// XCODEAGENT_BUSINESS_ROUTES_END
```

禁止增量 append。

---

### 1.11 页面顺序

`ProductPlan.pages` 顺序具有菜单 / 导航语义。

因此：

- `requestedPageIds` 保持输入顺序；
- `appliedPageIds` 保持在输入中的相对顺序；
- 路由受管区域保持 ProductPlan 顺序；
- 不按 `pageId` 自动排序。

同一页面集合发生顺序变化，属于真实设计变化，应反映到路由文件。

---

### 1.12 Build 执行边界

每个 DAG 成功后均进入 Finalization；Route Projection 仅在成功 DAG 的冻结 Build scope 具有 Route Impact 时执行。

```text
正常 DAG：
所有当前 execution slice 任务成功
        ↓
Post-DAG Finalization
```

```text
空 DAG：
视为成功
        ↓
Post-DAG Finalization
```

以下状态不得进入 Finalization：

```text
failed
cancelled
requires_user_input
waiting_repair
running
pending
```

因此失败 Build 不会通过 Route Projector 改写业务路由。

---

### 1.13 Workspace 与 Code Graph 原则

Build Scheduler 已在 Build Task 调度批次完成后同步对应 Code Graph。

因此当 DAG 成功结束时：

```text
Build Task 产生的业务代码
```

已经进入 Code Graph。

Route Projection 随后直接修改真实 Workspace 中的确定性路由文件。

本方案规定：

> Route Projection 后不额外刷新 Code Graph。

原因：

- Route Projection 位于所有 Build Task 之后；
- 后续 Authorization Platform Projection / EDD 不依赖 Route 修改后的 Code Graph；
- 下一次 Workspace Inspection 会重新扫描真实 Workspace。

Route 的文件变化只进入本次 Build 的：

```text
code_change_set
Build Summary
Evidence
```

---

### 1.14 状态模型

Route Projection 不维护历史 Route 状态。

删除：

```text
routeFacts
previous_successful_route_facts
Route Head
Route Bootstrap
committedPageIds
currentSuccessfulPageIds
Route Generation
```

Route 的正确性只来自：

```text
当前 Confirmed ProductPlan
+
当前真实 Workspace
        ↓
全量 reconcile
```

---

## 二、模板工程改造实施计划

目标仓库：

```text
springboot-template
branch: template_refactor
```

### 2.1 建立 Route Projector v2 正式契约

新增 / 修改：

```text
template-source/base/contracts/
├── route-projector.json
├── route-projector-input.schema.json
└── route-projector-output.schema.json
```

要求：

- Descriptor 固定 `route-projector-contract.v2`；
- protocol 固定 `route-projector.v2`；
- Input / Output Schema 明确定义字段、类型、必填项、唯一性和 pageId 格式；
- 不保留 v1 兼容逻辑。

---

### 2.2 确保契约随模板生成进入 Workspace

Template Engine 必须保证三份文件一起进入：

```text
.xcodeagent/template-contracts/
```

最终生成应用中至少包含：

```text
.xcodeagent/template-contracts/route-projector.json
.xcodeagent/template-contracts/route-projector-input.schema.json
.xcodeagent/template-contracts/route-projector-output.schema.json
```

模板源码契约和最终 Workspace 契约必须字节或语义一致。

---

### 2.3 修改 Route Projector 协议

修改：

```text
template-source/base/frontend/scripts/xcodeagent/route-projector.mjs
```

将：

```text
route-projector.v1
```

升级为：

```text
route-projector.v2
```

旧 v1 输入直接失败。

---

### 2.4 统一输入校验

Projector 执行时至少校验：

- root 为 object；
- protocol 为 `route-projector.v2`；
- `pages` 为数组；
- `pageId` 满足统一格式；
- `pageId` 不重复；
- `name` 为非空字符串；
- `resourceKey` 如存在则为非空字符串。

这些规则必须与 v2 Input Schema 一致。

---

### 2.5 增加模板内部 pageId 冲突校验

模板维护 Base / Capability / 平台保留 pageId。

在任何 Workspace 写入之前执行：

```text
for business pageId:
    if pageId reserved by template:
        fail
```

XCodeAgent 不消费 reserved ID 集合。

冲突时：

- Projector 返回非 0；
- stderr 给出明确错误；
- 不修改 `routes.tsx`。

---

### 2.6 收敛 pageId 映射规则

当前 Runtime 与 Projector 都依赖：

```text
pageDirectoryFromId(pageId)
```

目标是保持唯一规则。

优先方案：

```text
共享纯 JS/TS Page Identity 模块
```

由：

- Runtime
- Node Projector

共同消费。

如果当前构建边界暂时无法直接共享，则必须维护一份统一的 mapping test vectors，至少覆盖：

```text
pageId
pageDirectory
routeSegment
invalid cases
```

并让两端实现执行同一组向量测试。

---

### 2.7 修改页面存在性判断

当前逻辑：

```text
任一页面不存在
→ Projector 整体失败
```

改为：

```text
requestedPages = input.pages

appliedPages = []
skippedPages = []

for page in requestedPages:
    entry = resolvePageEntry(page.pageId)

    if entry exists and is regular file:
        appliedPages += page
    else:
        skippedPages += page
```

页面缺失属于正常投影结果，不是 Projector 异常。

---

### 2.8 保持 ProductPlan 输入顺序

`appliedPages` 不得重新排序。

例如：

```text
Input:
C, A, B

Workspace:
C, B
```

结果必须为：

```text
requestedPageIds = [C, A, B]
appliedPageIds   = [C, B]
skippedPageIds   = [A]
```

最终业务路由顺序：

```text
C
B
```

---

### 2.9 全量 reconcile 受管区域

继续使用：

```text
XCODEAGENT_BUSINESS_ROUTES_START
XCODEAGENT_BUSINESS_ROUTES_END
```

每次以当前 `appliedPages` 完整生成受管区域。

支持：

- 页面新增；
- 页面删除；
- 页面名称变化；
- `resourceKey` 变化；
- 页面顺序变化；
- 重复执行。

---

### 2.10 v2 输出

Projector 成功返回：

```json
{
  "status": "applied",
  "requestedPageIds": [],
  "appliedPageIds": [],
  "skippedPageIds": []
}
```

不返回：

```text
changed
pages
```

---

### 2.11 原子写入 routes.tsx

Projector 必须先完成：

```text
输入 Schema 校验
→ pageId 格式校验
→ reserved pageId 校验
→ 页面存在性计算
→ marker 校验
→ 内存生成完整 nextRoutesText
```

以上任一步失败均不得写入 Workspace。

只有全部成功后才执行：

```text
temporary file
→ fsync / close
→ atomic replace routes.tsx
```

若：

```text
nextRoutesText == currentRoutesText
```

则不写文件。

---

### 2.12 Runtime 页面加载保持不变

继续使用：

```text
import.meta.glob()
```

以及：

```text
pageId
→ pageDirectory
→ index.tsx
```

Route Projector 只生成 Route Definition，例如：

```ts
{
  name: '资产管理',
  pageId: 'asset_list',
  resourceKey: 'PAGE.ASSET_LIST'
}
```

不生成具体页面 import。

---

### 2.13 模板测试

至少覆盖以下测试。

#### Case 1：v2 合法输入

```text
protocol = route-projector.v2
```

正常执行。

---

#### Case 2：v1 输入

```text
protocol = route-projector.v1
```

必须失败。

---

#### Case 3：全部页面存在

```text
Input: A, B
Workspace: A, B
```

输出：

```text
requested = A, B
applied   = A, B
skipped   = []
```

---

#### Case 4：部分页面不存在

```text
Input: A, B, C
Workspace: A, B
```

输出：

```text
requested = A, B, C
applied   = A, B
skipped   = C
```

Projector 成功。

---

#### Case 5：页面删除

第一次：

```text
Input = A, B
Workspace = A, B
```

第二次：

```text
Input = A
Workspace = A, B
```

最终业务路由只能包含 A。

---

#### Case 6：重复执行

相同 Input + Workspace 连续执行两次。

第二次不得产生文件变化。

---

#### Case 7：页面顺序

相同集合不同输入顺序，应产生对应的路由顺序变化。

---

#### Case 8：非法 pageId

例如：

```text
AssetList
asset-list
asset list
```

必须失败，且不得修改文件。

---

#### Case 9：reserved pageId 冲突

例如业务输入模板保留的：

```text
welcome
```

Projector 必须失败，且不得修改文件。

---

#### Case 10：marker 异常

marker：

- 缺失；
- 重复；
- 顺序异常。

必须失败且不得修改文件。

---

#### Case 11：入口不是普通文件

`index.tsx`：

- 不存在；
- 是目录；
- 非普通文件。

均进入 `skippedPageIds`。

---

#### Case 12：stdout v2 契约

精确验证：

```text
status
requestedPageIds
appliedPageIds
skippedPageIds
```

以及数组内容和顺序。

---

#### Case 13：失败原子性

在以下错误下注入测试：

```text
reserved pageId conflict
marker invalid
write failure
```

断言失败前后的 `routes.tsx` 内容完全一致。

---

### 2.14 同步模板文档

同步更新：

```text
template-source/base/frontend/docs/project-structure.md
```

至少说明：

- Route Projector v2 位置；
- Descriptor / Schema 位置；
- pageId 映射职责；
- reserved pageId 属于模板内部；
- 页面存在性判断；
- 受管路由 marker；
- 原子 reconcile；
- stdout v2 结构。

---

### 2.15 模板工程验收

完成后至少执行：

```bash
mvn -f template-engine/pom.xml verify
./scripts/ci/verify-base-frontend.sh
```

模板侧 Done Definition：

```text
1. 只支持 route-projector.v2。
2. Descriptor / Input Schema / Output Schema 随模板一起进入 Workspace。
3. Input Schema 与实际 Projector 校验规则一致。
4. reserved pageId 仅由模板内部维护和校验。
5. 缺失页面只进入 skipped，不导致 Projector 失败。
6. 页面入口必须是普通 index.tsx 文件才可 applied。
7. ProductPlan 页面顺序完整保留。
8. Workspace 残留页面不会被自动注册。
9. Projector 成功输出只包含业务投影结果，不包含 changed。
10. Projector 任意失败不留下部分 routes.tsx 修改。
11. 相同输入重复执行幂等。
12. 模板完整验证命令通过。
```

---

## 三、XCodeAgent 改造实施计划

目标仓库：

```text
XCodeAgent
branch: dev_agent
```

### 3.1 升级 Route Projector Adapter 至 v2

保留：

```text
Backend/app/services/template_route_projector.py
```

主要职责调整为：

```text
load_route_projector_contract()
load_route_projector_input_schema()
load_route_projector_output_schema()
build_route_projector_input()
validate_route_projector_input()
apply_template_route_projection()
validate_route_projector_result()
```

只接受：

```text
route-projector-contract.v2
route-projector.v2
```

---

### 3.2 Descriptor 校验

`load_route_projector_contract()` 必须校验：

```text
schemaVersion == route-projector-contract.v2
protocol      == route-projector.v2
inputSchema   存在
outputSchema  存在
command       为非空字符串数组
```

Schema 路径必须解析在：

```text
.xcodeagent/template-contracts/
```

内部，不允许路径逃逸。

---

### 3.3 输入构造

继续从：

```text
ProductPlan.pages
+
authorization_manifest.bindings.pages
```

构造：

```json
{
  "protocol": "route-projector.v2",
  "pages": [...]
}
```

XCodeAgent 只生成业务 DTO，不包含：

```text
pageDirectory
routePath
routes.tsx
reservedPageIds
template capability pageIds
```

---

### 3.4 输入校验

Projector 调用前按 v2 Input Schema 校验。

XCodeAgent 校验通用协议规则，例如：

- protocol；
- pages 类型；
- pageId 格式；
- pageId 唯一；
- name；
- resourceKey。

XCodeAgent 不校验：

```text
welcome 是否 reserved
Capability 是否占用了某 pageId
```

这些属于模板内部语义。

---

### 3.5 删除历史 Route 比较

删除：

```text
extract_route_facts()
requires_route_projection()
previous_successful_route_facts
```

以及对应调用。

Route Projection 不再依赖历史 Build Route Evidence。

---

### 3.6 删除 Route DAG Task

修改：

```text
Backend/app/services/scope_assembly.py
```

删除：

```text
platform_route_projection
route_projection_required
```

Route Projection 不再进入 Build DAG。

---

### 3.7 删除 Route Task Executor

删除：

```text
Backend/app/services/platform_task_executors/template_route_projection.py
```

以及 Executor Registry 中：

```text
template.route_projection
```

注册。

---

### 3.8 清理 Domain / Compiler 特殊逻辑

清理：

```text
BuildTaskPlatformExecutor
DETERMINISTIC_PLATFORM_EXECUTOR_ALLOWLIST
```

中的：

```text
template.route_projection
```

以及：

```text
Backend/app/services/build_unit_compiler.py
```

中 Route Projection 专属 dependency / compiler 分支。

---

### 3.9 清理 Build Scheduler 特殊逻辑

从：

```text
Backend/app/graph/subgraphs/build.py
```

删除：

```text
platform_executor == "template.route_projection"
```

相关：

- deterministic dispatch；
- task result；
- route task failure；
- route task change set；
- route task status。

Route Projection 改由 Finalization 直接调用 Adapter。

---

### 3.10 清理 Acceptance 特殊逻辑

修改：

```text
engineering_acceptance.py
business_acceptance.py
```

删除 Route Task 专用验收逻辑。

Route Projection 不再作为 DAG Task 参与 Task Acceptance。

---

### 3.11 删除 routeFacts Evidence

删除：

```text
persist_build_run_success_evidence(... routeFacts ...)
load_latest_successful_build_route_facts()
routeFactsEvidencePath
```

及所有 routeFacts 历史比较逻辑。

如果 `persist_build_run_success_evidence()` 仅用于 Route Facts，则删除该函数；若还有其他消费者，则改造成通用 Build Evidence，不再包含 Route baseline。

---

### 3.12 建立统一 Post-DAG Finalization

新增统一入口，例如：

```python
finalize_build(...)
```

固定顺序：

```text
Route Impact?
        ├─ true → Route Projection
        └─ false → 跳过并记录原因
        ↓
Authorization Platform Projection
        ↓
EDD / Acceptance
        ↓
Build Completed
```

只允许 DAG 成功路径进入。

---

### 3.13 空 DAG 进入 Finalization

当前：

```python
if not tasks:
    return ...
```

需要重构。

目标：

```text
有 Tasks
    ↓
Scheduler
    ↓
DAG Success
    ↓
finalize_build()
```

```text
无 Tasks
    ↓
视为 Success
    ↓
finalize_build()
```

页面删除等没有代码生成任务、且 scope 为 `application` 或 `page` 的场景，仍会执行 Route reconcile。`endpoint` 与 `data_source` scope 即使成功完成 Finalization，也不执行 Route Projection。

---

### 3.14 DAG 失败不得进入 Finalization

以下状态直接停留 / 返回，不执行 Route Projection：

```text
failed
cancelled
requires_user_input
waiting_repair
pending
running
```

修复完成后重新达到 DAG Success，才进入 Finalization。

---

### 3.15 Finalization 幂等恢复

不增加：

```text
routeProjectionExecuted = true
```

等状态。

同一 Build Run 的 Finalization 可以因恢复重复进入。

依赖：

```text
Route Projector 全量 reconcile
+
Authorization Projection 幂等
+
EDD 可重复验证
```

保证恢复安全。

---

### 3.16 Route Projection 使用 Workspace Change Capture

调用结构：

```python
captured = capture_workspace_changes(
    workspace=workspace,
    source_tool="template.route_projection",
    action=lambda: apply_template_route_projection(
        workspace,
        route_input,
        run_id=build_run_id,
    ),
)
```

其中：

```text
captured.value
```

是 Projector v2 Output。

```text
captured.code_change_set
```

是唯一 Route 文件变化事实。

---

### 3.17 不再处理 output.changed

删除所有：

```text
result.changed
changed bool 校验
Template changed 与 code_change_set 对比
```

Route 是否真正修改 Workspace，只判断：

```text
captured.code_change_set
```

---

### 3.18 v2 Output 校验

`apply_template_route_projection()` 成功后执行：

```text
validate_route_projector_result()
```

校验：

```text
status == applied
requestedPageIds
appliedPageIds
skippedPageIds
```

以及：

```text
requestedPageIds == input pages[].pageId
```

```text
applied ∩ skipped = ∅
```

```text
applied ∪ skipped = requested
```

和顺序约束。

XCodeAgent 不根据 Page Unit 再判断某个 `skippedPageId` 是否“应该”存在。

真实 Workspace 即最终判断依据。

---

### 3.19 Route Change Set 纳入 Build 变更归集

Route `captured.code_change_set` 合并到本次 Build 的最终变更证据：

```text
Route Change Set
        ↓
merge_code_change_sets(...)
        ↓
code_change_state_update(...)
        ↓
Build Summary / AG-UI / Evidence
```

例如：

```json
{
  "routeProjection": {
    "requestedPageIds": [
      "asset_list",
      "portal_home"
    ],
    "appliedPageIds": [
      "asset_list"
    ],
    "skippedPageIds": [
      "portal_home"
    ],
    "changedFiles": [
      "frontend/src/constants/routes.tsx"
    ]
  }
}
```

其中：

```text
requested / applied / skipped
```

来自模板 v2 Output；

```text
changedFiles
```

来自 Workspace Change Capture。

---

### 3.20 Route Projection 后不刷新 Code Graph

不得因为 Route Change Set 调用：

```text
refresh_code_graph_after_changes()
```

Route Projection 位于所有 Build Task 之后。

下一次 Workspace Inspection 再根据真实 Workspace 重建 / 更新代码图。

---

### 3.21 正式设计输入边界

Route Projection 使用当前 Build execution context 已绑定的正式：

```text
product_plan
project_plan.authorization_manifest
```

Build 完成前不允许修改前序正式设计。

不新增 Route 专用：

```text
ProductPlan Snapshot
TechnicalPlan Snapshot
```

机制。

若恢复时缺少正式设计输入：

```text
Finalization failed
```

不能回退到：

- 当前 UI 状态；
- 最新会话值；
- 空默认值；
- 临时 Workspace 文件。

---

### 3.22 XCodeAgent 回归测试

至少覆盖：

| 场景 | 断言 |
| --- | --- |
| application/page DAG 完成 | DAG 成功后进入统一 Finalization 并执行 Route Projection |
| endpoint/data_source DAG 完成 | DAG 成功后进入统一 Finalization，但跳过 Route Projection 并继续权限收尾 |
| application 空 DAG | 不提前 return，仍进入 Finalization 并执行 Route Projection |
| DAG failed | 不调用 Route Projector |
| waiting repair | 不调用 Route Projector |
| repair 后成功 | 进入 Finalization |
| v1 Descriptor | fail-closed |
| v2 Descriptor | 正常加载 |
| Input Schema 不匹配 | 调用模板前失败 |
| Output Schema 不匹配 | Projector 结果 fail-closed |
| 部分页面不存在 | 接受模板返回 skipped |
| reserved ID 冲突 | 模板失败，XCodeAgent 不维护具体 ID |
| Route 有实际文件变化 | code_change_set 包含 routes.tsx |
| Route 无文件变化 | Route code_change_set 为空 |
| Finalization 重入 | 第二次执行保持幂等 |
| Route 后置流程失败再恢复 | 可重新执行 Projector |
| Route Projection | 不调用 Route 专用 Code Graph refresh |
| 正式设计输入缺失 | Finalization fail-closed |

删除旧测试：

```text
platform_route_projection 必须出现在 DAG
routeFacts 决定是否追加 Route Task
上一成功 Build routeFacts 是比较基线
Route Task Acceptance
```

---

### 3.23 XCodeAgent Done Definition

```text
1. 只支持 route-projector.v2。
2. Build DAG 不包含 platform_route_projection / template.route_projection。
3. Route Projection 不读取、比较或保存 routeFacts。
4. DAG 成功路径与空 DAG 进入统一 Finalization。
5. DAG 失败、等待确认、等待修复时不执行 Route Projection。
6. Route Input 按模板公开 v2 Schema 校验。
7. XCodeAgent 不维护 reserved pageId。
8. XCodeAgent 不对 skippedPageIds 做 Page Unit 二次判断。
9. v2 Output 严格校验集合和顺序。
10. Workspace Change Capture 是 Route 文件变化唯一事实源。
11. Projector Output 不包含 changed。
12. Route Change Set 纳入 Build Summary / Evidence。
13. Route Projection 后不额外刷新 Code Graph。
14. Finalization 可幂等重入。
15. 正式设计输入缺失时 fail-closed。
```

---

## 四、关键实施断点及解决方案

### 4.1 断点一：空 DAG 收尾

#### 问题

当前 `run_build_scheduler()` 存在：

```python
if not tasks:
    return ...
```

如果只删除 Route DAG Task，空 DAG 将直接完成，不会执行 Route Projection。

典型场景：

```text
ProductPlan 删除页面 B
+
没有新的 Build Task
```

如果没有 Finalization，旧 B 路由无法清理。

#### 解决方案

所有 DAG 成功路径统一进入：

```text
finalize_build()
```

包括：

```text
有 DAG Task
→ 全部成功
→ Finalization
```

```text
空 DAG
→ 视为成功
→ Finalization
```

---

### 4.2 断点二：恢复与幂等语义

#### 问题

Build 存在：

- retry；
- repair；
- workflow resume；
- Finalization 后置步骤失败后的恢复。

不能把“每次 Build 执行一次 Route Projection”理解为物理调用一次。

#### 解决方案

定义：

> 同一 Build Run 的 Finalization 可以重复进入，所有步骤必须幂等。

不维护：

```text
routeProjectionExecuted
```

相同输入和 Workspace 再次执行 Projector，应保持 Workspace 不变。

---

### 4.3 断点三：Route 文件变化事实

#### 问题

如果由模板声明：

```text
changed = true / false
```

则会与 XCodeAgent 的实际 Workspace 观测形成两个事实源。

#### 解决方案

v2 删除 `changed`。

唯一事实源：

```text
capture_workspace_changes()
        ↓
captured.code_change_set
```

Build Summary、changedFiles、审计全部来自真实 Workspace 差异。

---

### 4.4 断点四：Projector 输出可靠性

#### 问题

模板通过 stdout 返回 JSON，不能直接信任。

#### 解决方案

双方定义正式 v2 Output Schema。

XCodeAgent 进一步验证：

```text
requested == input pages
applied ∩ skipped == ∅
applied ∪ skipped == requested
数组顺序合法
```

任一失败均阻止后续 Finalization。

---

### 4.5 断点五：正式设计输入变化

#### 问题

理论风险：

```text
旧设计构建 Workspace
+
新设计执行 Route Projection
```

#### 流程约束

XCodeAgent Build 生命周期中：

```text
前序正式设计确认
→ Build
→ Build 完成
→ 才允许重新修改前序设计
```

#### 解决方案

不增加 Route 专用设计快照。

Route Projection 只使用当前 Build execution context 已绑定的正式设计输入。

---

## 五、跨仓库契约一致性检查表

实施时双方必须逐项确认。

| 契约项 | 模板工程 | XCodeAgent |
| --- | --- | --- |
| Protocol | `route-projector.v2` | 只接受 / 生成 v2 |
| Descriptor | `route-projector-contract.v2` | 严格校验 v2 |
| Input Schema | 模板公开 | 调用前按同一 Schema 校验 |
| Output Schema | 模板公开 | stdout 按同一 Schema 校验 |
| pageId 通用格式 | 校验 | 校验 |
| reserved pageId | 模板内部校验 | 不维护 |
| pageId → directory | 模板内部 | 不理解 |
| 页面存在性 | 真实 Workspace | 不二次判断 |
| applied / skipped | 模板计算 | 直接消费并校验结构 |
| 页面顺序 | 保持输入顺序 | 保持 ProductPlan 顺序 |
| routes.tsx | 模板拥有 | 不理解内部结构 |
| marker | 模板拥有 | 不理解 |
| 文件是否变化 | 不在 Output 声明 | Workspace Change Capture |
| Projector 失败原子性 | 模板保证 | 依赖该保证 |
| Route 历史状态 | 不保存 | 不保存 |
| Code Graph Refresh | 无 | Route 后不刷新 |

---

## 六、最终执行流程

```text
Confirmed ProductPlan / TechnicalPlan
                ↓
        Build DAG Planning
                ↓
        Build DAG Execution
                ↓
      ┌─────────┴─────────┐
      │                   │
   DAG Success          DAG 非成功
      │                   │
      │                停止 / 等待
      │                   │
      ▼                   └─ 不执行 Route Projection
Post-DAG Finalization
      │
      ├─ 构造 route-projector.v2 Input
      ├─ v2 Input Schema 校验
      │
      ▼
Template Route Projector
      │
      ├─ v2 协议校验
      ├─ 通用 pageId 校验
      ├─ reserved pageId 校验
      ├─ 检查真实 Workspace 页面入口
      ├─ 计算 applied / skipped
      ├─ 全量 reconcile
      └─ 原子写入 routes.tsx
      │
      ▼
v2 Output
      │
      ├─ Output Schema 校验
      └─ requested / applied / skipped 关系校验
      │
      ▼
Workspace Change Capture
      │
      └─ 生成真实 Route code_change_set
      │
      ▼
Authorization Platform Projection
      │
      ▼
EDD / Acceptance
      │
      ▼
Build Completed
```

---

## 七、最终职责模型

```text
ProductPlan / Authorization Manifest
            │
            │ 设计事实
            ▼
        XCodeAgent
            │
            │ route-projector.v2
            ▼
    Template Projector
            │
            ├─ 模板内部 pageId 规则
            ├─ 真实 Workspace 页面
            ├─ applied / skipped
            └─ 原子 reconcile
            │
            ▼
        routes.tsx
            │
            │ Workspace Change Capture
            ▼
  code_change_set / Build Summary
```

最终原则：

> **设计意图来自正式规划，运行事实来自真实 Workspace。**

> **模板负责解释 Workspace 中哪些页面可路由；XCodeAgent 负责观测 Projector 对 Workspace 实际改了什么。**

> **双方不重复声明同一事实，不维护 Route 历史状态，不让 XCodeAgent 理解模板内部结构。**
