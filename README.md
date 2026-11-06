# DevAgent Studio 桌面端打包

本项目由 Electron 前端和 Python 后端组成。下面的脚本会先用 PyInstaller 构建并暂存后端，再生成所选环境、对应平台的 Electron 安装包。所有命令均从**项目根目录**执行；Windows 和 macOS 需要分别在对应系统上打包，Mac 的 Intel 与 Apple Silicon 也必须分别在同架构机器上打包。

## 打包前准备

1. 安装 Node.js 20.19.0 或 24.x，以及 `pnpm`。项目的 `Frontend/package.json` 声明 `pnpm@9.15.9`；首次安装前端依赖时请使用该版本，以匹配 `Frontend/pnpm-lock.yaml`。
2. 安装 Python 3.12 或 3.14，确保所用解释器有 `pip`。macOS 脚本优先使用 `Backend/.venv/bin/python`；Windows 脚本默认使用 `py -3.12`，也可以通过 `-Python` 指定 Python 可执行文件。
3. 准备目标环境的后端配置文件，填写公司实际服务地址、模型及凭据。后端打包脚本只复制所选环境的文件，并保留其文件名；真实配置不要提交到 Git。
4. 安装前端依赖。以下命令只需在首次打包或依赖变化后执行；一键打包脚本不会重装 `node_modules`。

macOS 终端：

```bash
cd Frontend
pnpm install --frozen-lockfile
cd ..
```

Windows PowerShell：

```powershell
Set-Location Frontend
pnpm install --frozen-lockfile
Set-Location ..
```

## 环境选择

打包时**必须显式指定环境**，不指定会报错，也不会使用终端已有的 `APP_ENV` 自动选择环境。
macOS 使用 `--env`，Windows 使用 `-Env`，均支持以下四种取值：

| 环境参数 | 后端配置文件 | 脱敏模板 |
| --- | --- | --- |
| `dev` | `Backend/.env` | `Backend/.env.example` |
| `st` | `Backend/.env.st` | `Backend/.env.st.example` |
| `uat` | `Backend/.env.uat` | `Backend/.env.uat.example` |
| `prd` | `Backend/.env.prd` | `Backend/.env.prd.example` |

例如，打 st 包前先复制 `Backend/.env.st.example` 为 `Backend/.env.st` 并填写实际配置。
每个安装包只包含对应环境的一份配置；缺少目标文件时直接报错，不回退读取 dev 配置。

## macOS 打包

Apple Silicon（`uname -m` 输出 `arm64`）：

```bash
bash scripts/mac_pack_arm64.sh --env st
bash scripts/mac_pack_arm64.sh --env prd
```

Intel Mac（`uname -m` 输出 `x86_64`）：

```bash
bash scripts/mac_pack_x64.sh --env st
bash scripts/mac_pack_x64.sh --env prd
```

默认打完整语法包。如需精简包，在对应命令后加 `--slim`：

```bash
bash scripts/mac_pack_arm64.sh --env st --slim  # Apple Silicon
bash scripts/mac_pack_x64.sh --env st --slim    # Intel Mac
```

脚本会校验当前机器的架构，不支持在 arm64 Mac 上直接打 x64 包，或反过来打包。如需指定 Python，可在命令前设置 `PYTHON`，例如：

```bash
PYTHON=/absolute/path/to/python bash scripts/mac_pack_arm64.sh --env st
```

成功后，DMG、ZIP 和解包的 `.app` 均位于 `Frontend/dist/`。请以本次运行新生成的文件为准；目录中可能还保留着之前的旧产物。

正式包只收录编译后的前端文件、应用元数据和图标，不重复打入前端 `node_modules`；后端仍作为独立资源随包提供。macOS 和 Windows 打包入口均使用这条规则。

## Windows x64 打包

在 Windows PowerShell 中，从项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\win_pack.ps1 -Env st
powershell -ExecutionPolicy Bypass -File .\scripts\win_pack.ps1 -Env prd
```

如需使用 Python 3.14 或指定虚拟环境中的 Python：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\win_pack.ps1 -Env st -Python 'C:\path\to\python.exe'
```

精简包加 `-Slim`，也可与 `-Python` 一起使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\win_pack.ps1 -Env st -Slim
```

成功后，Windows 安装程序（`*-setup.exe`）及解包目录位于 `Frontend/dist/`。

## 注意事项

- 上述示例中的 `st` / `prd` 可替换为 `dev` / `uat`。脚本会将同一环境传递给后端资源准备和 Electron 构建；安装后 EXE 或应用根据构建时固定的环境读取对应配置。
- macOS 的 `prd` 命令启用公证，需要提供相应签名和公证凭据；其他环境没有可用签名证书时可生成未签名包。对外发布前需准备项目要求的发布配置。
- 精简包只内置代码图默认语言及后端 AST 校验需要的语法；工作区通过 `.code-review-graph/languages.toml` 指定的其他 Tree-sitter 语法将不可用。需要完整自定义语言支持时，不加 `--slim` / `-Slim`。
- 所选环境配置会随后端一起进入安装包。不要把包含真实密钥的产物上传、公开分享或提交到 Git。
- 如果只需分别执行两步，先准备后端资源，再在 `Frontend` 目录运行相同环境的打包命令。例如 macOS Apple Silicon：`bash scripts/build-backend-mac.sh arm64 --env st`，然后 `pnpm build:mac:arm64:st`；Windows：`powershell -ExecutionPolicy Bypass -File scripts/build-backend-win.ps1 -Env st`，然后 `pnpm build:win:st`。独立后端打包同样必须指定环境，前后两步的平台、架构和环境必须一致。

更多跨平台说明见 [docs/CROSS_PLATFORM_SUPPORT.md](docs/CROSS_PLATFORM_SUPPORT.md)。
