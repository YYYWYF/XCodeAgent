import type { AuthenticationResult } from '../shared/authentication'

/** 通过 SDK 消费独立 Python 认证流，禁止自行解析 SSE。 */
export async function runAuthentication(
  baseUrl: string,
  input: Record<string, unknown>,
  onProgress?: (value: AuthenticationResult) => Promise<void> | void
): Promise<AuthenticationResult> {
  const { HttpAgent, randomUUID } = await import('@ag-ui/client')
  const endpoint = new URL(baseUrl)
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(endpoint.hostname)) {
    throw new Error('桌面认证必须连接本机 Python 后端。')
  }
  const agent = new HttpAgent({
    url: `${baseUrl.replace(/\/$/, '')}/authentication/run`,
    threadId: randomUUID()
  })
  let progressError: unknown
  const timer = setTimeout(() => agent.abortRun(), input.action === 'login' ? 910_000 : 15_000)
  try {
    const result = await agent.runAgent(
      { forwardedProps: { authentication: input } },
      {
        onCustomEvent: async ({ event }) => {
          if (event.name === 'authentication') {
            const value = event.value as AuthenticationResult
            if (value.status === 'in_progress') {
              try { await onProgress?.(value) } catch (error) {
                progressError = error
                agent.abortRun()
              }
            }
          }
        }
      }
    )
    if (progressError) throw progressError
    const payload = (result.result as { authentication?: AuthenticationResult } | undefined)?.authentication
    if (payload?.status !== 'completed') {
      throw new Error(payload?.error?.message || 'Python 认证接口未返回完整结果，请检查后端连接。')
    }
    return payload
  } catch (error) {
    throw progressError || error
  } finally {
    clearTimeout(timer)
  }
}
