import assert from 'node:assert/strict'
import { test } from 'node:test'
import { shouldSettleAdjust } from '../src/renderer/src/service/uiDesignAdjustSettlement'

/** 提交 adjust 那一刻：workflow 还是上一轮的 runId + requires_user_input。
 *  此时**不能**判定完成，否则 loading 一闪就没（用户看到"点了没反应"）。 */
test('刚提交时不能判定完成', () => {
  assert.equal(
    shouldSettleAdjust({
      adjusting: true,
      submittedRunId: 'run-old',
      currentRunId: 'run-old',
      workflowStatus: 'requires_user_input'
    }),
    false
  )
})

/** 新一轮 run 已经起来、还在跑：继续显示生成中。 */
test('新一轮运行中不能判定完成', () => {
  assert.equal(
    shouldSettleAdjust({
      adjusting: true,
      submittedRunId: 'run-old',
      currentRunId: 'run-new',
      workflowStatus: 'running'
    }),
    false
  )
})

/** runId 换代 + 落回待输入态 = 这一轮真的结束了。 */
test('runId 换代且落回待输入态 → 完成', () => {
  assert.equal(
    shouldSettleAdjust({
      adjusting: true,
      submittedRunId: 'run-old',
      currentRunId: 'run-new',
      workflowStatus: 'requires_user_input'
    }),
    true
  )
})

/** 不处于 adjust 时永远不判定（这是 adjust 专用收口，不能影响池生成路径）。 */
test('非 adjust 时不判定', () => {
  assert.equal(
    shouldSettleAdjust({
      adjusting: false,
      submittedRunId: 'run-old',
      currentRunId: 'run-new',
      workflowStatus: 'requires_user_input'
    }),
    false
  )
})

/** 提交时拿不到 runId（空串）也不能误判：只有真正换代才算。 */
test('提交时 runId 缺失也不能误判', () => {
  assert.equal(
    shouldSettleAdjust({
      adjusting: true,
      submittedRunId: '',
      currentRunId: '',
      workflowStatus: 'requires_user_input'
    }),
    false
  )
})

/** 其他终态（failed 等）不算正常收口，保持现状交给别的机制处理。 */
test('非待输入态不判定完成', () => {
  assert.equal(
    shouldSettleAdjust({
      adjusting: true,
      submittedRunId: 'run-old',
      currentRunId: 'run-new',
      workflowStatus: 'failed'
    }),
    false
  )
})
