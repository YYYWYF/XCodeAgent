# 模型转发接入验证记录

日期：2026-10-19。

## 交付范围

- `code7.1_model_transmit`：direct/gateway 两种上游，默认 direct；AG-UI 模型信息与推理接口；SQLite 实际输入输出历史；可选 LangSmith；默认关闭的 mock 分析扩展入口。
- `code7.2_devagent_studio_transmit`：基于 code6 的独立应用源码副本，统一 LangChain 转发适配器。保留原有保存、业务上下文、工具执行、确认门和恢复流程，无业务数据同步功能。
- 用户已明确提供的三项原模型配置迁入服务私有 `.env`；两端生成独立且一致的访问凭证。示例不含真实值。云效等其他环境配置按本应用的 `.env.example` 填写。

## 定向验证

以下命令分别使用目标项目自身的虚拟环境，全部调用模拟上游或本机服务，不向 LangSmith 上传真实数据。

在 `code7.1_model_transmit`：

```powershell
.\.venv\Scripts\python.exe -B -m unittest -q
```

15 项通过。包括两种上游认证头、模型参数透传、文本及工具分片、历史成功/失败/取消/中断、重复调用拦截、禁用 LangSmith 零网络、真实 RunTree 配合模拟 Client、观测故障隔离、mock 载荷、配置校验异常不回显密钥。

在 `code7.2_devagent_studio_transmit/Backend`：

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_model_factory tests.test_llm_provider tests.test_model_transmit tests.test_model_transmit_e2e -q
.\.venv\Scripts\python.exe -B -m unittest tests.test_dag_settings tests.test_unit_attempt_identity tests.test_unit_generation tests.test_unit_generation_orchestrator tests.test_ui_design_generator -q
```

分别为 26 项和 54 项。完整 HTTP 测试启动独立转发子进程及本地模型模拟器，验证 Studio → AG-UI → 转发服务 → 官方 OpenAI SDK → 模拟模型 → SQLite。覆盖同步/异步、会话隔离、上下文不自动追加、两种 spaid 来源、文本及工具参数分片、多个候选分帧隔离、上游认证失败和输出截断。测试不依赖实际企业网络。

曾发生一次测试子进程在 25 秒内未就绪；已将测试启动等待提高到 45 秒并补充超时日志。业务调用超时不受此测试夹具调整影响。

新增/变更 Python 模块通过语法解析和实际 import 检查。服务启动脚本通过 PowerShell 语法检查且使用 UTF-8 BOM。

## 健康接口

实际启动两个目标目录的服务后验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8100/health
Invoke-RestMethod http://127.0.0.1:8000/health
```

均返回 `status=ok`。Studio 的 provider 为 `transmit`，公开逻辑模型为 `studio-model`；转发服务当前上游为 `direct`。Studio 启动需要读取用户环境中原有数据库加密密钥，因此沙箱内初次启动被拒绝；经授权在沙箱外启动后健康检查通过，未改动该密钥及其保存逻辑。

验证结束后已清理本次创建的服务进程；实际使用时按启动说明运行，不依赖验证进程常驻。

## 真实外部联调限制

真实 direct 烟测通过 Studio 适配器发送单条“请只回复OK。”，输出预算 64、SDK 重试 0。在沙箱内及沙箱外的临时转发进程均得到 `UPSTREAM_CONNECTION_ERROR`，失败记录已持久化。进一步在沙箱外 DNS 查询确认 `open-llm.uat.cmbchina.cn` 无法解析。因此当前未证明真实企业模型连通，需在可解析该域名的企业网络环境复测。

gateway 模式的真实认证参数尚未提供，已完成模拟请求/响应联调，未执行真实网关调用。LangSmith 真实服务未配置和启用，验证使用 SDK 与模拟客户端。

## 其他验证边界

- 本次未修改前端源代码，未安装前端依赖，未运行前端构建、Electron UI 或安装包构建。
- 旧测试的宽范围检查存在与本次无关的基线问题：`tests.test_workspace_template_reuse` 的旧模板状态断言与当前模板合同不同；`tests.test_template_reconcile_v2_e2e` 引用已不存在的 `_state_digest`。在 code6 基线复核可重现，未修改无关生产逻辑，也未将全量旧测试宣称为通过。
- 当前服务 SQLite 仅支持单进程、单 worker。配置切换后需重启，不提供故障时自动更换上游或历史上下文自动补拼。
