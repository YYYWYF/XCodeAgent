# Windows 平台修复命令参考

本文件只在 Agent 确认当前工作区宿主是 **Windows**（`echo %OS%` 输出 `Windows_NT`，或 PowerShell `$PSVersionTable.OS` 含 `Win`）时使用。

**Shell 选择**：DevAgent Studio 的 `execute` 工具在 Windows 上默认通过 `cmd.exe /c` 执行命令。本文件命令以 **CMD 语法**为主，能用 PowerShell 时优先 PowerShell（功能更全）；如果命令必须用 PowerShell（如 `Remove-Item -Recurse`），明确用 `powershell -Command "..."` 包装。

**路径分隔符**：Windows 命令接受 `\` 和 `/` 两种分隔符，但 `execute` 工具的命令字符串里**优先用 `\`** 以避免某些 CMD 内置命令的解析问题。

## 环境诊断（共用）

```cmd
:: 一次性收集 Node/pnpm/版本管理器信息（每项失败都不阻塞下一项）
node --version 2>nul || echo node: NOT_FOUND
pnpm --version 2>nul || echo pnpm: NOT_FOUND
corepack --version 2>nul || echo corepack: NOT_FOUND
where nvm 2>nul && echo nvm: available || echo nvm: NOT_FOUND
where fnm 2>nul && echo fnm: available || echo fnm: NOT_FOUND
echo %USERPROFILE%
echo %PROCESSOR_ARCHITECTURE%
```

收集 `%USERPROFILE%`（用户家目录，例如 `C:\Users\zhangsan`）便于后续拼路径。

## A. Node 版本不符

### A.1 检查已有版本管理器

```cmd
:: nvm-windows 已装时
nvm list
```

如果输出里有符合 `engines.node` 范围的版本（例如 `20.19.0`），直接用：

```cmd
nvm use 20.19.0
node --version
```

**注意**：nvm-windows 的 `nvm use` 需要写 `C:\Program Files\nodejs` 的软链权限——nvm-windows 默认装到这个位置，**首次安装时**已经请求过管理员权限。如果用户的 nvm 装在系统目录但没拿到权限，`nvm use` 会报 `exit status 1: Access is denied`。这种情况让 nvm 改装到用户目录（见 A.3）。

### A.2 没装符合版本时：用 nvm-windows 装

```cmd
nvm install 20.19.0
nvm use 20.19.0
node --version
```

### A.3 用户系统连 nvm 都没装：按生成应用的运行环境选装法

**判断生成应用要在哪种环境跑**——看后端注入的 `failureEvidence.deployment.npm_registry`：
- 含 `central.jaf.cmbchina.cn` → **公司内部**：跳到下面的"公司内部装法"
- 否则 → **公司外部**：走下面的"公司外部装法"

#### 公司外部装法

**优先推荐 fnm**（不需要管理员权限，纯用户目录）：

```cmd
:: 用 winget 装 fnm（Windows 10 1809+ 自带 winget）
winget install Schniz.fnm

:: 装完后 fnm 立即可用
fnm install 20.19.0
fnm use 20.19.0
node --version
```

如果 winget 不可用（旧版 Windows），引导用户从 GitHub Releases 下载 fnm 二进制并解压到 `%USERPROFILE%\bin\`，把这一步写入报告让用户跑——Agent 不要直接写二进制文件。

**退而求其次**用 nvm-windows：

```cmd
:: nvm-windows 需要 .exe 安装包，无法用一行命令静默装。
:: Agent 把下载链接和安装步骤写入报告，让用户跑：
::   1. 从 https://github.com/coreybutler/nvm-windows/releases 下载 nvm-setup.exe
::   2. 双击安装（会请求管理员权限一次）
::   3. 安装完后 nvm 命令可用
```

**注意**：nvm-windows 安装**必须**走 GUI 安装包（涉及服务注册），无法用 winget/解压静默装。这是 Windows 平台的固有限制。

#### 公司内部装法（招行桌面助手，不走 GitHub/winget）

公司内部**禁止**从 GitHub 下载，也**不要**跑 `winget install`（公司软件分发要走桌面助手）：

1. **不要**跑 `winget install Schniz.fnm`，也**不要**给用户贴 GitHub 下载链接——这两条在公司内部都不可达。
2. 把下面指引写进修复报告的"用户下一步操作"：
   ```
   1. 打开「招行桌面助手」
   2. 进入「软件管理」
   3. 搜索「NVM for Windows」
   4. 点安装
   5. 安装完成后回到当前工作台，点「重试诊断并修复」
   ```
3. 用户装完桌面助手版的 nvm-windows 后，Agent 在用户重试时验证可用：
   ```cmd
   nvm version
   nvm list
   nvm install 20.19.0
   nvm use 20.19.0
   node --version
   ```
   然后继续走 A.4 激活 pnpm。

**注意**：招行桌面助手版的 nvm-windows 与官方版可能装到不同位置（通常 `%USERPROFILE%\nvm` 或 `C:\Program Files\nvm`）。Agent 不需要知道具体位置，只要 `nvm version` 能跑通就用它。

### A.4 激活 pnpm 并重试

```cmd
corepack enable
corepack prepare pnpm@11.9.0 --activate
pnpm --version

cd frontend && pnpm install
```

## B. pnpm 命令缺失

### B.1 优先用 corepack

```cmd
corepack enable
corepack prepare pnpm@11.9.0 --activate
pnpm --version
```

**注意**：`corepack enable` 在 Windows 上需要在 Node 安装目录下创建符号链接。如果 Node 装在 `C:\Program Files\nodejs\`（系统目录），corepack 会报 `EPERM`。

**解决**：让 Node 装到用户目录（参考 A.3 用 fnm 装的 Node 就在 `%USERPROFILE%\.fnm\node-versions\` 下，**不在系统目录**），corepack enable 就不会撞权限。

### B.2 corepack 不可用时：用 npm 装到用户目录

```cmd
:: 改 npm 全局前缀到用户目录
if not exist "%USERPROFILE%\.npm-global" mkdir "%USERPROFILE%\.npm-global"
npm config set prefix "%USERPROFILE%\.npm-global"

:: 把新 prefix 加入 PATH（当前子进程生效）
set "PATH=%USERPROFILE%\.npm-global;%PATH%"

:: 装 pnpm
npm install -g pnpm@11.9.0
pnpm --version
```

**注意**：CMD 的 `set PATH=...` 只在当前 cmd 进程有效；后续 `pnpm install` 必须在**同一个** `execute` 调用里跑：

```cmd
set "PATH=%USERPROFILE%\.npm-global;%PATH%" && cd frontend && pnpm install
```

## C. pnpm 版本与 lockfile 不兼容

```cmd
corepack enable
corepack prepare pnpm@11.9.0 --activate
pnpm --version

cd frontend && pnpm install
```

如果 `corepack enable` 报权限错（EPERM），按 B.1 的"解决"段落处理。

## D. 网络/镜像源不通

### D.1 诊断当前 registry

```cmd
cd frontend && pnpm config get registry
```

### D.2 写入镜像源

`.npmrc` 被 `SENSITIVE_FILE_NAMES` 拦截，**不能用 `write_file`/`delete_file` 工具**——必须用 `execute` 跑 CMD/PowerShell 命令：

```cmd
:: CMD：新建或追加
if not exist frontend\.npmrc (
  echo registry=<注入值>> frontend\.npmrc
) else (
  echo registry=<注入值>>> frontend\.npmrc
)
type frontend\.npmrc
```

或 PowerShell（更稳）：

```cmd
powershell -Command "Add-Content -Path 'frontend\.npmrc' -Value 'registry=<注入值>'"
powershell -Command "Get-Content 'frontend\.npmrc'"
```

`<注入值>` 来自后端 `Settings.npm_registry`（环境变量 `DEVAGENTSTUDIO_NPM_REGISTRY`）：
- 公司外部（默认）：`https://registry.npmmirror.com`
- 公司内部（招商银行）：`http://central.jaf.cmbchina.cn/artifactory/api/npm/group-npm/`

### D.3 重试

```cmd
cd frontend && pnpm install
```

## F. 原生模块编译失败

### F.1 先试预编译镜像（自动）

用 `execute` 跑 PowerShell 追加到 `/frontend/.npmrc`（`<MIRROR>` 是后端注入值，默认 `https://registry.npmmirror.com`）：

```cmd
powershell -Command "@('prefer-offline=false','node-linker=hoisted','sharp_libvips_binary_host=<MIRROR>/mirrors/sharp-libvips/','node_sass_binary_site=<MIRROR>/mirrors/node-sass/') | Add-Content 'frontend\.npmrc'"
powershell -Command "Get-Content 'frontend\.npmrc'"
```

然后删除 node_modules 并重试：

```cmd
:: 删除 node_modules（CMD 语法；如果目录很大，用 PowerShell 更快更稳）
if exist frontend\node_modules rmdir /s /q frontend\node_modules

:: 重试安装
cd frontend && pnpm install
```

如果 `rmdir /s /q` 报"目录非空"或长时间不动（Windows 文件锁常见），换 PowerShell：

```cmd
powershell -Command "Remove-Item -Recurse -Force 'frontend\node_modules' -ErrorAction SilentlyContinue"
cd frontend && pnpm install
```

### F.2 仍失败：装 Visual Studio Build Tools（**让用户在终端跑**）

构建工具链需要管理员权限，**Agent 不要自动跑**。写到报告的"用户下一步操作"里：

```
选项 1（轻量，推荐）：用 winget 装
  winget install Microsoft.VisualStudio.2022.BuildTools --override "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"

选项 2（完整）：装 Visual Studio Build Tools
  从 https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022 下载
  安装时勾选「C++ 生成工具」工作负载

选项 3（旧方式）：npm 包
  以管理员身份打开 PowerShell，跑：
    npm install --global windows-build-tools
```

用户装完后回到工作台点重试。

## G. GitHub 二进制下载失败

用 `execute` 跑 PowerShell **追加**到 `/frontend/.npmrc` 末尾（`<MIRROR>` 是后端注入值，默认 `https://registry.npmmirror.com`）：

```cmd
powershell -Command "@('electron_mirror=<MIRROR>/mirrors/electron/','electron_builder_binaries_mirror=<MIRROR>/mirrors/electron-builder-binaries/','node_sass_binary_site=<MIRROR>/mirrors/node-sass/','puppeteer_download_base_url=<MIRROR>/mirrors/chromium-browser-snapshots/','sharp_libvips_binary_host=<MIRROR>/mirrors/sharp-libvips/','playwright_download_host=<MIRROR>/mirrors/playwright/') | Add-Content 'frontend\.npmrc'"
powershell -Command "Get-Content 'frontend\.npmrc'"
```

如果注入值是公司内部的 artifactory（形如 `http://central.jaf.cmbchina.cn/.../api/npm/group-npm/`），它**不提供** `/mirrors/...` GitHub 二进制转发——跳过本节，把"当前镜像源不代理 GitHub 二进制"写入报告让用户找运维解决。

然后：

```cmd
cd frontend && pnpm install
```

## H. node_modules 损坏

```cmd
:: 优先用 CMD（无依赖）
if exist frontend\node_modules rmdir /s /q frontend\node_modules

:: 如果上面命令报"目录非空"或长时间不动，换 PowerShell
powershell -Command "Remove-Item -Recurse -Force 'frontend\node_modules' -ErrorAction SilentlyContinue"

:: 重试安装
cd frontend && pnpm install
```

**Windows 特有的坑**：
- 文件被进程占用（dev server 在跑、IDE 打开了文件）→ 删除会失败。让后端 launcher 先停掉预览进程，再删。
- 路径过长（node_modules 嵌套深）→ CMD `rmdir` 可能报"路径太长"。换 PowerShell 的 `Remove-Item` 或启用 Windows 长路径支持。

## I. pnpm store 损坏

```cmd
pnpm store prune
if exist frontend\node_modules rmdir /s /q frontend\node_modules
cd frontend && pnpm install
```

仍失败时让用户在终端跑（**不要替用户跑**）：

```cmd
:: 找到 store 位置（通常 %LOCALAPPDATA%\pnpm\store）
pnpm store path

:: 手动删除该目录后重装
rmdir /s /q "%LOCALAPPDATA%\pnpm\store"
cd frontend && pnpm install
```

## K. 安装超时

```cmd
:: 切换到镜像源再重试（沿用 D 节）
cd frontend && pnpm install
```

后端会把 `INSTALL_TIMEOUT_SECONDS` 从 120 提升到 600；这是后端代码改动，不在本 skill 范围。

## L. 磁盘空间不足

```cmd
:: 查 C 盘剩余空间
wmic logicaldisk where "DeviceID='C:'" get FreeSpace,Size /format:list

:: 或 PowerShell
powershell -Command "Get-PSDrive C | Select-Object Used,Free"
```

把可用空间写进报告，让用户清理：

```cmd
:: 推荐先跑这条（安全）
pnpm store prune
```

## M. 权限不足

### M.1 诊断

```cmd
where node
where pnpm
echo %USERPROFILE%
```

如果 `where node` 输出 `C:\Program Files\nodejs\node.exe`，就是装到系统目录了。

### M.2 在用户目录装 Node（**不需要管理员**）

**判断生成应用要在哪种环境跑**——看后端注入的 `failureEvidence.deployment.npm_registry`：

- **公司外部**（不含 `central.jaf.cmbchina.cn`）：用 winget 装 fnm

  ```cmd
  :: 用 winget 装 fnm（Windows 10 1809+ 自带 winget）
  winget install Schniz.fnm

  :: 装目标 Node
  fnm install 20.19.0
  fnm use 20.19.0

  :: 激活 pnpm
  corepack enable
  corepack prepare pnpm@11.9.0 --activate

  :: 重试安装
  cd frontend && pnpm install
  ```

- **公司内部**（含 `central.jaf.cmbchina.cn`）：从招行桌面助手装 NVM for Windows

  公司内**禁止**跑 `winget install`，也**不要**给用户贴 GitHub 下载链接——这两条在公司内部都不可达。把下面指引写进修复报告的"用户下一步操作"：

  ```
  1. 打开「招行桌面助手」
  2. 进入「软件管理」
  3. 搜索「NVM for Windows」
  4. 点安装
  5. 安装完成后回到当前工作台，点「重试诊断并修复」
  ```

  用户装完桌面助手版的 nvm-windows 后，Agent 在用户重试时验证可用：

  ```cmd
  nvm version
  nvm install 20.19.0
  nvm use 20.19.0
  node --version

  :: 激活 pnpm
  corepack enable
  corepack prepare pnpm@11.9.0 --activate

  :: 重试安装
  cd frontend && pnpm install
  ```

**绝对不要**：
- ❌ 用管理员 PowerShell 跑 `pnpm install`
- ❌ 用 `icacls "C:\Program Files\nodejs" /grant ...` 改系统目录权限
- ❌ 用 `runas /user:Administrator ...` 提升权限

## Windows 特有的注意事项

1. **路径长度**：Windows 默认 MAX_PATH=260 字符。node_modules 嵌套深时容易撞。建议启用长路径支持：
   ```
   :: 需要管理员权限，让用户跑
   reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f
   ```
   Agent **不要**自动跑（要管理员），写进报告让用户跑。

2. **文件锁**：Windows 上文件被进程占用时无法删除。常见占用源：
   - 仍在运行的 dev server（让后端 launcher 停掉）
   - 资源管理器 / IDE 打开了 node_modules 里的文件（提示用户关掉再试）
   - Windows Defender / 杀毒软件正在扫描（等几秒重试）

3. **PowerShell 执行策略**：PowerShell 默认 Restricted（禁止跑脚本）。Agent 调 `powershell -Command` 不受影响（命令行参数直接执行，不走脚本文件），但如果以后要跑 `.ps1` 文件，需要 `-ExecutionPolicy Bypass`：
   ```
   powershell -ExecutionPolicy Bypass -File script.ps1
   ```
   本 skill 不依赖 `.ps1` 文件，所以这不是问题。

4. **符号链接**：Windows 的符号链接需要开发者模式或管理员权限。pnpm 默认用 hardlink，不需要符号链接，所以**正常情况不会撞**。但 corepack enable 在 Node 装到系统目录时会创建符号链接，这就是为什么 B.1 推荐用 fnm 装 Node 到用户目录的原因。
