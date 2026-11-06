import { useEffect, useState } from 'react'
import type { WorkflowRunPayload } from '../../../../typings'
import { isArtifactReference, readWorkflowArtifact } from '../../../../service/workflowArtifacts'

/** 当前确认卡才读取所需正式资料；局部展示结果不写回会话，也不用于恢复执行。 */
export function useWorkflowDocuments(
  source: WorkflowRunPayload,
  workspace: string | undefined,
  enabled: boolean
): {
  workflow: WorkflowRunPayload
  loading: boolean
  error?: string
} {
  const [loaded, setLoaded] = useState<{
    source: WorkflowRunPayload
    workflow: WorkflowRunPayload
    error?: string
  }>()
  const mode = String(
    (source.state?.clarification as { mode?: string })?.mode ||
      source.summary.clarification?.mode ||
      ''
  )
  const required =
    enabled &&
    [
      'requirement_document_confirmation',
      'project_plan_confirmation',
      'technical_plan_confirmation'
    ].includes(mode)
  const refs = source.state?.artifactRefs as Record<string, unknown> | undefined
  const keys = required
    ? mode === 'requirement_document_confirmation'
      ? ['requirement_spec', 'product_plan']
      : mode === 'technical_plan_confirmation'
        ? ['technical_plan', 'product_plan', 'requirement_spec']
        : ['project_plan']
    : []
  const hasRefs =
    keys.some((key) => source.state?.[key] == null && isArtifactReference(refs?.[key])) ||
    Boolean(
      required && source.confirmationArtifact?.contentRef && !source.confirmationArtifact.content
    )

  useEffect(() => {
    let stale = false
    if (!required || !hasRefs || !workspace) return
    /** 只复制所选卡片的视图；切换 Run 后丢弃迟到的资料响应。 */
    const load = async (): Promise<void> => {
      try {
        const state = { ...source.state }
        // 串行读取控制瞬时内存，不预取未展示阶段或历史卡片的资料。
        for (const key of keys) {
          const ref = refs?.[key]
          if (state[key] == null && isArtifactReference(ref))
            state[key] = await readWorkflowArtifact(workspace, ref)
          if (stale) return
        }
        let artifact = source.confirmationArtifact
        if (artifact?.contentRef && !artifact.content) {
          const content = await readWorkflowArtifact(workspace, artifact.contentRef)
          if (typeof content !== 'string') throw new Error('确认文档正文格式无效。')
          artifact = { ...artifact, content }
        }
        if (!stale)
          setLoaded({ source, workflow: { ...source, state, confirmationArtifact: artifact } })
      } catch (error) {
        if (!stale)
          setLoaded({
            source,
            workflow: source,
            error: error instanceof Error ? error.message : '资料读取失败。'
          })
      }
    }
    void load()
    return () => {
      stale = true
    }
  }, [source, workspace, required, hasRefs])

  if (!required || !hasRefs) return { workflow: source, loading: false }
  if (!workspace)
    return { workflow: source, loading: false, error: '缺少工作区，无法读取确认资料。' }
  return loaded?.source === source
    ? { workflow: loaded.workflow, loading: false, error: loaded.error }
    : { workflow: source, loading: true }
}
