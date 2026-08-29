# DAG 任务业务自检范围一致性分析

## 结论

当前实现不满足“每个任务仅关注自身交付内容是否符合业务预期”。同一个任务的三种范围没有统一：

```text
任务 Unit / source_refs 范围
≠ 业务验收 expected 范围
≠ 后端 Agent implementation_contract 范围
```

后端任务因此最容易出现“Agent 按较窄范围实现，但验收按较宽范围检查”的失败。

## 1. 当前端到端链路

```text
prepare_build_tasks
  → create_build_task_plan
  → apply_unit_compilation
  → compile_business_acceptance
  → 保存 Build DAG
  → Build Scheduler
  → execution_task_packet
  → Backend DataSource Agent
  → verify_business_acceptance
  → Java AST verifier
  → Repair
```

主要入口：

- `Backend/app/services/build_task_planner.py:1124`
- `Backend/app/services/build_unit_compiler.py:9`
- `Backend/app/services/business_acceptance.py:66`
- `Backend/app/graph/subgraphs/build.py:476`
- `Backend/app/agents/data_source/prompt_context.py:88`

## 2. P0：页面级 Endpoint 范围被复制给每个后端 Endpoint Unit

页面 BuildContext 会收集页面下所有 Endpoint：

- `Backend/app/services/build_context_resolver.py:126`
- `Backend/app/services/build_context_resolver.py:152`

例如：

```text
页面 endpoint_ids = [orders.list, customers.list]
```

但每个后端 Unit 实际已经拥有自己的正式身份：

```text
backend:endpoint:orders-api:orders.list
```

Unit Skeleton 也明确保存了 `api_contract_id` 和 `endpoint_id`（`Backend/app/services/build_unit_skeleton.py:184`）。问题在于 Unit 编译时没有使用 Unit 自己的 Endpoint，而是继续使用页面上下文的全部 Endpoint：

- `Backend/app/services/build_unit_compiler.py:283`
- `Backend/app/services/build_unit_compiler.py:293`

因此订单任务会得到：

```text
task.source_refs.endpoint_ids = [orders.list, customers.list]
```

而执行包内部因为按任务 Endpoint 再次裁剪，最终只有：

```text
implementation_contract.api_contract.endpoints = [orders.list]
```

这就是最直接的验收/执行不对齐。

另外，当前 Unit 的 `target` 也继承页面 target，而不是当前 Endpoint：

```text
source_refs.target = {type: page, id: dashboard}
```

而不是：

```text
{type: endpoint, id: orders.list, api_contract_id: orders-api}
```

这会进一步导致 `prompt_context._task_scope_ids()` 无法稳定取得当前 API Contract。最终 `_scoped_api_contracts()` 可能返回多个契约，但 `task_implementation_contract()` 只取第一个（`Backend/app/agents/data_source/prompt_context.py:128-137`）。如果契约顺序改变，订单任务甚至可能拿到客户 API Contract。

## 3. P0：实体范围只保护了 Agent，没有保护业务验收

Unit 编译器会按照固定任务 ID 过滤 `entity_designs`：

```text
backend:endpoint:...::<entityId>::<stage>
```

见 `Backend/app/services/build_unit_compiler.py:79`。

但它同时删除了模型和任务中的 `source_refs.entity_ids`：

- `Backend/app/services/build_unit_compiler.py:70`
- `Backend/app/services/build_unit_compiler.py:74`

业务验收侧如果发现 `entity_ids` 为空，会回退到整个 BuildContext 的实体集合：

- `Backend/app/services/business_acceptance.py:539`
- `Backend/app/services/business_acceptance.py:543`

结果是：

```text
DataSource Agent 看到：Order
BusinessAcceptance 看到：Order + Customer
```

只读复现结果：

```text
compiled source entity_designs = [Order]
compiled source entity_ids = None
business acceptance expected.entities = [Order, Customer]
```

因此实体隔离目前只对执行 Agent 生效，对业务规则生成不生效。

现有测试甚至明确断言 `entity_ids` 不存在：

- `Backend/tests/test_data_source_generation_prompt.py:376`

这使得该问题容易长期存在。

## 4. P0：TechnicalPlan 生产者和业务验收/执行消费者已经漂移

当前文档契约明确说不再生成、不再读取 EndpointDetail，操作、基数、选择器、事务、状态码等应归入 TechnicalPlan Endpoint：

- `docs/PRODUCT_UI_TECHNICAL_PLANNING.md:303-305`

但实际 TechnicalPlan 生产代码只保留这些 Endpoint 字段：

```text
id
method
path
summary
parameters
request_schema_ref
response_schema_ref
error_codes
authentication
```

见 `Backend/app/services/project_plan.py:1366`。`create_technical_plan()` 也没有生成 `endpoint_detail_plans`（`Backend/app/services/project_plan.py:1774`）。

但业务验收仍然读取旧字段：

- `Backend/app/services/business_acceptance.py:533`
- `Backend/app/services/business_acceptance.py:605`
- `Backend/app/services/business_acceptance.py:762`

后端执行包也仍然读取旧的 `endpoint_detail_plans`：

- `Backend/app/agents/data_source/prompt_context.py:138`
- `Backend/app/agents/data_source/prompt_context.py:250`

只读复现：

```text
TechnicalPlan Endpoint 没有 operation_semantics
TechnicalPlan 没有 endpoint_detail_plans

backend.repository:
  checks = 1
  expected.operations = []

backend.application_service:
  checks = 0

backend.endpoint_controller:
  expected.operations = []
```

这会产生两类问题：

1. 规则缺失，后端代码错误却没有对应检查；
2. 如果历史 EndpointDetail 仍存在，验收规则会引用 `endpoint_detail`，但当前正式产物中无法定位来源，Verifier 会直接 blocked。

正式来源缺失的处理位于 `Backend/app/services/business_acceptance_verifier.py:250` 和 `:317`。

## 5. P1：操作语义没有按当前任务 Endpoint 过滤

`_operation_expectations()` 遍历全部 `endpoint_details`，没有根据当前任务的 `endpoint_ids` 过滤：

- `Backend/app/services/business_acceptance.py:605`

`_endpoint_detail_sources()` 也同样遍历全部详情：

- `Backend/app/services/business_acceptance.py:762`

因此即使 Endpoint 范围被修正，Repository、Service、Controller 任务仍可能继承同一计划内其他接口的操作语义。

复现中当前任务是 `orders.list`，但验收 operations 包含：

```text
orders.list:list
customers.list:query
```

## 6. P1：模型可以覆盖平台范围，候选任务校验没有拦截

Unit 编译使用：

```python
source_refs = {
    **canonical_source_refs,
    **provided_source_refs,
}
```

见 `Backend/app/services/build_unit_compiler.py:72`。

因此模型返回的 `source_refs` 可以覆盖：

- `endpoint_ids`
- `target`
- `technical_plan_endpoint`
- 其他来源字段

但 `build_task_candidate_contract_errors()` 只校验交付物结构、路径和 `target_id`，没有校验这些字段必须等于 Unit 正式范围：

- `Backend/app/services/build_task_planner.py:655`

范围不是平台不可变约束，而是模型可影响的输入。

## 7. P1：Application Build 的空范围语义相互矛盾

Application Build 显式设置：

```text
endpoint_ids = []
entity_ids = []
source_refs = {}
```

见 `Backend/app/graph/nodes/tasks.py:1262`。

但业务验收把空列表解释成“全部”：

- `_endpoint_expectations()` 在空 `endpoint_ids` 时不筛选；
- `_formal_inputs()` 在空实体范围时回退全量实体。

而执行包把空列表解释成“没有”：

- `_task_scope_ids()` 得到空集合；
- `implementation_contract` 可能为空。

复现结果：

```text
业务验收 endpoints = [orders.list, customers.list]
执行包 api_contract = {}
执行包 entities = []
```

同一个 Application Task 在规则生成和执行阶段的语义相反。

## 8. P1：Repair 继承了验收规则，但没有继承执行上下文

Repair 的业务验收继承是正确的：

- `Backend/app/services/business_acceptance.py:76`

但 Repair Task 把父任务来源嵌套到了：

```text
source_refs.parent
source_refs.repair
```

见 `Backend/app/services/build_repair_planner.py:552`。

而 DataSource 执行包只读取顶层：

```text
source_refs.target
source_refs.endpoint_ids
source_refs.entity_designs
```

见 `Backend/app/agents/data_source/prompt_context.py:160`。

只读复现：

```text
Repair business_acceptance_checks = 1
Repair instruction_paths = []
Repair api_contract = {}
Repair endpoint_detail = {}
Repair entities = []
```

因此 Repair Agent 可能收到“必须满足原业务规则”的任务，却没有原任务的后端 Skill、Endpoint Contract 和实体绑定。

## 9. P2：空业务检查会静默通过

普通代码任务没有交付物时，编译器直接生成空检查：

- `Backend/app/services/business_acceptance.py:270`

Verifier 对空检查的最终状态是 `passed`：

- `Backend/app/services/business_acceptance_verifier.py:67`
- `Backend/app/services/business_acceptance_verifier.py:89`

复现：

```text
business_acceptance_checks = []
status = passed
summary = 0/0
```

`backend:bootstrap` 空检查可以是合理的，但普通 Repository、Service、Controller 不应因为规则生成失败而自动通过。

## 10. P2：Java verifier 的操作白名单落后于当前语义

Java verifier 只识别：

```text
list / query / get / create / update / delete
```

见 `Backend/app/services/business_acceptance_verifiers/java_inspection_support.py:105`。

当前业务语义可能出现 `read / action`。这些操作会退化成按方法名前缀匹配，无法正确映射到 `find/get/save/...`，可能造成误判失败。

## 11. 为什么后端比前端更容易自检失败

后端链路同时具备以下特点：

- 一个 Endpoint Unit 通常只实现一个接口；
- 一个任务还会进一步按实体拆成 repository/service/controller 等阶段；
- Java verifier 会逐项检查操作方法、selector 字段、Repository 委托、事务注解、Controller 映射；
- Agent 的实现上下文又是严格收窄的；
- 但业务验收仍可能携带整页 Endpoint、整页实体和其他接口操作语义。

因此后端最容易出现：

```text
Agent 实际只实现 Order
Verifier 却要求 Order + Customer
```

前端页面任务按页面消费多个 Endpoint 本身较合理，所以同样的页面级范围复制在前端不一定立即暴露；后端 Endpoint Unit 则天然需要更细粒度的边界。

## 12. 当前实现中相对正确的部分

- BusinessAcceptance 是确定性编译器，不读取源码、不调用模型；
- Build 会清理 Agent 自报的验收证据；
- 检查包含正式来源、稳定哈希、目标路径和固定 verifier；
- Verifier 会检查来源哈希；
- 任务 owner、Unit、路径和前端 Endpoint 唯一归属有确定性校验；
- Repair 继承父业务检查，避免降低预期。

问题主要在于这些校验没有共享同一个不可覆盖的任务范围。

## 13. 建议修复优先级（本次不实施）

1. 建立平台生成且不可被模型覆盖的 canonical scope：

   ```text
   unit_id
   api_contract_id
   endpoint_id
   entity_ids
   target
   ```

2. `BusinessAcceptance`、DataSource `implementation_contract`、Verifier、Repair 全部只消费这份 scope。

3. 页面 Build 中，按每个后端 Unit 自身的 `api_contract_id + endpoint_id` 收窄 `source_refs`，不要继续使用页面全集。

4. 明确区分“空范围”和“全量范围”，不要再用空列表同时表达两种含义。

5. 按当前 TechnicalPlan Endpoint 重建操作语义来源，并同步 TechnicalPlan producer、BuildContext、business acceptance、execution packet 和 Java verifier。

6. Repair 继承父任务完整执行 scope，而不是只把来源嵌套在 `source_refs.parent`。

7. 普通后端任务若规则无法生成，应阻断或标记配置错误，不能生成空检查后通过。

8. 补充真实生产路径回归测试：

   ```text
   双 Endpoint + 双实体页面
   → resolve_target_build_context
   → apply_unit_compilation
   → compile_business_acceptance
   → execution_task_packet
   → verify_business_acceptance
   ```

## 验证情况

本次分析没有修改业务代码、测试或配置。

已执行：

```text
Backend/.venv/bin/python -m pytest -q \
  tests/test_business_acceptance.py \
  tests/test_business_acceptance_verifier.py \
  tests/test_data_source_generation_prompt.py
```

结果：

```text
36 passed, 5 subtests passed
```

以及：

```text
Backend/.venv/bin/python -m pytest -q \
  tests/test_build_task_planner.py \
  tests/test_build_scheduler.py
```

结果：

```text
68 passed
```

另外执行了双 Endpoint、双实体、TechnicalPlan、Repair 的只读函数级复现。未执行真实后端代码生成、完整 DAG/Build、Electron 或前端构建验证。
