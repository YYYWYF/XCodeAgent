# Build 任务单元语义化文案

Build DAG 内部继续使用稳定的 `unit_id` 参与校验、依赖编译和调度；确认 UI 不直接展示 `Unit` 或原始 ID，而是展示下面的用户可理解文案。

| `unit_id` 规则 | UI 文案 | 说明 |
| --- | --- | --- |
| `page:<pageId>` | 前端页面 | 某个具体页面的实现任务 |
| `backend:endpoint:<apiContractId>:<endpointId>` | 后端接口 | 某个接口契约的后端实现任务 |
| `frontend:data:<sourceId>` | 前端数据 | 前端数据源适配、Mock 或数据绑定能力 |
| `frontend:<name>` | 前端公共能力 | 前端 Shell、路由、鉴权、API Client 等共享能力 |
| `backend:<name>` | 后端公共能力 | 后端启动、公共基础设施等共享能力 |
| `database:<scope>` | 数据库 | 数据库结构、种子或校验范围 |
| `application:root` | 应用公共能力 | 应用级共享任务或未细分的默认任务 |
| 未知或缺省值 | 应用任务 | 保证异常数据仍有可读的兜底文案 |

## 展示规则

- 任务卡片头部右侧展示语义化单元文案，例如“前端页面”“后端接口”。
- 任务卡片底部不再重复展示 Unit；底部只保留修改范围和工程验收项。
- 任务 ID 仍然保留稳定的彩色标识；下游任务在头部展示彩色“前置 ID”标签。
- 原始 `unit_id` 仅作为内部协议字段保留，不参与用户确认文案。

## DAG 生成进度卡片

进度列表继续一条记录对应一个 Unit，`unit_id`、调度粒度、状态图标及任务统计口径均不变。`dag-generation.v1` 的 Unit 记录可选携带只读 `displayName`；这个字段只负责当前进度的标题，不参与身份、策略、校验、复用或重试。旧快照没有该字段时，前端显示可区分的中文类别兜底，不显示原始 ID。

页面名称来自当前 Run 使用的已确认 TechnicalPlan `pages[].name`，接口名称来自同一计划 `api_contracts[].endpoints[].name`。重复名称附加稳定中文序号；名称缺失或与对应 ID 相同则使用“未命名页面/接口”加序号，不从 ID、URL 或路由推测业务名。共享能力始终按一个 Unit 展示，不按 Endpoint 拆分。

名称在 `SequentialPlanningInputs.create_run()` 创建新 PlanningRun 时，从同一份冻结 TechnicalPlan 派生并筛选到本轮 `required_unit_ids`。内部 `PlanningRun/PlanningRunProjection.unit_display_names` 是显式必填、允许空映射的只读展示元数据，随 `.devagentstudio/plans/planning-run.json` 原子持久化；实时进度、最终结果和精确 Run/Thread 匹配的重连投影统一读取这份映射，不再从后来的磁盘计划重新命名 Run。字段缺失按当前合同拒绝读取，不提供旧格式迁移或兼容分支。失败或停止的同一 Run 可以保留自己的原名称。

Regenerate 仍先校验并消费旧 Pending，再重读正式输入，最后创建带新名称的新 Run。Native Recovery 创建的新 Run 从当前输入计算名称；恢复的 Candidate 不携带来源 Run 名称，也不补造 Attempt。若 Regenerate 已提交新 Pending，服务直接读取对应 Run 的持久名称，不重复生成。该映射不额外参与输入指纹、DraftIdentity、候选复用或 Pending/Formal 内容；正式计划字段原有的指纹规则保持不变。

固定公共能力名称如下：

| Unit | 展示名称 |
| --- | --- |
| `frontend:api-client` | 前端接口调用封装 |
| `frontend:data:static` | 前端静态数据处理 |
| `frontend:auth-guard` | 页面访问权限配置 |
| `frontend:shell` | 前端应用环境 |
| `backend:bootstrap` | 后端基础环境配置 |
| `application:root` | 应用任务结构 |
| `app:integration` | 应用集成节点 |

状态和策略是两个独立字段：状态说明进展；策略说明处理方式或无需生成的原因。`not_required` 始终显示“无需生成”，策略按参与方式说明“无新增任务需求”“前置条件已满足”“复用已有任务”“沿用已有能力”或“已有职责已满足”。`reuse_and_generate` 优先显示“部分复用 · 部分新增”；`model` 和 `deterministic` 分别显示“AI 生成”和“系统生成”。

| 内部状态 | 用户文案 |
| --- | --- |
| `not_required` | 无需生成 |
| `pending` | 等待生成 |
| `generating` | 生成中 |
| `validating` | 校验中 |
| `candidate_ready` | 生成完成 |
| `round_exhausted` | 本轮尝试已用尽 |
| `aborted` | 已终止 |

尝试次数、复用/新增任务数、全局修复轮次、全局校验问题、阶段名、运行失败/取消均使用准确的中文表达。仅模型 Unit 且真实发生过尝试时展示次数；恢复的有效候选不补造模型尝试。进度快照 revision 仅用于拒绝过期快照，属于内部顺序标识，不向用户展示，也不表示 Regenerate 次数。

这些名称和文案是展示投影，不改变 DAG 任务生成数量或归属、Candidate 生命周期、Validator、Retry、Recovery、Regenerate、Pending/Formal authority 或 Unit 划分。

阶段文案优先采用当前 `phase`：准备、全局校验、汇总、最终校验、保存均显示对应阶段。`globalRepairRound > 0` 只作为修复轮次事实；“正在进行全局修复”仅用于生成阶段，不能覆盖后续汇总、校验或保存状态。
