import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useUncommittedChanges } from '../../../../context'
import {
  readDeferredFingerprint,
  useMilestoneCommit,
  writeDeferredFingerprint,
  type UseMilestoneCommitReturn
} from './useMilestoneCommit'
import { resolveCommitScope } from './visibility'

/**
 * 发送前是否要拦一下：有未提交变更就先让用户决定"稍后 / 审阅并提交"。
 *
 * 单独成纯函数便于单测 —— 它决定"什么时候该打扰用户"，是最容易写错的部分；
 * hook 本身挂了 Git 状态机，不适合直接单测。
 *
 * - `eligibleCount > 0`：确实有东西可提交。0 时不该拦 —— 设计阶段仓库可能还没建立。
 * - `!dismissed`：用户对**当前这份**变更已经选过「稍后」。指纹由 useMilestoneCommit
 *   按工作区快照算，内容一变 dismissed 自动失效，会重新问。
 * - `!inspecting`：还在读 Git 状态，此时 eligibleCount 尚不可信，不要据此打扰。
 * - `!artifactsSettling`：设计稿还在生成。此刻提交会捞到一个中间态快照，所以既不该
 *   提交、也不该弹一个"主操作不可用"的窗去挡用户的推进，直接放行。
 *
 * **刻意不看 `commitResult`**：门禁是长期可重复触发的，一次会话里用户可能提交很多次。
 * 刚提交过这件事已经由 `eligibleCount` 表达（提交后快照里就没东西可提交了）；
 * 若再加一条"提交过就不再问"，第一次提交成功后门禁就永久失效，之后再改代码也不会提醒。
 */
export function shouldGateSend(input: {
  eligibleCount: number
  dismissed: boolean
  inspecting: boolean
  artifactsSettling: boolean
}): boolean {
  if (input.inspecting || input.artifactsSettling) return false
  if (input.dismissed) return false
  return input.eligibleCount > 0
}

/**
 * 这次澄清提交是否会让用户跳到下一个阶段。
 *
 * 发送门禁管得住"输入框"那条路，管不住卡片上的确认按钮 —— 用户完全可以在开发阶段
 * 不发言，直接点「确认并返回设计阶段」。那条路同样会在没保存的状态下把人推走，
 * 所以提交前要过同一道门禁。
 *
 * 只列真正**离开当前阶段**的确认动作：
 * - `revision_impact_confirmation: 'approved'` —— 正式修改影响确认，跳回设计/计划阶段。
 * - `planning_stage_entry: 'enter'` —— 设计阶段确认完毕，进入计划阶段。
 *
 * 刻意不含阶段内的审批（单元测试、性能、代码审查修复、API 门禁）：它们不换阶段。
 * 也不含验收确认：验收已经由 AcceptanceCommitDock 承担自动提交/手动审阅，
 * 在这里再拦一次是重复。同样不含 `revision_draft_interaction` —— 走到那张卡时
 * 用户已经在正式修改流程里，阶段早跳过了。
 */
export function isStageAdvanceDecision(answers: Record<string, unknown>): boolean {
  return (
    answers.revision_impact_confirmation === 'approved' || answers.planning_stage_entry === 'enter'
  )
}

export type CommitBeforeSendReturn = {
  /** 底层提交状态机：门禁弹窗与复用的提交弹窗都用它。 */
  commit: UseMilestoneCommitReturn
  /** 门禁弹窗是否可见。 */
  gateVisible: boolean
  /** 当前口径下可提交的文件数：门禁弹窗正文里的 N 与判据用的是同一个数。 */
  eligibleCount: number
  /** 请求推进（发送消息，或确认跳阶段）：判据成立则先弹门禁，否则直接执行。 */
  requestAdvance: (run: () => void | Promise<void>) => void
  /** 门禁弹窗选「稍后」：记下暂缓指纹，然后放行这次推进。 */
  handleDeferAndAdvance: () => void
  /** 门禁弹窗选「审阅并提交」：关掉门禁，交给提交弹窗接管。 */
  handleReview: () => void
  /** 门禁弹窗被关掉（点遮罩/ESC）：放弃这次发送。 */
  handleGateCancel: () => void
}

/**
 * 推进前提交门禁：把「有未提交变更」的提醒从"一直挂在那里"改成"往前推进前拦一下"。
 *
 * 为什么拦：把用户推向下一个阶段有两条路，两条都要过这道门禁 ——
 * 1. **发送消息**：设计/计划阶段那个输入框会把内容交给后端做**意图识别**，模型可能据此
 *    跳到别的阶段。
 * 2. **卡片确认**：`revision_impact_confirmation` / `planning_stage_entry` 这类按钮
 *    不经过输入框，点一下就直接跳阶段（见 `isStageAdvanceDecision`）。
 * 两条路都可能在用户"没保存"的状态下把人推走。
 *
 * 复用 `useMilestoneCommit` 而不是另写一套：inspect → 审阅弹窗 → commit 的全套能力
 * （含「稍后」的指纹去重）都已具备，这里只加"推进"这一个动作的前置判断。
 *
 * `includePlatformArtifacts` 传 true：设计阶段唯一的变更就是 `.devagentstudio` 产物，
 * 按业务代码口径算永远是 0，门禁会彻底不出现 —— 与它取代的那个常驻提醒框口径一致。
 */
export function useCommitBeforeSend(
  workspaceRoot: string,
  milestoneId: string,
  defaultCommitMessage: string,
  /** 设计稿还在生成中（见 shouldGateSend 的说明）。 */
  artifactsSettling = false
): CommitBeforeSendReturn {
  const commit = useMilestoneCommit(workspaceRoot, milestoneId, defaultCommitMessage, true)
  // 判据必须跟着**当前工作区**走，所以读共享快照（顶部角标那份）而不是
  // `useMilestoneCommit` 的本地快照：后者只在挂载与显式重读时更新，会一直停在
  // 挂载那一刻 —— 应用刚打开时工作区还是干净的，之后 Agent 写了代码、角标已经是 3 了，
  // 门禁却仍以为无事可拦，这正是"没拦住"的第二种成因。
  const { snapshot: sharedSnapshot } = useUncommittedChanges()
  const [gateVisible, setGateVisible] = useState(false)
  // 「稍后」记住的指纹。自己持有一份而不是复用 useMilestoneCommit 的 dismissed：
  // 那个是按它自己的本地快照算的，同样会停在挂载那一刻，工作区变了也不会解禁。
  const [deferredFingerprint, setDeferredFingerprint] = useState(() =>
    readDeferredFingerprint(milestoneId)
  )
  // 被拦下的那次推进。用 ref 而不是 state：它要在多个 callback 之间传递且不参与渲染，
  // 放进 state 会让下面的 callback 依赖它、每次拦截都重建。
  const pendingAdvanceRef = useRef<(() => void | Promise<void>) | undefined>(undefined)

  // 换应用（milestoneId 变）时重读一次暂缓指纹：state 初值只在挂载时算，
  // 不重读会把上一个应用「稍后」的状态带到新应用上。
  useEffect(() => {
    setDeferredFingerprint(readDeferredFingerprint(milestoneId))
  }, [milestoneId])

  // 共享快照还没读到（Git 不可用、工作区刚切）时退回本地快照：本地那份可能旧，
  // 但比"什么都不知道"强；下面的 inspecting 会先挡住首帧的误判。
  const gateSnapshot = sharedSnapshot ?? commit.snapshot
  const eligibleCount = useMemo(
    () => resolveCommitScope({ snapshot: gateSnapshot, includePlatformArtifacts: true }).length,
    [gateSnapshot]
  )
  // 指纹是工作区内容的属性，同一份内容怎么算都一样；内容一变就与记下的那份不等，
  // 「稍后」自动失效、门禁重新问。
  const dismissed =
    Boolean(deferredFingerprint) && deferredFingerprint === gateSnapshot?.fingerprint

  const requestAdvance = useCallback(
    (run: () => void | Promise<void>): void => {
      if (
        !shouldGateSend({
          eligibleCount,
          dismissed,
          // 共享快照已就绪时不再等本地那次读取：本地读取可能是很久以前发起的，
          // 拿它当"还没读完"会把已经知道答案的门禁白白按下去。
          inspecting: commit.inspecting && !sharedSnapshot,
          artifactsSettling
        })
      ) {
        void run()
        return
      }
      pendingAdvanceRef.current = run
      setGateVisible(true)
    },
    [artifactsSettling, commit.inspecting, dismissed, eligibleCount, sharedSnapshot]
  )

  // 提交成功后放行被拦下的那次推进。用 effect 而不是让调用方显式调用：提交成功这一刻
  // 由状态机内部产生（commitResult），调用方接不到，只能靠渲染后的这个信号。
  // commitResult 只会由本 hook 的 handleCommit 置位，而它必须先经过门禁的「审阅并提交」，
  // 所以这里放行的必然是本次拦下的那一次推进，不会是遗留的旧动作。
  useEffect(() => {
    if (!commit.commitResult) return
    const run = pendingAdvanceRef.current
    pendingAdvanceRef.current = undefined
    if (run) void run()
  }, [commit.commitResult])

  const handleDeferAndAdvance = useCallback((): void => {
    // 先记指纹再放行：顺序反了的话，推进触发的状态更新可能让指纹比对失败，
    // 同一次推进会被追问第二次。记的是**判据用的那份**快照的指纹，否则下次比对
    // 会拿两把不同的尺子量。
    if (gateSnapshot) {
      writeDeferredFingerprint(milestoneId, gateSnapshot.fingerprint)
      setDeferredFingerprint(gateSnapshot.fingerprint)
    }
    setGateVisible(false)
    const run = pendingAdvanceRef.current
    pendingAdvanceRef.current = undefined
    if (run) void run()
  }, [gateSnapshot, milestoneId])

  const handleReview = useCallback((): void => {
    setGateVisible(false)
    // pendingAdvance 保留：提交成功后由上面的 effect 放行这次推进。
    void commit.handleOpenCommit()
  }, [commit])

  const handleGateCancel = useCallback((): void => {
    setGateVisible(false)
    // 放弃推进：输入框里的草稿、卡片上的确认按钮都还在，用户可改完再点。
    pendingAdvanceRef.current = undefined
  }, [])

  return {
    commit,
    gateVisible,
    eligibleCount,
    requestAdvance,
    handleDeferAndAdvance,
    handleReview,
    handleGateCancel
  }
}
