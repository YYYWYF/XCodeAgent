# 网关三档配置实施与验证

## 当前实现

- `MODEL_UPSTREAM` 继续选择 direct 或 gateway；默认 direct。
- `GATEWAY_TIER` 为 fast、pro、ultra 之一，默认 fast。
- 每档显式配置 `GATEWAY_<TIER>_BASE_URL` 和 `GATEWAY_<TIER>_MODEL_NAME`，统一通过不可变 `GatewayProfile` 成对解析。
- 三档共用 `GATEWAY_API_KEY`、`GATEWAY_KEY` 及现有身份头规则；不会自动按业务场景选档或因调用失败换档。
- 只校验启用上游及选中档位。pro、ultra 的真实接口未知，默认留空；选中未配置档位将明确阻止启动。
- 保留现有 AG-UI 路径与请求结构；gateway 的健康与模型信息额外公开 `gatewayTier`。
- SQLite 继续使用既有表结构，在 `extensions.gatewayTier` 保存服务端实际档位；该字段不能由客户端伪造。LangSmith metadata 与 mock 埋点保留同一档位。

## 验证结果

| 验证 | 结果 |
| --- | --- |
| 7.1 `.venv/Scripts/python.exe -B -m unittest discover -s tests -v` | 22 项通过。覆盖三档流式/非流式、工具调用、地址和模型匹配、认证头、配置文件加载、缺失参数、非法档位、失败不换档、历史及追踪。 |
| 7.3 Backend `.venv/Scripts/python.exe -B -m unittest tests.test_model_transmit tests.test_model_transmit_e2e` | 18 项通过。通过真实回环 HTTP 串联现有 Studio 适配器、新转发服务和本地模拟上游，网关用例遍历三档与 fixed/request 两种 spaid 来源。 |
| Python 语法检查 | 变更服务模块与测试文件通过 AST 解析，服务配置实际 import 成功。 |
| Studio `/health` | 本地 8000 返回 `status=ok`、`provider=transmit`。新版本各档位服务由 HTTP 回归独立启动并检查健康。 |

跨项目测试初次因夹具仍使用旧单组网关配置名而失败。已将 7.3 **测试夹具**更新为 `GATEWAY_TIER` 和所选档位的显式配置，并增加三档覆盖；7.3 产品业务代码、前端和调用协议未修改。code7.2 和 XCodeAgent 未修改。

Windows 沙箱中，测试进程树清理因权限受限而失败。夹具改为显式回收 Windows 虚拟环境启动器及其子进程，并在权限审批后于沙箱外完成回归。受限测试留下的进程已按父进程及完整命令核实后清理；原有 8000 和 8100 服务保留，没有在其运行数据库上启动第二个实例。

真实 fast/pro/ultra 企业接口、真实 LangSmith 服务未在本次联调；网关参数、凭据权限与三档共用规则仍需按实际接口确认。

## 部署与切换

本次仅向私有 `.env` 补齐缺失的档位配置，已核对全部原有配置值保持不变。当前仍为 direct，pro/ultra 地址和模型名称为空。旧单组 `GATEWAY_BASE_URL/MODEL_NAME` 不再读取；如果其他部署存在旧配置，应将其地址与模型标识成对转存至实际档位，而不是覆盖整个环境文件。

切换至 pro 时，填写以下配置以及共享凭据、spaid 参数：

```dotenv
MODEL_UPSTREAM=gateway
GATEWAY_TIER=pro
GATEWAY_PRO_BASE_URL=<真实pro接口基础地址，不包含/chat/completions>
GATEWAY_PRO_MODEL_NAME=<真实pro模型标识>
```

补齐实际参数后，在原服务终端 Ctrl+C 停止进程，再从 7.1 根目录运行 `./scripts/start.ps1`。只运行单实例、单 worker。当前已运行的旧服务不会自动加载代码或配置；建议同时重启 Studio 后端以刷新模型信息缓存。
