# 权限节点展示与 Build 错误归因复核

## 已知事实

读取当前会话的完成事件发现：权限节点的 Graph 状态为 completed，真实 Bootstrap 子状态为 failed，已继续进入 Build。随后两个 Build 任务以 runner_crash 失败，原因分别包含“当前运行已停止”“拒绝继续执行 Agent”。Build 节点没有覆盖共享 message，handle_failure 把上一节点的权限提示复制成了最终错误。这解释了为什么仅移动权限 Recovery 卡没有消除截图中的文案：实际恢复目标是 Build。

另外，Electron 会话保存白名单没有保留步骤 nodeName/attempt/authorizationBootstrap，刷新后节点身份和结果可能丢失；历史事件合并只复制权限结果，没有同时补齐节点身份。

## 本轮修改文件

| 文件 | 修改目的 |
| --- | --- |
| `Backend/app/services/workflow_failure_message.py` | 失败 Build 按当前执行切片的失败任务取原因，保留收尾错误优先级；无任务原因时不借用上游 message |
| `Backend/app/graph/subgraphs/build.py` | 调度结束显式写入当前 message/error，成功时清空旧错误 |
| `Backend/app/protocols/workflow/runtime.py` | 权限实时步骤标题按真实初始化子状态投影，不按 Graph completed 宣告初始化成功 |
| `Backend/tests/test_workflow_failure_message.py` | 复现权限放行后 Build 失败的摘要、收尾与 lifecycle 归因 |
| `Backend/tests/test_build_subgraph_scheduler.py` | 真实调度器 runner 异常边界覆盖上游文案的回归 |
| `Backend/tests/test_authorization_bootstrap.py` | completed 节点在成功、复用、人工、失败、跳过结果下的实时标题 |
| `Frontend/src/shared/authorizationBootstrap.ts` | 提取主进程与渲染进程共用的字段白名单，并统一节点/动作详情结果标题 |
| `Frontend/src/renderer/src/service/authorizationBootstrap.ts` | 重导出共享合同，现有消费入口保持不变 |
| `Frontend/src/main/sessionMessageNormalization.ts` | 保存节点名、轮次及经过同一白名单校验的权限结果和完整有界 DDL |
| `Frontend/src/renderer/src/service/processStepHistory.ts` | 完成事件恢复时补齐节点名/轮次，标题使用真实子状态 |
| `Frontend/src/renderer/src/service/buildFailureMessage.ts` | 按同一 Run 的最后一个失败 Build 完成事件提取任务原因，仅替换与错误旧摘要相同的文本 |
| `Frontend/src/renderer/src/service/recoveryIncident.ts` | 当前 Build 的任务事实优先于误归因的恢复摘要，保留原动作和诊断来源 |
| `Frontend/src/renderer/src/components/AiChatPanel/globalFallbackState.ts` | 最后一次失败消息优先展示当前 Build 原因 |
| `Frontend/src/renderer/src/components/AiChatPanel/recoverySurface.tsx` | 纠正重试错误中与当前错误摘要相同的误归因文本；其他重试错误保留 |
| `Frontend/src/renderer/src/components/AiChatPanel/AiChatPanel.tsx` | 底部当前错误及重试错误使用同一归因修正 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/ProcessSteps/index.tsx` | 权限节点标题消费真实 Bootstrap 子状态 |
| 同目录 `AuthorizationBootstrapPanel.tsx` | 动作详情与节点标题共用文案 |
| `Frontend/tests/authorizationBootstrap.test.ts` | Electron 落盘往返、真实会话状态复现、错误归因、标题与详情一致性 |
| `Frontend/scripts/run-execution-recovery-tests.mjs` | LESS 在当前进程预处理，避免测试 worker 退出阻塞 |
| `docs/CODEBASE_INDEX.md` | 当前边界、共享合同与回归入口 |

## 验证

- Backend：`.venv/bin/python -m unittest tests.test_workflow_failure_message tests.test_build_subgraph_scheduler tests.test_authorization_bootstrap`：68 项通过。测试遥测上传出现联网警告，不影响测试结果；未为测试开放遥测网络。
- Frontend：`node scripts/run-authorization-bootstrap-tests.mjs`：11 项通过。包含当前会话相同的“权限 completed、Build failed、最终 phase failed 且旧摘要为权限错误”的数据形状。
- Frontend：`node scripts/run-execution-recovery-tests.mjs`：43 项通过。
- Frontend：`pnpm build`：包含主进程/渲染进程类型检查和生产构建。
- `python3 -m py_compile`：Build 调度、失败归因服务及 AG-UI runtime 三个文件通过。
- `git diff --check`：通过。
- `/health`：返回 status=ok。

## 结果与边界

Graph 的 completed 只表示权限节点处理完毕并放行，不代表数据库初始化成功。节点标题与动作详情统一为：权限数据库初始化成功、权限数据库初始化成功（复用已有结果）、权限数据库待手动初始化、权限数据库初始化未完成、权限数据库初始化已跳过。

权限提示继续以节点详情中的弱提示展示，保留已有权限恢复重试，DDL 默认折叠。真实 Build 失败仍显示自己的原因和重试入口，不会被伪装成权限初始化警告。没有改写现有会话、持久化恢复记录或执行目标数据库 SQL；没有新增兼容读写或迁移。

当前会话中的“运行已停止”属于尚未修复的 Build 执行事实，本轮只修正错误归因及展示，不能宣称 Build 已成功。Electron 原生自动化仍报 Sky Computer Use service startup request failed，未完成窗口视觉验收，未用浏览器替代。
