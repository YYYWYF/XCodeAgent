# Java 与 Runtime 模板仓库整合方案

## 结论与本次范围

将 `/Users/frank/agent-runtime-template` 中的 Python 模板迁入 `/Users/frank/IdeaProjects/springboot-template`，由后者的 Template Engine 统一发布。共享前端，根据已确认的应用架构选择 Java 或 Python 后端，不把 Python 服务塞进 Java 的 `backend/`。

本方案已在两个本地模板仓库实施。尚未运行真实生成包、更新包或启动验收；实现状态不能替代联调通过的结论。

## 当前落地状态

- `springboot-template` 已加入 Runtime Source、能力解析、互斥后端物化、Artifact/State schema 与增量更新处理；Service 沿原 Generate/Update 接口，模板 revision 为 `2026.10.28.1`。
- `agent-runtime-template` 已整理成 Base + Extension 维护快照；统一仓库作为后续维护和发布的权威来源，不恢复独立 Git 拉取。
- 首个实际文件扩展是 `python_business_mysql`，其 `files/src/app/infrastructure/` 独占数据库设置和事务适配器；主 Runtime 能力依赖它，其他能力依赖主能力。其余基础能力先由真实 Base 实现支撑，扩展配置仅接受 `{}`，不开放未实现的可选开关。
- 已实现当前模型环境、MySQL/TLS 连接、namespace checkpoint、匿名 Cookie 重建和实际健康检查；未提供企业登录/授权和独立 Debug Profile。
- 共享 Rsbuild 前端已增加 Runtime 同源代理与官方 AG-UI 依赖；Python 和前端依赖锁文件均已同步。
- Studio 静态验收已适配显式注册表、AG-UI 转发参数、冻结 Pydantic 上下文和当前线程键；仍不执行目标工程或据此声称动态行为通过。

后续联调需要运行新版本 Template Engine 和 Studio 后端，再通过已确认的 Runtime 架构请求生成新工程。旧生成工程不在本批变更范围内，不增加历史包迁移或模板拉取兜底。

## 目录与交付布局

建议在现有源目录旁增加第三类源码：

```text
template-source/code/
  contracts/                  # 保留当前 Route Projector 合同
  frontend/base/              # 共享 React / Rsbuild 前端
  frontend/extensions/
  backend/base/               # 原 Java 后端
  backend/extensions/
  agent-runtime/base/         # Python 固定平台骨架
    pyproject.toml
    uv.lock
    src/app/
    tests/
    README.md
  agent-runtime/extensions/   # 按能力分配唯一文件所有权
  template-revision.txt
```

Runtime 平台固定骨架放 Base；确有可选实现的部分放 Extension。九项 capability 必须由 Engine 的源码声明和依赖图表达，并对应实际实现，不能只复制旧 `template-capabilities.json` 或补空目录。每个文件只能有一个来源，禁止通过后加载覆盖解决所有权冲突。

Java 包仍输出 `frontend/ + backend/ + .devagentstudio/`；Runtime 包输出 `frontend/ + agent-runtime/ + .devagentstudio/`。Runtime 包不能夹带 Java 后端，也不能丢失路由合同和 TemplateState。

迁入完成后，统一仓库是模板源码的唯一维护位置。旧仓库仅作为搬运参考，不建立运行时外部路径依赖、符号链接或第二套 Git 拉取兜底。不迁入 `.venv`、数据库文件、`.env`、密钥、缓存和 IDE 文件。

## 已确认的断点

| 环节 | 当前实际情况 | 所需修改 |
| --- | --- | --- |
| Engine 源码加载 | `CodeTemplateRelease` / `CodeTemplateReleaseLoader` 只加载 frontend 与 backend | 增加 Runtime 源码和声明解析，保留唯一所有权、依赖及路径安全检查 |
| 能力解析 | 未注册的 Runtime capability 会被 `ExtensionResolver` 拒绝 | 注册当前九项能力及依赖闭包，明确拒绝当前不支持的 Runtime 登录/授权组合 |
| 工程生成 | `V3ProjectMaterializer` 固定写入 backend 并投射 Java 配置 | 依据显式实现能力选择后端根，Runtime 不生成 Java 注解、POM 和后端注册内容 |
| State | V3 schema 的 installedArtifacts 前缀只接受 frontend/backend | 加入 agent-runtime；State、实际字节清单与路径必须一致 |
| 增量更新 | `V3UpdatePlanner` 的 Artifact 路径和字节读取仍按两类工程处理 | 支持 Runtime Artifact，改名/主题更新保留 Runtime 能力和布局，拒绝跨实现根切换 |
| 前端代理 | 共享前端使用 Rsbuild，`/api` 固定代理到 8080 | Runtime 按 `AGENT_RUNTIME_PUBLIC_URL` 配置同源代理，Java 保留现有行为 |
| Python 业务库 | 旧模板使用 aiosqlite 业务存储，没有 PyMySQL 业务依赖 | 对齐当前 MySQL Repository/事务及 TLS 合同，启动不自动建表 |
| 模型配置 | 旧模板读 dotenv、旧 fallback 键及带 provider 的模型名 | 消费当前 Studio 注入的 MODEL_*，显式 provider、headers、超时和重试配置 |
| 健康检查 | 旧 `/health` 只有固定状态，缺少本轮 launchId/checks | 增加真实就绪检查和本轮身份，支持前端代理就绪复核 |
| 会话隔离 | 旧线程键已包含用户、企业等身份，但没有本轮 namespace | 复用可信身份计算，加入 preview/probe 隔离，不能信任前端自报归属 |
| 静态业务验收 | Studio 部分检查假定嵌套 principal、直接 factory 调用等特定写法 | 按模板真实注册表、上下文与 checkpointer 装配更新检查，保留实际身份和依赖证据要求 |

## 实施顺序

### 1. 先固定当前合同

沿用 [Template Engine 消费合同](AGENT_RUNTIME_TEMPLATE_ENGINE_1028.md) 和 [启动合同](RUNTIME_LAUNCH_CONTRACT_1028.md)。不增加新拉取接口；继续 `/v1/generate` 的 `requestedConfig`、V3 State 和 `/v1/update` 的 V4 更新合同。

架构只取已确认 TechnicalPlan，应用配置仍以 `.devagentstudio/application.json` 为准。Runtime 当前支持 anonymous-session；登录或授权开启时明确阻断，不默默关闭配置。

### 2. 搬入并调整 Python 骨架

复用旧模板真实的 AG-UI 服务、Agent 注册表、交互协议、可信身份与 checkpoint 实现。保持七模块固定入口，补齐 MySQL、模型环境、launchId、健康检查及 namespace。

重点修改旧模板的 `settings.py`、`models/factory.py`、`infrastructure/database.py`、`server/app.py`、`server/health.py`、`persistence/checkpointer.py`、`interaction/service.py` 和依赖清单。业务数据库切换 MySQL；checkpoint SQLite 是独立职责，可继续保留，不能一起误删。

旧 `ALLOWED_ORIGINS` 按逗号解析，当前注入的是 JSON 数组，需要对齐。旧任意 loopback 端口的 CORS 放行也需按当前启动合同收口。删除 dotenv 与旧 fallback 配置来源，避免重启后读到另一套模型或数据库配置。

修改依赖后同步 `uv.lock`，不能只改 pyproject，让后续 frozen sync 失败。

### 3. 接入 Engine Core 与 Source

主要入口位于 `template-engine/engine-core/src/main/java/com/devagentstudio/template/engine/`：

- `core/v3/CodeTemplateRelease.java`：承载 Runtime 源码。
- `source/v3/CodeTemplateReleaseLoader.java`：加载第三类根及能力声明。
- `core/v3/ExtensionResolver.java`：依赖、配置与冲突检查。
- `core/v3/V3ProjectMaterializer.java`：按实现类型物化唯一后端。
- `core/v3/V3UpdatePlanner.java`：Runtime Artifact 与更新路径。
- `core/v3/ApplicationConfigProjector.java`：确保 Runtime 配置不落到 Java YAML。

能力和实现选择属于 Core/Source，不放到 Service 控制器。Service 的 `PackageBuilder` 已负责通用 ZIP 打包，应复用其确定性打包逻辑。

### 4. 同步公开合同和源文件门禁

更新 `engine-service/src/main/resources/contracts/v3/template-state.schema.json`、相关 V4 schema 及 `openapi/engine-service-v1.yaml` 中受影响的路径/描述；同时更新实际 State 读写与更新计划，避免只改 schema。

同步 `scripts/ci/list-code-template-release-inputs.py` 等源码输入清单、受保护表面声明和模板 revision，并在模板仓库 `docs/DEVELOPMENT_GUIDE.md` 记录第三种源码归属及生成布局。保留当前 Java 合同、Route Projector 和 identity/managed markers。

### 5. 共享前端接通 Runtime

修改 `template-source/code/frontend/base/rsbuild.config.ts`，沿现有配置增加 Runtime 分支，保留 inspector 和路由投影。代理需覆盖实际业务路径、AG-UI `/agents/*/run`、匿名会话路径和 `/__runtime/health`，保留 Cookie 与 SSE。不能只改 `/api` 端口。

模型、数据库和匿名会话密钥仅传给 Python；前端服务进程只获取 Runtime public URL。新增产品调用使用官方 AG-UI 客户端/事件类型，补齐对应依赖，不能另造 fetch/SSE 协议。

### 6. 对齐 Studio 消费与业务验收

Studio 现有选择、ZIP 安全检查、物化事务、启动锁、日志和服务卡片继续复用。按真实模板结构调整静态验收：旧模板的 `_AGENT_BUILDERS` 注册表、`RuntimeContext.user_id` 等可信字段、AsyncExitStack 管理的 checkpointer 都应按实际装配语义识别；不为通过检查重写正确模板，也不以降低归属校验代替适配。

## 完成标准与验证边界

后续验证需同时覆盖：Java 原布局；Runtime Generate 的完整 ZIP/State/readiness；V4 改名/主题后的能力与路径；MySQL/模型环境；前端代理和匿名会话；真实会话回复及失败详情。下载成功不等于运行就绪。

本次未执行任何测试、构建、health、语法或启动验证。仅运行了依赖锁文件生成：`uv lock --project <临时 Runtime Base>` 和 `pnpm install --lockfile-only --ignore-scripts --dir <临时 Frontend Base>`，均成功。pnpm 提示当前 Node 25.9.0 超出模板声明的 `>=20.19 <23`，并有 peer dependency 警告；未运行额外检查。后续若安排验证，遵守用户要求：不循环调试、不反复跑脚本，验证失败即停止并返回具体命令和错误。
