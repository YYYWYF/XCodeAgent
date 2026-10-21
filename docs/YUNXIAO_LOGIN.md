# 云效登录接入

此目录是独立应用副本。来源 `F:/XCodeAgent/XCodeAgent` 和 `F:/Code/code5_XCode_demo` 不参与运行时写入。

## 配置及启动

1. 编辑本项目 `Backend/.env` 的 `YUNXIAO_ENDPOINT`，填写云效认证服务基址，如 `https://your-auth-service.example`。不能填写 `/auth/state` 等完整接口路径。源项目和 demo 的真实 `.env` 未复制。
2. `expiresAt`、`refreshExpiresAt` 按 demo 原始含义处理为 Unix 秒级时间戳，乘以 1000 保存。没有采用 demo 调试用的 `expiry_date=1000`。
3. 默认沿 demo 将 token 接口 HTTP 200 的非零业务码视为等待授权，HTTP 202 也视为等待；401、403 等 HTTP 错误立即失败。服务端有明确等待码时设置 `YUNXIAO_PENDING_CODES=1001,1002`，其他非零业务码将立即失败。
4. `YUNXIAO_LOGIN_TIMEOUT_SECONDS=180` 是整次授权的截止时间，单次认证请求限制为 5 秒。`YUNXIAO_POLL_INTERVAL_SECONDS=1` 控制间隔。
5. 认证和业务接口沿 demo 支持配置的 HTTP 或 HTTPS 地址，不限制 HTTP 只能访问本机。`YUNXIAO_API_BASE_URL` 可设置独立云端业务地址，留空沿用认证地址。Python 不向其他 origin 或重定向目标转发 token。

Windows 开发启动（打开两个 PowerShell，工作目录都设为 `F:\Code\code6_devAgent_ystLogin`）：

```powershell
# 第一次运行才需要；本交付目录已建立独立 .venv 和前端依赖。
python -m venv Backend/.venv
Backend/.venv/Scripts/python.exe -m pip install -r Backend/requirements.txt
cd Frontend
pnpm install --frozen-lockfile
cd ..

# 终端一：启动本项目 Python 后端。
.\scripts\start-backend.ps1

# 终端二：启动本项目 Electron。
.\scripts\start-frontend.ps1
```

后端入口与原工程保持一致：默认 8000 端口、热重载、原有虚拟环境选择；前端入口仅封装原工程 `pnpm dev`，默认连接本机 8000。未增加云效专属启动流程。更换环境仍沿用原工程方式（后端设置 `DEVAGENTSTUDIO_WORKING_DIR`，前端运行对应 `pnpm dev:st` 等命令）。

本机全局 pnpm 的版本切换 shim 曾出现路径错误；如再次发生，可在 Frontend 执行 `npm exec --yes --package=pnpm@9.15.9 -- pnpm build` 或相应 dev/test 命令。无需修改来源项目。

## 职责与合同

- Python `services/yunxiao_auth.py` 实现异步 state → token 轮询 → userinfo，不使用 LLM；请求 state 保持 `X-User-Agent: Cli`。
- `routes/authentication.py`、`protocols/authentication.py` 提供独立 `/authentication/run` AG-UI 功能流，动作有 initialize/login/status/cancel/logout/shutdown/request。使用现有标准生命周期、进度事件和 STATE_SNAPSHOT；不进入 `/workflow/run`。
- Electron `main/authClient.ts` 使用 `@ag-ui/client` 消费流；`main/auth.ts` 仅协调系统浏览器和桌面会话。渲染器使用已有 auth IPC，不获取或持有 token。
- 新 IPC：`auth:cancel`、`auth:progress`、`auth:cloud-request`。移除 `auth:get-access-token`，不存在旧版兼容分支。`auth:login`、`auth:status`、`auth:reauthenticate` 保持职责。
- 云端 JSON 请求通过 IPC → Python 同一 AG-UI 功能流代理。Authorization 由 Python 从文件读取后附加，调用方不能覆盖；401 清理该请求的失效凭据。
- 本功能实现桌面登录门禁及云端凭据管理，没有给全部既有本地工作区工具新增远程访问鉴权。后端应仅监听本机地址。

## 与原工程一致的启动行为

保留原工程应用标识、Electron `userData` 路径、单实例锁、后端启动方式和窗口/托盘顺序。冷启动只清除凭据后显示登录窗口，不调用 Python 认证接口或访问云效；点击登录按钮才建立 Python 认证会话并发起云效授权。Python 未启动时也能看到登录窗口，点击登录后在界面提示连接失败。登录成功后沿用原有切换主窗口逻辑。

## 凭据与生命周期

凭据唯一来源为 `用户主目录/<环境目录>/yst-login/auth.json`，不维护全局 token 缓存。环境目录分别为 `.devagentstudio_dev`、`.devagentstudio_st`、`.devagentstudio_uat`、`.devagentstudio`。用户 token 只在请求或加解密过程中短暂经过内存。

Windows 使用当前用户 DPAPI；macOS/Linux 使用 keyring 的系统安全钥匙串保存 Fernet 密钥，缺少安全后端时拒绝明文降级。文件只含加密载荷；仅保存需要的 access/refresh、有效期、用户信息和 yst 标识。无 token 进入 AG-UI 状态、日志或模型上下文。

`session.json` 只保存随机会话身份与 active 标记，不包含 token。保留该文件是为了退出后拒绝迟到的请求再次落盘。凭据原子写入，初始化会删除旧 token 和临时文件；取消/重新认证清理凭据但允许同一会话重新登录；shutdown 先使会话失效再清理。正常隐藏窗口进入托盘不删除凭据；真正退出才删除。

每次状态查询与云端请求都读取文件，提前 30 秒判定过期；文件缺失、损坏或过期进入重新登录。demo 未给出 refresh 协议，因此当前不猜测刷新接口、不做自动刷新。

Electron 退出先通过 AG-UI shutdown 清理，再结束打包后端，最后进行文件删除兜底。Windows 后端可能被强制终止，所以不能单靠 Python lifespan。崩溃/断电残留在下一次桌面启动清理。登录有互斥和取消信号，保存前校验会话栅栏；旧会话不得覆盖当前凭据。

## 验证

```powershell
cd Backend
.venv/Scripts/python.exe -m unittest tests.test_authentication -v
cd ../Frontend
pnpm test:auth
pnpm build
```

后端测试使用 MockTransport 模拟云效协议和临时凭据目录，并验证真实 Windows DPAPI、过期、损坏、取消竞态、旧会话、环境不一致、网络失败和完整 AG-UI 生命周期。真实云效联调需要用户补充 YUNXIAO_ENDPOINT 后在系统浏览器中完成授权；本次不声称已完成真实云效授权。

含中文的新启动脚本使用 UTF-8 BOM，确保 Windows PowerShell 5.1 正确解析。若执行策略阻止脚本，可使用 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1`，前端同理。退出时先从托盘退出 Electron，再停止后端。
