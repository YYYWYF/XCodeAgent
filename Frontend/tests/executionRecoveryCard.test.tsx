import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ExecutionRecoveryCard from '../src/renderer/src/components/AiChatPanel/components/ExecutionRecoveryCard'
import AgentErrorCard from '../src/renderer/src/components/AgentErrorCard'
import type { ExecutionRecoveryCandidate } from '../src/renderer/src/typings'

/** 构造恢复卡片所需的公开候选。 */
function recovery(
  availability: ExecutionRecoveryCandidate['availability']
): ExecutionRecoveryCandidate {
  return {
    sourceRunId: 'run-A',
    ownerSessionId: 'session-A',
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

test('planning recovery error card shows safe failure summary and details', () => {
  const markup = renderToStaticMarkup(
    createElement(AgentErrorCard, {
      error: '上一次模型调用失败，当前执行现场可以安全继续。',
      failureDiagnostic: {
        sourceRunId: 'run-B',
        origin: 'model_call',
        code: 'MODEL_CONNECTION_ERROR',
        operation: 'technical_planning',
        provider: 'openai-compatible',
        model: 'mimo-v2.5-pro',
        httpStatus: 503,
        message: 'model unavailable'
      },
      onRetry: () => undefined,
      recovery: true,
      retryLabel: '继续执行',
      title: '规划执行已中断'
    })
  )
  assert.match(markup, /503 · mimo-v2\.5-pro/)
  assert.match(markup, /model unavailable/)
  assert.match(markup, /错误详情/)
  assert.match(markup, /Source Run ID/)
  assert.match(markup, /继续执行/)
  assert.doesNotMatch(markup, /请查看错误详情和相关执行记录后重试/)
})

test('planning recovery error card remains usable without legacy diagnostics', () => {
  const markup = renderToStaticMarkup(
    createElement(AgentErrorCard, {
      error: '上一次模型调用失败。',
      onRetry: () => undefined,
      recovery: true,
      retryLabel: '继续执行',
      title: '规划执行已中断'
    })
  )
  assert.match(markup, /规划执行已中断/)
  assert.match(markup, /继续执行/)
  assert.doesNotMatch(markup, /错误详情 ▾/)
})
