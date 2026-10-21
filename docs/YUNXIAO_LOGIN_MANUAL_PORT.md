# 云效登录认证手工移植：完整源码与逐项差异

本文件用于在内网原始 XCodeAgent 中手工复现 code6_devAgent_ystLogin 的认证功能。下面的新增源码和替换代码均来自现有实现，不是伪代码，也不使用“省略异常处理”等不完整摘录。无需访问本机 F: 盘路径；文中的相对路径均相对于内网项目根目录（包含 Backend、Frontend 的目录）。

## 1. 使用方式与边界

1. 原工程基线是生成本说明时的 XCodeAgent；内网版本若不同，按函数和上下文手工合并，不整文件覆盖大型模块。
2. 先完成“运行必需”17 个文件（7 个新增、10 个修改，包含 requirements.txt），再追加第 4 节配置。其余 5 个源码/脚本为测试或辅助。
3. 新文件复制完整代码。已有小文件提供完整目标代码；已有大型文件按每组“修改前/修改后”逐项替换。上下文相同的行应保留。 对于同时提供完整文件和修改对照的旧文件，两种方法二选一；不要整文件替换后再重复执行下面的修改组。
4. 本文的关键代码块为 UTF-8；PowerShell 中含中文的新脚本应保存为 UTF-8 BOM。压缩包中的原始脚本字节已保留 BOM。
5. 同目录附带 runtime.patch（运行必需代码）、tests-tools.patch（可选测试/脚本）和完整源码包。手工移植无需执行补丁；diff 中 + 表示增加、- 表示删除，符号不能复制进源码。
6. 不迁移 .env、auth.json、session.json、密钥、.venv、node_modules、构建目录和模拟平台。源码包中仅包含明确白名单源码与说明。
7. 本次功能不改应用身份、Electron userData、单实例锁、环境目录映射或原后端启动脚本；新增认证初始化只发生于点击登录。
8. 当前没有 refresh_token 自动刷新，也没有给所有本地 Python 接口增加鉴权。内网后端仍按原工程监听本机地址。

## 2. 完整文件清单

### 2.1 代码、依赖与辅助脚本（共 22 个）

| 序号 | 路径 | 操作 | 分类 |
|---|---|---|---|
| 1 | `Backend/app/services/auth_protection.py` | 新增 | 运行必需 |
| 2 | `Backend/app/services/auth_credentials.py` | 新增 | 运行必需 |
| 3 | `Backend/app/services/yunxiao_auth.py` | 新增 | 运行必需 |
| 4 | `Backend/app/protocols/authentication.py` | 新增 | 运行必需 |
| 5 | `Backend/app/routes/authentication.py` | 新增 | 运行必需 |
| 6 | `Backend/app/main.py` | 修改 | 运行必需 |
| 7 | `Backend/requirements.txt` | 修改 | 运行必需 |
| 8 | `Frontend/src/shared/authentication.ts` | 新增 | 运行必需 |
| 9 | `Frontend/src/main/authClient.ts` | 新增 | 运行必需 |
| 10 | `Frontend/src/main/auth.ts` | 修改 | 运行必需 |
| 11 | `Frontend/src/main/index.ts` | 修改 | 运行必需 |
| 12 | `Frontend/src/preload/index.ts` | 修改 | 运行必需 |
| 13 | `Frontend/src/preload/index.d.ts` | 修改 | 运行必需 |
| 14 | `Frontend/src/renderer/src/window.d.ts` | 修改 | 运行必需 |
| 15 | `Frontend/src/renderer/src/login.tsx` | 修改 | 运行必需 |
| 16 | `Frontend/src/renderer/src/login.less` | 修改 | 运行必需 |
| 17 | `Frontend/src/renderer/src/service/cloudApi.ts` | 修改 | 运行必需 |
| 18 | `Backend/tests/test_authentication.py` | 新增 | 测试 |
| 19 | `Frontend/tests/authentication.test.ts` | 修改 | 测试 |
| 20 | `scripts/start-frontend.ps1` | 新增 | 辅助，可选 |
| 21 | `scripts/start-yst-backend.ps1` | 新增 | 辅助，可选 |
| 22 | `Frontend/scripts/verify-yst-electron.mjs` | 新增 | 历史测试辅助，可选 |

### 2.2 配置与说明文件（另外 4 个）

| 路径 | 操作 | 手工移植内容 |
|---|---|---|
| `Backend/.env.example` | 修改 | 追加第 4 节的无秘密配置模板；保留原配置，不复制源机器配置值。 |
| `docs/YUNXIAO_LOGIN.md` | 新增 | 添加配置、职责、凭据生命周期和验收说明；可使用本文第 3、4、6 节整理，不影响运行。 |
| `docs/CODEBASE_INDEX.md` | 修改 | 追加第 7 节模块职责说明，不替换原索引。 |
| `README.md` | 修改 | 添加“云效登录见 docs/YUNXIAO_LOGIN.md”的链接。 |

以上合计 26 个功能相关文件：12 个新增、14 个修改。运行时真实值另行配置于本机 `Backend/.env`，不包含在补丁/源码包中。本移植文档及导出补丁是此次额外交付文件，不计入功能改动清单。

## 3. 必须保持一致的运行合同

登录链路：React -> preload -> Electron IPC -> AG-UI SDK -> Python -> 云效。Python 执行认证和加密存储；Electron 只协调浏览器、窗口和清理；React 不取得 token。

云效协议：

| 阶段 | HTTP | 参数/头 | 响应 data |
|---|---|---|---|
| state | GET /auth/state | X-User-Agent: Cli | state |
| 授权页面 | 浏览器 /auth/authorize | URL 编码后的 state | 用户在浏览器确认 |
| token | GET /auth/token | query state | token, expiresAt, refreshToken?, refreshExpiresAt?, tokenType?, ystIdToken?, ystRefreshToken? |
| 用户 | GET /auth/userinfo | Authorization: Bearer <token> | id, nickname |

普通成功信封为 `{"code":0,"data":{...}}`。`expiresAt`/`refreshExpiresAt` 视为 Unix 秒级到期时间戳，乘 1000 保存；不是剩余秒数。tokenType 仅接受 Bearer/bearer。默认 token 接口 HTTP 202 或 HTTP 200 非零业务码继续等待；配置 YUNXIAO_PENDING_CODES 后仅指定业务码等待，其余错误立即失败。

本机接口 POST /authentication/run，标准 AG-UI 请求 forwardedProps.authentication 中放 action、sessionId、workingDir，request 动作另带 request。动作：initialize/login/status/cancel/logout/shutdown/request。复用现有 build_ag_ui_action_stream、AgUiActionResult/Progress，不手写 SSE 解析。检查 Electron/Python 环境目录一致，认证不进入 /workflow/run，不调用 LLM，不持久化聊天。

公开 AuthenticationResult 不含 token；授权 URL 仅用于浏览器打开。主进程内存只保留 UUID、任务状态；Python 的服务实例只保存任务控制对象，不长期缓存凭据。

凭据目录：`Path.home() / DEVAGENTSTUDIO_WORKING_DIR / "yst-login"`。dev/st/uat/prd 对应 `.devagentstudio_dev`/`.devagentstudio_st`/`.devagentstudio_uat`/`.devagentstudio`。Electron 原 app.getPath('userData') 不变。

`auth.json` 外层只含 ciphertext（系统加密之后的 Base64）；`session.json` 只含 sessionId/active，不含 token。Windows 当前用户 DPAPI；非 Windows 系统钥匙串保存 Fernet 密钥。不允许明文降级。登录保存前验证当前任务和会话，退出使 active=false，拒绝迟到写入。

读取凭据发现缺失、损坏、解密失败、已过期或剩余不足 30 秒时返回未登录；后几种清理文件。没有到期定时删除任务，不实现自动刷新。

HTTP 配置沿 demo 支持 HTTP/HTTPS（HTTP 不限本机）。认证单次请求 5 秒；整次默认 180 秒；轮询默认 1 秒；业务请求 30 秒；follow_redirects=False、trust_env=False（环境代理不生效）。只有 Electron -> Python 限本机。

云端请求只能发送到配置 origin，不能代理 /auth 接口；Authorization 由 Python 从文件生成，调用方不能覆盖。401 清理之前比较当前 token，不能误删并发重新登录后的新凭据。云端 403 沿用原前端认证失败通知；网络或 500 不直接作为 token 无效处理。

启动清理残留后显示登录框，不初始化 Python 认证。点击登录才 initialize/login；成功确认文件状态后打开主窗口。关闭窗口隐藏到托盘保留 token。真正退出先 shutdown，再原后端关闭处理，最后 Electron 文件删除兜底；异常退出下次启动清理。

## 4. 手工配置（必须完成）

将以下配置说明追加到 Backend/.env.example，并在内网自己的 Backend/.env 填写真实值。不要覆盖原模型、数据库等配置：

```dotenv
# 仅追加以下云效配置，不替换原工程其他配置。
# 必须改为内网真实认证服务基址，不包含 /auth/state 等接口路径。
YUNXIAO_ENDPOINT=https://your-intranet-auth.example
YUNXIAO_API_BASE_URL=
YUNXIAO_LOGIN_TIMEOUT_SECONDS=180
YUNXIAO_POLL_INTERVAL_SECONDS=1
YUNXIAO_PENDING_CODES=

```

认证地址是服务基址，不要附 /auth/state；刷新真实值后重启 Python。YUNXIAO_PENDING_CODES 留空沿 demo，平台明确等待码时填逗号分隔整数。Electron/Python 默认均使用 dev 环境；切换环境沿原工程方法，两端必须一致。

依赖基线：Python 3.12；原工程已有 httpx>=0.27.0、cryptography==46.0.3；新增 keyring>=25.6,<26。前端沿原 package.json/pnpm-lock.yaml，已有 @ag-ui/client/core 0.0.57、Electron 37.2.5、pnpm 9.15.9，不需更改依赖声明或升级 Electron。内网离线安装须准备这些包及其传递依赖、Electron 对应平台二进制，不复制别机 .venv。

## 5. 逐文件：原内容、修改说明与完整目标代码

下列顺序适合手工实施。先建立新增文件，再改调用方；全部完成后统一编译验证。仅运行必需文件决定功能，辅助文件有明确标记。

### 5.1 Backend/app/services/auth_protection.py

操作：**新增**；分类：**运行必需**。

原内容：原工程没有独立的云效凭据加密模块。

修改内容：增加 protect() 与 _windows_protect()。Windows 当前用户 DPAPI；其他平台使用系统钥匙串保存 Fernet 密钥；不允许明文降级。

**完整目标文件内容**（无省略；新建文件直接粘贴，小型旧文件确认基线后替换）：

```python
"""使用系统安全存储保护本次桌面会话的登录凭据。"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from cryptography.fernet import Fernet


def protect(data: bytes, *, decrypt: bool = False) -> bytes:
    """Windows 使用当前用户 DPAPI，其他平台使用系统钥匙串保存加密密钥。"""
    if os.name == "nt":
        return _windows_protect(data, decrypt=decrypt)
    import keyring
    backend = keyring.get_keyring()
    # 禁止把认证密钥降级保存到明文文件后端。
    if not type(backend).__module__.startswith(("keyring.backends.macOS", "keyring.backends.SecretService", "keyring.backends.kwallet")):
        raise RuntimeError("未找到系统安全钥匙串，无法保存登录凭据。")
    service, account = "devagentstudio-yst-login", "credential-encryption"
    key = keyring.get_password(service, account)
    if not key:
        if decrypt:
            raise RuntimeError("登录凭据的系统密钥不存在，请重新登录。")
        key = Fernet.generate_key().decode("ascii")
        keyring.set_password(service, account, key)
    codec = Fernet(key.encode("ascii"))
    return codec.decrypt(data) if decrypt else codec.encrypt(data)


def _windows_protect(data: bytes, *, decrypt: bool) -> bytes:
    """调用 Windows DPAPI，并释放系统分配的输出内存。"""
    class Blob(ctypes.Structure):
        """描述 DPAPI 输入输出的二进制缓冲区。"""
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.restype = wintypes.BOOL
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError("系统无法保护或解密登录凭据，请重新登录。")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(output.data)
```

### 5.2 Backend/app/services/auth_credentials.py

操作：**新增**；分类：**运行必需**。

原内容：原工程由 Electron 写明文 auth.json，并长期缓存 token。

修改内容：增加 AuthCredentials 与 CredentialStore；加密文件权威存储、原子替换、30 秒提前失效、session.json 会话栅栏、初始化/注销/退出清理。

**完整目标文件内容**（无省略；新建文件直接粘贴，小型旧文件确认基线后替换）：

```python
"""文件是用户 token 的唯一持久来源；会话栅栏阻止迟到的登录写入。"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Callable
from pydantic import BaseModel, ConfigDict, Field
from app.branding import USER_DATA_DIRECTORY_NAMES, DEFAULT_USER_DATA_DIRECTORY_NAME
from app.services.auth_protection import protect


class AuthCredentials(BaseModel):
    """校验保存在加密文件中的当前认证凭据。"""
    model_config = ConfigDict(extra="forbid")
    access_token: str = Field(min_length=1)
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_at_ms: int = Field(gt=0)
    refresh_expires_at_ms: int = 0
    user_id: str = Field(min_length=1)
    user_name: str = Field(min_length=1)
    yst_id_token: str = ""
    yst_refresh_token: str = ""


class CredentialStore:
    """集中处理凭据文件、环境校验和桌面会话所有权。"""
    def __init__(self, root: Path | None = None, codec: Callable = protect):
        """定位独立的认证子目录，允许测试注入临时目录和加密器。"""
        self.working_dir = os.getenv("DEVAGENTSTUDIO_WORKING_DIR", DEFAULT_USER_DATA_DIRECTORY_NAME)
        if self.working_dir not in USER_DATA_DIRECTORY_NAMES:
            raise RuntimeError("DEVAGENTSTUDIO_WORKING_DIR 不是受支持的用户目录。")
        self.root = root if root is not None else Path.home() / self.working_dir / "yst-login"
        self.path = self.root / "auth.json"
        self.session_path = self.root / "session.json"
        self.codec = codec

    def _check_directory(self) -> None:
        """拒绝符号链接路径，并确保认证目录存在。"""
        if any(item.is_symlink() for item in (self.root, *self.root.parents)):
            raise RuntimeError("认证目录不允许使用符号链接。")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink() or self.session_path.is_symlink():
            raise RuntimeError("认证文件不允许使用符号链接。")

    def _atomic_write(self, path: Path, payload: dict) -> None:
        """先完整写入同目录临时文件，再原子替换正式文件。"""
        self._check_directory()
        descriptor, name = tempfile.mkstemp(prefix=".auth-", suffix=".tmp", dir=self.root)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def initialize(self, session_id: str) -> None:
        """新 Electron 会话先建立栅栏，再删除上次异常退出残留。"""
        self._atomic_write(self.session_path, {"sessionId": session_id, "active": True})
        self.clear()

    def require_session(self, session_id: str) -> None:
        """每次操作和最终写入前校验当前桌面会话仍有效。"""
        self._check_directory()
        try:
            session = json.loads(self.session_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise RuntimeError("桌面认证会话未初始化，请重新启动应用。") from None
        if session != {"sessionId": session_id, "active": True}:
            raise RuntimeError("桌面认证会话已结束或已被替换。")

    def save(self, session_id: str, credentials: AuthCredentials) -> None:
        """凭据加密后原子落盘，不在实例属性中缓存 token。"""
        encrypted = self.codec(credentials.model_dump_json().encode("utf-8"))
        self.require_session(session_id)
        self._atomic_write(self.path, {"ciphertext": base64.b64encode(encrypted).decode("ascii")})

    def read(self, session_id: str) -> AuthCredentials | None:
        """读取当前文件；缺失、损坏或即将过期时要求重新登录。"""
        self.require_session(session_id)
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            encrypted = base64.b64decode(payload["ciphertext"], validate=True)
            credentials = AuthCredentials.model_validate_json(self.codec(encrypted, decrypt=True))
            if credentials.expires_at_ms <= int(time.time() * 1000) + 30_000:
                self.clear()
                return None
            return credentials
        except Exception:
            self.clear()
            return None

    def clear(self) -> None:
        """删除 token 和本模块创建的临时文件，不影响其他应用数据。"""
        self._check_directory()
        self.path.unlink(missing_ok=True)
        for temporary in self.root.glob(".auth-*.tmp"):
            temporary.unlink(missing_ok=True)

    def invalidate(self, session_id: str) -> None:
        """退出时先撤销写入权限，再删除凭据；保留不含秘密的栅栏。"""
        self.require_session(session_id)
        self._atomic_write(self.session_path, {"sessionId": session_id, "active": False})
        self.clear()
```

### 5.3 Backend/app/services/yunxiao_auth.py

操作：**新增**；分类：**运行必需**。

原内容：原工程没有 Python 云效认证业务。

修改内容：增加 AuthService：异步 state/token/userinfo、取消和登录互斥、有效期换算、公开状态、可信云端 JSON 代理。HTTP/HTTPS 均支持，trust_env=False 不读取环境代理；无自动刷新。

**完整目标文件内容**（无省略；新建文件直接粘贴，小型旧文件确认基线后替换）：

```python
"""云效桌面认证业务；使用异步 HTTP，不缓存用户 token。"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urlencode, urlsplit, unquote
import httpx
from pydantic import BaseModel, Field, ValidationError
from app.services.auth_credentials import AuthCredentials, CredentialStore


class StateData(BaseModel):
    """校验 state 响应。"""
    state: str = Field(min_length=1, max_length=2048)


class TokenData(BaseModel):
    """沿 demo 的协议解释有效期为 Unix 秒级时间戳。"""
    token: str = Field(min_length=1)
    expiresAt: int = Field(gt=0)
    refreshToken: str = ""
    refreshExpiresAt: int = 0
    tokenType: Literal["Bearer", "bearer"] = "Bearer"
    ystIdToken: str = ""
    ystRefreshToken: str = ""


class UserData(BaseModel):
    """校验认证用户的公开标识和名称。"""
    id: str = Field(min_length=1)
    nickname: str = Field(min_length=1)


class AuthService:
    """串行管理登录任务；内存只保留任务身份和取消信号。"""
    def __init__(self, store: CredentialStore, transport: httpx.AsyncBaseTransport | None = None):
        """初始化文件存储和可注入的 HTTP 传输。"""
        self.store = store
        self.transport = transport
        self.lock = asyncio.Lock()
        self.attempt: asyncio.Event | None = None

    async def reset(self, session_id: str, *, initialize: bool = False, shutdown: bool = False) -> dict:
        """取消并隔离旧登录任务，再初始化、清理或结束桌面会话。"""
        async with self.lock:
            if not initialize:
                self.store.require_session(session_id)
            if self.attempt:
                self.attempt.set()
                self.attempt = None
            if initialize:
                self.store.initialize(session_id)
            elif shutdown:
                self.store.invalidate(session_id)
            else:
                self.store.clear()
            return {"authenticated": False, "workingDir": self.store.working_dir}

    def status(self, session_id: str) -> dict:
        """公开登录状态，不把 token 暴露到 AG-UI。"""
        credentials = self.store.read(session_id)
        result: dict[str, Any] = {"authenticated": credentials is not None, "workingDir": self.store.working_dir}
        if credentials:
            result.update(user={"id": credentials.user_id, "name": credentials.user_name}, expiresAt=credentials.expires_at_ms)
        return result

    def _endpoint(self) -> str:
        """沿 demo 使用配置的 HTTP 或 HTTPS 认证地址。"""
        endpoint = os.getenv("YUNXIAO_ENDPOINT", "").strip().rstrip("/")
        parsed = urlsplit(endpoint)
        if not endpoint:
            raise RuntimeError("请在 Backend/.env 配置 YUNXIAO_ENDPOINT 后重启 Python 后端。")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
            raise RuntimeError("YUNXIAO_ENDPOINT 必须是无凭据、查询参数和片段的服务地址。")
        if parsed.scheme not in {"http", "https"}:
            raise RuntimeError("YUNXIAO_ENDPOINT 必须使用 HTTP 或 HTTPS。")
        return endpoint

    async def _get(self, client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        """限制每次请求耗时，并把网络异常转成不含 token 或 state 的错误。"""
        try:
            return await client.get(url, **kwargs)
        except httpx.RequestError:
            raise RuntimeError("云效认证服务连接失败或请求超时，请检查网络后重试。") from None

    def _data(self, response: httpx.Response, stage: str) -> dict:
        """校验 HTTP 和业务信封，不向日志或界面回显上游原始载荷。"""
        if response.status_code != 200:
            raise RuntimeError(f"{stage}失败：HTTP {response.status_code}。")
        try:
            payload = response.json()
            if not isinstance(payload, dict) or type(payload.get("code")) is not int:
                raise ValueError()
            if payload["code"] != 0:
                raise RuntimeError(f"{stage}失败：业务码 {payload['code']}。")
            if not isinstance(payload.get("data"), dict):
                raise ValueError()
            return payload["data"]
        except (ValueError, TypeError):
            raise RuntimeError(f"{stage}返回了无效响应。") from None

    def _check_attempt(self, session_id: str, attempt: asyncio.Event) -> None:
        """在请求间及落盘前检查取消和会话栅栏。"""
        if attempt.is_set() or self.attempt is not attempt:
            raise RuntimeError("登录已取消，请重新发起登录。")
        self.store.require_session(session_id)

    async def login(self, session_id: str, report: Callable[..., Awaitable[None]]) -> dict:
        """完成 state、浏览器授权等待、token 和用户信息的认证闭环。"""
        async with self.lock:
            self.store.require_session(session_id)
            if self.attempt is not None:
                raise RuntimeError("已有登录正在进行，请等待或取消后重试。")
            attempt = asyncio.Event()
            self.attempt = attempt
        try:
            endpoint = self._endpoint()
            timeout = float(os.getenv("YUNXIAO_LOGIN_TIMEOUT_SECONDS", "180"))
            interval = float(os.getenv("YUNXIAO_POLL_INTERVAL_SECONDS", "1"))
            if not 1 <= timeout <= 900 or not 0.1 <= interval <= 10:
                raise RuntimeError("云效登录超时或轮询间隔配置超出允许范围。")
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                    await report("requesting_state", "正在获取云效授权标识", 10)
                    state = StateData.model_validate(self._data(await self._get(client, f"{endpoint}/auth/state", headers={"X-User-Agent": "Cli"}), "获取授权标识")).state
                    self._check_attempt(session_id, attempt)
                    await report("awaiting_authorization", "请在浏览器中完成云效授权", 25, authorizationUrl=f"{endpoint}/auth/authorize?{urlencode({'state': state})}")
                    pending_codes = {int(code.strip()) for code in os.getenv("YUNXIAO_PENDING_CODES", "").split(",") if code.strip()}
                    while True:
                        self._check_attempt(session_id, attempt)
                        response = await self._get(client, f"{endpoint}/auth/token", params={"state": state})
                        pending = response.status_code == 202
                        if response.status_code == 200:
                            try:
                                body = response.json()
                            except ValueError:
                                raise RuntimeError("获取 token 返回了无效响应。") from None
                            pending = isinstance(body, dict) and type(body.get("code")) is int and (body["code"] in pending_codes if pending_codes else body["code"] != 0)
                        if not pending:
                            token = TokenData.model_validate(self._data(response, "获取 token"))
                            break
                        try:
                            await asyncio.wait_for(attempt.wait(), timeout=interval)
                        except TimeoutError:
                            pass
                    self._check_attempt(session_id, attempt)
                    await report("loading_user", "正在读取云效用户信息", 65)
                    user = UserData.model_validate(self._data(await self._get(client, f"{endpoint}/auth/userinfo", headers={"Authorization": f"Bearer {token.token}"}), "获取用户信息"))
                    credentials = AuthCredentials(access_token=token.token, refresh_token=token.refreshToken, token_type="Bearer", expires_at_ms=token.expiresAt * 1000, refresh_expires_at_ms=token.refreshExpiresAt * 1000, user_id=user.id, user_name=user.nickname, yst_id_token=token.ystIdToken, yst_refresh_token=token.ystRefreshToken)
                    if credentials.expires_at_ms <= int(time.time() * 1000) + 30_000:
                        raise RuntimeError("云效 token 已过期或即将过期，请重新登录。")
                    await report("saving_credentials", "正在安全保存本次登录凭据", 90)
                    async with self.lock:
                        self._check_attempt(session_id, attempt)
                        self.store.save(session_id, credentials)
                    return self.status(session_id)
        except TimeoutError:
            raise RuntimeError("等待云效授权超时，请点击登录重试。") from None
        except ValidationError:
            raise RuntimeError("云效认证响应字段不符合 demo 协议。") from None
        finally:
            async with self.lock:
                if self.attempt is attempt:
                    self.attempt = None

    async def cloud_request(self, session_id: str, request: dict) -> dict:
        """在 Python 内读取文件并添加认证头，凭据不经过渲染器。"""
        credentials = self.store.read(session_id)
        if not credentials:
            return {"httpStatus": 401, "body": {"detail": "登录已失效，请重新登录。"}}
        base = urlsplit(os.getenv("YUNXIAO_API_BASE_URL", "").strip() or self._endpoint())
        if base.scheme not in {"http", "https"} or not base.hostname:
            raise RuntimeError("云端业务接口必须配置为 HTTP 或 HTTPS 服务地址。")
        target = urlsplit(request["url"])
        if (target.scheme, target.hostname, target.port) != (base.scheme, base.hostname, base.port) or target.username or target.password or target.fragment:
            raise RuntimeError("云端请求地址不属于已配置的可信服务。")
        if "auth" in unquote(unquote(target.path)).lower().split("/"):
            raise RuntimeError("云端业务请求不能访问认证接口。")
        allowed_headers = {k: v for k, v in request.get("headers", {}).items() if k.lower() in {"content-type", "accept"}}
        allowed_headers["Authorization"] = f"Bearer {credentials.access_token}"
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False, transport=self.transport) as client:
            try:
                response = await client.request(request["method"], request["url"], headers=allowed_headers, content=request.get("body"))
            except httpx.RequestError:
                raise RuntimeError("云端业务接口连接失败。") from None
        if response.status_code == 401:
            # 只清理该请求使用的凭据，不能删除并发重新登录保存的新 token。
            current = self.store.read(session_id)
            if current and current.access_token == credentials.access_token:
                self.store.clear()
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = {"detail": "云端接口未返回 JSON。"}
        return {"httpStatus": response.status_code, "body": body}
```

### 5.4 Backend/app/protocols/authentication.py

操作：**新增**；分类：**运行必需**。

原内容：原工程没有独立桌面认证 AG-UI 动作协议。

修改内容：增加 CloudRequest、AuthenticationInput、延迟创建服务与 build_authentication_stream()；校验会话和环境；使用现有 AG-UI action stream 输出完整生命周期。

**完整目标文件内容**（无省略；新建文件直接粘贴，小型旧文件确认基线后替换）：

```python
"""独立桌面认证 AG-UI 协议，不进入开发工作流或持久化聊天。"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.protocols.ag_ui_action_stream import AgUiActionResult, AgUiActionProgress, build_ag_ui_action_stream
from app.services.auth_credentials import CredentialStore
from app.services.yunxiao_auth import AuthService


class CloudRequest(BaseModel):
    """限制桌面云端 JSON 请求的输入范围。"""
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=4096)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = Field(default=None, max_length=1_048_576)


class AuthenticationInput(BaseModel):
    """显式校验桌面环境和会话身份。"""
    model_config = ConfigDict(extra="forbid")
    action: Literal["initialize", "login", "status", "cancel", "logout", "shutdown", "request"]
    sessionId: str = Field(pattern=r"^[0-9a-f-]{36}$")
    workingDir: str
    request: CloudRequest | None = None


_service: AuthService | None = None


def authentication_service() -> AuthService:
    """延迟创建认证服务，避免模块导入时触碰用户文件。"""
    global _service
    if _service is None:
        _service = AuthService(CredentialStore())
    return _service


def build_authentication_stream(payload: dict, accept: str | None = None):
    """把成功、进度、取消及业务错误包装为完整 AG-UI 生命周期。"""
    async def operation(report):
        """通过后端唯一业务入口派发认证动作。"""
        value = AuthenticationInput.model_validate((payload.get("forwardedProps") or {}).get("authentication", {}))
        service = authentication_service()
        if value.workingDir != service.store.working_dir:
            raise RuntimeError("Electron 和 Python 的用户环境不一致，请检查 DEVAGENTSTUDIO_WORKING_DIR。")
        if value.action == "login":
            async def progress(stage, message, percent, **data):
                """只公开授权 URL 和进度，不投影 token。"""
                await report(AgUiActionProgress(stage=stage, message=message, percent=percent, data=data))
            result = await service.login(value.sessionId, progress)
        elif value.action == "status":
            result = service.status(value.sessionId)
        elif value.action == "request":
            if value.request is None:
                raise RuntimeError("云端请求缺少参数。")
            result = await service.cloud_request(value.sessionId, value.request.model_dump())
        else:
            result = await service.reset(value.sessionId, initialize=value.action == "initialize", shutdown=value.action == "shutdown")
        return AgUiActionResult(data=result, message="认证操作已完成。")
    return build_ag_ui_action_stream(payload=payload, event_name="authentication", state_key="authentication", run_id_prefix="authentication", progress_operation=operation, error_message_prefix="认证操作失败", accept=accept, emit_progress_text=False, register_workspace_run=False)
```

### 5.5 Backend/app/routes/authentication.py

操作：**新增**；分类：**运行必需**。

原内容：原工程没有 /authentication/run。

修改内容：增加 POST /authentication/run，StreamingResponse 输出 AG-UI SSE；不进入 LLM/开发工作流。

**完整目标文件内容**（无省略；新建文件直接粘贴，小型旧文件确认基线后替换）：

```python
"""桌面云效认证的独立 AG-UI 路由。"""
from typing import Any
from fastapi import APIRouter, Body, Header
from fastapi.responses import StreamingResponse
from app.protocols.authentication import build_authentication_stream

router = APIRouter(tags=["authentication"])


@router.post("/authentication/run")
async def run_authentication(payload: dict[str, Any] = Body(...), accept: str | None = Header(default="text/event-stream")) -> StreamingResponse:
    """运行不依赖 LLM 或登录前用户 token 的桌面认证动作。"""
    return StreamingResponse(build_authentication_stream(payload, accept), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

### 5.6 Backend/app/main.py

操作：**修改**；分类：**运行必需**。

原内容：原 main.py 未导入或注册认证路由；/health 无认证能力条目。

修改内容：导入 authentication_router；app.include_router(authentication_router)；/health.tools 增加 authentication 元数据。仅改以下差异，保留其他路由和 lifespan。

**与原工程逐项对照，共 3 处修改组。** 行号是原基线的位置，内网有差异时按相邻代码定位。每组都给出真实修改前后内容，其他部分保持原样。

修改组 1：原文件约第 50 行；目标文件约第 50 行。

修改前：

```python
)
from app.protocols.data_sources import data_sources_capabilities
from app.protocols.endpoint_designs import endpoint_designs_capabilities
from app.routes.data_sources import data_sources_router
from app.routes.endpoint_designs import endpoint_designs_router
from app.protocols.revision_preview import (
```

修改后：

```python
)
from app.protocols.data_sources import data_sources_capabilities
from app.protocols.endpoint_designs import endpoint_designs_capabilities
from app.routes.authentication import router as authentication_router
from app.routes.data_sources import data_sources_router
from app.routes.endpoint_designs import endpoint_designs_router
from app.protocols.revision_preview import (
```

修改组 2：原文件约第 132 行；目标文件约第 133 行。

修改前：

```python
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(data_sources_router)
app.include_router(endpoint_designs_router)

```

修改后：

```python
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(authentication_router)
app.include_router(data_sources_router)
app.include_router(endpoint_designs_router)

```

修改组 3：原文件约第 162 行；目标文件约第 164 行。

修改前：

```python
        },
        "database_encryption": database_encryption_metadata(),
        "tools": {
            "workflow_run": workflow_capabilities(),
            "application_page_planning": application_page_planning_capabilities(),
            "application_lifecycle": application_lifecycle_capabilities(),
```

修改后：

```python
        },
        "database_encryption": database_encryption_metadata(),
        "tools": {
            "authentication": {"endpoint": "/authentication/run", "transport": "ag-ui-sse", "workflowIndependent": True, "actions": ["initialize", "login", "status", "cancel", "logout", "shutdown", "request"], "credentialStorage": "encrypted-environment-file", "expiresAtUnit": "unix-seconds"},
            "workflow_run": workflow_capabilities(),
            "application_page_planning": application_page_planning_capabilities(),
            "application_lifecycle": application_lifecycle_capabilities(),
```

**完整统一差异**（便于逐行核对；+/- 不属于源码）：

```diff
diff --git a/Backend/app/main.py b/Backend/app/main.py
--- a/Backend/app/main.py
+++ b/Backend/app/main.py
@@ -50,6 +50,7 @@
 )
 from app.protocols.data_sources import data_sources_capabilities
 from app.protocols.endpoint_designs import endpoint_designs_capabilities
+from app.routes.authentication import router as authentication_router
 from app.routes.data_sources import data_sources_router
 from app.routes.endpoint_designs import endpoint_designs_router
 from app.protocols.revision_preview import (
@@ -132,6 +133,7 @@
     version="0.1.0",
     lifespan=lifespan,
 )
+app.include_router(authentication_router)
 app.include_router(data_sources_router)
 app.include_router(endpoint_designs_router)
 
@@ -162,6 +164,7 @@
         },
         "database_encryption": database_encryption_metadata(),
         "tools": {
+            "authentication": {"endpoint": "/authentication/run", "transport": "ag-ui-sse", "workflowIndependent": True, "actions": ["initialize", "login", "status", "cancel", "logout", "shutdown", "request"], "credentialStorage": "encrypted-environment-file", "expiresAtUnit": "unix-seconds"},
             "workflow_run": workflow_capabilities(),
             "application_page_planning": application_page_planning_capabilities(),
             "application_lifecycle": application_lifecycle_capabilities(),
```

### 5.7 Backend/requirements.txt

操作：**修改**；分类：**运行必需**。

原内容：原依赖没有为云效跨平台加密声明 keyring。

修改内容：追加 keyring>=25.6,<26。httpx 与 cryptography 已在原工程中，无需重复声明。

**与原工程逐项对照，共 1 处修改组。** 行号是原基线的位置，内网有差异时按相邻代码定位。每组都给出真实修改前后内容，其他部分保持原样。

修改组 1：原文件约第 20 行；目标文件约第 20 行。

修改前：

```text
code-review-graph==2.3.7
tree-sitter==0.26.0
tree-sitter-language-pack==0.11.0
```

修改后：

```text
code-review-graph==2.3.7
tree-sitter==0.26.0
tree-sitter-language-pack==0.11.0

# 非 Windows 平台的系统安全钥匙串；Windows 凭据直接使用 DPAPI。
keyring>=25.6,<26
```

**完整统一差异**（便于逐行核对；+/- 不属于源码）：

```diff
diff --git a/Backend/requirements.txt b/Backend/requirements.txt
--- a/Backend/requirements.txt
+++ b/Backend/requirements.txt
@@ -20,3 +20,6 @@
 code-review-graph==2.3.7
 tree-sitter==0.26.0
 tree-sitter-language-pack==0.11.0
+
+# 非 Windows 平台的系统安全钥匙串；Windows 凭据直接使用 DPAPI。
+keyring>=25.6,<26
```

### 5.8 Frontend/src/shared/authentication.ts

操作：**新增**；分类：**运行必需**。

原内容：原工程没有前后端桌面认证公开数据合同。

修改内容：新增 AuthenticationProgress、CloudJsonRequest、AuthenticationResult。只含公开状态、用户信息、授权 URL、进度及业务结果，不含 token。

**完整目标文件内容**（无省略；新建文件直接粘贴，小型旧文件确认基线后替换）：

```ts
/** 桌面认证公开合同；不包含用户 token。 */
export type AuthenticationProgress = {
  stage: string
  message: string
  percent: number
}

export type CloudJsonRequest = {
  url: string
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD'
  headers: Record<string, string>
  body?: string
}

export type AuthenticationResult = {
  status: 'completed' | 'failed' | 'in_progress'
  authenticated?: boolean
  workingDir?: string
  user?: { id: string; name: string }
  expiresAt?: number
  progress?: AuthenticationProgress
  authorizationUrl?: string
  httpStatus?: number
  body?: unknown
  error?: { message?: string }
}
```

### 5.9 Frontend/src/main/authClient.ts

操作：**新增**；分类：**运行必需**。

原内容：原工程没有独立 Python 认证流客户端。

修改内容：新增 runAuthentication()：HttpAgent SDK、本机后端限制、认证参数 forwardedProps、进度事件、最终结果校验、超时及回调异常传播。

**完整目标文件内容**（无省略；新建文件直接粘贴，小型旧文件确认基线后替换）：

```ts
import type { AuthenticationResult } from '../shared/authentication'

/** 通过 SDK 消费独立 Python 认证流，禁止自行解析 SSE。 */
export async function runAuthentication(
  baseUrl: string,
  input: Record<string, unknown>,
  onProgress?: (value: AuthenticationResult) => Promise<void> | void
): Promise<AuthenticationResult> {
  const { HttpAgent, randomUUID } = await import('@ag-ui/client')
  const endpoint = new URL(baseUrl)
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(endpoint.hostname)) {
    throw new Error('桌面认证必须连接本机 Python 后端。')
  }
  const agent = new HttpAgent({
    url: `${baseUrl.replace(/\/$/, '')}/authentication/run`,
    threadId: randomUUID()
  })
  let progressError: unknown
  const timer = setTimeout(() => agent.abortRun(), input.action === 'login' ? 910_000 : 15_000)
  try {
    const result = await agent.runAgent(
      { forwardedProps: { authentication: input } },
      {
        onCustomEvent: async ({ event }) => {
          if (event.name === 'authentication') {
            const value = event.value as AuthenticationResult
            if (value.status === 'in_progress') {
              try { await onProgress?.(value) } catch (error) {
                progressError = error
                agent.abortRun()
              }
            }
          }
        }
      }
    )
    if (progressError) throw progressError
    const payload = (result.result as { authentication?: AuthenticationResult } | undefined)?.authentication
    if (payload?.status !== 'completed') {
      throw new Error(payload?.error?.message || 'Python 认证接口未返回完整结果，请检查后端连接。')
    }
    return payload
  } catch (error) {
    throw progressError || error
  } finally {
    clearTimeout(timer)
  }
}
```

### 5.10 Frontend/src/main/auth.ts

操作：**修改**；分类：**运行必需**。

原内容：原文件使用 mock 生成 token，写明文文件，并以全局 accessToken 判断登录；还导出 getAccessToken()。

修改内容：替换为 Python 协调：sessionId/状态变量，首次点击才 initialize，loginWithYunxiao 打开系统浏览器，状态查询改异步，业务代理，shutdown 与本地清理兜底。删除全部模拟认证和长期 token 缓存。

**完整目标文件内容**（无省略；新建文件直接粘贴，小型旧文件确认基线后替换）：

```ts
import { app, shell } from 'electron'
import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'
import { DEVAGENTSTUDIO_ENV } from './env'
import { getBackendBaseUrl } from './backendService'
import { runAuthentication } from './authClient'
import type { AuthenticationProgress, CloudJsonRequest, AuthenticationResult } from '../shared/authentication'

const sessionId = crypto.randomUUID()
let loginPending = false
let quitting = false
let authenticationInitialized = false

/** 获取当前运行环境的数据目录，不缓存任何用户 token。 */
export function getDevAgentStudioDataDir(): string {
  return path.join(app.getPath('home'), DEVAGENTSTUDIO_ENV.WORKING_DIR)
}

/** 按需创建环境数据目录。 */
export async function ensureDevAgentStudioDataDir(): Promise<string> {
  const directory = getDevAgentStudioDataDir()
  await fs.mkdir(directory, { recursive: true })
  return directory
}

/** 通过标准 AG-UI 动作调用 Python 认证业务。 */
async function action(name: string, extra: Record<string, unknown> = {}): Promise<AuthenticationResult> {
  return runAuthentication(getBackendBaseUrl(), {
    action: name,
    sessionId,
    workingDir: DEVAGENTSTUDIO_ENV.WORKING_DIR,
    ...extra
  })
}

/** 仅在点击登录按钮后建立 Python 会话，启动登录窗口不依赖认证接口。 */
async function initializeAuthentication(): Promise<void> {
  if (authenticationInitialized) return
  await action('initialize')
  authenticationInitialized = true
}

/** 每次从 Python 的凭据文件取得公开登录状态。 */
export async function getAuthenticationStatus(): Promise<AuthenticationResult> {
  // 冷启动已清理凭据；尚未点击登录时直接显示原有登录窗口。
  if (!authenticationInitialized) return { status: 'completed', authenticated: false }
  return action('status')
}

/** 由后端读取文件和有效期，判断是否允许进入主窗口。 */
export async function hasValidAuthToken(): Promise<boolean> {
  return (await getAuthenticationStatus()).authenticated === true
}

/** 调用 Python 云效认证流，主进程只负责系统浏览器和公开进度。 */
export async function loginWithYunxiao(onProgress: (value: AuthenticationProgress) => void): Promise<void> {
  if (quitting) throw new Error('应用正在退出。')
  if (loginPending) throw new Error('已有登录正在进行。')
  loginPending = true
  try {
    await initializeAuthentication()
    if (quitting) throw new Error('应用正在退出。')
    await runAuthentication(getBackendBaseUrl(), {
      action: 'login', sessionId, workingDir: DEVAGENTSTUDIO_ENV.WORKING_DIR
    }, async (value) => {
      if (quitting) throw new Error('应用正在退出。')
      if (value.authorizationUrl) {
        const url = new URL(value.authorizationUrl)
        if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) {
          throw new Error('Python 返回了无效授权地址。')
        }
        await shell.openExternal(url.toString())
      }
      if (value.progress) onProgress(value.progress)
    })
    if (quitting || !(await hasValidAuthToken())) throw new Error('登录凭据未保存或已失效。')
  } catch (error) {
    if (!quitting && authenticationInitialized) await action('cancel').catch(() => undefined)
    throw error
  } finally {
    loginPending = false
  }
}

/** 取消后端轮询并清除当前登录凭据，允许用户重新登录。 */
export async function clearAuthState(): Promise<void> {
  if (authenticationInitialized) await action('logout')
  else await revokeLocalCredentials()
}

/** 读取凭据和携带 token 的云端业务请求均由 Python 完成。 */
export async function requestAuthenticatedCloud(input: CloudJsonRequest): Promise<{ httpStatus: number; body: unknown }> {
  const result = await action('request', { request: input })
  if (typeof result.httpStatus !== 'number') throw new Error('云端接口没有返回 HTTP 状态。')
  return { httpStatus: result.httpStatus, body: result.body }
}

/** 退出前使后端会话失效，旧登录任务不得再次保存凭据。 */
export async function shutdownAuthentication(): Promise<void> {
  quitting = true
  try {
    if (authenticationInitialized) await action('shutdown')
  } finally {
    await revokeLocalCredentials()
  }
}

/** 启动和退出时的本地清理兜底，仅写无秘密的栅栏并删除凭据。 */
export async function revokeLocalCredentials(): Promise<void> {
  const directory = path.join(getDevAgentStudioDataDir(), 'yst-login')
  await fs.mkdir(directory, { recursive: true })
  const metadata = await fs.lstat(directory)
  if (metadata.isSymbolicLink()) throw new Error('认证目录不能是符号链接。')
  const fence = path.join(directory, 'session.json')
  if ((await fs.lstat(fence).catch(() => undefined))?.isSymbolicLink()) throw new Error('认证会话文件不能是符号链接。')
  await fs.writeFile(fence, JSON.stringify({ sessionId, active: false }), { mode: 0o600 })
  await fs.unlink(path.join(directory, 'auth.json')).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== 'ENOENT') throw error
  })
  for (const name of await fs.readdir(directory)) {
    if (name.startsWith('.auth-') && name.endsWith('.tmp')) await fs.unlink(path.join(directory, name))
  }
}
```

**与原工程逐项对照，共 1 处修改组。** 行号是原基线的位置，内网有差异时按相邻代码定位。每组都给出真实修改前后内容，其他部分保持原样。

修改组 1：原文件约第 1 行；目标文件约第 1 行。

修改前：

```ts
import { app } from 'electron'
import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'
import { DEVAGENTSTUDIO_ENV } from './env'

export type AuthRecord = {
  access_token: string
}

const MOCK_LOGIN_DELAY_MS = 2000

let accessToken: string | null = null

/** 获取当前运行环境对应的 DevAgent Studio 数据目录。 */
export function getDevAgentStudioDataDir(): string {
  return path.join(app.getPath('home'), DEVAGENTSTUDIO_ENV.WORKING_DIR)
}

/** 确保当前运行环境对应的数据目录已经创建。 */
export async function ensureDevAgentStudioDataDir(): Promise<string> {
  const dataDir = getDevAgentStudioDataDir()
  await fs.mkdir(dataDir, { recursive: true })
  return dataDir
}

/** 等待指定时长，用于保留当前模拟登录的交互节奏。 */
function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms)
  })
}

/** 返回当前环境的认证记录文件路径。 */
function getAuthFile(): string {
  return path.join(getDevAgentStudioDataDir(), 'auth.json')
}

/** 将认证记录写入当前环境的 auth.json。 */
async function writeAuthRecord(authRecord: AuthRecord): Promise<void> {
  await ensureDevAgentStudioDataDir()
  await fs.writeFile(getAuthFile(), `${JSON.stringify(authRecord, null, 2)}\n`, {
    encoding: 'utf8',
    mode: 0o600
  })
}

/** 返回主进程内存中是否存在有效 access_token。 */
export function hasValidAuthToken(): boolean {
  return Boolean(accessToken)
}

/** 返回主进程内存中的 access_token，不触碰持久化文件。 */
export function getAccessToken(): string | null {
  return accessToken
}

/** 模拟 CMB Device Flow；后续在此替换为真实认证实现。 */
export async function authCmbDeviceFlow(): Promise<AuthRecord> {
  await delay(MOCK_LOGIN_DELAY_MS)
  return {
    access_token: `mock-access-token-${Date.now()}-${crypto.randomBytes(8).toString('hex')}`
  }
}

/** 执行登录流程，并在落盘成功后更新主进程内存。 */
export async function loginWithCmbDeviceFlow(): Promise<void> {
  const authRecord = await authCmbDeviceFlow()
  await writeAuthRecord(authRecord)
  accessToken = authRecord.access_token
}

/** 清空主进程认证内存并删除当前环境的 auth.json。 */
export async function clearAuthState(): Promise<void> {
  accessToken = null
  try {
    await fs.unlink(getAuthFile())
  } catch (error) {
    const errorCode =
      error && typeof error === 'object' && 'code' in error
        ? (error as { code?: string }).code
        : undefined
    if (errorCode !== 'ENOENT') throw error
  }
}
```

修改后：

```ts
import { app, shell } from 'electron'
import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'
import { DEVAGENTSTUDIO_ENV } from './env'
import { getBackendBaseUrl } from './backendService'
import { runAuthentication } from './authClient'
import type { AuthenticationProgress, CloudJsonRequest, AuthenticationResult } from '../shared/authentication'

const sessionId = crypto.randomUUID()
let loginPending = false
let quitting = false
let authenticationInitialized = false

/** 获取当前运行环境的数据目录，不缓存任何用户 token。 */
export function getDevAgentStudioDataDir(): string {
  return path.join(app.getPath('home'), DEVAGENTSTUDIO_ENV.WORKING_DIR)
}

/** 按需创建环境数据目录。 */
export async function ensureDevAgentStudioDataDir(): Promise<string> {
  const directory = getDevAgentStudioDataDir()
  await fs.mkdir(directory, { recursive: true })
  return directory
}

/** 通过标准 AG-UI 动作调用 Python 认证业务。 */
async function action(name: string, extra: Record<string, unknown> = {}): Promise<AuthenticationResult> {
  return runAuthentication(getBackendBaseUrl(), {
    action: name,
    sessionId,
    workingDir: DEVAGENTSTUDIO_ENV.WORKING_DIR,
    ...extra
  })
}

/** 仅在点击登录按钮后建立 Python 会话，启动登录窗口不依赖认证接口。 */
async function initializeAuthentication(): Promise<void> {
  if (authenticationInitialized) return
  await action('initialize')
  authenticationInitialized = true
}

/** 每次从 Python 的凭据文件取得公开登录状态。 */
export async function getAuthenticationStatus(): Promise<AuthenticationResult> {
  // 冷启动已清理凭据；尚未点击登录时直接显示原有登录窗口。
  if (!authenticationInitialized) return { status: 'completed', authenticated: false }
  return action('status')
}

/** 由后端读取文件和有效期，判断是否允许进入主窗口。 */
export async function hasValidAuthToken(): Promise<boolean> {
  return (await getAuthenticationStatus()).authenticated === true
}

/** 调用 Python 云效认证流，主进程只负责系统浏览器和公开进度。 */
export async function loginWithYunxiao(onProgress: (value: AuthenticationProgress) => void): Promise<void> {
  if (quitting) throw new Error('应用正在退出。')
  if (loginPending) throw new Error('已有登录正在进行。')
  loginPending = true
  try {
    await initializeAuthentication()
    if (quitting) throw new Error('应用正在退出。')
    await runAuthentication(getBackendBaseUrl(), {
      action: 'login', sessionId, workingDir: DEVAGENTSTUDIO_ENV.WORKING_DIR
    }, async (value) => {
      if (quitting) throw new Error('应用正在退出。')
      if (value.authorizationUrl) {
        const url = new URL(value.authorizationUrl)
        if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) {
          throw new Error('Python 返回了无效授权地址。')
        }
        await shell.openExternal(url.toString())
      }
      if (value.progress) onProgress(value.progress)
    })
    if (quitting || !(await hasValidAuthToken())) throw new Error('登录凭据未保存或已失效。')
  } catch (error) {
    if (!quitting && authenticationInitialized) await action('cancel').catch(() => undefined)
    throw error
  } finally {
    loginPending = false
  }
}

/** 取消后端轮询并清除当前登录凭据，允许用户重新登录。 */
export async function clearAuthState(): Promise<void> {
  if (authenticationInitialized) await action('logout')
  else await revokeLocalCredentials()
}

/** 读取凭据和携带 token 的云端业务请求均由 Python 完成。 */
export async function requestAuthenticatedCloud(input: CloudJsonRequest): Promise<{ httpStatus: number; body: unknown }> {
  const result = await action('request', { request: input })
  if (typeof result.httpStatus !== 'number') throw new Error('云端接口没有返回 HTTP 状态。')
  return { httpStatus: result.httpStatus, body: result.body }
}

/** 退出前使后端会话失效，旧登录任务不得再次保存凭据。 */
export async function shutdownAuthentication(): Promise<void> {
  quitting = true
  try {
    if (authenticationInitialized) await action('shutdown')
  } finally {
    await revokeLocalCredentials()
  }
}

/** 启动和退出时的本地清理兜底，仅写无秘密的栅栏并删除凭据。 */
export async function revokeLocalCredentials(): Promise<void> {
  const directory = path.join(getDevAgentStudioDataDir(), 'yst-login')
  await fs.mkdir(directory, { recursive: true })
  const metadata = await fs.lstat(directory)
  if (metadata.isSymbolicLink()) throw new Error('认证目录不能是符号链接。')
  const fence = path.join(directory, 'session.json')
  if ((await fs.lstat(fence).catch(() => undefined))?.isSymbolicLink()) throw new Error('认证会话文件不能是符号链接。')
  await fs.writeFile(fence, JSON.stringify({ sessionId, active: false }), { mode: 0o600 })
  await fs.unlink(path.join(directory, 'auth.json')).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== 'ENOENT') throw error
  })
  for (const name of await fs.readdir(directory)) {
    if (name.startsWith('.auth-') && name.endsWith('.tmp')) await fs.unlink(path.join(directory, name))
  }
}
```

**完整统一差异**（便于逐行核对；+/- 不属于源码）：

```diff
diff --git a/Frontend/src/main/auth.ts b/Frontend/src/main/auth.ts
--- a/Frontend/src/main/auth.ts
+++ b/Frontend/src/main/auth.ts
@@ -1,85 +1,124 @@
-import { app } from 'electron'
+import { app, shell } from 'electron'
 import crypto from 'node:crypto'
 import fs from 'node:fs/promises'
 import path from 'node:path'
 import { DEVAGENTSTUDIO_ENV } from './env'
+import { getBackendBaseUrl } from './backendService'
+import { runAuthentication } from './authClient'
+import type { AuthenticationProgress, CloudJsonRequest, AuthenticationResult } from '../shared/authentication'
 
-export type AuthRecord = {
-  access_token: string
-}
+const sessionId = crypto.randomUUID()
+let loginPending = false
+let quitting = false
+let authenticationInitialized = false
 
-const MOCK_LOGIN_DELAY_MS = 2000
-
-let accessToken: string | null = null
-
-/** 获取当前运行环境对应的 DevAgent Studio 数据目录。 */
+/** 获取当前运行环境的数据目录，不缓存任何用户 token。 */
 export function getDevAgentStudioDataDir(): string {
   return path.join(app.getPath('home'), DEVAGENTSTUDIO_ENV.WORKING_DIR)
 }
 
-/** 确保当前运行环境对应的数据目录已经创建。 */
+/** 按需创建环境数据目录。 */
 export async function ensureDevAgentStudioDataDir(): Promise<string> {
-  const dataDir = getDevAgentStudioDataDir()
-  await fs.mkdir(dataDir, { recursive: true })
-  return dataDir
+  const directory = getDevAgentStudioDataDir()
+  await fs.mkdir(directory, { recursive: true })
+  return directory
 }
 
-/** 等待指定时长，用于保留当前模拟登录的交互节奏。 */
-function delay(ms: number): Promise<void> {
-  return new Promise((resolve) => {
-    setTimeout(resolve, ms)
+/** 通过标准 AG-UI 动作调用 Python 认证业务。 */
+async function action(name: string, extra: Record<string, unknown> = {}): Promise<AuthenticationResult> {
+  return runAuthentication(getBackendBaseUrl(), {
+    action: name,
+    sessionId,
+    workingDir: DEVAGENTSTUDIO_ENV.WORKING_DIR,
+    ...extra
   })
 }
 
-/** 返回当前环境的认证记录文件路径。 */
-function getAuthFile(): string {
-  return path.join(getDevAgentStudioDataDir(), 'auth.json')
+/** 仅在点击登录按钮后建立 Python 会话，启动登录窗口不依赖认证接口。 */
+async function initializeAuthentication(): Promise<void> {
+  if (authenticationInitialized) return
+  await action('initialize')
+  authenticationInitialized = true
 }
 
-/** 将认证记录写入当前环境的 auth.json。 */
-async function writeAuthRecord(authRecord: AuthRecord): Promise<void> {
-  await ensureDevAgentStudioDataDir()
-  await fs.writeFile(getAuthFile(), `${JSON.stringify(authRecord, null, 2)}\n`, {
-    encoding: 'utf8',
-    mode: 0o600
-  })
+/** 每次从 Python 的凭据文件取得公开登录状态。 */
+export async function getAuthenticationStatus(): Promise<AuthenticationResult> {
+  // 冷启动已清理凭据；尚未点击登录时直接显示原有登录窗口。
+  if (!authenticationInitialized) return { status: 'completed', authenticated: false }
+  return action('status')
 }
 
-/** 返回主进程内存中是否存在有效 access_token。 */
-export function hasValidAuthToken(): boolean {
-  return Boolean(accessToken)
+/** 由后端读取文件和有效期，判断是否允许进入主窗口。 */
+export async function hasValidAuthToken(): Promise<boolean> {
+  return (await getAuthenticationStatus()).authenticated === true
 }
 
-/** 返回主进程内存中的 access_token，不触碰持久化文件。 */
-export function getAccessToken(): string | null {
-  return accessToken
-}
-
-/** 模拟 CMB Device Flow；后续在此替换为真实认证实现。 */
-export async function authCmbDeviceFlow(): Promise<AuthRecord> {
-  await delay(MOCK_LOGIN_DELAY_MS)
-  return {
-    access_token: `mock-access-token-${Date.now()}-${crypto.randomBytes(8).toString('hex')}`
+/** 调用 Python 云效认证流，主进程只负责系统浏览器和公开进度。 */
+export async function loginWithYunxiao(onProgress: (value: AuthenticationProgress) => void): Promise<void> {
+  if (quitting) throw new Error('应用正在退出。')
+  if (loginPending) throw new Error('已有登录正在进行。')
+  loginPending = true
+  try {
+    await initializeAuthentication()
+    if (quitting) throw new Error('应用正在退出。')
+    await runAuthentication(getBackendBaseUrl(), {
+      action: 'login', sessionId, workingDir: DEVAGENTSTUDIO_ENV.WORKING_DIR
+    }, async (value) => {
+      if (quitting) throw new Error('应用正在退出。')
+      if (value.authorizationUrl) {
+        const url = new URL(value.authorizationUrl)
+        if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) {
+          throw new Error('Python 返回了无效授权地址。')
+        }
+        await shell.openExternal(url.toString())
+      }
+      if (value.progress) onProgress(value.progress)
+    })
+    if (quitting || !(await hasValidAuthToken())) throw new Error('登录凭据未保存或已失效。')
+  } catch (error) {
+    if (!quitting && authenticationInitialized) await action('cancel').catch(() => undefined)
+    throw error
+  } finally {
+    loginPending = false
   }
 }
 
-/** 执行登录流程，并在落盘成功后更新主进程内存。 */
-export async function loginWithCmbDeviceFlow(): Promise<void> {
-  const authRecord = await authCmbDeviceFlow()
-  await writeAuthRecord(authRecord)
-  accessToken = authRecord.access_token
+/** 取消后端轮询并清除当前登录凭据，允许用户重新登录。 */
+export async function clearAuthState(): Promise<void> {
+  if (authenticationInitialized) await action('logout')
+  else await revokeLocalCredentials()
 }
 
-/** 清空主进程认证内存并删除当前环境的 auth.json。 */
-export async function clearAuthState(): Promise<void> {
-  accessToken = null
+/** 读取凭据和携带 token 的云端业务请求均由 Python 完成。 */
+export async function requestAuthenticatedCloud(input: CloudJsonRequest): Promise<{ httpStatus: number; body: unknown }> {
+  const result = await action('request', { request: input })
+  if (typeof result.httpStatus !== 'number') throw new Error('云端接口没有返回 HTTP 状态。')
+  return { httpStatus: result.httpStatus, body: result.body }
+}
+
+/** 退出前使后端会话失效，旧登录任务不得再次保存凭据。 */
+export async function shutdownAuthentication(): Promise<void> {
+  quitting = true
   try {
-    await fs.unlink(getAuthFile())
-  } catch (error) {
-    const errorCode =
-      error && typeof error === 'object' && 'code' in error
-        ? (error as { code?: string }).code
-        : undefined
-    if (errorCode !== 'ENOENT') throw error
+    if (authenticationInitialized) await action('shutdown')
+  } finally {
+    await revokeLocalCredentials()
   }
 }
+
+/** 启动和退出时的本地清理兜底，仅写无秘密的栅栏并删除凭据。 */
+export async function revokeLocalCredentials(): Promise<void> {
+  const directory = path.join(getDevAgentStudioDataDir(), 'yst-login')
+  await fs.mkdir(directory, { recursive: true })
+  const metadata = await fs.lstat(directory)
+  if (metadata.isSymbolicLink()) throw new Error('认证目录不能是符号链接。')
+  const fence = path.join(directory, 'session.json')
+  if ((await fs.lstat(fence).catch(() => undefined))?.isSymbolicLink()) throw new Error('认证会话文件不能是符号链接。')
+  await fs.writeFile(fence, JSON.stringify({ sessionId, active: false }), { mode: 0o600 })
+  await fs.unlink(path.join(directory, 'auth.json')).catch((error: NodeJS.ErrnoException) => {
+    if (error.code !== 'ENOENT') throw error
+  })
+  for (const name of await fs.readdir(directory)) {
+    if (name.startsWith('.auth-') && name.endsWith('.tmp')) await fs.unlink(path.join(directory, name))
+  }
+}
```

### 5.11 Frontend/src/main/index.ts

操作：**修改**；分类：**运行必需**。

原内容：原 auth IPC 调用模拟登录、暴露 get-access-token；openAuthenticatedWindow 同步判断内存 token；启动/退出清理旧路径文件。

修改内容：替换认证导入与 setupAuthIpc；移除 get-access-token，增加 cancel/progress/cloud-request；状态判断加 await；启动清理 yst-login；退出 Python shutdown 后本地清理并最终再清理。只按差异修改，不覆盖整个大型文件。

**与原工程逐项对照，共 6 处修改组。** 行号是原基线的位置，内网有差异时按相邻代码定位。每组都给出真实修改前后内容，其他部分保持原样。

修改组 1：原文件约第 26 行；目标文件约第 26 行。

修改前：

```ts
import {
  clearAuthState,
  ensureDevAgentStudioDataDir,
  getAccessToken,
  getDevAgentStudioDataDir,
  hasValidAuthToken,
  loginWithCmbDeviceFlow
} from './auth'

registerDesignRuntimeScheme()
```

修改后：

```ts
import {
  clearAuthState,
  ensureDevAgentStudioDataDir,
  getAuthenticationStatus,
  revokeLocalCredentials,
  shutdownAuthentication,
  requestAuthenticatedCloud,
  getDevAgentStudioDataDir,
  hasValidAuthToken,
  loginWithYunxiao
} from './auth'

registerDesignRuntimeScheme()
```

修改组 2：原文件约第 1677 行；目标文件约第 1680 行。

修改前：

```ts
  })
}

/** 注册登录、内存 token 读取和重新认证所需的 Electron IPC。 */
function setupAuthIpc(): void {
  ipcMain.handle('auth:status', async () => ({
    authenticated: hasValidAuthToken()
  }))

  ipcMain.handle('auth:get-access-token', async () => ({
    accessToken: getAccessToken()
  }))

  ipcMain.handle('auth:login', async () => {
    await loginWithCmbDeviceFlow()
    if (loginWindow && !loginWindow.isDestroyed()) {
      loginWindow.destroy()
    }
```

修改后：

```ts
  })
}

/** 注册 Python 认证桥接、公开进度及重新登录所需的 Electron IPC。 */
function setupAuthIpc(): void {
  ipcMain.handle('auth:status', async () => getAuthenticationStatus())
  ipcMain.handle('auth:cloud-request', async (_event, request) => requestAuthenticatedCloud(request))
  ipcMain.handle('auth:cancel', async () => { await clearAuthState(); return { ok: true } })

  ipcMain.handle('auth:login', async (event) => {
    if (isQuitting || event.sender !== loginWindow?.webContents) throw new Error('当前窗口不能发起登录。')
    await loginWithYunxiao((progress) => {
      if (!event.sender.isDestroyed()) event.sender.send('auth:progress', progress)
    })
    if (isQuitting) throw new Error('应用正在退出。')
    if (loginWindow && !loginWindow.isDestroyed()) {
      loginWindow.destroy()
    }
```

修改组 3：原文件约第 1816 行；目标文件约第 1819 行。

修改前：

```ts
  loadRendererPage(loginWindow, 'login')
}

/** 根据主进程内存中的登录态打开主窗口或登录窗口。 */
async function openAuthenticatedWindow(): Promise<void> {
  if (hasValidAuthToken()) {
    if (loginWindow && !loginWindow.isDestroyed()) {
      loginWindow.hide()
    }
```

修改后：

```ts
  loadRendererPage(loginWindow, 'login')
}

/** 根据 Python 凭据文件中的有效登录态打开主窗口或登录窗口。 */
async function openAuthenticatedWindow(): Promise<void> {
  if (await hasValidAuthToken()) {
    if (loginWindow && !loginWindow.isDestroyed()) {
      loginWindow.hide()
    }
```

修改组 4：原文件约第 1876 行；目标文件约第 1879 行。

修改前：

```ts
async function clearAuthStateBeforeStartup(): Promise<boolean> {
  try {
    await ensureDevAgentStudioDataDir()
    await clearAuthState()
    return true
  } catch (error) {
    const authFile = path.join(getDevAgentStudioDataDir(), 'auth.json')
    console.error('Failed to clear auth token during startup', error)
    dialog.showErrorBox(
      '认证状态清理失败',
```

修改后：

```ts
async function clearAuthStateBeforeStartup(): Promise<boolean> {
  try {
    await ensureDevAgentStudioDataDir()
    await revokeLocalCredentials()
    return true
  } catch (error) {
    const authFile = path.join(getDevAgentStudioDataDir(), 'yst-login', 'auth.json')
    console.error('Failed to clear auth token during startup', error)
    dialog.showErrorBox(
      '认证状态清理失败',
```

修改组 5：原文件约第 1976 行；目标文件约第 1979 行。

修改前：

```ts
/** 在应用退出前清除认证状态并停止本地后端服务。 */
async function cleanupBeforeQuit(): Promise<void> {
  try {
    await clearAuthState()
  } catch (error) {
    console.error('Failed to clear auth token', error)
  }
```

修改后：

```ts
/** 在应用退出前清除认证状态并停止本地后端服务。 */
async function cleanupBeforeQuit(): Promise<void> {
  try {
    await shutdownAuthentication()
  } catch (error) {
    console.error('Failed to clear auth token', error)
  }
```

修改组 6：原文件约第 1992 行；目标文件约第 1995 行。

修改前：

```ts
  } catch (error) {
    console.error('Failed to stop backend service', error)
  }
}

if (hasSingleInstanceLock) {
```

修改后：

```ts
  } catch (error) {
    console.error('Failed to stop backend service', error)
  }
  // 后端结束后再清理一次，覆盖强制终止或迟到写入的竞态。
  try {
    await revokeLocalCredentials()
  } catch (error) {
    console.error('认证文件最终清理失败', error)
    dialog.showErrorBox('登录凭据清理失败', '请检查用户数据目录的文件权限。下次启动应用会再次清理残留凭据。')
  }
}

if (hasSingleInstanceLock) {
```

**完整统一差异**（便于逐行核对；+/- 不属于源码）：

```diff
diff --git a/Frontend/src/main/index.ts b/Frontend/src/main/index.ts
--- a/Frontend/src/main/index.ts
+++ b/Frontend/src/main/index.ts
@@ -26,10 +26,13 @@
 import {
   clearAuthState,
   ensureDevAgentStudioDataDir,
-  getAccessToken,
+  getAuthenticationStatus,
+  revokeLocalCredentials,
+  shutdownAuthentication,
+  requestAuthenticatedCloud,
   getDevAgentStudioDataDir,
   hasValidAuthToken,
-  loginWithCmbDeviceFlow
+  loginWithYunxiao
 } from './auth'
 
 registerDesignRuntimeScheme()
@@ -1677,18 +1680,18 @@
   })
 }
 
-/** 注册登录、内存 token 读取和重新认证所需的 Electron IPC。 */
+/** 注册 Python 认证桥接、公开进度及重新登录所需的 Electron IPC。 */
 function setupAuthIpc(): void {
-  ipcMain.handle('auth:status', async () => ({
-    authenticated: hasValidAuthToken()
-  }))
-
-  ipcMain.handle('auth:get-access-token', async () => ({
-    accessToken: getAccessToken()
-  }))
-
-  ipcMain.handle('auth:login', async () => {
-    await loginWithCmbDeviceFlow()
+  ipcMain.handle('auth:status', async () => getAuthenticationStatus())
+  ipcMain.handle('auth:cloud-request', async (_event, request) => requestAuthenticatedCloud(request))
+  ipcMain.handle('auth:cancel', async () => { await clearAuthState(); return { ok: true } })
+
+  ipcMain.handle('auth:login', async (event) => {
+    if (isQuitting || event.sender !== loginWindow?.webContents) throw new Error('当前窗口不能发起登录。')
+    await loginWithYunxiao((progress) => {
+      if (!event.sender.isDestroyed()) event.sender.send('auth:progress', progress)
+    })
+    if (isQuitting) throw new Error('应用正在退出。')
     if (loginWindow && !loginWindow.isDestroyed()) {
       loginWindow.destroy()
     }
@@ -1816,9 +1819,9 @@
   loadRendererPage(loginWindow, 'login')
 }
 
-/** 根据主进程内存中的登录态打开主窗口或登录窗口。 */
+/** 根据 Python 凭据文件中的有效登录态打开主窗口或登录窗口。 */
 async function openAuthenticatedWindow(): Promise<void> {
-  if (hasValidAuthToken()) {
+  if (await hasValidAuthToken()) {
     if (loginWindow && !loginWindow.isDestroyed()) {
       loginWindow.hide()
     }
@@ -1876,10 +1879,10 @@
 async function clearAuthStateBeforeStartup(): Promise<boolean> {
   try {
     await ensureDevAgentStudioDataDir()
-    await clearAuthState()
+    await revokeLocalCredentials()
     return true
   } catch (error) {
-    const authFile = path.join(getDevAgentStudioDataDir(), 'auth.json')
+    const authFile = path.join(getDevAgentStudioDataDir(), 'yst-login', 'auth.json')
     console.error('Failed to clear auth token during startup', error)
     dialog.showErrorBox(
       '认证状态清理失败',
@@ -1976,7 +1979,7 @@
 /** 在应用退出前清除认证状态并停止本地后端服务。 */
 async function cleanupBeforeQuit(): Promise<void> {
   try {
-    await clearAuthState()
+    await shutdownAuthentication()
   } catch (error) {
     console.error('Failed to clear auth token', error)
   }
@@ -1992,6 +1995,13 @@
   } catch (error) {
     console.error('Failed to stop backend service', error)
   }
+  // 后端结束后再清理一次，覆盖强制终止或迟到写入的竞态。
+  try {
+    await revokeLocalCredentials()
+  } catch (error) {
+    console.error('认证文件最终清理失败', error)
+    dialog.showErrorBox('登录凭据清理失败', '请检查用户数据目录的文件权限。下次启动应用会再次清理残留凭据。')
+  }
 }
 
 if (hasSingleInstanceLock) {
```

### 5.12 Frontend/src/preload/index.ts

操作：**修改**；分类：**运行必需**。

原内容：原 preload 暴露 getAccessToken，没有取消、进度订阅、云端请求代理。

修改内容：删除 getAccessToken；增加 cancel、requestCloudJson、onProgress（返回取消订阅函数）；保留 login/status/reauthenticate。

**与原工程逐项对照，共 1 处修改组。** 行号是原基线的位置，内网有差异时按相邻代码定位。每组都给出真实修改前后内容，其他部分保持原样。

修改组 1：原文件约第 19 行；目标文件约第 19 行。

修改前：

```ts
  auth: {
    login: () => ipcRenderer.invoke('auth:login'),
    status: () => ipcRenderer.invoke('auth:status'),
    getAccessToken: () => ipcRenderer.invoke('auth:get-access-token'),
    reauthenticate: () => ipcRenderer.invoke('auth:reauthenticate'),
  },
  settings: {
```

修改后：

```ts
  auth: {
    login: () => ipcRenderer.invoke('auth:login'),
    status: () => ipcRenderer.invoke('auth:status'),
    cancel: () => ipcRenderer.invoke('auth:cancel'),
    requestCloudJson: (request) => ipcRenderer.invoke('auth:cloud-request', request),
    onProgress: (listener) => {
      const handler = (_event, progress) => listener(progress)
      ipcRenderer.on('auth:progress', handler)
      return () => ipcRenderer.removeListener('auth:progress', handler)
    },
    reauthenticate: () => ipcRenderer.invoke('auth:reauthenticate'),
  },
  settings: {
```

**完整统一差异**（便于逐行核对；+/- 不属于源码）：

```diff
diff --git a/Frontend/src/preload/index.ts b/Frontend/src/preload/index.ts
--- a/Frontend/src/preload/index.ts
+++ b/Frontend/src/preload/index.ts
@@ -19,7 +19,13 @@
   auth: {
     login: () => ipcRenderer.invoke('auth:login'),
     status: () => ipcRenderer.invoke('auth:status'),
-    getAccessToken: () => ipcRenderer.invoke('auth:get-access-token'),
+    cancel: () => ipcRenderer.invoke('auth:cancel'),
+    requestCloudJson: (request) => ipcRenderer.invoke('auth:cloud-request', request),
+    onProgress: (listener) => {
+      const handler = (_event, progress) => listener(progress)
+      ipcRenderer.on('auth:progress', handler)
+      return () => ipcRenderer.removeListener('auth:progress', handler)
+    },
     reauthenticate: () => ipcRenderer.invoke('auth:reauthenticate'),
   },
   settings: {
```

### 5.13 Frontend/src/preload/index.d.ts

操作：**修改**；分类：**运行必需**。

原内容：原声明包含 getAccessToken，不含新增桥接类型。

修改内容：导入 shared/authentication 的类型；同步删除 token getter 并增加 cancel/requestCloudJson/onProgress 签名。

**与原工程逐项对照，共 2 处修改组。** 行号是原基线的位置，内网有差异时按相邻代码定位。每组都给出真实修改前后内容，其他部分保持原样。

修改组 1：原文件约第 1 行；目标文件约第 1 行。

修改前：

```ts
import { ElectronAPI } from '@electron-toolkit/preload'
import type {
  ApplicationSchemaConfig,
  DevelopmentPlanningPageTreeNode
```

修改后：

```ts
import { ElectronAPI } from '@electron-toolkit/preload'
import type { AuthenticationProgress, CloudJsonRequest } from '../shared/authentication'
import type {
  ApplicationSchemaConfig,
  DevelopmentPlanningPageTreeNode
```

修改组 2：原文件约第 15 行；目标文件约第 16 行。

修改前：

```ts
      auth: {
        login: () => Promise<{ ok: true }>
        status: () => Promise<{ authenticated: boolean }>
        getAccessToken: () => Promise<{ accessToken: string | null }>
        reauthenticate: () => Promise<{ ok: true }>
      }
      settings: {
```

修改后：

```ts
      auth: {
        login: () => Promise<{ ok: true }>
        status: () => Promise<{ authenticated: boolean }>
        cancel: () => Promise<{ ok: true }>
        requestCloudJson: (request: CloudJsonRequest) => Promise<{ httpStatus: number; body: unknown }>
        onProgress: (listener: (progress: AuthenticationProgress) => void) => () => void
        reauthenticate: () => Promise<{ ok: true }>
      }
      settings: {
```

**完整统一差异**（便于逐行核对；+/- 不属于源码）：

```diff
diff --git a/Frontend/src/preload/index.d.ts b/Frontend/src/preload/index.d.ts
--- a/Frontend/src/preload/index.d.ts
+++ b/Frontend/src/preload/index.d.ts
@@ -1,4 +1,5 @@
 import { ElectronAPI } from '@electron-toolkit/preload'
+import type { AuthenticationProgress, CloudJsonRequest } from '../shared/authentication'
 import type {
   ApplicationSchemaConfig,
   DevelopmentPlanningPageTreeNode
@@ -15,7 +16,9 @@
       auth: {
         login: () => Promise<{ ok: true }>
         status: () => Promise<{ authenticated: boolean }>
-        getAccessToken: () => Promise<{ accessToken: string | null }>
+        cancel: () => Promise<{ ok: true }>
+        requestCloudJson: (request: CloudJsonRequest) => Promise<{ httpStatus: number; body: unknown }>
+        onProgress: (listener: (progress: AuthenticationProgress) => void) => () => void
         reauthenticate: () => Promise<{ ok: true }>
       }
       settings: {
```

### 5.14 Frontend/src/renderer/src/window.d.ts

操作：**修改**；分类：**运行必需**。

原内容：原渲染器 window 类型暴露 getAccessToken，不含新增桥接方法。

修改内容：与 preload 同步更新公开方法和类型，确保编译与运行接口一致。

**与原工程逐项对照，共 2 处修改组。** 行号是原基线的位置，内网有差异时按相邻代码定位。每组都给出真实修改前后内容，其他部分保持原样。

修改组 1：原文件约第 1 行；目标文件约第 1 行。

修改前：

```ts
export {}

import type { ElectronAPI } from '@electron-toolkit/preload'
```

修改后：

```ts
import type { AuthenticationProgress, CloudJsonRequest } from '../../shared/authentication'
export {}

import type { ElectronAPI } from '@electron-toolkit/preload'
```

修改组 2：原文件约第 16 行；目标文件约第 17 行。

修改前：

```ts
      auth: {
        login: () => Promise<{ ok: true }>
        status: () => Promise<{ authenticated: boolean }>
        getAccessToken: () => Promise<{ accessToken: string | null }>
        reauthenticate: () => Promise<{ ok: true }>
      }
      settings: {
```

修改后：

```ts
      auth: {
        login: () => Promise<{ ok: true }>
        status: () => Promise<{ authenticated: boolean }>
        cancel: () => Promise<{ ok: true }>
        requestCloudJson: (request: CloudJsonRequest) => Promise<{ httpStatus: number; body: unknown }>
        onProgress: (listener: (progress: AuthenticationProgress) => void) => () => void
        reauthenticate: () => Promise<{ ok: true }>
      }
      settings: {
```

**完整统一差异**（便于逐行核对；+/- 不属于源码）：

```diff
diff --git a/Frontend/src/renderer/src/window.d.ts b/Frontend/src/renderer/src/window.d.ts
--- a/Frontend/src/renderer/src/window.d.ts
+++ b/Frontend/src/renderer/src/window.d.ts
@@ -1,3 +1,4 @@
+import type { AuthenticationProgress, CloudJsonRequest } from '../../shared/authentication'
 export {}
 
 import type { ElectronAPI } from '@electron-toolkit/preload'
@@ -16,7 +17,9 @@
       auth: {
         login: () => Promise<{ ok: true }>
         status: () => Promise<{ authenticated: boolean }>
-        getAccessToken: () => Promise<{ accessToken: string | null }>
+        cancel: () => Promise<{ ok: true }>
+        requestCloudJson: (request: CloudJsonRequest) => Promise<{ httpStatus: number; body: unknown }>
+        onProgress: (listener: (progress: AuthenticationProgress) => void) => () => void
         reauthenticate: () => Promise<{ ok: true }>
       }
       settings: {
```

### 5.15 Frontend/src/renderer/src/login.tsx

操作：**修改**；分类：**运行必需**。

原内容：原页面调用模拟登录，无云效授权进度、取消操作和对应说明。

修改内容：增加 progress state、onProgress 订阅与卸载清理、handleCancel、进度/错误提示；按钮改为使用云效登录/等待云效授权；保留原主题与关闭窗口行为。

**完整目标文件内容**（无省略；新建文件直接粘贴，小型旧文件确认基线后替换）：

```tsx
import React, { useEffect, useState } from 'react'
import ReactDOM from 'react-dom/client'
import {
  ArrowRightOutlined,
  CheckCircleFilled,
  CloseOutlined,
  CodeOutlined,
  SafetyCertificateOutlined
} from '@ant-design/icons'
import { Button, Typography, message } from 'antd'
import 'antd/dist/antd.less'
import './styles/global.less'
import { cx } from './utils'
import { ApplicationThemeProvider, useApplicationTheme } from './hooks/useApplicationTheme'
import { loadApplicationTheme } from './service/applicationSettings'
import './login.less'

const { Paragraph, Text, Title } = Typography

const CAPABILITIES = [
  'Workflow 智能编排',
  '本地工作区安全访问',
  '多 Agent 协同开发'
]

/** 将未知异常转换为登录界面可展示的消息。 */
function formatError(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

/** 渲染带有自定义关闭控件和设备流入口的无边框登录窗口。 */
export function LoginApp(): JSX.Element {
  const [loggingIn, setLoggingIn] = useState(false)
  const [progress, setProgress] = useState('')
  useEffect(() => window.devAgentStudio?.auth.onProgress((value) => setProgress(value.message)), [])

  /** 显式取消 Python 中的授权轮询后再允许重新登录。 */
  const handleCancel = async (): Promise<void> => {
    try {
      await window.devAgentStudio?.auth.cancel()
      setProgress('登录已取消，可以重新发起登录。')
    } catch (error) {
      message.error(formatError(error, '取消登录失败'))
    }
  }
  const { theme } = useApplicationTheme()

  /** 请求关闭当前登录窗口，主进程会按既有逻辑隐藏窗口并保留应用后台运行。 */
  const handleClose = (): void => {
    window.close()
  }

  /** 调用 Electron 登录流程并保持按钮加载状态。 */
  const handleLogin = async (): Promise<void> => {
    const authApi = window.devAgentStudio?.auth
    if (!authApi?.login) {
      message.error('当前环境不支持登录。')
      return
    }

    setLoggingIn(true)
    setProgress('正在连接 Python 认证服务…')
    try {
      const result = await authApi.login()
      if (!result.ok) {
        throw new Error('登录失败')
      }
    } catch (error) {
      const errorMessage = formatError(error, '登录失败')
      setProgress(errorMessage)
      message.error(errorMessage)
      setLoggingIn(false)
    }
  }

  return (
    <div className={cx('login-page')} data-theme={theme}>
      <div aria-hidden="true" className={cx('login-grid')} />
      <div aria-hidden="true" className={cx('login-orb', 'login-orb-primary')} />
      <div aria-hidden="true" className={cx('login-orb', 'login-orb-secondary')} />

      <main className={cx('login-shell')}>
        <section className={cx('login-brand-panel')}>
          <header className={cx('login-brand-header')}>
            <div className={cx('login-brand')}>
              <span className={cx('login-logo')}>
                <CodeOutlined />
              </span>
              <strong>DevAgent Studio</strong>
            </div>
          </header>

          <div className={cx('login-hero')}>
            <Text className={cx('login-kicker')}>BUILD / ORCHESTRATE / DELIVER</Text>
            <Title level={1}>让每一次构建，都更接近答案</Title>
            <Paragraph>
              从需求梳理到代码交付，在一个可追踪、可确认的智能工程工作台中完成。
            </Paragraph>
          </div>

          <div aria-hidden="true" className={cx('login-terminal')}>
            <div className={cx('login-terminal-bar')}>
              <span /><span /><span />
              <code>agent://secure-session</code>
            </div>
            <div className={cx('login-terminal-body')}>
              <code><em>$</em> DevAgent Studio connect --workspace</code>
              <code><b>✓</b> runtime ready · context protected</code>
              <span className={cx('login-terminal-cursor')} />
            </div>
          </div>

          <div className={cx('login-capabilities')}>
            {CAPABILITIES.map((item) => (
              <span key={item}>
                <CheckCircleFilled />
                {item}
              </span>
            ))}
          </div>
        </section>

        <section className={cx('login-auth-panel')}>
          <button
            aria-label="关闭登录窗口"
            className={cx('login-close')}
            onClick={handleClose}
            type="button"
          >
            <CloseOutlined />
          </button>

          <div className={cx('login-auth-copy')}>
            <span className={cx('login-auth-icon')}>
              <SafetyCertificateOutlined />
            </span>
            <Title level={2}>欢迎回来</Title>
            <Paragraph>登录后继续访问你的工作区、会话与智能开发流程。</Paragraph>
          </div>

          <div className={cx('login-action')}>
            <Button
              block
              icon={<ArrowRightOutlined />}
              loading={loggingIn}
              onClick={handleLogin}
              size="large"
              type="primary"
            >
              {loggingIn ? '等待云效授权' : '使用云效登录'}
            </Button>
            <Text className={cx('login-action-hint')} role="status" aria-live="polite">
              {progress || '将在系统浏览器中完成云效登录'}
            </Text>
            {loggingIn && <Button type="text" onClick={() => void handleCancel()}>取消登录</Button>}
            <Text className={cx('login-action-hint')}>
              <SafetyCertificateOutlined /> 凭证在本机加密保存，退出应用后删除
            </Text>
          </div>
        </section>
      </main>
    </div>
  )
}

/** 在登录窗口首次渲染前读取应用级主题。 */
async function bootstrapLogin(): Promise<void> {
  await loadApplicationTheme()
  ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
    <React.StrictMode>
      <ApplicationThemeProvider>
        <LoginApp />
      </ApplicationThemeProvider>
    </React.StrictMode>
  )
}

void bootstrapLogin()
```

**与原工程逐项对照，共 4 处修改组。** 行号是原基线的位置，内网有差异时按相邻代码定位。每组都给出真实修改前后内容，其他部分保持原样。

修改组 1：原文件约第 1 行；目标文件约第 1 行。

修改前：

```tsx
import React, { useState } from 'react'
import ReactDOM from 'react-dom/client'
import {
  ArrowRightOutlined,
```

修改后：

```tsx
import React, { useEffect, useState } from 'react'
import ReactDOM from 'react-dom/client'
import {
  ArrowRightOutlined,
```

修改组 2：原文件约第 31 行；目标文件约第 31 行。

修改前：

```tsx
/** 渲染带有自定义关闭控件和设备流入口的无边框登录窗口。 */
export function LoginApp(): JSX.Element {
  const [loggingIn, setLoggingIn] = useState(false)
  const { theme } = useApplicationTheme()

  /** 请求关闭当前登录窗口，主进程会按既有逻辑隐藏窗口并保留应用后台运行。 */
```

修改后：

```tsx
/** 渲染带有自定义关闭控件和设备流入口的无边框登录窗口。 */
export function LoginApp(): JSX.Element {
  const [loggingIn, setLoggingIn] = useState(false)
  const [progress, setProgress] = useState('')
  useEffect(() => window.devAgentStudio?.auth.onProgress((value) => setProgress(value.message)), [])

  /** 显式取消 Python 中的授权轮询后再允许重新登录。 */
  const handleCancel = async (): Promise<void> => {
    try {
      await window.devAgentStudio?.auth.cancel()
      setProgress('登录已取消，可以重新发起登录。')
    } catch (error) {
      message.error(formatError(error, '取消登录失败'))
    }
  }
  const { theme } = useApplicationTheme()

  /** 请求关闭当前登录窗口，主进程会按既有逻辑隐藏窗口并保留应用后台运行。 */
```

修改组 3：原文件约第 47 行；目标文件约第 59 行。

修改前：

```tsx
    }

    setLoggingIn(true)
    try {
      const result = await authApi.login()
      if (!result.ok) {
        throw new Error('登录失败')
      }
    } catch (error) {
      message.error(formatError(error, '登录失败'))
      setLoggingIn(false)
    }
  }
```

修改后：

```tsx
    }

    setLoggingIn(true)
    setProgress('正在连接 Python 认证服务…')
    try {
      const result = await authApi.login()
      if (!result.ok) {
        throw new Error('登录失败')
      }
    } catch (error) {
      const errorMessage = formatError(error, '登录失败')
      setProgress(errorMessage)
      message.error(errorMessage)
      setLoggingIn(false)
    }
  }
```

修改组 4：原文件约第 132 行；目标文件约第 147 行。

修改前：

```tsx
              size="large"
              type="primary"
            >
              {loggingIn ? '登录中' : '启动安全登录'}
            </Button>
            <Text className={cx('login-action-hint')}>
              <SafetyCertificateOutlined /> 凭证仅保存在当前运行环境
            </Text>
          </div>
        </section>
```

修改后：

```tsx
              size="large"
              type="primary"
            >
              {loggingIn ? '等待云效授权' : '使用云效登录'}
            </Button>
            <Text className={cx('login-action-hint')} role="status" aria-live="polite">
              {progress || '将在系统浏览器中完成云效登录'}
            </Text>
            {loggingIn && <Button type="text" onClick={() => void handleCancel()}>取消登录</Button>}
            <Text className={cx('login-action-hint')}>
              <SafetyCertificateOutlined /> 凭证在本机加密保存，退出应用后删除
            </Text>
          </div>
        </section>
```

**完整统一差异**（便于逐行核对；+/- 不属于源码）：

```diff
diff --git a/Frontend/src/renderer/src/login.tsx b/Frontend/src/renderer/src/login.tsx
--- a/Frontend/src/renderer/src/login.tsx
+++ b/Frontend/src/renderer/src/login.tsx
@@ -1,4 +1,4 @@
-import React, { useState } from 'react'
+import React, { useEffect, useState } from 'react'
 import ReactDOM from 'react-dom/client'
 import {
   ArrowRightOutlined,
@@ -31,6 +31,18 @@
 /** 渲染带有自定义关闭控件和设备流入口的无边框登录窗口。 */
 export function LoginApp(): JSX.Element {
   const [loggingIn, setLoggingIn] = useState(false)
+  const [progress, setProgress] = useState('')
+  useEffect(() => window.devAgentStudio?.auth.onProgress((value) => setProgress(value.message)), [])
+
+  /** 显式取消 Python 中的授权轮询后再允许重新登录。 */
+  const handleCancel = async (): Promise<void> => {
+    try {
+      await window.devAgentStudio?.auth.cancel()
+      setProgress('登录已取消，可以重新发起登录。')
+    } catch (error) {
+      message.error(formatError(error, '取消登录失败'))
+    }
+  }
   const { theme } = useApplicationTheme()
 
   /** 请求关闭当前登录窗口，主进程会按既有逻辑隐藏窗口并保留应用后台运行。 */
@@ -47,13 +59,16 @@
     }
 
     setLoggingIn(true)
+    setProgress('正在连接 Python 认证服务…')
     try {
       const result = await authApi.login()
       if (!result.ok) {
         throw new Error('登录失败')
       }
     } catch (error) {
-      message.error(formatError(error, '登录失败'))
+      const errorMessage = formatError(error, '登录失败')
+      setProgress(errorMessage)
+      message.error(errorMessage)
       setLoggingIn(false)
     }
   }
@@ -132,10 +147,14 @@
               size="large"
               type="primary"
             >
-              {loggingIn ? '登录中' : '启动安全登录'}
+              {loggingIn ? '等待云效授权' : '使用云效登录'}
             </Button>
+            <Text className={cx('login-action-hint')} role="status" aria-live="polite">
+              {progress || '将在系统浏览器中完成云效登录'}
+            </Text>
+            {loggingIn && <Button type="text" onClick={() => void handleCancel()}>取消登录</Button>}
             <Text className={cx('login-action-hint')}>
-              <SafetyCertificateOutlined /> 凭证仅保存在当前运行环境
+              <SafetyCertificateOutlined /> 凭证在本机加密保存，退出应用后删除
             </Text>
           </div>
         </section>
```

### 5.16 Frontend/src/renderer/src/login.less

操作：**修改**；分类：**运行必需**。

原内容：原样式没有新增取消按钮的专用覆盖。

修改内容：在末尾追加登录区 text 按钮样式，复用 --login-text-secondary、--login-text、--login-soft，兼容明暗主题。

**与原工程逐项对照，共 1 处修改组。** 行号是原基线的位置，内网有差异时按相邻代码定位。每组都给出真实修改前后内容，其他部分保持原样。

修改组 1：原文件约第 411 行；目标文件约第 411 行。

修改前：

```less
    animation: none;
  }
}
```

修改后：

```less
    animation: none;
  }
}

.@{class-prefix}-login-action .ant-btn-text {
  height: 32px;
  color: var(--login-text-secondary);
  background: transparent;
  box-shadow: none;
  &:hover, &:focus {
    color: var(--login-text);
    background: var(--login-soft);
    box-shadow: none;
    transform: none;
  }
}
```

**完整统一差异**（便于逐行核对；+/- 不属于源码）：

```diff
diff --git a/Frontend/src/renderer/src/login.less b/Frontend/src/renderer/src/login.less
--- a/Frontend/src/renderer/src/login.less
+++ b/Frontend/src/renderer/src/login.less
@@ -411,3 +411,16 @@
     animation: none;
   }
 }
+
+.@{class-prefix}-login-action .ant-btn-text {
+  height: 32px;
+  color: var(--login-text-secondary);
+  background: transparent;
+  box-shadow: none;
+  &:hover, &:focus {
+    color: var(--login-text);
+    background: var(--login-soft);
+    box-shadow: none;
+    transform: none;
+  }
+}
```

### 5.17 Frontend/src/renderer/src/service/cloudApi.ts

操作：**修改**；分类：**运行必需**。

原内容：原云端请求在渲染器取得 token 并直接发起 fetch。

修改内容：替换为 requestCloudJson() -> preload IPC -> Electron -> Python；仅允许字符串请求体及受控头；保留 HTTP 错误发布和重新认证机制。

**完整目标文件内容**（无省略；新建文件直接粘贴，小型旧文件确认基线后替换）：

```ts
import { createHttpError, reportAuthenticationFailure } from './authentication'
import type { CloudJsonRequest } from '../../../shared/authentication'

/** 云端请求交给 Python 从凭据文件读取 token，渲染器不接触凭据。 */
export async function requestCloudJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const api = window.devAgentStudio?.auth
  if (!api?.requestCloudJson) {
    const error = createHttpError(401, { detail: '当前环境无法访问桌面认证服务。' })
    reportAuthenticationFailure(error)
    throw error
  }
  if (init.body !== undefined && init.body !== null && typeof init.body !== 'string') {
    throw new Error('云端 JSON 请求体必须是字符串。')
  }
  const headers: Record<string, string> = {}
  new Headers(init.headers).forEach((value, key) => {
    if (['content-type', 'accept'].includes(key)) headers[key] = value
  })
  if (init.body && !headers['content-type']) headers['content-type'] = 'application/json'
  const request: CloudJsonRequest = {
    url, method: (init.method || 'GET').toUpperCase() as CloudJsonRequest['method'],
    headers, body: typeof init.body === 'string' ? init.body : undefined
  }
  const result = await api.requestCloudJson(request)
  if (result.httpStatus >= 400 || result.httpStatus < 200 || result.httpStatus >= 300) {
    const error = createHttpError(result.httpStatus, result.body)
    reportAuthenticationFailure(error)
    throw error
  }
  return result.body as T
}
```

**与原工程逐项对照，共 1 处修改组。** 行号是原基线的位置，内网有差异时按相邻代码定位。每组都给出真实修改前后内容，其他部分保持原样。

修改组 1：原文件约第 1 行；目标文件约第 1 行。

修改前：

```ts
import { createHttpError, reportAuthenticationFailure } from './authentication'

/** 通过 Electron 主进程内存中的 access_token 调用云端 Java JSON 接口。 */
export async function requestCloudJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const requestUrl = normalizeCloudUrl(url)
  const authApi = window.devAgentStudio?.auth
  if (!authApi?.getAccessToken) {
    const error = createHttpError(401, { detail: '当前环境无法读取 access_token。' })
    reportAuthenticationFailure(error)
    throw error
  }

  const { accessToken } = await authApi.getAccessToken()
  if (!accessToken) {
    const error = createHttpError(401, { detail: '缺少 access_token。' })
    reportAuthenticationFailure(error)
    throw error
  }

  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${accessToken}`)
  headers.set('Accept', 'application/json')
  if (init.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(requestUrl, { ...init, headers })
  if (!response.ok) {
    const payload = await readErrorPayload(response)
    const error = createHttpError(response.status, payload)
    reportAuthenticationFailure(error)
    throw error
  }
  return readSuccessPayload<T>(response)
}

/** 校验云端请求必须使用完整的 HTTP 或 HTTPS 地址。 */
function normalizeCloudUrl(url: string): string {
  const parsedUrl = new URL(url)
  if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
    throw new Error('云端接口只支持 http 或 https URL。')
  }
  return parsedUrl.toString()
}

/** 宽容读取错误响应，确保 401/403 即使返回非 JSON 也能被识别。 */
async function readErrorPayload(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined
  const text = await response.text()
  if (!text) return undefined
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

/** 严格读取成功的 JSON 响应，无效 JSON 作为普通接口错误抛出。 */
async function readSuccessPayload<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
```

修改后：

```ts
import { createHttpError, reportAuthenticationFailure } from './authentication'
import type { CloudJsonRequest } from '../../../shared/authentication'

/** 云端请求交给 Python 从凭据文件读取 token，渲染器不接触凭据。 */
export async function requestCloudJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const api = window.devAgentStudio?.auth
  if (!api?.requestCloudJson) {
    const error = createHttpError(401, { detail: '当前环境无法访问桌面认证服务。' })
    reportAuthenticationFailure(error)
    throw error
  }
  if (init.body !== undefined && init.body !== null && typeof init.body !== 'string') {
    throw new Error('云端 JSON 请求体必须是字符串。')
  }
  const headers: Record<string, string> = {}
  new Headers(init.headers).forEach((value, key) => {
    if (['content-type', 'accept'].includes(key)) headers[key] = value
  })
  if (init.body && !headers['content-type']) headers['content-type'] = 'application/json'
  const request: CloudJsonRequest = {
    url, method: (init.method || 'GET').toUpperCase() as CloudJsonRequest['method'],
    headers, body: typeof init.body === 'string' ? init.body : undefined
  }
  const result = await api.requestCloudJson(request)
  if (result.httpStatus >= 400 || result.httpStatus < 200 || result.httpStatus >= 300) {
    const error = createHttpError(result.httpStatus, result.body)
    reportAuthenticationFailure(error)
    throw error
  }
  return result.body as T
}
```

**完整统一差异**（便于逐行核对；+/- 不属于源码）：

```diff
diff --git a/Frontend/src/renderer/src/service/cloudApi.ts b/Frontend/src/renderer/src/service/cloudApi.ts
--- a/Frontend/src/renderer/src/service/cloudApi.ts
+++ b/Frontend/src/renderer/src/service/cloudApi.ts
@@ -1,62 +1,31 @@
 import { createHttpError, reportAuthenticationFailure } from './authentication'
+import type { CloudJsonRequest } from '../../../shared/authentication'
 
-/** 通过 Electron 主进程内存中的 access_token 调用云端 Java JSON 接口。 */
+/** 云端请求交给 Python 从凭据文件读取 token，渲染器不接触凭据。 */
 export async function requestCloudJson<T>(url: string, init: RequestInit = {}): Promise<T> {
-  const requestUrl = normalizeCloudUrl(url)
-  const authApi = window.devAgentStudio?.auth
-  if (!authApi?.getAccessToken) {
-    const error = createHttpError(401, { detail: '当前环境无法读取 access_token。' })
+  const api = window.devAgentStudio?.auth
+  if (!api?.requestCloudJson) {
+    const error = createHttpError(401, { detail: '当前环境无法访问桌面认证服务。' })
     reportAuthenticationFailure(error)
     throw error
   }
-
-  const { accessToken } = await authApi.getAccessToken()
-  if (!accessToken) {
-    const error = createHttpError(401, { detail: '缺少 access_token。' })
+  if (init.body !== undefined && init.body !== null && typeof init.body !== 'string') {
+    throw new Error('云端 JSON 请求体必须是字符串。')
+  }
+  const headers: Record<string, string> = {}
+  new Headers(init.headers).forEach((value, key) => {
+    if (['content-type', 'accept'].includes(key)) headers[key] = value
+  })
+  if (init.body && !headers['content-type']) headers['content-type'] = 'application/json'
+  const request: CloudJsonRequest = {
+    url, method: (init.method || 'GET').toUpperCase() as CloudJsonRequest['method'],
+    headers, body: typeof init.body === 'string' ? init.body : undefined
+  }
+  const result = await api.requestCloudJson(request)
+  if (result.httpStatus >= 400 || result.httpStatus < 200 || result.httpStatus >= 300) {
+    const error = createHttpError(result.httpStatus, result.body)
     reportAuthenticationFailure(error)
     throw error
   }
-
-  const headers = new Headers(init.headers)
-  headers.set('Authorization', `Bearer ${accessToken}`)
-  headers.set('Accept', 'application/json')
-  if (init.body !== undefined && !headers.has('Content-Type')) {
-    headers.set('Content-Type', 'application/json')
-  }
-
-  const response = await fetch(requestUrl, { ...init, headers })
-  if (!response.ok) {
-    const payload = await readErrorPayload(response)
-    const error = createHttpError(response.status, payload)
-    reportAuthenticationFailure(error)
-    throw error
-  }
-  return readSuccessPayload<T>(response)
+  return result.body as T
 }
-
-/** 校验云端请求必须使用完整的 HTTP 或 HTTPS 地址。 */
-function normalizeCloudUrl(url: string): string {
-  const parsedUrl = new URL(url)
-  if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
-    throw new Error('云端接口只支持 http 或 https URL。')
-  }
-  return parsedUrl.toString()
-}
-
-/** 宽容读取错误响应，确保 401/403 即使返回非 JSON 也能被识别。 */
-async function readErrorPayload(response: Response): Promise<unknown> {
-  if (response.status === 204) return undefined
-  const text = await response.text()
-  if (!text) return undefined
-  try {
-    return JSON.parse(text)
-  } catch {
-    return text
-  }
-}
-
-/** 严格读取成功的 JSON 响应，无效 JSON 作为普通接口错误抛出。 */
-async function readSuccessPayload<T>(response: Response): Promise<T> {
-  if (response.status === 204) return undefined as T
-  return response.json() as Promise<T>
-}
```

### 5.18 Backend/tests/test_authentication.py

操作：**新增**；分类：**测试**。

原内容：原工程没有这组 Python 云效回归测试。

修改内容：新增 11 个测试方法：真实系统加密、文件权威性、HTTP/HTTPS、损坏过期、取消退出竞态、重复/旧会话、AG-UI、环境错误、网络超时、代理和启动清理。

**完整目标文件内容**（无省略；新建文件直接粘贴，小型旧文件确认基线后替换）：

```python
"""云效登录、文件凭据与取消栅栏的独立回归。"""
from __future__ import annotations
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from app.services.auth_credentials import CredentialStore, AuthCredentials
from app.services.auth_protection import protect
from app.services.yunxiao_auth import AuthService
from app.protocols import authentication
from app.protocols.authentication import build_authentication_stream

SESSION = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
TOKEN = "private-access-token-never-in-events"
REFRESH = "private-refresh-token-never-in-events"


class AuthenticationTests(unittest.IsolatedAsyncioTestCase):
    """用 HTTP 模拟服务及真实系统加密验证认证边界。"""
    def setUp(self):
        """每个测试隔离用户目录和环境，不访问真实云效。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.environment = patch.dict(os.environ, {"YUNXIAO_ENDPOINT": "http://127.0.0.1:9999", "YUNXIAO_POLL_INTERVAL_SECONDS": "0.1", "YUNXIAO_LOGIN_TIMEOUT_SECONDS": "2", "YUNXIAO_PENDING_CODES": "", "DEVAGENTSTUDIO_WORKING_DIR": ".devagentstudio_dev"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.store = CredentialStore(Path(self.temporary.name)/"yst-login")
        self.store.initialize(SESSION)
        self.calls = []
        self.pending_count = 0
        self.service = AuthService(self.store, httpx.MockTransport(self.handler))

    def handler(self, request):
        """按 demo 返回 state、token 和用户，并记录认证请求头。"""
        self.calls.append(request)
        if request.url.path.endswith("/auth/state"):
            assert request.headers["X-User-Agent"] == "Cli"
            return httpx.Response(200, json={"code": 0, "data": {"state": "state with & characters"}})
        if request.url.path.endswith("/auth/token"):
            assert request.url.params["state"] == "state with & characters"
            if self.pending_count:
                self.pending_count -= 1
                return httpx.Response(200, json={"code": 1001, "data": None})
            return httpx.Response(200, json={"code": 0, "data": {"token": TOKEN, "expiresAt": int(time.time())+3600, "refreshToken": REFRESH, "refreshExpiresAt": int(time.time())+7200, "tokenType": "Bearer"}})
        if request.url.path.endswith("/auth/userinfo"):
            assert request.headers["Authorization"] == f"Bearer {TOKEN}"
            return httpx.Response(200, json={"code": 0, "data": {"id": "u1", "nickname": "测试用户"}})
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(200, json={"ok": True})

    async def progress(self, *args, **kwargs):
        """保存公开阶段供测试检查。"""
        pass

    async def test_login_encrypts_file_and_reads_without_memory_cache(self):
        """验证真正落盘、密文及新存储实例可读取。"""
        self.pending_count = 1
        result = await self.service.login(SESSION, self.progress)
        self.assertTrue(result["authenticated"])
        self.assertNotIn(TOKEN, self.store.path.read_text())
        self.assertNotIn(REFRESH, self.store.path.read_text())
        reloaded = CredentialStore(self.store.root).read(SESSION)
        self.assertEqual(reloaded.access_token, TOKEN)
        self.assertGreater(reloaded.expires_at_ms, time.time()*1000)
        self.assertFalse(hasattr(self.service, "access_token"))
        self.store.path.unlink()
        self.assertFalse(self.service.status(SESSION)["authenticated"])

    async def test_configured_http_and_https_services_follow_demo(self):
        """非本机 HTTP 和 HTTPS 均可完成认证，业务代理也沿用配置协议。"""
        for endpoint in ("http://yunxiao.internal:8080", "https://yunxiao.example"):
            with self.subTest(endpoint=endpoint):
                with patch.dict(os.environ, {"YUNXIAO_ENDPOINT": endpoint, "YUNXIAO_API_BASE_URL": ""}):
                    self.calls.clear()
                    result = await self.service.login(SESSION, self.progress)
                    self.assertTrue(result["authenticated"])
                    self.assertEqual([str(call.url).split("/auth/")[0] for call in self.calls], [endpoint] * 3)
                    response = await self.service.cloud_request(SESSION, {"url": endpoint + "/api", "method": "GET"})
                    self.assertEqual(response["httpStatus"], 200)
                    self.assertEqual(self.calls[-1].headers["Authorization"], f"Bearer {TOKEN}")
                    with patch.dict(os.environ, {"YUNXIAO_API_BASE_URL": "http://business.internal:8081"}):
                        response = await self.service.cloud_request(SESSION, {"url": "http://business.internal:8081/api", "method": "GET"})
                        self.assertEqual(response["body"], {"ok": True})
                        self.assertEqual(str(self.calls[-1].url), "http://business.internal:8081/api")

    async def test_corrupt_or_expired_file_is_removed(self):
        """损坏或即将到期的文件不能继续表示有效登录。"""
        await self.service.login(SESSION, self.progress)
        self.store.path.write_text('{"ciphertext":"invalid"}')
        self.assertFalse(self.service.status(SESSION)["authenticated"])
        self.assertFalse(self.store.path.exists())
        credentials = AuthCredentials(access_token=TOKEN, expires_at_ms=int(time.time()*1000)-1, user_id="u", user_name="n")
        self.store.save(SESSION, credentials)
        self.assertIsNone(self.store.read(SESSION))
        self.assertFalse(self.store.path.exists())

    async def test_cancel_and_shutdown_prevent_late_write(self):
        """取消和退出后到达的 token 不能重新创建凭据。"""
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            """暂停 token 请求，模拟用户在网络请求中退出。"""
            if request.url.path.endswith("/auth/token"):
                started.set()
                await release.wait()
            return self.handler(request)
        self.service.transport = httpx.MockTransport(delayed)
        task = asyncio.create_task(self.service.login(SESSION, self.progress))
        await started.wait()
        await self.service.reset(SESSION, shutdown=True)
        release.set()
        with self.assertRaisesRegex(RuntimeError, "取消|结束"):
            await task
        self.assertFalse(self.store.path.exists())
        with self.assertRaises(RuntimeError):
            self.store.require_session(SESSION)

    async def test_duplicate_login_and_stale_session_are_rejected(self):
        """同一会话只允许一个轮询，旧会话不能读取新凭据。"""
        started, release = asyncio.Event(), asyncio.Event()
        async def report(*args, **kwargs):
            """等待测试发出继续信号。"""
            started.set()
            await release.wait()
        task = asyncio.create_task(self.service.login(SESSION, report))
        await started.wait()
        with self.assertRaisesRegex(RuntimeError, "正在进行"):
            await self.service.login(SESSION, self.progress)
        await self.service.reset(OTHER, initialize=True)
        release.set()
        with self.assertRaises(RuntimeError):
            await task
        with self.assertRaises(RuntimeError):
            self.service.status(SESSION)
        self.assertFalse(self.store.path.exists())

    async def test_complete_ag_ui_lifecycle_does_not_expose_credentials(self):
        """失败和成功都有终帧，事件不包含 token。"""
        with patch.object(authentication, "_service", self.service):
            chunks = [chunk async for chunk in build_authentication_stream({"threadId":"t", "runId":"r", "forwardedProps":{"authentication":{"action":"login", "sessionId":SESSION, "workingDir":".devagentstudio_dev"}}})]
        raw = "".join(chunks)
        events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertTrue(events[-1]["result"]["authentication"]["authenticated"])
        self.assertIn("STATE_SNAPSHOT", [event["type"] for event in events])
        self.assertNotIn(TOKEN, raw)
        self.assertNotIn(REFRESH, raw)

    async def test_environment_mismatch_has_handled_failure(self):
        """开发模式两端目录不一致时不能写入错误环境。"""
        with patch.object(authentication, "_service", self.service):
            raw = "".join([chunk async for chunk in build_authentication_stream({"forwardedProps":{"authentication":{"action":"status", "sessionId":SESSION, "workingDir":".devagentstudio_st"}}})])
        self.assertIn('"status":"failed"', raw)
        self.assertIn("RUN_FINISHED", raw)
        self.assertFalse(self.store.path.exists())

    async def test_missing_endpoint_and_network_failure_are_safe(self):
        """配置和网络错误不会回显 token 或上游 URL 参数。"""
        with patch.dict(os.environ, {"YUNXIAO_ENDPOINT":""}):
            with self.assertRaisesRegex(RuntimeError, "YUNXIAO_ENDPOINT"):
                await self.service.login(SESSION, self.progress)
        def failed(request):
            """模拟服务网络中断。"""
            raise httpx.ConnectError("sensitive URL", request=request)
        self.service.transport = httpx.MockTransport(failed)
        with self.assertRaisesRegex(RuntimeError, "连接失败"):
            await self.service.login(SESSION, self.progress)
        self.assertFalse(self.store.path.exists())

    async def test_timeout_and_fatal_http_response_do_not_save(self):
        """超时和服务端 HTTP 拒绝都终止登录。"""
        def pending(request):
            """持续返回等待授权状态。"""
            if request.url.path.endswith("/auth/token"):
                return httpx.Response(202)
            return self.handler(request)
        self.service.transport = httpx.MockTransport(pending)
        with patch.dict(os.environ, {"YUNXIAO_LOGIN_TIMEOUT_SECONDS":"1"}):
            with self.assertRaisesRegex(RuntimeError, "超时"):
                await self.service.login(SESSION, self.progress)
        self.service.transport = httpx.MockTransport(lambda request: httpx.Response(401))
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            await self.service.login(SESSION, self.progress)
        self.assertFalse(self.store.path.exists())

    async def test_trusted_cloud_proxy_uses_file_and_blocks_foreign_origin(self):
        """业务 token 只由 Python 附加，不能发到任意域名。"""
        await self.service.login(SESSION, self.progress)
        result = await self.service.cloud_request(SESSION, {"url":"http://127.0.0.1:9999/api", "method":"GET", "headers":{"Authorization":"Bearer wrong"}})
        self.assertEqual(result["body"], {"ok":True})
        with self.assertRaisesRegex(RuntimeError, "可信服务"):
            await self.service.cloud_request(SESSION, {"url":"https://other.example/api", "method":"GET"})
        with self.assertRaisesRegex(RuntimeError, "认证接口"):
            await self.service.cloud_request(SESSION, {"url":"http://127.0.0.1:9999/auth/token", "method":"GET"})

    async def test_startup_cleans_previous_session_and_temporary_files(self):
        """新桌面启动删除上次退出残留，并隔离旧会话。"""
        await self.service.login(SESSION, self.progress)
        (self.store.root/".auth-crash.tmp").write_text("partial")
        await self.service.reset(OTHER, initialize=True)
        self.assertFalse(self.store.path.exists())
        self.assertFalse(list(self.store.root.glob(".auth-*.tmp")))
        self.assertFalse(self.service.status(OTHER)["authenticated"])


if __name__ == "__main__":
    unittest.main()
```

### 5.19 Frontend/tests/authentication.test.ts

操作：**修改**；分类：**测试**。

原内容：原测试验证渲染器取 token/fetch 的旧云端请求行为。

修改内容：保留原认证错误基础用例；更新云端请求用例为 IPC，增加主进程 AG-UI 进度/结果、本机限制、不完整结果及浏览器回调错误用例。

**与原工程逐项对照，共 2 处修改组。** 行号是原基线的位置，内网有差异时按相邻代码定位。每组都给出真实修改前后内容，其他部分保持原样。

修改组 1：原文件约第 1 行；目标文件约第 1 行。

修改前：

```ts
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { requestCloudJson } from '../src/renderer/src/service/cloudApi'
```

修改后：

```ts
import { runAuthentication } from '../src/main/authClient'
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { requestCloudJson } from '../src/renderer/src/service/cloudApi'
```

修改组 2：原文件约第 208 行；目标文件约第 209 行。

修改前：

```ts
  assert.equal(authorization, null)
})

test('Java JSON 请求从 Electron 内存读取 token 并强制使用 Bearer 头', async () => {
  const originalWindow = globalThis.window
  const originalFetch = globalThis.fetch
  let authorization: string | null = null
  Object.assign(globalThis, {
    window: {
      devAgentStudio: {
        auth: {
          getAccessToken: async () => ({ accessToken: 'memory-token' })
        }
      }
    },
    fetch: async (_url: string, init?: RequestInit) => {
      authorization = new Headers(init?.headers).get('Authorization')
      return jsonResponse(200, { ok: true })
    }
  })

  try {
    const result = await requestCloudJson<{ ok: boolean }>('https://cloud.example/api', {
      headers: { Authorization: 'Bearer caller-token' }
    })
    assert.deepEqual(result, { ok: true })
    assert.equal(authorization, 'Bearer memory-token')
  } finally {
    Object.assign(globalThis, { window: originalWindow, fetch: originalFetch })
  }
})

test('Java 401 与缺少内存 token 都发布认证失败', async () => {
  const originalWindow = globalThis.window
  const originalFetch = globalThis.fetch
  const statuses: number[] = []
  const unsubscribe = subscribeAuthenticationFailure((failure) => {
    statuses.push(failure.status)
  })

  try {
    Object.assign(globalThis, {
      window: {
        devAgentStudio: {
          auth: {
            getAccessToken: async () => ({ accessToken: 'memory-token' })
          }
        }
      },
      fetch: async () => jsonResponse(401)
    })
    await assert.rejects(requestCloudJson('https://cloud.example/api'))
    assert.deepEqual(statuses, [401])

    resetAuthenticationFailure()
    Object.assign(globalThis, {
      window: {
        devAgentStudio: {
          auth: {
            getAccessToken: async () => ({ accessToken: null })
          }
        }
      }
    })
    await assert.rejects(requestCloudJson('https://cloud.example/api'))
    assert.deepEqual(statuses, [401, 401])
  } finally {
    unsubscribe()
    Object.assign(globalThis, { window: originalWindow, fetch: originalFetch })
  }
})
```

修改后：

```ts
  assert.equal(authorization, null)
})

test('云端请求通过 IPC 交给 Python，渲染器不读取 token 或发送 fetch', async () => {
  const originalWindow = globalThis.window
  const originalFetch = globalThis.fetch
  let sent: unknown
  Object.assign(globalThis, {
    window: { devAgentStudio: { auth: { requestCloudJson: async (input: unknown) => {
      sent = input
      return { httpStatus: 200, body: { ok: true } }
    } } } },
    fetch: async () => { throw new Error('渲染器不得直接调用云端') }
  })
  try {
    const result = await requestCloudJson('https://cloud.example/api', {
      method: 'POST', body: '{}', headers: { Authorization: 'Bearer should-not-forward' }
    })
    assert.deepEqual(result, { ok: true })
    assert.deepEqual(sent, { url: 'https://cloud.example/api', method: 'POST', body: '{}', headers: { 'content-type': 'application/json' } })
  } finally { Object.assign(globalThis, { window: originalWindow, fetch: originalFetch }) }
})

for (const status of [401, 403] as const) {
  test(`Python 云端代理返回 ${status} 仍触发全局认证提示`, async () => {
    const originalWindow = globalThis.window
    const statuses: number[] = []
    const unsubscribe = subscribeAuthenticationFailure((failure) => statuses.push(failure.status))
    Object.assign(globalThis, { window: { devAgentStudio: { auth: {
      requestCloudJson: async () => ({ httpStatus: status, body: { detail: '认证拒绝' } })
    } } } })
    try {
      await assert.rejects(requestCloudJson('https://cloud.example/api'))
      assert.deepEqual(statuses, [status])
    } finally { unsubscribe(); Object.assign(globalThis, { window: originalWindow }) }
  })
}

test('主进程消费 Python AG-UI 进度和最终文件登录状态', async () => {
  const originalFetch = globalThis.fetch
  const progress: string[] = []
  let sent: unknown
  Object.assign(globalThis, { fetch: async (_url: unknown, init: RequestInit) => {
    sent = JSON.parse(String(init.body)).forwardedProps.authentication
    return eventStreamResponse([
      { type: 'RUN_STARTED', threadId: 'auth-thread', runId: 'auth-run' },
      { type: 'CUSTOM', name: 'authentication', value: { status: 'in_progress', progress: { stage: 'awaiting_authorization', message: '等待授权', percent: 25 } } },
      { type: 'RUN_FINISHED', threadId: 'auth-thread', runId: 'auth-run', result: { authentication: { status: 'completed', authenticated: true, user: { id: 'u1', name: '用户' } } } }
    ])
  } })
  try {
    const result = await runAuthentication('http://127.0.0.1:8000', { action: 'status' }, (value) => { progress.push(value.progress!.stage) })
    assert.equal(result.authenticated, true)
    assert.deepEqual(progress, ['awaiting_authorization'])
    assert.deepEqual(sent, { action: 'status' })
  } finally { Object.assign(globalThis, { fetch: originalFetch }) }
})

test('主进程拒绝非本机后端及不完整的认证结果', async () => {
  await assert.rejects(runAuthentication('https://remote.example', { action: 'status' }), /本机/)
  const originalFetch = globalThis.fetch
  Object.assign(globalThis, { fetch: async () => eventStreamResponse([
    { type: 'RUN_STARTED', threadId: 't', runId: 'r' },
    { type: 'RUN_FINISHED', threadId: 't', runId: 'r', result: {} }
  ]) })
  try { await assert.rejects(runAuthentication('http://127.0.0.1:8000', { action: 'status' }), /完整结果/) }
  finally { Object.assign(globalThis, { fetch: originalFetch }) }
})

test('浏览器打开失败不会被 SDK subscriber 吞掉', async () => {
  const originalFetch = globalThis.fetch
  Object.assign(globalThis, { fetch: async () => eventStreamResponse([
    { type: 'RUN_STARTED', threadId: 't', runId: 'r' },
    { type: 'CUSTOM', name: 'authentication', value: { status: 'in_progress', progress: { stage: 'awaiting_authorization', message: '等待授权', percent: 25 } } },
    { type: 'RUN_FINISHED', threadId: 't', runId: 'r', result: { authentication: { status: 'completed' } } }
  ]) })
  try {
    await assert.rejects(runAuthentication('http://127.0.0.1:8000', { action: 'login' }, () => { throw new Error('浏览器打开失败') }), /浏览器打开失败/)
  } finally { Object.assign(globalThis, { fetch: originalFetch }) }
})
```

**完整统一差异**（便于逐行核对；+/- 不属于源码）：

```diff
diff --git a/Frontend/tests/authentication.test.ts b/Frontend/tests/authentication.test.ts
--- a/Frontend/tests/authentication.test.ts
+++ b/Frontend/tests/authentication.test.ts
@@ -1,3 +1,4 @@
+import { runAuthentication } from '../src/main/authClient'
 import assert from 'node:assert/strict'
 import { afterEach, test } from 'node:test'
 import { requestCloudJson } from '../src/renderer/src/service/cloudApi'
@@ -208,71 +209,80 @@
   assert.equal(authorization, null)
 })
 
-test('Java JSON 请求从 Electron 内存读取 token 并强制使用 Bearer 头', async () => {
+test('云端请求通过 IPC 交给 Python，渲染器不读取 token 或发送 fetch', async () => {
   const originalWindow = globalThis.window
   const originalFetch = globalThis.fetch
-  let authorization: string | null = null
+  let sent: unknown
   Object.assign(globalThis, {
-    window: {
-      devAgentStudio: {
-        auth: {
-          getAccessToken: async () => ({ accessToken: 'memory-token' })
-        }
-      }
-    },
-    fetch: async (_url: string, init?: RequestInit) => {
-      authorization = new Headers(init?.headers).get('Authorization')
-      return jsonResponse(200, { ok: true })
-    }
-  })
-
-  try {
-    const result = await requestCloudJson<{ ok: boolean }>('https://cloud.example/api', {
-      headers: { Authorization: 'Bearer caller-token' }
+    window: { devAgentStudio: { auth: { requestCloudJson: async (input: unknown) => {
+      sent = input
+      return { httpStatus: 200, body: { ok: true } }
+    } } } },
+    fetch: async () => { throw new Error('渲染器不得直接调用云端') }
+  })
+  try {
+    const result = await requestCloudJson('https://cloud.example/api', {
+      method: 'POST', body: '{}', headers: { Authorization: 'Bearer should-not-forward' }
     })
     assert.deepEqual(result, { ok: true })
-    assert.equal(authorization, 'Bearer memory-token')
-  } finally {
-    Object.assign(globalThis, { window: originalWindow, fetch: originalFetch })
-  }
-})
-
-test('Java 401 与缺少内存 token 都发布认证失败', async () => {
-  const originalWindow = globalThis.window
-  const originalFetch = globalThis.fetch
-  const statuses: number[] = []
-  const unsubscribe = subscribeAuthenticationFailure((failure) => {
-    statuses.push(failure.status)
-  })
-
-  try {
-    Object.assign(globalThis, {
-      window: {
-        devAgentStudio: {
-          auth: {
-            getAccessToken: async () => ({ accessToken: 'memory-token' })
-          }
-        }
-      },
-      fetch: async () => jsonResponse(401)
-    })
-    await assert.rejects(requestCloudJson('https://cloud.example/api'))
-    assert.deepEqual(statuses, [401])
-
-    resetAuthenticationFailure()
-    Object.assign(globalThis, {
-      window: {
-        devAgentStudio: {
-          auth: {
-            getAccessToken: async () => ({ accessToken: null })
-          }
-        }
-      }
-    })
-    await assert.rejects(requestCloudJson('https://cloud.example/api'))
-    assert.deepEqual(statuses, [401, 401])
-  } finally {
-    unsubscribe()
-    Object.assign(globalThis, { window: originalWindow, fetch: originalFetch })
-  }
-})
+    assert.deepEqual(sent, { url: 'https://cloud.example/api', method: 'POST', body: '{}', headers: { 'content-type': 'application/json' } })
+  } finally { Object.assign(globalThis, { window: originalWindow, fetch: originalFetch }) }
+})
+
+for (const status of [401, 403] as const) {
+  test(`Python 云端代理返回 ${status} 仍触发全局认证提示`, async () => {
+    const originalWindow = globalThis.window
+    const statuses: number[] = []
+    const unsubscribe = subscribeAuthenticationFailure((failure) => statuses.push(failure.status))
+    Object.assign(globalThis, { window: { devAgentStudio: { auth: {
+      requestCloudJson: async () => ({ httpStatus: status, body: { detail: '认证拒绝' } })
+    } } } })
+    try {
+      await assert.rejects(requestCloudJson('https://cloud.example/api'))
+      assert.deepEqual(statuses, [status])
+    } finally { unsubscribe(); Object.assign(globalThis, { window: originalWindow }) }
+  })
+}
+
+test('主进程消费 Python AG-UI 进度和最终文件登录状态', async () => {
+  const originalFetch = globalThis.fetch
+  const progress: string[] = []
+  let sent: unknown
+  Object.assign(globalThis, { fetch: async (_url: unknown, init: RequestInit) => {
+    sent = JSON.parse(String(init.body)).forwardedProps.authentication
+    return eventStreamResponse([
+      { type: 'RUN_STARTED', threadId: 'auth-thread', runId: 'auth-run' },
+      { type: 'CUSTOM', name: 'authentication', value: { status: 'in_progress', progress: { stage: 'awaiting_authorization', message: '等待授权', percent: 25 } } },
+      { type: 'RUN_FINISHED', threadId: 'auth-thread', runId: 'auth-run', result: { authentication: { status: 'completed', authenticated: true, user: { id: 'u1', name: '用户' } } } }
+    ])
+  } })
+  try {
+    const result = await runAuthentication('http://127.0.0.1:8000', { action: 'status' }, (value) => { progress.push(value.progress!.stage) })
+    assert.equal(result.authenticated, true)
+    assert.deepEqual(progress, ['awaiting_authorization'])
+    assert.deepEqual(sent, { action: 'status' })
+  } finally { Object.assign(globalThis, { fetch: originalFetch }) }
+})
+
+test('主进程拒绝非本机后端及不完整的认证结果', async () => {
+  await assert.rejects(runAuthentication('https://remote.example', { action: 'status' }), /本机/)
+  const originalFetch = globalThis.fetch
+  Object.assign(globalThis, { fetch: async () => eventStreamResponse([
+    { type: 'RUN_STARTED', threadId: 't', runId: 'r' },
+    { type: 'RUN_FINISHED', threadId: 't', runId: 'r', result: {} }
+  ]) })
+  try { await assert.rejects(runAuthentication('http://127.0.0.1:8000', { action: 'status' }), /完整结果/) }
+  finally { Object.assign(globalThis, { fetch: originalFetch }) }
+})
+
+test('浏览器打开失败不会被 SDK subscriber 吞掉', async () => {
+  const originalFetch = globalThis.fetch
+  Object.assign(globalThis, { fetch: async () => eventStreamResponse([
+    { type: 'RUN_STARTED', threadId: 't', runId: 'r' },
+    { type: 'CUSTOM', name: 'authentication', value: { status: 'in_progress', progress: { stage: 'awaiting_authorization', message: '等待授权', percent: 25 } } },
+    { type: 'RUN_FINISHED', threadId: 't', runId: 'r', result: { authentication: { status: 'completed' } } }
+  ]) })
+  try {
+    await assert.rejects(runAuthentication('http://127.0.0.1:8000', { action: 'login' }, () => { throw new Error('浏览器打开失败') }), /浏览器打开失败/)
+  } finally { Object.assign(globalThis, { fetch: originalFetch }) }
+})
```

### 5.20 scripts/start-frontend.ps1

操作：**新增**；分类：**辅助，可选**。

原内容：原工程通过 Frontend/pnpm dev 启动，没有此根目录包装脚本。

修改内容：新增无参数包装：读取 packageManager，npm exec 调用固定 pnpm 版本运行原 pnpm dev，并恢复 npm cache 环境；不增加云效专属启动策略。中文 PowerShell 文件保存为 UTF-8 BOM。

**完整目标文件内容**（无省略；新建文件直接粘贴，小型旧文件确认基线后替换）：

```powershell
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FrontendRoot = Join-Path $RepoRoot "Frontend"
$Package = Get-Content -LiteralPath (Join-Path $FrontendRoot "package.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$PreviousNpmCache = $env:npm_config_cache
$env:npm_config_cache = Join-Path $RepoRoot ".npm-cache"
Push-Location $FrontendRoot
try {
  # 执行与原工程相同的 pnpm dev，使用声明版本避开本机损坏的全局 pnpm 入口。
  & npm.cmd exec --yes --package $Package.packageManager -- pnpm dev
  $FrontendExitCode = $LASTEXITCODE
}
finally {
  Pop-Location
  $env:npm_config_cache = $PreviousNpmCache
}
exit $FrontendExitCode
```

### 5.21 scripts/start-yst-backend.ps1

操作：**新增**；分类：**辅助，可选**。

原内容：原工程已有 start-backend.ps1。

修改内容：新增无参数转调脚本，只调用原 start-backend.ps1。通常可不迁移，直接用原后端入口。中文 PowerShell 文件保存为 UTF-8 BOM。

**完整目标文件内容**（无省略；新建文件直接粘贴，小型旧文件确认基线后替换）：

```powershell
[CmdletBinding()]
param()
# 统一使用原工程的后端启动脚本，不增加云效专属启动流程。
& (Join-Path $PSScriptRoot "start-backend.ps1")
exit $LASTEXITCODE
```

### 5.22 Frontend/scripts/verify-yst-electron.mjs

操作：**新增**；分类：**历史测试辅助，可选**。

原内容：原工程没有本次接入时的 Electron CDP 验证脚本。

修改内容：保留原验证代码供查阅。非 --startup-only 路径依赖旧测试服务：18060 后端、18061/test/approve 及固定测试用户；它不是可直接运行的内网真实平台验收脚本，不要照搬其端口/授权请求到产品。

**完整目标文件内容**（无省略；新建文件直接粘贴，小型旧文件确认基线后替换）：

```javascript
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { spawn } from 'node:child_process'
import WebSocket from 'ws'
const frontend = path.resolve(import.meta.dirname, '..')
const evidence = path.resolve(frontend, '../.validation')
const credentials = path.join(os.homedir(), '.devagentstudio_dev', 'yst-login', 'auth.json')
const checks = []
let processForTest
/** 等待条件，保留失败边界。 */
async function until(operation, description, timeout = 15000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    try { const result = await operation(); if (result) return result } catch {}
    await new Promise(resolve => setTimeout(resolve, 150))
  }
  throw new Error(`等待超时：${description}`)
}
/** 连接本次 Electron 的调试端点。 */
async function connect(url) {
  const socket = new WebSocket(url)
  await new Promise((resolve, reject) => { socket.once('open', resolve); socket.once('error', reject) })
  let nextId = 0
  const pending = new Map()
  socket.on('message', raw => {
    const message = JSON.parse(raw.toString())
    const callback = pending.get(message.id)
    if (!callback) return
    pending.delete(message.id)
    if (message.error) callback.reject(new Error(message.error.message)); else callback.resolve(message.result)
  })
  return { socket,
    call(method, params = {}) {
      const id = ++nextId
      return new Promise((resolve, reject) => { pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })) })
    },
    async evaluate(expression) {
      const result = await this.call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
      if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
      return result.result?.value
    }
  }
}
/** 查找实际 Electron 页面。 */
async function page(suffix) {
  return until(async () => {
    const pages = await (await fetch('http://127.0.0.1:18311/json')).json()
    return pages.find(item => item.url.includes(`/out/renderer/${suffix}.html`))
  }, `Electron ${suffix} 页面`)
}
/** 启动目标副本，使用本机模拟认证后端。 */
async function start() {
  const env = { ...process.env, DEVAGENTSTUDIO_BACKEND_URL: process.env.DEVAGENTSTUDIO_BACKEND_URL || 'http://127.0.0.1:18060' }
  delete env.ELECTRON_RUN_AS_NODE
  processForTest = spawn(path.join(frontend, 'node_modules/electron/dist/electron.exe'), [frontend, '--inspect=18310', '--remote-debugging-port=18311'], { cwd: frontend, windowsHide: true, env, stdio: ['ignore', 'pipe', 'pipe'] })
  const output = await fs.open(path.join(evidence, 'electron.log'), 'a')
  processForTest.stdout.on('data', chunk => { void output.write(chunk) })
  processForTest.stderr.on('data', chunk => { void output.write(chunk) })
  processForTest.once('exit', () => { void output.close() })
  await page('login')
}
/** 使用真实 app.quit 验证退出清理。 */
async function quit() {
  const targets = await (await fetch('http://127.0.0.1:18310/json')).json()
  const main = await connect(targets[0].webSocketDebuggerUrl)
  await main.evaluate(`process.getBuiltinModule('module').createRequire(process.cwd() + '/package.json')('electron').app.quit()`)
  main.socket.close()
  await until(() => processForTest.exitCode !== null, 'Electron 正常退出', 25000)
}
/** 检查凭据是否存在，不输出任何凭据值。 */
async function exists() { return fs.stat(credentials).then(() => true, () => false) }
try {
  await start()
  let login = await connect((await page('login')).webSocketDebuggerUrl)
  await until(() => login.evaluate(`document.body.innerText.includes('使用云效登录')`), '登录界面渲染')
  assert.equal(await login.evaluate(`typeof window.devAgentStudio.auth.getAccessToken`), 'undefined')
  checks.push('渲染器没有 token 读取接口')
  const fence = JSON.parse(await fs.readFile(path.join(path.dirname(credentials), 'session.json'), 'utf8'))
  assert.equal(fence.active, false)
  checks.push('点击登录前未初始化 Python 认证会话')
  if (process.argv.includes('--startup-only')) {
    const status = await login.evaluate('window.devAgentStudio.auth.status()')
    assert.equal(status.authenticated, false)
    checks.push('Python 未启动时仍正常显示登录窗口')
    await quit()
    login.socket.close()
    assert.equal(await exists(), false)
    await fs.writeFile(path.join(evidence, 'startup-result.json'), JSON.stringify({ passed: true, checks }, null, 2))
    console.log(JSON.stringify({ passed: true, checks }, null, 2))
  } else {
  for (const theme of ['light', 'dark']) {
    await login.evaluate(`window.devAgentStudio.settings.saveTheme({theme:'${theme}'})`)
    await until(() => login.evaluate(`document.querySelector('[data-theme]')?.getAttribute('data-theme') === '${theme}'`), `${theme} 主题`)
    const screenshot = await login.call('Page.captureScreenshot', { format: 'png' })
    await fs.writeFile(path.join(evidence, `login-${theme}.png`), Buffer.from(screenshot.data, 'base64'))
    checks.push(`实际 Electron ${theme} 登录主题截图`)
  }
  await login.evaluate(`[...document.querySelectorAll('button')].find(button=>button.textContent.includes('使用云效登录')).click()`)
  await until(() => login.evaluate(`document.body.innerText.includes('请在浏览器中完成云效授权')`), '等待授权进度')
  await login.evaluate(`[...document.querySelectorAll('button')].find(button=>button.textContent.includes('取消登录')).click()`)
  await until(() => login.evaluate(`document.body.innerText.includes('使用云效登录')`), '取消后恢复按钮')
  assert.equal(await exists(), false)
  checks.push('取消授权后没有凭据文件，并可重试')
  await login.evaluate(`[...document.querySelectorAll('button')].find(button=>button.textContent.includes('使用云效登录')).click()`)
  await until(() => login.evaluate(`document.body.innerText.includes('请在浏览器中完成云效授权')`), '再次等待授权')
  await fetch('http://127.0.0.1:18061/test/approve')
  const main = await connect((await page('index')).webSocketDebuggerUrl)
  login.socket.close()
  const encrypted = await fs.readFile(credentials, 'utf8')
  assert.ok(!encrypted.includes('synthetic-electron-test-token'))
  assert.ok(!encrypted.includes('synthetic-refresh-token'))
  const status = await main.evaluate('window.devAgentStudio.auth.status()')
  assert.equal(status.authenticated, true)
  assert.equal(status.user.id, 'electron-test-user')
  checks.push('授权后加密落盘，主窗口读取 Python 登录状态')
  await main.evaluate('window.close()')
  assert.equal(await exists(), true)
  checks.push('关闭窗口进入托盘时保留凭据')
  await quit()
  main.socket.close()
  assert.equal(await exists(), false)
  checks.push('真正 app.quit 后删除凭据文件')
  await fs.writeFile(credentials, encrypted)
  await start()
  login = await connect((await page('login')).webSocketDebuggerUrl)
  assert.equal(await exists(), false)
  checks.push('冷启动删除残留凭据并显示登录窗口')
  await quit()
  login.socket.close()
  await fs.writeFile(path.join(evidence, 'electron-result.json'), JSON.stringify({ passed: true, checks }, null, 2))
  console.log(JSON.stringify({ passed: true, checks }, null, 2))
  }
} finally {
  if (processForTest && processForTest.exitCode === null) processForTest.kill()
}
```

## 6. 启动与验收

在内网目标项目根目录执行（将路径替换为本机项目位置；不依赖 F: 盘）：

```powershell
py -3.12 -m venv .\Backend\.venv
.\Backend\.venv\Scripts\python.exe -m pip install -r .\Backend\requirements.txt
# 在 Backend/.env 配置真实云效地址。
.\scripts\start-backend.ps1
```

另开 PowerShell：

```powershell
cd <项目根目录>\Frontend
pnpm install --frozen-lockfile
pnpm dev
```

可选的新 start-frontend.ps1 仅包装原 pnpm dev；start-yst-backend.ps1 仅转调原入口，不是必需。原脚本优先使用当前激活的虚拟环境，避免激活别的项目。Python/Node/pnpm 的依赖必须来自内网包源或事先准备的离线包，不要求内网访问公网。

迁移测试后执行：

```powershell
# 项目根目录
Push-Location Backend
.\.venv\Scripts\python.exe -m unittest tests.test_authentication -v
Pop-Location
Push-Location Frontend
pnpm test:auth
pnpm build
Pop-Location
Invoke-RestMethod http://127.0.0.1:8000/health
```

本说明导出时未重新运行上述应用测试；它们是内网合并后的验收命令。Python 测试使用 MockTransport 和临时凭据目录，但 Windows 会实际调用 DPAPI。非 Windows 需要受支持的系统钥匙串。

必须在实际 Electron 中手工验证：

1. Python 未启动也能显示登录窗口，未点击前不请求云效；点击后连接失败可明确提示。
2. 正常授权：获取 state、打开浏览器、轮询 token、userinfo、加密文件、主窗口。
3. 拒绝/取消/超时/断网/字段错误后可重试，旧任务不能重新写文件。
4. 真实磁盘 auth.json 中没有明文 token；渲染器无 getAccessToken，公开事件不含 token。
5. 云端请求由 Python 添加 Bearer；外部 origin 和认证接口代理被拒绝；401 清理旧凭据。
6. token 过期/损坏需要重新登录；不存在后台自动刷新。
7. 关闭窗口托盘保留 token；真正退出删除；强制结束后下次启动清理；无迟到写回。
8. 明暗主题进度/取消按钮可读。

**历史 Electron 脚本特别说明**：Frontend/scripts/verify-yst-electron.mjs 默认依赖当时的 18060 Python 桥接、18061/test/approve 和固定测试用户，这些旧测试服务不在本交付中。完整模式不可直接用于内网真实服务。建议按上面的手工清单验收。若仅使用 --startup-only，也须先 pnpm build、手工建项目根 .validation 目录、确认没有其他同身份 Electron 运行；脚本会清理当前 dev 凭据并使用调试端口 18310/18311，仅在专门测试环境运行。本交付没有新增模拟服务。

## 7. 文档同步的建议内容

原 README.md 增加：`云效登录配置与实现见 [docs/YUNXIAO_LOGIN.md](docs/YUNXIAO_LOGIN.md)。`

原 docs/CODEBASE_INDEX.md 末尾追加：

> 云效桌面认证：services/yunxiao_auth.py 负责 state/token/userinfo 和可信云端业务请求；auth_credentials.py 负责文件权威凭据与桌面会话；auth_protection.py 负责系统加密。protocols/authentication.py 与 routes/authentication.py 提供独立 /authentication/run AG-UI；main/authClient.ts 使用 SDK；main/auth.ts 负责浏览器和退出协调；shared/authentication.ts 定义无 token 公开合同。移除 auth:get-access-token，新增 cancel/progress/cloud-request。冷启动保留原启动顺序，点击登录才初始化 Python；启动和退出清理凭据。详见 docs/YUNXIAO_LOGIN.md。

新 docs/YUNXIAO_LOGIN.md 可收录本文第 3、4、6 节，或复制随包附带的原说明后将本机绝对路径替换为内网路径。说明文件不影响认证运行。

## 8. 补丁核对与完整性

若内网安装 Git，可以先只检查，不修改任何源码：

```powershell
git apply --check --ignore-space-change --ignore-whitespace <移植资料目录>/yunxiao-login-runtime.patch
```

检查成功只表示补丁匹配内网原代码，不代表真实云效联调通过。用户选择手工移植时无需 git apply。需要自动应用时应先保存自己的修改并检查基线；内网存在差异时应按本文逐项合并，不能强制覆盖。

源码包 files/ 下为 22 个白名单文件的当前完整内容，保留原始字节（包括 PowerShell BOM）；SOURCE_MANIFEST.json 记录原/目标 SHA-256 与代码行数。对于大型文件，files/ 用于比对，不要求整文件覆盖。配置模板为 CONFIGURATION.example，全部为占位内容。

运行补丁只含 17 个必需文件；可选补丁只含 5 个测试/辅助文件。Backend/.env.example 与真实 .env 均不在补丁或源码包内，必须按第 4 节手工追加。文档可以独立复制，源码包只是减少手工输入错误。
