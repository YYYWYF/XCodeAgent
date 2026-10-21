# Studio 模型转发接入

本目录是 `code6_devAgent_ystLogin` 的独立应用源码副本。原目录不参与运行时写入；依赖、构建结果和私有环境文件不作为应用源码复制。聊天保存、上下文构造、工具执行、确认门和 Native Recovery 均沿用原有实现。

## 配置与启动

1. 在相邻 `code7.1_model_transmit` 配置并启动服务，默认监听 `127.0.0.1:8100`。
2. 使用 `Backend/.env.example` 创建本地 `Backend/.env`，设置同一个服务访问凭证 `MODEL_TRANSMIT_TOKEN`。此凭证与上游模型密钥独立。
3. 云效等原有公共环境参数继续按当前环境填写；上游密钥只存在转发服务私有环境。
4. 首次运行分别安装依赖：`python -m venv Backend/.venv`，`Backend/.venv/Scripts/python.exe -m pip install -r Backend/requirements.txt`；在 `Frontend` 执行 `pnpm install --frozen-lockfile`。
5. 沿用 `scripts/start-backend.ps1` 与 `scripts/start-frontend.ps1`，默认后端端口 8000。

## 当前通信合同

Studio 后端与转发服务使用 AG-UI，前端继续通过已有 `@ag-ui/client` 调用 Studio。Python 通过 `httpx-sse` 读取标准 SSE，并以官方 `ag_ui.core.Event` 联合类型校验。不存在手写 SSE 分割器或前端新增传输。

`POST /model-info/run` 在首次模型调用时查询当前公开模型信息。`POST /model-inference/run` 的 `forwardedProps.modelInference.request` 保存原始模型消息、工具声明和生成参数；`threadId` 为现有 Runnable 会话身份，`runId` 为单次调用 UUID。调用级 `max_tokens`、`max_retries=0`、超时和扩展参数均保留，不在 Studio 中决定 direct/gateway 上游。

标准文本/工具事件供协议观察，适配器只将 `model-inference.chunk` 和 `model-inference.result` 中的原始模型载荷还原为 LangChain 消息，防止重复输出。错误事件、失败状态、终帧缺失均按模型调用失败处理，上游 401 不作为云效重新登录信号。

`Settings.model_name` 的 `studio-model` 是工作流证据中的逻辑标识，不是实际厂商模型配置。实际模型名来自模型信息和推理响应。`/health` 只报告本地后端状态与转发协议元数据，不主动访问模型服务。

## 保存边界

本副本没有新增正式聊天保存、上下文拼接、业务数据镜像或远程恢复。每次调用所需完整上下文仍由既有 Agent/Graph 构造。转发服务独立保存实际输入输出，并可通过 LangSmith 关联追踪；关闭追踪或追踪平台故障不改变 Studio 的业务状态。

## 验证

本次执行结果和真实网络联调限制见 [MODEL_TRANSMIT_VALIDATION.md](MODEL_TRANSMIT_VALIDATION.md)。

后端运行 `python -m unittest tests.test_model_factory tests.test_llm_provider` 以及新增的 `tests.test_model_transmit*` 测试；UI/DAG 调用预算与相关业务回归仍使用原测试。服务端另有模型请求、流式工具调用、历史持久化和异常测试。真实第二种网关凭据尚未提供，网关模式只验证模拟请求和响应，不宣称真实联调成功。
