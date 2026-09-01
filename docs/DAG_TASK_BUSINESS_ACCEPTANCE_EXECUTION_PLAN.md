# DAG 单任务业务验收实施计划

## 1. 文档目标

本文档用于指导 XCodeAgent 分阶段实现 DAG 单任务完成后的业务自检能力。

本能力只回答一个问题：

> 当前 DAG 叶子任务提交的代码交付物，是否实现了该任务从已确认正式产物中分配到的业务责任？

本能力不负责：

- 运行后续单元测试；
- 验证多个 Unit 的真实协作；
- 启动前后端并进行浏览器点击；
- 执行端到端测试；
- 替代应用预览和最终人工验收。

工程检查负责文件变更、授权范围、交付物入口和导出结构；本文新增的业务检查负责判断代码内容是否实现了当前任务
应承担的 API、数据映射和分层调用契约。

## 2. 当前问题

当前 Build Task 的验收链路为：

```text
任务规划模型被要求返回空 acceptance_criteria / acceptance_checks
→ 模型输出边界再次强制置空
→ 任务归一化再次置空
→ engineering_acceptance 根据 change_scope 等信息生成 acceptance_checks
→ acceptance_checks.description 再投影为 acceptance_criteria
→ Build Scheduler 执行工程检查
```

这导致：

1. Build Task 的 `acceptance_criteria` 实际只是工程检查文案，不是业务验收标准；
2. 当前检查能够确认文件是否按计划变更，但不能确认文件内容是否实现业务目标；
3. PageImplementationContract、EndpointDetail、EntityDesign 和 API Contract 中已有的业务事实没有被编译为任务内检查；
4. `frontend.code`、`backend.code` 粒度过粗，无法说明一个叶子任务实际交付页面、API 模块、Repository、Service 还是 Controller；
5. 同一个 endpoint Unit 中不同分层任务可能被错误地分配完整 endpoint 验收责任。

目标工程检查链路应直接收敛为：

```text
engineering_acceptance 根据 change_scope、allowed_paths、source_refs 和 deliverables 生成 acceptance_checks
→ Build Scheduler 执行 acceptance_checks
```

任务规划模型不再输出 `acceptance_criteria`、`acceptance_checks` 或
`verification_commands`，任务归一化也不再为它们创建空数组占位。工程检查只由
`engineering_acceptance` 拥有和生成。

## 3. 核心设计决策

### 3.1 删除 Build Task 的 `acceptance_criteria`

仅删除 Build Task 中由工程检查文案投影得到的 `acceptance_criteria`。

不得删除或改变以下正式产物中的业务验收字段：

- RequirementSpec `acceptance_criteria`；
- ProductPlan 页面 `acceptance_criteria`；
- ProductPlan `product_acceptance_criteria`；
- PageImplementationContract `productAcceptance`；
- EndpointDetail `acceptance_criteria`；
- EntityDesign/EntityDetail `acceptance_criteria`。

Build Task 改为保存两套互不混淆的检查：

```json
{
  "acceptance_checks": [],
  "business_acceptance_checks": []
}
```

- `acceptance_checks`：沿用现有字段，表示工程检查；
- `business_acceptance_checks`：新增字段，表示当前任务的业务实现一致性检查。

### 3.2 检查标准由平台编译

任务规划模型只负责声明任务边界和交付物，不生成任何验收字段或验证命令。

任务规划模型允许输出的当前任务字段收敛为：

```text
id
unit_id
owner
task_type
title
description
dependencies
change_scope
allowed_paths
source_refs
deliverables
requires_capabilities
provides_capabilities
impact_scope
can_run_in_parallel
parallel_reason
status
```

以下字段属于平台所有，不得出现在模型任务输出契约或模型 JSON 示例中：

```text
acceptance_criteria
acceptance_checks
business_acceptance_checks
verification_commands
acceptance_evidence
business_acceptance_evidence
business_acceptance_summary
```

模型输出解析边界应按允许字段投射任务候选。模型即使返回额外平台字段，也不得复制到候选任务；
不再保留针对验收字段的“先要求空数组、再专门置空”逻辑。

```text
任务规划模型
→ 只生成任务边界、change_scope、deliverables、source_refs
→ 平台归一化任务
→ 平台合并完全重复任务
→ 平台编译 Unit 和依赖
→ 平台生成 acceptance_checks
→ 平台生成 business_acceptance_checks
→ 平台校验完整 DAG
```

原因：

- 防止模型降低验收门槛；
- 防止模型发明不存在的 verifier；
- 防止检查引用被合并或改写前的旧 task/path；
- 保证检查来源于已确认正式产物；
- 保证相同输入生成稳定检查 ID 和预期值。

工程检查和业务检查的生成职责分别固定为：

```text
engineering_acceptance
→ 生成 acceptance_checks

business_acceptance
→ 生成 business_acceptance_checks
```

`acceptance_checks.description` 直接供前端展示、Scheduler失败信息和Repair上下文使用，
不再额外投影出一份重复的 Build Task `acceptance_criteria`。

### 3.3 执行 Agent 不能给自己判定通过

Frontend/Backend Owner Agent：

- 实现任务；
- 返回变更文件和说明；
- 可以提供自检观察，但只作为补充证据；
- 不得修改两类检查；
- 不得决定最终验收状态。

最终检查由平台拥有的 `BusinessAcceptanceVerifier` 执行。

### 3.4 BusinessAcceptanceVerifier 是确定性平台服务

当前 `BusinessAcceptanceVerifier` 是普通后端服务和确定性检查调度器，不是Agent：

```text
BusinessAcceptanceVerifier
└── Deterministic Verifier Registry：九种固定代码检查器
```

当前不实现或调用 `BusinessAcceptanceReviewAgent`。未来是否需要语义Agent，必须等待相关能力稳定后
重新评估，不能作为当前Verifier的隐式fallback。

### 3.5 不在单任务自检中使用浏览器点击

当前阶段明确禁止默认使用浏览器点击，原因包括：

- Frontend Agent 当前不启动 dev server；
- 页面与 endpoint 可以并行生成，页面任务结束时真实后端未必完成；
- 构建、单元测试和预览属于后续阶段；
- 当前 integration runner 明确跳过 E2E；
- 浏览器点击会把单任务检查扩大为运行时集成检查。

单任务业务检查验证的是“代码是否实现正式业务契约”，不是“完整应用运行后一定正确”。

### 3.6 当前契约直接更新

本项目只支持当前契约。本实施不增加：

- 历史 DAG 迁移；
- 新旧字段双读；
- fallback alias；
- 双写逻辑；
- 版本探测。

实现时直接同步更新当前 `build-dag.v3` 的生产者、消费者、测试和展示。

### 3.7 当前实施范围

当前只实施一个确定性小闭环，交付顺序固定为：

```text
Phase 1：清理字段并建立deliverables契约
→ Phase 2：实现确定性业务验收MVP
→ Phase 5：接通Repair、展示和可观测性
```

原Phase 3和Phase 4暂停，不属于当前实施范围和完成条件。原因是页面
permission、actions、状态处理以及部分后端语义能力仍在开发中，现在固化检查结构、
Review Agent提示或代码判定规则，很可能与最终实现契约产生偏差。

Phase 0不作为独立交付阶段；其中的现状特征测试并入Phase 1的第一批实施任务，
用于保护字段清理过程。

当前小闭环只允许平台编译和执行以下确定性检查：

```text
frontend.api_contract
frontend.page_endpoint_usage
frontend.static_data_contract
backend.domain_mapping
backend.repository_contract
backend.application_service_contract
backend.endpoint_contract
backend.external_api_client_contract
backend.external_api_mapping_contract
```

除此之外的业务检查类型，即使出现在上游正式产物中，当前也不得生成占位检查、
不得调用通用Agent猜测验收，更不得默认通过。待相关能力稳定后，重新分析真实实现和正式契约，
再决定是否恢复Phase 3或Phase 4。

## 4. 当前 Unit 与交付物边界

### 4.1 Unit 类型

| Unit 模式 | 作用 | 是否生成业务交付物 |
| --- | --- | --- |
| `application:root` | Unit Graph 根节点 | 否 |
| `frontend:shell` | 前端模板壳能力 | 通常否，多数为模板已满足 |
| `frontend:api-client` | 公共 HTTP Client | 主要是技术能力 |
| `frontend:auth-guard` | 公共鉴权守卫 | 主要是技术能力 |
| `backend:bootstrap` | Spring Boot 公共启动和配置 | 主要是技术能力 |
| `frontend:data:<sourceId>` | 静态数据业务 API 模块 | 是 |
| `backend:endpoint:<contractId>:<endpointId>` | endpoint 分层后端实现 | 是 |
| `page:<pageId>` | 页面及页面消费代码 | 是 |
| `app:integration` | 页面和 endpoint 汇合 | 属于后续集成阶段 |

### 4.2 交付物类型

一个任务可能同时产生多个交付物，因此使用 `deliverables` 数组，不使用单个 `deliverable`。

第一阶段允许的交付物类型：

```text
frontend.page
frontend.api_module
frontend.static_data_module
frontend.shared_capability

backend.domain_mapping
backend.repository
backend.application_service
backend.endpoint_controller
backend.external_api_client
backend.external_api_mapping
backend.bootstrap
```

任务示例：

```json
{
  "id": "order-list-page",
  "unit_id": "page:order-list",
  "owner": "frontend",
  "task_type": "frontend.code",
  "deliverables": [
    {
      "id": "page:order-list",
      "kind": "frontend.page",
      "target_id": "order-list",
      "paths": [
        "frontend/src/pages/OrderList/index.tsx"
      ],
      "provides": [
        "order-list.render",
        "order-list.search"
      ]
    },
    {
      "id": "api-module:orders",
      "kind": "frontend.api_module",
      "target_id": "orders",
      "paths": [
        "frontend/src/apis/orderApi.ts"
      ],
      "provides": [
        "orders.list.client"
      ]
    }
  ]
}
```

### 4.3 交付物校验规则

平台必须校验：

1. `deliverables[].paths` 全部位于任务 `change_scope` 或 `allowed_paths`；
2. `frontend.*` 交付物只能属于 `page:*` 或 `frontend:*` Unit，并由 frontend owner 执行；
3. `backend.*` 交付物只能属于 `backend:*` Unit，并由 backend owner 执行；
4. `frontend.page` 必须包含当前 PageKey 的页面入口；
5. `backend.endpoint_controller` 必须属于当前 endpoint Unit；
6. backend endpoint 任务的实体范围必须是 Unit `source_refs.entity_designs` 的子集；
7. 同一精确路径不能被同批次多个交付物重复拥有；
8. 每个业务检查必须引用一个真实 `deliverable_id`。

## 5. 业务检查契约

### 5.1 字段结构

```json
{
  "business_acceptance_checks": [
    {
      "id": "business:order-api:contract:<digest>",
      "deliverable_id": "api-module:orders",
      "kind": "frontend.api_contract",
      "description": "订单业务 API 模块必须以类型化函数实现已确认接口契约。",
      "sources": [
        {
          "artifact": "api_contract",
          "target_id": "orders.list",
          "pointer": "api_contracts.orders.endpoints.orders.list",
          "sha256": "..."
        }
      ],
      "expected": {
        "endpoint_id": "orders.list",
        "method": "GET",
        "path": "/api/orders",
        "request_schema_ref": "OrderListRequest",
        "response_schema_ref": "OrderListResponse"
      },
      "target_paths": [
        "frontend/src/apis/orderApi.ts"
      ],
      "verification": {
        "mode": "deterministic",
        "verifier": "frontend_api_contract"
      },
      "required": true,
      "verification_stage": "build"
    }
  ]
}
```

### 5.2 字段语义

| 字段 | 说明 |
| --- | --- |
| `id` | 由 kind、sources、expected、target_paths 稳定哈希生成 |
| `deliverable_id` | 当前检查归属的任务交付物 |
| `kind` | 白名单中的检查类型 |
| `description` | 面向用户和 Repair 的简短说明 |
| `sources` | 一个或多个正式产物切片的类型、目标、JSON Pointer 和内容哈希 |
| `expected` | 从正式产物确定性提取的结构化预期 |
| `target_paths` | 本检查允许读取的任务内文件 |
| `verification.mode` | 当前固定为 `deterministic`；不得写入未实现的 `semantic_review` |
| `verification.verifier` | 检查器注册名 |
| `required` | 是否阻断任务完成；第一阶段统一为 true |
| `verification_stage` | 固定为 `build` |

### 5.3 来源哈希

每项检查必须保存当前正式输入哈希。正式输入发生变化后，旧证据不得复用。

哈希来源：

- PageImplementationContract：对当前页面运行时投影做稳定 JSON 哈希；
- EndpointDetail：使用正式引用 `sha256`；
- EntityDesign：对当前任务字段、数据库 bindings、静态数据或外部 API field_mappings 的完整必需切片做稳定 JSON 哈希；
- API Contract：对当前 contract/endpoint/schema 切片做稳定 JSON 哈希。

UI Design 不是业务数据展示契约，当前不作为 Build Task 业务检查的来源。

不得使用只反映文件路径、不反映正式内容的弱引用。

## 6. 业务检查生成规则

### 6.1 前端壳与页面工程边界

`frontend:shell` 是模板已提供的公共能力，包括入口、App、Layout、Provider、自动路由和菜单渲染。
正常 Build 只复用该 Unit，不生成业务检查：

- 工作区证据完整时标记为 `reused` 或 `already_satisfied`；
- 任务修改 shell 禁止文件时由工程检查失败；
- 模板壳缺失时返回模板准备阶段，不用业务检查修复。

`frontend.page` 的核心交付物是 `frontend/src/pages/<PageKey>/index.tsx`，可附带任务授权范围内的
typings、constants、hooks、utils 和可复用 components。以下属于工程检查，由
`engineering_acceptance` 生成：

- 页面入口存在且有 React 组件 `export default`；
- 脚手架占位内容已被替换；
- 新增组件存在有效导出，且被页面或其可达 hook/component 引用；
- 页面没有修改路由、Layout、Provider 等禁止文件。

不从 UI Design 生成信息项或业务字段展示检查。UI Design 只作为样式和交互实现参考，
不是阻塞单任务完成的业务数据来源。

### 6.2 前端业务 API 模块

`frontend.api_module` 通常是 `frontend/src/apis/<biz>Api.ts`；它不等于模板已提供且只读的
`frontend:api-client` / `src/apis/service.ts`。业务 API 模块可与单个页面同任务交付，多页面共享时应拆成
独立前置任务和交付物。

| 检查类型 | 正式输入 | 验证内容 |
| --- | --- | --- |
| `frontend.api_contract` | API Contract endpoint/schema | 导出函数、HTTP method/path、path/query/body 参数位置、请求与响应结构类型、复用公共 service、不实现契约外接口 |

类型检查比较结构而不强制类型名称一致，必须覆盖 required/optional、array、nested object 和 enum。
验证器应使用 TypeScript AST/Compiler API 追踪导出函数和类型引用，不得以“字段名在文件中出现过”代替类型契约检查。

### 6.3 页面接口消费

| 检查类型 | 正式输入 | 验证内容 |
| --- | --- | --- |
| `frontend.page_endpoint_usage` | PageImplementationContract `requiredEndpointIds`、API Contract | 页面或其可达 hook/component 实际调用对应业务 API 导出，不是只 import 未调用，不直接使用 axios/fetch 或硬编码 URL |

检查器从页面入口建立有界 import/call 图，将调用的 API 模块与导出符号关联到已完成前置任务的
`frontend.api_contract` 结构化证据。页面 verifier 不跨任务重新扫描 API 模块源码。它不检查页面展示了哪些字段，
也不运行真实后端。

### 6.4 静态数据交付物

输入：

- EntityDesign `static_design`；
- API Contract；
- EndpointDetail operation semantics。

生成 `frontend.static_data_contract`，覆盖：

- 字段；
- 种子数据；
- 返回 envelope；
- 已声明操作的类型化导出函数；
- 筛选、分页、排序和 create/update/delete 所需的参数与返回结构；
- 数据必须位于业务 API 模块；
- 禁止导入真实 HTTP service；
- 禁止把业务数组散落到页面。

本检查只证明静态数据模块的结构契约。筛选结果、分页边界、排序顺序和变更后状态的真实正确性由单元测试证明，
不得仅凭源码扫描宣称行为已通过。

### 6.5 数据库后端分层交付物

| 交付物 | 业务输入 | 检查类型 | 验证重点 |
| --- | --- | --- | --- |
| `backend.domain_mapping` | EntityDesign字段、database bindings、API Schema | `backend.domain_mapping` | Entity/PO/DTO 字段与类型、表列映射、Converter/Assembler 转换 |
| `backend.repository` | EntityDesign database bindings、EndpointDetail operation semantics | `backend.repository_contract` | Mapper/Repository/RepositoryImpl 方法、selector、返回数量/分页结构、Mapper XML statement 与表列引用 |
| `backend.application_service` | EndpointDetail operation semantics、API Schema | `backend.application_service_contract` | Service 方法、Repository 调用、Assembler/Converter、事务 |
| `backend.endpoint_controller` | API Contract method/path/request/response、EndpointDetail status | `backend.endpoint_contract` | Spring Mapping、参数来源、DTO、状态码、ApplicationService 委托，禁止直访 Repository/Mapper |

不得把 Controller 的 HTTP 验收分配给 Repository，不得把数据库映射验收分配给页面任务。
仅将 EndpointDetail 中结构化且能映射到代码的 operation semantics 编译为检查；无法转换为确定性断言的自由文本不生成检查。

Repository 检查不证明真实 SQL 结果，ApplicationService 检查不证明无法从结构化决策推导的复杂业务分支；
这些行为继续由单元测试和集成测试负责。

### 6.6 外部 API 后端分层交付物

| 交付物 | 业务输入 | 检查类型 | 验证重点 |
| --- | --- | --- | --- |
| `backend.external_api_client` | EntityDesign `external_api_design.connection + operations[]` | `backend.external_api_client_contract` | 当前 Endpoint 关联操作的有效连接、上游 method/path、请求/响应 DTO、项目 HTTP Client；新建实现优先 OpenFeign，已有兼容 HTTP Client 可复用；相同 `operation_id` 复用 Client 方法，禁止凭空增加地址/字段或引入持久化代码 |
| `backend.external_api_mapping` | EntityDesign `external_api_design.operations[].response_handling + field_mappings`、API Schema | `backend.external_api_mapping_contract` | `entity_payload=true` 操作的载荷路径及每个 source_field 到 entity_field 映射、嵌套路径、必要类型/枚举转换；非实体响应不虚构映射，映射责任不落入 Controller |

当前 Build 上下文的外部 API 实体摘要只包含 `mapping_count`，不足以生成字段级映射检查。
BusinessAcceptanceCompiler 必须直接读取已确认 EntityDesign 的完整必需 `field_mappings` 切片并记录哈希；
不得让规划模型重新生成映射预期。

### 6.7 不进入单任务自检的标准

以下标准不得编译为 Build Task `business_acceptance_checks`：

- 页面通过真实后端加载数据；
- 多个 Unit 组合后的完整业务流程；
- 浏览器点击后的真实视觉和交互结果；
- 真实数据库或外部 API 可用性；
- 跨页面导航后的目标页面行为；
- 性能、部署和生产环境可用性；
- 需要人工判断的整体体验。

这些标准留在单元测试、集成测试、预览或最终验收阶段。

## 7. 谁来检查

### 7.1 BusinessAcceptanceCompiler

新增普通后端服务：

```text
Backend/app/services/business_acceptance.py
```

职责：

- 在任务归一化、重复合并和 Unit 编译后运行；
- 识别每个 deliverable；
- 从正式产物切出当前任务责任；
- 生成稳定业务检查；
- 按交付物依赖稳定排序检查；同任务中 `frontend.api_contract` 必须先于 `frontend.page_endpoint_usage`；
- 校验检查来源、类型、路径和交付物归属；
- 不执行源码验收。

### 7.2 BusinessAcceptanceVerifier

新增普通后端服务：

```text
Backend/app/services/business_acceptance_verifier.py
```

职责：

- 在 Owner Agent 返回 completed/already_satisfied 后运行；
- 按顺序读取当前任务的全部业务检查；
- 根据 `kind` 分派白名单 verifier；
- 当前只执行已注册的确定性检查器；
- 当前不调用语义Agent；
- 汇总逐项证据和最终结果；
- 不修改工作区。

伪代码：

```python
def verify_business_acceptance(task, workspace_root, formal_artifacts):
    results = []
    for check in task["business_acceptance_checks"]:
        verifier = BUSINESS_VERIFIER_REGISTRY[check["kind"]]
        results.append(
            verifier.verify(
                check=check,
                task=task,
                workspace_root=workspace_root,
                formal_artifacts=formal_artifacts,
                prior_results=results,
                dependency_evidence=load_completed_dependency_evidence(task),
            )
        )
    return summarize_business_acceptance(results)
```

### 7.3 后续语义验收执行方式

当前暂停，不创建Agent目录、提示词、结果契约或调用链。

恢复该能力前必须重新确认：

- permission、actions、状态处理和后端服务语义的实际代码形态；
- 正式产物能够提供的稳定结构化输入；
- 确定性检查无法覆盖的真实缺口；
- Agent证据如何与当前代码索引和任务范围绑定。

只有上述前置条件稳定后，才能重新决定使用确定性解析、代码图、只读Agent或其他方式；
本文当前阶段不预设最终实现。

### 7.4 Build Scheduler

`build_scheduler.py` 负责：

```text
Owner执行
→ 归属文件差异
→ verify_engineering_acceptance
→ verify_business_acceptance
→ 合并两类结果
→ completed / repair / blocked
```

状态规则：

| 条件 | 结果 |
| --- | --- |
| 工程检查失败 | `acceptance_verification_failed` |
| 业务检查发现明确不符合 | `business_acceptance_failed` |
| 业务检查缺少证据 | `business_acceptance_blocked` |
| 两类检查全部通过 | 保持 `completed` / `already_satisfied` |

## 8. 怎么检查

### 8.1 确定性检查

复用当前工程契约 verifier 的安全原则：

- 只读工作区；
- 目标路径必须位于 workspace；
- 只读取当前任务目标文件；如需依赖任务信息，只消费已完成依赖的结构化验收证据，不跨任务扫描源码；
- 限制单文件大小、引用深度和文件数量；
- 使用固定白名单 kind；
- 返回结构化失败原因和证据。

适合确定性检查的事实：

- 文件、类、函数和导出是否存在；
- HTTP method/path；
- Request/Response Schema字段；
- DTO/TypeScript字段；
- import、export、直接调用和有界可达调用目标；
- 禁止依赖或禁止符号；
- Spring/事务注解；
- 静态数据模块是否错误调用真实service；
- Entity/PO/DTO/上游 DTO 之间的字段映射；
- Repository、ApplicationService、Controller 之间的限定分层依赖。

TypeScript/TSX 类型契约与页面调用链使用平台打包的 tree-sitter AST；Java 和 MyBatis XML 检查同样使用
tree-sitter Java/XML AST。正则只用于 AST 已定位节点内部的路径占位符和 Spring 注解参数归一化，不能跨源码全文
证明类型归属、调用可达性或字段映射已通过。

### 8.2 语义检查

当前不实施。

对于需要理解action顺序、permission行为、复杂状态变化或Service业务分支的验收标准：

- 当前不生成对应 `business_acceptance_checks`；
- 不使用通用Agent临时判断；
- 不把“没有检查能力”转换为passed；
- 保留在后续单元测试、集成测试或最终验收阶段；
- 等相关实现稳定后重新规划Phase 3或Phase 4。

### 8.3 结果判定

```text
确定性断言失败
→ failed

确定性断言通过
→ passed

确定性检查缺少必需文件、正式输入、依赖证据，或当前 AST grammar 无法安全解析
→ blocked
```

禁止把不确定结果当作通过；`blocked` 终止当前 DAG，不进入面向业务源码的 Repair。

## 9. 执行结果契约

任务执行结果新增：

```json
{
  "acceptance_evidence": [],
  "business_acceptance_evidence": [
    {
      "check_id": "business:order-api:contract:<digest>",
      "kind": "frontend.api_contract",
      "status": "passed",
      "verifier": "frontend_api_contract",
      "evidence": "...",
      "facts": {
        "endpoint_exports": [
          {
            "endpoint_id": "orders.list",
            "module": "@/apis/orderApi",
            "export_symbol": "fetchOrderList"
          }
        ]
      },
      "observations": []
    }
  ],
  "business_acceptance_summary": {
    "total": 1,
    "passed": 1,
    "failed": 0,
    "blocked": 0
  }
}
```

字段责任：

- `acceptance_evidence`：现有工程检查证据；
- `business_acceptance_evidence`：新增业务检查证据；
- `business_acceptance_evidence[].facts`：检查器输出的有界机器可读事实，仅供当前任务后续检查或已声明依赖任务消费；
- `business_acceptance_summary`：供 Scheduler、Repair和前端展示使用。

## 10. Repair 与重试规则

### 10.1 工程检查

工程文件差异检查继续按 retry attempt 基线重新编译，保留现有 added/modified 重试语义。

### 10.2 业务检查

业务检查从失败父任务原样继承：

- RepairPlanner不得修改；
- Repair Task不得降低预期；
- Repair Task只能在父任务授权范围内修复；
- 修复后重新执行父任务全部业务检查；
- 正式产物哈希变化时，不得继续Repair，必须返回重新规划。

### 10.3 already_satisfied

文件已存在不能自动证明业务检查通过。

`already_satisfied` 任务必须满足以下之一：

1. 重新执行业务检查并生成当前证据；
2. 存在与当前正式产物哈希、交付物哈希完全匹配的已验证证据。

第一阶段只实现方案1，不实现业务证据缓存。

## 11. 分阶段实施计划

## Phase 0：契约基线与特征测试（并入Phase 1）

### 目标

先冻结当前行为，避免字段清理时误伤正式产物和后续流程。本阶段不作为独立里程碑，
在Phase 1开始时一并完成。

### 修改范围

- 增加当前 Build Task 字段消费清单测试；
- 覆盖 task preparation空字段要求及置空逻辑、工程检查编译、前端投影、Repair、SmallTask等消费者；
- 明确正式产物的 `acceptance_criteria` 不属于本次删除范围。
- 列出 Build Task `verification_commands` 的全部生产者和消费者，确认删除后没有执行能力丢失。

### 重点文件

```text
Backend/tests/test_engineering_acceptance.py
Backend/tests/test_build_task_planner.py
Backend/tests/test_prepare_build_tasks_guard.py
Backend/tests/test_build_subgraph_scheduler.py
Backend/tests/test_build_repair_planner.py
```

### 退出条件

- 当前 Build Task `acceptance_criteria`、模型侧 `acceptance_checks` 和
  `verification_commands` 的所有读写点已列入测试；
- 正式 ProductPlan/EndpointDetail/EntityDesign验收字段有保留测试；
- 没有实现新功能。

## Phase 1：字段清理与交付物契约

### 目标

删除模型验收空字段契约、专用置空逻辑和 Build Task `acceptance_criteria` 投影链路，
让 `engineering_acceptance` 成为 `acceptance_checks` 的唯一生产者，同时引入并验证
`deliverables`。

### 修改内容

1. Task Planner输出 `deliverables`；
2. 从Task Planner提示词、返回格式和JSON示例中删除
   `acceptance_criteria`、`acceptance_checks`、`verification_commands`；
3. 删除 `_reset_model_acceptance_fields`、`_reset_task_acceptance_fields` 及调用点；
4. 模型输出解析和任务归一化只投射允许字段，不再创建上述空数组占位；
5. `engineering_acceptance` 继续根据任务元数据直接生成 `acceptance_checks`，并为 `frontend.page` 增加页面入口、
   default export、占位替换、任务内新增组件导出/可达引用检查；
6. `engineering_acceptance` 不再投影 Build Task `acceptance_criteria`；
7. 删除“criteria必须等于check description”的契约校验；
8. 删除Build Task `verification_commands` 的无效生产和持久化；
9. 增加交付物归属、路径、owner和Unit校验；
10. 更新Repair、SmallTask、Scheduler和前端消费者，使其直接读取结构化
    `acceptance_checks`；
11. 更新前端，把 `acceptance_checks[].description` 直接展示为“工程检查”。

### 重点文件

```text
Backend/app/agents/main/task_preparer.py
Backend/app/services/build_task_planner.py
Backend/app/services/build_unit_compiler.py
Backend/app/services/engineering_acceptance.py
Backend/app/services/build_task_progress.py
Backend/app/services/build_repair_planner.py
Backend/app/services/small_task.py
Frontend/src/renderer/src/typings/workflow.ts
Frontend/src/renderer/src/components/AiChatPanel/components/ProcessSteps/DagGenerationProgress.tsx
Frontend/src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/BuildTaskPlanConfirmation.tsx
```

### 验证

```bash
cd Backend
python3 -m unittest tests.test_engineering_acceptance
python3 -m unittest tests.test_build_task_planner
python3 -m unittest tests.test_prepare_build_tasks_guard
```

```bash
cd Frontend
pnpm build
```

```bash
curl -sS http://127.0.0.1:8000/health
```

### 退出条件

- Build Task JSON不再包含 `acceptance_criteria`；
- Task Planner输出契约和示例不再包含三类空字段；
- `_reset_model_acceptance_fields` 和 `_reset_task_acceptance_fields` 已删除；
- normalizer不再生成验收或验证命令空数组；
- `engineering_acceptance` 是 `acceptance_checks` 的唯一生产者；
- Build Task不再持久化未执行的 `verification_commands`；
- 正式产物的验收字段保持不变；
- 所有任务包含合法 `deliverables`；
- `frontend:shell` 正常只复用模板证据，不生成业务检查；
- `frontend.page` 的入口/default export、占位替换和任务内组件导出/引用由工程检查阻断；
- 工程检查和现有Build调度行为不变；
- 前端不再把工程检查展示为业务验收标准。

## Phase 2：确定性业务验收 MVP

### 目标

实现不依赖Agent的第一批业务检查。

### 首批检查类型

```text
frontend.api_contract
frontend.page_endpoint_usage
frontend.static_data_contract
backend.domain_mapping
backend.repository_contract
backend.application_service_contract
backend.endpoint_contract
backend.external_api_client_contract
backend.external_api_mapping_contract
```

### 新增文件

```text
Backend/app/services/business_acceptance.py
Backend/app/services/business_acceptance_verifier.py
Backend/app/services/business_acceptance_verifiers/frontend_api.py
Backend/app/services/business_acceptance_verifiers/frontend_page.py
Backend/app/services/business_acceptance_verifiers/frontend_static_data.py
Backend/app/services/business_acceptance_verifiers/backend_domain.py
Backend/app/services/business_acceptance_verifiers/backend_repository.py
Backend/app/services/business_acceptance_verifiers/backend_application_service.py
Backend/app/services/business_acceptance_verifiers/backend_endpoint.py
Backend/app/services/business_acceptance_verifiers/backend_external_api.py
Backend/app/services/business_acceptance_verifiers/typescript_inspection.py
Backend/app/services/business_acceptance_verifiers/typescript_ast.py
Backend/app/services/business_acceptance_verifiers/typescript_ast_types.py
Backend/app/services/business_acceptance_verifiers/java_inspection.py
Backend/app/services/business_acceptance_verifiers/java_ast.py
Backend/app/services/business_acceptance_verifiers/java_inspection_support.py
Backend/tests/test_business_acceptance.py
Backend/tests/test_business_acceptance_verifier.py
```

`business_acceptance_verifier.py` 只负责注册、调度和汇总；各 kind 与语言解析能力拆分到独立模块，
避免把九种规则堆入单个服务文件。

同一任务中相同 deliverable kind 的兄弟交付物先聚合为一条业务检查。例如 Entity、PO、DTO、Converter、
Assembler 的路径共同进入一条 `backend.domain_mapping` 检查，Repository 接口与 Mapper/XML 也共同进入一条
`backend.repository_contract` 检查。检查仍以首个 deliverable ID 作为稳定锚点，但 `target_paths` 覆盖该 kind
的完整交付物集合；不得要求任一单文件独立包含整条跨层契约。

### 修改内容

1. 根据 deliverable和正式产物编译检查；
2. 增加检查kind白名单；
3. 增加稳定ID和 `sources[]` hash；
4. 在 DAG validation中拒绝无verifier检查；
5. 为 TypeScript/TSX 契约、页面调用链、Java 分层交付物和 MyBatis XML 实现 tree-sitter AST 解析；
6. 读取已确认 EntityDesign 的完整必需外部 API field_mappings 切片，不使用只含计数的摘要；
7. 将现有工程 `contract_binding` 中的 API/Spring method、path、Schema 语义断言原子迁移到对应业务 kind，
   工程检查只保留文件、授权、入口和导出结构，不重复阻断同一契约；
8. 在Build Scheduler工程检查后调用业务检查；
9. 持久化 `business_acceptance_evidence`；
10. 前端展示业务检查数量、状态和失败原因；
11. 增加 `business_acceptance_failed` 和 `business_acceptance_blocked` 分类。

`business_acceptance_failed` 表示 AST 已得到明确的实现不一致，可以进入 Repair；
`business_acceptance_blocked` 表示正式来源、依赖证据或当前 grammar 无法安全裁决，当前 DAG 终止且不生成代码修复任务。
解析器不支持时 evidence 使用 `facts.reason_code = verifier_unsupported_syntax`，不得降级为全文正则失败。

### 验证场景

#### 前端 API 与页面消费

- 业务 API 导出函数的 method/path、请求结构和响应结构完整：通过；
- `service.get<ResponseType>(path, params)`、interface 继承和 `integer`/TypeScript `number` 等价类型：通过；
- required/optional、嵌套对象、数组或枚举不一致：失败；
- 页面或可达 hook/component 实际调用全部 requiredEndpointIds：通过；
- 页面通过具名 import 别名或 namespace import 调用依赖导出：通过；
- 页面只有本地同名函数而没有对应 API import 绑定：失败；
- 只 import 未调用、直接 axios/fetch 或硬编码 URL：失败；
- 类型字段或函数名只出现在注释中：不得误判通过；
- 目标文件越过任务范围：DAG校验失败。

#### 静态数据

- 内存API模块字段和返回结构正确：通过；
- 错误导入真实service：失败；
- 数据数组散落在页面：失败；
- 缺少契约声明的操作、分页或筛选结构：失败；
- 仅通过源码存在某个分支宣称筛选/排序行为正确：不允许。

#### 数据库后端分层

- Entity/PO/DTO、表列和 Converter/Assembler 映射完整：通过；
- Repository selector、返回 cardinality、分页结构或 Mapper XML statement 错误：失败；
- MyBatis XML 的 mapper、statement id、SQL selector 和列名通过 XML AST 读取：通过；
- ApplicationService 经 Repository 完成约定 operation，且事务声明与 EndpointDetail 一致：通过；
- ApplicationService 直接调用 Mapper，或缺少必需事务：失败。

#### 后端 Endpoint

- method/path/DTO/status/service调用完整：通过；
- 类型级 `@RequestMapping` 与方法级 Mapping 组合后匹配正式路径：通过；
- Mapping、路径或 Service 字段散落在不同类型/方法，未形成同一 handler 实现：失败；
- 路径错误：失败；
- DTO字段缺失：失败；
- Controller绕过Service直接访问Repository：失败。

#### 外部 API 后端分层

- Client 使用已确认上游 method/path 和请求/响应 DTO：通过；
- Client 凭空增加 URL、字段或持久化依赖：失败；
- 全部已确认 source_field 按嵌套路径映射到对应 entity_field：通过；
- 字段映射缺失、反向或只有 `mapping_count` 没有正式映射输入：分别返回 failed 或 blocked。

### 验证命令

```bash
cd Backend
python3 -m unittest tests.test_business_acceptance
python3 -m unittest tests.test_business_acceptance_verifier
python3 -m unittest tests.test_engineering_acceptance
python3 -m unittest tests.test_build_subgraph_scheduler
```

```bash
cd Frontend
pnpm build
```

```bash
curl -sS http://127.0.0.1:8000/health
```

### 退出条件

- 首批检查全部由确定性代码执行；
- 九种检查均有标准正例、反例、假阳性和 blocked 测试；
- Owner Agent不能通过结果字段绕过检查；
- 明确错误稳定失败；
- 证据包含check_id、kind、路径和原因；
- 无法执行的检查返回blocked，不默认通过；
- Repair能够收到业务失败原因。

## Phase 3：后续语义验收能力（暂停，待重新设计）

当前不实施，也不创建占位Agent、检查类型或持久化字段。

permission、actions、页面状态和无法从结构化 EndpointDetail 推导的复杂Service语义的相关实现稳定后，
需要重新从实际代码和正式产物出发设计；
不得直接按本文早期讨论中的假设开工。

恢复条件：

- 相关正式产物字段已经稳定；
- 生成代码具有可识别的稳定实现结构；
- 已完成一轮真实应用样本分析；
- 已确认确定性Verifier无法覆盖的具体问题；
- 新方案经单独评审后更新本文档。

## Phase 4：后续扩展业务检查（暂停，待重新设计）

当前不实施，也不提前注册permission、actions、页面视觉/状态、真实 SQL 结果、外部 API 在线可用性或
其他需要运行时/语义判断的扩展检查类型。后续是否新增这些检查、采用何种名称、输入和验证方式，
以相关能力完成后的重新分析结果为准。

## Phase 5：确定性小闭环加固

### 目标

仅围绕Phase 2已实现的确定性检查，完成业务失败后的Repair、展示和可观测性小闭环。

### 修改内容

1. Repair Task继承父任务业务检查；
2. Repair Planner输入同时包含工程与业务失败证据；
3. 修复后重跑父任务全部检查；
4. SmallTask packet传递结构化业务检查摘要；
5. 前端分开展示工程检查和业务检查；
6. DAG确认页只读展示业务检查来源和目标；
7. 记录九种已支持kind的通过、失败、blocked和平均耗时；
8. blocked达到流程阈值时要求重新规划或人工决策，不无限重试；
9. 不接入语义Agent，不统计语义Agent指标。

### 验证场景

- 工程失败只修工程问题；
- 业务失败修复后原检查通过；
- Repair不能改变检查expected；
- 正式产物hash变化后Repair停止并返回重新规划；
- 前端能够定位失败检查、目标交付物和证据；
- 并发任务证据不会串到其他任务。

### 验证命令

```bash
cd Backend
python3 -m unittest tests.test_business_acceptance
python3 -m unittest tests.test_business_acceptance_verifier
python3 -m unittest tests.test_build_subgraph_scheduler
python3 -m unittest tests.test_build_repair_planner
```

```bash
cd Frontend
pnpm build
```

```bash
curl -sS http://127.0.0.1:8000/health
```

### 退出条件

- Repair闭环不会降低业务标准；
- 任务重试不会复用过期证据；
- 用户能够区分工程失败、业务失败和证据不足；
- 业务Verifier异常不会让任务错误完成；
- 整个闭环只生成和执行Phase 2白名单中的九种确定性检查；
- Phase 3、Phase 4未被任何运行时代码或配置隐式启用。

## 12. DAG 校验规则

任务计划保存前必须拒绝：

1. 缺少 `deliverables` 的可执行代码任务；
2. deliverable路径越过任务授权范围；
3. deliverable kind与owner/Unit不匹配；
4. 业务检查引用未知deliverable；
5. 业务检查kind不在白名单；
6. kind没有注册verifier；
7. `sources` 为空，或任一 source artifact不存在/hash为空；
8. 任一 source target不属于当前Unit或其声明的正式依赖；
9. target_paths越过任务授权范围；
10. expected缺少对应kind要求的字段；
11. 将 integration/runtime验收错误标记为build检查；
12. 模型直接返回任意检查对象并试图覆盖平台编译结果。

## 13. 安全和稳定性要求

- 所有新函数和方法按仓库规则添加中文用途注释；
- 所有源码读取必须限制在workspace和当前任务路径内；跨任务关联只消费结构化验收证据；
- 不读取、输出或保存密钥；
- 当前运行链路不得创建或调用语义验收Agent；
- 模型输出始终视为不可信输入；
- 检查ID、正式输入哈希和目标文件哈希必须可追溯；
- 同一检查不能读取其他并发任务尚未归属的变更作为通过证据；
- verifier异常必须返回blocked/failed，不能吞错后通过；
- 每种检查都必须限制输入文件数量、单文件大小和总上下文；
- 不新增历史兼容分支。

## 14. 测试策略

每个检查类型至少包含：

1. 标准正例；
2. 缺少关键实现的反例；
3. 错误实现的反例；
4. 注释或无关字符串造成的假阳性反例；
5. 越权路径反例；
6. 正式输入哈希过期反例；
7. 无法判断时的blocked用例；
8. Repair继承用例；
9. already_satisfied重新验证用例；
10. 并发任务证据隔离用例。

全阶段回归至少覆盖：

```bash
cd Backend
python3 -m unittest tests.test_business_acceptance
python3 -m unittest tests.test_business_acceptance_verifier
python3 -m unittest tests.test_engineering_acceptance
python3 -m unittest tests.test_build_task_planner
python3 -m unittest tests.test_prepare_build_tasks_guard
python3 -m unittest tests.test_build_subgraph_scheduler
python3 -m unittest tests.test_build_repair_planner
```

涉及前端字段和展示后：

```bash
cd Frontend
pnpm build
```

每次代码变更后：

```bash
curl -sS http://127.0.0.1:8000/health
```

## 15. 非目标

本计划不包含：

- Playwright、Selenium或其他浏览器自动化建设；
- 页面像素级视觉对比；
- 真实前后端联调；
- 真实数据库验收；
- 外部API在线可用性验证；
- 单元测试生成策略调整；
- 集成测试阶段调整；
- 最终应用验收流程调整；
- Build DAG历史格式兼容。

## 16. 当前小闭环完成标准

Phase 1、Phase 2和Phase 5完成后，当前里程碑应满足：

1. Build Task不再使用含混的 `acceptance_criteria`；
2. 工程检查和业务检查具有独立字段、执行器和证据；
3. 每个可执行业务任务明确声明自己的交付物；
4. 每条业务检查都能追溯到已确认正式产物；
5. 每条业务检查只验证当前任务可以独立负责的代码；
6. Owner Agent不能修改检查或自行判定通过；
7. 只生成并执行九种已注册的确定性业务检查；
8. passed结果必须有可复核代码证据；
9. 无法判断时返回blocked，不默认通过；
10. 业务失败可以在原任务范围内进入Repair；
11. 正式产物变化后旧检查和旧证据自动失效；
12. 单任务自检、单元测试、集成测试和最终验收边界清晰且不重复；
13. Phase 3、Phase 4保持暂停，permission、actions和其他未稳定能力不生成占位检查；
14. 当前运行链路不包含语义Agent、Agent fallback或相关持久化契约。
