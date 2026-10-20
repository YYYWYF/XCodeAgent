import assert from 'node:assert/strict'
import { sessionToRestoreForPhase } from '../src/renderer/src/components/AiChatPanel/hooks/phaseSessionSelection'
import type { WorkbenchPhase } from '../src/renderer/src/workbenchPhase'

// 所有阶段只按更新时间恢复最近会话：消息数、旧修订和数组顺序不决定展示。
for (const phase of [
  'product',
  'planning',
  'development',
  'test',
  'review',
  'acceptance'
] as WorkbenchPhase[]) {
  const old = {
    id: 'old',
    workbenchPhase: phase,
    updatedAt: 1,
    messageCount: 99,
    revisionContext: { changeId: 'old' }
  }
  const latest = {
    id: 'latest',
    workbenchPhase: phase,
    updatedAt: 3,
    messageCount: 0,
    revisionContext: { changeId: 'new' }
  }
  const otherPhase = {
    id: 'other-stage',
    workbenchPhase: 'release' as WorkbenchPhase,
    updatedAt: 100,
    messageCount: 50
  }
  assert.equal(sessionToRestoreForPhase([old, otherPhase, latest], phase)?.id, latest.id)
  assert.equal(sessionToRestoreForPhase([latest, old, otherPhase], phase)?.id, latest.id)
  assert.equal(sessionToRestoreForPhase([otherPhase], phase), undefined)
  assert.equal(sessionToRestoreForPhase([], phase), undefined)
  // 显式业务交接可定位刚创建的会话，后续普通浏览仍回到最近会话。
  assert.equal(sessionToRestoreForPhase([old, latest], phase, old.id)?.id, old.id)
  assert.equal(sessionToRestoreForPhase([old, latest], phase)?.id, latest.id)
}
console.log('phase session selection tests passed')

// 本地会话仅承接输出，不再充当后端 continuation 的入场凭据。
import { revisionContinuationOutputSession } from '../src/renderer/src/components/AiChatPanel/hooks/revisionSession'
import type { ChatSessionSummary } from '../src/renderer/src/service/chatSessions'
import type { WorkflowRevisionContinuation } from '../src/renderer/src/typings'
const continuation = { changeId: 'current-change' } as WorkflowRevisionContinuation
const devOutput = {
  id: 'dev-output', workflowId: 'app', workbenchPhase: 'development', updatedAt: 2
} as ChatSessionSummary
assert.equal(revisionContinuationOutputSession([], 'app', undefined, continuation), undefined)
assert.equal(revisionContinuationOutputSession([devOutput], 'app', undefined, continuation)?.id, 'dev-output')
assert.equal(revisionContinuationOutputSession([
  devOutput, { ...devOutput, id: 'other-app', workflowId: 'other', updatedAt: 10 }
], 'app', undefined, continuation)?.id, 'dev-output')
