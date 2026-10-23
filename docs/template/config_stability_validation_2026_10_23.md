# 应用配置稳定性修复验收记录（2026-10-23）

## 验收结论与证据边界

**已知事实：** 本次只修改 XCodeAgent。五阶段中的代码修复、自动化回归、真实后端 AG-UI 与模板服务联调已完成。桌面 Electron 的界面操作、关闭重开及明暗主题视觉验收尚未执行，**整体桌面端到端验收未完成，不能标记全部交付条件通过**。

**合理推断：** 当前新建合同、V4 完整性门禁、定点编辑及阶段恢复已有自动化和真实模板链路证据；它们不能替代 Electron 实际渲染与交互证据。

**建议：** 在可操作的 Electron 会话中补测 Base/Login/Login+Authorization 三类新建应用的配置卡、设置保存、关闭重开、推断依赖输入保留及两种主题，再做最终桌面验收。

约束：未引入旧数据兼容、迁移、历史工程修复或 P2 自然语言扩展；未改数据库和登录前端固定配置；未整体替换两个 application YAML；XCodeAgent 不补工程配置结构，只校验模板片段并更新已有参数。

## 分阶段结果

| 阶段 | 根因与修复 | 自动化与验收结果 |
| --- | --- | --- |
| 1 读写与白屏 | Python 读取不再补写默认结构；合法可选子对象只进入内存草稿；Electron 增加路径级校验；普通保存保留磁盘受管字段/revision，读失败中止；配置卡校验根对象、ErrorBoundary、完整草稿合并与首次字段初始化；成功后刷新配置 | Base/Login/Login+Authorization 的创建 Schema 同时经过 Electron 校验器与 Python 提交校验；非法根 SSR 不崩溃；局部表单/重复刷新保留输入；过期保存及读盘失败测试通过。Electron 界面验收待补 |
| 2 V4 Ensure 门禁 | 增加 capability × 文件 × 真实片段路径覆盖、目标/格式/owner 校验；递归拒绝重复键、alias/anchor/merge 及冲突；WorkingCopy 后验检查必需路径和原有值后才写 State；dotenv 注释不再破坏幂等性 | 缺目标/错路径/重复键/不安全 YAML/后验失败/磁盘故障均不提交；journal 恢复通过；实际模板两条链路通过 |
| 3 参数编辑 | YAML AST 完整路径定位单行标量；倒序替换位置，不全文件 dump；变更键按完整路径统计；缺键不新增 | 同名非受管叶子、行尾注释、CRLF、幂等、缺键、流式/多行标量冲突及 journal 恢复通过 |
| 4 阶段与恢复 | 持久化 DESIRED_SAVED/CAPABILITY_APPLIED/PARAMETERS_APPLIED；保留真实 stage/code/target；原 Desired 未同步阻止新提案；核对磁盘状态跳过已安装能力；文件外部删除只报冲突/缺结构 | Desired 写盘失败、V4 失败、参数失败后原 ID 恢复、崩溃窗口、新提案阻断、外部删除及脱敏诊断通过；不宣称 Desired/V4/参数三阶段全局回滚 |
| 5 前端重试与集成 | 工作区范围 pending 只保存 version/operationId/revision/requestId/digest；SHA-256 与 Python 一致；变更提案先 get 再阻断；并发同提案只提交一次；成功读盘才清 pending；设置与对话提供显式恢复原操作，AG-UI resume 只传身份，后端核对已保存 Desired 摘要与修订 | 同提案/变更提案/并发/读盘失败保留 ID/显式恢复不传密钥通过；真实 AG-UI 生命周期、重放与 resume 通过；Electron 全流程未执行 |

## 真实模板联调

运行服务为 `127.0.0.1:18080`，进程 PID 36727 的命令使用 `template-engine/engine-service/target/engine-service-1.0.0-SNAPSHOT.jar`。实际二进制 SHA-256：`269bf8d378568ad72b7c968664012e0304336d642f12a1b3d86eba42f36333f2`，大小 17773913 字节。旁侧源码工作区为 `1016`、提交 `81a33bfd88b6cf550aaece3000b4a9334fa528ed`；源码提交仅作旁证，不代替上述运行 JAR 标识。返回的 TemplateRevision 为 `2026.10.22.2`。

首次真实顺序联调在新增 Authorization 时 HTTP 500。核查当前模板合同后发现 XCodeAgent 的递归 `exclude_none=True` 会丢掉 Maven spec 中固定存在但值为 null 的 `version`/`scope` 键。修复当前 V4 请求/下一 State 的序列化并补回归后，从全新工程重新测试两条链路均通过；没有修改模板服务或修补历史工作区。

真实运行命令：

```sh
cd Backend
.venv/bin/python -m tests.manual_config_stability_e2e
```

该入口真实调用 `/v1/generate`、生产包校验与物化，再通过运行中的后端 `/application-config/run` AG-UI HTTP 执行配置，不使用 Mock 模板包。测试仅使用合成凭据，不发起真实认证。断言包含 run/message/custom/state/run-finish 生命周期、相同 operationId 重放、仅身份 resume、纯参数修改不递增序列，以及非受管同名 YAML 叶子/主 YAML 字节保持。

| 链路 | 全新工作区 | 能力提交 configRevision | 能力提交 stateSequence | 结果 |
| --- | --- | --- | --- | --- |
| sequential | `/private/var/folders/6d/10z6jxy93p9bh1p_k9bm2_g40000gn/T/config-stability-sequential-6tde0ow6` | 2 → 3 | 1 → 2 | APPLIED |
| combined | `/private/var/folders/6d/10z6jxy93p9bh1p_k9bm2_g40000gn/T/config-stability-combined-crge5msb` | 2 | 1 | APPLIED |

上表从 Base 的 revision=1 / sequence=0 开始；后续各自追加的纯参数修改均 APPLIED，sequence 保持，非受管内容保留。证据文件位置（均在各工作区内）：`.devagentstudio/application.json`、`.devagentstudio/template-state.json`、`.devagentstudio/runtime/application-config/real-*.json`、`frontend/.env`、`backend/src/main/resources/application.yml`、`backend/src/main/resources/application-dev.yml`。本报告不复制任何 application.json、密钥或配置 before/after 内容。

## 自动化命令与结果

- 后端：以下 10 个模块通过，共 **86 项**。

```sh
cd Backend
.venv/bin/python -m unittest \
  tests.test_config_surface_gate tests.test_template_update_v4 \
  tests.test_template_update_v4_transaction tests.test_template_capability_reconcile \
  tests.test_application_config_orchestrator tests.test_application_config_prerequisite \
  tests.test_application_parameter_updater tests.test_application_config_mutation \
  tests.test_application_authorization_config tests.test_application_config_change_resolver
```

- 前端 `pnpm test:application-config`：**40/40**；`pnpm test:managed-workspace`：**11/11**。
- 前端 `pnpm build`：TypeScript、Electron main/preload、renderer 构建通过。
- 所有改动 Python 文件的 `py_compile`：通过。
- `curl -sS http://127.0.0.1:8000/health`：`status=ok`。本机访问在沙箱外获准执行，报告只记录状态，不复制完整健康响应。
- `git diff --check`：通过。
- 验证环境 Node `22.23.1`，使用项目指定 pnpm `9.15.9`。全局新版 pnpm 曾触发联网签名等待，最终使用缓存中的该指定版本执行；其自动产生的 lockfile 变化已撤回，未变更依赖。

## 未完成、限制及反向检验

1. 当前 Computer Use 只提供空的浏览器表面，没有可操作的原生 Electron 应用；本机 9222 也无 Electron 调试接口。遵守仓库约束，没有以 Vite 浏览器页面替代 Electron 验收。因此卡片/设置实际交互、关闭重开与明暗主题视觉状态待测。
2. 新建联调工作区仅物化模板并验证配置，尚未安装自身业务依赖或启动业务服务，因此未运行生成工程自身的业务 Build、业务单测及业务集成测试；本次通过的是 XCodeAgent 构建、配置自动化和真实配置链路，不代表生成业务或真实登录服务可用。
3. 严格 YAML 门禁会拒绝重复键、锚点/别名/合并键及无法唯一定位的结构；不会猜测更新或主动修用户文件。原操作的 Desired 尚未成功保存或被外部修改时，显式 resume 会拒绝，必须恢复原提案或解决冲突。
4. 反向检验：真实模板链路与失败注入支持结构和恢复正确；反例边界是 Electron 特有的表单挂载、窗口刷新、主题或 IPC 故障。这些尚无现场证据，故不能把本报告的后端联调通过解释为桌面端到端完成。

## 修改文件清单

下列均为本次新增或修改的文件；用户提供的 `codex_xcodeagent_config_stability_fix_bab0ba4.md` 保持原样，未列为修改。

### Backend/app/

- `Backend/app/protocols/application_config_operation.py`
- `Backend/app/services/application_config/repository.py`
- `Backend/app/services/application_config/schema.py`
- `Backend/app/services/application_config/service.py`
- `Backend/app/services/application_config_orchestrator.py`
- `Backend/app/services/application_parameter_updater/editors.py`
- `Backend/app/services/application_parameter_updater/service.py`
- `Backend/app/services/config_operation_state.py`
- `Backend/app/services/template_reconcile/config_surfaces.py`
- `Backend/app/services/template_reconcile/executor_v3.py`
- `Backend/app/services/template_reconcile/protocol_v3.py`
- `Backend/app/services/template_reconcile/service.py`
- `Backend/app/services/template_reconcile/yaml_config_merge.py`

### Backend/tests/

- `Backend/tests/manual_config_stability_e2e.py`
- `Backend/tests/test_application_authorization_config.py`
- `Backend/tests/test_application_config_change_resolver.py`
- `Backend/tests/test_application_config_mutation.py`
- `Backend/tests/test_application_config_orchestrator.py`
- `Backend/tests/test_application_config_prerequisite.py`
- `Backend/tests/test_application_parameter_updater.py`
- `Backend/tests/test_config_surface_gate.py`
- `Backend/tests/test_template_capability_reconcile.py`
- `Backend/tests/test_template_update_v4.py`

### Frontend/src/

- `Frontend/src/main/managedWorkspace.ts`
- `Frontend/src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/ApplicationConfigPrerequisiteCard.tsx`
- `Frontend/src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/ConfigCardBoundary.tsx`
- `Frontend/src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/index.tsx`
- `Frontend/src/renderer/src/components/AiChatPanel/hooks/useWorkflowConversation.ts`
- `Frontend/src/renderer/src/components/SettingsPage/SettingsPage.tsx`
- `Frontend/src/renderer/src/components/SettingsPage/settingsConfigChanges.ts`
- `Frontend/src/renderer/src/components/SettingsPage/useSettingsConfigRecovery.ts`
- `Frontend/src/renderer/src/components/Welcome/applicationService.ts`
- `Frontend/src/renderer/src/pages/WorkbenchPage.tsx`
- `Frontend/src/renderer/src/pages/useApplicationConfigRefresh.ts`
- `Frontend/src/renderer/src/service/applicationConfigOperation.ts`
- `Frontend/src/renderer/src/service/applicationStorage.ts`

### Frontend/tests/

- `Frontend/tests/applicationConfigCard.test.tsx`
- `Frontend/tests/applicationConfigRecovery.test.ts`
- `Frontend/tests/applicationPersistence.test.ts`
- `Frontend/tests/managedWorkspace.test.ts`
- `Frontend/tests/settingsConfigApply.test.tsx`

### Frontend/scripts/

- `Frontend/scripts/run-application-config-tests.mjs`

### docs/

- `docs/CODEBASE_INDEX.md`
- `docs/template/config_stability_validation_2026_10_23.md`

