# 内置后台退场与交接验证（2026-11-08）

本批为前端断连恢复之后补齐后台自己的退场责任：前端连接使用短租约，真实业务任务独立存活；升级先建立退出栅栏，确认任务和收口工作结束、清理预览、等待原 PID 退出后才放行。原正式产物确认门不变，不增加历史数据读取或迁移。

## 本批代码范围

- 后台控制与入口：`Backend/app/services/backend_runtime.py`、`middleware/backend_runtime.py`、`protocols/backend_runtime.py`、`main.py`、`environment.py`、`packaging/backend_server.py`。
- 真实活动计数：Detached Execution、UI 生成池、Workspace Bootstrap、Builtin Authorization、Preview Runtime 和 Workspace Process Registry 各自暴露当前执行数量；Run Registry 已包含仍在收口的同步 Worker。
- 自身预览清理：`frontend_project_launcher.py`、`backend_process_registry.py` 只清理本实例持有的进程句柄。
- Electron：`Frontend/src/main/backendRuntime.ts`、`backendRuntimeClient.ts`、`backendService.ts`、`index.ts`；维护私有控制凭据、连接租约、身份核验、两阶段退出和更新失败后的就绪重查。
- UI 生成归属：`application_planning_workflow.py::_ui_confirmation` 同步同阶段新 Run，修复冷恢复反复报“正在交接”。
- 对应后台和前端测试，以及 `CODEBASE_INDEX.md`、`CROSS_PLATFORM_SUPPORT.md`。

## 自动检查

- `curl -sS http://127.0.0.1:8000/health`：`status=ok`；源码后台 `managedRuntime.enabled=false`，不自动退场。
- 后台 Runtime/环境/打包契约/Detached/AG-UI/生成池/Bootstrap/预览与取消：`python -m unittest tests.test_backend_runtime tests.test_backend_environment tests.test_environment_packaging tests.test_detached_execution tests.test_ag_ui_action_stream tests.test_ui_design_generation_pool tests.test_workspace_bootstrap_service tests.test_preview_runtime tests.test_preview_runtime_cancellation`，93 项通过。
- UI 恢复、规划恢复投影、原生中断和结构化动作：36 项通过。使用 unittest Runner，结束后显式调用 `close_workflow_checkpointer()` 回收原有中断测试创建的 SQLite 线程；直接运行该组合会在断言结束后等待这些线程，不能把打印 `OK` 等同于进程已退出。
- `pnpm test:backend-environment`：HTTP 环境、私有登记、实例接管，以及升级忙碌拒绝、清理失败回滚、不完整 AG-UI 回执拒绝、等待真实子进程退出均通过。
- `pnpm test:app-update`：22 项通过。
- `pnpm build`：TypeScript、主进程、preload 和 renderer 构建通过；变更 Python 文件的 `py_compile`、`git diff --check` 通过。
- 扩展预览测试中的 `.npmrc` 旧拒绝断言已按现有安装修复契约同步：允许前端 registry 配置，继续拒绝后端 `.npmrc` 和其它敏感路径，没有修改该业务路径规则。

## 真实 macOS Electron 与冻结后端

使用 PyInstaller 构建真实 onedir ARM64 后端，再通过 electron-builder 构建独立应用标识、独立 Electron 数据目录的未签名 `.app`。窗口实际显示并通过原生 UI 操作，未使用浏览器或隐藏窗口代替。临时包及配置保存在本机私有临时目录，未作为发布产物提交。

最终独立工程：`/Users/yifei/Coding/xcodeagent_test/packaged-retirement-verified-20261108`，应用“安装包退场最终验证1108”。

1. 通过 UI 新建项目、生成需求文档与产品规划，在原联合确认门显式确认后，点击 UI 设计生成。
2. 对隔离测试包的模型连接加入 30 秒受控网络延迟，随后完整退出 Electron。后台 PID `62432` 仍存活；Manifest 为 `generating`，生成 ID 为 `ui-design-b03a720d78474988b6426e4a391b048e`。通过官方 AG-UI 客户端请求 `prepare_exit` 返回 `busy`，没有取消任务或关闭接收栅栏。
3. 重新启动真实 `.app`，仍接入 PID `62432`；模型请求完成后恢复同一生成 ID 的结果，页面展示“后台退场验证 / 原任务只执行一次”。需求显示“已确认”，UI 为“待确认”，“下一步”可用但未点击；没有自动生成 TechnicalPlan。
4. 在空闲后台仍存活时改变临时包可执行文件的 mtime，使当前可执行文件身份与登记不同，再启动 `.app`。旧 PID `62432` 已退出，新后台登记为 PID `63195`；登记的可执行文件身份与文件一致。
5. 新实例冷恢复仍显示同一设计结果、已确认需求及有效 UI 待确认门；修复前复现的“正在交接 / 旧确认失效”没有再出现。
6. 整个最终工程只有三次接受记录：首次规划、需求确认、显式 UI 生成；重开及只读恢复没有新增生成提交。Lifecycle owner 为最后一次 UI 动作 Run `7dd9eb07-9b1d-4096-897a-9af93eb68508`。
7. 另用控制客户端建立租约后直接结束该客户端而不发送 detach，再完整退出 Electron。观察原 PID `63195` 保持运行，失联租约过期后后台自行退出，未发送强杀信号。该步骤验证真实租约过期；不是 Electron SIGKILL 故障注入。

首次包实测发现的 UI owner 不同步问题先修复生产者，再以全新工程复验；没有为旧错误现场添加兼容读取或迁移。

## 证据边界

- 已验证 macOS ARM64 未签名本地测试包、真实模型请求加受控延迟、完整退出/重开、后台拒绝忙碌退场、可执行文件身份变化交接、冷恢复和失联租约退场。
- 本轮未执行完整自动更新安装器替换、签名/公证发布包、macOS x64、Windows/NSIS、干净机器安装或 Electron SIGKILL。当前主机为 macOS ARM64；可执行文件身份变化只验证交接路径，不能替代这些发布环境验证。
