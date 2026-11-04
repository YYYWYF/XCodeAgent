# macOS 平台修复命令参考

本文件只在 Agent 确认当前工作区宿主是 **macOS**（`uname -s` 输出 `Darwin`，含 Intel 与 Apple Silicon）时使用。所有命令默认在子进程 bash/sh 里跑；`execute` 工具自带 `shell=True`，按 POSIX 语法写。

## 环境诊断（共用）

```bash
# 一次性收集 Node/pnpm/版本管理器信息（每项失败都不阻塞下一项）
node --version 2>&1 || echo "node: NOT_FOUND"
pnpm --version 2>&1 || echo "pnpm: NOT_FOUND"
corepack --version 2>&1 || echo "corepack: NOT_FOUND"
command -v nvm >/dev/null && echo "nvm: available" || ([ -s "$HOME/.nvm/nvm.sh" ] && echo "nvm: installed_not_loaded" || echo "nvm: NOT_FOUND")
command -v fnm >/dev/null && echo "fnm: available" || echo "fnm: NOT_FOUND"
uname -m   # arm64 = Apple Silicon；x86_64 = Intel
```

把工作区路径分隔符、当前用户家目录（`echo $HOME`）一并收集，便于后续拼路径。

## A. Node 版本不符

### A.1 检查并加载已有版本管理器

```bash
# nvm 已装但未加载时，先 source
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"

# 列出本机已装的 Node 版本
nvm ls 2>/dev/null || fnm ls 2>/dev/null || ls "$HOME/.nvm/versions/node/" 2>/dev/null
```

如果发现已装版本里有符合 `engines.node` 范围的（例如 `v20.19.0`），直接用：

```bash
source "$HOME/.nvm/nvm.sh" && nvm use 20.19.0 && node --version
```

### A.2 没装符合版本时：用 nvm 装

```bash
# 确保 nvm 已加载
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"

# 装并切换
nvm install 20.19.0
nvm use 20.19.0
node --version
```

### A.3 用户系统连 nvm 都没装：先按生成应用的运行环境选装法

**判断生成应用要在哪种环境跑**——看后端注入的 `failureEvidence.deployment.npm_registry`：
- 含 `central.jaf.cmbchina.cn` → **公司内部**：跳到下面的"公司内部装法"
- 否则 → **公司外部**：走下面的"公司外部装法"

#### 公司外部装法（从 GitHub 装 nvm，不需要 sudo）

```bash
# 下载并安装 nvm 到 ~/.nvm/
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash

# 在当前子进程立即启用 nvm
export NVM_DIR="$HOME/.nvm"
source "$NVM_DIR/nvm.sh"

# 装目标 Node 并切换
nvm install 20.19.0
nvm use 20.19.0

# 激活模板要求的 pnpm
corepack enable
corepack prepare pnpm@11.9.0 --activate

# 验证
node --version
pnpm --version
```

**注意**：curl 从 raw.githubusercontent.com 下载。如果失败（外网受限/网络抖动），先按模式 D 配镜像源再回来重试。

#### 公司内部装法（招行桌面助手，不跑 curl）

公司内部**禁止**从 GitHub 下载，**必须**走招行桌面助手：

1. **不要**跑 `curl ... | bash`——公司网络到不了 raw.githubusercontent.com，且违反公司软件分发规定。
2. 把下面指引写进修复报告的"用户下一步操作"，让用户在自己机器上装：
   ```
   1. 打开「招行桌面助手」
   2. 进入「软件管理」
   3. 搜索「NVM」
   4. 点安装（macOS 版本）
   5. 安装完成后回到当前工作台，点「重试诊断并修复」
   ```
3. 用户装完桌面助手版的 nvm 后，nvm 通常落在 `~/.nvm/`（与官方一致）。Agent 在用户重试时再次执行本节开头的"加载 nvm"命令验证：
   ```bash
   export NVM_DIR="$HOME/.nvm"
   [ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"
   nvm --version
   ```
   能跑通就继续走 A.4 装目标 Node。

### A.4 重试安装

```bash
# 同一个 shell 上下文（nvm 已 source、Node 已切换）
cd frontend && pnpm install
```

## B. pnpm 命令缺失

### B.1 优先用 corepack

```bash
corepack enable
corepack prepare pnpm@11.9.0 --activate
pnpm --version
```

### B.2 corepack 不可用时（Node <16.13）：用 npm 装到用户目录

```bash
# 改 npm 全局前缀到用户目录（避免 /usr/local 权限问题）
mkdir -p "$HOME/.npm-global"
npm config set prefix "$HOME/.npm-global"

# 把新 prefix 加入 PATH（当前子进程生效）
export PATH="$HOME/.npm-global/bin:$PATH"

# 装 pnpm
npm install -g pnpm@11.9.0
pnpm --version
```

**注意**：`export PATH` 只在当前子进程有效；如果 `pnpm install` 在同一个 `execute` 调用里跑就直接连着跑：

```bash
export PATH="$HOME/.npm-global/bin:$PATH" && cd frontend && pnpm install
```

## C. pnpm 版本与 lockfile 不兼容

```bash
corepack enable
corepack prepare pnpm@11.9.0 --activate
pnpm --version   # 验证切过来了
cd frontend && pnpm install
```

## D. 网络/镜像源不通

### D.1 诊断当前 registry

```bash
cd frontend && pnpm config get registry
```

### D.2 写入镜像源

`.npmrc` 被 `SENSITIVE_FILE_NAMES` 拦截，**不能用 `write_file`/`delete_file` 工具**——必须用 `execute` 跑 shell 命令：

```bash
# 文件不存在 → 新建；已存在 → 追加一行
if [ ! -f frontend/.npmrc ]; then
  echo "registry=<注入值>" > frontend/.npmrc
else
  echo "registry=<注入值>" >> frontend/.npmrc
fi
cat frontend/.npmrc   # 验证
```

`<注入值>` 来自后端 `Settings.npm_registry`（环境变量 `DEVAGENTSTUDIO_NPM_REGISTRY`）：
- 公司外部（默认）：`https://registry.npmmirror.com`
- 公司内部（招商银行）：`http://central.jaf.cmbchina.cn/artifactory/api/npm/group-npm/`

### D.3 重试

```bash
cd frontend && pnpm install
```

## F. 原生模块编译失败

### F.1 先试预编译镜像（自动）

用 `execute` 跑 shell 追加到 `/frontend/.npmrc`（`<MIRROR>` 是后端注入值，默认 `https://registry.npmmirror.com`）：

```bash
cat >> frontend/.npmrc <<'EOF'
prefer-offline=false
node-linker=hoisted
sharp_libvips_binary_host=<MIRROR>/mirrors/sharp-libvips/
node_sass_binary_site=<MIRROR>/mirrors/node-sass/
EOF
cat frontend/.npmrc   # 验证
```

然后删除 node_modules 并重试：

```bash
rm -rf frontend/node_modules
cd frontend && pnpm install
```

### F.2 仍失败：装 Xcode Command Line Tools（**让用户在终端跑**）

```bash
xcode-select --install
```

这会弹系统对话框，**需要用户点同意**。Agent 不要自动跑（跑了用户也得点弹窗）；写到报告的"用户下一步操作"里。

## G. GitHub 二进制下载失败

用 `execute` 跑 shell **追加**到 `/frontend/.npmrc` 末尾（`<MIRROR>` 是后端注入值，默认 `https://registry.npmmirror.com`）：

```bash
cat >> frontend/.npmrc <<'EOF'
electron_mirror=<MIRROR>/mirrors/electron/
electron_builder_binaries_mirror=<MIRROR>/mirrors/electron-builder-binaries/
node_sass_binary_site=<MIRROR>/mirrors/node-sass/
puppeteer_download_base_url=<MIRROR>/mirrors/chromium-browser-snapshots/
sharp_libvips_binary_host=<MIRROR>/mirrors/sharp-libvips/
playwright_download_host=<MIRROR>/mirrors/playwright/
EOF
cat frontend/.npmrc   # 验证
```

如果注入值是公司内部的 artifactory（形如 `http://central.jaf.cmbchina.cn/.../api/npm/group-npm/`），它**不提供** `/mirrors/...` GitHub 二进制转发——跳过本节，把"当前镜像源不代理 GitHub 二进制"写入报告让用户找运维解决。

然后：

```bash
cd frontend && pnpm install
```

## H. node_modules 损坏

```bash
# 删除损坏的 node_modules
rm -rf frontend/node_modules

# 重试安装
cd frontend && pnpm install
```

## I. pnpm store 损坏

```bash
pnpm store prune
rm -rf frontend/node_modules
cd frontend && pnpm install
```

仍失败时让用户在终端跑（**不要替用户跑**）：

```bash
pnpm store path         # 找到 store 位置（通常 ~/Library/pnpm/store）
rm -rf "$(pnpm store path)"
cd frontend && pnpm install
```

## K. 安装超时

```bash
# 切换到镜像源再重试（沿用 D 节）
cd frontend && pnpm install
```

后端会把 `INSTALL_TIMEOUT_SECONDS` 从 120 提升到 600；这是后端代码改动，不在本 skill 范围。

## L. 磁盘空间不足

```bash
df -h /
df -h "$HOME"
```

把可用空间写进报告，让用户清理：

```bash
# 推荐先跑这条（安全）
pnpm store prune
```

## M. 权限不足

### M.1 诊断

```bash
which node
which pnpm
ls -la "$(dirname "$(which node)")/.." 2>/dev/null
echo "$HOME"
```

如果 `which node` 输出 `/usr/local/bin/node` 或 `/usr/bin/node`，就是装到系统目录了。

### M.2 用 nvm 在用户目录装 Node（**不需要 sudo**）

**判断生成应用要在哪种环境跑**——看后端注入的 `failureEvidence.deployment.npm_registry`：

- **公司外部**（不含 `central.jaf.cmbchina.cn`）：从 GitHub 装 nvm

  ```bash
  # 装 nvm 到 ~/.nvm/
  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash

  # 启用并装 Node
  export NVM_DIR="$HOME/.nvm"
  source "$NVM_DIR/nvm.sh"
  nvm install 20.19.0
  nvm use 20.19.0

  # 激活 pnpm
  corepack enable
  corepack prepare pnpm@11.9.0 --activate

  # 重试安装
  cd frontend && pnpm install
  ```

- **公司内部**（含 `central.jaf.cmbchina.cn`）：从招行桌面助手装 nvm

  公司内**禁止**从 GitHub 下载。把下面指引写进报告，让用户在自己机器上装：

  ```
  1. 打开「招行桌面助手」
  2. 进入「软件管理」
  3. 搜索「NVM」
  4. 点安装（macOS 版本）
  5. 安装完成后回到当前工作台，点「重试诊断并修复」
  ```

  用户装完后，Agent 在重试时验证 nvm 可用：

  ```bash
  export NVM_DIR="$HOME/.nvm"
  [ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"
  nvm --version
  nvm install 20.19.0
  nvm use 20.19.0
  corepack enable
  corepack prepare pnpm@11.9.0 --activate
  cd frontend && pnpm install
  ```

**绝对不要**：
- ❌ `sudo pnpm install`
- ❌ `sudo chmod -R ...` 改 `/usr/local/lib` 权限
- ❌ 公司内部时不要尝试 `curl https://raw.githubusercontent.com/... | bash`——网络到不了，且违反公司软件分发规定
