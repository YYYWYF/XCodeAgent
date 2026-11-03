# 会话摘要与消息分页验证（2026-11-03）

结果：实现与验证完成，用户实测确认通过。任务开始时工作区干净，没有覆盖其他未提交修改。未修改 Python 业务流程、模型提示词、AG-UI 传输或确认门。

## 实现

- 原 `<sessionId>.json` 仍为唯一聊天正文，派生 `.json.index` 保存摘要、消息字节范围和控制上下文位置。索引按源文件身份/大小/时间校验，可从当前 JSON 重建，不迁移或删除原记录。
- 列表、工作区历史目录及阶段 sequence/幂等查找读取摘要；有有效索引时不全量读取正文。
- 打开会话读取最近 20 条；额外保留必要的当前工作流/确认/开发目标和原提问。上滚按消息 ID 向前分页，磁盘位置决定消息次序。
- 分页保存只更新/移除已加载范围；未加载前缀留在磁盘。版本确认、串行保存、后台执行、失败保存和草稿保护继续沿用。
- 流式收尾使用同步更新后的实际缓存；认证回滚只移除本次乐观消息。避免运行中翻页后旧闭包数组误删新读入历史。
- 视口附近才挂载重消息卡片；交互过的表单保留挂载。前插及实际行高变化补偿阅读锚点，并关闭浏览器重复滚动补偿。
- JSON 索引构建、深层消息规范化和保存放入单独的存储 Worker，避免主进程重复深拷贝。

## 最终通过的验证

Frontend 目录运行：

- `pnpm test:session-history`：11/11，真实临时文件 + 生产 Hook；尾页/上翻、摘要命中后零全量读取、索引损坏重建、Unicode/转义边界、未加载历史保留、并发流式输出、失败重试、切换隔离、磁盘顺序、表单保留和锚点高度补偿。
- `pnpm test:connection-recovery`：108/108，新增生产 Workflow Hook 的运行中前插历史后完整保存回归。
- `pnpm test:performance-retention`：13/13。
- `pnpm test:stage-sessions`：通过，原子保存与会话身份测试。
- `pnpm test:message-list-scroll`：8/8。
- `pnpm test:workflow-preview`：99/99。
- `pnpm test:application-planning-runtime`：通过。
- `pnpm build`：通过，包含 Node/Web TypeScript 检查、主进程 Worker、preload 和 renderer 构建。存在原有 AG-UI 静态/动态导入分包提示，退出码为 0。
- 新增存储/历史 Hook/缓存/虚拟行/滚动核心文件的定向 ESLint：通过。
- `git diff --check`：通过。
- `curl -sS http://127.0.0.1:8000/health`：`status=ok`。开始检查时没有后端进程，随后沿现有 `.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` 启动并验证。

## Electron 实测

通过 native CUA 操作真正的 Electron 开发客户端；没有用浏览器 Vite 页面替代 UI 验收。

- 既有测试目录 `/Users/yifei/Coding/xcodeagent_test/normal-journey-secondary-20261018` 中，通过正式 preload 创建独立的“分页性能验证 1103”会话（ID `25635818-0ca7-40f0-8e24-916fc0e72b10`），保存 105 条合成消息；未覆盖用户会话。
- 真实 IPC `sessions:read` 首次返回 20 条，total=105，第一条为消息 086；`sessions:list` 返回项不含 messages。
- 实际打开开发会话，上滚自动加载更早消息。最终稳定版本的 DOM 测量：beforeRows=20、afterRows=40、anchorDelta=0、mounted=19。
- 检查浅色/深色主题，消息、顶部历史加载入口和 Composer 可读；最后恢复原浅色主题。
- 输入专用未发送草稿 `分页草稿 KEEP-HISTORY-1103（不发送）`，返回首页，经“打开工作目录”重新进入同一项目，确认草稿原文保留；随后仅清除本次测试草稿，恢复原空输入。
- 运行已有 `manualPerformanceRetention.ts` 的真实 Provider + 官方 SDK 受控 AG-UI 流：PASS，requestCount=1，cancelled=0，backgroundCompleted=true，originalThreadPreserved=true，unsavedPreserved=true，draftPreserved=true，retainedClients=4。关闭隔离验证结果，fetch 在 harness finally 恢复。
- 开发客户端冷启动的更新检查曾输出“Python 版本检测失败”后继续打开；本次使用独立已健康的后端。部分既有测试应用的 Lifecycle 身份不匹配告警未作迁移或修改，不作为本次真实模型任务成功的证据。

## 边界

- 首次索引构建、索引失效和保存仍在 Worker 内处理完整 JSON；不是把磁盘改成数据库或分块正文存储。
- 索引有效后的列表与分页读取按字节范围工作。单条特别大的消息仍可能有较高解析/渲染成本；本次没有引入单消息内部详情的另一个分页合同。
- 未运行 Windows、打包安装、真实模型长时间 Build 或数小时/隔夜压力测试。后台任务实测为受控标准 AG-UI 流，不冒充真实模型 Build。
- 合成验收会话保留，未执行未经授权的删除。

## 文件

- `Frontend/package.json`
- `Frontend/scripts/run-session-history-tests.mjs`
- `Frontend/src/main/index.ts`
- `Frontend/src/main/sessionHistoryIndex.ts`
- `Frontend/src/main/sessionStorage.ts`
- `Frontend/src/main/sessionStorageWorker.ts`
- `Frontend/src/preload/index.d.ts`
- `Frontend/src/renderer/src/components/AiChatPanel/AiChatPanel.tsx`
- `Frontend/src/renderer/src/components/AiChatPanel/components/MessageList/HistoryMessageRow.tsx`
- `Frontend/src/renderer/src/components/AiChatPanel/components/MessageList/MessageList.less`
- `Frontend/src/renderer/src/components/AiChatPanel/components/MessageList/index.tsx`
- `Frontend/src/renderer/src/components/AiChatPanel/components/MessageList/useHistoryScroll.ts`
- `Frontend/src/renderer/src/components/AiChatPanel/hooks/sessionMessageCache.ts`
- `Frontend/src/renderer/src/components/AiChatPanel/hooks/useChatSessions.ts`
- `Frontend/src/renderer/src/components/AiChatPanel/hooks/useSessionHistory.ts`
- `Frontend/src/renderer/src/components/AiChatPanel/hooks/useSessionRuntimeStore.ts`
- `Frontend/src/renderer/src/components/AiChatPanel/hooks/useWorkflowConversation.ts`
- `Frontend/src/renderer/src/service/chatSessions.ts`
- `Frontend/src/renderer/src/window.d.ts`
- `Frontend/src/shared/sessionHistory.ts`
- `Frontend/tests/sessionHistory.test.tsx`
- `Frontend/tests/sessionHistoryRuntime.test.tsx`
- `Frontend/tests/workflowConversationRuntime.test.ts`
- `docs/CODEBASE_INDEX.md`
