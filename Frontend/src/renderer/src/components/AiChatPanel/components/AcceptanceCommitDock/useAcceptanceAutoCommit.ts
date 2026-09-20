import { useCallback, useEffect, useRef, useState } from 'react'
import { commitVersionControl, inspectAllVersionControl } from '../../../../service/versionControl'
import type { VersionControlCommitResult, VersionControlSnapshot } from '../../../../typings'
import {
  ACCEPTANCE_COMMIT_MESSAGE,
  decideAcceptanceCommit,
  hasSensitivePath
} from './acceptanceCommit'

export type AcceptanceCommitState = {
  snapshot: VersionControlSnapshot | undefined
  commitResult: VersionControlCommitResult | undefined
  /** 正在读取 Git 状态或正在提交。 */
  working: boolean
  /** 读取或提交失败的原因；空串表示没失败。 */
  error: string
  /** 需要用户自己审阅（有暂存内容/敏感文件/读不到状态）。 */
  needsManualReview: boolean
  /** 提交后工作区仍有未提交内容。 */
  remainingDirty: boolean
}

/**
 * 验收阶段自动保存代码：读到变更后提交一次，并报告结果。
 *
 * 只提交一次 —— 用 ref 记住已经处理过的指纹。React 严格模式下 effect 会执行两次，
 * 没有这个守卫会提交两次（第二次因指纹变化或工作区已干净而失败/空提交）。
 *
 * 只处理**首次**读到的快照：提交后工作区可能又变了（验收动作本身会写 lifecycle），
 * 不应该追着反复提交。用户后续的改动由各档提醒承载。
 */
export function useAcceptanceAutoCommit(
  workspaceRoot: string,
  enabled: boolean,
  message: string = ACCEPTANCE_COMMIT_MESSAGE
): AcceptanceCommitState {
  const [snapshot, setSnapshot] = useState<VersionControlSnapshot>()
  const [commitResult, setCommitResult] = useState<VersionControlCommitResult>()
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const [needsManualReview, setNeedsManualReview] = useState(false)
  const [remainingDirty, setRemainingDirty] = useState(false)
  // 已经处理过的工作区 + 指纹，避免重复提交（含严格模式的双次 effect）。
  const handledRef = useRef('')

  const run = useCallback(async (): Promise<void> => {
    if (!workspaceRoot || !enabled) return
    setWorking(true)
    setError('')
    try {
      const inspected = await inspectAllVersionControl(workspaceRoot)
      setSnapshot(inspected)
      const decision = decideAcceptanceCommit({
        hasSnapshot: true,
        inspectError: '',
        unborn: inspected.head === 'UNBORN',
        hasStagedChanges: inspected.hasStagedChanges,
        hasSensitivePath: hasSensitivePath(inspected.eligiblePaths),
        eligibleCount: inspected.eligiblePaths.length
      })
      if (decision !== 'commit') {
        // clean 时无事可做；fallback 时交给手动提醒，这里都不提交。
        setNeedsManualReview(decision === 'fallback')
        return
      }
      const handle = `${workspaceRoot}:${inspected.fingerprint}`
      if (handledRef.current === handle) return
      handledRef.current = handle
      const result = await commitVersionControl({
        workspaceRoot,
        // 自动保存的语义是"把当前状态存下来"，所以范围与选择都是全部变更
        // （含 .xcodeagent 产物，版本要能追溯设计）。与模板 baseline 一致。
        requestedPaths: inspected.eligiblePaths,
        selectedPaths: inspected.eligiblePaths,
        expectedFingerprint: inspected.fingerprint,
        message
      })
      setCommitResult(result)
      setSnapshot(result.snapshot)
      setRemainingDirty(result.remainingDirty)
    } catch (caught) {
      // 自动路径失败不该打断验收 —— 降级成手动提醒，让用户自己处理。
      setError(caught instanceof Error ? caught.message : '自动保存代码失败。')
      setNeedsManualReview(true)
    } finally {
      setWorking(false)
    }
  }, [workspaceRoot, enabled, message])

  useEffect(() => {
    void run()
  }, [run])

  return { snapshot, commitResult, working, error, needsManualReview, remainingDirty }
}
