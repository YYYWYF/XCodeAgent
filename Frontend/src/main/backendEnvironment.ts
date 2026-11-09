import http from 'node:http'
import type { AppEnv } from './env'

/** 返回目标环境唯一允许携带的后端配置文件名。 */
export function backendEnvFilename(appEnv: AppEnv): string {
  return appEnv === 'dev' ? '.env' : `.env.${appEnv}`
}

/** 核验后端身份，缺失身份或环境错配时给出可执行的启动提示。 */
export function assertBackendEnvironment(health: unknown, appEnv: AppEnv): void {
  const actual =
    typeof health === 'object' && health !== null && 'app_env' in health
      ? health.app_env
      : undefined
  if (actual !== appEnv) {
    throw new Error(
      `后端环境为 ${typeof actual === 'string' ? actual : '未知'}，客户端环境为 ${appEnv}。` +
        `请使用 bash scripts/start-backend.sh --env ${appEnv} 启动对应后端` +
        `（Windows: scripts/start-backend.ps1 -Env ${appEnv}）。`
    )
  }
}

/** 读取现有基础设施健康接口；连接失败可重试，环境错配立即拒绝启动。 */
export function readBackendHealth(baseUrl: string, appEnv: AppEnv): Promise<Record<string, unknown> | undefined> {
  return new Promise((resolve, reject) => {
    let settled = false
    /** 统一收口网络事件，避免超时与迟到响应重复完成。 */
    const finish = (healthy: boolean, error?: Error, health?: Record<string, unknown>): void => {
      if (settled) return
      settled = true
      if (error) reject(error)
      else resolve(healthy ? health : undefined)
    }
    const request = http.get(`${baseUrl}/health`, { timeout: 1000 }, (response) => {
      if (!response.statusCode || response.statusCode < 200 || response.statusCode >= 300) {
        response.resume()
        finish(false)
        return
      }
      const chunks: Buffer[] = []
      let size = 0
      response.on('data', (chunk: Buffer) => {
        size += chunk.length
        if (size > 1024 * 1024) {
          finish(false)
          response.destroy()
          return
        }
        chunks.push(chunk)
      })
      response.once('error', () => finish(false))
      response.once('end', () => {
        if (settled) return
        let health: unknown
        try {
          health = JSON.parse(Buffer.concat(chunks).toString('utf8'))
        } catch {
          finish(false)
          return
        }
        try {
          assertBackendEnvironment(health, appEnv)
          finish(true, undefined, health as Record<string, unknown>)
        } catch (error) {
          finish(false, error as Error)
        }
      })
    })
    request.once('timeout', () => {
      finish(false)
      request.destroy()
    })
    request.once('error', () => finish(false))
  })
}

/** 保留启动健康判定；后台接入额外读取同一份公开实例身份。 */
export async function checkBackendHealth(baseUrl: string, appEnv: AppEnv): Promise<boolean> {
  return Boolean(await readBackendHealth(baseUrl, appEnv))
}
