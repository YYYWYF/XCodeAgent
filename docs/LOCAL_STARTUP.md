# 本机启动与模型用量验证

配置日期：2026-10-26。以下步骤用于当前 Windows 电脑，两个工程的依赖已安装，无需重新安装或激活虚拟环境。

## 已准备的环境

| 工程 | 环境与配置 |
| --- | --- |
| code8 | 独立 `.venv`，Python 3.12.4，`requirements.txt` 依赖已安装；私有 `.env` 已创建 |
| code8.1 后端 | 独立 `Backend/.venv`，Python 3.12.4，后端依赖已安装；私有 `Backend/.env` 已创建 |
| code8.1 前端 | Node 24.20.0，项目专用 pnpm 9.15.9，锁定依赖与 Electron 已安装 |

code8 的上游连接、模型及凭证按用户授权沿用 code7.1 的现有配置，原工程未修改。code8 使用独立历史数据库；与 Studio 通信的访问令牌已生成并在两端配置匹配，本说明不展示凭证。

code8.1 的 `MODEL_TRANSMIT_URL` 已设为 `http://127.0.0.1:8100`。默认 UI 模型也复用该转发服务。上游模型配置统一在 code8 的 `.env` 中维护，Studio 侧只配置转发服务地址和访问令牌。

新增的 `scripts/start-local-backend.ps1` 与 `scripts/start-local-frontend.ps1` 固定选择本工程 Python 环境，并优先使用 pnpm 9.15.9。电脑全局 pnpm 是 11，直接执行全局 `pnpm dev/build` 会有项目版本冲突，请使用下列入口。项目 pnpm 启动器位于 `Backend/.venv/Scripts/pnpm.cmd`，对应的 Corepack 缓存位于当前用户目录。

## 按顺序打开三个 PowerShell 窗口

### 窗口一：code8 模型转发服务

```powershell
cd F:\Code\code8_model_transmit_service
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

默认地址为 `http://127.0.0.1:8100`，保持窗口运行。后续每次模型调用结束后，在这个窗口查看埋点 JSON 和随后单独打印的 `current_operation`。

### 窗口二：code8.1 Studio 后端

```powershell
cd F:\Code\code8.1_studio_transmit
powershell -ExecutionPolicy Bypass -File .\scripts\start-local-backend.ps1
```

默认地址为 `http://127.0.0.1:8000`，脚本显式读取本副本的 `Backend/.env`。保持窗口运行。

### 窗口三：code8.1 Studio 桌面前端

```powershell
cd F:\Code\code8.1_studio_transmit
powershell -ExecutionPolicy Bypass -File .\scripts\start-local-frontend.ps1
```

该命令启动 Electron 开发应用，并连接窗口二的后端。等待桌面窗口出现，按现有界面操作即可；无需另外打开 Vite 浏览器页面。开发模式需要单独启动后端，前端启动命令不代替窗口二。

## 手动验证

可另开一个 PowerShell 窗口执行健康查询：

```powershell
Invoke-RestMethod http://127.0.0.1:8100/health
Invoke-RestMethod http://127.0.0.1:8000/health
```

两者应返回 `status: ok`，Studio 健康信息中的 `provider` 应为 `transmit`。健康查询和 `/model-info/run` 都不调用上游推理，因此不会打印模型用量摘要。

在 Studio 打开一个可用应用，进入需求设计或技术规划等会话，发送一条需要模型处理的消息。观察窗口一：每次实际模型调用结束后打印一条 JSON，重点查看 `data.model.input_tokens`、`output_tokens`、`total_tokens`、`name`、`provider`、耗时和执行状态；随后一行打印 `current_operation`。一次界面操作可能触发多次模型调用，出现多条摘要属于正常情况。

上游没有返回用量时，Token 字段可能为 `null`；失败或取消也可能只有部分用量。当前 `usage_source`、`usage_status` 按约定为 `null`。LangSmith 当前未启用时，对应 trace/span 字段为 `null`。完整字段含义及当前 Run ID 的转发合同见 [MODEL_TRANSMIT.md](MODEL_TRANSMIT.md)。

仅检查打印格式、不消耗真实模型 Token，可执行：

```powershell
cd F:\Code\code8_model_transmit_service
.\.venv\Scripts\python.exe -X utf8 -B .\scripts\demo_observation.py
```

该演示使用内存模拟上游，示例输入 284、输出 45、总计 329，不代表真实消耗，也不需要先启动三个窗口。

## 已完成的验证与范围

- code8 和 Studio 两个独立 Python 环境均通过 `pip check`。
- code8：45 项测试通过；Studio 转发接入：30 项测试通过，包含真实回环 HTTP 到模拟上游的链路。
- 前端类型检查及 Electron Vite 构建通过，原前端源码和锁文件未修改。
- 使用当前私有配置检查了 code8 健康接口、两端令牌匹配和模型信息接口，未发送真实模型推理请求。
- 上游域名已解析成功；真实 API 凭证有效性、上游用量返回和桌面完整业务操作，仍由上述手动请求验证。

Studio 的完整应用创建与后续工程生成沿用原有模板引擎和托管数据库依赖。本副本的示例地址分别为 `http://127.0.0.1:18080` 和 `http://127.0.0.1:18081`，这些外部业务服务未在本次安装或启动。如果新建应用在模板/数据库注册阶段报错，需要按实际环境填写 `Backend/.env` 对应配置并准备这些原有服务，或使用已具备条件的现有应用验证模型链路。

## 停止与端口调整

停止时关闭 Electron，再分别在三个 PowerShell 窗口按 `Ctrl+C`。若应用按原有行为保留在托盘，使用托盘退出。

默认使用 8100（转发）和 8000（Studio 后端）；启动时如果提示端口占用，应先检查已有服务。需要换端口时：转发脚本支持 `-Port`，并同步修改 `Backend/.env` 的 `MODEL_TRANSMIT_URL`；后端脚本支持 `-Port`，前端脚本通过 `-BackendUrl http://127.0.0.1:新端口` 指向它。修改 `.env` 后重启对应服务。
