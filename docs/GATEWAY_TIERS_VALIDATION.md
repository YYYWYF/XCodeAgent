# 网关三档验证范围

code8 继承 code7.1 的 fast/pro/ultra 配置及离线 SDK 测试，但不依赖原工程源码或数据库。

验证入口：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest tests.test_gateway_tiers tests.test_service tests.test_config_safety -v
.\.venv\Scripts\python.exe -X utf8 -B -m unittest tests.test_model_observation tests.test_observation_service -v
```

用例使用 MockTransport、假密钥和临时 SQLite。覆盖三档地址/模型成对选择、共享认证与 spaid、失败不换档、档位历史、SDK 本地追踪身份及新增模型统计。网关调用的日志 model.provider 为 gateway，model.name 为该档实际配置值。

这些是离线验证，不证明企业网关、真实 LangSmith 或最新直连 Studio 的网络连通。pro/ultra 未提供真实参数时保持空配置，选中未配置档位应阻止启动。部署配置取 code8 独立 .env，切换配置后重启单实例服务。
