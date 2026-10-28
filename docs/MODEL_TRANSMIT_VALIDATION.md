# code8.1 模型转发接入验证

## 定向验证结果

使用已安装依赖的 Python 3.12 解释器执行，不复制其虚拟环境，也不修改依赖环境。所有模型上游使用内存模拟，跨项目服务使用隔离临时配置和数据库。

```powershell
python -X utf8 -B -m unittest tests.test_model_factory tests.test_llm_provider tests.test_model_transmit tests.test_transmit_latest_runtime tests.test_model_transmit_code8_e2e tests.test_transmit_backend_health -v
```

30 项通过。包括预算和 UI 独立连接、配置不读取上游密钥、同步/异步与流式、工具 JSON 分片、多模态输入、结束帧检查、提前关闭、零重试、原始字节流取消、前端 Run 扩展关联、网络重试范围和业务错误分类。

跨项目用例实际启动临时回环 HTTP code8，使用最新 Studio 工厂发起三次请求（同步、异步、异步流式），核对完整消息、工具/生成参数、请求模型、usage=284/45/329、本地历史与控制台独立 current_operation。重复业务 Run 的模型请求使用不同单次 ID，没有触发服务 409；原业务 ID 保留在 extensions.studio_run_id。

后端健康用例执行完整最新 app 的真实 lifespan，临时重定向用户目录并创建隔离数据库加密密钥，通过真实回环 HTTP 检查 `/health` 返回 status=ok、provider=transmit、transport=ag-ui-sse。测试使用系统分配端口，不停止或替换原有 8000 服务。临时服务器结束后正常回收。

## 基线问题与未宣称通过的检查

初次额外检查：

```powershell
python -X utf8 -B -m unittest tests.test_model_transmit tests.test_model_request_cancellation tests.test_unit_model_failure tests.test_model_transport_retry tests.test_ui_design_generator tests.test_dag_settings -q
```

该宽范围调用未全部通过，原因已调查：

- UI 三个旧用例只 mock invoke，但最新产品代码已使用 stream，导致模拟输出为空。原始未修改工程的 `tests.test_ui_design_generator` 可复现相同三个失败，UI 业务及该测试文件均未改动；新增工厂测试单独验证 UI thinking、独立连接和专属 token 预算。
- `tests.test_dag_settings` 将全局默认 max_tokens 断言为 2048，原始源码实际为 32768；在未修改源工程可复现。同样保留业务与测试基线，不改业务默认值迎合断言。
- 原始同步暂停测试使用 `/tmp/model-heartbeat-pause`，Windows 解析为工作盘根目录下路径，沙箱不可写；新转发取消测试使用无写入的隔离运行身份验证相同原始字节边界。
- 原始 `test_unit_model_failure` 的一个端到端用例在 Windows 清理仍打开的 SQLite checkpoint 时失败；新增定向分类测试验证转发异常到原诊断代码的映射。
- 旧 `tests.test_model_transport_retry` 使用 pytest，当前测试解释器未安装 pytest；新增 unittest 用例验证转发网络错误的有界重试和认证错误不重试，没有为此改动其他项目环境。

因此不宣称全量历史测试通过。未执行真实企业模型、真实网关或 LangSmith 网络联调；这需要用户的服务配置和网络。

## 源码与界面范围

按源工作区文件哈希建立副本，最终检查只允许模型接入白名单中的后端、测试、依赖示例和文档变化。整个 Frontend、登录代码、Agent 业务、Graph 节点、提示词和工具文件保持基线内容。由于前端未变，没有安装前端依赖、执行 pnpm build 或 Electron UI 验证。
