# 模型直连与转发

在 `dev_agent` 维护同一套业务代码，通过 Backend 私有环境配置中的 `MODEL_TRANSPORT` 选择接入方式。配置修改后重启后端；不支持任务执行中切换，不自动探测或回退到另一模式。

## 直连（默认）

```ini
MODEL_TRANSPORT=direct
MODEL_PROVIDER=openai
MODEL_BASE_URL=https://api.openai.com/v1
MODEL_API_KEY=replace-with-your-token
MODEL_NAME=your-model
```

保留现有 OpenAI 兼容及 Anthropic 原生协议、OpenCode 可选请求头、取消 hooks 和调用预算。UI 独立直连继续使用 `UI_DESIGN_MODEL_BASE_URL/API_KEY/NAME`，三项齐全才启用。直连不校验、不使用转发配置。

## 转发

```ini
MODEL_TRANSPORT=transmit
MODEL_TRANSMIT_URL=http://127.0.0.1:8100
MODEL_TRANSMIT_TOKEN=replace-with-service-token
MODEL_TRANSMIT_TRUST_ENV=false
```

只要求转发服务连接，不要求上游模型地址、密钥或名称。服务需提供 AG-UI `/model-info/run` 和 `/model-inference/run`，当前适配的服务合同为 OpenAI Chat Completions 原始消息/工具载荷。原生 Anthropic 和 OpenCode 专用路由由直连模式提供，不能假定转发服务支持。

可选 `MODEL_TRANSMIT_SPAID` 随调用传递。转发是全局模式：需求、规划、代码生成、UI 设计和辅助模型调用全部使用这一套服务配置。没有 UI 独立转发服务，直连的 UI 独立配置在转发模式下不生效。

## 转发专用请求参数

沿用 `dev_agent_model_transmit` 分支的专用协议，推理调用发往 `/model-inference/run`。请求为官方 AG-UI `RunAgentInput`：顶层 `threadId`、`runId` 关联业务线程与执行，`messages/tools/context` 为空数组，`state` 为空对象；实际模型载荷位于 `forwardedProps.modelInference`：

| 字段 | 内容 |
| --- | --- |
| `request` | 完整模型消息、工具 Schema、temperature、max_tokens、thinking、stop、stream 等生成参数；模型占位名固定 `studio-model`，真实模型由服务配置决定 |
| `timeoutSeconds` | 本次上游模型调用超时 |
| `maxRetries` | 本次上游 SDK 重试预算，0 保持零重试 |
| `metadata.sessionId` | 当前会话身份 |
| `metadata.requestId` | 每次模型请求独立生成的身份 |
| `metadata.userId/projectId/studioDataRef` | 调用上下文显式提供时传递 |
| `metadata.spaid` | 调用上下文或全局 `MODEL_TRANSMIT_SPAID` 提供时传递 |
| `metadata.extensions.context` | 业务阶段 `business_stage`、操作 `business_operation`、节点 `node_name` 和目标 `target`；只传已有业务事实 |
| `metadata.extensions.workflow_scope/operation_id/conversation_id` | 调用上下文显式提供时传递 |

首次实际模型调用懒加载 `/model-info/run`。服务认证使用 `Authorization: Bearer <MODEL_TRANSMIT_TOKEN>`，不向服务传递本地上游模型密钥。同步、异步、流式和工具调用均由同一适配器生成上述请求；直连 SDK 不接收这些转发专用字段。

## 代码边界

- `agents/model_factory.py` 是统一模型入口，返回 LangChain `BaseChatModel`；`services/llm_client.py` 也复用它。
- `agents/transmit_chat_model.py` 适配标准 invoke/stream、异步及工具调用；`services/model_transmit_client.py` 使用官方 AG-UI 类型和 httpx-sse。
- `agents/model_business_context.py` 与 `model_operations.py` 保存任务隔离的阶段、操作和目标上下文，直连与转发共用。协议转换只在转发适配器中发生。
- 转发沿用业务 Run/Thread，每次模型请求生成独立 requestId；取消关闭当前连接，并发调用不共享可变身份。
- 超时和零重试覆盖保留；转发 HTTP 层不添加重试，上游 SDK 重试预算交给服务。原业务层明确配置的瞬时故障重试范围保留；认证和参数错误不进入它。
- 产品 AG-UI 端点、工具执行位置、工作流确认门、文档采纳统计及存储合同保持不变。

`/health` 的 `model_transport` 表示实际配置模式；`provider`、`base_url` 指向当前接入方式。`model_transmit` 在转发时返回协议元数据，直连时为 null；不返回密钥。健康检查不代表上游模型请求成功。

## 手动验证

按本次要求不新增测试文件，最终修改由用户手动验证。可分别配置两种模式并重启后端，观察需求、技术规划、UI 设计、工具调用及停止行为；转发模式在服务端核对业务身份、操作上下文及调用预算。不要将凭据提交到仓库。
