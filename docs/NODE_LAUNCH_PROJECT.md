# `launch_project` 节点详细分析

## 1. 节点概览

- 节点名称：启动工程节点
- 主图 `node_id`：`launch_project`
- 节点类型：确定性langGraph节点，没有llm调用以及提示词
- 节点有必要存在，不能和集成测试节点并行


节点在主图中的位置总体合理：

```text
integration_test -> launch_project -> END
                                      |
                                      | 用户提交结构化验收动作后的下一次 run
                                      v
                                 acceptance -> finalize_project
```



### 1.1 节点承担的独立职责

它负责完成以下职责：

1. 解析本次运行的工作区。
2. 读取应用权威数据源类型。
3. 按数据源策略决定是否启动 Java 后端。
4. 清理上一轮预览进程。
5. 构建并启动 Maven 后端，验证进程和启动日志。
6. 安装前端依赖并启动开发服务器。
7. 通过 HTTP 或本次启动日志验证前端就绪。
8. 保存 PID、日志路径和结构化启动证据。
9. 生成最终验收请求并停止当前 Graph run。

这些职责和 `integration_test` 的“验证代码质量”、`acceptance` 的“消费用户决策”均不同，因此需要一个独立的运行边界。

### 1.2 是否可以删除该节点

不建议删除。

如果删除并把启动逻辑放入 `integration_test`：

- 测试节点会同时承担验证和长生命周期进程管理，职责混合。
- 启动副作用会污染测试重试；每次复测都可能重启预览进程。
- 测试失败证据和启动失败证据难以区分。
- 用户验收前的持久化边界不再清晰。

如果删除并把启动逻辑放入 `acceptance`：

- 用户尚未看到预览就进入验收节点，时序错误。
- 启动耗时和错误会发生在用户提交验收动作之后。
- 无法先生成预览地址再暂停等待用户。

因此，保留独立节点是合理的。

### 1.3 是否应该拆成多个 Graph 节点

当前不必立即拆分为 `launch_backend` 和 `launch_frontend` 两个主图节点，原因是：

- database 工程要求后端就绪后才启动前端，实际是顺序关系。
- 前端失败时需要补偿停止本次 Java 进程，放在一个统一服务内更容易保持事务式语义。
- Static 工程只执行前端分支，拆成两个节点会增加条件边和 checkpoint 状态。

只有在以下需求出现时，才值得拆分：

- 前端需要分别展示后端构建、后端启动、前端安装、前端启动的实时进度。
- 希望单独重试某个启动阶段。
- 支持多个后端服务或多个前端应用。
- 启动阶段耗时显著，需要更细粒度的 checkpoint 和取消能力。

更低成本的改进是保留一个 Graph 节点，但让 launcher 发出结构化阶段进度事件。

### 1.4 是否可以与其他节点并行

#### 与 `integration_test` 并行：不应该

`launch_project` 必须消费测试通过后的稳定工作区。并行执行会产生以下风险：

- Maven、前端安装或测试可能同时修改 `target`、依赖目录或运行日志。
- 测试尚未完成时就可能向用户暴露失败或未验证的预览。
- 集成测试可能需要占用与预览相同的本地端口。
- 启动失败无法判断是工程问题还是测试过程中的临时文件变化。

因此 `integration_test -> launch_project` 的顺序边是必要的。

#### 与 `acceptance` 并行：不可能

`acceptance` 的输入是用户看完 `preview_url` 后提交的结构化决策，天然依赖启动成功，不能并行。

#### 后端和前端启动并行：默认不应该

当前顺序是后端完全就绪后再启动前端，见 [`Backend/app/services/project_launcher.py`](Backend/app/services/project_launcher.py#L45)。这样可以：

- 避免后端失败后仍留下无意义的前端进程。
- 确保用户首次打开页面时后端已经可用。
- 简化前端失败后的 Java 补偿清理。

理论上可以并行执行“前端依赖预检”和“后端 Maven 预检”，但不建议并行实际进程启动。可并行部分的收益有限，不值得破坏当前清晰的失败顺序。

## 2. 分析节点设计

### 2.1 节点在 Graph 中的位置

主图注册和边定义见 [`Backend/app/graph/workflow.py`](Backend/app/graph/workflow.py#L171)。正常路径为：

```text
build
  -> integration_test
      -> quality gate passed
          -> launch_project
              -> END
```

这个位置总体合理：

- 位于质量门禁之后，不会默认启动已知失败的工程。
- 位于人工验收之前，能够先生成预览地址。
- 节点结束当前 run，使用户验收成为显式的新一轮交互。

但前置条件只由路由保证。请求协议允许 `resume_from="launch_project"`，见 [`Backend/app/protocols/workflow/request.py`](Backend/app/protocols/workflow/request.py#L648)，主图 START 也允许直接进入该节点，见 [`Backend/app/graph/workflow.py`](Backend/app/graph/workflow.py#L22)。这会绕过 `quality_gate_passed`。

建议二选一：

1. 推荐：公开协议不允许普通业务请求直接恢复到 `launch_project`，只保留受控调试入口。
2. 或者：节点入口显式校验 `quality_gate_passed is True`，调试模式使用单独的受控标记。

### 2.2 提示词设计

该节点不是 Agent，不调用 ChatModel、Deep Agent、Agent Registry 或模型工厂，所以没有 System Prompt/User Prompt，也不存在提示词 token 成本或模型不确定性。 

### 2.3 输入字段设计

节点通过 `workspace_root(state)` 间接读取以下 Graph State 字段，定义见 [`Backend/app/workspace/spec_documents.py`](Backend/app/workspace/spec_documents.py#L13)：

| 字段 | 类型 | 读取顺序 | 是否必要 | 评价 |
| --- | --- | --- | --- | --- |
| `workspace` | `str` | 1 | 是 | 应成为启动阶段唯一推荐的工作区来源 |
| `workspace_path` | `str` | 2 | 否 | 与 `workspace` 重复，属于兼容字段 |
| `project_id` | `str` | 3 | 条件必要 | 仅在工作区缺失时推导默认目录 |
| 默认 `demo-project` | 固定值 | 4 | 否 | 调试便利，但生产中有误启动风险 |


这种“Graph State 只提供工作区，launcher 从磁盘读取工程”的设计有利于复用和控制上下文大小。但应把 `workspace` 收紧为必需输入，而不是允许无提示落到 `demo-project`。

### 2.4 输出字段设计

字段定义见 [`Backend/app/graph/state.py`](Backend/app/graph/state.py#L116)，节点成功输出如下：

| 字段 | 当前用途 | 是否必要 | 评价 |
| --- | --- | --- | --- |
| `phase="launch_project"` | 标识当前阶段 | 是 | 合理 |
| `status="requires_user_input"` | 暂停并触发验收生命周期 | 是 | 合理 |
| `preview_url` | Workflow 摘要和前端自动导航 | 当前协议必要 | 与其他位置重复 |
| `launch_result` | 完整结构化启动证据 | 是 | 有助于审计和排错，但公开投影可进一步裁剪宿主路径 |
| `acceptance_request` | 生成人工验收载荷 | 是 | 合理，但其中部分字段冗余 |
| `clarification` | 驱动现有确认 UI | 当前协议必要 | 与 `acceptance_request` 表意重叠 |
| `timeline` | 追加节点历史 | 是 | 合理 |

成功输出的精确结构为：

```yaml
phase: launch_project
status: requires_user_input
preview_url: string | null
launch_result: object
acceptance_request:
  status: requires_user_input
  message: 项目已通过集成测试并启动预览，请用户验收。
  preview_url: string | null
  package_json_path: string | null
  server: object | null
clarification:
  mode: page_acceptance
  status: requires_user_input
  message: 请预览页面并完成最终验收。
  questions: []
timeline:
  - launch_project
```

失败输出为：

```yaml
phase: launch_project
status: failed
preview_url: failure_reason
launch_result:
  status: failed
  message: string
  preview_url: failure_reason
  failed_stage: string
acceptance_request:
  status: failed
  message: 项目启动失败：<failure_reason>
  preview_url: failure_reason
timeline:
  - launch_project
```


### 2.5 是否和其他节点有任务冲突

#### 与 `integration_test` 的部分职责重叠

`launch_project` 会再次执行：

- `mvn clean install`
- 前端包管理器 `install`
- 前端/后端启动就绪检查

其中构建和依赖安装可能已经在 Build/Integration Test 阶段执行过。该重复并非完全不合理：启动前重新构建可以证明当前磁盘状态仍可运行。但当前架构没有显式区分：

- 质量门禁中的构建验证；
- 启动节点为了生成可运行包而执行的打包；
- 依赖已经满足时的无效重复安装。

建议长期将“构建可运行产物”作为 Build/Test 的正式产出，例如提供确认过的 JAR 路径和前端依赖状态；launch 节点只验证产物 revision 与当前 workspace revision 一致后启动。短期保留重新构建更安全。


## 3. 路由分析

### 3.1 进入节点的现有路由

正常边：

```text
integration_test -- quality_gate_passed=true --> launch_project
```

该边必要且方向正确。

路由函数 `route_test_validation()` 优先读取 `quality_gate_passed`；因此质量门禁通过时，`integration_next_action="launch_project"` 并不是实际路由的必要条件。后者主要用于状态说明和兼容投影。

另有 START 恢复边：

```text
START -- resume_from=launch_project --> launch_project
```

该边对普通生产请求不是必要的，且能绕过门禁。建议限制为显式调试能力，或者删除公开恢复集合中的 `launch_project`。

### 3.2 执行成功后的路由

现状：

```text
launch_project(status=requires_user_input) -> END
```

这是必要的边。节点必须先结束当前 run，等待用户在预览面板完成验收，不能直接连接 `acceptance`，否则 `acceptance` 会在没有用户决策时立即执行并再次返回等待状态，产生伪节点事件和不必要的 checkpoint。

用户提交 `clarificationAnswers.page_acceptance` 后，协议层在下一次 run 设置：

```text
resume_from=acceptance
acceptance_decision=accepted | changes_requested
```

随后：

```text
acceptance(accepted=true)  -> finalize_project -> END
acceptance(accepted=false) -> END
```

这些边合理，验收调整应该在后续明确动作中路由到计划调整、细节重设计或局部修复，而不是由 `launch_project` 猜测。

### 3.3 执行失败后的路由

现状：

```text
launch_project(status=failed) -> END
```

它不会进入 `handle_failure`。这保证了节点仍能完成 AG-UI 生命周期并返回结构化失败，但缺少恢复策略。

不建议简单增加一条统一的：

```text
launch_project failed -> launch_project
```

因为配置错误、代码错误和瞬时进程错误的解决方式不同，盲目重试会重复安装、重复 Maven 构建或持续占用资源。

建议增加按 `failed_stage` 和结构化 `error_code` 决定的路由：

| 失败阶段 | 推荐路由 | 理由 |
| --- | --- | --- |
| `datasource_policy` | `END/await_user_input` | 需要修复 application.json，重试无效 |
| `backend_database_config` | `END/await_user_input` | 需要用户更新连接配置或恢复密钥 |
| `backend_validation` | `END/handle_failure` | 缺少 Maven/Java 属于环境问题，Agent 通常不能安全修复 |
| `backend_cleanup` | 有界重试一次，仍失败则 `END` | 可能是短暂退出延迟，也可能需要人工处理进程身份 |
| `backend_build` | `small_task_repair` 或 `integration_test` 修复入口 | 属于新的真实构建失败，应进入证据驱动修复闭环 |
| `backend_jar` | `small_task_repair`/`build` | 多为构建配置或打包产物问题 |
| `backend_start` | 瞬时错误有界重试；确定性错误进入修复/失败 | 端口或启动时序可能可重试，应用异常需要代码修复 |
| `frontend_start` | 按子错误分类 | 安装网络错误可重试；编译错误应进入修复；缺 script 应回到 Build |

在没有实现上述分类前，保持 `END` 比无条件重试安全，但用户体验不完整。

### 3.4 哪些边需要或不需要存在

| 边 | 判断 |
| --- | --- |
| `integration_test -> launch_project` | 必须存在 |
| `launch_project(success) -> END` | 必须存在，用于人工验收门禁 |
| `launch_project(failed) -> END` | 当前安全兜底可保留，但应升级为分类路由 |
| `launch_project(failed) -> handle_failure` | 不应无条件存在，会丢失可恢复场景 |
| `launch_project(failed) -> launch_project` | 不应无条件存在，会造成重复副作用 |
| `START -> launch_project` | 仅调试需要，普通生产请求不需要 |
| Direct Modification `launch_project -> finalize` | 可以存在，但不应复用带验收语义的节点适配器 |

## 4. 节点错误处理

### 4.1 已结构化处理的错误

统一 launcher 位于 [`Backend/app/services/project_launcher.py`](Backend/app/services/project_launcher.py#L23)。现有 `failed_stage` 包括：

| `failed_stage` | 典型原因 | 当前处理 |
| --- | --- | --- |
| `datasource_policy` | application.json 缺失、损坏、类型无效或 external_api 未启用 | 不启动任何进程，直接结构化失败 |
| `backend_validation` | 缺少 pom、Maven 或 Java | 直接失败 |
| `backend_database_config` | plantMode 缺失、DBID/内置模式不支持、密码无法解密 | Maven 前失败 |
| `backend_cleanup` | 旧 Java 进程无法安全识别或停止 | 中止 Maven，避免误杀和并发启动 |
| `backend_build` | Maven 返回非零、超时或 OSError | 保存 stdout/stderr 日志后失败 |
| `backend_jar` | 无唯一主 SNAPSHOT JAR | 直接失败 |
| `backend_start` | Popen 失败、进程退出或就绪超时 | 清理本次进程并失败 |
| `frontend_start` | package、script、包管理器、install、进程或健康检查失败 | 必要时补偿停止 Java |

这些错误大多不会抛出异常，而是返回完整启动证据。这符合 AG-UI“业务失败也完成完整 run 生命周期”的项目约束。


## 5. 依赖分析

### 5.1 上游节点字段依赖

| 生产节点/边界 | 字段 | `launch_project` 是否直接读取 | 是否必要 | 分析 |
| --- | --- | --- | --- | --- |
| Workflow 请求 | `workspace` | 是 | 是 | 唯一推荐的工程定位字段 |
| 兼容状态 | `workspace_path` | 是 | 否 | 与 `workspace` 重复 |
| Workflow 请求 | `project_id` | 条件读取 | 条件必要 | 仅用于工作区回退 |
| `integration_test` | `quality_gate_passed` | 否，路由读取 | 是 | 启动前质量门禁 |
| `integration_test` | `integration_next_action` | 否，路由次要读取 | 否/兼容 | qgp=true 时实际路由不依赖它 |
| `integration_test` | `test_report` | 否 | 验收 UI 条件必要 | 生命周期 `testSummary` 使用 |
| `build` | `build_summary` | 否 | 否 | 启动器重新从磁盘验证工程 |
| `build` | `build_results` | 否 | 否 | 不应把全量构建状态注入启动节点 |
| 规划节点 | `project_plan` | 否 | 否 | 启动只消费最终工程和 application.json |
| 需求节点 | `requirement_spec` | 否 | 否 | 与运行预览无直接关系 |

`quality_gate_passed` 是必要依赖，但只存在于图路由层；`integration_next_action` 在成功路径冗余。

### 5.2 工作区配置依赖

权威文件：

```text
<workspace>/.xcodeagent/application.json
```

字段级依赖：

| 字段 | 使用条件 | 是否必要 | 用途 |
| --- | --- | --- | --- |
| `datasource.type` | 始终 | 是 | 允许 `database`/`static`，拒绝 external_api |
| `datasource.db.useBuiltin` | database + Maven 后端 | 条件必要 | 当前为 true 时拒绝直接连接 |
| `datasource.db.dbidMode` | database + Maven 后端 | 条件必要 | 当前存在时拒绝直接连接 |
| `datasource.db.plantMode` | database + Maven 后端 | 是 | 直接连接配置对象 |
| `plantMode.domain` | 同上 | 是 | MySQL host |
| `plantMode.port` | 同上 | 是 | MySQL port |
| `plantMode.userName` | 同上 | 是 | 用户名 |
| `plantMode.pwd` | 同上 | 是 | 密码，支持加密 envelope |
| `plantMode.schema` | 同上 | 是 | 数据库名 |

Static 分支完全跳过 Java 和数据库密码解析是必要的安全边界，避免前端内存数据模式无意访问数据库凭据。

如果 `pwd` 是加密 envelope，还依赖平台私钥文件，由 [`Backend/app/services/database_crypto.py`](Backend/app/services/database_crypto.py#L44) 解析。该依赖仅在加密密码场景必要。

### 5.3 后端工程依赖

| 文件/环境 | 精确用途 | 是否必要 | 分析 |
| --- | --- | --- | --- |
| `backend/pom.xml` 或 `Backend/pom.xml` | 判断 Maven 后端存在 | 条件必要 | Static 不需要；database 通常应需要 |
| `backend/mvnw`/`mvnw.cmd` | 项目 Maven wrapper | 条件必要 | 没有时回退 PATH `mvn` |
| PATH `mvn` | Maven 构建 | 条件必要 | wrapper 存在时不需要 |
| PATH `java` | `java -jar` | 条件必要 | 后端启动必需 |
| `target/*-SNAPSHOT.jar` | 唯一主程序 | 条件必要 | 由 `mvn clean install` 产生 |
| 后端 stdout/stderr | 就绪 marker | 当前设计必要 | 应逐步由健康端点替代 |

主 JAR 选择会排除 `original-*`、sources、javadoc、tests/test JAR。要求唯一主包是合理的 fail-close，避免启动错误产物。

设计风险：`datasource.type=database` 但找不到 Maven 后端时，当前逻辑将后端标记为 `backend_project_missing/skipped`，仍启动前端并可能进入验收。是否允许这种状态应由应用架构契约决定；如果 database 应用必须经后端访问数据，该依赖应从“可选”提升为“必需”。

### 5.4 前端工程依赖

前端 package 搜索顺序由 [`Backend/app/services/frontend_project_launcher.py`](Backend/app/services/frontend_project_launcher.py#L282) 定义：

```text
frontend/package.json
Frontend/package.json
app/frontend/package.json
package.json
第一级其他目录中的 package.json（需包含 dev/start）
```

字段和文件依赖：

| 字段/文件 | 是否必要 | 用途 |
| --- | --- | --- |
| `package.json` 顶层对象 | 是 | 工程入口 |
| `scripts.dev` | 与 start 二选一 | 第一优先启动脚本 |
| `scripts.start` | 与 dev 二选一 | 第二优先启动脚本 |
| script 中 `--port`/`--port=`/`PORT=` | 否 | 推断预览端口，缺失时默认 localhost:80 |
| script 中 `react-scripts` | 否 | 决定是否移除继承的 HOST |
| `pnpm-lock.yaml` | 否 | 存在时选择 pnpm |
| `yarn.lock` | 否 | pnpm lock 不存在时选择 yarn |
| PATH `pnpm`/`yarn` | 是 | 实际安装和启动命令 |

没有 lockfile 时实现默认使用 `pnpm`，符合仓库包管理器偏好。前端 `install` 每次运行是否必要取决于依赖 revision；可以基于 lockfile hash 缓存，但在没有可靠缓存失效机制前，保留安装更安全。

### 5.5 运行时文件和进程依赖

目录：

```text
<workspace>/.xcodeagent/runtime/launch/
```

主要文件：

| 文件 | 用途 | 是否必要 |
| --- | --- | --- |
| `backend.pid` | Backend 重启后恢复并安全停止旧 Java | 是 |
| `frontend.pid` | 复用或停止前端预览进程 | 是 |
| `backend-build.stdout.log` | Maven 构建证据 | 是 |
| `backend-build.stderr.log` | Maven 错误证据 | 是 |
| `backend.stdout.log` | Java 就绪和运行证据 | 是 |
| `backend.stderr.log` | Java 错误和就绪降级证据 | 是 |
| `install.stdout.log` | 前端依赖安装证据 | 是 |
| `install.stderr.log` | 前端安装错误证据 | 是 |
| `frontend.stdout.log` | 前端真实 URL和就绪证据 | 是 |
| `frontend.stderr.log` | 前端致命编译错误证据 | 是 |

后端还依赖 [`Backend/app/services/backend_process_registry.py`](Backend/app/services/backend_process_registry.py#L23) 中的工作区锁和内存进程表。PID 恢复会检查命令包含 `java -jar` 和当前工作区 `target` 下的 JAR，属于必要的防误杀安全依赖。



## 7. 优化点

1. **集成测试节点和工程启动节点有重复的阶段**

    pnpm install          # 重复

    mvn clean install     # 重复

    需要优化，
    复用 Maven 已生成的 JAR，launch_project 直接执行 java -jar。
复用已经安装好的前端依赖，launch_project 直接执行 pnpm dev。
用 workspace_revision、lockfile hash、JAR SHA-256 等确认测试完成后工程没有变化。


2. **启动失败的错误提示需要优化**
UI显示过于简单，并且要给出用户提示如何进行修复


3. **可以优化为前后端并发启动**
仅最后的启动步骤pnpm run dev和jar -- 命令并发执行