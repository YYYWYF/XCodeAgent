# XCodeAgent 后端 Agent 节点审计报告（按 13.3 模板）

> 模板依据：`docs/XCODEAGENT_COMPLETE_WORKFLOW.md` 第 13.3 节「后续节点审计统一模板」
> 审计日期：2026-08-11
> 审计基线：当前工作区源码（含未提交改动：`task_preparer.py` / `build_task_planner.py` 的验收字段强制置空、`main.py` 格式化与 uvicorn 入口）
> 2026-08-18 更新：数据库上下文检查节点已退役，相关段落按现行工作流收敛。
> 审计范围：
> - 应用初始化阶段：`requirements`、`ui_confirmation`（ui_design）、`project_planning`
> - 任务规划阶段：`inspect_workspace`、`prepare_build_tasks`
> - build 阶段后端 Agent：`data_source_agent`（owner=backend）
> 性质：只读审计，未改动任何代码

---

## 1. 审计方法

1. 以 13.3 模板的 17 个字段为审计骨架，逐节点补齐：

   ```text
   节点名称 / node_id / 版本
   节点类型
   直接负责人（DRI）/ 审核人 / 批准人
   目标与非目标
   触发条件 / 前置条件
   当前提示词及源码位置
   输入
   输出
   成功与校验规则
   硬依赖 / 可选依赖
   可并行条件 / 冲突资源 / join 条件
   副作用 / 授权范围 / 幂等键 / checkpoint
   错误分类
   最大重试次数 / 退避 / 超时
   成功路由 / 失败路由 / 修复后回测路由
   用户确认载荷 / basedOnRevision
   可观测 evidence / 日志和持久化路径
   ```

2. 事实来源：节点实现源码 + 提示词源码 + 文档交叉核对；每项结论附源码位置。
3. 说明：仓库没有显式的 DRI/审核人/批准人元数据，该项按模块归属推断，建议后续在节点 docstring 或 artifacts 中补齐。

---

## 2. 审计对象总览

| 节点/Agent | 阶段 | 节点类型 | 主要源码位置 | 产物 schema 版本 |
| --- | --- | --- | --- | --- |
| `requirements` | 应用初始化 | 直接 ChatModel + 确定性确认门禁 | `agents/main/requirements_analyzer.py`、`graph/nodes/requirements.py` | requirement-spec（无显式版本，靠 `confirmation_status`） |
| `ui_confirmation`（ui_design） | 应用初始化 | 每页直接 ChatModel + 代码校验 + 确认门禁 | `services/ui_design_generator.py`、`graph/nodes/ui_confirmation.py` | ui-designs.json（无显式版本） |
| `project_planning` | 应用初始化 | 直接 ChatModel + 确定性修复/契约校验 + 确认门禁 | `agents/main/planner.py`、`graph/nodes/planning.py` | project-plan（`version` / `source_project_plan_version`） |
| `inspect_workspace` | 任务规划 | 确定性扫描 + 代码图索引（无 LLM） | `graph/nodes/workspace_inspection.py`、`services/workspace_inspector.py` | workspace-snapshot `1.2.0` |
| `prepare_build_tasks` | 任务规划 | 直接 ChatModel（无工具）+ 7 阶段确定性编译器 | `graph/nodes/tasks.py`、`agents/main/task_preparer.py`、`services/build_task_planner.py` | `build-dag.v3` + `build-unit-graph.v3` |
| `data_source_agent` | build | Deep Agent + 确定性调度/验收 harness | `agents/data_source/agent.py`、`agents/data_source/generator.py`、`graph/subgraphs/build.py` | `build-dag.v3`、`repair-acceptance.v2` |

---

## 3. 逐节点审计

### 3.1 `requirements` / 需求分析与需求文档确认

| 模板字段 | 审计结论 |
| --- | --- |
| 节点名称 / node_id / 版本 | `requirements`；requirement-spec 无独立 schema 版本，靠 `confirmation_status`（`pending_user_input` / `pending_user_confirmation` / `confirmed`）驱动状态机 |
| 节点类型 | 直接 ChatModel（`bind_tools([ask_user])`，非 Deep Agent）+ 确定性文档同步/确认门禁；Graph 入口见 `graph/application_planning_workflow.py::_requirements` |
| DRI / 审核人 / 批准人 | main agent 负责人 / node 实现人 / 用户（确认门禁） |
| 目标与非目标 | 目标：生成/修订完整 RequirementSpec 并等待用户确认，覆盖应用信息、角色、模块、页面、数据源、业务流程、验收标准。非目标：不规划、不生成/修改代码、不调 subagent；只允许 `ask_user`，每轮 1–4 个实质性澄清问题，禁止开放式“还有没有更多页面/角色”追问 |
| 触发条件 / 前置条件 | 创建规划 Graph 默认入口或 `resume_from=requirements`；前置：`workspaceRoot` 非空、lifecycle 可创建/读取、`.xcodeagent/application.json` 可读取数据源权威类型与菜单 `rootPath/enable` |
| 当前提示词及源码位置 | `agents/main/requirements_analyzer.py::_requirements_prompt`；数据源类型只读注入，禁止推断或改写；修订时注入完整旧 spec 并要求保留稳定 ID |
| 输入 | `state.request`、已有 `requirement_spec`（修订）、权威 `datasource_type`、菜单 rootPath/enable、本轮澄清答案或 `edited_requirement_spec`/编辑后 Markdown、`user_interaction_submission` |
| 输出 | `requirement_spec`、`requirement_spec_path`（Markdown）、`requirement_spec_json_path`、`clarification`、`status`、`timeline`；写入 `.xcodeagent/specs/requirement-spec.md\|json`；推进 lifecycle（`ANALYZING_REQUIREMENT → GENERATING_REQUIREMENT_SPEC → AWAITING_REQUIREMENT_CONFIRMATION → GENERATING_UI_DESIGNS`） |
| 成功与校验规则 | 需求缺口进入 `pending_user_input`；澄清答案不能视为确认；确认必须来自本轮显式交互；Markdown 修改同步回 JSON（`sync_requirement_spec_from_markdown` / `apply_requirement_spec_editor_changes`）；页面路由去重并应用菜单根路径；数据源类型强制覆盖为权威类型；重复澄清抑制（仅放行“其他/补充/是否还有”类可选追加问题） |
| 硬依赖 / 可选依赖 | 硬依赖：`application.json`、spec_documents、requirement_spec 服务、ask_user 工具；可选：已存在 spec（修订）、编辑后 Markdown |
| 可并行条件 / 冲突资源 / join 条件 | 不可并行（单节点串行）；冲突资源：spec md/json 文件与 lifecycle JSON（原子写）；无 join |
| 副作用 / 授权范围 / 幂等键 / checkpoint | 写 spec 文件、推进 lifecycle、写 Graph checkpoint（SQLite，按 workspace/project 分库）；幂等键：`requirement_spec_path + confirmation_status`；异常时 lifecycle 记 `FAILED(recoverable=true)` 或 `CANCELLED`，保留同一阶段供重试 |
| 错误分类 | 缺少用户输入/正式产物 → `requires_user_input`；模型/解析异常 → 抛异常并落 lifecycle FAILED；无节点级重试预算，仅依赖传输层重试 |
| 最大重试次数 / 退避 / 超时 | 模型层 `MODEL_MAX_RETRIES=2`、`MODEL_TIMEOUT_SECONDS=120`；无指数退避；节点本身不重试 |
| 成功路由 / 失败路由 / 修复后回测路由 | `confirmed → ui_confirmation`；`requires_user_input → END`（await_user_input）；失败/取消 → 用户显式重跑同阶段 |
| 用户确认载荷 / basedOnRevision | `mode=requirement_spec_confirmation`，含 `spec_summary` 与文本确认问题；无 revision/hash 字段，靠文件路径 + confirmation_status 恢复 |
| 可观测 evidence / 日志和持久化路径 | `llm.token` 流式事件、ask_user 澄清载荷、`.xcodeagent/specs/requirement-spec.md\|json`、lifecycle JSON、SQLite checkpoint |

### 3.2 `ui_confirmation`（ui_design）/ UI 设计稿生成与确认

| 模板字段 | 审计结论 |
| --- | --- |
| 节点名称 / node_id / 版本 | `ui_confirmation`；`ui-designs.json` 无显式 schema 版本 |
| 节点类型 | 每页直接 ChatModel 生成 TSX（`bind(max_tokens=ui_design_max_tokens=8192)`）+ 确定性代码校验 + 用户确认门禁 |
| DRI / 审核人 / 批准人 | ui_design 服务负责人 / node 实现人 / 用户（逐页确认） |
| 目标与非目标 | 目标：为每个页面生成纯视觉 React + antd5 + `@ant-design/pro-components` 设计稿（内联 Mock、无 API、无 useEffect/fetch），等待全部页面确认。非目标：不 clone 模板工程、不装依赖、不启 dev server、不改需求、不注册菜单 |
| 触发条件 / 前置条件 | requirements 确认后；`resume_from=ui_confirmation`；前置：`requirement_spec.pages` 存在且确认。首次进入只生成骨架（pending），用户逐页“选模板 / 换一换 / 多页调整”后经 `ui_design_action` 回填 |
| 当前提示词及源码位置 | `services/ui_design_generator.py::_build_ui_design_prompt`（内联 antd-ui-design SKILL.md 全文）；失败修复 `_build_repair_prompt`；调整 `_build_adjust_prompt` |
| 输入 | `requirement_spec.pages`（pageId/name/path/description）、`page_key`、`ui_design_action`（select_template/regenerate/adjust_pages）、已有落盘 code（恢复时复用）、`.xcodeagent/ui-design` 目录 |
| 输出 | `.xcodeagent/ui-design/pages/<PageKey>/index.tsx`、内联 `code`、`code_path/menu_path/route_path/status`、`ui-designs.json`（`confirmation_status`）、`ui_confirmation.progress` 流式事件 |
| 成功与校验规则 | 非空 + `export default` + 长度 ≥30；import 仅白名单（react/react-dom/antd/pro-components/icons/cssinjs/dayjs）；无未定义 JSX 引用；esbuild TSX 语法校验（缺失时仅跳过）；失败按独立预算回喂修复，耗尽后该页 `generation_failed`，绝不把未通过代码落成可用设计稿；`regenerate` 成功即置该页 `confirmed` |
| 硬依赖 / 可选依赖 | 硬依赖：antd-ui-design 技能全文、`.xcodeagent/ui-design` 目录；可选：esbuild、已存在设计稿 |
| 可并行条件 / 冲突资源 / join 条件 | 生成并发上限 3（`_UI_DESIGN_CONCURRENCY=3`）；`page_key` 用 `used_keys` 去重防写冲突；adjust 串行防限流/写冲突 |
| 副作用 / 授权范围 / 幂等键 / checkpoint | 写 ui-design 目录与 `ui-designs.json`，推进 lifecycle 至 `AWAITING_UI_DESIGN_CONFIRMATION`；幂等键：`page_key + code_path`；checkpoint 恢复依赖文件复用 |
| 错误分类 | 模型输出不合格 → 自动修复；修复耗尽 → 单页 `generation_failed`（不阻断其他页）；全部确认缺失 → `requires_user_input` |
| 最大重试次数 / 退避 / 超时 | 每页“首次生成 + 最多 `UI_DESIGN_MAX_RETRIES=2` 次修复”共 3 次模型调用（与底层 `MODEL_MAX_RETRIES` 独立）；无退避 |
| 成功路由 / 失败路由 / 修复后回测路由 | 全部页面 confirmed → `project_planning`；否则停在 `await_user_input` 重放确认卡；失败页可再次“换一换” |
| 用户确认载荷 / basedOnRevision | `mode=ui_design_confirmation`，含 `pending_count` 与 `pages`（内联 code）快照；“确认全部设计稿”才放行；无 revision 哈希 |
| 可观测 evidence / 日志和持久化路径 | `ui_confirmation.progress`、`ui_design_generated/validate_failed/repaired` 日志、`ui-designs.json`、落盘 TSX |

### 3.3 `project_planning` / 项目计划生成与确认

| 模板字段 | 审计结论 |
| --- | --- |
| 节点名称 / node_id / 版本 | `project_planning`；产物含 `version`，下游 build-dag 记录 `source_project_plan_version` |
| 节点类型 | 直接 ChatModel（无工具、planning-only）+ 确定性策略修复/契约校验 + 用户确认门禁 |
| DRI / 审核人 / 批准人 | main planner 负责人 / node 实现人 / 用户 |
| 目标与非目标 | 目标：由 RequirementSpec 生成完整 ProjectPlan（页面树、数据源、权限、流程、架构、验收标准、API 契约）。非目标：不改代码；数据库应用固定 Java 8 + Spring Boot + MySQL 8 + Redis，Static 应用固定前端内存数据层，不可协商 |
| 触发条件 / 前置条件 | 创建 Graph 中 UI 全部确认后；主 Graph 中为验收调整/SmallTask 升级后的计划修订入口；`resume_from=project_planning`；前置：RequirementSpec 已确认 |
| 当前提示词及源码位置 | `agents/main/planner.py::_planning_prompt`（严格路由规则 A–H、schema 必须位于同一 contract、`api_contracts` 为字段事实源）；修订走 `revise_project_plan_with_chat_model` |
| 输入 | 已确认 RequirementSpec、既有 ProjectPlan（修订）、权威数据源类型、`request` 计划反馈、`route_root_path/menu_enabled` 菜单策略 |
| 输出 | `project_plan`（`pending_user_confirmation`/`confirmed`）、`frontend_pages`、`project_plan_path`（Markdown）、`project_plan_json_path`、`clarification`；创建 Graph 确认后 `application_planning_confirmation`（`confirm_application_planning_artifacts`） |
| 成功与校验规则 | 页面依赖 + API 契约一致性 + 数据源策略三合一校验；失败自动回灌错误修订一次；仍有剩余错误 → 用户澄清，不得进入模板生成或 Build；用户 Markdown 修改同步回 JSON 并完整重写 Markdown 恢复权威数据源类型 |
| 硬依赖 / 可选依赖 | 硬依赖：RequirementSpec confirmed、权威数据源类型；可选：既有 plan、`planning_adjustment_request` |
| 可并行条件 / 冲突资源 / join 条件 | 不可并行；冲突资源：`plans/project-plan.md\|json` |
| 副作用 / 授权范围 / 幂等键 / checkpoint | 写计划文档；创建 Graph 确认后推进 lifecycle 至 `GENERATING_APPLICATION_TEMPLATE_FILES`；幂等键：`project_plan_path + confirmation_status` |
| 错误分类 | 可确定性修复 → 自动修订一次；不可修复缺口 → `requires_user_input`；模型/解析异常 → 抛异常并落 lifecycle FAILED |
| 最大重试次数 / 退避 / 超时 | 自动修复 1 次；传输层重试 2 次/120s；无节点级循环 |
| 成功路由 / 失败路由 / 修复后回测路由 | 创建 Graph：confirmed 后 END（转模板生成）；主 Graph：confirmed 后回 `detail_confirmation`；`requires_user_input → await_user_input` |
| 用户确认载荷 / basedOnRevision | `mode=project_plan_confirmation`（含 `plan_summary`）或 `project_plan_dependency_validation_error`（含 errors 摘要）；基于 `plan.version`/`source_project_plan_version` 追踪修订 |
| 可观测 evidence / 日志和持久化路径 | `llm.token`、澄清载荷、`plans/project-plan.md\|json`、lifecycle JSON、checkpoint |

### 3.4 `inspect_workspace` / 检查工作区

| 模板字段 | 审计结论 |
| --- | --- |
| 节点名称 / node_id / 版本 | `inspect_workspace`；快照 schema `1.2.0`，按 `workspace_revision` 缓存 |
| 节点类型 | 确定性扫描和代码图索引；无 LLM 提示词 |
| DRI / 审核人 / 批准人 | workspace_inspector / code_graph 负责人；无人工确认 |
| 目标与非目标 | 目标：建立工作区文件清单、技术栈、入口、构建/测试命令、前后端事实、CRG 代码图，供任务规划导航。非目标：不修改业务代码；首次进入的前端 scaffold（`menus.ts` + 页面占位）仅为辅助，异常只记日志 |
| 触发条件 / 前置条件 | `detail_confirmation` 完成后；`resume_from=inspect_workspace` 恢复时不重复 scaffold；前置：显式 `workspaceRoot`、ProjectPlan |
| 当前提示词及源码位置 | 无提示词；`services/workspace_inspector.py::inspect_workspace` |
| 输入 | workspace 根、缓存目录、ProjectPlan、CRG provider、`on_progress` 回调 |
| 输出 | `workspace_snapshot_summary`（受限字段投影）、`workspace_snapshot_path`（`cache/workspace-snapshots/<revision>.1.2.0.json`）、`workspace_snapshot_hash`（SHA-256）、`workspace_revision`、`workspace_inspection.progress` 事件、`timeline`（cache_hit 标记） |
| 成功与校验规则 | `_safe_workspace_file` 路径限制；文件数上限 4000（截断标记）；按 revision+schema 命中缓存（命中时仍重跑 CRG 并回写）；无显式 workspaceRoot 时完全跳过 CRG；CRG 不可用降级 `NullCodeGraphProvider`，不阻断流程 |
| 硬依赖 / 可选依赖 | 硬依赖：rg 文件清单（30s 超时，失败降级）；可选：CRG、frontend_scaffold |
| 可并行条件 / 冲突资源 / join 条件 | 无并行；冲突资源：快照缓存文件（revision 键控，天然幂等） |
| 副作用 / 授权范围 / 幂等键 / checkpoint | 写快照缓存；首次进入写 `src/constants/menus.ts` 与页面占位目录（存在 scaffold 与前端菜单任务双 owner 风险，见 4 节）；幂等键：`workspace_revision + schema_version` |
| 错误分类 | 文件搜索/CRG 失败可降级；scaffold 异常仅日志；无阻断性错误 |
| 最大重试次数 / 退避 / 超时 | 子进程超时 10s；CRG 内部自身超时/降级；节点不重试 |
| 成功路由 / 失败路由 / 修复后回测路由 | 成功后固定进入 `prepare_build_tasks`；扫描失败按既有降级策略处理 |
| 用户确认载荷 / basedOnRevision | 无确认；`basedOnRevision=workspace_revision` |
| 可观测 evidence / 日志和持久化路径 | `workspace_inspection.progress`、`frontend_scaffold` 日志、快照 JSON + hash |

### 3.5 `prepare_build_tasks` / 生成并编译 Build DAG

| 模板字段 | 审计结论 |
| --- | --- |
| 节点名称 / node_id / 版本 | `prepare_build_tasks`；产物 `build-dag.v3` + `build-unit-graph.v3`，修复任务验收 `repair-acceptance.v2` |
| 节点类型 | 直接 ChatModel（无工具、planning-only）+ 7 阶段确定性编译器（unit_skeleton → build_context → contract_validation → model_planning → task_compilation → dag_validation → artifact_persistence） |
| DRI / 审核人 / 批准人 | task_preparer / build_task_planner 负责人；模型候选任务由确定性编译器“审核” |
| 目标与非目标 | 目标：按 scope 编译后端/前端可执行任务 DAG（后端四阶段 stage 拆分、页面 API 服务文件、菜单登记）。非目标：模型不得生成数据库、验证或测试任务；不得越过 required Unit；不重建模板骨架 |
| 触发条件 / 前置条件 | 上游 `inspect_workspace`；`resume_from=prepare_build_tasks`；前置：ProjectPlan confirmed（未确认先走确认/修订分支）、目标详情和绑定实体设计存在 |
| 当前提示词及源码位置 | `agents/main/task_preparer_prompt.py::build_task_preparation_prompt`（规划器自有七段规则、后端真实目录树、Java 8 约束、四阶段 stage 规则；不读取或内联 Skill）；Static 复用同一 Prompt 的范围化规则 |
| 输入 | 确认 ProjectPlan、`build_execution_scope`、PageDetail/EndpointDetail、有界实体设计摘要、WorkspaceSnapshot（裁剪到 80 项/12k 字符）、已有 DAG、可复用 Unit |
| 输出 | `build-dag.v3`（build_units/unit_graph/task_registry/task_graph/tasks/execution batches）、`build_context`、`dag_generation_progress` 七阶段快照、`.xcodeagent/plans/build-task-plan.json`、`BUILD_TASK_DAG.md` |
| 成功与校验规则 | Unit skeleton 合法；契约校验（页面依赖、API contract scope）；模型任务归一化后强制置空验收字段，工程验收由确定性编译器按 change_scope/allowed_paths/菜单/API 契约生成；正常 Build 不含 database Unit/owner；任务 ID/依赖/拓扑/循环/批次校验；页面 PageKey 与实时唯一目录纠正、漏报菜单登记时确定性补齐 |
| 硬依赖 / 可选依赖 | 硬依赖：确认 ProjectPlan、详情、实体摘要和快照；可选：可复用 Unit 与已有 DAG |
| 可并行条件 / 冲突资源 / join 条件 | 编译阶段串行；冲突资源：build-task-plan.json；执行批次（`execution.batches`）决定后续 build 并行面 |
| 副作用 / 授权范围 / 幂等键 / checkpoint | 写 build-task-plan.json 与 BUILD_TASK_DAG.md；幂等键：`build-dag.v3 + source_project_plan_version + workspace_snapshot_ref.workspace_revision`；checkpoint 持久化 |
| 错误分类 | 各阶段失败 → `requires_user_input`（带错误澄清，用户确认后重跑，不自动重试）；模型 JSON 解析失败/无有效任务 → 同样转 requires_user_input；异常型错误抛给节点生命周期 |
| 最大重试次数 / 退避 / 超时 | 模型单次调用 + 传输层重试 2 次/120s；`default_max_tokens=2048`；无节点级自动重试 |
| 成功路由 / 失败路由 / 修复后回测路由 | completed → `build`；requires_user_input → END；后续修复不再回本节点（修复任务直接追加到 build 调度） |
| 用户确认载荷 / basedOnRevision | `build_context_error` / `api_contract_inconsistency` / `build_task_plan_validation_error` 等澄清载荷；`basedOnRevision` 为 plan version 与 workspace revision |
| 可观测 evidence / 日志和持久化路径 | `dag_generation_progress` 七阶段快照、脱敏模型诊断日志（response_sha256、token 用量、finish_reason）、build-task-plan.json、BUILD_TASK_DAG.md |

### 3.7 `data_source_agent`（build 阶段后端 Agent，owner=backend）

| 模板字段 | 审计结论 |
| --- | --- |
| 节点名称 / node_id / 版本 | `data_source_agent`（registry 名 `data-source-generation-agent`）；build 任务 owner=`backend`，遵循 `build-dag.v3` |
| 节点类型 | Deep Agent（`create_deep_agent`）+ 外层确定性 BuildScheduler 调度与工程验收 harness |
| DRI / 审核人 / 批准人 | data_source Agent 负责人 / 确定性验收编译器 / 用户（仅高危数据库审批与修复范围扩大时） |
| 目标与非目标 | 目标：执行批准的 Java 8 后端实现任务，按任务实体来源读取 springboot-mybatis-generate 或 springboot-external-api-generate，严格服从已确认 API Contract、EndpointDetail、EntityDesign 与允许路径。非目标：本阶段不执行数据库结构变更、迁移、种子数据、项目级构建/测试/安装；不改正式规划产物或 DAG；不静默改契约（改不了就返回 change_request） |
| 触发条件 / 前置条件 | build 节点选择就绪批次后按 owner 分组派发；前置：同批数据库任务已完成（`_database_first_candidates`）、依赖满足、文件锁不冲突、任务已编译确定性验收检查 |
| 当前提示词及源码位置 | System Prompt：`agents/data_source/agent.py::create_data_source_agent`；执行 Prompt：`agents/data_source/generator.py::_data_source_generation_prompt`；定向上下文与任务级 Skill 映射：`agents/data_source/prompt_context.py`。执行 Prompt 仅保留最小任务包、目标正式上下文、单份验证边界和 JSON 结果协议。 |
| 输入 | backend owner 最小任务包（id/unit/description/change_scope/allowed_paths/target_files/实体与接口引用/required_skill_paths）、目标 API Contract + EndpointDetail + 完整 EntityDesign、`selected_skill_names`；工具：delete_file、受限 execute、get_mysql_config。完整 ProjectPlan、BuildTaskPlan summary、调度验收字段和 Code Graph 不进入 DataSource 模型上下文。 |
| 输出 | 每 task 结构化 `task_results`（task_id/status=completed\|already_satisfied\|failed/summary/failure_category/failure_reason/change_request）、真实文件 diff（`capture_agent_file_changes` + 批次级快照 diff 校验）、`build_results`、`acceptance_evidence` |
| 成功与校验规则 | 结果必须覆盖全部任务；真实 diff 必须落在任务授权路径（`_filter_change_set_for_tasks` + `apply_batch_scope_violation`）；`verify_task_file_changes` 用工程验收检查完成态任务（文件操作类型、scope boundary、菜单登记、后端契约绑定——必须实现 endpoint method/path 且 DTO JSON 映射匹配 request/response schema）；Agent 自然语言不是验收证据 |
| 硬依赖 / 可选依赖 | 硬依赖：确认 API Contract/EndpointDetail/EntityDesign、任务级实体引用、工作区文件权限和匹配的内置 Skill；可选：MySQL 配置工具、用户技能快照。DataSource 不依赖 WorkspaceSnapshot 代码图。 |
| 可并行条件 / 冲突资源 / join 条件 | 与 frontend/database owner 按 owner 组并发（ThreadPoolExecutor）；同 owner 内按文件锁选择兼容批次（`_lock_compatible_batch`，锁来自 lock_scope/change_scope/target_files/allowed_paths）；数据库任务先行；跨 owner 文件通过批次 diff 归属校验 |
| 副作用 / 授权范围 / 幂等键 / checkpoint | 写 `backend/` 下文件（`permissions mode=data_source`），删除须用 delete_file；受限命令工具；幂等键：`task_id + retry_count`（仅显式 `retry_failed_tasks` 恢复 pending，带 `retry_at` 审计）；checkpoint：SQLite + build-task-plan.json / repair_task_plan.json |
| 错误分类 | runner/协议异常（`runner_protocol_error`）→ retry 类；代码/契约/质量错误 → repair 类 → 只读 RepairPlanner 编译修复任务（继承父任务授权范围）；范围扩大/改变确认产物 → `requires_user_confirmation`；用户拒绝/不可恢复 → failed → `handle_failure` |
| 最大重试次数 / 退避 / 超时 | 调度循环上限 `max_iterations = len(tasks)*2`；模型层重试 2 次/120s；显式 retry 恢复带 `retry_count` 递增审计；无指数退避；修复任务经 RepairPlanner 后在同一 build 节点内继续调度 |
| 成功路由 / 失败路由 / 修复后回测路由 | 全部切片任务完成 → `integration_test`（`route_build_result` 只放行 `build_summary.status=completed`）；needs_repair → RepairPlanner → 批准后回 build 批次；requires_confirmation（数据库审批/修复范围）→ await_user_input；失败 → handle_failure；修复后回测走 integration_test，不直接回 build |
| 用户确认载荷 / basedOnRevision | 数据库高危审批 `mode=agent_approval`（含 risk、statementCount、绑定的 `database_change_plan`；批准时把同一份计划写入任务 `approved_database_change_plan` 复用，防重新生成导致 plan hash 漂移）；修复范围扩大 `repair_scope_confirmation`；`basedOnRevision`：`source_project_plan_version + workspace_snapshot_ref.workspace_revision + build-dag.v3 + repair-acceptance.v2` |
| 可观测 evidence / 日志和持久化路径 | `build` 进度事件（`scheduler:*`、`build_execution_slice`）、`agent-process` 工具活动（非持久化）、`llm.token`、`build_results`/`acceptance_evidence`、脱敏诊断日志、build-task-plan.json / BUILD_TASK_DAG.md / repair_task_plan.json |

---

## 4. 横切发现与建议

1. **信任边界设计一致且合理**：三个初始化节点和任务规划都是“planning-only 提示词 + 确定性门禁兜底”；build 后端 Agent 的结果不以自然语言为准，全部由工作区 diff 和确定性验收检查验证。模型能影响的范围被压缩到“任务候选/设计稿/计划草案”本身。
2. **重试策略分层但不对称**：传输层 2 次/120s；UI 设计有独立 1+2 预算；build 调度循环 `len(tasks)*2`；显式 retry 恢复。但 `prepare_build_tasks` 失败只转 `requires_user_input`，没有节点级自动重试/退避。
3. **revision/hash 覆盖不全**：build-dag.v3、workspace-snapshot 1.2.0、repair-acceptance.v2 有版本；但 requirement-spec 与 ui-designs.json 无 schema 版本或内容哈希，恢复只靠 `confirmation_status` + 文件路径。
4. **已知风险与代码 TODO 一致**：工作区未提交改动强制清空模型验收字段并留有 `TODO(验收措施)`，说明“确定性验收编译器”仍是当前唯一验收手段；前端 scaffold 与菜单任务双 owner 风险仍需关注。
5. **文档 13.3 模板的“DRI/审核人/批准人”仓库无元数据**，当前只能按模块归属推断；若要形成正式审计基线，建议在节点 docstring 或 artifacts 中补充 DRI 与审核人字段。

---

## 5. 附：13.3 模板原文

```text
节点名称 / node_id / 版本：
节点类型：LLM / Deep Agent / 确定性逻辑 / 工具动作 / 人工确认 / 终态
直接负责人（DRI）/ 审核人 / 批准人：

目标与非目标：
触发条件 / 前置条件：
当前提示词及源码位置：

输入：
- artifact 文件 + 字段/JSON Pointer + 来源节点 + revision/hash
- Graph State 字段 + schema 版本

输出：
- artifact 文件 + 字段 + schema 版本 + revision/hash
- Graph State delta
- AG-UI 自定义事件、状态快照和结构化 result/error

成功与校验规则：
硬依赖 / 可选依赖：
可并行条件 / 冲突资源 / join 条件：
副作用 / 授权范围 / 幂等键 / checkpoint：

错误分类：
- 缺少用户输入或正式产物
- 可重试模型、工具、网络或进程错误
- 代码、契约或质量错误
- 需要扩大范围或升级正式 Workflow
- 用户拒绝或不可恢复错误

最大重试次数 / 退避 / 超时：
成功路由 / 失败路由 / 修复后回测路由：
用户确认载荷 / basedOnRevision：
可观测 evidence / 日志和持久化路径：
```

---

## 6. 参考源码索引

### 应用初始化阶段

- `Backend/app/graph/application_planning_workflow.py`：创建规划 Graph、生命周期推进与恢复路由
- `Backend/app/graph/nodes/requirements.py`：需求节点状态机（分析/修订/确认）
- `Backend/app/agents/main/requirements_analyzer.py`：需求提示词与直接模型调用
- `Backend/app/graph/nodes/ui_confirmation.py`：UI 确认节点（骨架/动作/确认）
- `Backend/app/services/ui_design_generator.py`：设计稿生成/校验/修复/调整
- `Backend/app/graph/nodes/planning.py`：项目规划与确认门禁
- `Backend/app/agents/main/planner.py`：规划提示词与模型调用
- `Backend/app/services/project_plan.py`、`services/api_contract_validation.py`、`services/page_dependencies.py`：计划校验与修复

### 任务规划阶段

- `Backend/app/graph/nodes/workspace_inspection.py`：工作区扫描节点
- `Backend/app/services/workspace_inspector.py`：快照构建/缓存/降级
- `Backend/app/services/frontend_scaffold.py`：前端脚手架（菜单 + 页面占位）
- `Backend/app/services/entity_design.py`：实体数据库设计、确认与执行证据
- `Backend/app/services/database_schema_summary.py` / `database_schema_diff.py` / `database_requirement_schema.py`：实体设计和专门数据库流程的 schema 探测与 diff
- `Backend/app/graph/nodes/tasks.py`：prepare_build_tasks 节点（7 阶段）
- `Backend/app/agents/main/task_preparer.py`：任务规划提示词与模型调用
- `Backend/app/services/build_task_planner.py`：任务归一化、编译、语义校验
- `Backend/app/services/build_task_progress.py`：DAG 生成进度七阶段快照
- `Backend/app/services/build_unit_skeleton.py` / `build_context_resolver.py` / `build_task_menu.py` / `build_unit_compiler.py` / `engineering_acceptance.py`：确定性编译与验收

### build 阶段后端 Agent

- `Backend/app/graph/subgraphs/build.py`：BuildScheduler 调度循环、owner 派发、审批与修复路由
- `Backend/app/agents/data_source/agent.py`：Data Source Deep Agent 创建（系统提示、工具、权限）
- `Backend/app/agents/data_source/generator.py`：后端执行提示词与调用
- `Backend/app/agents/registry.py`：Agent bundle 按工作区/技能快照缓存
- `Backend/app/agents/workspace_scope.py`：工作区文件权限与虚拟文件系统
- `Backend/app/services/build_scheduler.py`：批次选择、结果归一化、验收验证、显式重试
- `Backend/app/services/engineering_acceptance(_verifier).py`：确定性工程验收检查
- `Backend/app/services/build_result_coordinator.py`：任务结果协议化
- `Backend/app/agents/repair_planner/`：构建失败修复规划（只读）

### 配置与协议

- `Backend/app/config.py`：`MODEL_MAX_RETRIES`、`MODEL_TIMEOUT_SECONDS`、`UI_DESIGN_MAX_RETRIES`、`UI_DESIGN_MAX_TOKENS`、`AGENT_MAX_TOKENS`
- `Backend/app/graph/workflow.py`：主 Graph 节点路由（build → integration_test 等）
- `Backend/app/graph/state.py`：`ProjectState` 字段定义
