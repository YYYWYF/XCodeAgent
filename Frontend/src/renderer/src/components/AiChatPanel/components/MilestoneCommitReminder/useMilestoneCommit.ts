import { useEffect, useMemo, useState } from 'react'
import { message } from 'antd'
import { commitVersionControl, inspectAllVersionControl } from '../../../../service/versionControl'
import type { VersionControlCommitResult, VersionControlSnapshot } from '../../../../typings'
import { useUncommittedChanges } from '../../../../context'
import { resolveCommitScope } from './visibility'

const DEFERRED_STORAGE_PREFIX = 'devagentstudio:version-control:milestone-deferred:'

export type UseMilestoneCommitReturn = {
  snapshot: VersionControlSnapshot | undefined
  commitResult: VersionControlCommitResult | undefined
  /** 当前提醒口径下的可提交文件（业务代码，或按需含平台产物）。 */
  eligiblePaths: string[]
  selectedPaths: string[]
  commitMessage: string
  inspectError: string
  commitError: string
  inspecting: boolean
  committing: boolean
  modalVisible: boolean
  dismissed: boolean
  setCommitMessage: (value: string) => void
  setSelectedPaths: (value: string[]) => void
  setModalVisible: (value: boolean) => void
  loadSnapshot: () => Promise<VersionControlSnapshot | undefined>
  handleDefer: () => void
  handleOpenCommit: () => Promise<void>
  handleCommit: () => Promise<void>
}

/** 里程碑代码提交状态机：inspect → 审阅弹窗 → commit，含 deferred 指纹去重。
 *  从 MilestoneCommitReminder 抽出，供模板就绪卡片与验收提醒复用。 */
export function useMilestoneCommit(
  workspaceRoot: string,
  milestoneId: string,
  defaultCommitMessage: string,
  /**
   * 是否把 `.devagentstudio` 平台产物也纳入"可提交"口径。
   *
   * 默认 false：代码提交提醒只该由业务代码变更触发，平台自身的状态流转
   * （lifecycle 每次 +1、规划文档、报告）不算"用户改了代码"。
   *
   * 设计阶段的「设计文档已确认，可保存为设计版本」弱提醒要传 true ——
   * 那时唯一的变更就是 `.devagentstudio`，按业务代码算永远是 0，提醒会彻底消失。
   * 这与文档 §4.3 把它和"代码提交入口分开"的定位一致：两条通道，两种口径。
   */
  includePlatformArtifacts = false
): UseMilestoneCommitReturn {
  // 常驻快照（顶部角标与各档提醒共用）：提交成功后必须回填，否则角标会停在旧数字，
  // 用户刚被告知"已提交"却仍看到未提交数，会怀疑没提交成功。
  const { apply: applyUncommittedChanges, refresh: refreshUncommittedChanges } =
    useUncommittedChanges()
  const [snapshot, setSnapshot] = useState<VersionControlSnapshot>()
  const [commitResult, setCommitResult] = useState<VersionControlCommitResult>()
  const [selectedPaths, setSelectedPaths] = useState<string[]>([])
  const [commitMessage, setCommitMessage] = useState(defaultCommitMessage)
  const [inspectError, setInspectError] = useState('')
  const [commitError, setCommitError] = useState('')
  const [inspecting, setInspecting] = useState(false)
  const [committing, setCommitting] = useState(false)
  const [modalVisible, setModalVisible] = useState(false)
  const [dismissed, setDismissed] = useState(false)

  // 「提醒口径」：角标计数与提醒是否出现用它，按 includePlatformArtifacts 分流，
  // 默认只算业务代码 —— 平台产物的状态流转不该让"用户改了代码"的提醒亮起来。
  // **它不再决定弹窗的默认勾选**（见下面 setSelectedPaths 处的说明）。
  const eligiblePaths = useMemo(
    () => resolveCommitScope({ snapshot, includePlatformArtifacts }),
    [snapshot, includePlatformArtifacts]
  )
  // 「可提交范围」：全部变更，**含 .devagentstudio 产物**。后端用它做合法性校验
  // （selected ⊆ requested），所以不能跟着提醒口径一起收窄 —— 否则用户在弹窗里
  // 勾上产物（它们确实列在那里、也确实该能随版本追溯）就会被拒"所选文件已不属于
  // 当前可提交变更"。两者是两个概念，别合并。
  //
  // 它同时也是弹窗的**默认勾选**：弹窗列出的就是这批文件（后端 `files` 直接由
  // `eligible_paths` 构造），默认全勾选等于"看到什么就默认提交什么"，
  // 用户想排除哪几个再自己取消。早先默认勾的是提醒口径（只有业务代码），
  // 于是弹窗里列出的产物全是不勾状态，与"全选"按钮的语义也不一致。
  const requestedPaths = useMemo(() => snapshot?.eligiblePaths ?? [], [snapshot])

  const loadSnapshot = async (): Promise<VersionControlSnapshot | undefined> => {
    setInspecting(true)
    setInspectError('')
    try {
      const nextSnapshot = await inspectAllVersionControl(workspaceRoot)
      setSnapshot(nextSnapshot)
      setSelectedPaths(nextSnapshot.eligiblePaths)
      const deferredFingerprint = readDeferredFingerprint(milestoneId)
      setDismissed(deferredFingerprint === nextSnapshot.fingerprint)
      return nextSnapshot
    } catch (error) {
      setInspectError(error instanceof Error ? error.message : '无法读取当前 Git 状态。')
      return undefined
    } finally {
      setInspecting(false)
    }
  }

  // 首次挂载时读取一次 Git 状态。
  useEffect(() => {
    let active = true
    setInspecting(true)
    setInspectError('')
    void inspectAllVersionControl(workspaceRoot)
      .then((nextSnapshot) => {
        if (!active) return
        setSnapshot(nextSnapshot)
        setSelectedPaths(nextSnapshot.eligiblePaths)
        const deferredFingerprint = readDeferredFingerprint(milestoneId)
        setDismissed(deferredFingerprint === nextSnapshot.fingerprint)
      })
      .catch((error) => {
        if (!active) return
        setInspectError(error instanceof Error ? error.message : '无法读取当前 Git 状态。')
      })
      .finally(() => {
        if (active) setInspecting(false)
      })
    return () => {
      active = false
    }
  }, [workspaceRoot, milestoneId, includePlatformArtifacts])

  const handleDefer = (): void => {
    if (!snapshot) return
    writeDeferredFingerprint(milestoneId, snapshot.fingerprint)
    setDismissed(true)
  }

  const handleOpenCommit = async (): Promise<void> => {
    const currentSnapshot = snapshot ?? (await loadSnapshot())
    if (!currentSnapshot) return
    setCommitError('')
    setModalVisible(true)
  }

  const handleCommit = async (): Promise<void> => {
    if (!snapshot || !selectedPaths.length || !commitMessage.trim() || committing) return
    setCommitting(true)
    setCommitError('')
    try {
      const result = await commitVersionControl({
        workspaceRoot,
        requestedPaths,
        selectedPaths,
        expectedFingerprint: snapshot.fingerprint,
        message: commitMessage.trim()
      })
      setCommitResult(result)
      setSnapshot(result.snapshot)
      // 先采纳提交结果让角标立刻归零，再整读一次校准：提交返回的快照只覆盖本次
      // 请求的文件，直接留在共享 store 里会让后续消费者看到不完整的工作区视图。
      applyUncommittedChanges(result.snapshot)
      void refreshUncommittedChanges()
      setModalVisible(false)
      message.success('代码已提交')
    } catch (error) {
      setCommitError(error instanceof Error ? error.message : '提交失败，请重新检查后重试。')
    } finally {
      setCommitting(false)
    }
  }

  return {
    snapshot,
    commitResult,
    eligiblePaths,
    selectedPaths,
    commitMessage,
    inspectError,
    commitError,
    inspecting,
    committing,
    modalVisible,
    dismissed,
    setCommitMessage,
    setSelectedPaths,
    setModalVisible,
    loadSnapshot,
    handleDefer,
    handleOpenCommit,
    handleCommit
  }
}

/** 读取当前里程碑上次暂缓提醒时对应的工作区指纹。 */
function readDeferredFingerprint(milestoneId: string): string {
  try {
    return window.sessionStorage.getItem(`${DEFERRED_STORAGE_PREFIX}${milestoneId}`) || ''
  } catch {
    return ''
  }
}

/** 保存暂缓提醒的工作区指纹，使代码变化后可以重新出现。 */
function writeDeferredFingerprint(milestoneId: string, fingerprint: string): void {
  try {
    window.sessionStorage.setItem(`${DEFERRED_STORAGE_PREFIX}${milestoneId}`, fingerprint)
  } catch {
    // 会话存储不可用时仅保留组件内的暂缓状态。
  }
}
