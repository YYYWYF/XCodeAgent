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

- Windows backend: `powershell -ExecutionPolicy Bypass -File scripts/start-backend.ps1 -Env st`
- macOS backend: `bash scripts/start-backend.sh --env st`
- Frontend on either platform: run `pnpm dev:st` from `Frontend`.

Use matching `dev`, `st`, `uat` or `prd` values on both sides. Local startup defaults to `dev`
when neither the script argument nor process `APP_ENV` is supplied. Backend files are `.env`
for dev and `.env.st`, `.env.uat`, `.env.prd` for the other environments. Copy the corresponding
`.example` file and supply actual company configuration; populated files are ignored by Git.
Only the selected file is loaded. Missing files and invalid environments fail without falling back
to dev. File-defined business values override process variables; undeclared values retain the existing
process/default rules. Startup controls (environment, file path, host/port, working directory and
`PYTHONUTF8`) cannot be overridden by the file. Environment selection also fixes the user data
directory to `.devagentstudio_dev`, `.devagentstudio_st`, `.devagentstudio_uat` or `.devagentstudio`.
Electron checks `/health.app_env` before proceeding, including source runs; a mismatch is fatal.
See `Backend/README.md` for direct Uvicorn and PowerShell examples.

## Packaging

From the repository root, use the matching one-command package entry with an explicit environment:

- Windows x64: `powershell -ExecutionPolicy Bypass -File scripts/win_pack.ps1 -Env st`
- macOS Intel: `bash scripts/mac_pack_x64.sh --env prd`
- macOS Apple Silicon: `bash scripts/mac_pack_arm64.sh --env st`

All package entries, including standalone backend builders, require an explicit `dev`, `st`, `uat`
or `prd` argument even when the shell has `APP_ENV`. The backend builders accept
`bash scripts/build-backend-mac.sh arm64 --env st` or
`powershell -ExecutionPolicy Bypass -File scripts/build-backend-win.ps1 -Env st`.
Only the selected environment file is staged, keeping its filename. Frontend package commands such as
`pnpm build:win:st`, `pnpm build:mac:arm64:st` and `pnpm build:unpack:st` verify that the selected file
exists and reject any additional environment files before building. Generic `build:mac` and
`build:unpack` require choosing an environment-specific command. Existing artifact names and
update publishing rules are unchanged; use separate company release destinations for each environment.

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

## Desktop startup updates

The Electron main process shows the update window, waits for the existing Python backend startup
promise, then uses `@ag-ui/client` to call `/app-update/run` at the actual Python backend address
(including the dynamically assigned port in packaged apps). It submits only the running version
from `app.getVersion()` in `forwardedProps.appUpdate: { action: "check", currentVersion }`.
Python reads its existing `Settings.template_engine_base_url` and calls Java
`{base}/v1/app-updates/check`. Success and failure both use the standard AG-UI lifecycle with
custom event `app-update` and snapshot key `appUpdate`. No model or workspace workflow is started.

`DEVAGENTSTUDIO_TEMPLATE_ENGINE_BASE_URL` in the Python backend `.env` is the sole Java address
configuration, shared with Bootstrap and template updates. Packaged apps load the selected
`resources/backend/.env[.st|.uat|.prd]` through `DEVAGENTSTUDIO_BACKEND_ENV_FILE`;
Electron passes its compiled `APP_ENV` to Python and verifies the returned health identity.
Restart the backend/app after changing the setting; rebuilding
the frontend is unnecessary. Electron never reads or exposes the `.env` contents and has no separate
Java update URL or build-time Java address injection.

The Java response supplies the CDN URL for the target version directory. The client reads
`latest.yml` on Windows or `latest-mac.yml` on macOS directly from that directory.
The manifest version must exactly match `latestAppVersion`. A target can be higher or lower
than the current version. The user confirms the switch after the download completes.
Keep generated SHA-512, size and filenames intact. Windows uses NSIS EXE; macOS auto-update uses
ZIP while DMG remains available for initial installation. The ZIP filenames must distinguish
`arm64` and `x64`; when publishing both architectures, combine their `files` entries into the same
version's `latest-mac.yml`, preserve each artifact's hash/size, and do not overwrite it with the
other architecture's manifest. Keep the existing artifact names in the manifest.

After the Windows signing step replaces the EXE and refreshes `dist/latest.yml` with the
signed file's size and SHA-512, run `node scripts/stage-windows-update.mjs Frontend/dist`
from the repository root. The script uses the manifest version, verifies the signed file against
the manifest, and moves all contents of `Frontend/dist` into `Frontend/dist/<version>/`.
Run this step after packaging has removed temporary build outputs. Use `./Frontend/dist` as
the pipeline's upload root and preserve the version directory in the CDN path.

Local test setup (three terminals):

1. Serve a local release root on `127.0.0.1:18081` with the update manifest and its referenced
   files in a `1.0.1/` subdirectory. For example, run
   `python3 -m http.server 18081 --bind 127.0.0.1 --directory /absolute/path/to/release-root`
   with a root containing only release files.
2. Start the Java JAR from `springboot-template` with its `application-local.yml` and an absolute
   `TEMPLATE_ENGINE_SOURCE_ROOT`. Local release settings are `latestAppVersion: "1.0.1"` and
   `appDownloadUrl: "http://127.0.0.1:18081/"`. HTTP and HTTPS base URLs are supported.
3. Configure the existing `DEVAGENTSTUDIO_TEMPLATE_ENGINE_BASE_URL` in `Backend/.env` to point to
   that Java service, start the Python development backend with the command above, then run
   `pnpm dev` in `Frontend`. For an isolated test, override that same variable only in the Python
   process environment; no Java address is passed to Electron.
   Source runs check/download by default, but offer **继续开发 / 退出** after downloading and never install.

The application shows a 480×300 update window, then login if no update is available. Python startup
uses the existing readiness budget and is not counted in the 5-second version check. A Python
startup failure retains the original fatal startup handling; a Java/AG-UI check failure opens login.
Manifest checks time out after 10 seconds. Downloads are cancelled after 30
seconds without new bytes or 15 minutes total. Errors show briefly, then enter normal startup.
Packaged applications offer **立即安装 / 退出**. Installation first completes existing shutdown
cleanup, then hands control to the updater. **退出** quits without installing; subsequent launches
check Java again before reusing a verified updater cache. A second instance focuses the update
window instead of bypassing it.

The bundled backend belongs to the current Electron process. Normal client quit stops its process
tree, including in-progress work, rather than leaving tasks running in the background. A subsequent
launch creates a new backend and reads existing workflow checkpoints through the explicit recovery
flows. There is no persisted backend registry, cross-launch instance adoption, client lease or
`/backend-runtime/run` endpoint. Windows restores `detached: false`; POSIX retains a separate process
group for shutdown. The backend's stdout/stderr are piped to Electron for startup diagnostics.
Code-graph native grammar probes use a dedicated frozen entry mode,
`devagentstudio-backend.exe --devagentstudio-parser-probe <grammar>`. The probe loads only the
requested Tree-sitter grammar and exits before backend configuration or Uvicorn startup. On Windows
it uses `CREATE_NO_WINDOW`; the upstream timeout, per-language cache and failure handling remain
in place. A frozen executable cannot interpret the upstream Python `-I -c` invocation. Source
development keeps the upstream interpreter probe. Rebuild the backend and Electron installer after
changing this adapter; verify multi-language scanning without console flashes or extra HTTP servers
on Windows. Other nonempty backend entry arguments are rejected before service startup.

The shared source-development backend remains under the developer's control and is not stopped
when Electron exits.

On Windows, **立即安装** runs NSIS silently and explicitly targets the directory containing the
running EXE, including custom paths with spaces or non-ASCII characters. The installer restarts
the application after updating; it does not show an installation-directory selection page.
Manual installation keeps the existing installer UI settings. Windows may still request UAC
approval when the existing installation requires administrator privileges. This behavior is
controlled by the installed application's updater code: install a baseline containing this change
before testing its next automatic update; an older running version retains its previous behavior.

Run `pnpm test:app-update`, `pnpm test:auth`, `pnpm lint`, `pnpm typecheck`, and `pnpm build`.
From `Backend`, also run `.venv/bin/python -m unittest tests.test_app_update` and check `/health`.
Verify the UI in Electron, including light/dark themes, download progress, fail-open, and exit
without installation. Complete installation tests require two packaged versions containing the
update implementation; macOS requires valid signing (ad-hoc local packages only validate download
and UI). Validate signed Intel/Apple Silicon packages and Windows x64 NSIS on their native hosts.

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
