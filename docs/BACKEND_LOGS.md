# Python 后端实时日志

进入应用工作台后，点击右上角主题按钮右侧的 **设置 → 实时日志**。右侧抽屉占应用窗口宽度的 75%，用于查看平台 Python 后端日志。生成项目的 Java/Vite 日志仍在预览区的“服务状态”查看。

## 查看与操作

日志框底部提供常显水平滚动条，可拖动查看超长日志；滚动条沿用明暗主题颜色。

- 抽屉打开时立即读取日志，默认开启“最新在前”和“自动刷新”；自动刷新约每秒检查一次变化。
- 模型流式输出在未换行、未完成调用时即可显示，并发调用按各自身份合并。条目倒序不改变正文、代码及异常堆栈内部的顺序。
- 搜索在当前加载的日志窗口中按不区分大小写的字面量筛选、高亮。复制按钮使用原生 Electron 剪贴板，复制当前筛选和排序下可见的完整文本。
- 阅读历史位置时保留滚动锚点；停留在最新位置时自动跟随。关闭自动刷新后可以手动刷新，关闭抽屉或离开工作台会取消订阅。
- 后端断线时保留已读取内容并展示错误；自动刷新开启时重试连接。日志文件不可写时保留内存查看并报告存储错误。

## 内容与存储

采集现有 Python logging、stdout 和 stderr，包括 Uvicorn 服务/访问日志、业务日志、异常堆栈和已启用的模型日志。Uvicorn 的 stderr 不直接等同于 ERROR；结构化 logging 保留真实级别，无法确定级别的 stderr 标为 OUTPUT。调试接口自己的访问日志不进入查看窗口，避免查看行为制造持续噪音。

模型正文、thinking 及工具调用仍受原 `MODEL_OUTPUT_LOG_ENABLED` 控制，默认值不变。工具调用保留原来已记录的字段，只改为单行紧凑 JSON，字符串内部的换行以 `\n` 转义。LangChain 路径记录现有工具参数，独立 LlmClient 路径仍仅记录原有 ID 和名称，不额外采集工具结果。

日志位于 `~/<DEVAGENTSTUDIO_WORKING_DIR>/logs/backend.log`。各环境沿用现有用户目录隔离，不写入安装目录或业务工作区。文件使用 UTF-8 JSONL，每行保存一个带逻辑条目身份的脱敏文本片段；同一模型调用的片段在界面合并，文件属于内部诊断记录。单文件 10 MiB，保留 `.1` 到 `.5` 五份归档。正常退出保存尚未完成的尾部；运行时每 200 毫秒保存可安全脱敏的长行前缀，跨写入边界的敏感尾部暂存内存。

界面最多显示最近 1 MiB / 5000 行，截断时提示查看日志文件。重启仅从当前文件和归档的有界尾部恢复当前日志格式。文件、快照和复制文本均不包含已知敏感环境值及可识别的密码、密钥、认证头或连接串凭据；原开发控制台输出行为保持不变。

## AG-UI 合同

- `POST /backend-logs/run`，通过标准 AG-UI SDK 访问。
- 动作输入：`forwardedProps.backendLogs.action` 为 `get` 或 `watch`；不接受客户端文件路径或工作区。
- Custom 事件名：`backend-logs`；StateSnapshot/RunFinished 状态键：`backendLogs`。
- 日志投影：`logs.entries`、`filePath`、`updatedAt`、`revision`、`truncated`、`storageError`。条目包含 `id`、`timestamp`、`source`、`level` 和 `text`。
- `watch` 单次约 20 秒，有变化才通过公共进度事件推送，结束后客户端续订。成功与可处理失败均遵循完整的 AG-UI 运行、消息、结果、状态和结束生命周期。
- 接口只读且不登记业务工作区执行，不改变 Workflow、正式产物或确认门。`/health.tools.backend_logs` 发布当前能力。

## 验证入口

Backend：`.venv/bin/python -m unittest tests.test_backend_logs tests.test_model_output_logger tests.test_ag_ui_action_stream tests.test_app_update`。

Frontend：`pnpm test:backend-logs`、`pnpm test:service-logs`、相关文件 ESLint、`pnpm typecheck` 和 `pnpm build`。UI 检查使用 Electron，安装包验证需在对应 Windows/macOS 平台运行。
