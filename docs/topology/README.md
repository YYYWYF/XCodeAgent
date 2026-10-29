# 生成应用拓扑设计目录

本目录从 `dev_agent_integration_0922_back@aa9e3375` 搬入，目标基线为 `dev_agent_integration_1028@43b1f11d`。与新版冲突时采用新版当前合同；基础类型迁入不等于功能可用。

- [搬运顺序与状态](AGENT_RUNTIME_PORT_1028.md)：本次实施入口、裁决和未完成事项。
- [统一拓扑架构](APPLICATION_TOPOLOGY_DESIGN.md)：显式选择、正式事实和阶段边界。
- [拓扑实现模板](TOPOLOGY_IMPLEMENTATION_TEMPLATE.md)：Definition 与交付顺序。
- [重构决策记录](TOPOLOGY_REFACTOR_REVIEW.md)：保留和舍弃的旧设计。
- [Runtime Direct 整体设计](agent-runtime-direct/AGENT_RUNTIME_DIRECT_DESIGN.md)。
- [正式合同](agent-runtime-direct/AGENT_RUNTIME_DIRECT_CONTRACTS.md)。
- [构建与运行](agent-runtime-direct/AGENT_RUNTIME_DIRECT_BUILD_RUNTIME.md)。
- [1028 模板生产／消费端接入清单](agent-runtime-direct/AGENT_RUNTIME_TEMPLATE_ENGINE_1028.md)。
- [安全与调试](agent-runtime-direct/AGENT_RUNTIME_DIRECT_SECURITY_DEBUG.md)。

本次只搬运 Agent Runtime Direct 设计，不将 Gateway 或独立 Java 拓扑标记为已实现。内置数据库以 [现行整体方案](../BUILTIN_DATASOURCE_OVERALL_REVIEW.md) 为准；Runtime 接入复用其注册、建表、检查和取密合同。

## 1028 实施补充：数据库、模板与传输

- Studio 新增或实质修改的产品动作采用 AG-UI 完整生命周期，Renderer 使用官方客户端。生成应用的新增产品动作也遵守仓库 AG-UI 规则；既有 Endpoint 的方法、路径和正式 Schema 不得在搬运中被暗改。
- 业务表使用平台内置 MySQL、当前完整物理表名和已有实体表操作；连接复用 prepare_managed_startup／resolve_managed_runtime。只向 Python 子进程注入所需连接，不传 SaaS 服务密钥，连接错误和日志统一脱敏。
- Runtime checkpoint／短期记忆与业务数据库分开；是否更换其存储需独立模板能力与验收，本次不把业务 MySQL 接入等同于已替换 checkpoint SQLite。
- Template Engine Generate 的当前包校验及配置更新事务保持 1028 合同。需要新增 Runtime 根目录与 capability 时同步生产者、消费者和测试，不使用旧 Git 克隆兜底或手造 TemplateState。
- 保留新版实体创建／重建／结构检查／操作记录、版本 SQL 交付、字段映射、配置事务、资源锁、任务结束及恢复语义，不恢复旧 Direct 独立 SQL／API 确认链。

