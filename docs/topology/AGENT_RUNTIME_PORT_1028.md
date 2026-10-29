# Agent Runtime 搬运到 1028

日期：2026-10-28。
当前开放状态：用户明确要求提前开放 Runtime 调试验证，架构选项已可选；选择后仍需确认技术规划。Python 工程检查、测试生成、数据库/模型注入、受控启动及静态业务验收已接入代码；复杂动态/自然语言语义按验收能力边界明确 blocked，独立 Debug Profile 尚未完成，模板端尚需同步及联调。下文此前批次的“不可用／完成后开放”属于当时状态，已被本决定取代。没有关闭正式确认、应用能力或真实模板检查；本批未运行测试、构建或健康检查。
来源：`dev_agent_integration_0922_back@aa9e3375`。
基线：`dev_agent_integration_1028@43b1f11d`。

## 原则

以 1028 当前产品、存储和协议合同为准，按功能迁入，不执行整分支覆盖。当前 Java 流程不因 Runtime 扩展而改变行为。所有新增产品动作使用 AG-UI；正式产物仍需明确确认。禁止历史兼容、目录猜测、自动拓扑选择、旧 Git 模板兜底和 SQLite 业务建表。

## 搬运顺序

| 阶段 | 范围与前置依赖 | 当前状态 |
| --- | --- | --- |
| 1. 设计与基础合同 | 搬入本目录 8 份设计；统一品牌、内置数据库、模板和确认门；移植拓扑类型、事实提取和显式选择协议 | 基础事实与路径策略共 11 项测试通过；未注册 Definition、未启用 UI |
| 2. 模板与配置 | 在当前 Template Engine Generate／TemplateState 及配置更新事务中支持 Runtime 根、capability、受控路径和路由描述；对齐 Python 模板实际配置键 | Studio 侧已写入请求、State 身份、根校验、事务物化、就绪与配置能力保留；本批待一次验证，真实模板由用户后续同步 |
| 3. 生成与 DAG | Runtime 七模块、Python 业务 owner／Units、当前 Endpoint Design 到 Python Build Context、页面依赖、验收与失效规则；接入当前确认／结束／恢复 | 已接入生成/DAG和静态业务验收：字段/DTO、路由、SQL、服务返回、七模块实际装配及来源哈希；不支持的动态/自然语言语义明确 blocked，未运行验证 |
| 4. 内置数据库 | 共用已确认实体和 table_name、SaaS 创建／重建／检查；Python Repository、事务、驱动与 TLS；启动时取密并只注入 Python 子进程 | Studio 已共用结构检查和一次取密，注入 MySQL 组件及 TLS 模式；模板驱动、事务和 TLS 实现需用户同步并联调，不执行 SQLite/启动 DDL |
| 5. 启动和调试 | Runtime 依赖同步、启动／停机／心跳，模型配置注入、可信身份、会话凭据，三类服务和日志、调试入口 | 受控启动、状态订阅健康检测、停止/退出清理、主模型注入、三服务及日志、独立窗口接口调试已接入；前端代理核对本次进程。独立 Debug Profile/短期凭据及真实会话联调未完成 |
| 6. 一次验证 | 针对搬运范围的 Python 检查、前端 build、Backend health、现有 Electron 内会话与入库调试 | 待实施；每项最多一次，失败立即停止并记录，不自动循环修复 |

## 冲突裁决

- 配置只读 `.devagentstudio/application.json`；Python 路径统一使用 `app.branding.WORKSPACE_ARTIFACT_DIR`，不保留旧路径别名。
- 技术规划、正式修订、生命周期、DAG、资源锁、任务结束及恢复均在 1028 实现上补 Runtime 接入点，不复制旧共享文件覆盖。
- 内置业务数据库复用 `managed_database_runtime.py` 的结构快照和一次取密。现有注入只覆盖 Java；Python 的连接配置、驱动和 TLS 必须与模板同步后才能标记完成。
- 表归属、物理表名、创建／重建确认、结构检查、操作记录、版本 SQL 交付以当前内置数据库合同为准。Runtime 启动禁止 DDL，不增加旧 Direct SQL／API 正式产物。
- Runtime checkpoint／memory 与业务表分开。业务接入内置 MySQL 不代表 checkpoint 已更换存储，也不承诺搬迁历史调试数据。
- 模板保持当前 Generate／配置更新事务与真实 TemplateState，不恢复旧分支 Git 克隆补齐模板、手造 State 或绕过 readiness。
- 服务抽屉在新版上增加 Runtime 卡片、日志与调试入口；保留数据库检查、日志搜索／复制／展开和当前维护门禁。没有 Java 服务时明确跳过。
- 模型配置沿当前项目配置传入 Runtime；不把服务商余额／权限失败当作数据库异常，不通过改模型或重复请求掩盖错误。

## 验证与恢复工作

## 显式选择入口

技术规划生成提交后，原 `/application-page-planning/run` AG-UI 流先停在 `technical_plan_topology_selection`，前端不得预选 Java。`select_topology` 沿既有 gateId/artifactRevision 校验选择，确定性写入当前 TechnicalPlan，随后回到正常技术规划确认；选择本身不推进模板生命周期。

当前 Java 业务应用可用；Agent Runtime 选项展示为不可用。Python Endpoint 与七模块 Agent DAG 的代码接入已完成；Python/Agent 语义验收、数据库运行注入、前端调用及启动调试仍待接通。不能因为模板消费端和代码生成已写入就启用完整 Runtime。此入口解决“无选择而静默进入 Java”的缺口，不代表 Runtime 功能搬运完成。后续应先完成这些运行能力，再开放该选项，不得只修改 available 标记。

## 当前 Python 业务生成批次

- 保留 1028 的逻辑 `owner=backend`、`backend:endpoint:<contract>:<endpoint>` 和固定 objects/repository/upstream/service/controller 职责，不搬入旧版按 Entity/SQL 拆任务的规则。这是相对于原搬运设想的明确调整：业务 owner 不代表实现语言，正式拓扑决定实现根及代码执行器。
- `runtime_business_contract.py` 从显式服务图编译冻结实现合同，规划器根据该合同使用 Python 规则，Local Validator 保持原身份和任务数量，仅替换允许的工程根。没有显式 Runtime 服务图的 Java 任务继续原路径。
- `agents/agent_runtime/` 逐任务创建文件执行器，确认任务的精确 `target_files/change_scope/allowed_paths` 必须一致，不能写 Java、SQL、环境文件、模型、身份或 checkpoint 基础设施。权限 backend 在实际写入前再次检查精确文件和所有路径段的符号链接。
- Endpoint 执行包复用新版正式 API 合同和 Endpoint Design 裁剪逻辑，按当前 Pydantic 模型校验确认产物，使用同一字段取值语义的 Python 投射；不恢复 Direct API/SQL 正式产物，不调用 Java Skill，不把物理表名再次推导。
- Build 继续原来的真实 Diff、逐任务报告、工具活动及取消登记。新增结果使用当前严格协议，不恢复旧 implementation_action；失败停止该批后续 Python 调用。
- 骨架摘要包含 topology，任务来源绑定平台编译的实现合同。切换实现根时旧任务不能继续声明可复用。
- 公共文件工具只针对平台提供的精确 Runtime Python 清单放行 `.py` 创建；不解除其他 Agent 的临时脚本禁写。
- 按用户要求不运行测试、构建、语法检查、health 或 Electron 验证；以上为代码接入状态，不是运行验收结论。

## 七模块 Agent DAG 批次

代码已接入当前 Unit Worker、Frozen Store/Catalog、Local/Global Validation、累计 Scope Assembly、PendingPlan 和 Build owner 分派。平台固定模块/职责/身份/依赖，模型只选择策略内精确文件和实现步骤；不搬入旧 Graph、旧按 Entity/SQL 拆任务的规则或旧 implementation_action 结果协议。对照、当前 Agent 合同和文件归属见 [七模块 DAG 设计](agent-runtime-direct/AGENT_DAG_1028.md)。本批按要求未运行任何测试、构建、health 或语法检查。

后续顺序：Python 业务/Agent 验收 → MySQL 驱动/TLS 与一次取密注入 → Runtime 启停、模型注入、三服务及调试入口及前端 Agent 调用 → 开放选择。模板端仍由用户同步。

## Python 工程检查与测试生成批次

- 集成检查只从当前磁盘已确认 TechnicalPlan 判定 Runtime，早于“无实体/API”及 Java/Maven 分支。Runtime 即使没有业务表也必须检查工程，不能用缺少 Maven 显示跳过后端。
- 复用现有受控命令执行、取消、质量门、命令日志及失败证据：依次执行 `uv sync --frozen`、项目解释器 AST 源码解析及 `pytest`。命令不继承宿主 Python、平台模型或数据库凭据，不加载环境文件；首个阻塞失败后的步骤标为未执行，不自动重试。源码解析只证明语法有效，不宣称应用可导入或业务正确。
- TestGeneration 复用当前生成确认、映射缓存及失败处理，识别 `agent-runtime/src/app/**/*.py`，仅开放平铺的 `agent-runtime/tests/test_*.py`；不允许生成依赖文件、生产代码或任意辅助脚本。Python 结构检查要求真实用例和断言，语法错误及行号进入已有诊断；真正可收集/执行仍以 pytest 退出码为准。
- 单测报告绑定 Runtime 依赖合同摘要，启动缓存使用 Runtime 源码/测试/依赖摘要；不复用 Java 指纹。测试工程失败保留逻辑 backend owner，修复线索指向 Runtime，而非 Java pom.xml。
- **尚未接通启动**：若请求启动检查，当前明确返回所缺适配并阻断；不运行缺少数据库和模型注入的临时进程，不伪造启动通过。业务/Agent 语义验收仍待接入。
- 后续按依赖顺序完成 MySQL 驱动/TLS 与一次取密注入、Runtime 启停和模型注入、三服务日志与调试入口，再进行用户授权的一次验证。模板同步仍由用户负责。
- 本批未运行测试、构建、Python 语法检查、Backend health 或 Electron 验证。

## 数据库、模型与启动消费端批次

当前 Studio 已接入同一 Launcher 的 Runtime 分支，共用 1028 维护入口、工作区锁、取消/退出/孤儿收口及数据库 SaaS 结构检查和一次取密。Java 未声明则不启动；没有模型或数据库凭据、uv 缺失、TemplateState 不匹配、依赖同步/健康/代理失败均明确返回，不做自动换模型或补模板。

服务抽屉拆出三类卡片，Runtime 独立日志及独立 Electron `/docs` 接口调试入口；前端同源代理需匹配当前 launchId。临时 Python 启动验收使用 probe 进程，结束回收，不替换已有预览。以上为代码接入，未宣称真实运行通过。

模板需同步的环境键、PyMySQL/TLS、健康响应和代理合同见 [启动合同](agent-runtime-direct/RUNTIME_LAUNCH_CONTRACT_1028.md)。此批取代前文“启动适配尚未接入”的占位状态；仍待业务语义验收、独立 Debug Profile/凭据、模板同步和真实会话联调。用户要求不运行验证，本批没有执行测试、构建、health、Python 语法或 Electron 校验。

## 静态业务验收批次

Runtime 在同一 Build 中编译并执行 Python/Agent 专项检查，不套用 Java AST、不随 Java 可选自检关闭、不以空检查通过。保留当前正式合同、只读依赖范围、失效规则、结果证据和 blocked/failed 分类；不新建工作流或产品 API。

字段/DTO、同一 handler 路由与服务返回链、受限参数化 SQL/查询树、正式响应映射、七模块实际工厂装配已有专项检查。复杂动态注册、ORM、自然语言规则、写 Tool 确认等不能静态证明的情况明确阻断，详细边界与文件归属见 [业务验收合同](agent-runtime-direct/BUSINESS_ACCEPTANCE_1028.md)。这不是任意 Python 业务的完整语义证明，也不替代模板联调/真实会话测试。本批未运行任何校验。

## 验证规则

本轮仅按上述顺序修改授权范围。每项验证执行一次，失败即停，保留已写入变更并报告原始错误，后续由用户决定。不得因仅基础类型可导入或进程健康就把阶段 2—5 标记为完成。

本轮起始工作区有 Frontend/package-lock.json 修改及 .workbuddy/、docs/img*.png 未跟踪文件；均不属于本次搬运范围，不覆盖、不暂存。

第二批生产／消费端清单和路径策略见 [模板接入清单](agent-runtime-direct/AGENT_RUNTIME_TEMPLATE_ENGINE_1028.md)。用户明确要求沿 Template Engine 合同完成 Studio 代码，目标 Runtime 模板由用户随后同步。当前 Python 登录／授权配置面未交付，Runtime 仅接受已关闭这两项开关的应用；这是显式能力拒绝，不会修改应用开关。

