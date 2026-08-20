# integration_test 单元测试生成与执行方案

## 1. 文档状态

- 方案状态：已实现（零测试文件放行版本）。
- 适用范围：XCodeAgent Backend 的 Testing Subgraph，重点为 `integration_test` 节点。
- 目标项目示例：`~/Documents/xc10/frontend` 与 `~/Documents/xc10/backend`。
- 核心目标：根据本次代码修改、当前源码、既有测试和 `.xcodeagent` 项目产物，生成或同步前后端单元测试，并在 `actual_project_checks` 阶段执行真实测试；失败时进入 SmallTaskAgent 修复闭环。

## 2. 目标与边界

当前 `integration_test` 主要检查前后端能否安装、构建和启动，没有覆盖用户项目的单元测试，也没有在代码生成后生成测试文件。

本方案增加以下能力：

1. 在执行项目检查前，识别本次功能修改涉及的前后端源码。
2. 以本次真实代码 diff 为首要参考，结合确认后的项目文档、当前源码和既有测试生成测试任务。
3. 测试文件不存在时创建，已存在时原地更新或追加；源码逻辑变化时同步修改对应测试。
4. 对生成结果做确定性校验，禁止 Agent 越权修改生产代码或测试配置。
5. 在 `actual_project_checks` 中独立执行前端 Jest 和后端 Maven 单元测试。
6. 安装、类型检查、构建或单元测试失败时，统一派发给 SmallTaskAgent 修复并重新验证；本轮没有对应测试文件时，单元测试检查以 `passed=true, skipped=true` 放行。

本方案不新增新的公开 API、AG-UI 传输协议或主工作流节点，只增强 Testing Subgraph 内部流程。

## 3. Testing Subgraph 流程

建议将 Testing Subgraph 调整为：

```text
START
  -> prepare_test_generation
  -> generate_or_update_tests（按任务条件执行）
  -> validate_generated_tests
  -> actual_project_checks
  -> main_quality_gate
  -> repair_planning
  -> END
```

外层路由保持现有语义：

- 全部通过：进入 `launch_project`。
- 可修复失败：进入 `small_task_repair`，完成后重新进入 `integration_test`。
- 需要用户确认：进入 `await_user_input`。
- 不可恢复失败：进入现有失败处理流程。

`actual_project_checks` 必须位于测试生成和确定性校验之后，因此执行命令时测试文件已经落盘且符合路径、命名和基本结构要求。

## 4. 测试生成的输入产物

### 4.1 第一优先级：本次真实代码 diff

测试生成不能只依赖 `changed_files`。文件路径只能说明“改了哪里”，无法说明“行为如何变化”。测试生成 Agent 必须读取逐文件 diff，包括新增、删除和上下文代码。

在 `integration_test` 清理或重置 `code_changes`、`code_change_sets` 之前，构造 `test_change_context`。建议汇总以下来源：

- `state.code_changes`
- `state.code_change_sets`
- `build_results[*].changed_files`
- SmallTaskAgent 产生的 `small_task_code_change_sets`
- 直接修改产生的 `direct_code_change_sets`
- 各执行阶段返回的 `changedFiles`

其中 `code_change_sets[*].files[*].diff` 是最重要的行为变化证据；仅有文件路径时，应重新读取当前源码并通过可用的变更记录补全上下文。

建议 Graph State 中的结构为：

```json
{
  "scope": "current_change",
  "changes": [
    {
      "path": "backend/src/main/java/.../OrderService.java",
      "layer": "backend",
      "changeType": "modified",
      "diff": "...",
      "truncated": false,
      "additions": 12,
      "deletions": 3,
      "sourceTool": "apply_patch",
      "taskIds": ["task-1"]
    }
  ],
  "relatedTestFiles": [],
  "contextRefs": []
}
```

diff 过长可以截断并标记 `truncated: true`，但 Agent 此时必须读取完整当前源码，不能只基于截断内容生成测试。

### 4.2 参考资料优先级

测试生成 Agent 按以下顺序获取上下文：

1. 本次真实代码 diff。
2. 变更文件的当前完整源码。
3. 与源码关联的现有测试文件。
4. 当前功能对应的 PageDetail 或 EndpointDetail。
5. 相关 API Contract。
6. 当前 BuildTask 及其验收条件。
7. 可重建的源码与测试映射。
8. 项目测试配置，例如 `package.json`、Jest 配置、`pom.xml` 和 Surefire 配置。
9. 测试目标直接依赖的少量源码。

RequirementSpec 和完整 ProjectPlan 只在现有局部资料不足时按需读取，不应把整个项目文档、全部源码或完整会话一次性塞入上下文。

各类产物的职责不同：

- diff 说明本次具体改动。
- 已确认的项目文档说明预期业务行为。
- 当前源码说明最终实际实现。
- 既有测试与映射说明应更新哪个测试文件。
- 测试配置说明可运行的测试框架、命令和代码风格。

如果 diff 与确认后的需求或契约冲突，不能通过编造测试来固化错误行为，应将冲突交给质量门禁和修复流程处理。

## 5. TestGenerationAgent 设计

建议新增：

```text
Backend/app/agents/test_generation/
├── __init__.py
├── agent.py
└── generator.py
```

可配套新增测试范围、映射和确定性校验服务：

```text
Backend/app/services/test_generation_scope.py
Backend/app/services/test_generation_validation.py
Backend/app/services/test_mapping.py
```

### 5.1 Agent 职责

TestGenerationAgent 仅负责：

- 分析本次变更是否需要新增或修改单元测试。
- 定位与变更源码关联的既有测试。
- 创建测试文件或原地更新既有测试。
- 返回结构化的生成结果、覆盖的主要行为和源码映射。

Agent 不负责执行安装、构建或测试命令，也不允许修改：

- 前后端生产源码。
- `.xcodeagent` 下的正式项目文档和契约。
- `package.json`、锁文件、Jest 配置或 `pom.xml`。
- 与本次功能无关的测试文件。

工具层必须限制其写入范围，而不能只依赖 prompt：

```text
frontend/tests/*.test.ts
frontend/tests/*.test.tsx
backend/src/test/java/**/*.java
```

TestGenerationAgent 不提供命令执行工具。

### 5.2 Prompt 必须包含的规则

生成测试的所有语义规则必须进入 Agent prompt，建议固定包含以下章节：

1. 角色与任务目标。
2. 输入来源及可信度排序。
3. 本次允许处理的源码与功能范围。
4. diff 优先的读取步骤。
5. 既有测试的查找、更新和去重策略。
6. 前端测试规则。
7. 后端测试规则。
8. 禁止修改项和越权边界。
9. 测试执行由后续确定性阶段负责的说明。
10. 结构化 JSON 输出格式。

关键行为规则包括：

- 先读 diff，再读当前完整源码，然后读取关联的现有测试。
- diff 是变化证据，不是最终产品真相；需求、契约和当前实现需交叉验证。
- 纯样式、注释、导入整理或不改变行为的重构通常不生成新测试。
- 已有测试文件时必须原地更新，不能创建 `Test2`、`test-new`、`test-fixed` 等重复文件。
- 保留既有的无关有效测试，合并 import，避免重复用例。
- 不得为了让测试通过而弱化断言、添加 skip 或修改生产代码。

安全限制、路径限制和结构校验同时由确定性代码实现，形成“prompt 负责语义、工具层负责权限、校验器负责兜底”的三层约束。

## 6. 测试文件映射与持久化

不新增以下运行时计划和清单文件：

```text
.xcodeagent/plans/test-generation-plan.json
.xcodeagent/reports/test-generation-manifest.json
```

原因是测试生成任务属于单次执行状态，直接保存在 Graph State 即可；新增计划和清单会与现有 BuildTask、代码变更记录和测试报告形成重复事实源。

仅新增可重建缓存：

```text
.xcodeagent/cache/unit-test-mappings.json
```

它只保存源码与测试的稳定映射，不保存源码或测试正文。建议字段：

```json
{
  "entries": [
    {
      "layer": "frontend",
      "moduleType": "page",
      "featureName": "order-detail",
      "sourceFiles": ["frontend/src/pages/OrderDetail.tsx"],
      "sourceHashes": {},
      "testFile": "frontend/tests/page-order-detail.test.tsx",
      "testHash": "...",
      "behaviors": ["loads order detail"],
      "status": "active",
      "lastVerifiedAt": "..."
    }
  ]
}
```

查找既有测试的优先级为：

1. `unit-test-mappings.json`。
2. 代码图中的 `related_tests`。
3. 扫描测试文件 import 与被测符号。
4. 根据约定的路径和文件名推导。

缓存写入应采用原子更新；文件缺失、损坏或过期时可通过扫描源码和测试重新构建。正式测试结果继续写入现有 `test-report.json`，并增加测试生成摘要、映射路径和各检查结果，无需再增加 manifest。

## 7. 前端单元测试规范

### 7.1 路径和命名

所有生成的前端测试平铺在：

```text
frontend/tests/
```

命名格式：

```text
<模块类型>-<功能名称>.test.ts(x)
```

模块类型建议限定为：

- `page`
- `component`
- `api`
- `hook`
- `service`
- `util`

功能名称使用稳定功能标识转换后的 kebab-case。页面和组件使用 `.test.tsx`，API、service 和 util 通常使用 `.test.ts`。

示例：

```text
frontend/tests/page-order-detail.test.tsx
frontend/tests/api-order-query.test.ts
```

### 7.2 内容范围

- 使用项目现有 Jest 和 React Testing Library 能力。
- 优先使用已存在的 `render`、`screen`、`fireEvent` 和 `waitFor`，不为测试生成随意增加依赖。
- 每个功能通常只覆盖 1～3 个主要行为：主成功路径、一个重要交互或一个关键异常/空状态。
- API 调用和复杂外部依赖使用 mock。
- 不测试 CSS、颜色、间距、className、Ant Design 内部实现或视觉快照。
- 禁止 `toHaveStyle`、`getComputedStyle` 以及仅验证样式的 snapshot。
- 禁止 `it.skip`、`test.skip`、`describe.skip` 和无意义占位断言。

如果源码逻辑变化影响已有用例，必须修改对应测试文件中的 mock、输入或断言；不能仅因为测试文件已经存在就跳过同步。

## 8. 后端单元测试规范

测试文件必须放在：

```text
backend/src/test/java/
```

目录应镜像生产代码 package，测试类命名为：

```text
<SourceClass>Test.java
```

测试优先级：

1. Service 业务逻辑。
2. 有实际分支逻辑的 validator、mapper 或 controller。
3. 不为 DTO、配置类、getter/setter 等无业务逻辑代码生成低价值测试。

默认采用 JUnit 5 和 Mockito：

```java
@ExtendWith(MockitoExtension.class)
class OrderServiceTest {
    @Mock
    private OrderRepository orderRepository;

    @InjectMocks
    private OrderService orderService;
}
```

一般不使用 `@SpringBootTest`，避免为纯 Service 逻辑启动完整 Spring 容器。每个功能重点测试主成功路径和一个重要分支或异常路径。已有测试类时原地追加或修改，不创建重复测试类。

## 9. 生成结果的确定性校验

`validate_generated_tests` 不调用模型，应至少检查：

- Agent 仅修改了授权测试目录。
- 前端测试是否平铺、命名是否符合规范。
- 后端测试目录是否与 package 对应。
- 测试文件是否包含至少一个有效测试用例。
- import、被测符号和源码映射是否有效。
- 是否出现样式断言、skip、空测试或重复文件。
- 是否意外修改生产代码、构建配置或 `.xcodeagent` 正式产物。

建议为校验结果增加检查项：

```text
frontend_test_generation
backend_test_generation
```

任何校验失败都进入与安装、build 相同的质量门禁和修复流程。

## 10. actual_project_checks 命令设计

`actual_project_checks` 会执行真实单元测试命令。测试生成 Agent 本身不运行命令。

### 10.1 前端

建议执行顺序：

1. `frontend_install`
2. `frontend_build`
3. `frontend_unit_tests`

脚本选择规则：

- 单元测试优先使用 `test:unit`，否则使用 `test`。
- 不自动选择 e2e、watch 或交互模式脚本。
- 对当前 `xc2/frontend`，执行 `pnpm run test`。

### 10.2 后端 Maven 两阶段命令

原有单条 `mvn clean install` 改成两条独立命令：

```bash
mvn -Dmaven.test.skip=true clean install
mvn -DskipTests=false -Dmaven.test.skip=false test
```

第一条对应 `backend_build`：

- 执行 clean。
- 解析并安装生产依赖。
- 编译生产代码。
- 生成 `target`、JAR 或其他打包产物。
- 执行 package、verify 和 install 生命周期。
- 不编译也不执行测试。

第二条对应 `backend_unit_tests`：

- 显式恢复测试编译和执行。
- 解析测试依赖。
- 执行 `testCompile`。
- 使用 Surefire 运行 JUnit/Mockito 测试。
- 生成 `target/test-classes` 和 Surefire 报告。

使用 `maven.test.skip=true` 而不是只使用 `skipTests`，可以确保第一阶段完全不处理测试源码；测试编译错误和测试失败统一在第二阶段暴露。

如果 `backend_build` 失败，不再执行 `mvn test`，而是生成一个带原因的 blocked/skipped `backend_unit_tests` 结果，避免同一编译问题产生重复修复任务。

注意：第一条命令会先把尚未经过单元测试验证的构件安装到本地 Maven 仓库。这是“先完成依赖安装和 target 生成，再专门运行测试”顺序带来的明确取舍。

### 10.3 静态跳过规则

- 本次没有后端源码变化时，可以按现有静态规则跳过后端检查。
- 只要存在真实后端源码变化，即使数据源静态，也必须运行 `backend_build` 和 `backend_unit_tests`。
- 前端规则同理，不能因为测试文件是 Agent 新生成的就跳过 Jest。

### 10.4 日志位置

建议保存到：

```text
.xcodeagent/runtime/tests/frontend_unit_tests/
.xcodeagent/runtime/tests/backend_build/
.xcodeagent/runtime/tests/backend_unit_tests/
```

Maven Surefire 原始报告仍位于用户后端项目的：

```text
backend/target/surefire-reports/
```

## 11. 失败派发与 SmallTaskAgent 修复闭环

安装、build、测试生成校验、Jest 或 Maven 测试失败，都由 `main_quality_gate` 转换为统一 revision request。建议补充：

- `affected_test_files`
- `related_source_files`
- `repair_candidate_paths`
- 失败命令、退出码和精简错误摘要
- 完整日志引用

修复授权路径由以下集合合并得到：

1. 当前 BuildTask 已授权的生产源码路径。
2. 失败的测试文件。
3. 测试映射中与失败测试关联的源码文件。

不得授权 SmallTaskAgent 修改 `.xcodeagent` 正式产物。后端检查的归属统一规范为 `backend`，不要沿用可能误导修复路由的 `data_source`。

SmallTaskAgent 的判断原则：

- 实现违反已确认需求或契约：修复生产源码。
- 实现正确但测试预期、mock 或断言过期：修复测试。
- 禁止通过删除测试、弱化断言或添加 skip 使检查变绿。
- 若修复必须扩大到未授权业务范围，应请求用户确认。

修复后重新进入 `integration_test`，再次构造真实 diff、同步受影响测试，并运行全部相关检查。沿用现有最多 3 轮真实修复的限制。

## 12. Graph State 变更

建议新增或扩展以下状态字段：

```text
test_change_context
test_generation_tasks
test_generation_results
test_generation_summary
test_mapping_path
test_generation_code_change_sets
```

TestGenerationAgent 产生的测试文件 diff 也应合并回 `code_changes` 和 `code_change_sets`，保证后续审计、修复和报告能看到测试文件变更。

单次生成计划保存在 Graph State，不额外写入 plan 文件；状态压缩时保留测试范围、映射、已修改文件、检查结果、未解决失败和下一步动作。

## 13. AG-UI 与前端展示

本功能不新增公开端点。继续通过现有 Testing Subgraph 和 AG-UI 生命周期发送：

- 测试生成进度。
- `integration_test.checks` 中的新检查项。
- 结构化成功或错误结果。
- 完整 state snapshot/delta 与 run finish。

前端复用通用检查展示能力识别新增 check ID，无需增加手写 SSE、普通 REST 或自定义事件解析。

## 14. 预计实现位置

主要新增文件：

```text
Backend/app/agents/test_generation/__init__.py
Backend/app/agents/test_generation/agent.py
Backend/app/agents/test_generation/generator.py
Backend/app/services/test_generation_scope.py
Backend/app/services/test_generation_validation.py
Backend/app/services/test_mapping.py
```

主要修改位置预计包括：

```text
Backend/app/graph/subgraphs/testing.py
Backend/app/services/integration_test_runner.py
Backend/app/services/test_validation.py
Backend/app/nodes/small_task.py
Backend/app/state.py
Backend/app/agents/registry.py
Backend/app/services/workspace_scope.py
Backend/app/services/test_documents.py
docs/WORKFLOW.md
docs/NODE_INTEGRATION_TEST.md
docs/CODEBASE_INDEX.md
```

实际实现前应再次依据当前代码确认准确路径和现有抽象，采用最小修改，不引入新的前端或 Maven 测试依赖。

## 15. 分阶段落地

### 阶段一：测试执行能力

- 将后端 `mvn clean install` 拆成两条命令。
- 在 `actual_project_checks` 中增加 `frontend_unit_tests` 和 `backend_unit_tests`。
- 将测试失败接入现有质量门禁和 SmallTaskAgent 修复路由。

### 阶段二：测试生成能力

- 构建 `test_change_context`。
- 增加 TestGenerationAgent 和受限写入工具。
- 实现前后端生成规则及确定性校验。

### 阶段三：测试同步与映射

- 增加可重建的 `test-mappings.json`。
- 完成源码变化时的既有测试同步。
- 完善修复后的重新生成、映射更新和报告。

## 16. 验收标准

至少覆盖以下场景：

1. 新增前端功能时，在 `frontend/tests` 生成符合命名规则的平铺测试。
2. 修改前端已有功能时，更新原测试而非创建重复文件。
3. 纯 CSS 修改不生成或扩展单元测试。
4. 新增或修改后端 Service 时，在正确 package 下生成或更新 Mockito/JUnit 5 测试。
5. Agent 尝试写入生产源码或配置文件时被工具层拒绝。
6. Jest 失败能生成包含测试文件和关联源码的 revision request。
7. Maven 测试失败能派发给 SmallTaskAgent，而 `backend_build` 失败时不会重复执行测试。
8. SmallTaskAgent 修改源码后，对应既有测试会在下一轮同步。
9. 测试映射文件缺失或损坏时可重建。
10. 修复循环仍受最多 3 轮限制。
11. 新检查项通过现有 AG-UI 流程展示，不新增旁路协议。

## 17. 实现后的验证命令

代码实现完成后，应按实际修改范围执行：

```bash
python3 -m py_compile <changed-backend-python-files>
python3 -m unittest <focused-testing-and-repair-tests>
curl -sS http://127.0.0.1:8000/health
```

并在 `~/Documents/xc2` 的真实前后端项目中验证：

```bash
cd ~/Documents/xc2/frontend
pnpm run test

cd ~/Documents/xc2/backend
mvn -Dmaven.test.skip=true clean install
mvn -DskipTests=false -Dmaven.test.skip=false test
```

前端相关实现还应按仓库规范执行 `pnpm build`；所有失败都必须调查并修复，无法解决时需报告准确命令、日志位置和剩余原因。
