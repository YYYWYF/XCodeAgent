import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import styleConfig from './src/config/style.json';

type NodeModuleLoader = (specifier: string) => Promise<Record<string, any>>;

const loadNodeModule = new Function('specifier', 'return import(specifier)') as NodeModuleLoader;

function localApplicationStorePlugin() {
  return {
    name: 'local-application-store',
    async configureServer(server) {
      const fs = await loadNodeModule('node:fs/promises');
      const path = await loadNodeModule('node:path');
      const applicationsFile = path.join(server.config.root, 'data', 'applications.json');

      const ensureStoreFile = async () => {
        await fs.mkdir(path.dirname(applicationsFile), { recursive: true });
        try {
          await fs.access(applicationsFile);
        } catch {
          await fs.writeFile(applicationsFile, '[]\n', 'utf8');
        }
      };

      const readApplications = async () => {
        await ensureStoreFile();
        const rawValue = await fs.readFile(applicationsFile, 'utf8');
        const parsed = JSON.parse(rawValue || '[]');
        return Array.isArray(parsed) ? parsed : [];
      };

      const writeApplications = async (applications: unknown) => {
        if (!Array.isArray(applications)) {
          throw new Error('applications must be an array');
        }
        await ensureStoreFile();
        await fs.writeFile(applicationsFile, `${JSON.stringify(applications, null, 2)}\n`, 'utf8');
      };

      const sendJson = (res, statusCode: number, payload: unknown) => {
        res.statusCode = statusCode;
        res.setHeader('Content-Type', 'application/json; charset=utf-8');
        res.end(JSON.stringify(payload));
      };

      const readBody = (req) =>
        new Promise<string>((resolve, reject) => {
          let body = '';
          req.setEncoding('utf8');
          req.on('data', (chunk) => {
            body += String(chunk);
          });
          req.on('end', () => resolve(body));
          req.on('error', reject);
        });

      server.middlewares.use('/api/local-applications', async (req, res) => {
        try {
          if (req.method === 'GET') {
            sendJson(res, 200, { applications: await readApplications() });
            return;
          }

          if (req.method === 'PUT' || req.method === 'POST') {
            const body = await readBody(req);
            const payload = JSON.parse(body || '{}');
            await writeApplications(payload.applications);
            sendJson(res, 200, { ok: true });
            return;
          }

          sendJson(res, 405, { error: 'Method not allowed' });
        } catch (error) {
          sendJson(res, 500, {
            error: error instanceof Error ? error.message : 'Unknown local store error',
          });
        }
      });
    },
  };
}

export default defineConfig({
  base: './',
  plugins: [react(), localApplicationStorePlugin()],
  server: {
    proxy: {
      '/api/agent': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/agent/, ''),
      },
    },
  },
  css: {
    preprocessorOptions: {
      less: {
        // Ant Design 的 Less 源码包含 JavaScript 表达式，需要开启此选项。
        javascriptEnabled: true,
        // 与 cx() 共用 style.json，保证 TSX 类名和 Less 选择器同步换前缀。
        additionalData: `@class-prefix: ${styleConfig.classPrefix};`,
      },
    },
  },
});
