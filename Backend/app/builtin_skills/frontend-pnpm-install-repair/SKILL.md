---
name: frontend-pnpm-install-repair
description: 前端依赖安装失败的自动诊断与修复技能。当 DevAgent Studio 在工作区启动前端预览、执行集成测试、或运行开发阶段任务时，pnpm install 命令因 Node 版本不符、镜像源不通、lockfile 与 pnpm 版本不匹配、依赖目录损坏、网络/代理异常、原生模块编译失败等原因失败，本技能给出一份"先诊断、后修复、再验证"的确定性操作序列，让 Agent 自动归类失败模式并执行对应修复动作（含安装/激活 Node 与 pnpm），全程无需用户在自己终端跑命令。
---

# 前端依赖安装失败自动诊断与修复规范

本技能规定 Agent 在工作区执行 `pnpm install` 失败时的**诊断→自动修复→重试验证**操作序列。目标是把"原始 stderr 抛给用户"替换为"Agent 识别失败模式 → 自动执行修复 → 自动重试 → 输出修复报告"的闭环。

**核心立场**：Agent 在 DevAgent Studio 的子进程里执行命令，**子进程的环境变量、PATH、shell 配置都是独立的**——在子进程里切换到指定 Node 版本**不会影响**用户的全局 shell、其他终端窗口或其他项目。所以"修环境"这类动作 Agent 应该**直接做**，不要把命令贴给用户让他们自己去跑。

## 适用范围

适用场景：
- 工作台「服务状态」抽屉里启动预览时 `pnpm install` 失败
- 集成测试运行器在 `frontend_install` 节点失败
- 代码审查修复子流程中 `pnpm_install_frontend` 工具失败
- 开发阶段任务执行时识别到 `node_modules` 不完整或损坏

**不适用**于：
- `pnpm build` / `pnpm run dev` / `pnpm test` 等运行时阶段失败（属于构建/运行问题）
- 后端 Maven/Gradle 安装失败
- 业务代码本身的语法/类型错误

## 核心原则

1. **先诊断、再动手**：每次执行修复前必须先确认失败模式属于本技能列出的某一类，根据 stderr/stdout 中的识别信号归类。
2. **能自动修就自动修**：Node 版本不对就装/切到对的版本，pnpm 没装就用 corepack 激活，镜像源不通就改 `.npmrc`，node_modules 坏了就删了重装。**不要**把这些命令贴给用户让他们自己跑——子进程是隔离的，做了不污染用户系统。
3. **修复动作要最小**：能用 corepack 解决的不要装 nvm；能用 `pnpm config set` 解决的不要删 `node_modules`；能 `--force` 重装的不要动 `pnpm-lock.yaml`。
4. **修复后必须重试**：执行完修复动作后，必须重新执行一次 `pnpm install` 验证修复有效，而不是直接报告"已修复"。
5. **同一工作区不能并发执行**：pnpm install 是排他操作，开始修复前必须先确认工作区没有其他正在执行的 pnpm 进程；启动新进程前清理 PID 文件指向的僵尸进程。
6. **修复动作是 Agent 行为**：本技能由 DevAgent Studio 的诊断修复流程触发，Agent 通过 `execute`/`write_file`/`read_file`/`delete_file` 工具完成所有动作，**不要**让用户在自己终端里跑命令。

## 平台识别（必读）

本技能的所有修复命令都分 macOS 和 Windows 两套写法。**开始任何修复动作前，先确认生成应用的目标操作系统**，再读对应的平台参考文件，**严格按对应平台的命令写**。

**选中规则**（按优先级）：

1. 后端 `Settings.deployment_platform`（环境变量 `DEVAGENTSTUDIO_PLATFORM`）显式配置：
   - `macos` → 读 `references/macos.md`
   - `windows` → 读 `references/windows.md`
2. 未配置时（开发期），按当前进程操作系统识别（`sys.platform`）：
   - `darwin` → 读 `references/macos.md`
   - `win32` / `cygwin` → 读 `references/windows.md`

**绝对不要**在没确认平台的情况下盲跑命令——`rm -rf` 在 Windows CMD 不存在、`rmdir /s /q` 在 macOS 报错，套错平台命令等于把修复搞砸。

## npm registry 选择（必读）

本技能所有写 `frontend/.npmrc` 的 `registry=` 行，**值必须读自后端 `Settings.npm_registry`**（环境变量 `DEVAGENTSTUDIO_NPM_REGISTRY`）。后端在调用诊断/修复流程时把这个值注入到修复上下文里。

| 生成应用运行环境 | 期望 registry |
| --- | --- |
| 公司外部（默认） | `https://registry.npmmirror.com` |
| 公司内部（招商银行） | `http://central.jaf.cmbchina.cn/artifactory/api/npm/group-npm/` |

**绝对不要**：
- ❌ 不要在 SKILL/命令里硬编码 registry URL——必须读注入值
- ❌ 不要保留旧的 `https://registry.npmjs.org` 当 fallback——网络不通时换镜像源，不通时报告而不是回退

## Agent 可用工具速查

修复动作只能用以下工具完成，不要创建临时脚本：

| 工具 | 用途 |
| --- | --- |
| `read_file` | 读 `package.json`、`pnpm-lock.yaml`、`.npmrc`、错误日志 |
| `write_file` | 写 `frontend/.npmrc`（追加或新建） |
| `execute` | 跑 `node --version`、`pnpm --version`、Node/pnpm 安装与切换命令、`pnpm config set/get`、`pnpm store prune`、`pnpm install`、`rm -rf frontend/node_modules` |

**注意**：
- `delete_file` 工具**不能**删 `.npmrc`（被 `SENSITIVE_FILE_NAMES` 拦截），也**不能**删目录（`node_modules/`）；删 `node_modules/` 必须用 `execute` 跑 `rm -rf`（macOS/Linux）或 `rmdir /s /q`（Windows CMD）/ `powershell -Command "Remove-Item -Recurse -Force"`。
- `write_file` 也不能覆盖写 `.npmrc`（同样被拦）——只能由 `execute` 用 shell 重定向（macOS/Linux）或 CMD `>` / PowerShell `Set-Content` 完成。具体命令见平台参考。
- 修改 `.npmrc` 的**追加**操作通过 `execute` 跑 `cat >> frontend/.npmrc <<EOF ... EOF`（POSIX）或 `echo registry=... >> frontend\.npmrc`（CMD）完成，**不要**用 `write_file` 整个重写——它会因 sensitive 拦截失败。

`execute` 在工作区根目录运行；访问 `frontend/` 子目录时的命令前缀按平台区分（见对应平台参考文件）。

## 工作区关键路径（虚拟绝对路径）

| 用途 | 路径 |
| --- | --- |
| 前端工程根 | `/frontend/` |
| 依赖声明 | `/frontend/package.json` |
| 依赖锁定 | `/frontend/pnpm-lock.yaml` |
| pnpm workspace 配置 | `/frontend/pnpm-workspace.yaml` |
| 工程级 npmrc（可新增/修改） | `/frontend/.npmrc` |
| 已安装依赖目录 | `/frontend/node_modules/` |

## 失败模式总览

按"运营同事实际遇到"的概率从高到低排列。每节给出**识别信号 → 根因 → 修复思路**，**具体平台命令在 `references/macos.md` 与 `references/windows.md` 里**，按下表"参考章节"列跳转。

| 模式 | 触发关键字（stderr/stdout 中） | 修复思路（命令细节见平台参考） |
| --- | --- | --- |
| A. Node 版本不符 | `engines.node`、`not compatible`、`Unsupported engine`、`ERR_PNPM_UNSUPPORTED_ENGINE` | 用版本管理器（nvm/fnm/nvm-windows）切换到 `package.json.engines.node` 要求的版本 |
| B. pnpm 命令缺失 | `command not found: pnpm`、`spawn pnpm ENOENT`、`'pnpm' 不是内部或外部命令`、退出码 127 | 用 corepack 激活模板要求的 pnpm；corepack 不可用时按平台备选方案 |
| C. pnpm 版本与 lockfile 不兼容 | `ERR_PNPM_LOCKFILE_VERSION_MISMATCH`、`lockfile version` | corepack prepare 模板要求的 pnpm 版本 |
| D. 网络/镜像源不通 | `ETIMEDOUT`、`ECONNRESET`、`ENOTFOUND`、`ECONNREFUSED`、`fetch failed`、`getaddrinfo`、`socket hang up` | 写 `.npmrc` 切到镜像源，重试 |
| F. 原生模块编译失败 | `node-gyp`、`gyp ERR`、`MSBuild`、`make: command not found`、`python3: command not found` | 先试预编译镜像 → 仍失败再装编译链（平台命令见参考） |
| G. GitHub 二进制下载失败 | `github.com/.../releases/download`、`node-sass`、`puppeteer`、`electron`、`sharp` 下载失败 | 在 `.npmrc` 配置二进制镜像 |
| H. node_modules 损坏 | `ENOENT: no such file or directory`（指向 `node_modules/.pnpm/...`）、`Unexpected end of JSON input` | 删 `node_modules` 重装（删除命令按平台区分） |
| I. pnpm store 损坏 | `ERR_PNPM_STORE_*`、`store is corrupted`、校验和不匹配 | `pnpm store prune` 后重装 |
| J. peer dependency 冲突 | `ERR_PNPM_PEER_DEP_CONFLICT`、`peer dep missing`、`Unmet peer dependencies` | 报告用户（涉及模板依赖，不自动改） |
| K. 安装超时 | 退出码 124、`subprocess.TimeoutExpired`、120s 内未完成 | 提升 timeout 上限；同时切换到镜像源 |
| L. 磁盘空间不足 | `ENOSPC`、`no space left on device` | 报告用户清理磁盘（不替用户删文件） |
| M. 权限不足 | `EACCES`、`EPERM`、`operation not permitted` | 用版本管理器在用户目录装 Node（避开 sudo/管理员） |

---

## A. Node 版本不符（engines.node 不兼容）

**识别信号**：

```
error   engines.node: not compatible
error   expected node >=20.19 <23
error   Unsupported engine: wanted node >=20.19, got node v18.x.x
ERR_PNPM_UNSUPPORTED_ENGINE
```

**根因**：当前 shell 的 Node 版本不在 `frontend/package.json` 的 `engines.node` 范围内（模板要求 `>=20.19 <23`）。

**自动修复序列**：

1. **诊断**：用 `execute` 收集当前 Node/pnpm 版本作为证据（具体命令见平台参考的"环境诊断"节）。
2. **读模板要求**：用 `read_file` 读 `/frontend/package.json`，提取 `engines.node` 范围（例如 `>=20.19 <23`）和 `packageManager`（例如 `pnpm@11.9.0`）。
3. **挑选目标 Node 版本**：从 `engines.node` 范围里挑一个具体版本（取下限的稳定版本，例如 `>=20.19 <23` → `20.19.0`）。
4. **检查版本管理器**：macOS 优先 nvm，其次 fnm；Windows 用 nvm-windows 或 fnm。如果都没装，按平台参考文件引导装一个。
   - **判断生成应用要在哪种环境跑**：看 `failureEvidence.deployment.npm_registry` 注入值——
     - 含 `central.jaf.cmbchina.cn` → **公司内部**：nvm / nvm-windows 都得从**招行桌面助手**装（不能从 GitHub 下载）。Agent 不要跑 `curl | bash`，也不要让用户去 GitHub。把"打开招行桌面助手 → 软件管理 → 搜索 NVM（macOS）/ NVM for Windows（Windows）→ 点安装"写进报告的"用户下一步操作"，让用户在自己机器上装完后回到工作台点重试。
     - 否则 → **公司外部**：macOS 走 `curl ... | bash`（GitHub raw），Windows 走 nvm-windows 的 GitHub Releases。
5. **切换并验证**：用版本管理器切到目标 Node，跑 `node --version` 确认；然后跑 `corepack enable && corepack prepare pnpm@11.9.0 --activate` 激活模板要求的 pnpm。
6. **重试 `pnpm install`**（在同一个 shell 上下文里），确保新激活的 Node/pnpm 生效。
7. **报告**：把"已切换 Node 到 v20.19.0、pnpm install 成功"作为修复事实写入报告。

**绝对不要**：
- ❌ 不要装到系统目录（`/usr/local`、`C:\Program Files`）——会留下权限隐患
- ❌ 不要修改 `frontend/package.json` 的 `engines.node` 去适配用户的旧 Node——这反向污染模板
- ❌ 生成的应用要在公司内部跑时，不要尝试从 GitHub 下载 nvm/nvm-windows（网络不通，且违反公司软件分发规定）——必须引导用户走招行桌面助手

平台相关的具体命令（含 nvm 安装、环境变量加载、子进程 PATH 设置）：见 `references/macos.md` 和 `references/windows.md` 的 **A 节**。

---

## B. pnpm 命令缺失

**识别信号**：

```
shutil.which("pnpm") returned None
command not found: pnpm
spawn pnpm ENOENT
[WinError 2] 系统找不到指定的文件
'pnpm' 不是内部或外部命令
```

**根因**：当前环境没有可执行的 `pnpm`。

**自动修复序列**：

1. **诊断**：先确认 Node 已装——`node --version` 必须能跑通，否则先去 A 模式装 Node。
2. **优先用 corepack**（Node ≥16.13 自带，**不需要 sudo/管理员**）激活模板要求的 pnpm 版本。
3. **corepack 不可用时**：按平台备选方案（npm 全局安装到用户目录前缀）激活 pnpm，避免系统目录权限问题。
4. **重试 `pnpm install`**。
5. **报告**：写"已通过 corepack 激活 pnpm@11.9.0"（或对应方案）。

**绝对不要**：
- ❌ 不要用 sudo/管理员权限装 pnpm——会留下权限隐患
- ❌ 不要修改 `/frontend/package.json` 的 `packageManager` 字段

平台相关的具体命令：见 `references/macos.md` 和 `references/windows.md` 的 **B 节**。

---

## C. pnpm 版本与 lockfile 不兼容

**识别信号**：

```
ERR_PNPM_LOCKFILE_VERSION_MISMATCH
Cannot install with "pnpm@<X>" because the lockfileVersion is <Y>
lockfile is not compatible with the current pnpm version
```

**根因**：模板 `frontend/package.json` 的 `packageManager: pnpm@11.9.0` 对应 lockfileVersion 9.x；用户当前 pnpm 是 7.x/8.x（lockfileVersion 5/6）或 12.x（lockfileVersion 10），就会撞这个错。

**自动修复序列**：

1. **诊断**：拿当前 pnpm 版本与 `package.json.packageManager` 期望值。
2. **corepack 切换**：激活模板声明的 pnpm 版本。
3. **重试 `pnpm install`**。
4. **报告**：写"已用 corepack 切换到 pnpm@11.9.0，与 lockfileVersion 对齐"。

**绝对不要**：
- ❌ 不要改 `frontend/package.json` 的 `packageManager` 字段去适配用户的旧 pnpm
- ❌ 不要删 `pnpm-lock.yaml`——版本对齐后旧 lockfile 还能继续用，删了要全量重新解析

平台相关的具体命令：见 `references/macos.md` 和 `references/windows.md` 的 **C 节**。

---

## D. 网络/镜像源不通

**识别信号**：

```
ETIMEDOUT
ECONNRESET
ENOTFOUND registry.npmjs.org
ECONNREFUSED
fetch failed
getaddrinfo EAI_AGAIN
socket hang up
network request to https://registry.npmjs.org/... failed
```

或 install 跑到超时仍无进展，且 stdout 停在 `Resolving:` / `Downloading:` 阶段。

**根因**：当前 shell 走默认 registry（`https://registry.npmjs.org`），但用户网络无法访问（公司内网/海外网络受限/代理未生效）。

**自动修复序列**：

1. **诊断当前 registry**：跑 `pnpm config get registry`。
2. **检查工作区是否已有 .npmrc**：用 `read_file /frontend/.npmrc` 读一下（读是允许的，只是不能写）。
   - **已存在且已配置镜像源** → 跳过写入，直接重试
   - **不存在 或 没设 registry** → 继续下一步
3. **写入镜像源**：`.npmrc` 被 `SENSITIVE_FILE_NAMES` 拦截，**不能用 `write_file`/`delete_file` 工具**——必须用 `execute` 跑 shell 命令写入（命令细节见平台参考的 D 节）：
   - macOS/Linux：`cat >> frontend/.npmrc <<'EOF'\nregistry=<注入值>\nEOF`（不存在时 `>` 新建）
   - Windows CMD：`echo registry=<注入值> >> frontend\.npmrc`
   - Windows PowerShell：`Add-Content frontend\.npmrc "registry=<注入值>"`
   
   `<注入值>` 是后端通过 `Settings.npm_registry` 注入到修复上下文里的（`failureEvidence.deployment.npm_registry`），公司外部默认 `https://registry.npmmirror.com`，公司内部是 artifactory 代理。
4. **重试 `pnpm install`**。
5. **重试成功** → 报告"已切换到镜像源并安装成功"。
6. **重试仍失败** → 报告"当前网络无法访问镜像源 `<注入值>`"，让用户确认网络连接（拔网线重连、重启路由器、切换 Wi-Fi 等基础排查）。

平台相关的具体命令：见 `references/macos.md` 和 `references/windows.md` 的 **D 节**。

---

## F. 原生模块编译失败

**识别信号**：

```
node-gyp
gyp ERR! find Python
gyp ERR! find VS
MSBuild : error MSB3428
make: command not found
python3: command not found
xcode-select: error: tool 'xcodebuild' requires Xcode
```

**根因**：依赖里含原生模块（`node-sass`、`sharp`、`bcrypt` 等），需要本地编译，但用户系统缺构建工具链。

**自动修复序列**（先试免编译方案，再考虑装编译链）：

**第 1 步：先试预编译二进制路径**（**优先**，多数原生模块都有 prebuilt binary）：

1. 用 `execute` 跑 shell 命令把镜像配置**追加**到 `/frontend/.npmrc`（不能用 `write_file`/`delete_file`，`.npmrc` 被 SENSITIVE_FILE_NAMES 拦截）——`<MIRROR>` 是后端注入值（`failureEvidence.deployment.npm_registry`，公司外部默认 `https://registry.npmmirror.com`）：
   ```ini
   prefer-offline=false
   node-linker=hoisted
   sharp_libvips_binary_host=<MIRROR>/mirrors/sharp-libvips/
   node_sass_binary_site=<MIRROR>/mirrors/node-sass/
   ```
2. 删 `/frontend/node_modules/`（用 `execute` 跑平台对应的 `rm -rf` / `rmdir /s /q`）
3. 重试 `pnpm install`：预编译版本会优先下载，跳过本地编译。
4. **成功** → 报告"已切换预编译二进制路径并安装成功"，结束。

**第 2 步：第 1 步失败后再装编译工具链**：

构建工具链的安装**通常需要管理员权限或系统弹窗**，**不要 Agent 自动跑**，把对应平台的命令写入报告让用户在自己终端跑：

- **macOS**：`xcode-select --install`（弹系统对话框，需用户点同意）
- **Windows**：管理员 PowerShell 跑 `npm install --global windows-build-tools`，或装 Visual Studio Build Tools（勾选 C++ 工作负载）
- **Linux**：`sudo apt-get install -y build-essential python3`

**第 3 步**：用户在终端装完工具链后回到工作台点重试，Agent 这一轮重新跑 `pnpm install` 即可（前面残留的 `.npmrc` 配置仍生效）。

平台相关的具体命令（含删除 `node_modules` 的安全命令、镜像写入方式）：见 `references/macos.md` 和 `references/windows.md` 的 **F 节**。

---

## G. GitHub 二进制下载失败

**识别信号**：

```
fetching https://github.com/.../releases/download/... failed
node-sass: unable to download binding.node
puppeteer: failed to download Chromium
electron: failed to download
sharp: unable to download prebuilt binary
```

**根因**：依赖的 `postinstall` 脚本要从 GitHub Releases 下载二进制，但用户网络访问 GitHub 受限。

**自动修复序列**：

1. 写 `/frontend/.npmrc`（**追加**到已有内容末尾，**不要**覆盖）——把 `<MIRROR>` 替换为后端注入的镜像源基础地址（`Settings.npm_registry`，公司外部默认 `https://registry.npmmirror.com`）：
   ```ini
   electron_mirror=<MIRROR>/mirrors/electron/
   electron_builder_binaries_mirror=<MIRROR>/mirrors/electron-builder-binaries/
   node_sass_binary_site=<MIRROR>/mirrors/node-sass/
   puppeteer_download_base_url=<MIRROR>/mirrors/chromium-browser-snapshots/
   sharp_libvips_binary_host=<MIRROR>/mirrors/sharp-libvips/
   playwright_download_host=<MIRROR>/mirrors/playwright/
   ```
   注意：公司内部的 artifactory 代理（`http://central.jaf.cmbchina.cn/artifactory/api/npm/group-npm/`）**不提供** `/mirrors/...` 这种 GitHub 二进制转发。如果 `<MIRROR>` 注入值是 artifactory，**跳过**本节直接走到末尾——把"当前镜像源不代理 GitHub 二进制，需要内部 GitHub 代理地址"写入报告让用户找运维。
2. 重试 `pnpm install`。
3. 成功 → 报告"已切换 GitHub 二进制镜像并安装成功"。
4. 仍失败 → 提取失败的包名写入报告，提示用户检查网络连接。

**追加 .npmrc 的安全做法**：
- 先 `read_file` 读已有 `.npmrc`
- 把已有内容按行拆开，过滤掉同名 key（避免重复定义）
- 把要写入的新行加进去
- `write_file` 写回合并后的完整内容

平台相关的具体命令：见 `references/macos.md` 和 `references/windows.md` 的 **G 节**。

---

## H. node_modules 损坏

**识别信号**：

```
ENOENT: no such file or directory, open '/frontend/node_modules/.pnpm/<pkg>/...'
Cannot find module '<pkg>'
Unexpected end of JSON input (in node_modules/.pnpm/lock.yaml)
```

**根因**：上次安装被中断（用户手动停止 / 后端重启 / 系统重启），`node_modules` 处于半完成状态。

**自动修复序列**：

1. **先确认无活跃 pnpm/vite 进程**（由后端 launcher 的 PID 文件清理负责，Agent 不直接管）。
2. **删除损坏的 `node_modules/`**——删除命令按平台区分（macOS/Linux 用 `rm -rf`，Windows 用 `rmdir /s /q` 或 PowerShell `Remove-Item -Recurse -Force`），具体见平台参考。
3. **不要**删 `/frontend/pnpm-lock.yaml`——它记录了已解析的依赖图，保留它能让重装快很多。
4. **重试 `pnpm install`**。
5. 成功 → 报告"已清理损坏的 node_modules 并重新安装成功"。
6. 仍失败 → 升级到模式 I（store 损坏）继续诊断。

平台相关的具体命令：见 `references/macos.md` 和 `references/windows.md` 的 **H 节**。

---

## I. pnpm store 损坏

**识别信号**：

```
ERR_PNPM_STORE_*
store is corrupted
Unexpected content hash
integrity checksum failed
```

**根因**：pnpm 全局 store 里的某个 tarball 损坏或被手动改过。store 位置按平台不同：
- macOS：`~/Library/pnpm/store`
- Linux：`~/.local/share/pnpm/store`
- Windows：`%LOCALAPPDATA%/pnpm/store`

**自动修复序列**：

1. **清 store 无效条目**：跑 `pnpm store prune`（命令跨平台一致）。
2. **同时清工作区 node_modules**（沿用模式 H 的步骤）。
3. **重试 `pnpm install`**：让 install 从干净的 store 重新链接。
4. 成功 → 报告"已清理 pnpm store 并安装成功"。
5. 仍失败 → 把以下内容写入报告让用户在自己终端跑（**不要替用户跑，因为会清掉所有项目的缓存**）：先 `pnpm store path` 找到 store 位置，再手动删除该目录后重装。

平台相关的具体命令：见 `references/macos.md` 和 `references/windows.md` 的 **I 节**。

---

## J. peer dependency 冲突

**识别信号**：

```
ERR_PNPM_PEER_DEP_CONFLICT
peer dep missing: react@..., required by ...
Unmet peer dependencies
```

**根因**：模板 `frontend/package.json` 中某个依赖的 peer 要求与另一个依赖的版本不匹配（典型场景：升级了 antd 但 antd-pro-components 还没跟上）。

**为什么不自动修**：依赖版本是模板工程师维护的契约，Agent 改任何版本都可能引入隐藏的兼容性问题。这种情况是**模板本身需要升级**，不是用户的工作区出问题。

**自动诊断 + 报告**：

1. 用 `read_file` 读 `/frontend/package.json`，确认所有 `dependencies` 和 `devDependencies` 的版本与模板原始定义一致。
2. 如果发现**用户业务代码里改了 `package.json` 的依赖版本**（这是模板工程的禁改区，见 `frontend-template-modification-boundary` SKILL），把这条作为诊断发现写入报告，**不要**回滚用户改动，**不要**继续修复——这属于业务侧决定，需要用户回滚或升级。
3. 如果 `package.json` 与模板一致但仍冲突，把"模板自身 peer dep 冲突"作为升级信号写入报告，提示用户**升级模板**或**联系模板维护者**。

**绝对不要**：
- ❌ 不要在 `package.json` 加 `pnpm.peerDependencyRules` 静默冲突——这会让真正的版本错位被掩盖
- ❌ 不要降级/升级任何依赖版本——模板由维护者统一管
- ❌ 不要在 `pnpm-workspace.yaml` 加 `overrides` 强制对齐版本（这是上一项的变种）

---

## K. 安装超时

**识别信号**：

- 后端日志记录 `INSTALL_TIMEOUT_SECONDS` 触发（默认 120s）
- 退出码 124（POSIX timeout 命令）
- 平台抛出 `subprocess.TimeoutExpired`

**根因**：默认 120s 超时太慢（首次安装几百 MB 依赖时不够），或网络慢。

**自动修复序列**：

1. **看 stderr 当前阶段**：
   - 停在 `Resolving:` 阶段 → 同时按模式 D 切换镜像源
   - 停在 `Downloading:` 大包（typescript/webpack/esbuild）→ 同时按模式 D 切换镜像源
   - 停在 `Linking:` / `Building:` → 同时按模式 F 切换到预编译二进制
2. **重试 `pnpm install`**：后端下次启动 launcher 时会用更长的超时（600s，需要后端配合提升）。
3. **报告**：写"已切换镜像源 / 已切换预编译路径；后端会把超时上限提升到 600s 后重试"。

**注意**：当前端 launcher 的 `INSTALL_TIMEOUT_SECONDS = 120` 是硬编码，Agent 不能改后端代码。Agent 能做的是：
- 通过切换到镜像源让下载变快
- 把"需要把 timeout 提升到 600s"作为升级建议写入报告，让后端开发者改代码

---

## L. 磁盘空间不足

**识别信号**：

```
ENOSPC: no space left on device
no space left on device
```

**根因**：用户磁盘满了。

**为什么不自动修**：Agent **不能**替用户删用户文件——用户磁盘上有什么是该用户决定的，Agent 删错了就是事故。

**自动诊断 + 报告**：

1. 用 `execute` 确认磁盘余量（命令按平台区分）。
2. 把"磁盘剩余空间不足（剩余 X GB）"作为诊断结果写入报告。
3. 给出**用户操作指引**（**不要自动执行**）：
   - 清理 pnpm store：`pnpm store prune`（推荐先跑这条，安全）
   - 清理其他项目的 `node_modules`（用户自己决定哪些项目可以清）
   - 清理系统缓存/回收站
4. 提示用户腾出空间后回到工作台点重试。

平台相关的具体命令：见 `references/macos.md` 和 `references/windows.md` 的 **L 节**。

---

## M. 权限不足

**识别信号**：

```
EACCES: permission denied
EPERM: operation not permitted
```

通常指向 macOS/Linux 的 `~/Library/pnpm/`、`/usr/local/lib/node_modules/`，或 Windows 的 `C:\Program Files\nodejs\` —— Node 装到了系统目录，全局 pnpm 的缓存和全局安装路径都在系统目录，当前用户没写权限。

**根因**：Node 装到了系统目录，全局 pnpm 装在系统目录。

**自动修复序列**（用版本管理器在用户目录重装 Node，避开 sudo/管理员）：

1. **诊断**：跑命令确认 Node 安装位置（命令按平台区分）。
2. **检查版本管理器**（macOS 优先 nvm/fnm，Windows 用 nvm-windows/fnm）：
   - **可用**：直接装目标 Node 版本并切换
   - **不可用**：装一个到用户目录（不需要 sudo/管理员）
3. **激活模板要求的 pnpm**：`corepack enable && corepack prepare pnpm@11.9.0 --activate`
4. **重试 `pnpm install`**（同一个 shell 上下文里）。
5. **报告**：写"已把 Node 切换到用户目录，避开了系统目录的权限问题"。

**绝对不要**：
- ❌ 不要 `sudo pnpm install`（会留下 root 拥有的 node_modules，下次用户又装不进去）
- ❌ 不要用 sudo/管理员权限跑后端 launcher 启动的任何命令
- ❌ 不要修改系统目录的权限（`chmod -R /usr/local/lib`、`icacls "C:\Program Files\nodejs"`）——会把系统权限搞乱

平台相关的具体命令：见 `references/macos.md` 和 `references/windows.md` 的 **M 节**。

---

## 统一修复执行框架

无论命中哪个模式，Agent 必须按下面的步骤框架执行，确保动作可追踪：

### 步骤 1：识别操作系统

按"平台识别（必读）"节确定当前是 macOS 还是 Windows，**读对应的平台参考文件**准备好命令模板。

### 步骤 2：捕获原始错误

读 stderr/stdout 全文（不要被截断），用 `read_file` 读后端记录的 `install.stderr.log` / `install.stdout.log`（在工作区 `.devagentstudio/runtime/launch/` 下）。

### 步骤 3：归类失败模式

按"识别信号"列里的关键字逐条比对 stderr/stdout，**取第一个命中的模式**作为本次诊断结果。多模式同时命中时，按"先环境后网络"的顺序优先处理环境（A > B > C > D > ...）。

### 步骤 4：执行修复动作

按对应模式的"自动修复序列"+ **平台参考文件的具体命令**逐条执行 `execute` / `write_file` / `delete_file` / `read_file`。每执行完一条都要检查 exit_code——非零就停下来报告失败，**不要**继续盲目跑下一条。

### 步骤 5：重试验证

修复动作执行完后，**必须**重新执行一次 `pnpm install` 验证修复有效（命令按平台参考）。

- 成功 → 修复闭环，写报告
- 仍失败 → 把新的 stderr 抓回来，**回到步骤 3 重新归类**（可能是另一个模式），新一轮修复

### 步骤 6：写报告

修复报告**必须**用 Markdown 列表呈现给用户：

```markdown
## 依赖安装失败诊断与修复报告

**当前操作系统**：<macOS / Windows>
**失败模式**：<模式字母 + 名称，例如 "D. 网络/镜像源不通">
**识别信号**：<stderr/stdout 中实际命中的关键字，原样摘录 1-2 行>
**根因分析**：<一句话说明为什么会失败>

**已执行的修复动作**：
- <动作 1，例如 "已用 corepack 激活 pnpm@11.9.0">
- <动作 2，例如 "已写入 frontend/.npmrc，切换 registry 到 <注入值>">
- <动作 3，例如 "已重新执行 pnpm install，验证通过">

**修复结果**：<成功 / 部分成功（哪步失败）/ 未执行修复（原因）>

**用户下一步操作**（如果修复未能完全闭环）：
- <用户需要在自己终端做的事>
```

---

## 与其他 Skill 的关系

- **`frontend-template-modification-boundary`**：本 skill 的修复动作**不允许**修改 `frontend/package.json` 的依赖声明、`pnpm-lock.yaml`、`vite.config.ts` 等模板管理文件；只允许新增/修改 `frontend/.npmrc`，以及删除 `frontend/node_modules/`。
- **`frontend-code-scan`**：那个 skill 负责发现 npm 包的版本安全风险（不修复），本 skill 负责安装失败时的环境/配置修复。两者可以串联：先跑本 skill 让依赖装上，再跑 code-scan 检查版本风险。
- **`react-develop-specification`**：本 skill 不管业务代码，React 编码规范由那个 skill 约束。

---

## 禁止行为清单

- ❌ 不要在 `/frontend/` 根目录创建任何新文件，**除了** `.npmrc`（本 skill 唯一允许的新增）
- ❌ 不要修改 `package.json`、`pnpm-lock.yaml`、`vite.config.ts`、`tsconfig.json`、模板脚本
- ❌ 不要 `sudo` 任何命令；不要用管理员 PowerShell 跑任何命令
- ❌ 不要全局安装 pnpm/Node 到系统目录（用 corepack/版本管理器装到用户目录）
- ❌ 不要在没读 stderr 的情况下凭猜测执行修复（每次必须先归类失败模式）
- ❌ 不要在执行修复动作后不验证（必须重试 `pnpm install` 确认结果）
- ❌ 不要删除 `/frontend/` 之外的用户文件（`~/.npmrc`、`~/.pnpm-store/`、其他应用的 `node_modules/` 都属于用户私有空间，只能给指引不能动手）
- ❌ 不要把"在终端跑某命令"作为修复主体——Agent 自己能做的就自己做，只有涉及系统级安装 / 用户私有目录时才让用户跑
- ❌ 不要在没确认 PeerDep 冲突根因（模板自带 vs 用户改动）的情况下动 `package.json` 加 `overrides`
- ❌ 不要在没识别操作系统的情况下盲跑命令——`rm -rf` 在 Windows CMD 不存在，`rmdir /s /q` 在 macOS 报错，套错平台命令等于把修复搞砸
