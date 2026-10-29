import { useEffect, useRef, useState } from 'react'
import {
  getAgentSurfaceSelectionDraft,
  saveAgentSurfaceSelection,
  type AgentSurfaceSelectionDraft
} from '../../../../service/agentSurfaceSelection'
import type { JsonRecord } from './RequirementDocPanelData'

type DraftState = {
  source: JsonRecord
  workspaceRoot: string
  snapshot?: AgentSurfaceSelectionDraft
  loading: boolean
  saving: boolean
  error: string
}

type DraftView = {
  productPlan: JsonRecord
  editable: boolean
  busy: boolean
  error: string
  reload: () => void
  toggle: (agentId: string, pageId: string, enabled: boolean) => Promise<void>
}

/** 读取权威草稿并回填每次保存结果，异步结果只属于原工作区及原规划版本。 */
export function useAgentSurfaceDraft(
  productPlan: JsonRecord,
  workspaceRoot?: string,
  onChanged?: () => void
): DraftView {
  const [state, setState] = useState<DraftState>()
  const [revision, setRevision] = useState(0)
  const scope = useRef(0)
  const saving = useRef(false)
  const enabled = Boolean(
    workspaceRoot &&
      productPlan.confirmation_status === 'pending_user_confirmation' &&
      Array.isArray(productPlan.agents) &&
      productPlan.agents.length
  )

  useEffect(() => {
    const generation = ++scope.current
    saving.current = false
    if (!enabled || !workspaceRoot) {
      setState(undefined)
      return
    }
    const initial: DraftState = {
      source: productPlan,
      workspaceRoot,
      loading: true,
      saving: false,
      error: ''
    }
    setState(initial)
    void getAgentSurfaceSelectionDraft(workspaceRoot)
      .then((snapshot) => {
        if (scope.current === generation) setState({ ...initial, snapshot, loading: false })
      })
      .catch((reason: unknown) => {
        if (scope.current === generation) {
          setState({
            ...initial,
            loading: false,
            error: reason instanceof Error ? reason.message : '读取当前浮窗草稿失败'
          })
        }
      })
    return () => {
      scope.current += 1
    }
  }, [enabled, workspaceRoot, productPlan, revision])

  // 工作区或工作流草稿一旦改变，上一份快照立即失效，不等待 effect 才禁用旧入口。
  const current =
    state && state.source === productPlan && state.workspaceRoot === workspaceRoot
      ? state
      : undefined
  const displayedPlan = enabled && current?.snapshot ? current.snapshot.productPlan : productPlan
  const editable = Boolean(
    enabled &&
      current?.snapshot &&
      displayedPlan.confirmation_status === 'pending_user_confirmation'
  )

  /** 显式重读草稿，不自动重放因版本冲突或网络失败而未确认的写操作。 */
  const reload = (): void => {
    if (!saving.current) setRevision((value) => value + 1)
  }

  /** 使用已展示快照的服务端摘要进行 CAS，保存成功后立即替换界面的草稿和摘要。 */
  const toggle = async (agentId: string, pageId: string, selected: boolean): Promise<void> => {
    if (!editable || !current?.snapshot || current.loading || !workspaceRoot || saving.current)
      return
    const generation = scope.current
    saving.current = true
    setState({ ...current, saving: true, error: '' })
    try {
      const snapshot = await saveAgentSurfaceSelection(
        workspaceRoot,
        agentId,
        pageId,
        selected,
        current.snapshot.productPlanHash
      )
      if (scope.current !== generation) return
      setState({ ...current, snapshot, saving: false, error: '' })
      onChanged?.()
    } catch (reason: unknown) {
      if (scope.current === generation) {
        setState((previous) =>
          previous
            ? {
                ...previous,
                saving: false,
                error: reason instanceof Error ? reason.message : '保存浮窗选择失败'
              }
            : previous
        )
      }
    } finally {
      if (scope.current === generation) saving.current = false
    }
  }

  return {
    productPlan: displayedPlan,
    editable,
    busy: enabled && (!current || current.loading || current.saving),
    error: current?.error || '',
    reload,
    toggle
  }
}
