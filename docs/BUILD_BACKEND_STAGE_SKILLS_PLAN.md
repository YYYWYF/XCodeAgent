# Build 后端任务分阶段提示词与 Skill 改造清单

## 一、目标与明确约束

将 datasourceAgent 的执行规则组织为：

```text
公共执行规则
  └─ 当前任务阶段
       ├─ 阶段提示词
       └─ 阶段 Skill
            ├─ 数据库参考
            ├─ 外部 API 参考
            └─ 必要的组合编排参考
```

- [ ] 支持 Bootstrap、Foundation、Business 的数据库、外部 API、数据库＋外部 API 三种组合。
- [ ] Controller 使用统一的 HTTP 适配规范，不因底层来源不同而加载持久层、上游调用实现规范。
- [ ] Build 的 datasourceAgent **完全排除 `springboot-template-modification-boundary`**：不出现在技能发现目录、必读路径和执行参考中，也不能通过原路径读取。
- [ ] 该模板边界 skill 保留在仓库及全局技能目录中，规划阶段读取其目录规范的行为保持不变。
- [ ] 复用现有 datasourceAgent、任务阶段、实现契约和 AG-UI 工作流，不增加 Agent 类型或产品接口。
- [ ] 保留正式计划确认、依赖调度、任务写入范围、重试和任务修复机制。

## 二、Skill 结构与内容清单

在 `Backend/app/builtin_skills/` 下建立四个阶段 skill：

```text
springboot-backend-bootstrap/
├─ SKILL.md
└─ references/
   ├─ bootstrap-database.md
   └─ bootstrap-external-api.md

springboot-backend-foundation/
├─ SKILL.md
└─ references/
   ├─ foundation-database.md
   └─ foundation-external-api.md

springboot-backend-business/
├─ SKILL.md
└─ references/
   ├─ business-database.md
   ├─ business-external-api.md
   ├─ business-source-orchestration.md
   └─ business-error-handling.md

springboot-backend-controller/
├─ SKILL.md
└─ references/
   └─ controller-resource-authorization.md
```

`SKILL.md` 保留标准入口名；参考文件名称包含阶段与用途，避免同名文件混淆。

**阶段入口的统一结构**

- [ ] 每份入口包含：适用阶段、权威输入、职责与交付物、执行顺序、禁止跨阶段行为、参考文件路由、完成条件。
- [ ] 阶段共用规则放在入口，来源实现细节放在 references。
- [ ] 不在各参考中重复完整执行协议、结果 JSON 或其他阶段的实现步骤。
- [ ] 延续现有后端生成 skill 的英文正文约定；新增或修改 Python 函数使用中文说明注释。

**各阶段的内容归属**

| 阶段 | 阶段公共职责 | 数据库参考 | 外部 API 参考 |
|---|---|---|---|
| Bootstrap | 读取实际工程，复用已有能力，只补齐获批的公共依赖与启用项 | MySQL、MyBatis-Plus、运行时连接注入 | OpenFeign 依赖、Boot/Cloud 基线、全局启用 |
| Foundation | 准备模块骨架，保留已有成员，不提前实现业务方法 | PO、Mapper、XML 占位、空 Repository 接口与注入骨架 | Client 骨架、确认的地址键、连接和局部配置 |
| Business | 实现一个 Endpoint，补齐 Service、DTO、必要转换并复用共享方法 | 确认的数据库操作、查询条件、分页、映射、写入保护和事务 | 完整 Operation 签名、传输 DTO、调用参数、响应映射与错误适配 |
| Controller | 适配范围内 Endpoint 的 HTTP 合同 | 不加载 | 不加载 |

- [ ] 将现有字段取值、空值处理、查询条件和业务异常规范放入适用的 Business 内容中，保留当前语义。
- [ ] 混合 Business 同时加载两种来源参考及组合编排参考；其他 Business 不加载组合编排参考。
- [ ] 组合编排只要求遵循确认合同中的顺序、数据依赖、事务及失败语义；禁止自行设计补偿、分布式事务或重试策略。
- [ ] Controller 规范包含请求绑定、校验、响应包装、已有 Service 调用及逐 Endpoint 权限约束。
- [ ] 从旧生成 skill 迁入仍有效的实现规范；不复制模板边界 skill 全文，不通过新 skill 反向引用它。
- [ ] 新 skill 中的目录、DTO、Repository 等必要示例直接归入对应阶段参考；实际路径仍以批准任务和真实工程为准。
- [ ] 四个阶段 skill 接管后，删除旧 `springboot-backend-generate` 资源和注册项，同步更新当前引用，不保留别名或兼容路由。

## 三、执行路由、提示词与技能可见性

**任务类型解析**

- [ ] 在 datasourceAgent 内新增独立的执行配置模块，统一解析阶段、来源集合、阶段提示词和必读技能路径。
- [ ] Bootstrap 通过稳定 `unit_id` 判断；其他阶段读取 `source_refs.backend_stage`。
- [ ] 数据源集合读取任务携带的已确认 Endpoint 设计快照，不从任务标题、自由文本或模型输出推断。
- [ ] Bootstrap 使用本轮任务声明的来源集合；Foundation 使用当前模块开发范围的集合；Business 使用当前单个 Endpoint 的集合。
- [ ] 非法阶段、非法来源继续在模型调用前失败，不降级成通用阶段。
- [ ] Controller 仍校验来源合同，但其必读参考不按来源增加实现细节。
- [ ] 空来源不再导致阶段 skill 整体不加载；来源合法性和现有契约完整性校验分别处理，不为缺失合同补造默认来源。

**加载矩阵**

| 任务 | 必读内容 |
|---|---|
| Bootstrap＋数据库 | Bootstrap 入口＋数据库 Bootstrap 参考 |
| Bootstrap＋外部 API | Bootstrap 入口＋外部 API Bootstrap 参考 |
| Bootstrap＋混合 | Bootstrap 入口＋上述两份参考 |
| Foundation＋数据库 | Foundation 入口＋数据库 Foundation 参考 |
| Foundation＋外部 API | Foundation 入口＋外部 API Foundation 参考 |
| Foundation＋混合 | Foundation 入口＋上述两份参考 |
| Business＋数据库 | Business 入口＋数据库业务参考＋业务异常参考 |
| Business＋外部 API | Business 入口＋外部 API 业务参考＋业务异常参考 |
| Business＋混合 | Business 入口＋两份来源参考＋组合编排参考＋业务异常参考 |
| Controller | Controller 入口；声明资源点时额外读取资源权限参考 |

- [ ] 顺序固定为阶段入口、数据库参考、外部 API 参考、组合编排参考、阶段公共参考。
- [ ] 同次调用中的重复路径按首次出现顺序去重。

**提示词职责**

- [ ] 公共 system prompt 保留角色、虚拟路径、读取真实文件、写入范围、正式契约保护和通用执行限制。
- [ ] 当前阶段提示词简洁说明本次职责及权威输入，不再拼接其他阶段的完整规则。
- [ ] Skill 承载具体工程规范，避免提示词和 Skill 双份维护长篇实现要求。
- [ ] 字段映射执行规则仅用于 Business；Controller 权限规则仅用于 Controller；配置规则仅用于相应 Bootstrap 或 Foundation。
- [ ] 保留任务包的 `stage`、`execution_steps`、`instruction_paths` 和 `implementation_contract`，不新增正式计划字段。
- [ ] 保留 WorkspaceSnapshot 导航上下文、`already_satisfied` 语义、结构化失败与真实 Diff 归属。
- [ ] 长提示词从 generator 中提取到独立模块，generator 聚焦调用和结果协调；避免继续扩大已有混合职责文件。

**完全排除模板边界 Skill**

- [ ] 从 datasourceAgent 的必读 skill 和 references 选择逻辑中移除该 skill。
- [ ] 给内置技能挂载增加可选的精确白名单；其他 Agent 不传白名单时继续使用现有全目录行为。
- [ ] datasourceAgent 仅挂载本次任务需要的阶段 skill，用户 skill 和 AGENTS 记忆沿原有入口加载。
- [ ] 使用空默认后端与选中 skill 的独立只读子挂载构造受限技能目录，不再挂载整个物理内置技能根目录。
- [ ] 校验目录枚举、直接读取、搜索和批量下载均不能触达未挂载的模板边界 skill，技能目录保持只读。
- [ ] 白名单进入 Agent bundle 缓存键，并且只作用于 datasourceAgent；不修改缓存实例的共享状态。
- [ ] 调度仍按现有单任务执行。若内部调用一次传入多个任务，挂载这些任务所需阶段 skill 的稳定并集，各任务只适用自己的必读规则。
- [ ] 用户显式选择 skill 的白名单、快照和冲突规则保持现状；不新增按名称过滤用户自定义 skill 的策略。
- [ ] 保留现有工具能力，不将数据库配置工具的进一步裁剪混入本次改造。
- [ ] 执行记录中的 `requiredSkillsLoaded` 更新为新阶段技能路径；本次不扩展为“已实际读取”的审计协议。

## 四、资源注册、文档与边界

- [ ] 更新内置技能常量、必需资源清单、启动完整性校验和全局技能目录展示。
- [ ] 全局目录显示四个新阶段 skill，继续显示模板边界 skill；旧统一生成 skill 不再出现。
- [ ] 检查冻结包资源完整性。现有打包脚本已收集整个内置技能目录，复用其机制，不新增另一套资源复制流程。
- [ ] 更新 `docs/CODEBASE_INDEX.md`，记录阶段路由、来源组合、受限挂载及规划阶段继续使用模板目录规范的边界。
- [ ] 同步修改受影响的当前测试和文档引用；不读取或恢复历史格式。
- [ ] 不改 AG-UI 端点、事件名称、前端请求、正式任务计划或持久化格式。
- [ ] 保留工作区已有日志相关改动，在 generator 和索引文件中仅做针对性合并。
