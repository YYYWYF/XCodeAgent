# 最新 Studio 模型转发副本

## 来源与边界

本目录复制自 `F:/DevAgent_Studio/XCodeAgent` 的最新工作区源码，再参考 code7.2/code7.3 相同的三层转发适配器接入 code8。不是以旧 Studio 覆盖最新业务。前端、登录和业务文件保持基线内容；不新增身份采集、遥测上报、提示词、工作流节点或存储格式。

私有环境文件、虚拟环境、node_modules、缓存、构建产物、运行数据库及 .git 不复制。依赖和配置在本目录独立准备，不依赖源工程的代码修改。

## 启动

1. 按相邻 code8 README 配置并启动转发服务（默认 8100）。
2. 在本目录准备 Python 3.12 环境：`python -m venv Backend/.venv`，再安装 `Backend/requirements.txt`。
3. 从 `Backend/.env.example` 创建本目录私有 `Backend/.env`，设置 `MODEL_TRANSMIT_URL/TOKEN`，其中令牌匹配服务 `SERVICE_TOKEN`。
4. 其他模板、数据库等原有设置按当前测试环境填写；无需设置上游 MODEL_API_KEY/BASE_URL/NAME。
5. 当前电脑依赖和私有配置已准备，使用 `scripts/start-local-backend.ps1` 和 `scripts/start-local-frontend.ps1` 启动。完整的三窗口命令、健康检查和模型用量验证见 [LOCAL_STARTUP.md](LOCAL_STARTUP.md)。

本副本不处理或重写登录行为。未启动转发服务不会阻止模型对象构造或本地健康查询，首次模型请求时才查询模型公开信息。

## 当前调用合同

前端仍通过原有 AG-UI 调 Studio。后端 LangChain 模型统一通过官方 `RunAgentInput` 请求 `/model-info/run` 与 `/model-inference/run`。Python 使用 httpx-sse 和官方 Event 类型；没有手写 SSE 拆包，也没有新增前端传输代码。

业务方完整 messages、工具 Schema、temperature、max_tokens、thinking、stop 等生成参数保留。UI、DAG 的独立预算、修复和续写由原业务代码继续负责。调用级 max_retries=0 仍代表零次上游 SDK 重试。转发 HTTP 客户端不自动重发；现有特定业务调用中的传输重试策略保持其原有范围。

只消费 model-inference.chunk/result 中的原始模型载荷，标准 AG-UI 文本与工具事件不再次加入结果。用量尾帧及终态结果也不重复计数。工具执行在原 Studio，模型服务只返回指令。

## 身份

单次模型请求 envelope.threadId 取当前 Runnable configurable.thread_id；每次请求生成独立 envelope.runId，沿用 code7.2/7.3 的防重合同。同一前端业务 Run 可以产生多次模型请求，不能把前端 runId 重复用作 code8 的请求主键。

已有 Runnable metadata.run_id（若存在）保留在 `metadata.extensions.studio_run_id`；project_id、显式 user_id、workflow_scope 和其他已有扩展逐调用传递。不新增前端字段、不推测可见会话身份。

因此 code8 现有控制台 identity.run_id 是单次模型请求 ID，不是前端交互轮次 ID。原始业务 Run 可在 code8 历史 extensions.studio_run_id 中关联。这是现有不可重复 runId 合同的明确边界，本工程不修改 code8 防重或日志语义。

## UI 独立连接

UI_DESIGN_MODEL_TRANSMIT_URL 留空时复用全局服务；填写后使用独立转发地址。令牌、代理、spaid 可独立配置，缺省跟随全局。不同模型由不同服务实例的服务端配置决定；实例必须分端口和 SQLite 数据库。UI 的 for_ui_design_model() 入口和所有生成业务参数保持不变。

旧 Settings 构造字段保留以免改变业务测试与内部视图，但 from_env 不再读取直连地址、密钥、原生协议或 OpenCode 请求头；统一工厂只使用转发字段，不以旧字段回退直连。

## 取消与错误

转发客户端使用最新 model_request_cancellation 的 request/response hooks。取消在原始 HTTP 字节块边界观察，SSE 心跳没有可见正文时也能退出，关闭本次流并使服务关闭上游。当前 Run 的停止不会污染共享模型的其他 Run。无字节等待仍由原 HTTP 读取超时约束。

上游认证、限流、超时及协议异常保留稳定转发错误码，不抛 Studio HTTPException，不触发登录重认证。Unit 模型诊断映射到现有分类；不改变失败恢复门禁。转发失败不静默直连、不换模型、不扩大修复预算。

## 支持范围

当前 code8 支持 OpenAI 兼容 Chat Completions；Studio 的传输统一为 AG-UI。原生 Anthropic /messages、OpenCode 专用头及其特殊路由降级不由当前 code8 承载，不能宣称原直连配置全部等价。不会修改 code8 或通过隐式直连绕过该限制。

code8 的埋点 JSON 和独立 current_operation 仅由服务打印，Studio 不解析或再上报该摘要。Studio 原有业务遥测保持基线行为。

## 验证入口

在 Backend：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest tests.test_model_factory tests.test_llm_provider tests.test_model_transmit tests.test_transmit_latest_runtime tests.test_model_transmit_code8_e2e -v
```

用例包括同步/异步、工具和多模态、零重试、消息及预算透传、SSE 取消、并发身份、错误分类，以及适配器到真实回环 HTTP code8 再到模拟上游和 SQLite 的完整链路。code8 E2E 默认从相邻 `code8_model_transmit_service` 读取服务源代码，不使用或修改其真实 .env 和数据库。

全量历史测试不宣称全部通过；继承的部分 UI 夹具仍 mock invoke 而产品已用 stream，DAG 默认预算断言仍为 2048 而源码为 32768，另有 Windows 临时路径和 SQLite 清理问题。验证记录单独列出，避免通过修改无关业务逻辑迎合旧断言。
