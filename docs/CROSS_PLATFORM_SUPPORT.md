# Windows and macOS Support

## Supported targets

- Windows: x64, packaged with the backend staged under `Frontend/resources/backend/win32`.
- macOS Intel: x64, packaged with the backend staged under `Frontend/resources/backend/darwin-x64`.
- macOS Apple Silicon: arm64, packaged with the backend staged under `Frontend/resources/backend/darwin-arm64`.

PyInstaller and macOS code signing must run on macOS. Build the x64 and arm64 packages on matching
macOS hosts; do not reuse one architecture's frozen backend for the other architecture.

## Development

- Windows backend: `powershell -ExecutionPolicy Bypass -File scripts/start-backend.ps1`
- macOS backend: `bash scripts/start-backend.sh`
- Frontend on either platform: run `pnpm dev` from `Frontend`.

## Packaging

1. On Windows x64, run `scripts/build-backend-win.ps1`, then `pnpm build:win:<environment>`.
2. On an Intel Mac, run `bash scripts/build-backend-mac.sh x64`, then
   `pnpm build:mac:x64:<environment>`.
3. On an Apple Silicon Mac, run `bash scripts/build-backend-mac.sh arm64`, then
   `pnpm build:mac:arm64:<environment>`.

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
