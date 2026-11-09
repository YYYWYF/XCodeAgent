# 数据源配置与字段映射前置旅程实施记录

实施基线：`8307f708734c814342f6c2292947de2c2ee70fa8`，与原方案一致。

## 当前行为

- 字段映射选择“数据表”后，根据应用配置与已成功读取的数据库目录判断前置条件；不根据表数量猜测数据库模式。
- 内置库无真实表时引导关联实体的“模拟数据表”页签。一个关联实体直接定位，多个关联实体必须先选择；缺少有效关联时提示核对技术规划并打开开发产物目录，不沿用此前浏览的实体。
- 外部库没有连接时进入现有数据源抽屉，使用“新增数据库连接”；已有连接但没有表时进入同一抽屉，使用“添加数据表”。本次采用方案 P0，不自动弹出二级配置/选表窗口。
- 读取中仅显示加载状态；目录失败或应用模式与目录冲突时显示错误与“重试读取”，不误导用户重复创建连接。
- 首次尚无当前工作区的成功快照时，`sourcesLoading` 才阻塞选择；已有快照后的后台刷新仅置 `sourcesRefreshing`，保持候选与下拉框可用，不再闪现首次加载引导或因禁用而关闭下拉。确认按钮仍等待刷新完成；刷新错误直到新快照成功才清除，切换工作区不复用原快照的加载资格。
- 表添加和内置建表成功继续使用原工作区事件刷新。抽屉关闭或点击“返回字段映射”时恢复原接口，并额外只读刷新来源和真实列。
- 字段映射保持原组件实例、Endpoint、来源类型、更换来源的暂选项及内存草稿；不会隐式暂存、确认或执行建表。切工作区或 Endpoint 后原返回意图失效。
- RUNNING/UNKNOWN、结构冲突、真实列读取失败继续由原实体表面板核验；引导本身不把设计字段或空列表当作建表成功。

## 组件复用与文件边界

三条真实调用链保持不变：

1. `DataSourcesDrawer → DataSourceEditorModal → DatabaseConnectionForm`：原检测、加密、保存及取消保护。
2. `DataSourcesDrawer → AddDatabaseTables`：原真实表发现、候选勾选和 `changeSelectedTables`。
3. `DevelopmentArtifactsPanel → EntityInfoPanel → EntityContentTabs → EntityTablePanel → EntityTableConfirmation`：原创建/重建确认、执行与真实结构核验。

改动文件按职责如下（均为已有文件，纯判断模块与测试除外）：

| 范围 | 文件 |
| --- | --- |
| 状态与提示 | `FieldMapping/databasePrerequisite.ts`（新增）、`index.tsx`、`index.less` |
| 只读加载状态 | `FieldMapping/bindingSources.ts`、`useBindingWorkspace.ts` |
| 导航与返回上下文 | `AiChatPanel.tsx`、`hooks/useDevelopmentArtifactDetail.ts` |
| 实体页签和返回按钮 | `DevelopmentArtifactsPanel/index.tsx`、`DevelopmentArtifactsPanel.less`、`EntityInfoPanel/index.tsx`、`EntityContentTabs.tsx` |
| 抽屉返回按钮 | `DataSourcesPage/DataSourcesDrawer.tsx`、`DataSourcesDrawer.less` |
| 回归 | `Frontend/tests/databasePrerequisite.test.ts`（新增）、`apiDesign.test.ts` |
| 文档 | `docs/CODEBASE_INDEX.md`、本记录 |

未新增连接表单、选表器、建表弹窗或第二套 HTTP/AG-UI 服务；Backend、SaaS、模板、Graph、Build 与正式映射保存/确认协议无改动。

## 验证及限制

- `pnpm test:api-design`：58 项通过，0 失败；覆盖模式互斥、有效实体关联与多实体选择、加载/失败/恢复、实际 Hook 的草稿保留、只读动作与工作区事件隔离；已有 `bindingSources.test.ts` 覆盖迟到响应和卸载失效。测试包含预期的模拟网络失败日志，最终 TAP 全通过。
- `pnpm build`：通过，包含 TypeScript 和 Electron/Vite 打包；保留原 AG-UI 动态/静态导入提示。
- `curl --max-time 10 -sS http://127.0.0.1:8000/health`：通过，`status=ok`、`app_env=dev`。
- 沙箱内首次健康检查无法连接；pnpm 测试首次因无法访问注册表而在切换项目要求的 9.15.9 时失败。均已在沙箱外重试成功，未修改包管理器或签名校验配置。
- Electron 实际交互、明暗主题视觉验收及可销毁数据库端到端建表/选表尚未完成。本次本机 UI 控制服务报 `Sky Computer Use service startup request failed`，无法连接已运行的 Electron；没有用浏览器页面替代，也未操作未被指定为可销毁的真实数据库。

以上未完成项不视为验收通过；本次测试没有替代完整 Electron 组件交互验收。
