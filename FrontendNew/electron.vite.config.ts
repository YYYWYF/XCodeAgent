import { resolve } from 'path'
import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'

const supportedAppEnvs = ['dev', 'st', 'uat'] as const
type AppEnv = (typeof supportedAppEnvs)[number]

const isSupportedAppEnv = (value: string): value is AppEnv =>
  supportedAppEnvs.includes(value as AppEnv)

const appEnv = process.env['APP_ENV'] ?? 'dev'

if (!isSupportedAppEnv(appEnv)) {
  throw new Error(`Unsupported APP_ENV: ${appEnv}`)
}

const appEnvDefine = {
  'process.env.APP_ENV': JSON.stringify(appEnv)
}

export default defineConfig({
  main: {
    define: appEnvDefine
  },
  preload: {
    define: appEnvDefine
  },
  renderer: {
    build: {
      rollupOptions: {
        input: {
          index: resolve('src/renderer/index.html'),
          //login: resolve('src/renderer/login.html')
        }
      }
    },
    resolve: {
      alias: {
        '@renderer': resolve('src/renderer/src')
      }
    },
    css: {
      preprocessorOptions: {
        less: {
          // 与 cx() 共用 style.json，保证 TSX 类名和 Less 选择器同步换前缀。
          //additionalData: `@class-prefix: ${styleConfig.classPrefix};`,
          javascriptEnabled: true
        }
      }
    },
    plugins: [react()]
  }
})
