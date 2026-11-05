import { randomUUID } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'

export type ArtifactReference = {
  artifactId: string
  byteLength: number
  mediaType: 'application/json'
}

/** 校验当前内容地址，禁止把任意路径或损坏引用送入资料读取接口。 */
export function isArtifactReference(value: unknown): value is ArtifactReference {
  if (!value || typeof value !== 'object') return false
  const ref = value as ArtifactReference
  return (
    /^[a-f0-9]{64}$/.test(ref.artifactId) &&
    Number.isInteger(ref.byteLength) &&
    ref.byteLength >= 0 &&
    ref.byteLength <= 16 * 1024 * 1024 &&
    ref.mediaType === 'application/json'
  )
}

/** 通过官方 AG-UI 客户端按需读取单份资料，读取失败不能回退到会话快照。 */
export async function readWorkflowArtifact(
  workspaceRoot: string,
  reference: ArtifactReference
): Promise<unknown> {
  if (!workspaceRoot || !isArtifactReference(reference)) throw new Error('资料引用或工作区无效。')
  const base = window.devAgentStudio?.agentBaseUrl?.replace(/\/$/, '') || '/api/agent'
  const agent = createAgUiHttpAgent({
    url: `${base}/workflow-artifacts/run`,
    threadId: randomUUID()
  })
  const result = await agent.runAgent({
    forwardedProps: { artifact: { workspaceRoot, reference } }
  })
  const payload = (
    result.result as {
      workflowArtifact?: { status?: string; content?: unknown; error?: { message?: string } }
    }
  )?.workflowArtifact
  if (payload?.status !== 'completed') throw new Error(payload?.error?.message || '资料读取失败。')
  return payload.content
}
