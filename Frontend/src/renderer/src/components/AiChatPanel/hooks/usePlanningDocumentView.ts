import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { WorkflowRunPayload } from '../../../typings'
import { isArtifactReference, readWorkflowArtifact } from '../../../service/workflowArtifacts'

type DocumentKind = 'requirement-doc' | 'technical-plan'
type DocumentRequest = { key: string; reference: unknown }
type LoadedDocuments = { identity: string; values: Record<string, unknown>; error?: string }

const DOCUMENT_KEYS: Record<DocumentKind, readonly string[]> = {
  'requirement-doc': ['requirement_spec', 'product_plan'],
  'technical-plan': ['technical_plan', 'product_plan']
}

/** 按当前可见文档选择引用，不预取其他阶段的资料。 */
function documentRequests(
  source: WorkflowRunPayload | undefined,
  kind: DocumentKind | undefined
): DocumentRequest[] {
  if (!source || !kind) return []
  const candidate = source.state?.artifactRefs
  const refs =
    candidate && typeof candidate === 'object' && !Array.isArray(candidate)
      ? (candidate as Record<string, unknown>)
      : undefined
  const requests = DOCUMENT_KEYS[kind]
    .filter((key) => refs && key in refs)
    .map((key) => ({ key, reference: refs?.[key] }))
  const artifact = source.confirmationArtifact
  const matches =
    kind === 'technical-plan'
      ? artifact?.id === 'technical_plan'
      : artifact?.id === 'requirement_spec' || artifact?.id === 'product_plan'
  if (matches && artifact?.contentRef)
    requests.push({ key: 'markdown', reference: artifact.contentRef })
  return requests
}

/** 按不可变引用读取右侧文档；正文仅留在面板内，不写回 Canonical State 或历史。 */
export function usePlanningDocumentView(
  source: WorkflowRunPayload | undefined,
  workspace: string | undefined,
  kind: DocumentKind | undefined,
  readArtifact = readWorkflowArtifact
): { workflow?: WorkflowRunPayload; loading: boolean; error?: string; retry: () => void } {
  const [attempt, setAttempt] = useState(0)
  const [loaded, setLoaded] = useState<LoadedDocuments>()
  const cache = useRef(new Map<string, Promise<unknown>>())
  // 依赖引用内容而非每帧 Workflow 对象，避免心跳、进度及同源快照重复读取。
  const serialized = JSON.stringify(documentRequests(source, kind))
  const requests = useMemo(() => JSON.parse(serialized) as DocumentRequest[], [serialized])
  const identity = JSON.stringify([
    workspace,
    source?.threadId,
    source?.runId,
    kind,
    serialized,
    attempt
  ])

  /** 显式重试失败的读取；已成功的相同 hash 可以继续复用。 */
  const retry = useCallback((): void => setAttempt((value) => value + 1), [])

  useEffect(() => {
    if (!requests.length) return
    let stale = false
    /** 串行读取所选文档，并在切换工作区、运行或版本后丢弃迟到结果。 */
    const load = async (): Promise<void> => {
      const values: Record<string, unknown> = {}
      try {
        if (!workspace) throw new Error('缺少工作区，无法读取文档。')
        for (const { key, reference } of requests) {
          if (!isArtifactReference(reference)) throw new Error('文档引用无效，请重新同步状态。')
          const cacheKey = JSON.stringify([workspace, reference])
          let reading = cache.current.get(cacheKey)
          if (!reading) {
            reading = readArtifact(workspace, reference)
            cache.current.set(cacheKey, reading)
            // 仅保留少量最近文档，避免连续修订累积所有历史正文。
            if (cache.current.size > 8) cache.current.delete(cache.current.keys().next().value!)
          }
          let value: unknown
          try {
            value = await reading
          } catch (error) {
            if (cache.current.get(cacheKey) === reading) cache.current.delete(cacheKey)
            throw error
          }
          if (stale) return
          if (
            key === 'markdown'
              ? typeof value !== 'string'
              : !value || typeof value !== 'object' || Array.isArray(value)
          ) {
            cache.current.delete(cacheKey)
            throw new Error('文档正文格式无效。')
          }
          values[key] = value
        }
        if (!stale) setLoaded({ identity, values })
      } catch (error) {
        if (!stale)
          setLoaded({
            identity,
            values: {},
            error: error instanceof Error ? error.message : '文档读取失败。'
          })
      }
    }
    void load()
    return () => {
      stale = true
    }
  }, [identity, requests, workspace, readArtifact])

  if (!source || !requests.length) return { workflow: source, loading: false, retry }
  if (loaded?.identity !== identity) return { workflow: source, loading: true, retry }
  if (loaded.error) return { workflow: source, loading: false, error: loaded.error, retry }
  const { markdown, ...documents } = loaded.values
  return {
    workflow: {
      ...source,
      state: { ...source.state, ...documents },
      confirmationArtifact:
        source.confirmationArtifact && typeof markdown === 'string'
          ? { ...source.confirmationArtifact, content: markdown }
          : source.confirmationArtifact
    },
    loading: false,
    retry
  }
}
