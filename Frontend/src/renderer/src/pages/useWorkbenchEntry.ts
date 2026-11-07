import { message } from 'antd'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  WORKBENCH_ENTRY_TIMEOUT_MS,
  workbenchEntryFailureMessage,
  workbenchEntryTimeoutMessage,
  workbenchSessionEntryFailure,
  type WorkbenchEntryFailure,
  type WorkbenchEntryProgress
} from './workbenchEntryMessages'

type WorkbenchEntryStage = 'loading' | 'leaving' | 'ready'
const MIN_VISIBLE_MS = 520
const FADE_MS = 280

// 集中管理入口加载、业务失败提示和整体超时，后续刷新不重新触发入口门禁。
export function useWorkbenchEntry(onEntryLoadFailure: () => void) {
  const [entryStage, setEntryStage] = useState<WorkbenchEntryStage>('loading')
  const [configurationLoaded, setConfigurationLoaded] = useState(false)
  const [developmentPlanningPagesLoaded, setPlanningLoaded] = useState(false)
  const [chatSessionHistoryReady, setHistoryReady] = useState(false)
  const startedAtRef = useRef(Date.now())
  const stageRef = useRef<WorkbenchEntryStage>('loading')
  const failedRef = useRef(false)
  const progressRef = useRef<WorkbenchEntryProgress>({
    configuration: false, planning: false, sessionHistory: false
  })

  // 首个入口错误只处理一次；具体技术原因由调用方记录在日志中。
  const failWithMessage = useCallback((content: string): void => {
    if (stageRef.current !== 'loading' || failedRef.current) return
    failedRef.current = true
    message.error(content, 6)
    onEntryLoadFailure()
  }, [onEntryLoadFailure])

  // 以明确的失败类别选择业务文案，禁止直接展示异常消息。
  const failWorkbenchEntry = useCallback((reason: WorkbenchEntryFailure): void => {
    failWithMessage(workbenchEntryFailureMessage(reason))
  }, [failWithMessage])

  // 记录配置已读，供超时时准确列出尚未完成的资源。
  const markConfigurationLoaded = useCallback((): void => {
    progressRef.current.configuration = true
    setConfigurationLoaded(true)
  }, [])

  // 规划检查完成即可放行，文档缺失或待确认仍沿用原有业务处理。
  const markPlanningArtifactsLoaded = useCallback((): void => {
    progressRef.current.planning = true
    setPlanningLoaded(true)
  }, [])

  // 会话错误只进入安全的业务类别，原始异常仅写入诊断日志。
  const handleSessionHistoryReadyChange = useCallback((ready: boolean, error?: string): void => {
    progressRef.current.sessionHistory = ready
    setHistoryReady(ready)
    if (error) {
      console.warn('工作台入口恢复历史会话失败。', error)
      failWorkbenchEntry(workbenchSessionEntryFailure(error))
    }
  }, [failWorkbenchEntry])

  useEffect(() => {
    if (entryStage !== 'loading') return
    const timer = window.setTimeout(() => {
      failWithMessage(workbenchEntryTimeoutMessage(progressRef.current))
    }, WORKBENCH_ENTRY_TIMEOUT_MS)
    return () => window.clearTimeout(timer)
  }, [entryStage, failWithMessage])

  useEffect(() => {
    if (!configurationLoaded || !developmentPlanningPagesLoaded || !chatSessionHistoryReady || entryStage !== 'loading') return
    const remaining = Math.max(0, MIN_VISIBLE_MS - (Date.now() - startedAtRef.current))
    const timer = window.setTimeout(() => {
      if (failedRef.current) return
      stageRef.current = 'leaving'
      setEntryStage('leaving')
    }, remaining)
    return () => window.clearTimeout(timer)
  }, [configurationLoaded, developmentPlanningPagesLoaded, chatSessionHistoryReady, entryStage])

  useEffect(() => {
    if (entryStage !== 'leaving') return
    const timer = window.setTimeout(() => {
      stageRef.current = 'ready'
      setEntryStage('ready')
    }, FADE_MS)
    return () => window.clearTimeout(timer)
  }, [entryStage])

  return {
    entryStage,
    developmentPlanningPagesLoaded,
    failWorkbenchEntry,
    markConfigurationLoaded,
    markPlanningArtifactsLoaded,
    handleSessionHistoryReadyChange
  }
}
