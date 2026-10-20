import assert from 'node:assert/strict'
import { createPlanningStageReentry, type PlanningStreamChunk } from '../src/renderer/src/components/AiChatPanel/planningStageReentry'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'

/** 构造原 Graph 在线执行时发布的节点帧。 */
function workflow(phase: string): WorkflowRunPayload {
  return { runId: 'reentry-run', threadId: 'planning-checkpoint', events: [], summary: { status: 'running', phase } }
}

// 回退先准备设计会话，再按到达顺序接入真实生成帧；异步读盘期间不能写入来源计划历史。
{
  let finishPreparation!: (key: string) => void
  let preparations = 0
  const deliveries: Array<{ key: string; chunk: PlanningStreamChunk }> = []
  const router = createPlanningStageReentry({
    sourceSessionKey: 'plan-session',
    prepareDesignSession: async (incoming) => {
      preparations++
      assert.equal(incoming.threadId, 'planning-checkpoint')
      return new Promise<string>((resolve) => { finishPreparation = resolve })
    },
    publish: (key, chunk) => { deliveries.push({ key, chunk }) }
  })
  router.publish({ workflow: workflow('design_intent_analysis') })
  router.publish({ content: '正在调用需求 Agent' })
  router.publish({ workflow: workflow('requirements') })
  assert.equal(deliveries.length, 0)
  finishPreparation('design-session')
  await router.finish()
  assert.equal(preparations, 1)
  assert.deepEqual(deliveries.map((entry) => entry.key), ['design-session', 'design-session', 'design-session'])
  assert.equal(deliveries[1].chunk.content, '正在调用需求 Agent')
  router.publish({ workflow: { ...workflow('product_planning'), summary: { status: 'requires_user_input', phase: 'product_planning' } } })
  assert.equal(deliveries.at(-1)?.key, 'design-session')
}

// UI 回退同样进入设计会话，继续承接原 UI Agent，不创建另一条业务运行。
{
  const phases: string[] = []
  const router = createPlanningStageReentry({
    sourceSessionKey: 'plan',
    prepareDesignSession: async () => 'design',
    publish: (key, chunk) => { phases.push(`${key}:${chunk.workflow?.summary.phase}`) }
  })
  router.publish({ workflow: workflow('ui_confirmation') })
  await router.finish()
  assert.deepEqual(phases, ['design:ui_confirmation'])
}

// 问答和技术生成保留原阶段；不能因为存在旧设计产物或历史消息就跳到设计阶段。
for (const chunk of [{ content: '技术规划说明' }, { workflow: workflow('technical_planning') }]) {
  let moved = false
  const keys: string[] = []
  const router = createPlanningStageReentry({
    sourceSessionKey: 'plan',
    prepareDesignSession: async () => { moved = true; return 'design' },
    publish: (key) => { keys.push(key) }
  })
  router.publish(chunk)
  await router.finish()
  assert.equal(moved, false)
  assert.deepEqual(keys, ['plan'])
}

console.log('planning stage reentry tests passed')
