import assert from 'node:assert/strict'
import { continueRevisionOnce, revisionContinuationRecoveryVisible } from '../src/renderer/src/service/revisionContinuationGuard'
import type { ApplicationLifecycle, WorkflowRevisionContinuation } from '../src/renderer/src/typings'

const continuation = { action: 'continue_revision_build', changeId: 'guard-change',
  formalBranch: 'workbench_plan_revision', token: 'test-token'.repeat(8), technicalPlanSha256: 'a'.repeat(64)
} as WorkflowRevisionContinuation
const ready = { revision: 10, activeFormalRevision: { ...continuation, status: 'continuation_ready',
  planningThreadId: 'planning', sourceThreadId: 'development' }
} as unknown as ApplicationLifecycle

// 确认后 PLAN 已只读，但平台开发交接失败仍可恢复；同阶段旧会话不得获得该按钮。
assert.equal(revisionContinuationRecoveryVisible(ready, continuation, 'planning'), true)
assert.equal(revisionContinuationRecoveryVisible(ready, continuation, 'development'), true)
assert.equal(revisionContinuationRecoveryVisible(ready, continuation, 'old-planning'), false)
assert.equal(revisionContinuationRecoveryVisible({ ...ready, activeFormalRevision: undefined }, continuation, 'planning'), false)

/** 使用后端生命周期状态机代替网络，记录真实提交边界而非只验证 helper 返回值。 */
function harness() {
  let lifecycle = ready
  let submissions = 0
  let reads = 0
  let submit: () => Promise<boolean> = async () => {
    lifecycle = { ...ready, revision: 11, activeFormalRevision: undefined }
    return true
  }
  const input = {
    workspace: '/guard-test', applicationId: 'guard-app', continuation,
    /** 每次返回当前后端状态，模拟已经消费后重新挂载组件。 */
    readLifecycle: async () => { reads += 1; return lifecycle },
    /** 确保生产路径把读取到的状态通知工作台。 */
    onLifecycle: (value: ApplicationLifecycle) => { assert.equal(value, lifecycle) },
    /** 只在真正发送 AG-UI 前计数。 */
    submit: async () => { submissions += 1; return submit() }
  }
  return { input, setLifecycle: (value: ApplicationLifecycle) => { lifecycle = value },
    setSubmit: (value: typeof submit) => { submit = value },
    submissions: () => submissions, reads: () => reads }
}

// Runtime 与冷恢复同时交付同一凭据：只有一次读取和提交，重新进入不再写消息或发送。
{
  const h = harness()
  const first = continueRevisionOnce(h.input)
  const second = continueRevisionOnce({ ...h.input })
  assert.equal(first, second)
  assert.deepEqual(await Promise.all([first, second]), [true, true])
  assert.equal(h.submissions(), 1)
  assert.equal(h.reads(), 1)
  assert.equal(await continueRevisionOnce({ ...h.input }), true)
  assert.equal(h.submissions(), 1)
}

// 重新进入的旧快照遇到已消费、已结束或另一轮修订时，均不会提交旧凭据。
for (const activeFormalRevision of [undefined,
  { ...ready.activeFormalRevision!, status: 'building' },
  { ...ready.activeFormalRevision!, status: 'failed' },
  { ...ready.activeFormalRevision!, changeId: 'new-change' },
  { ...ready.activeFormalRevision!, technicalPlanSha256: 'b'.repeat(64) }]) {
  const h = harness()
  h.setLifecycle({ ...ready, activeFormalRevision })
  assert.equal(await continueRevisionOnce(h.input), true)
  assert.equal(h.submissions(), 0)
}

// 提交前失败可以显式重试；首次失败不会把同一尝试永久标记为成功。
{
  const h = harness()
  h.setSubmit(async () => { throw new Error('请求尚未入场') })
  await assert.rejects(continueRevisionOnce(h.input), /请求尚未入场/)
  h.setSubmit(async () => { h.setLifecycle({ ...ready, activeFormalRevision: undefined }); return true })
  assert.equal(await continueRevisionOnce(h.input), true)
  assert.equal(h.submissions(), 2)
}

// 后端已消费但响应丢失或节点失败：重新读取事实后交给 Recovery，重试不重发。
for (const throws of [true, false]) {
  const h = harness()
  h.setSubmit(async () => {
    h.setLifecycle({ ...ready, activeFormalRevision: { ...ready.activeFormalRevision!, status: 'failed' } })
    if (throws) throw new Error('已入场后连接断开')
    return false
  })
  assert.equal(await continueRevisionOnce(h.input), true)
  assert.equal(await continueRevisionOnce(h.input), true)
  assert.equal(h.submissions(), 1)
}

// 无法读取后端时失败关闭；恢复网络后的显式重试才允许首次提交。
{
  const h = harness()
  await assert.rejects(continueRevisionOnce({ ...h.input,
    readLifecycle: async () => { throw new Error('后端不可达') }
  }), /后端不可达/)
  assert.equal(h.submissions(), 0)
  assert.equal(await continueRevisionOnce(h.input), true)
  assert.equal(h.submissions(), 1)
}

console.log('revision continuation guard: 并发、重入、消费前失败及消费后恢复验证通过')
