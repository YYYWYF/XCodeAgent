import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createInterface } from 'node:readline';

// 此进程仅包装公开 SDK；stdout 只输出本机控制回执，不输出 endpoint/token/SDK 异常。
let manager;
let closing;
let monitor;
let booted = false;
const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
process.stdout.on('error', () => {});

/** 写入有界且不含凭据的控制帧，并等待管道接收。 */
function emit(frame) {
  return new Promise(resolve => process.stdout.write(`${JSON.stringify(frame)}\n`, resolve));
}

/** 先等待公开 SDK dispose，再发清理回执；失败不声明已停止。 */
function close(id = 0, exitCode = 0) {
  if (closing) return closing;
  closing = (async () => {
    clearInterval(monitor);
    try {
      await manager?.dispose();
      await emit({ type: 'stopped', id });
    } catch {
      await emit({ type: 'failed', id, code: 'stop_unconfirmed' });
      exitCode = 1;
    }
    process.exit(exitCode);
  })();
  return closing;
}

/** 复核公开 metadata 摘要后，从当前工具安装目录正常解析公开 /process。 */
async function start(input) {
  try {
    const require = createRequire(pathToFileURL(path.join(process.cwd(), 'package.json')));
    const metadata = await readFile(require.resolve('@za38.03/agent-ui-creator/package.json'));
    const digest = createHash('sha256').update(metadata).digest('hex');
    if (digest !== input.packageJsonSha256 || process.versions.node !== input.nodeVersion
      || process.versions.electron || !path.isAbsolute(input.workspace)) {
      throw new Error('identity mismatch');
    }
    const sdk = await import('@za38.03/agent-ui-creator/process');
    if (closing) return;
    manager = new sdk.CreatorProcessManager({
      projectRoot: input.workspace, allowExternalEndpoint: false,
      environment: { ...process.env, CREATOR_HOT_RELOAD: 'false' },
      startupTimeoutMs: input.startupTimeoutMs, stopTimeoutMs: 500,
    });
    // 凭据只保留在 SDK 内存；不得把 ensureStarted 的 endpoint 返回给 Python 或 renderer。
    await manager.ensureStarted();
    if (closing) return;
    if (!Number.isInteger(manager.processId) || manager.processId <= 0) throw new Error('missing process');
    booted = true;
    await emit({ type: 'ready', id: 0, pid: manager.processId });
    monitor = setInterval(() => {
      if (!closing && !manager.processId) {
        void emit({ type: 'failed', id: 0, code: 'bridge_failed' }).then(() => close(0, 1));
      }
    }, 250);
  } catch {
    if (!closing) {
      await emit({ type: 'failed', id: 0, code: 'start_failed' });
      await close(0, 1);
    }
  }
}

/** 串行状态查询，不在子进程退出后用 isBusy 隐式重新启动。 */
async function command(input) {
  if (input.action === 'stop') { await close(input.id); return; }
  if (input.action !== 'status' || !booted || !manager?.processId || closing) throw new Error('not ready');
  const busy = await manager.isBusy();
  if (typeof busy !== 'boolean' || !manager.processId) throw new Error('invalid status');
  await emit({ type: 'status', id: input.id, busy });
}

let received = false;
let commands = Promise.resolve();
lines.on('line', line => {
  try {
    if (Buffer.byteLength(line, 'utf8') > 16384) throw new Error('input too large');
    const input = JSON.parse(line);
    if (!received) {
      received = true;
      void start(input);
    } else {
      if (!Number.isSafeInteger(input.id) || input.id <= 0) throw new Error('invalid id');
      commands = commands.then(() => command(input)).catch(async () => {
        await emit({ type: 'failed', id: input.id, code: 'bridge_failed' });
        await close(input.id, 1);
      });
    }
  } catch {
    void emit({ type: 'failed', id: 0, code: 'bridge_failed' }).then(() => close(0, 1));
  }
});
// 父进程退出会关闭 stdin；信号及断管同样走 SDK 清理，避免遗漏独立子进程组。
lines.on('close', () => { void close(); });
process.once('SIGTERM', () => { void close(); });
process.once('SIGINT', () => { void close(); });
