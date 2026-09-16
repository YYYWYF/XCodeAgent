# XCodeAgent 桌面端打包

本项目由 Electron 前端和 Python 后端组成。下面的脚本会先用 PyInstaller 构建并暂存后端，再生成对应平台的 Electron 开发安装包。所有命令均从**项目根目录**执行；Windows 和 macOS 需要分别在对应系统上打包，Mac 的 Intel 与 Apple Silicon 也必须分别在同架构机器上打包。

## 打包前准备

1. 安装 Node.js 20.19.0 或 24.x，以及 `pnpm`。项目的 `Frontend/package.json` 声明 `pnpm@9.15.9`；首次安装前端依赖时请使用该版本，以匹配 `Frontend/pnpm-lock.yaml`。
2. 安装 Python 3.12 或 3.14，确保所用解释器有 `pip`。macOS 脚本优先使用 `Backend/.venv/bin/python`；Windows 脚本默认使用 `py -3.12`，也可以通过 `-Python` 指定 Python 可执行文件。
3. 准备本机的 `Backend/.env`。后端打包脚本会检查它，并将其复制进安装包；不要提交或对外分发含真实密钥的安装包。
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

## macOS 打包

Apple Silicon（`uname -m` 输出 `arm64`）：

```bash
bash scripts/mac_pack_arm64.sh
```

Intel Mac（`uname -m` 输出 `x86_64`）：

```bash
bash scripts/mac_pack_x64.sh
```

脚本会校验当前机器的架构，不支持在 arm64 Mac 上直接打 x64 包，或反过来打包。如需指定 Python，可在命令前设置 `PYTHON`，例如：

```bash
PYTHON=/absolute/path/to/python bash scripts/mac_pack_arm64.sh
```

成功后，DMG、ZIP 和解包的 `.app` 均位于 `Frontend/dist/`。请以本次运行新生成的文件为准；目录中可能还保留着之前的旧产物。

## Windows x64 打包

在 Windows PowerShell 中，从项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\win_pack.ps1
```

如需使用 Python 3.14 或指定虚拟环境中的 Python：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\win_pack.ps1 -Python 'C:\path\to\python.exe'
```

成功后，Windows 安装程序（`*-setup.exe`）及解包目录位于 `Frontend/dist/`。

## 注意事项

- 这三个一键脚本构建的是 `dev` 包。macOS 没有可用签名证书时会生成未签名的包；对外发布前还需要完成签名、公证等发布配置。
- `Backend/.env` 会随后端一起进入安装包。不要把包含真实密钥的产物上传、公开分享或提交到 Git。
- 如果只需分别执行两步，可使用 `scripts/build-backend-mac.sh` / `scripts/build-backend-win.ps1` 和 `Frontend/package.json` 中对应的 `build:mac:<arch>:dev` / `build:win:dev` 命令。平台与架构必须保持一致。

更多跨平台说明见 [docs/CROSS_PLATFORM_SUPPORT.md](docs/CROSS_PLATFORM_SUPPORT.md)。
