# 后台异常退出后原窗口自愈验证（2026-11-09）

> 历史验证记录：文中的跨次启动后台接管及独立常驻部分已撤回；当前客户端退出会停止其内置 Backend，窗口运行期间的连接同步保留。

本批补齐后台故障后的连接恢复：Electron 原窗口保持打开，主进程核验原后台并在其已退出时启动或接管后继实例；新地址通过基础设施 IPC 发布，原 Planning Runtime 和 Session 只读同步当前现场。生成请求、恢复执行请求和确认不会自动重发。未改后端产品协议或业务确认规则。

## 当前改动范围

- `Frontend/src/main/{backendService,backendConnectionMonitor,backendConnectionIpc,backendProcess,index}.ts`：统一监督、单次启动、进程归属核验、退出/安装暂停、连接快照及启动失败子进程清理。源码开发后台不会由 Electron 另行启动。
- `Frontend/src/shared/backendConnection.ts`、`preload/index.ts` 及前后端 Window 声明：公开连接 revision、status、baseUrl 和 backendInstanceId；不公开进程控制随机数。
- Renderer `main.tsx`、`service/{backendConnection,authentication,agUiAgent,applicationPlanningRuntime,recoveryStore,workspaceTools,databaseCredentialCrypto,telemetry}.ts`、`useWorkflowConversation.ts`：首次补读、已知 origin 重绑定、旧流释放、读取/回调代次隔离、连接恢复后只读对账。业务快照和认证阻断保持原规则。
- 测试入口 `backendEnvironment.test.ts` 和新增 `backendConnectionMonitor.test.ts`、`backendConnectionRecovery.test.ts`；更新代码索引及跨平台说明。

## 自动检查

- `pnpm test:backend-environment`：环境 HTTP 断言及四项测试通过。覆盖并发检查只执行一次恢复、失败后再探测、停止后的迟到探测/启动不发布、真实官方 AG-UI 客户端从缓存旧地址访问新 HTTP 服务、IPC 补读不覆盖更新事件、旧读取不能覆盖新结束快照，以及整个连接恢复过程不调用生成/停止能力。
- `pnpm test:connection-recovery`：122 项通过。
- `pnpm test:application-planning-runtime`：规划 Runtime 及关联旅程通过。
- `pnpm test:auth`：10 项通过，包含没有 Window 的 SDK 使用场景；未收到连接快照时不执行地址重绑定。
- `pnpm test:execution-recovery`：按当前工作区重跑后 51 项通过；未修改其它在途的诊断卡实现和用例。
- `pnpm test:app-update`：22 项通过。
- `pnpm build`：主进程与 renderer TypeScript、Electron 主进程/preload/renderer 构建通过。
- 本批修改路径的 `git diff --check` 通过，其它在途改动保持原样。
- 后端健康检查返回 `status=ok`；本批未修改 Python 业务代码。

## 真实 Electron 与冻结后台故障注入

用当前源码经 PyInstaller 和 electron-builder 构建未签名 macOS ARM64 `.app`，实际打开原生窗口。隔离测试包使用占位模型配置和本地计数服务，没有复制实际模型凭据；计数服务用于确认整个故障恢复没有发起模型生成。

工程：`/Users/yifei/Coding/xcodeagent_test/packaged-retirement-verified-20261108`。开始时仅有三次接受记录，lifecycle revision 为 8，需求已确认，UI 设计已生成但仍等待显式确认。

1. 故障前后台 PID `69652`，地址 `http://127.0.0.1:62388`，实例 `backend-41c13d407284404a9380b020f8cda826`。Electron 主进程 PID `69628`，renderer PID `69670`、`69764`。在原窗口输入“后台崩溃后保留此草稿，尚未发送”，不提交。
2. 核对私有登记、health 的 PID/实例及临时包路径之后，仅向该隔离后台发送 SIGKILL。约 2.53 秒内自动登记后继 PID `69788`，新地址 `http://127.0.0.1:63212`，实例 `backend-e708cbc8632e4edd98c0ba065d36fde0`。
3. 未重启 Electron、未刷新原窗口、未点击重新同步。原主进程及 renderer PID 全部仍存活；未发送草稿保留，需求仍“已确认”，UI 仍“待确认”，原设计结果可见。“下一步”未点击。
4. 后继后台实际打开该工程的 `checkpoints.sqlite`，证明原窗口已向后继发出恢复读取，而非只保留缓存画面。接受记录仍为三条，lifecycle revision 仍为 8，模型调用计数为 0。
5. 再用 SIGSTOP 暂停仍存活的 PID `69788`。原窗口出现“后台连接正在恢复，请稍候。”；私有登记未换 PID，系统中该临时包后台进程只有一份，未强杀或重复启动。SIGCONT 后同一后台恢复响应，提示自动消失，草稿仍保留。
6. 原窗口 Cmd+R 冷重载后从最近项目重入同一工程，仍恢复原 UI 待确认门和已确认需求。窗口最初注入的是旧地址，当前地址由 preload 补读提供；未建立第二个后台或新增业务提交。冷重载会重建页面 UI，本批不声称持久化未发送草稿或自动恢复页面导航。
7. 最后正常退出测试 Electron。PID `69788` 自行退场，私有登记没有被监督器改成新的 PID；接受记录仍为三条，模型调用仍为 0，lifecycle revision 仍为 8。全部复验完成后停止测试计数服务。

无 Window 的 SDK 边界修正并重跑构建后，再次打包、打开同一工程并进行 SIGKILL：PID `71249`、端口 `51823` 自动替换为 PID `71447`、端口 `52886`，耗时约 2.55 秒。原窗口与“最终构建重连草稿，未发送”保留，确认状态未变化；复验结束正常退出应用。

本次还查看了已运行的源码 Electron；后台自行启动/换端口的验收证据来自上述真实打包应用，未使用浏览器或隐藏窗口代替。

## 验证边界

- 本轮实机覆盖待确认现场的后台崩溃、活进程暂时失联、原窗口恢复、换端口后的冷重载和正常退出；单元测试覆盖结束状态与迟到读取隔离。
- 不声称后台进程崩溃后原内存计算仍能继续。业务节点是否重试继续由当前 checkpoint、生命周期及原恢复/确认门决定。
- Windows/NSIS、macOS x64、签名/公证发布包、完整安装器升级和干净机器安装仍未在本轮验证；当前主机为 macOS ARM64。
