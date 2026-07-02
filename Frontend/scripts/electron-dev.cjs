const { spawn } = require('node:child_process');
const net = require('node:net');
const path = require('node:path');
const electronPath = require('electron');

const HOST = process.env.VITE_HOST || '127.0.0.1';
const PREFERRED_PORT = Number(process.env.VITE_PORT || 5173);
const PROJECT_ROOT = path.join(__dirname, '..');
const VITE_BIN = path.join(PROJECT_ROOT, 'node_modules', '.bin', process.platform === 'win32' ? 'vite.cmd' : 'vite');

let viteProcess = null;
let electronProcess = null;
let shuttingDown = false;

function waitForPort(host, port, timeoutMs = 30000) {
  const startedAt = Date.now();

  return new Promise((resolve, reject) => {
    const tryConnect = () => {
      const socket = net.connect({ host, port });

      socket.once('connect', () => {
        socket.destroy();
        resolve();
      });

      socket.once('error', () => {
        socket.destroy();
        if (Date.now() - startedAt >= timeoutMs) {
          reject(new Error(`Timed out waiting for ${host}:${port}`));
          return;
        }
        setTimeout(tryConnect, 250);
      });
    };

    tryConnect();
  });
}

function isPortAvailable(host, port) {
  return new Promise((resolve) => {
    const server = net.createServer();

    server.unref();
    server.once('error', () => resolve(false));
    server.once('listening', () => {
      server.close(() => resolve(true));
    });
    server.listen({ host, port });
  });
}

async function getAvailablePort(host, preferredPort) {
  let port = preferredPort;

  while (!(await isPortAvailable(host, port))) {
    port += 1;
  }

  return port;
}

function stopProcess(childProcess) {
  if (childProcess && !childProcess.killed) {
    childProcess.kill();
  }
}

function shutdown(exitCode = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  stopProcess(electronProcess);
  stopProcess(viteProcess);
  process.exit(exitCode);
}

async function main() {
  const port = await getAvailablePort(HOST, PREFERRED_PORT);
  const devServerUrl = `http://${HOST}:${port}`;

  if (port !== PREFERRED_PORT) {
    console.warn(`Port ${PREFERRED_PORT} is in use, using ${port} instead.`);
  }

  viteProcess = spawn(
    VITE_BIN,
    ['--host', HOST, '--port', String(port), '--strictPort'],
    {
      stdio: 'inherit',
      env: process.env,
      cwd: PROJECT_ROOT,
    },
  );

  viteProcess.once('exit', (code) => {
    if (!shuttingDown && !electronProcess) {
      shutdown(code ?? 1);
    }
  });

  await waitForPort(HOST, port);

  electronProcess = spawn(electronPath, ['.'], {
    stdio: 'inherit',
    cwd: PROJECT_ROOT,
    env: {
      ...process.env,
      VITE_DEV_SERVER_URL: devServerUrl,
      ELECTRON_DISABLE_SECURITY_WARNINGS: 'true',
    },
  });

  electronProcess.once('exit', (code) => {
    shutdown(code ?? 0);
  });
}

process.on('SIGINT', () => shutdown(0));
process.on('SIGTERM', () => shutdown(0));

main().catch((error) => {
  console.error(error);
  shutdown(1);
});
