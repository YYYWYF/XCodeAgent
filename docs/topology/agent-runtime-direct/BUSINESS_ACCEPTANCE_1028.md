# Runtime 业务验收合同

本批接入只读、确定性的业务验收，不执行目标源码、SQL、HTTP 或模型调用。复用现有 Build 编译、正式来源校验、结果摘要、失败卡和修复分类；不增加产品 API、第二套 DAG 或模型自报通过。

## 编译和执行

`runtime_business_acceptance.py` 仅根据当前显式 Runtime 服务图，将原 backend 交付职责映射为 Python 检查，并为每个 `agent.runtime` 交付物生成模块检查。交付物 owner、Unit、任务 ID 和依赖保持当前合同。Java/前端继续原编译器，Runtime 不调用 Java AST。

| 检查 | 实际核对的内容 |
| --- | --- |
| python.objects_contract | 每个正式实体独立的类型定义，字段、必填、类型、枚举；不聚合多个类凑齐字段 |
| python.endpoint_contract | 同一 FastAPI handler 的 method/path/prefix、Path/Query/Header、Body DTO、response_model、状态码、可信上下文及服务返回链 |
| python.repository_contract | 真实 DB-API Cursor 的 execute、正式物理表/CRUD、占位符与绑定参数、写入列和值来源、可信 Principal 归属、受支持的 WHERE 逻辑树 |
| python.application_service_contract | 当前唯一服务操作、Repository 委托及上下文、事务范围、实际返回字段的固定值/请求引用/数据库或上游来源 |
| python.upstream_contract | 当前 HTTP Operation 的 method/path、参数、同一响应的状态处理与返回链；复杂连接和转换需专用证据 |
| python.bootstrap_contract | 当前依赖声明和初始化禁止项；不将静态声明当作连接/TLS已验证，真实资源由后续启动检查验收 |
| agent.module_contract | 当前 Agent/模块对应的实际 DeepAgents 工厂及正式配置，不把配置副本或未使用变量当成装配 |

七模块检查覆盖：system_prompt 实参、项目模型工厂、持久化 checkpointer 和隔离键、实际 Tool 列表与正式 Endpoint 的相同服务方法、skills/knowledge 开关及资源装配、可信 RuntimeContext 创建与图调用。Memory 的 namespace/Principal/thread 必须进入实际 checkpoint key。普通业务和 Agent Tool 共享正式 ApplicationService；客户端自报 owner/principal 不能作为可信身份。

Runtime 检查在 Build 中实际执行，不受 Java 可选 DAG 自检开关关闭；Java 原策略不变。缺少平台编译检查的 Runtime 任务明确阻断，不通过空检查放行。修复继承父任务正式业务标准，不能降低验收预期。

## 来源与只读范围

- 原 API Contract/Endpoint Design 的切片哈希继续验证；另绑定当前服务图、正式实体和 Agent 模块事实。
- `agent_module_facts` 与规划来源共用；共享工厂的 Agent 集合和 knowledge 所消费的 Tool 配置纳入相应摘要，相关变化不能复用旧任务。
- 只读取当前声明交付物、当前 DAG 显式依赖交付文件、七模块固定模板只读入口；不沿目录扫描，也不沿任意 import 读取整个项目。
- 每项最多沿用已有上限 40 个文件、每文件 512 KB。所有路径段拒绝符号链接、隐藏目录、通配、穿越和工作区外文件；测试文件不作为生产实现证据。
- 注释、文档字符串、未调用的嵌套函数和静态不可达分支不提供业务证据。动态导入、反射、无法唯一绑定的类/方法不会按名字猜测。

## 判定和用户可见错误

- `passed`：当前静态检查范围内，实际 AST 满足正式合同；不等于端到端业务或模型回答已经验证。
- `failed`：可定位的实现不符，如字段缺失、路由错配、固定值错误、参数绑定不符或额外工具。沿既有修复分类处理。
- `blocked`：正式来源缺失/变化、依赖不足、超出边界或当前语法/语义不能证明。沿既有 business_acceptance_blocked 终止当前 DAG，不派 Agent 猜测验收器规则。

结果保留具体合同身份、文件/行号、观察项及 reason_code。Build failure_reason 同时带出具体失败证据，不仅显示通过/失败计数。

## 明确的静态边界

本批支持显式导入/类型注解、固定工厂与参数、常量 prefix、受限 DB-API SQL 和可追踪的直接返回赋值。以下情况会按具体节点明确 blocked，而不是伪造通过：

- 多 Agent 动态工厂分派、`**kwargs`、动态 alias、无法在声明依赖内找到的模型或服务。
- 多分支 handler、动态转换、集合映射、自然语言处理规则及整体 implementationDescription，尚无可消费的行为证据适配。
- ORM、多表/聚合 SQL、动态 SQL，以及受限 WHERE 解析器之外的运算；当前 WHERE 支持参数化标量比较、IS NULL/NOT NULL 和有界 AND/OR/括号。
- 上游动态 URL/Base URL 构造、复杂响应转换；只出现相同字符串不能证明已落实。
- 写 Tool 的显式确认授权控制流；只有 interrupt 字样不算确认成功证据。
- 启用的 skills/knowledge 动态资源装配、未能证明无残留检索的禁用知识模块，及未注册的模块扩展参数。

遇到这些阻断，应按具体模板实现补受限适配或正式行为证据消费，不扩大文件读取、不跳过安全/业务规则。模板真实实现尚未在此批运行验证，不能保证任意模板写法都会通过。

按用户要求，本批未执行测试、构建、health、Python 语法/import 检查或 Electron 验证。
