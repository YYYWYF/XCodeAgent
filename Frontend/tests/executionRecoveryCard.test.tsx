import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ExecutionRecoveryCard from '../src/renderer/src/components/AiChatPanel/components/ExecutionRecoveryCard'
import type { ExecutionRecoveryCandidate } from '../src/renderer/src/typings'

/** 构造恢复卡片所需的公开候选。 */
function recovery(
  availability: ExecutionRecoveryCandidate['availability']
): ExecutionRecoveryCandidate {
  return {
    sourceRunId: 'run-A',
    threadId: 'thread-A',
    executionKind: 'workbench',
    executionStatus: 'interrupted',
    availability,
    canContinue: availability === 'ready',
    reasonCode: 'RECOVERY_TEST',
    message: '恢复测试',
    updatedAt: '2026-09-12T00:00:00.000Z'
  }
}

test('READY recovery card exposes continue action', () => {
  const markup = renderToStaticMarkup(
    createElement(ExecutionRecoveryCard, {
      recovery: recovery('ready'),
      loading: false,
      onContinue: () => undefined
    })
  )
  assert.match(markup, /上一次执行未正常完成/)
  assert.match(markup, /继续执行/)
})

test('blocked and requires-handler recovery cards hide continue action', () => {
  for (const availability of ['blocked', 'requires_handler'] as const) {
    const markup = renderToStaticMarkup(
      createElement(ExecutionRecoveryCard, {
        recovery: recovery(availability),
        loading: false,
        onContinue: () => undefined
      })
    )
    assert.doesNotMatch(markup, /继续执行/)
  }
})

