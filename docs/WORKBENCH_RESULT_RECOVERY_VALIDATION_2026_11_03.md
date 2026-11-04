# 开发续接选择结果恢复验证

日期：2026-11-03。修复范围是断连/响应丢失以及重新进入会话后的结果展示，保留 continuation 一次性消费和所有原生确认边界。

## 原因与改动

原恢复逻辑只投影 active executions 的运行、失败和待确认状态。开发目标选择由一个已完成的 Run 返回，active execution 已释放；响应丢失后仅同步 lifecycle 无法恢复选择卡片。前一轮“续接去重通过”只证明没有再次消费，不能证明结果展示恢复正常。

- `Backend/app/services/workbench_result_projection.py`：只读当前 checkpoint 和该线程最新执行索引，严格核对 workspace、Run、Thread、owner、状态和选择模式；不调用 Graph，不返回 token，不读取旧 checkpoint。
- `Backend/app/protocols/application_lifecycle.py`：现有 AG-UI `get` 接收完整 `sessionId/sessionThreadId`，通过响应中的 `extensions.workbenchResult` 投影标准 Workflow；能力元数据同步发布，不新增 REST/JSON 端点或持久化副本。
- `Frontend/src/renderer/src/service/applicationLifecycle.ts`：按工作区/会话/线程隔离读取 Promise，避免另一会话复用结果。
- `Frontend/src/renderer/src/components/AiChatPanel/workbenchResultRecovery.ts`：结果合并到原空回复或原 Run，保持消息 ID；幂等、拒绝跨会话结果和较新本地输入后的旧结果。
- `Frontend/src/renderer/src/components/AiChatPanel/hooks/useWorkflowConversation.ts`：续接流收口后、冷进入和显式刷新均读取并保存恢复结果；运行中及切换后的迟到读取不回写。继续复用 `continueRevisionOnce`，结果恢复不发送 continuation。
- `NextDevelopmentGuide/index.tsx`：选择状态显示“等待选择开发目标”；续接发起消息改为“技术规划已确认，正在衔接本次修改的开发流程”，不再声称一定已执行 API 映射检查或自动生成 DAG。

## 自动化验证

```sh
cd Backend
.venv/bin/python -m unittest tests.test_workbench_result_projection tests.test_application_lifecycle_protocol tests.test_revision_continuation_handoff
# 19 项通过
.venv/bin/python -m py_compile app/services/workbench_result_projection.py app/protocols/application_lifecycle.py tests/test_workbench_result_projection.py
# 通过

cd Frontend
pnpm test:connection-recovery    # 109 项通过，含真实 Hook 挂载、读取、落盘、重入及选择组件渲染
pnpm test:stage-sessions         # 通过，含 continuation 单次消费保护
pnpm test:application-planning-runtime # 通过
pnpm test:workbench-phase        # 14 项通过
pnpm build                      # 通过
```

新增后端用例使用真实 SQLite checkpoint 和执行库，证明反复读取不推进 checkpoint、不改变 lifecycle 或执行数，错 owner/thread、新 Run 覆盖旧结果时不返回卡片，完整 AG-UI Get 结果不包含 token。新增 Hook 用例通过标准 AG-UI mock 读取恢复原回复并渲染真实 NextDevelopmentGuide，断言可见的选择标题和开发按钮；重新挂载不增加消息或重复落盘，不发送工作流请求。

首次验证的测试问题已修正并重跑：测试清理函数改用现有工作区专用连接关闭 API；既有协议 fixture 补齐当前已存在的 `initialize_application` action；选择组件 fixture 补齐真实 `pending` 进度。

## 真实 Electron

原生应用：DevAgent Studio（Electron），工作区 `/Users/yifei/Coding/xcodeagent_test/stage-thread-electron-20261103`，原开发会话 `1af93e82-38f6-46a9-b5c9-3aa82148c351`。所有输入、确认、选择列表、重入由原生界面操作，不用浏览器/Vite 渲染替代。

1. 重新打开用户截图所示的缺失结果会话：实际出现“你想开发哪个页面或 API？”卡片、`开发「静态公告页」` 按钮。展开“查看全部页面 / API”实际出现静态公告页条目及可用的 `开始开发「静态公告页」` 按钮；未点击执行 Build。
2. 该恢复引用原 Run `b93fe03f-a457-42df-9ec1-939b2ab329d0`，不是新建续接。浅色界面已目视检查。

### 完整断连与重入验收

为排除旧测试数据的前置依赖，最终通过 Electron 新建当前格式的独立应用 `续接结果恢复实测1103`，工作区 `/Users/yifei/Coding/xcodeagent_test/result-recovery-electron-20261103`。未启用模拟数据库，只有待开发静态页面 `recovery`、零实体、零业务 API。需求/ProductPlan 联合确认、UI 显式跳过、首次 TechnicalPlan 确认及正式修改影响确认全部通过原生界面完成，没有手动写入 confirmed 或跳过确认门。

- 正式修订：`chg_317b9e01c148415882923f9a119888ac`。
- 原开发线程：`bc4f5111-8745-4eac-af1c-4db7260a1066`；原开发 Session：`198d2d32-7d1a-405a-9fd9-96bc4914a928`。
- 独立 PLAN 线程：`1ee14a9b-98c9-4856-8d37-629aff4f9ec4`。
- 唯一续接 Run：`2af2a7cb-a8b4-4f7d-91c2-abff4dedd7c4`。

| 操作 | 前端可见结果 | 去重证据 |
| --- | --- | --- |
| 确认新技术规划，代理转发唯一续接；等后台完成后故意丢弃整份响应，向 Electron 返回 503 | 无需点击或再次发送，原开发会话自动显示“你想开发哪个页面或 API？”选择卡片、“等待选择开发目标”和可用的 `开发「结果恢复验收」` 按钮；深色截图已目视检查 | HTTP continuation 请求 1 次，原开发线程 API gate Run 1 个 |
| 展开恢复卡片的“查看全部页面 / API” | 实际显示 `/page/recovery` 未开发条目及可用的 `开始开发「结果恢复验收」` 按钮；未点击执行 Build | 不新增工作流请求 |
| 切换浅色、重载 Electron、从首页重新打开同一应用和原会话 | 同一张选择卡片和开发按钮再次可见，没有重复卡片或历史回复 | 会话始终为 6 条消息；continuation 用户消息 1 条，同 Run 恢复回复 1 条，HTTP 提交仍为 1 次；lifecycle revision 保持 24、无 activeFormalRevision/activeExecutions |

恢复后的回复已保存到 `/Users/yifei/.devagentstudio_dev/sessions/result-recovery-electron-20261103-e71992045805/frontend/198d2d32-7d1a-405a-9fd9-96bc4914a928.json`，引用的就是上述原 Run。该结果没有声称已检查 API 映射；本次续接实际执行的是已完成目标变更分析，后续具体开发目标仍由用户选择。

临时透明代理仅对最终新工作区注入故障，8000 转发至真实后端 8001，不替换业务结果或消费逻辑。审计 `/tmp/xcodeagent-result-fresh-audit.jsonl` 记录一次 `response_lost_after_backend_completed` 以及唯一的 `continue_revision_build`；不记录 token。完成后关闭代理和 8001 实例，恢复原 `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`，健康检查 `ok`。

### 未计为成功的旧场景与范围

原工作区验收期间有外部桌面操作结束了规划并发出只读查询，用户已回复允许继续测试。后续旧工作区的确认状态已改变，新影响确认被正常拦住；已取消该次未入场测试影响，保留文件和历史。备用旧静态应用的模拟数据库注册信息缺失，已有注册 AG-UI 动作因环境缺少 `MANAGED_DATABASE_SERVICE_KEY` 失败，未创建远端资源；设置页尝试的切换未保存（`CONFIG_REQUEST_INVALID`），本轮测试修订已通过既有 End 动作结束。该旧应用留下 FAILED 注册状态，未删除现有文件，也未将这些前置失败算作结果恢复通过。最终成功验收完全使用上述新应用，不依赖这些旧数据。

本轮未执行页面 Build，不覆盖 Windows 或完整测试/审查/验收流程。所有源代码改动尚未提交；主应用业务代码未修改。
