# 模型转发服务代码索引

| 文件 | 职责与当前合同 |
| --- | --- |
| `app/main.py` | FastAPI factory；`GET /health`，AG-UI `POST /model-info/run` 与 `POST /model-inference/run`；gateway 公开信息包含 `gatewayTier` |
| `app/contracts.py` | `forwardedProps.modelInference` 参数、身份与连接覆盖边界校验 |
| `app/config.py` | 默认 direct；`GATEWAY_TIER=fast/pro/ultra`；三组 `GATEWAY_<TIER>_BASE_URL/MODEL_NAME` 由 `GatewayProfile` 成对解析，只校验启用档位，共用网关凭据 |
| `app/upstream.py` | 官方 OpenAI SDK；direct/gateway 连接、选定档位地址和模型、网关身份头与网络资源关闭；失败不换档 |
| `app/aggregation.py` | 完整响应及流式文本、工具参数、扩展字段聚合 |
| `app/protocol.py` | 官方 AG-UI 生命周期、custom 原始模型结果和标准文本/工具事件 |
| `app/inference.py` | 防重登记、调用生命周期、部分输出检查点、错误与取消收尾；`extensions.gatewayTier` 服务端保留，客户端不可覆盖，direct 时移除 |
| `app/history.py` | 单进程 SQLite `model_calls`，请求上下文及结果持久化；重启中断对账 |
| `app/tracing.py` | 可选 LangSmith 显式追踪与独立 mock 分析扩展入口；追踪 metadata 与 mock 载荷关联实际 gateway 档位 |
| `tests/` | 无外网 SDK/协议/存储/追踪回归 |
| `scripts/start.ps1` | UTF-8 BOM PowerShell 启动，默认本机 8100，固定单 worker |

服务不负责业务流程、工具执行、正式聊天保存、上下文补拼及 Studio 业务数据同步。更改公开路径、custom 事件名、SQLite 字段或目录职责时同步更新本索引与 README。

三档共用当前 AG-UI 请求合同，Studio 无需传入档位或改代码。pro、ultra 的真实地址与模型标识待提供，默认留空；切换至未配置档位会明确阻止启动。旧单组 `GATEWAY_BASE_URL/MODEL_NAME` 已移除。配置切换后重启服务，建议重启 Studio 后端刷新公开模型缓存。档位历史使用既有 JSON 扩展字段，不修改 SQLite 表结构。验证入口为 `tests/test_gateway_tiers.py` 和现有全套服务测试。
