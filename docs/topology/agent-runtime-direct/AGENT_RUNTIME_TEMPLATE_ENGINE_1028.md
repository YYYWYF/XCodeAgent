# Runtime 模板接入 1028：生产端与消费端对齐清单

本文件给出 Studio 本轮实现的消费合同和模板交付清单，不代表外部模板服务已经支持 Runtime。
当前配置、TemplateState、Generate 和 V4 更新事务继续以 1028 为准。

## 本轮 Studio 消费端改动

| 位置 | 当前合同 | Runtime 接入要求 |
| --- | --- | --- |
| workspace_bootstrap/requested_config.py | 从 canonical application.json 编译，绑定 sourceConfigRevision | 只在已确认 Direct 服务图下叠加固定 Python 实现 capability，不增加新的请求字段 |
| workspace_bootstrap/template_package.py | 保留原 ZIP 安全检查和唯一 V3 State | 由请求 capability 校验 frontend/agent-runtime 根，拒绝混入 backend 及 State 漂移 |
| workspace_bootstrap/materializer.py | 原字节清单校验、事务回滚 | Runtime 根纳入相同事务，不允许覆写任何已有受管源码根 |
| workspace_bootstrap/readiness.py | 当前 Java 入口检查保留 | Direct 增加实际 Python 入口、锁文件、七模块策略、路由描述和正式选择复核 |
| template_reconcile/protocol_v3.py | V3 字段和 schemaVersion 不变 | 只有显式请求并实际启用 python_application_runtime 时接受 agent-runtime Artifact 身份 |
| template_reconcile/protocol_v4.py | 保留现有能力配置面 | Runtime 仅支持无登录／无授权；尚未加入 Python 登录配置面，不借用 Java YAML |
| template_reconcile/service.py、application_config_orchestrator.py | 原 V4 操作、状态序列与恢复事务保留 | 共享同一 capability 编译器，主题／名称更新不丢弃 Runtime 实现能力 |
| workspace_bootstrap/template_engine_client.py | generate 实际走配置的 Engine /v1/generate | 不重新接通未调用的旧 Git fallback，不手造 TemplateState |

## 本批已迁入的独立模块

`Backend/app/services/agent_runtime_template_policy.py` 来自旧分支七模块策略，控制 prompt、model、memory、tools、skills、knowledge、context 的读取、修改、新增及测试路径，返回隔离副本与稳定策略摘要。

搬运同时收紧了实际文件系统边界：要求入口是文件、测试和新增根是目录，拒绝最终文件和任何中间目录的符号链接，包括可选的 skills/knowledge 新增根。

当前策略描述迁入的七模块确切路径，目标模板必须提供真实实现，不能靠补空文件满足策略。尚未完成真实 Engine／模板交付验收，不注册拓扑、不启用生成器。

## 模板交付清单

Generate 继续 POST `/v1/generate`，外层只有现有 `requestedConfig`。Runtime 的实现选择由其中 `capabilities` 表达，以下键均为 `{"enabled":true,"config":{}}`：

```text
python_application_runtime
python_business_api
python_domain_model
python_repository
agent_runtime_public_edge
agent_runtime_principal_ownership
agent_runtime_local_debug
agent_runtime_anonymous_session
python_business_mysql
```

`applicationConfig` 继续只在首次 Generate 提供；应用配置不增加拓扑开关。上述能力要求来自已确认 TechnicalPlan 的 `type=agent_runtime_direct`、`serviceIds=[agent-runtime]`、`publicEdgeServiceId=agent-runtime`、`authenticationTermination=anonymous-session`，不能通过开关替代技术规划确认。

当前 Python 登录／授权配置面尚未交付。`auth.enable=true` 或 `authorization.enabled=true` 会明确拒绝 Runtime 请求，不改写或关闭应用配置；普通 Java 应用继续现有合同。

ZIP 顶层只允许 `frontend/`、`agent-runtime/` 及 `.devagentstudio` 的受管模板文件，不能混入 `backend/`。保留 `.devagentstudio/template-state.json`，`schemaVersion=3`，requested 与请求完全一致，effective 至少包含上述能力；不允许 Java managedContributions。Runtime 扩展文件的 Artifact 身份使用 `agent-runtime:<capabilityId>:<relativePath>`，其 target 为 `agent-runtime/<relativePath>`，capability 必须属于 effective。

Runtime 至少提供：

```text
agent-runtime/pyproject.toml
agent-runtime/uv.lock
agent-runtime/src/<project.scripts.agent-runtime 对应模块>.py
agent-runtime/src/app/agent/factory.py
agent-runtime/src/app/agent/context.py
agent-runtime/src/app/models/factory.py
agent-runtime/src/app/settings.py
agent-runtime/src/app/persistence/checkpointer.py
agent-runtime/src/app/tools/__init__.py
agent-runtime/src/app/interaction/schemas.py
agent-runtime/src/app/interaction/service.py
agent-runtime/tests/
.devagentstudio/template-contracts/route-projector.json
```

`pyproject.toml` 中 `project.scripts.agent-runtime` 必须是可解析的 `module:function`，对应真实 src 模块（也支持 package 的 `__init__.py`）；路径不能是文件或中间目录链接。路由描述必须是非空 JSON 对象，实际投影仍沿当前 Route Projector 合同完成检查。

`python_business_mysql` 表示模板需要提供 MySQL 业务 Repository／事务和依赖，不能只声明 capability。Studio 已补入启动环境消费端，唯一的 PyMySQL/TLS、模型环境键、健康响应和前端代理合同见 [Runtime 启动合同](RUNTIME_LAUNCH_CONTRACT_1028.md)，模板需同步该合同。业务表创建和重建始终由现有 SaaS 链路执行，模板启动不得自动建表。此处不声明真实 MySQL 连接或模型调用已验证。

V4 继续现有 `/v1/update`、`protocolVersion=4`、State 序列和 Operation 合同，不另建配置协议。实现能力不得因改主题／名称而丢失，Runtime 更新的操作目标不得跨到 backend 或平台私有目录。Python 登录配置尚不支持，不新增可绕过既有配置所有权规则的 dotenv 写入动作。

## 对齐后继续的顺序

1. 用户在实际 Engine／Runtime 模板端同步上述 capability、Artifact、真实模块及路由描述。
2. 用真实交付包验证 Studio 的请求、State、根隔离和 readiness；不以 fixture 验证替代真实模板验收。
3. 完成 Runtime 登录／授权配置面之前保持该场景明确不可用，不借用 Java 配置。
4. 对齐 Python 业务 MySQL 连接、TLS、驱动与模型配置注入键；不把业务存储接入等同于 checkpoint 更换存储。
5. 在模板交付及配置合同就绪后接入 Python 生成器、DAG、启动和调试界面。

本轮依据用户指示只修改 Studio 消费端，模板端由用户后续同步；不调用真实服务试探支持版本，不克隆旧模板兜底。
