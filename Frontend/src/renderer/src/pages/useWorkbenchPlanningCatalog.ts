import { useCallback, useContext, useEffect, useRef, useState } from 'react'
import { UiReadVisibility } from '../hooks/uiReadVisibility'
import { inspectWorkspacePlanningArtifacts } from '../service/applicationStorage'
import type { PlanningCatalogStatus } from '../../../shared/planningCatalog'

type Inspection = Awaited<ReturnType<typeof inspectWorkspacePlanningArtifacts>>
type CatalogState = {
  workspace: string
  inspection?: Inspection
  settled: boolean
  status: PlanningCatalogStatus
}
const RETRY_DELAYS = [300, 900]
const READ_TIMEOUT_MS = 15_000

/** 按目录源合并本次真实读取；失败只保留同工作区最近成功的展示，合法空数组正常替换。 */
function acceptInspection(previous: CatalogState | undefined, incoming: Inspection): Pick<CatalogState, 'inspection' | 'status'> {
  const productFailed = incoming.catalogIssues.some((issue) => issue.source === 'product')
  const technicalFailed = incoming.catalogIssues.some((issue) => issue.source === 'technical')
  const old = previous?.inspection
  return {
    inspection: {
      ...incoming,
      ...(old && productFailed ? { pages: old.pages, pageTree: old.pageTree } : {}),
      ...(old && technicalFailed ? { apiContracts: old.apiContracts, entities: old.entities } : {}),
      planningUnconfirmed: incoming.planningUnconfirmed ||
        Boolean(incoming.catalogIssues.length && old?.planningUnconfirmed)
    },
    status: {
      loading: true, issues: incoming.catalogIssues,
      productAvailable: !productFailed || previous?.status.productAvailable === true,
      technicalAvailable: !technicalFailed || previous?.status.technicalAvailable === true
    }
  }
}

/** 进入、重新显示、正式规划更新或手动刷新时真实读盘，窗口聚焦不触发读取。 */
export function useWorkbenchPlanningCatalog(
  workspaceRoot: string | undefined,
  refreshKey: string,
  read = inspectWorkspacePlanningArtifacts
) {
  const visible = useContext(UiReadVisibility)
  const workspace = workspaceRoot || ''
  const [state, setState] = useState<CatalogState>()
  const [revision, setRevision] = useState(0)
  const generation = useRef(0)

  /** 显式重新读取文件，不复用上次结果或进行执行恢复。 */
  const reload = useCallback((): void => { setRevision((value) => value + 1) }, [])
  /** 新迭代已清理正式规划，立即丢弃旧展示并重新读取当前目录。 */
  const reset = useCallback((): void => {
    generation.current += 1
    setState(undefined)
    setRevision((value) => value + 1)
  }, [])

  useEffect(() => {
    if (!visible || !workspace) return
    let active = true
    const timers = new Set<number>()
    /** 每轮重新读取拥有独立身份，手动刷新也使旧响应失效。 */
    const refresh = async (): Promise<void> => {
      const current = ++generation.current
      /** 只接收当前工作区最新一次请求的回执。 */
      const valid = (): boolean => active && current === generation.current
      setState((old) => ({
        ...(old?.workspace === workspace ? old : undefined), workspace, settled: false,
        status: { loading: true, issues: [],
          productAvailable: old?.workspace === workspace && old.status.productAvailable,
          technicalAvailable: old?.workspace === workspace && old.status.technicalAvailable }
      }))
      for (let attempt = 0; attempt <= RETRY_DELAYS.length; attempt += 1) {
        let deadline: number | undefined
        try {
          // IPC 无取消能力；期限只释放界面等待，迟到结果仍由代次隔离，不能再提交。
          const timeout = new Promise<never>((_, reject) => {
            deadline = window.setTimeout(() => reject(new Error('规划目录读取超时')), READ_TIMEOUT_MS)
            timers.add(deadline)
          })
          const incoming = await Promise.race([read(workspace), timeout])
          if (!valid()) return
          const done = incoming.catalogIssues.length === 0 || attempt === RETRY_DELAYS.length
          setState((old) => {
            const next = acceptInspection(old?.workspace === workspace ? old : undefined, incoming)
            return { workspace, ...next, settled: done, status: { ...next.status, loading: !done } }
          })
          if (done) return
        } catch (error) {
          if (!valid()) return
          console.warn('读取开发产物目录失败。', error)
          if (attempt === RETRY_DELAYS.length) {
            setState((old) => old?.workspace === workspace ? {
              ...old, settled: true,
              status: { ...old.status, loading: false, error: '规划目录读取失败，请重试。' }
            } : old)
            return
          }
        } finally {
          if (deadline !== undefined) { window.clearTimeout(deadline); timers.delete(deadline) }
        }
        if (!valid()) return
        await new Promise<void>((resolve) => {
          const timer = window.setTimeout(() => { timers.delete(timer); resolve() }, RETRY_DELAYS[attempt])
          timers.add(timer)
        })
        if (!valid()) return
      }
    }
    void refresh()
    return () => {
      active = false
      generation.current += 1
      timers.forEach((timer) => window.clearTimeout(timer))
    }
  }, [workspace, visible, refreshKey, revision, read])

  const current = state?.workspace === workspace ? state : undefined
  return {
    inspection: current?.inspection,
    settled: current?.settled === true,
    status: current?.status || {
      loading: true, issues: [], productAvailable: false, technicalAvailable: false
    },
    reload, reset
  }
}
