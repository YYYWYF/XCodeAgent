# 正式修订开发续接去重验证

日期：2026-11-03。针对真实 Electron 中已成功续接后再次出现 `continuation changeId 不匹配` 的问题。

补充更正：本记录的响应丢失场景只证明后台完成且没有重复消费，当时没有恢复缺失的选择结果，因此不能作为前端结果恢复通过的证据。该缺口已单独修复并完成可见卡片的 Electron 验收，见 [结果恢复验证](WORKBENCH_RESULT_RECOVERY_VALIDATION_2026_11_03.md)。

## 原因及修复边界

现场开发会话 `1af93e82-38f6-46a9-b5c9-3aa82148c351` 的 14:11 续接 Run `a00a55e4-8619-470e-89c4-a0707e8bd40a` 已完成，lifecycle revision 62、activeFormalRevision 为空；14:33 的第二次请求 `58f1f9ec-cc8c-4818-b6bf-df1a0798adf6` 在入场验证前被拒绝，因此没有新的执行登记。不能把后端正确拒绝旧凭据当作前端行为正常。

可重复投递的入口是 Planning Runtime 的终态交接、工作台 `continuation_ready` 冷恢复 effect，以及 Runtime 显式重试。原工作台接收者只检查所交付 Workflow 的旧 lifecycle，去重 Promise 又属于组件，重新挂载后无法证明凭据仍有效。历史快照和重新注册的回调因而有机会再次提交已消费的凭据。现场未保留足够的调用栈来区分 14:33 当次究竟由哪个入口投递；修复覆盖这些入口的共同提交边界。

- `AiChatPanel.tsx`：接收交接时先通过现有 AG-UI lifecycle Get 读取当前资格，已收口或不匹配的交接不再创建消息/切换会话。
- `service/revisionContinuationGuard.ts`：真正写消息和发送请求前再次读取当前生命周期；同工作区、应用、change/token 的并发请求在组件之外共享 Promise。失败后重新读取消费事实，只有仍 `continuation_ready` 的同一版本可显式重试。后端 CAS、token/hash 和原子登记合同不变。
- `hooks/useWorkflowConversation.ts`：所有开发续接入口经过同一 guard；收到执行登记后，即使后续节点失败，也不恢复凭据重试资格，交由原 Recovery。
- `service/applicationPagePlanning.ts`：权威 lifecycle 已结束或已消费时，忽略 checkpoint 残留凭据，不把只读对账变成新的交接或身份错误。
- `service/agUiAgent.ts`：一次性续接也要求 RUN_FINISHED，提前 EOF 不能作为成功回执。
- `AiChatPanel.tsx`：消费前失败的恢复卡独立于历史只读 Composer 和普通全局错误条件，仅对当前修订的规划/原开发线程显示。技术规划已确认后仍能“继续开发”，不会因此开放历史自由对话。

## 自动化验证

```sh
cd Frontend
pnpm test:stage-sessions                 # 通过，含新增去重/重入/消费前后失败及恢复可见性测试
pnpm test:application-planning-runtime   # 通过，含已收口 checkpoint 残留凭据的显式重试
pnpm test:connection-recovery            # 108 项通过
pnpm test:chat-skills                    # 94 项通过，含续接提前 EOF
pnpm test:workbench-phase                # 14 项通过
pnpm build                              # 通过

cd Backend
.venv/bin/python -m unittest tests.test_revision_continuation_handoff tests.test_planning_stage_thread_journey
# 10 项通过

curl -sS http://127.0.0.1:8000/health     # status=ok
git diff --check                        # 通过
```

连接回归最初两个 Hook 测试缺少新增的权威 lifecycle 读取 mock，产生 `agent.test ENOTFOUND`，已按当前协议补齐并重跑通过。聊天回归最初发现历史列表 fixture 未提供当前必需的临时会话 props，已补齐测试调用方并重跑通过；不添加兼容默认字段。

## 真实 Electron

使用已运行的原生 Electron，应用 `独立阶段线程验证1103`，工作区 `/Users/yifei/Coding/xcodeagent_test/stage-thread-electron-20261103`。所有业务输入、影响确认、技术规划确认及恢复点击均由原生界面执行，未用浏览器/Vite 页面替代。

| 场景 | 实际结果与证据 |
| --- | --- |
| 原错误会话冷启动与重试 | 重载、重新打开、点击原错误卡重试，续接用户消息仍为 3 条，lifecycle 仍为 revision 62，无新的消费/开发 Run。原失败历史保留。 |
| 正常确认自动续接 | 正式技术修改 `chg_d3067de318c4408289a0ea09ed9ae64a`，PLAN `9a463da1-2f08-4431-b9c2-e2db870fe863`；确认后仅新增 1 条续接消息和 Run `4eba3964-d90b-4a04-b072-159d9f08f174`，正常展示开发目标选择；revision 73，activeFormalRevision 为空。 |
| 消费前状态读取失败及失败重试 | 正式修改 `chg_db2e3bb33d0848f39995e8c31ed362a4`，PLAN `cbe2d087-5a7c-47cf-ae5c-a7e6527e1744`。确认后对 `continuation_ready` 的状态读取注入 503，重试后仍 revision 82、consumedAt 为空，HTTP `continue_revision_build` 请求数为 0。深色界面实际显示“开发交接尚未完成 / 继续开发”，同时保留历史只读说明。 |
| 恢复后消费成功、客户端响应丢失 | 对同一次修改恢复读取后点击“继续开发”，仅 1 次 HTTP 续接请求；后端完成后代理丢弃响应。revision 84、activeFormalRevision 为空；重新打开会话后请求数仍为 1。 |
| 最终代码下响应丢失复验 | 正式修改 `chg_3524f4d59950433c8e788f5a82531d5c`，PLAN `842cb0ba-9f31-4cbc-8b69-7bdb445b7a7e`。确认后只发出 1 次续接，后端 Run `b93fe03f-a457-42df-9ec1-939b2ab329d0` 完成，代理丢弃响应后客户端重新读取状态；revision 95、无 activeFormalRevision/activeExecutions。打开已确认的规划历史仅显示只读及“前往开发阶段”，返回开发没有新增续接。重载 Electron 再打开后仍无新增消息/开发 Run。 |

正常链路及两轮故障链路总共新增 3 条续接用户消息（测试前为 3，完成后为 6）；没有新增 `continuation changeId 不匹配`。两轮经过代理的 changeId 各只有 1 次 HTTP 续接请求。消费后失败状态的凭据重试分流另由 guard 的 `failed`/断连用例和 Backend 原子消费用例验证；本轮 Electron 注入的是消费前读取失败和消费成功后的响应丢失，没有伪造真实业务节点失败。

故障注入使用临时本地透明代理（8000 → 原后端 8001），只对上述测试工作区生效；不改变产品 API、源代码或凭据校验。审计只记录动作/changeId/threadId 和计数，不记录 token。审计位于 `/tmp/xcodeagent-continuation-probe-audit.jsonl`。验证不执行页面 Build，不覆盖 Windows 或无关完整测试/审查/验收流程。

测试结束已关闭代理和 8001 临时实例，恢复 `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`，健康检查 `ok`。Electron 留在原测试应用开发阶段，无运行中的模型/构建任务，未清除原始失败历史，未提交项目代码。
