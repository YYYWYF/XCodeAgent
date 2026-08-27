# 应用模板生成阶段目标、现状与改进计划

## 1. 文档范围

本文定义 TechnicalPlan 确认完成后，到应用具备进入开发条件之前的模板生成阶段，范围包括：

1. 前端模板和后端模板下载；
2. 页面占位文件增量注入；
3. 菜单项增量注入；
4. 模板生成 manifest；
5. 模板生成完成门禁；
6. TechnicalPlan 确认后的单次模板生成，以及失败后的终止状态记录。

本文中的“进入”是指从欢迎页打开应用并准备进入开发会话，不是用户在工作台内切换页面或路由。模板生成仍然是一个整体业务阶段，不为下载、页面、菜单和门禁增加新的生命周期枚举。

## 2. 本阶段最终决策

### 2.1 核心目标

- 在应用工作区准备可用的前端和后端模板工程。
- 根据最新正式 ProductPlan 页面清单补齐缺失页面占位文件。
- 根据同一份 ProductPlan 补齐缺失菜单项。
- 仅在用户确认 TechnicalPlan 后执行一次模板初始化；失败、重启、再次打开和进入工作台都不重新触发。
- 模板下载、页面、菜单和完成门禁的状态统一写入 manifest。
- 只有正式规划产物、manifest 和真实模板文件都满足门禁时，才允许进入开发。

### 2.2 API 骨架决策

本阶段完全取消 API 骨架：

- 不生成 `frontend/src/apis/<biz>Api.ts` 骨架；
- 不推导 API 候选文件名；
- 不新增 API 骨架服务；
- manifest 不包含 `apiSkeletons`；
- 完成门禁不检查 API 文件；
- TechnicalPlan 的 `api_contracts` 不参与模板文件初始化；
- 删除生命周期完成分支中的 `_preload_api_skeletons()` 及其辅助逻辑。

当前 TaskPlanner 可能把尚不存在的 API 文件声明为 `modify`，而 Build 实际产生 `added`。该问题已知，但不在本阶段通过预建候选空文件规避。后续单独统一 TaskPlanner、Frontend Agent 和验收逻辑的 `add/modify` 判断及 API 文件命名契约。

### 2.3 非目标

- 不生成真实业务页面实现。
- 不生成或预建任何业务 API 文件。
- 不重新生成或修改 RequirementSpec、ProductPlan、UiDesign、TechnicalPlan 的业务内容。
- 不把模板初始化作为正式产物确认的替代流程。
- 不把 EndpointDetail、Build DAG、构建、测试和验收混入本阶段。
- 不主动删除正式计划中已经移除的旧页面或菜单。
- 不增加模板专用的计划 hash、revision、版本绑定或迁移机制。
- 不修改 `application-lifecycle.json` 已有的 revision 和 CAS 语义。

## 3. 远端当前架构带来的调整

### 3.1 正式产物已经拆分

远端不再使用单一 `project-plan.json` 承担全部规划事实。模板阶段需要理解以下正式产物：

| 正式产物 | 路径 | 在模板阶段的职责 |
| --- | --- | --- |
| RequirementSpec | `.xcodeagent/specs/requirement-spec.json` | 完成门禁确认 |
| ProductPlan | `.xcodeagent/plans/product-plan.json` | 页面身份、名称、正式路由和菜单输入 |
| UiDesign Manifest | `.xcodeagent/specs/ui-designs.json` | 完成门禁确认；有设计稿时校验已落盘 PageKey 映射 |
| TechnicalPlan | `.xcodeagent/plans/technical-plan.json` | 完成门禁确认，不参与页面、菜单或 API 文件生成 |

ProductPlan 的 `pages` 是页面产品事实的唯一权威来源。TechnicalPlan 的 `pages` 只保存 endpoint 依赖和 action 实现引用，不能用于生成页面名称、正式路由或菜单。

### 3.2 PageKey 边界

- `ProductPlan.pages[].pageId` 是页面身份权威。
- PageKey 通过仓库共享的确定性规则从 pageId 派生。
- `ui-designs.json.pages[].page_key` 是已经落盘的技术映射和设计稿路径依据，不是 UI 阶段可自由修改的产品字段。
- 选择模板、重新生成或确认 UI 不改变 PageKey。
- UiDesign 已确认时，模板初始化应复用并校验对应 page_key；UiDesign 明确跳过时，使用同一共享规则派生 PageKey。
- 不允许 Renderer、模板服务和 Build 上下文分别维护不同的 PageKey 算法。

### 3.3 生命周期确认边界

TechnicalPlan 确认后，生命周期进入：

~~~text
generating_application_template_files
~~~

模板初始化完成后，门禁必须同时验证：

- RequirementSpec 为 confirmed；
- ProductPlan 为 confirmed；
- UiDesign 为 confirmed 或 skipped；
- TechnicalPlan 为 confirmed 且 `artifact_type = technical-plan`。

本阶段沿用远端已有的生命周期 revision、expected revision CAS 和 AG-UI 生命周期事件，不增加新版本字段。

## 4. 本轮实施状态

### 4.1 已落地能力

| 范围 | 当前能力 | 主要源码位置 |
| --- | --- | --- |
| 生命周期 | 持久化初始化阶段、状态、revision、线程 ID 和错误摘要 | `Backend/app/domain/application_lifecycle.py`、`Backend/app/services/application_lifecycle.py` |
| 下载 | Electron 主进程复用有效目录，依次下载缺失的前后端模板，并返回分目标结构化结果 | `Frontend/src/main/index.ts` |
| 下载重试 | 每个仓库最多尝试 3 次，单次 Git 操作超时 120 秒；第三次失败向上抛错 | `Frontend/src/main/index.ts`、`Frontend/src/renderer/src/service/templateApi.ts` |
| 页面写入 | 后端从最新正式 ProductPlan/UiDesign 推导 PageKey，只独占创建缺失占位文件 | `Backend/app/services/frontend_scaffold.py` |
| 菜单写入 | 后端按稳定 PageKey 只追加缺失 `BIZ_MENUS` 项 | `Backend/app/services/frontend_scaffold.py` |
| 并行编排与 manifest | 页面和菜单受控并行，单一写入者原子落盘 manifest | `Backend/app/services/application_template_generation.py` |
| 生命周期动作 | 独立 AG-UI 动作执行 prepare 和 complete，文件任务不阻塞事件循环 | `Backend/app/protocols/application_lifecycle.py` |
| 完成门禁 | 校验四份正式产物、manifest、模板入口、页面和菜单真实文件 | `Backend/app/services/application_lifecycle.py`、`Backend/app/services/application_template_generation.py` |
| 触发边界 | 只有 TechnicalPlan 确认回调会启动 readiness；欢迎页打开、重启、进入工作台和失败态不启动 | `Frontend/src/renderer/src/components/Welcome/ApplicationPagePlanningModal.tsx`、`Frontend/src/renderer/src/pages/AppEntryPage.tsx` |

### 4.2 本轮已处理的旧问题

- Renderer 不再吞掉下载错误，也不再返回伪成功元信息。
- 下载结果包含前后端各自的 status、attempt、path 和最终错误。
- 无法识别的非空目录不再被模板下载流程删除。
- 页面和菜单不再依赖 Renderer 临时 workflow，也不再由 Electron IPC 覆盖写入。
- 新编排器只读取正式 ProductPlan 和 UiDesign，并生成原子 manifest。
- 生命周期成功提交会重读 manifest 和真实文件，不再信任客户端布尔值。
- API 候选骨架和静默预加载逻辑已删除。
- 首页阶段分区、`canOpenApplicationWorkbench` 和 `planningConfirmedAt` 准入字段已删除，应用索引统一承担所有阶段的工作台入口。

## 5. 改进原则

### 5.1 统一读取正式产物

模板初始化不再依赖 Renderer workflow 快照。初始化编排器一次读取并校验：

~~~text
.xcodeagent/plans/product-plan.json
.xcodeagent/specs/ui-designs.json
~~~

随后生成本轮不可变输入：

- 页面预期文件清单；
- 菜单预期 key 清单；
- pageId 到 PageKey 的稳定映射。

RequirementSpec 和 TechnicalPlan 由完成门禁读取，用于确认状态校验，不参与页面和菜单文件生成。

模板阶段不新增或比较 ProductPlan hash。远端正式产物自身已有的上游 hash 规则保持原样，但不作为 template manifest 的版本绑定机制。

### 5.2 TechnicalPlan 确认后的单次初始化

只有用户确认 TechnicalPlan 后，前端才启动这一套流程。模板初始化失败后，生命周期保持终止失败态；应用重启、再次打开、进入工作台或查看失败详情都不会再次启动。

对账步骤：

1. 检查前后端模板目录是否可用；
2. 下载缺失模板；
3. 读取最新 ProductPlan 和 UiDesign Manifest；
4. 推导预期页面文件和菜单 key；
5. 检查工作区真实文件；
6. 只创建或注入缺失项；
7. 原子更新本轮 manifest；
8. 执行完成门禁。

应用进入工作台不依赖模板 readiness。是否可以进入开发并启动预览，仍以生命周期、manifest 和真实文件门禁结果为准。

### 5.3 增量而非重建

- 已存在的页面文件不覆盖。
- 已存在的菜单 key 不重复注入。
- ProductPlan 中新增页面时，只补齐新增页面和菜单。
- ProductPlan 中删除页面时，本阶段不删除旧页面或旧菜单。
- 无法识别的非空模板目录不直接删除，返回明确错误避免覆盖用户文件。

### 5.4 下载失败必须终止初始化

每个确实需要下载的模板仓库最多尝试 3 次：

- 第 1、2 次失败时保留错误并继续重试；
- 第 3 次失败后记录最后一次原始错误；
- 将对应 target 和整体 download 标记为 failed；
- 向上层抛出包含目标、attempt 和最终原因的错误；
- 页面和菜单步骤保持 pending，不继续初始化；
- 生命周期进入现有 `application_template_generation_failed` 分支。

当前阶段不要求并行下载、指数退避、错误分类或只恢复单个失败目标。

### 5.5 页面和菜单受控并行

模板下载完成并读取一次正式产物后，可以并行执行：

- 页面占位检查和增量创建，写入 `frontend/src/pages/**`；
- 菜单检查和增量注入，写入 `frontend/src/constants/menus.ts`。

二者没有必须串行的业务依赖：菜单依赖稳定的页面标识和路由，不依赖页面文件已经创建成功。

并行约束：

1. 下载、正式产物读取、PageKey 校验和两份预期清单推导仍然串行完成；
2. 两个任务共享同一次读取产生的不可变输入；
3. 等待两个任务全部结束，不因一个失败丢失另一个任务结果；
4. 两个任务只返回 expected、existing、created/injected、missing 和 error；
5. 两个任务不直接读改写 manifest；
6. 编排器作为单一写入者统一原子更新 manifest；
7. 任一任务失败时 overall 为 failed；
8. 同一工作区同一时刻只允许一个模板初始化编排任务；
9. manifest 写入完成后才允许串行执行完成门禁。

并行的主要价值是职责隔离和完整收集错误，性能收益是次要的。

## 6. 改进后的流程

### 6.1 首次执行

~~~mermaid
flowchart TD
    A["用户确认 TechnicalPlan"] --> B["生命周期进入模板生成中"]
    B --> C["创建本轮 manifest"]
    C --> D{"前后端模板目录是否可用？"}
    D -->|否| E["下载缺失模板；单个仓库最多 3 次"]
    E --> F{"下载是否成功？"}
    F -->|否| G["manifest 记录失败并抛错"]
    D -->|是| H["download = succeeded"]
    F -->|是| H
    H --> I["读取 ProductPlan 和 UiDesign Manifest"]
    I --> J["推导页面文件和菜单 key"]
    J --> K["并行补齐页面占位"]
    J --> L["并行补齐菜单项"]
    K --> M["等待两个任务全部结束"]
    L --> M
    M --> N["单一写入者原子更新 manifest"]
    N --> O["完成门禁重读正式产物、manifest 和真实文件"]
    O --> P{"是否全部满足？"}
    P -->|否| Q["进入终止失败状态，不重新触发"]
    P -->|是| R["ready_for_workbench"]
~~~

### 6.2 后续打开与失败状态

~~~mermaid
flowchart TD
    A["应用重启或再次打开应用"] --> B{"当前生命周期"}
    B -->|ready_for_workbench| C["直接进入工作台，不启动模板生成"]
    B -->|application_template_generation_failed| D["只展示失败信息，不提供模板重试"]
    B -->|其他阶段| E["等待规划流程，不进入模板生成"]
~~~

## 7. 模板生成 manifest

建议路径：

~~~text
.xcodeagent/template-generation-manifest.json
~~~

建议最小结构：

~~~json
{
  "generationId": "generation-...",
  "workspaceRoot": "...",
  "planningArtifacts": {
    "productPlanJsonPath": ".xcodeagent/plans/product-plan.json",
    "uiDesignsJsonPath": ".xcodeagent/specs/ui-designs.json"
  },
  "steps": {
    "download": {
      "status": "succeeded",
      "attempt": 1,
      "failedTargets": [],
      "targets": {
        "frontend": {
          "status": "succeeded",
          "path": "frontend",
          "attempt": 1,
          "error": null
        },
        "backend": {
          "status": "succeeded",
          "path": "backend",
          "attempt": 1,
          "error": null
        }
      }
    },
    "templateFiles": {
      "status": "succeeded",
      "expectedFiles": [],
      "existingFiles": [],
      "createdFiles": [],
      "missingFiles": [],
      "error": null
    },
    "menus": {
      "status": "succeeded",
      "path": "frontend/src/constants/menus.ts",
      "expectedKeys": [],
      "existingKeys": [],
      "injectedKeys": [],
      "missingKeys": [],
      "error": null
    },
    "gate": {
      "status": "succeeded",
      "checkedAt": "...",
      "error": null
    }
  },
  "overall": {
    "status": "succeeded",
    "lastCompletedStep": "gate",
    "updatedAt": "..."
  }
}
~~~

约束：

- manifest 不包含 `apiSkeletons`。
- manifest 不保存 ProductPlan hash、revision 或历史版本绑定。
- status 使用 pending、running、succeeded、failed。
- download.targets 记录每个模板的尝试次数和最终错误。
- 页面和菜单记录预期项、原有项、本次新增项和最终缺失项。
- 页面和菜单任务不直接写 manifest，由编排器收齐两个结果后统一原子写入。
- TechnicalPlan 确认时计算本次 expected 集合；后续打开应用不重新启动 readiness。
- overall.succeeded 只代表 TechnicalPlan 确认后这一次 readiness 和门禁成功。

## 8. 完成门禁

完成门禁不能只相信 Renderer 提交的 `succeeded`。至少验证：

1. 生命周期处于合法的模板生成阶段；
2. RequirementSpec 已确认；
3. ProductPlan 已确认；
4. UiDesign 已确认或明确跳过；
5. TechnicalPlan 已确认且 artifact_type 正确；
6. manifest 结构有效；
7. download、templateFiles、menus 均为 succeeded；
8. frontend 和 backend 模板目录包含可识别入口；
9. 根据最新 ProductPlan 和 PageKey 映射推导的页面文件均存在；
10. `menus.ts` 可读取且所需菜单 key 均存在。

门禁不检查任何 API 文件，也不在成功判断过程中静默创建文件。

如果 ProductPlan 在初始化后、门禁前发生变化，当前门禁按最新内容拒绝本次结果；必须回到规划流程并重新确认 TechnicalPlan 后，才允许启动新一轮模板生成。

## 9. 生命周期与 AG-UI 边界

### 9.1 生命周期阶段

本轮保持：

~~~text
generating_application_template_files
  ├─ success -> ready_for_workbench
  └─ failure -> application_template_generation_failed（终止）
~~~

`ready_for_workbench`、`application_template_generation_failed` 都不允许再次进入模板生成阶段。任何新一轮模板生成都必须先完成新的规划流程并重新确认 TechnicalPlan。

### 9.2 revision

`application-lifecycle.json` 的 revision 是仓库已有的生命周期并发控制字段：

- 首个快照从 1 开始；
- 有效状态转换时递增；
- 写入时通过 expected revision 做 CAS 冲突检查。

本轮直接沿用该逻辑：

- 不新增 lifecycle revision；
- 不新增 manifest revision；
- 不用 lifecycle revision 表示 ProductPlan 或模板文件版本；
- 不升级 lifecycle schemaVersion。

### 9.3 AG-UI 动作

模板初始化继续使用独立 `/application-lifecycle/run` AG-UI 端点：

- `prepare_template_generation`：接收结构化下载结果，读取正式产物，执行页面和菜单增量初始化，写入 manifest；
- `complete_template_generation`：重读正式产物、manifest 和真实文件，决定 ready 或 failed。

两个动作都必须保留完整 AG-UI run 生命周期、自定义结果或错误事件、状态快照和 run finish。

## 10. 实施调整

### 10.1 下载

- 已存在且有效的模板直接复用。
- 非空但无法识别的目录不删除，返回明确错误。
- 每个缺失仓库最多尝试 3 次。
- 第 3 次失败后抛出最后一次原始错误。
- IPC 返回前端和后端各自的 status、attempt、path 和 error。
- Renderer 不再吞掉下载错误或返回伪成功。

### 10.2 页面

- 从 `product-plan.json.pages` 读取页面。
- 通过共享 PageKey 规则和 UiDesign 映射得到稳定目录名。
- 只用独占创建方式写入缺失的 `frontend/src/pages/<PageKey>/index.tsx`。
- 已存在文件只记录为 existing，不覆盖真实实现。

### 10.3 菜单

- 从 ProductPlan 页面名称和正式路由推导预期菜单项。
- ProductPlan v6 页面为拍平列表，本阶段只补齐顶层页面入口，不虚构菜单层级。
- 按稳定 PageKey 检查和追加缺失项。
- 不整体替换 `BIZ_MENUS`。
- 菜单文件缺失、无法解析或写入失败时返回明确失败。

### 10.4 删除 API 骨架

- 删除 `_preload_api_skeletons()` 调用和实现。
- 删除 `_api_module_candidates()`、相关 camelCase 候选推导和骨架源码生成逻辑。
- 不把这些逻辑迁移到新的模板初始化服务。
- 删除 API 骨架相关测试、manifest 字段和门禁断言。
- 保留后续 `add/modify` 修复任务，不在本阶段引入替代兜底。

### 10.5 去除重复初始化路径

- Renderer 不再从 workflow 生成页面和菜单。
- 删除 `workspace:write-template-pages` IPC；页面和菜单不再由 Electron 写入。
- 后端模板初始化编排器成为页面和菜单 readiness 的唯一业务入口。
- 主 Workflow 的 workspace inspection 不得再次整体重写菜单或覆盖页面；若保留兼容调用，也只能复用同一套增量服务。

## 11. 实施顺序（本轮已按此落地）

1. 更新模板生成 manifest，步骤固定为 download、templateFiles、menus、gate。
2. 增加正式 ProductPlan、UiDesign Manifest 的读取和 PageKey 映射校验。
3. 修复模板下载结果结构和三次失败抛错。
4. 将页面写入改成“推导预期、检查存在、只创建缺失文件”的结构化步骤。
5. 将菜单写入改成“检查稳定 key、只追加缺失项”的结构化步骤。
6. 通过后端等价机制受控并行执行页面和菜单。
7. 由单一编排器统一原子写 manifest。
8. 改造完成门禁，重读四份正式产物、manifest 和真实文件。
9. 删除全部 API 骨架、候选文件和静默预加载逻辑。
10. 删除 Renderer workflow 页面初始化主路径，避免双写。
11. 调整应用入口，使 readiness 只由 TechnicalPlan 确认回调执行。
12. 增加单次触发、失败终止、部分失败和门禁拒绝测试。
13. 后续另立任务修复 API 文件确定性命名与 TaskPlanner `add/modify`。

## 12. 验收标准

- 第 1 次或第 2 次下载失败、第 3 次成功时，可以继续初始化。
- 连续 3 次下载失败时，manifest 记录 attempt = 3 和最终错误，并向上层抛错。
- 前端或后端任一模板最终失败时，页面和菜单步骤不执行，也不能进入开发。
- 已存在且有效的模板不会重复下载。
- 非空但无法识别的目录不会被自动删除。
- 应用重启、再次打开和进入工作台不依赖 Renderer workflow，也不会恢复或重新触发模板初始化。
- ProductPlan 页面使用 `pageId` 作为身份权威，PageKey 与 UiDesign 映射一致。
- UI 选择模板、重新生成或确认不会造成模板页面 PageKey 变化。
- 已存在页面文件不会被覆盖，已存在菜单不会重复注入。
- ProductPlan 发生变化后，必须重新完成规划并确认 TechnicalPlan，不能通过再次进入开发触发补齐。
- ProductPlan 删除页面时，本阶段不自动删除已有文件或菜单。
- 页面或菜单任一步失败时，manifest 整体失败，同时保留另一步的完整结果。
- 页面和菜单不会竞争写 manifest。
- 同一工作区的重复打开请求不会启动模板初始化任务。
- manifest 缺失、损坏、必需步骤未完成或真实产物缺失时，完成门禁拒绝进入开发。
- 旧 manifest 成功或最新 ProductPlan 存在新增缺失项时，都不能直接触发模板生成；必须重新完成规划并确认 TechnicalPlan。
- 完成门禁校验 RequirementSpec、ProductPlan、UiDesign、TechnicalPlan、manifest、模板目录、页面和菜单。
- 模板阶段不创建、检查或修改任何业务 API 文件。
- `_preload_api_skeletons()` 和 API 候选文件逻辑不再参与生命周期成功路径。
- 当前 API 文件 `add/modify` 不一致明确留待后续任务处理。
- application-lifecycle.json 继续使用现有 revision 和 expected revision CAS 语义。
- 不新增 ProductPlan hash、manifest revision 或模板版本绑定逻辑。
- 现有 AG-UI 生命周期事件和生命周期原子写入语义保持不变。
