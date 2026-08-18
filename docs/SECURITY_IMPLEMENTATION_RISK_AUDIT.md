# 工程风险审计结论

结论很明确：**如果该工程要携带真实模型密钥、数据库凭证并分发给真实用户，目前应判定为“发布阻断”状态。**

最严重的问题不是某个孤立漏洞，而是一条完整的信任边界穿透链：

> 不可信网页 / Renderer XSS / 模型生成代码 / 本机恶意进程  
> → 无鉴权本地后端或宽 IPC  
> → 任意选择工作区、自助批准  
> → 读取敏感文件、执行主机命令、窃取构建包内密钥

我对关键结论做了静态正向追踪、反向搜索、邻近安全实现对照，以及无害临时目录复现。没有读取或输出真实 `.env` 内容，也没有修改工程代码。

---

## P0：Critical，建议立刻阻断发布

### 1. 本地后端没有认证，且调用者能自行指定任意工作区根目录

**证据：**

- 文件、搜索、终端、Git、审批等高权限路由直接公开在 [Backend/app/main.py:247](C:/XCodeAgent/Backend/app/main.py:247)。
- `/tools/approvals/{id}/approve` 和 `/reject` 同样由调用方直接访问，[Backend/app/main.py:304](C:/XCodeAgent/Backend/app/main.py:304)。
- CORS 接受任意端口的 `localhost`、`127.0.0.1`、`[::1]`，以及 `null` Origin，并允许所有方法和 Header，[Backend/app/main.py:369](C:/XCodeAgent/Backend/app/main.py:369)。
- 全局搜索没有找到保护这些路由的 Bearer、Session、API key 或认证中间件。
- 请求能够传入 `workspace_root`，[Backend/app/workspace.py:87](C:/XCodeAgent/Backend/app/workspace.py:87)。
- `_workspace_root()` 只检查目录是否存在，没有限制其必须处于 XCodeAgent 管理目录内，[Backend/app/workspace.py:809](C:/XCodeAgent/Backend/app/workspace.py:809)。
- `_safe_path()` 确实限制了相对路径不能逃出所选根目录，但这个根目录本身由调用方选择，[Backend/app/workspace.py:819](C:/XCodeAgent/Backend/app/workspace.py:819)。

**影响：**

任意本机进程，以及满足 CORS 条件的浏览器页面，都可能把 `C:\`、用户目录、其他仓库或应用数据目录当作“工作区”。随后使用文件、搜索、Git、终端接口进行操作。

默认监听 `127.0.0.1` 是一层缓解，但 **localhost 不是身份认证边界**。若部署时通过环境变量改为非环回地址，风险会进一步变成网络可达。

**建议：**

1. Electron 启动后端时生成高熵、单次会话 capability，由所有敏感路由强制校验。
2. 不再接受原始 `workspace_root`；由可信主进程注册工作区，前端只传服务端生成的 opaque workspace ID。
3. 明确拒绝系统根目录、用户主目录、应用自身数据目录和平台凭据目录。
4. 浏览器 Renderer 不应直接访问后端；优先由 Electron Main 代理。
5. 非环回监听必须显式禁用，除非另行实现 TLS 和完整身份认证。

---

### 2. 审批机制可以“自助审批”，没有建立人机边界

**证据：**

- 创建审批后返回公开 ID，[Backend/app/approvals.py:53](C:/XCodeAgent/Backend/app/approvals.py:53)。
- 任何拿到 ID 的调用方都能调用 `approve(id)`，并直接获得 token，[Backend/app/approvals.py:83](C:/XCodeAgent/Backend/app/approvals.py:83)。
- operation grant 没有过期时间，[Backend/app/approvals.py:89](C:/XCodeAgent/Backend/app/approvals.py:89)。
- `is_operation_approved()` 只按操作名检查持久 grant，[Backend/app/approvals.py:153](C:/XCodeAgent/Backend/app/approvals.py:153)。

我用临时 ApprovalStore 做了无害复现：创建 pending ID 后，同一个调用方能够批准、拿 token 并成功消费。

**影响：**

这里的 HITL 审批本质上只是 API 流程步骤，不是安全控制。攻击方可以自己请求敏感操作、自己批准，再执行操作。

**建议：**

- 审批只能来自可信 Electron Main 的用户手势，后端公共路由不能提供 approve 能力。
- 审批必须绑定：
  - 用户/窗口；
  - thread/run；
  - workspace ID；
  - 操作类型；
  - 参数摘要或文件 diff hash；
  - 短 TTL；
  - 单次消费。
- 参数、工作区或 diff 发生变化后自动作废。

---

### 3. 终端风险分类器可以被解释器和包装器轻易绕过

**证据：**

- `_classify_command()` 使用短 denylist 判断风险，[Backend/app/workspace.py:1384](C:/XCodeAgent/Backend/app/workspace.py:1384)。
- 实际执行使用 `shell=False`，这避免了常见分号 shell 注入，但仍可执行任意可执行文件，[Backend/app/workspace.py:702](C:/XCodeAgent/Backend/app/workspace.py:702)。
- `TerminalExecRequest.approved` 字段被定义，但没有进入最终授权决策，[Backend/app/workspace.py:172](C:/XCodeAgent/Backend/app/workspace.py:172)。

实际最小复现中，这些命令都被分类为 `low`：

- `python -c ...`
- `node -e ...`
- `curl ...`

因此攻击者不需要直接写 `del`、`Remove-Item` 或其他 denylist 命令；解释器、下载器、脚本宿主都可以完成同等操作。

**建议：**

- 删除通用 HTTP 终端，或改为结构化工具。
- 未知命令默认高风险/拒绝，而不是默认低风险。
- 禁止 Python、Node、PowerShell、cmd、bash/sh、curl 等通用解释器和包装器。
- 在低权限 OS 账号、容器或 Job Object 中执行，默认无网络、干净环境变量、只读挂载。
- 强制总超时、进程树终止、输出上限和并发配额。

---

### 4. Agent 的 `execute` 工具完全绕过工作区、审批和命令安全策略

**证据：**

- 文件注释明确写着绕过既有执行边界，[Backend/app/tools/execute.py:1](C:/XCodeAgent/Backend/app/tools/execute.py:1)。
- 工具接受任意 command，[Backend/app/tools/execute.py:27](C:/XCodeAgent/Backend/app/tools/execute.py:27)。
- 使用 `subprocess.run(..., shell=True)`，没有有效总超时，且完整捕获输出，[Backend/app/tools/execute.py:45](C:/XCodeAgent/Backend/app/tools/execute.py:45)。
- 该工具被直接提供给：
  - [frontend agent](C:/XCodeAgent/Backend/app/agents/frontend/agent.py:52)
  - [data source agent](C:/XCodeAgent/Backend/app/agents/data_source/agent.py:49)
  - [small task agent](C:/XCodeAgent/Backend/app/agents/small_task/agent.py:58)
- SmallTask 虽有路径范围实现，[Backend/app/agents/small_task/scope.py:72](C:/XCodeAgent/Backend/app/agents/small_task/scope.py:72)，但 `execute` 不经过该范围检查。
- direct modification 还会重新补入这个工具，[Backend/app/agents/direct_modification.py:44](C:/XCodeAgent/Backend/app/agents/direct_modification.py:44)。
- 当前安装的 DeepAgents 会自动添加 general-purpose subagent，并继承父 Agent 工具，[deepagents/graph.py:691](C:/XCodeAgent/Backend/.venv/Lib/site-packages/deepagents/graph.py:691)。

**影响：**

仓库文件、日志、Skill 内容或用户输入中的 prompt injection，可能诱导 Agent 在宿主机执行命令，访问工作区外文件、环境变量和网络。提示词中写“只能访问某路径”无法代替工具层强制边界。

**建议：**

- 删除当前 `shell=True` 实现。
- 所有 Agent 必须共用同一套不可绕过的 Tool Policy 中间件。
- 只允许固定 argv 模板，不接收完整命令字符串。
- 显式关闭自动 general-purpose subagent，或只给它最小只读工具。
- 为每次 run 设置总 tool 次数、wall time、token、子 Agent 数量和输出预算。
- 将大量日志落盘，主上下文只返回摘要和文件引用。

---

### 5. 敏感文件保护可以通过 search 和 dry-run 泄漏

**证据：**

- write 在授权前读取旧文件并生成 diff，且 dry-run 时不需要审批，[Backend/app/workspace.py:413](C:/XCodeAgent/Backend/app/workspace.py:413)。
- patch 同样会先读取并返回旧内容差异，[Backend/app/workspace.py:491](C:/XCodeAgent/Backend/app/workspace.py:491)。
- delete dry-run 会把被删除文件完整放入 diff，[Backend/app/workspace.py:588](C:/XCodeAgent/Backend/app/workspace.py:588)。
- search 没有执行敏感文件检查，[Backend/app/workspace.py:691](C:/XCodeAgent/Backend/app/workspace.py:691)。
- `rg` 搜索包含隐藏文件，[Backend/app/workspace.py:1201](C:/XCodeAgent/Backend/app/workspace.py:1201)。
- 敏感文件列表是少量、区分大小写的精确文件名，[Backend/app/workspace.py:36](C:/XCodeAgent/Backend/app/workspace.py:36)。

我在临时目录放置了假 sentinel：

- `search_text(include_hidden=True)` 能搜到 `.env` 中的 sentinel。
- write、patch、delete 的 dry-run 都能从 diff 返回原 sentinel。

没有读取真实仓库 `.env`。

**影响：**

即使正式写入和删除要求审批，调用方仍可通过 dry-run 和搜索读取：

- `.env`
- `.env.staging`
- `.ENV`
- 私钥、PFX/PEM
- 云平台凭据
- 数据库密钥文件

**建议：**

- 将敏感路径检查放在任何读取、hash、diff、搜索之前。
- dry-run 与真实操作使用同一权限模型。
- 规则使用大小写归一化和 glob，并覆盖 `.env.*`、证书、SSH、云配置、平台密钥目录。
- 搜索默认跳过敏感和隐藏文件。
- 即使获批，也应对返回内容做 secret redaction。

---

### 6. 构建流程会把真实 `.env` 复制进安装包

**证据：**

- Windows 构建脚本复制后端 `.env`，[scripts/build-backend-win.ps1:41](C:/XCodeAgent/scripts/build-backend-win.ps1:41)、[scripts/build-backend-win.ps1:102](C:/XCodeAgent/scripts/build-backend-win.ps1:102)。
- macOS 构建脚本行为相同，[scripts/build-backend-mac.sh:35](C:/XCodeAgent/scripts/build-backend-mac.sh:35)。
- electron-builder 将对应资源目录打包，[Frontend/electron-builder.yml:16](C:/XCodeAgent/Frontend/electron-builder.yml:16)。
- 打包验证脚本还要求 `.env` 必须存在，[scripts/verify-packaged-backend.ps1:20](C:/XCodeAgent/scripts/verify-packaged-backend.ps1:20)。
- 后端启动时会加载旁边的 `.env`，[Backend/packaging/backend_server.py:36](C:/XCodeAgent/Backend/packaging/backend_server.py:36)。

当前工作区实际存在被 Git 忽略的 `Frontend/resources/backend/win32/.env`，大小约 710 字节。我没有打开它，也没有判断其中是否包含真实值。

**影响：**

如果其中包含模型供应商 API key，安装包的任何获得者都能从资源中提取。ASAR、代码签名和安装器都不能保护静态密钥。

**建议：**

1. 立即从构建资源中移除 `.env`。
2. 对曾进入任何对外安装包的密钥执行轮换。
3. 用户级密钥放入 Credential Manager、Keychain 等 OS vault。
4. 如果是产品共享密钥，改为服务端代理和短期令牌。
5. CI 对最终安装包做 secret scan，而不只是扫描 Git 仓库。

---

### 7. 模型生成 TSX 在同源、宽 sandbox 的 iframe 中执行

**证据：**

- 模型生成的页面代码写入 `page.code`，[Backend/app/agents/frontend/subagents/ui_design_generator.py:423](C:/XCodeAgent/Backend/app/agents/frontend/subagents/ui_design_generator.py:423)。
- 验证器主要检查 imports、未定义符号和语法，没有阻止 `window`、`parent`、存储或其他浏览器 API，[ui_design_generator.py:826](C:/XCodeAgent/Backend/app/agents/frontend/subagents/ui_design_generator.py:826)。
- 前端将代码传入实时预览，[Frontend/src/renderer/src/components/UiDesignStreamingPreview.tsx:110](C:/XCodeAgent/Frontend/src/renderer/src/components/UiDesignStreamingPreview.tsx:110)。
- TSX 编译器只改写模块加载，[Frontend/src/renderer/src/utils/compileTsx.ts:42](C:/XCodeAgent/Frontend/src/renderer/src/utils/compileTsx.ts:42)。
- Renderer iframe 使用根路径资源，[Frontend/src/renderer/src/components/design-runtime/DesignRenderer.tsx:43](C:/XCodeAgent/Frontend/src/renderer/src/components/design-runtime/DesignRenderer.tsx:43)。
- sandbox 同时开放 `allow-same-origin`、`allow-scripts`、`allow-popups` 和表单，[DesignRenderer.tsx:195](C:/XCodeAgent/Frontend/src/renderer/src/components/design-runtime/DesignRenderer.tsx:195)。
- frame 使用 `new Function` 执行代码，[Frontend/public/design-runtime/design-frame.html:35](C:/XCodeAgent/Frontend/public/design-runtime/design-frame.html:35)。
- preload 给 Renderer 暴露了 Electron 和 XCodeAgent 桥，[Frontend/src/preload/index.ts:15](C:/XCodeAgent/Frontend/src/preload/index.ts:15)。
- 主窗口还关闭了 Chromium sandbox，[Frontend/src/main/index.ts:1635](C:/XCodeAgent/Frontend/src/main/index.ts:1635)。

**影响：**

开发模式下，生成代码处于同源环境，可能访问 parent DOM、localStorage 和 preload 暴露接口。这是“模型输出 → Renderer 权限 → IPC/后端 → 主机”的关键链路。

**额外验证：**

生产 `file://` 模式目前还存在实现错误：`/design-runtime/design-frame.html` 会解析为 `file:///C:/design-runtime/design-frame.html`，而不是安装包资源路径。因此生产包很可能先表现为预览失效。**这不是安全修复**；以后修路径时若不同时隔离执行环境，P0 风险会立即在生产恢复。

**建议：**

- 不再执行任意 JS/TSX；优先让模型输出受限组件 DSL/JSON AST。
- 如果必须执行，放入独立进程或独立 WebContentsView：
  - 唯一 opaque origin；
  - 无 preload；
  - 无 `allow-same-origin`；
  - 临时 session partition；
  - 禁止导航、下载、权限、弹窗；
  - postMessage 使用 nonce、严格 schema 和精确 origin。
- 模型生成页面绝不能与主 Renderer 共用 DOM、存储和 IPC 能力。

---

### 8. Workflow 可以绕过正式文档确认和质量节点，并能注入任意产物路径

**证据：**

- 请求可直接设置 `resumeFrom`，[Backend/app/workflow/request.py:111](C:/XCodeAgent/Backend/app/workflow/request.py:111)。
- 该值不依赖 debug 真正启用；我复现了 `debug=false` 时接受 `finalize_project` 和 `launch_project`。
- 可跳转节点包括 build、integration、launch、acceptance、finalize，[Backend/app/workflow/request.py:648](C:/XCodeAgent/Backend/app/workflow/request.py:648)。
- 图从 START 直接按 resume 路由，[Backend/app/graph/workflow.py:22](C:/XCodeAgent/Backend/app/graph/workflow.py:22)。
- 文档确认检查集中在 task 节点，[Backend/app/graph/nodes/tasks.py:66](C:/XCodeAgent/Backend/app/graph/nodes/tasks.py:66)，launch/finalize 没有重新验证整个确认和质量不变量，[Backend/app/graph/nodes/lifecycle.py:6](C:/XCodeAgent/Backend/app/graph/nodes/lifecycle.py:6)。
- 请求恢复状态可以携带文件路径，[Backend/app/workflow/request.py:671](C:/XCodeAgent/Backend/app/workflow/request.py:671)。
- 路径处理会保留绝对路径，[Backend/app/workflow/request.py:1038](C:/XCodeAgent/Backend/app/workflow/request.py:1038)。
- 文档写入器直接使用这些 state path：
  - [spec_documents.py:151](C:/XCodeAgent/Backend/app/workflow/spec_documents.py:151)
  - [plan_documents.py:655](C:/XCodeAgent/Backend/app/workflow/plan_documents.py:655)
  - [task_documents.py:11](C:/XCodeAgent/Backend/app/workflow/task_documents.py:11)

我复现确认 `C:/Windows/Temp/spec.md` 和 `out.md` 会原样保留。

**影响：**

- 可绕过 RequirementSpec、ProjectPlan 和后续正式产物确认。
- 可跳过测试、验收或质量节点直接 launch/finalize。
- 可让工作流在管理工作区之外读写正式文档。

**建议：**

- 从公共请求契约中移除 `resumeFrom`。
- 恢复位置只能由服务端 checkpoint 的 pending interaction 决定。
- debug 跳转只能由进程级开发配置打开，并要求 Main capability。
- 每个下游节点重新验证：产物 revision/hash 已确认、确认属于当前 run、质量和验收已完成。
- 客户端不得提供物理路径，只传 artifact ID；物理路径由服务端生成并做 canonical containment/no-follow 检查。

---

## P1：High，建议 1～2 个迭代内完成

### 9. Electron IPC 没有校验调用者，Renderer 可直接传入文件路径和破坏性参数

**证据：**

- 主进程没有找到 `senderFrame`、`event.sender.id` 或可信 `webContents.id` 校验。
- 大量 handler 忽略 `_event`，[Frontend/src/main/index.ts:727](C:/XCodeAgent/Frontend/src/main/index.ts:727)。
- `resolveWorkspaceRoot()` 只做 `path.resolve`，[Frontend/src/main/index.ts:863](C:/XCodeAgent/Frontend/src/main/index.ts:863)。
- preload 直接发送原始路径，[Frontend/src/preload/index.ts:34](C:/XCodeAgent/Frontend/src/preload/index.ts:34)。
- 创建工程允许在任意现有目录写入 marker，[Frontend/src/main/index.ts:1217](C:/XCodeAgent/Frontend/src/main/index.ts:1217)。
- 删除工程主要依赖这个 marker 判断归属，[Frontend/src/main/index.ts:659](C:/XCodeAgent/Frontend/src/main/index.ts:659)。
- clone 流程会删除已有 frontend/backend 目录，[Frontend/src/main/index.ts:1252](C:/XCodeAgent/Frontend/src/main/index.ts:1252)。
- preload 还暴露通用 `electronAPI`，[Frontend/src/preload/index.ts:88](C:/XCodeAgent/Frontend/src/preload/index.ts:88)。

**影响：**

一旦 Renderer 被 XSS、生成代码或导航页面控制，攻击者可以伪造 marker、调用删除、clone、session、token 等 IPC。

**建议：**

- 移除通用 invoke/send 能力，按窗口提供最小 preload。
- 每个 IPC 校验调用 WebContents ID 和精确 URL。
- 路径必须来自可信目录选择器生成的 capability，不能接收任意字符串。
- 工程 ownership 使用随机不可伪造 token，而不是仅靠可写 marker。
- clone 到临时目录，验证后原子 rename；已有目录不自动递归删除。

---

### 10. 主窗口/登录窗口导航隔离不足，外部 URL 处理不统一

**证据：**

- 主窗口和登录窗口均 `sandbox: false`，[Frontend/src/main/index.ts:1635](C:/XCodeAgent/Frontend/src/main/index.ts:1635)、[Frontend/src/main/index.ts:1683](C:/XCodeAgent/Frontend/src/main/index.ts:1683)。
- 两者使用带宽 IPC 的 preload。
- 没有找到主窗口 `will-navigate`/`will-redirect` 白名单。
- `setWindowOpenHandler` 某些路径直接传给 `shell.openExternal`，[Frontend/src/main/index.ts:1667](C:/XCodeAgent/Frontend/src/main/index.ts:1667)。
- URL 归一化只应用在部分直接 IPC，[Frontend/src/main/index.ts:752](C:/XCodeAgent/Frontend/src/main/index.ts:752)。
- BrowserPreview iframe 允许 popup 逃出 sandbox，[Frontend/src/renderer/src/components/BrowserPreviewPanel.tsx:269](C:/XCodeAgent/Frontend/src/renderer/src/components/BrowserPreviewPanel.tsx:269)。

**建议：**

- 主窗口和登录窗口启用 Chromium sandbox。
- 登录窗口使用单独、极小 preload。
- 主窗口导航严格限制为打包页面或开发服务器地址。
- 所有外链统一要求 `https`；开发回环地址作为显式例外。
- 拒绝 `file:`、自定义 scheme、嵌入用户名密码的 URL。
- Preview 使用临时 partition，并默认拒绝权限、下载、弹窗和导航。

---

### 11. Clone、依赖安装和项目启动构成不可信供应链执行

**证据：**

- Renderer 能传任意模板仓库 URL，clone 时未固定 commit/hash，[Frontend/src/main/index.ts:1252](C:/XCodeAgent/Frontend/src/main/index.ts:1252)。
- Git 使用 `execFile` argv，因此不是 shell 注入；问题是信任和供应链。
- 前端安装依赖没有默认 `--ignore-scripts`，[Backend/app/project_launchers/frontend_project_launcher.py:326](C:/XCodeAgent/Backend/app/project_launchers/frontend_project_launcher.py:326)。
- 随后直接运行包内脚本，[frontend_project_launcher.py:380](C:/XCodeAgent/Backend/app/project_launchers/frontend_project_launcher.py:380)。
- 子进程继承宿主环境变量，[frontend_project_launcher.py:443](C:/XCodeAgent/Backend/app/project_launchers/frontend_project_launcher.py:443)。
- 后端会优先执行项目自带 `mvnw`，[Backend/app/project_launchers/backend_project_launcher.py:116](C:/XCodeAgent/Backend/app/project_launchers/backend_project_launcher.py:116)。
- Maven 构建和启动也继承环境，并注入数据库参数，[backend_project_launcher.py:560](C:/XCodeAgent/Backend/app/project_launchers/backend_project_launcher.py:560)。

**影响：**

模板仓库的 lifecycle script、构建插件、wrapper 脚本可在宿主机执行，并读取模型、LangSmith、数据库等环境变量。

**建议：**

- 模板必须 allowlist/signature/commit pin/hash 校验。
- frozen lockfile 安装，默认禁用 lifecycle scripts。
- 运行前展示来源、commit 和脚本摘要并要求可信确认。
- 安装和构建放在低权限沙箱，默认无宿主环境变量、无宿主文件系统、限制网络。
- 密钥绝不继承给项目子进程。

---

### 12. 数据库凭证存在多条泄漏路径，且默认连接关闭 TLS

**证据：**

- RSA 私钥写入用户工作目录，使用 `NoEncryption`，[Backend/app/database_crypto.py:44](C:/XCodeAgent/Backend/app/database_crypto.py:44)、[database_crypto.py:142](C:/XCodeAgent/Backend/app/database_crypto.py:142)。
- Windows 权限限制基本是空操作，[database_crypto.py:231](C:/XCodeAgent/Backend/app/database_crypto.py:231)。
- 数据源配置工具会返回包含明文 password 的结构，[Backend/app/agents/data_source/mysql_info.py:289](C:/XCodeAgent/Backend/app/agents/data_source/mysql_info.py:289)。
- 该工具被绑定给模型 Agent，[Backend/app/agents/data_source/agent.py:49](C:/XCodeAgent/Backend/app/agents/data_source/agent.py:49)。
- Skill 还要求模型把数据库配置写入 `application.yml`，[Backend/app/builtin_skills/data_source/SKILL.md:111](C:/XCodeAgent/Backend/app/builtin_skills/data_source/SKILL.md:111)。
- JDBC URL 使用 `useSSL=false`，[Backend/app/database_credentials.py:149](C:/XCodeAgent/Backend/app/database_credentials.py:149)。
- PyMySQL 连接没有 TLS 配置，[Backend/app/database_execution.py:175](C:/XCodeAgent/Backend/app/database_execution.py:175)。

**影响：**

密码可能进入模型请求、Tracing、工具消息、生成配置、Git diff；连接远程数据库时还可能被中间人截获。

**建议：**

- 模型只接触 opaque credential reference，不返回明文。
- 生成配置只写 `${SPRING_DATASOURCE_PASSWORD}` 等占位符。
- 密码由可信启动器通过短期环境或进程专用 secret mount 注入。
- 私钥放入 DPAPI/Credential Manager/Keychain，轮换当前本地密钥。
- 远程数据库强制 TLS 和证书验证。

---

### 13. UI 所谓“加密环境变量”实际上仍以明文保存

**证据：**

- Settings 页面将环境变量作为 password 输入，但保存原值，[Frontend/src/renderer/src/pages/SettingsPage.tsx:210](C:/XCodeAgent/Frontend/src/renderer/src/pages/SettingsPage.tsx:210)。
- `databaseCredentialCrypto` 只加密 plantMode 密码，[Frontend/src/renderer/src/utils/databaseCredentialCrypto.ts:118](C:/XCodeAgent/Frontend/src/renderer/src/utils/databaseCredentialCrypto.ts:118)。
- application 数据进入 localStorage，[Frontend/src/renderer/src/stores/applicationStorage.ts:44](C:/XCodeAgent/Frontend/src/renderer/src/stores/applicationStorage.ts:44)。
- 主进程还会把 applications 对象写入 JSON，[Frontend/src/main/index.ts:649](C:/XCodeAgent/Frontend/src/main/index.ts:649)。
- 现有测试明确断言 environment 不发生加密，[Frontend/scripts/run-database-credential-crypto-tests.mjs:88](C:/XCodeAgent/Frontend/scripts/run-database-credential-crypto-tests.mjs:88)。

**建议：**

环境配置中只存 secret reference；真实值存 OS vault。localStorage、applications JSON、session 导出和日志中均不得出现明文。

---

### 14. thread/run/checkpoint 没有绑定用户、工作区和调用主体

**证据：**

- 客户端可提供 thread ID 和 run ID，[Backend/app/workflow/runtime.py:153](C:/XCodeAgent/Backend/app/workflow/runtime.py:153)。
- checkpoint 主键主要使用 thread ID，[Backend/app/workflow/runtime.py:294](C:/XCodeAgent/Backend/app/workflow/runtime.py:294)。
- 活跃运行 registry 仅按 run ID 管理，重复 ID 可覆盖，[Backend/app/run_control.py:27](C:/XCodeAgent/Backend/app/run_control.py:27)。
- cancel 只需目标 run ID，[Backend/app/run_control.py:57](C:/XCodeAgent/Backend/app/run_control.py:57)。
- planning recovery 接受 workspace 和 thread ID 后读取状态，[Backend/app/application_page_planning.py:103](C:/XCodeAgent/Backend/app/application_page_planning.py:103)。
- checkpoint 默认以明文 SQLite 落盘，[Backend/app/workflow/checkpoints.py:17](C:/XCodeAgent/Backend/app/workflow/checkpoints.py:17)。
- 等待用户输入的 checkpoint 不会及时清理，[Backend/app/workflow/checkpoints.py:81](C:/XCodeAgent/Backend/app/workflow/checkpoints.py:81)。

**影响：**

在缺少认证的前提下，可能产生跨会话恢复、取消、覆盖或读取历史状态。checkpoint 还可能长期保存模型消息、工具输出和敏感参数。

**建议：**

使用 `{principal, workspace, workflow, thread, run}` 复合命名空间；run ID 服务端生成；重复活跃 ID 必须拒绝；恢复和取消要求 owner capability；checkpoint 做加密、字段清洗、大小/数量/时间配额和后台清理。

---

### 15. “资源锁”目前只是观察信息，不提供并发互斥

**证据：**

- run lease 注释明确说明不阻断资源交叉，[Backend/app/run_lease.py:42](C:/XCodeAgent/Backend/app/run_lease.py:42)。
- 生命周期锁采用 latest writer wins，[Backend/app/application_lifecycle.py:658](C:/XCodeAgent/Backend/app/application_lifecycle.py:658)。
- 前端执行模式明确忽略锁冲突，[Frontend/src/renderer/src/utils/planExecutionMode.ts:150](C:/XCodeAgent/Frontend/src/renderer/src/utils/planExecutionMode.ts:150)。
- 测试也把并发重叠视为预期行为，[Backend/tests/test_workspace_run_lease.py:168](C:/XCodeAgent/Backend/tests/test_workspace_run_lease.py:168)。
- 自动去重写文件使用 truncate/last-write-wins，[Backend/app/agents/auto_dedup_backend.py:67](C:/XCodeAgent/Backend/app/agents/auto_dedup_backend.py:67)。

**影响：**

多个工作流可能同时改正式文档、共享配置、路由、数据源、checkpoint 或相同代码文件，引发静默丢更新和不一致构建。

**建议：**

- 资源 claim 必须真正排他，冲突时排队或拒绝。
- 使用 TTL、heartbeat 和 fencing token。
- 文件修改使用 revision/hash CAS。
- 并行页面通过隔离 worktree/staging 产生结果，再进入受控 merge。
- 共享 API、路由、配置和数据库资源应升级为冲突资源。

---

### 16. 前端 PID 文件可导致误杀其他同用户进程

**证据：**

- 前端启动时读取工作区内 PID 文件，[Backend/app/project_launchers/frontend_project_launcher.py:95](C:/XCodeAgent/Backend/app/project_launchers/frontend_project_launcher.py:95)。
- 只要 PID 是正数且正在运行，就会尝试终止，没有验证 exe、cwd、命令行或启动时间，[frontend_project_launcher.py:606](C:/XCodeAgent/Backend/app/project_launchers/frontend_project_launcher.py:606)。
- 终止逻辑位于 [frontend_project_launcher.py:684](C:/XCodeAgent/Backend/app/project_launchers/frontend_project_launcher.py:684)。
- 后端进程注册表反而已经实现命令行和 workspace jar 身份校验，可作为安全对照，[Backend/app/project_launchers/backend_process_registry.py:107](C:/XCodeAgent/Backend/app/project_launchers/backend_process_registry.py:107)。

**建议：**

PID registry 放在工作区外受保护目录，记录 PID、进程启动时间、exe、cwd/command hash 和随机 nonce。任何身份字段不匹配时应拒绝 kill。

---

### 17. SQL 风险分类器同样是短正则 denylist，可绕过

**证据：**

- 风险模式定义于 [Backend/app/database_execution.py:17](C:/XCodeAgent/Backend/app/database_execution.py:17)。
- 分类逻辑位于 [database_execution.py:76](C:/XCodeAgent/Backend/app/database_execution.py:76)。
- 执行路径根据该分类决定是否审批，[database_execution.py:205](C:/XCodeAgent/Backend/app/database_execution.py:205)。

实际复现中以下语句全部被标为低风险：

- `DROP VIEW ...`
- `GRANT ALL ...`
- `DELETE ... WHERE 1=1`
- `UPDATE ... WHERE 1=1`
- `DROP/**/TABLE ...`

**建议：**

使用 MySQL 方言 AST parser；默认只允许单条、只读 `SELECT`；DDL/DML/DCL、无法解析语句、多语句、注释规避和可疑恒真条件默认高风险或拒绝。数据库账号本身还必须最小权限。

---

### 18. 默认工作区根目录计算错误，且 projectId 可形成路径逃逸

**证据：**

- 默认根目录通过 `Path(__file__).parents[4] / "var" / "workspaces"` 计算，[Backend/app/workflow/spec_documents.py:8](C:/XCodeAgent/Backend/app/workflow/spec_documents.py:8)。
- 在当前目录层级中，`parents[4]` 实际得到 `C:\`，不是 `C:\XCodeAgent`。
- 实测：
  - `REPOSITORY_ROOT = C:\XCodeAgent`
  - `WORKSPACES_BASE = C:\`
  - 默认 demo 工作区 = `C:\var\workspaces\demo`
- 绝对 project ID 会让路径直接变成其自身，例如 `C:\Windows\Temp\audit`。
- project ID 只按字符串处理，没有 slug/UUID 约束，[Backend/app/workflow/request.py:328](C:/XCodeAgent/Backend/app/workflow/request.py:328)。

完整后端测试中的多项 `C:\var` PermissionError 也从另一方向验证了这一点。

**建议：**

- 明确使用 `REPOSITORY_ROOT / "var" / "workspaces"`。
- project ID 仅接受固定格式 slug/UUID。
- 拒绝绝对路径、`.`、`..` 和路径分隔符。
- 拼接后 `resolve()` 并使用 `relative_to(WORKSPACES_BASE)` 验证。

---

### 19. 前端依赖存在较大已知漏洞和版本生命周期风险

对当前锁文件执行：

```text
pnpm audit --prod --registry=https://registry.npmjs.org/
```

结果：

- 287 个被分析依赖
- 54 个漏洞
- 14 high
- 34 moderate
- 6 low
- 0 critical

直接依赖中的重点：

- `axios 1.17.0`
- `compressing 1.10.3`
- `js-yaml 4.1.1`
- `electron 37.2.5`
- `electron-updater 6.3.9`

位置见 [Frontend/package.json:60](C:/XCodeAgent/Frontend/package.json:60)。

审计给出的高风险最低修复线包括：

- axios ≥ 1.18.0
- compressing ≥ 1.10.5
- js-yaml ≥ 4.3.1
- Electron 至少 ≥ 39.8.10 才能覆盖本次列出的 Electron advisories
- 间接 `builder-util-runtime` ≥ 9.7.0
- 间接 `path-to-regexp` ≥ 8.4.0

Electron 37 已于 2026-01-13 EOL，可见[官方发布计划](https://releases.electronjs.org/schedule)。

另外，源码中没有找到 `compressing` 和 `electron-updater` 的实际使用；如果确实未使用，删除优于继续承担攻击面。

**建议：**

升级到当前受支持 Electron 线，升级或移除上述依赖，使用 frozen lockfile，CI 强制 audit/SBOM。升级后必须重新审计，不能只满足最低版本号。

---

## P2：Medium，建议在安全边界修复后持续治理

### 20. 多处存在事件循环阻塞、无界输出和资源 DoS

**证据：**

- async 路由内执行同步 subprocess，最长可达 120 秒，[Backend/app/main.py:299](C:/XCodeAgent/Backend/app/main.py:299)。
- subprocess 先完整捕获输出，再截断返回，[Backend/app/workspace.py:737](C:/XCodeAgent/Backend/app/workspace.py:737)。
- 文件读取和搜索 fallback 可将大文件完整加载到内存，[Backend/app/workspace.py:1269](C:/XCodeAgent/Backend/app/workspace.py:1269)。
- Agent `execute` 无总超时和输出上限。
- AG-UI action stream 使用无界 queue，[Backend/app/ag_ui_action_stream.py:102](C:/XCodeAgent/Backend/app/ag_ui_action_stream.py:102)。
- 没找到统一 request body、速率、并发、JSON 深度限制。

**建议：**

增加 ASGI body/depth 限制、端点 semaphore、每用户和每工作区并发配额；subprocess 进入 worker/thread；输出使用固定大小 ring buffer 或流式落盘；客户端断开时终止完整进程树。

---

### 21. 原始 Workflow 流在取消时可能缺少完整终止事件

**证据：**

- 正常路径生成完整结束事件，[Backend/app/workflow/runtime.py:917](C:/XCodeAgent/Backend/app/workflow/runtime.py:917)。
- `CancelledError` 路径直接重新抛出，[Backend/app/workflow/runtime.py:971](C:/XCodeAgent/Backend/app/workflow/runtime.py:971)。
- 取消请求自己的流会结束，但被取消的原始流不一定得到 terminal event。
- 测试目前只断言原 task 被取消，[Backend/tests/test_workflow_ag_ui.py:1167](C:/XCodeAgent/Backend/tests/test_workflow_ag_ui.py:1167)。

**影响：**

前端可能长期停留在 streaming/running 状态，直到额外刷新或状态对账。

**建议：**

原始流应在 finally 中保证恰好一次终止：关闭 text message、发 cancellation state snapshot、结构化取消结果和 `RUN_FINISHED`。

---

### 22. Symlink/Junction 防护不一致

**证据：**

- `_safe_path()` 对单个文件 resolve 后的 containment 较好。
- 但目录树递归使用 `is_dir()` 和 `iterdir()`，[Backend/app/workspace.py:1082](C:/XCodeAgent/Backend/app/workspace.py:1082)，可能跟随指向工作区外的目录链接。
- 生命周期文件只根据 `workspace_root / ".xcodeagent"` 写入，没有拒绝 `.xcodeagent` 是 symlink/junction，[Backend/app/application_lifecycle.py:106](C:/XCodeAgent/Backend/app/application_lifecycle.py:106)。

**建议：**

所有路径组件采用 no-follow；Windows 额外检查 reparse point/junction；递归遍历前验证每个实际 canonical path 仍在允许根目录内。

---

### 23. 错误信息和 `/health` 暴露过多内部实现

**证据：**

- AG-UI action stream 会把异常字符串直接返回客户端，[Backend/app/ag_ui_action_stream.py:183](C:/XCodeAgent/Backend/app/ag_ui_action_stream.py:183)。
- Workflow 也会把内部异常文本写入事件，[Backend/app/workflow/runtime.py:979](C:/XCodeAgent/Backend/app/workflow/runtime.py:979)。
- 数据库异常原样返回，[Backend/app/database_execution.py:214](C:/XCodeAgent/Backend/app/database_execution.py:214)。
- `/health` 返回模型 base URL、provider、LangSmith 等内部配置，[Backend/app/main.py:95](C:/XCodeAgent/Backend/app/main.py:95)。

公钥本身不属于秘密；问题是把过多运行时拓扑和错误细节暴露给未认证调用者。

**建议：**

客户端只返回稳定错误码、用户安全消息和 correlation ID；堆栈与驱动错误进入经过脱敏的本地日志；`/health` 只保留状态和协议版本。

---

### 24. 大文件、职责混合和非原子持久化放大回归风险

当前明显超出工程约定约 350 行边界的文件包括：

- [Frontend/src/main/index.ts](C:/XCodeAgent/Frontend/src/main/index.ts) — 约 1921 行
- [Backend/app/agents/frontend/subagents/page_detail_plan.py](C:/XCodeAgent/Backend/app/agents/frontend/subagents/page_detail_plan.py) — 约 1633 行
- [Backend/app/workflow/workflow_visualization.py](C:/XCodeAgent/Backend/app/workflow/workflow_visualization.py) — 约 1590 行
- [Backend/app/workspace.py](C:/XCodeAgent/Backend/app/workspace.py) — 约 1419 行
- [Backend/app/workflow/request.py](C:/XCodeAgent/Backend/app/workflow/request.py) — 约 1338 行
- [Frontend/src/renderer/src/services/agUiAgent.ts](C:/XCodeAgent/Frontend/src/renderer/src/services/agUiAgent.ts) — 约 1335 行

特别是 `main/index.ts` 同时承载窗口、认证、文件、工程、Git、浏览器、session、token 和 IPC 权限，导致任何 Renderer 边界问题都很难被局部审计。

applications/session 等数据还存在直接覆盖写入，[Frontend/src/main/index.ts:649](C:/XCodeAgent/Frontend/src/main/index.ts:649)，而 Settings 已有临时文件原子替换范例，[Frontend/src/main/applicationSettings.ts:55](C:/XCodeAgent/Frontend/src/main/applicationSettings.ts:55)，说明可统一。

**建议：**

按权限域拆分 IPC router，并统一 schema、sender auth、路径 capability、大小限制和审计日志；所有持久化采用 temp + flush/fsync + atomic rename + revision CAS。

---

## 已排除或降级的误报

为了避免把普通实现写成“漏洞”，以下结论经过复查后没有纳入主要风险：

- ZIP Skill 导入已经处理条目数、展开大小、绝对路径、`..`、反斜杠、symlink/特殊条目和原子替换，[Backend/app/user_skill_imports.py:171](C:/XCodeAgent/Backend/app/user_skill_imports.py:171)。
- Git clone 使用 `execFile` 参数数组，不属于 shell 拼接注入；真正风险是未固定来源和执行供应链脚本。
- `/tools/terminal/exec` 使用 `shell=False`，所以没有把 `;` 之类直接当作 shell 注入；问题是任意解释器本身被允许。
- CORS 正则有边界锚定，没有接受 `localhost.evil.com`；问题是任意真实 localhost/null Origin。
- Markdown 链接做了协议过滤，React 渲染也没有发现应用源码中的直接 `dangerouslySetInnerHTML`，[Frontend/src/renderer/src/components/MarkdownContent.tsx:384](C:/XCodeAgent/Frontend/src/renderer/src/components/MarkdownContent.tsx:384)。
- 用户 Skill YAML 使用 `safe_load`，[Backend/app/user_skills.py:248](C:/XCodeAgent/Backend/app/user_skills.py:248)。
- 后端 PID 恢复已有进程身份验证；PID 欺骗问题只确认存在于前端 launcher。
- plantMode 新密码确实走 RSA 加密，没有把这部分错误归类为“完全明文”；问题是私钥存储、模型暴露和 environment 字段。
- 模型原始输出日志默认关闭，[Backend/app/config.py:31](C:/XCodeAgent/Backend/app/config.py:31)，所以只作为潜在放大器，不单独列漏洞。

---

## 建议实施优先级

| 优先级 | 时间建议 | 必须完成的内容 |
|---|---:|---|
| P0-A | 立即 | 暂停携带真实密钥的安装包发布；移除打包 `.env` 并轮换曾发布密钥 |
| P0-B | 24～72 小时 | 本地后端加入 per-launch capability；废除任意 workspace root；审批迁到可信 Main |
| P0-C | 24～72 小时 | 暂时禁用 `/tools/terminal/exec` 和 Agent `execute`，直到统一沙箱策略落地 |
| P0-D | 3～7 天 | 隔离或取消模型生成 JS/TSX 执行；移除同源和 preload 能力 |
| P0-E | 3～7 天 | 删除公共 `resumeFrom`；服务端生成 artifact path；修复 dry-run/search 敏感文件泄漏 |
| P1-A | 1 个迭代 | IPC sender 校验、窄 preload、路径 capability、导航和 Preview 权限隔离 |
| P1-B | 1 个迭代 | 模板签名/commit pin、依赖安装和项目启动沙箱、清理继承环境变量 |
| P1-C | 1～2 个迭代 | 数据库 secret reference、OS vault、强制 TLS |
| P1-D | 1～2 个迭代 | thread/run 所有权、checkpoint 加密与清理、真实资源锁、PID 身份校验、SQL AST |
| P1-E | 1～2 个迭代 | 修复默认工作区根目录；升级 Electron 和高风险依赖 |
| P2 | 2～6 周 | 限流、输出预算、取消生命周期、symlink 一致性、错误脱敏、文件拆分和原子持久化 |

---

## 验证结果与当前质量状态

### 后端测试

完整测试：

```text
730 tests run
18 failures
11 errors
10 skipped
```

其中包括：

- `C:\var` 默认工作区错误导致的 PermissionError，确认了前述路径问题。
- Windows symlink 权限和 CRLF 差异等环境问题。
- acceptance 回退节点、页面设计重复调用、选中页面上下文重复、detail spec、integration runner、code graph、DAG 路由等行为与测试不一致。

这些不全是安全漏洞，但说明当前回归套件不绿，不能作为发布质量门禁。

聚焦 Windows 命令平台测试为 `2/2` 通过，但只覆盖扩展名、大小写和带引号路径，没有覆盖 Python/Node/curl 解释器绕过。

### 前端测试

通过：

- auth：10/10
- managed workspace：4/4
- project deletion：5/5
- preview inspector：通过

### 前端构建

`pnpm build` 当前失败，缺失：

- `sucrase`
- `@ant-design/pro-components`
- `dayjs`

这三个包均已声明在 `package.json` 和 lockfile，因此更像当前 `node_modules` 安装状态不完整，而不是 manifest 漏声明。

另外审计环境为 Node `23.11.1`，工程约定为 Node `20.19.0`；pnpm 为 `8.15.9`。建议在干净 Node 20.19 环境执行 frozen install 后重新构建。

### 未完成的验证

- `127.0.0.1:8000` 当时没有运行后端，因此 `/health` 无法检查。
- 环境没有安装 `pip-audit`，本次未完成 Python CVE 扫描。
- Python requirements 没有 lock/hash，本身就是供应链可复现性盲区。
- 没有做真实恶意利用或读取真实密钥，只使用临时假 sentinel 和无害命令分类复现。

### 工作区状态

没有修改任何代码或配置。Git 中只有审计开始前就存在的用户未跟踪文件：

`docs/ENTITY_PAGE_ENDPOINT_INDEPENDENT_DELIVERY_DESIGN.md`

没有覆盖或改动该文件。
