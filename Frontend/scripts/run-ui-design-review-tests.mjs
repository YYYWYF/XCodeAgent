import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const root = path.resolve(import.meta.dirname, '..')
const require = createRequire(path.join(root, 'package.json'))
const output = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-ui-review-'))
const boundary = path.join(root, 'tests/uiDesignReviewBoundary.tsx')
const originalNodeEnv = process.env.NODE_ENV
process.env.NODE_ENV = 'development'
try {
  await build({
    configFile: false,
    esbuild: { jsx: 'automatic' },
    define: { 'process.env.NODE_ENV': JSON.stringify('development') },
    logLevel: 'error',
    root,
    ssr: { noExternal: true },
    plugins: [
      {
        name: 'ui-review-dom-boundary',
        enforce: 'pre',
        // 保留 React 的 Node 实现，仅隔离布局、图标和 iframe 的浏览器边界。
        resolveId(source, importer) {
          if (['react', 'react/jsx-runtime', 'react-test-renderer'].includes(source)) {
            return { id: require.resolve(source), external: true }
          }
          if (source === 'antd') return boundary
          if (source === '@ant-design/icons') return '\0ui-review-icons'
          if (
            importer?.endsWith('/Welcome/UiDesignConfirmationPanel.tsx') &&
            ['../DesignRenderer/DesignPagePreview', '../MarkdownContent/MarkdownContent'].includes(
              source
            )
          ) {
            return boundary
          }
        },
        // 图标外观在 Electron 中检查，组件测试只关注当前审阅动作。
        load(id) {
          if (id === '\0ui-review-icons')
            return `export { default as ArrowUpOutlined,
          default as CheckOutlined, default as EyeOutlined, default as InboxOutlined,
          default as LayoutOutlined, default as ReloadOutlined, default as ThunderboltOutlined } from ${JSON.stringify(boundary)}`
        }
      }
    ],
    css: { preprocessorOptions: { less: { additionalData: '@class-prefix: devagentstudio;' } } },
    build: {
      minify: false,
      outDir: output,
      ssr: path.join(root, 'tests/uiDesignReviewSync.test.ts'),
      rollupOptions: { output: { entryFileNames: 'review.mjs' } }
    }
  })
  await import(pathToFileURL(path.join(output, 'review.mjs')).href)
} finally {
  if (originalNodeEnv === undefined) delete process.env.NODE_ENV
  else process.env.NODE_ENV = originalNodeEnv
  await fs.rm(output, { recursive: true, force: true })
}
