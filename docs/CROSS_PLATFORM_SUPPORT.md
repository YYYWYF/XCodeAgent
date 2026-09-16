# Windows and macOS Support

## Supported targets

- Windows: x64, packaged with the backend staged under `Frontend/resources/backend/win32`.
- macOS Intel: x64, packaged with the backend staged under `Frontend/resources/backend/darwin-x64`.
- macOS Apple Silicon: arm64, packaged with the backend staged under `Frontend/resources/backend/darwin-arm64`.

PyInstaller and macOS code signing must run on macOS. Build the x64 and arm64 packages on matching
macOS hosts; do not reuse one architecture's frozen backend for the other architecture.
Backend packaging supports Python 3.12 and 3.14. Frontend packaging supports the Node 20.19.0
development baseline and Node 24.x; use the pnpm version declared in `Frontend/package.json`
with the checked-in lockfile.

## Development

- Windows backend: `powershell -ExecutionPolicy Bypass -File scripts/start-backend.ps1`
- macOS backend: `bash scripts/start-backend.sh`
- Frontend on either platform: run `pnpm dev` from `Frontend`.

## Packaging

From the repository root, use the matching one-command development package entry:

- Windows x64: `powershell -ExecutionPolicy Bypass -File scripts/win_pack.ps1`
- macOS Intel: `bash scripts/mac_pack_x64.sh`
- macOS Apple Silicon: `bash scripts/mac_pack_arm64.sh`

Each entry builds and stages the backend first, then builds the matching Electron package in
`Frontend/dist`. Run the macOS entry only on a host with the matching architecture. Install the
frontend dependencies beforehand; the entries call `pnpm` directly and do not reinstall
`node_modules`. Install the pnpm version declared by `Frontend/package.json` before a clean
`pnpm install --frozen-lockfile` so it matches the checked-in lockfile.

The default package includes all Tree-sitter language bindings. To build a smaller package, append
`--slim` to either macOS entry or `-Slim` to the Windows entry. The slim profile retains grammars
used by the code graph's built-in extension mapping and the backend's Java/TypeScript/TSX AST
validators. Custom workspace grammars outside that set are not available in slim packages.

The macOS backend script uses `Backend/.venv/bin/python` when present, or a system Python 3.14/3.12.
Set `PYTHON=/absolute/path/to/python` to select a different supported interpreter. The Windows
script defaults to `py -3.12`; pass `-Python C:\path\to\python.exe` to build with Python 3.14.
Pass that option to `scripts/win_pack.ps1` when using the one-command Windows entry.
For a clean frontend install, run `pnpm install --frozen-lockfile` using the declared pnpm version.

Production macOS commands enable electron-builder notarization. Supply signing and notarization
credentials through CI secrets such as `CSC_LINK`, `CSC_KEY_PASSWORD`, and the supported Apple
notarization variables. Never commit those values or a populated backend `.env`.

The Windows development package disables executable resource editing/signing so it can be built
without Windows symbolic-link privileges or a certificate. Staging and production packages retain
the normal electron-builder signing path and should receive Windows signing credentials in CI.

## Agent architecture mapping

- learn-coding-agent: retain the compact gather, act, verify loop and structured argv-first terminal
  contract; platform command parsing is only a compatibility adapter.
- OpenCode: keep process execution behind an auditable tool boundary, normalize executable identity
  before risk classification, and terminate owned process trees rather than unrelated host processes.
- Deep Agents: keep virtual filesystem permission rules as the primary write boundary. POSIX mode bits
  are defense in depth and are not treated as the Windows security boundary.

The implementation remains intentionally local to platform adapters, packaging, and process
lifecycle code. It does not change AG-UI endpoints, workflow event contracts, storage formats, or
the 128k context-budget design.
