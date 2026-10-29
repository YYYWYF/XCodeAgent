# XCodeAgent 二次修改自动后端骨架注入机制移除方案（Codex 实施版）

> **目标仓库**：`https://github.com/YYYWYF/XCodeAgent.git`  
> **目标分支**：`dev_agent`  
> **目标范围**：只移除二次正式修改中自动生成业务 Java 骨架的历史机制，以及 Build 中配套的“预置骨架”虚假契约。  
> **实施方式**：基于 Codex 执行时最新的 `dev_agent` 工作树先检索引用，再实施最小必要变更；不覆盖本地未提交的改动。  
> **特别说明**：`prebuilt_files` 是根据 TechnicalPlan **推算**的路径，不是注入函数实际产出的文件清单，也不代表文件真实存在。

## 1. 改造结论与边界

### 1.1 需要移除的两套相互配套的历史行为

**A. 自动注入（有实际文件写入）**

```text
二次正式修改 TechnicalPlan 确认
  → application_planning_workflow._reconcile_confirmed_revision()
  → _reconcile_revision_template_capabilities()       # 保留
  → _inject_revision_backend_skeleton()               # 移除
  → inject_deterministic_backend_skeleton()           # 移除
  → issue_revision_continuation()                    # 保留
```

旧注入器遍历 `TechnicalPlan.entities`，为每个有字段的实体确定性生成 Entity、PO、Mapper、Repository、Converter、DTO、Controller 与 Mapper XML；文件已存在但内容不同时可能覆盖，且不清理已删除实体文件。它既不按本次页面/Endpoint 范围筛选，也不严格区分数据库与外部 API 来源。

**B. `prebuilt_files`（无实际写入）**

```text
TechnicalPlan.entities
  → prebuilt_files_for_plan()
  → Build Context / Backend implementation_contract.prebuilt_files
  → Backend Agent 读取“预置文件”信息
```

旧函数只从实体推导文件路径，**不检查 WorkspaceSnapshot 或磁盘**。它可能把从未生成的业务类告知 Agent 为“预置”。删除实际注入器时，应同时删除这一旧字段及其 Skill 假设。

### 1.2 必须保留

- 首次 Workspace Bootstrap、模板下载及 Git baseline。
- Template Engine `Generate` / V4 `Update`，TemplateState、Login、Authorization 的能力收敛；`NO_CHANGE` 与 `CHANGED` 均保持原语义。
- 正式 TechnicalPlan 确认、独立 checkpoint、`template_reconcile` 的 CAS/重试与失败恢复、`issue_revision_continuation()`。
- `Build Unit Skeleton`（`build_unit_skeleton.py`）**不是**本次要删除的业务 Java Skeleton；保留所有 Unit/DAG/Stage 规则。
- `backend:bootstrap`、`backend:endpoint:*`，以及 `objects`、`repository`、`upstream`、`service`、`controller` 各阶段职责、依赖及任务调度。
- Backend Agent 的实际执行函数 `generate_data_sources_with_deep_agent()`、WorkspaceSnapshot、`allowed_paths`、`change_scope`、`implementation_contract`、权限约束投影。
- 内置 Backend Skill 的 Java 8、DDD、模板受管文件保护、权限隔离和 Spring Boot 编码规范。

### 1.3 明确禁止

- **不要**修改 `Hupy2118/springboot-template` 模板项目或 XCodeAgent Template Engine API 协议。
- **不要**新增任何 Build 之前的替代自动骨架生成节点、开关或后台任务。
- **不要**改变 API/Entity/Endpoint 设计的正式 Schema、Build DAG Task ID/Unit ID、依赖图、权限资源约束。
- **不要**修改业务工作区现存的 Java 源码，不自动删除旧业务包目录，不提供“清理历史骨架”的隐式副作用。
- **不要**通过 `"prebuilt_files": []` 保留已废弃字段，也不要新增同义旧字段。
- **不要**把本次修改扩大到数据库迁移、前端路由、权限 Bootstrap、Repair/EDD 或 Backend Agent 重构。

## 2. 影响文件与处理清单

### 2.1 必须修改/删除的 Python 生产文件（5 个）

| 文件 | 操作 | 说明 |
|---|---|---|
| `Backend/app/graph/application_planning_workflow.py` | 修改 | 删除注入调用、完整包装函数、相关 import，修正进度文案 |
| `Backend/app/services/template_scaffold_injection.py` | **删除** | 不再保留业务骨架渲染器、写入器或 `prebuilt_files_for_plan()` |
| `Backend/app/graph/nodes/tasks.py` | 修改 | 删除应用级 Build Context 的 `prebuilt_files` 和 import |
| `Backend/app/services/build_context_resolver.py` | 修改 | 删除页面/Endpoint Build Context 的 `prebuilt_files` 和 import |
| `Backend/app/agents/data_source/prompt_context.py` | 修改 | 删除 Bootstrap / Endpoint 实现契约中的 `prebuilt_files`、相关 import 和误导性注释 |

### 2.2 必须修改的 Skill 文档（6 个，不删除 Skill）

路径前缀：`Backend/app/builtin_skills/springboot-template-modification-boundary/`

1. `SKILL.md`
2. `references/module-layout.md`
3. `references/entity-template.md`
4. `references/repository-template.md`
5. `references/dto-template.md`
6. `references/controller-template.md`

要求仅清理“平台事先生成全部业务骨架”假设，保留规范示例及真实的 Base/Extension 模板保护约束。

### 2.3 测试与文档

- **删除** `Backend/tests/test_template_scaffold_injection.py`（仅测试已废弃的生成器和预置路径推导）。
- **修改** `Backend/tests/test_application_page_planning.py`（取消旧函数 mock，补充无自动写入的断言）。
- **补充/修改** `Backend/tests/test_data_source_generation_prompt.py`（Bootstrap / Endpoint 契约不再包含 `prebuilt_files`）。
- **补充/修改** `Backend/tests/test_page_build_context_resolver.py` 等 Build Context 测试：确认页面/Endpoint Context 中不再含 `prebuilt_files`。
- **更新测试命令引用**：`docs/FRONTEND_SHELL_PREREQUISITE.md`、`Backend/tests/DAG_PLANNING_BASELINE.md` 中旧 `tests.test_template_scaffold_injection` 命令；仅修改失效引用。
- `docs/DAG_TASK_GENERATION_UNIT_CANDIDATE_PLAN.md` 的历史/规划描述引用 `prebuilt_files_for_plan`：若该文档作为当前实施契约使用，应更正为 WorkspaceSnapshot 的真实文件事实；若明确是存档设计稿，则加废弃标记，而非大范围改写。

> 上述清单来自当前可见的 `dev_agent` 代码引用。Codex 需在本地再次全仓 `rg`，发现额外真实引用时先归类再处理，不可仅依赖此静态清单。

## 3. Codex 分步修改说明

### Step 0：建立基线、精确检索

在仓库根目录：

```bash
git status --short
rg -n '(_inject_revision_backend_skeleton|inject_deterministic_backend_skeleton|prebuilt_files_for_plan|prebuilt_files|template_scaffold_injection)' \
  Backend docs
rg -n '(预置代码|平台已根据 TechnicalPlan|进入开发阶段前已|平台预置的 Controller)' \
  Backend/app/builtin_skills/springboot-template-modification-boundary
```

记录每一处生产引用、测试引用、历史文档引用；保护本地用户改动。不要把“Build Unit Skeleton”与“业务 Java Skeleton”混为一谈。

### Step 1：删除二次修改的自动写入入口

修改 `Backend/app/graph/application_planning_workflow.py` 中 `_reconcile_confirmed_revision()`：

**修改前核心片段：**

```python
_reconcile_revision_template_capabilities(workspace, active_revision.change_id)
_template_reconcile_progress(
    workspace,
    "模板能力已更新，正在生成后端骨架并校验工作区。",
)
_inject_revision_backend_skeleton(workspace, state, strict=True)
token, issued = issue_revision_continuation(...)
```

**修改后核心片段：**

```python
_reconcile_revision_template_capabilities(workspace, active_revision.change_id)
token, issued = issue_revision_continuation(...)
```

同时：

1. 删除文件中 `_inject_revision_backend_skeleton()` 的**完整函数定义**，不要保留死代码。
2. 删除 `inject_deterministic_backend_skeleton` 的 import。
3. 删除只由该包装函数使用的 `technical_plan_json_path` import（先 `rg` 确认没有其他引用）。
4. 删除“正在生成后端骨架”的 AG-UI 进度；保留“正在更新模板能力”等真实进度。
5. 不修改 `_reconcile_revision_template_capabilities()`、`claim_template_reconcile_finalization()`、`issue_revision_continuation()`、异常捕获、`mark_template_reconcile_failed()` 或图路由。
6. 对 `design_stage_revision` 和 `workbench_plan_revision` 使用同一改造后的 Reconcile 流程。

**成功条件**：TechnicalPlan 确认 → V4 Ensure（可能 `NO_CHANGE`）→ continuation 正常签发；在这个时间段不会写任何业务 Java 骨架。

### Step 2：删除生成器和 Build 中的虚假预置清单

删除 `Backend/app/services/template_scaffold_injection.py` 整个模块（包含 render、命名推导、写文件等内部辅助函数）。

分别修改：

**`Backend/app/graph/nodes/tasks.py`**

- 移除 `from app.services.template_scaffold_injection import prebuilt_files_for_plan`。
- `_resolve_build_context()` 中的应用范围 Context，移除：

```python
"prebuilt_files": prebuilt_files_for_plan(project_plan),
```

**`Backend/app/services/build_context_resolver.py`**

- 移除同一 import。
- `resolve_target_build_context()` 不再设置：

```python
context["prebuilt_files"] = prebuilt_files_for_plan(project_plan)
```

- 原函数仍需返回全部 Endpoint/Page 的契约和依赖事实；不要修改其余数据结构。

**`Backend/app/agents/data_source/prompt_context.py`**

- 移除同一 import。
- `task_implementation_contract()` 的 `bootstrap` 分支移除 `prebuilt_files` 字段。
- `endpoint` 分支也移除 `prebuilt_files` 字段。
- 保留 `api_contract`、`api_design`、`authorization_constraints`、`verification_policy`、数据源能力和 Java 8 约束。
- 将关于读取“预置代码”的注释更正为“读取现有模板工程及文件修改边界”，不删除内置 Skill 加载逻辑。

**注意**：

- `prebuilt_files` 不能替换为仅“看见文件存在”就表示功能已满足的 `existing_files`，本次不创建新的预置文件契约。真实文件是否存在与任务是否完成，是两个不同判断。
- 现有 WorkspaceSnapshot 和允许写入路径仍是规划/执行时的权威文件事实。**不要**改变快照与 Task `add/modify` 分类机制。

### Step 3：清理 Backend Skill 的历史前提（重点）

目标 Skill：`springboot-template-modification-boundary`。该 Skill 在 Backend Agent 执行时会和 `springboot-backend-generate` 同时作为指令使用，必须消除互相冲突的语义。

**3.1 `SKILL.md`**

将：

- “平台在进入开发阶段前已根据 TechnicalPlan 生成业务骨架”
- “通过 `prebuilt_files` 确认预置文件”
- “所有 Entity/PO/Mapper/Repository/DTO/Controller 默认已经存在”

改为：

> 业务 Java 文件不再由二次修改的 Template Reconcile 提前生成。Backend Agent 只能按已批准的 Endpoint Task 与 `allowed_paths/change_scope` 创建或最小修改当前职责文件。执行前读取真实文件、相邻实现及 WorkspaceSnapshot；已有且满足契约则复用，缺失且本任务授权创建则生成，已存在但不满足则按约束修正。不得因为 TechnicalPlan 包含某实体就批量创建该实体全部 DDD 文件。

保留：Java 8、DDD 合理分层、公共 `common/` 和 `auth/` 受管文件禁止越权修改、Mapper XML / BaseMapper 规则。明确真正的模板 Base/Extension 预置能力与业务代码是两回事。

**3.2 `references/module-layout.md`**

- 标题及示例解释为**参考目录布局**，而不是实际已创建文件树。
- 原 `✅ 预置`、`🟡 Agent 补充` 的分类改为“当前 Endpoint 需要时由任务生成或复用”，可保留完整示例结构供 Agent 参考，但不可暗示每个实体均必须产生所有文件。
- 数据库场景：按确认的数据库操作与映射生成 PO/Mapper/Repository；纯外部 API 场景：不要强制生成数据库持久化文件。
- 保留业务包、领域/应用/基础设施分层和依赖方向说明；如果现有示例包名与当前 Task Contract 不一致，以确认的 Endpoint 契约和真实工作区为准。

**3.3 四份代码参考文档**

- `entity-template.md`：由“平台已生成，Agent 补充”改为“数据库职责需要时，Agent 按约定新建或复用”。
- `repository-template.md`：同上，保留 MyBatis-Plus、Mapper XML、自定义 SQL 门槛规则。
- `dto-template.md`：由“平台已预置 DTO 字段”改为“根据 Endpoint 请求/响应 Schema 生成或复用 DTO”。
- `controller-template.md`：由“平台已经生成 Controller 端点”改为“按确认的 HTTP Method/Path/Schema 创建或调整 Controller；业务逻辑留在 Service”。

**不要**改变内置 Skill 名称、目录结构、`REQUIRED_BUILTIN_SKILL_FILES` 注册和执行时的 `instruction_paths`。只修正内容，确保现有 `Backend/app/services/builtin_skills.py` 校验仍通过。

### Step 4：测试调整与新回归

**4.1 删除历史专属测试**

- 删除 `Backend/tests/test_template_scaffold_injection.py`，它依赖已删除模块。
- 同步删除/替换测试文档中对 `tests.test_template_scaffold_injection` 的调用。

**4.2 二次修改 Template Reconcile**

在 `Backend/tests/test_application_page_planning.py` 现有测试中：

- 删除 `patch("app.graph.application_planning_workflow._inject_revision_backend_skeleton")`；该符号已经不存在，不能再 Mock。
- 保留 V4 Ensure、CAS 重试、`issue_revision_continuation()` 相关断言。
- 增加测试：含多个 TechnicalPlan 实体的二次修订，分别覆盖 `design_stage_revision` 和 `workbench_plan_revision`，Reconcile 结束后检查业务 Java 目录没有新增；已有业务文件内容/哈希完全不变。
- `NO_CHANGE` 和能力发生变更的 Reconcile 均不得触发业务骨架写入（变化场景需隔离 Template Engine 在其授权范围内产生的正常 Extension 文件，避免误判）。

**4.3 Build Context / Agent Contract**

- `test_data_source_generation_prompt.py`：检查 bootstrap 和 endpoint 的 `implementation_contract`、实际执行 task packet 均**不包含** `prebuilt_files`；保留 source snapshots、API 设计、`allowed_paths`、`change_scope`、权限切片等必要字段。
- `test_page_build_context_resolver.py`：Page / Endpoint 的 Build Context 不包含 `prebuilt_files`，而 `required_unit_ids`、Endpoint 关联、API Design 等仍准确。
- 对应用级 `_resolve_build_context()` 增加同样的断言（在既有 tests 中追加，避免为微小改动创建新的大型测试框架）。

**4.4 Backend Agent / Build 调度**

验证首次缺少业务模块的工作区，Backend Endpoint Build 可新建实际任务所需文件：

1. **数据库**：objects / repository / service / controller 阶段可按契约创建 DTO、PO、Mapper、Repository、Service、Controller；允许的 Mapper XML 按规则生成。
2. **外部 API**：objects / upstream / service / controller；不得强制创建 PO、Mapper、Repository。
3. **混合数据源**：数据库及 upstream 分支各自负责独立数据源能力，service 负责字段映射和组合。
4. **已实现业务代码**：应按 WorkspaceSnapshot 的 add/modify 分类最小修订，不能因实体在 TechnicalPlan 中出现就重新生成整个模块。
5. **启用 Authorization**：TemplateReconcile V4 正常、Controller 只引用允许的 `@RequireAnyResource` 和平台 AuthConstants，不修改 auth 内部文件。

静态单元测试不能替代真实 Agent Build；有可用模型和开发环境时至少运行数据库 + 外部 API 两个端到端 smoke test。无模型/依赖时应明确记录未执行的检查，不能声称通过。

### Step 5：全仓清理与禁止回归检查

```bash
# 生产代码不得再引用已删除模块或字段
rg -n '(template_scaffold_injection|inject_deterministic_backend_skeleton|_inject_revision_backend_skeleton|prebuilt_files_for_plan|prebuilt_files)' \
  Backend/app

# Skill 不得出现“业务骨架已提前生成”的旧说法
rg -n '(预置代码|平台已根据 TechnicalPlan|进入开发阶段前已|prebuilt_files)' \
  Backend/app/builtin_skills/springboot-template-modification-boundary

# 仍需检索所有测试和历史文档引用，并按实际用途更新或注明存档
rg -n '(template_scaffold_injection|test_template_scaffold_injection|prebuilt_files_for_plan|prebuilt_files)' \
  Backend/tests docs

git diff --check
git status --short
```

**要求**：前两个 `rg` 命令应无匹配（exit code 为 1 属于预期无结果）；第三个如仍有历史引用，必须说明是否为存档文档，避免把历史引用当运行代码。使用新命名的“Build Unit Skeleton”无需删除。

## 4. 推荐回归命令

优先使用项目自身的 Python 虚拟环境（以下为已有 `Backend/.venv` 的示例），无环境时使用项目约定的执行器，不要临时改依赖版本：

```bash
cd Backend
.venv/bin/python -m unittest \
  tests.test_application_page_planning \
  tests.test_template_reconcile_finalization \
  tests.test_data_source_generation_prompt \
  tests.test_page_build_context_resolver \
  tests.test_unit_generation_requirements \
  tests.test_backend_unit_prompt_merge \
  tests.test_build_subgraph_scheduler \
  tests.test_code_graph_agent_scope \
  tests.test_unit_task_prompt
```

补充相关测试（根据实际变更和依赖）：

```bash
.venv/bin/python -m unittest \
  tests.test_build_task_planner \
  tests.test_build_unit_skeleton \
  tests.test_prepare_build_tasks_guard \
  tests.test_build_task_reuse_workspace
```

如果不存在 `.venv/bin/python`，报告缺少的环境并用用户项目指定的 Python 环境重试；禁止把跳过测试标为通过。

## 5. 验收标准（硬性）

| 编号 | 验收项 | 通过标准 |
|---|---|---|
| AC-01 | 生产入口彻底移除 | 无 `_inject_revision_backend_skeleton()` / `inject_deterministic_backend_skeleton()` 引用和定义 |
| AC-02 | 无虚假预置契约 | Build Context、Task Packet、Bootstrap/Endpoint implementation_contract 均无 `prebuilt_files` |
| AC-03 | 二次修改无业务文件副作用 | 确认 TechnicalPlan → Template Reconcile → continuation 期间，不因实体清单创建或覆盖 Java 业务模块 |
| AC-04 | 模板能力不回退 | `NO_CHANGE` / V4 `CHANGED`、Login、Authorization 的安装行为保持不变 |
| AC-05 | 修订恢复不回退 | 两个 formal revision 分支均可签发/消费 continuation，失败可按原 changeId 重试 |
| AC-06 | Build 能生成实际代码 | 新模块及缺失文件由已有 Endpoint Build Task 按需创建；已有业务代码最小修订 |
| AC-07 | 来源分流正确 | 纯外部 API 不生成无关 MyBatis 持久层；数据库/混合来源有对应职责 |
| AC-08 | 权限边界正确 | 已有 auth/common/yht 等模板代码不被业务任务无授权改写，受控 Endpoint 注解正确 |
| AC-09 | 旧工作区安全 | 不自动删除或回滚历史由骨架注入器生成的任何 Java/XML 文件 |
| AC-10 | Skill 与真实执行一致 | 不再宣称平台预置全部业务骨架；仍需加载原 Skill，遵守 Java 8 与文件写入约束 |
| AC-11 | 非目标模块不变化 | Build Unit Skeleton、DAG/Task ID、调度器、模板引擎接口、前端业务逻辑不变 |
| AC-12 | 测试与文档清理 | 旧模块、旧字段、失效测试命令无运行态残留；相关自动化测试通过 |

## 6. 失败处理与回退

- **Template Reconcile 失败**：保持既有 `mark_template_reconcile_failed` 和基于 `changeId` 的重试；不得引入新重试标志或跳过模板状态检验。
- **Build 生成文件失败**：按已有 Task/Agent 失败协议和 Repair/EDD 规则处理；**不要**恢复自动骨架注入作为兜底。
- **发现旧 `prebuilt_files` 被隐藏依赖使用**：先定位引用方，再将其改为消费真实 WorkspaceSnapshot 和当前任务授权路径；不可通过假造 `prebuilt_files` 兼容。
- **旧工作区已存在历史骨架**：按真实业务源码继续维护；不做自动删除或批量重建，避免丢失已完成代码。
- **回退方式**：仅回退本次 XCodeAgent 代码改动；不得回滚或清理用户工作区中的 Java 文件。

## 7. Codex 交付要求

Codex 完成后必须输出：

1. **修改文件清单**：新增/修改/删除文件和每个变更目的。
2. **调用链确认**：证明二次修订不再进入业务骨架生成器，`prebuilt_files` 不再进入 Build Contract。
3. **测试结果**：逐条列出已执行命令、通过/失败/跳过，包含失败原因；如无真实模型测试环境须明确披露。
4. **回归结果**：首次模板、二次正式修改、数据库/外部 API Build、Authorization 场景验证事实。
5. **边界检查**：证明模板服务、Build Unit / DAG/Task 编译调度、Backend Agent 执行器没有被无意修改。
6. **剩余风险**：仅报告有证据的实际风险，不以“可能”代替测试事实。

---

## 8. 可直接交给 Codex 的任务指令

> 基于 `YYYWYF/XCodeAgent` 仓库最新 `dev_agent` 分支，**彻底移除二次正式修改时的自动后端业务骨架注入机制**，同时清除 Build 中配套的 `prebuilt_files` 虚假预置文件契约。严格按本方案的 Step 0–5 实施。
>
> 重点：`_reconcile_confirmed_revision` 只能进行模板能力 Reconcile 和 continuation 签发，不能自动遍历 TechnicalPlan.entities 写 Java；删除 `template_scaffold_injection.py` 和专属测试；清理 `tasks.py`、`build_context_resolver.py`、`prompt_context.py` 的 `prebuilt_files`；更新 `springboot-template-modification-boundary` 的 Skill 及五份 references，不再暗示业务文件已提前生成。
>
> **不要**改 Template Engine、Login/Authorization V4 能力、Build Unit Skeleton、Unit Generation Requirements、Backend Task Manifest、Scheduler 或 Backend Agent 实现逻辑。当前 Build 的分层任务已具备业务文件创建能力，复用现有机制，不设计替代注入器。
>
> 同步维护测试和失效文档，至少运行第 4 节回归；对未能执行的端到端验证如实报告。最终按第 7 节输出交付清单、测试事实及遗留风险。若本地最新代码与这里的引用范围不一致，以 `rg` 和源码事实为准，保护用户未提交改动、保持修改范围最小。
