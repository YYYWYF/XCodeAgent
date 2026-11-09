import assert from 'node:assert/strict'
import { test } from 'node:test'
import { compactWorkflowEvents } from '../src/shared/workflowEventProjection'
import { normalizePersistentSessionMessage } from '../src/main/sessionMessageNormalization'
import { readWorkflowPayload } from '../src/renderer/src/service/agUiAgent'
import { processStepsForDisplay } from '../src/renderer/src/service/processStepHistory'

test('接收和落盘保留十三个失败轮次，事件不带重复状态且当前只存一份 state', () => {
  const state = {
    phase: 'build',
    selectedPageId: 'home',
    tasks: [{ id: 'task', source_refs: { endpoint_designs: 'x'.repeat(200_000) } }]
  }
  const events = Array.from({ length: 13 }, (_, index) => ({
    type: 'workflow.node.completed',
    runId: 'run',
    threadId: 'thread',
    nodeName: 'build',
    attempt: index + 1,
    status: 'failed',
    message: '构建失败',
    data: {
      stateDelta: state,
      detail: {
        error: '请求失败',
        buildTaskPlan: { task_graph: { tasks: state.tasks } },
        buildExecutionSlice: { tasks: state.tasks }
      }
    }
  }))
  const workflow = {
    runId: 'run',
    threadId: 'thread',
    summary: { status: 'failed' },
    events,
    state,
    state: state
  }
  const live = readWorkflowPayload(workflow)!
  const stored = normalizePersistentSessionMessage({
    id: 1,
    role: 'assistant',
    content: '结果',
    workflow
  })
  const saved = stored.workflow as typeof workflow
  assert.equal(live.events.length, 13)
  assert.equal(saved.events.at(-1)?.attempt, 13)
  assert.ok(Buffer.byteLength(JSON.stringify(saved.events)) < 10_000)
  assert.deepEqual(saved.state, state)
  assert.equal("result" in saved, false)
  assert.equal(saved.events.at(-1)?.data.detail.error, '请求失败')
  assert.equal((live.events[0].data?.stateDelta as any).selectedPageId, 'home')
  assert.equal(live.events[1].data?.stateDelta, undefined)
  assert.equal(events[0].data.stateDelta.tasks[0].source_refs.endpoint_designs.length, 200_000)
  assert.deepEqual(compactWorkflowEvents(saved.events), saved.events)
  const another = compactWorkflowEvents([
    ...saved.events,
    { runId: 'another-run', data: { stateDelta: { selectedPageId: 'home' } } }
  ])
  assert.deepEqual(another.at(-1)?.data, { stateDelta: { selectedPageId: 'home' } })
  const finished = compactWorkflowEvents([
    {
      type: 'workflow.run.finished',
      data: {
        summary: {
          status: 'failed',
          buildTaskPlan: state,
          failureDiagnostic: { code: 'MODEL_FAILED' }
        }
      }
    }
  ])
  assert.deepEqual(finished[0].data, {
    summary: { status: 'failed', failureDiagnostic: { code: 'MODEL_FAILED' } }
  })
})

test('有界展示增量仍能恢复节点步骤、授权结果、扫描结果、产物路径和显式字段清空', () => {
  const workflow = readWorkflowPayload({
    runId: 'run',
    threadId: 'thread',
    summary: { status: 'completed' },
    events: [
      {
        type: 'workflow.node.completed',
        nodeName: 'inspect_workspace',
        attempt: 1,
        status: 'completed',
        data: {
          stateDelta: {
            selected_entity_id: 'entity',
            detail_target_type: 'entity',
            technical_plan_json_path: 'plans/technical-plan.json',
            product_plan_path: 'plans/product-plan.md',
            timeline: ['inspect_workspace:cache_hit']
          },
          detail: {
            workspaceInspection: {
              schemaVersion: '1',
              revision: 'rev',
              cacheHit: true,
              fileManifest: { totalFiles: 1, sourceFiles: 1 },
              techStack: ['React']
            }
          }
        }
      },
      {
        type: 'workflow.node.completed',
        nodeName: 'authorization_bootstrap',
        attempt: 2,
        status: 'completed',
        data: {
          stateDelta: { selected_entity_id: null },
          detail: {
            authorizationBootstrap: {
              status: 'manual_required',
              message: '需要人工处理',
              ddlAvailable: false
            }
          }
        }
      }
    ]
  })!
  const steps = processStepsForDisplay(undefined, workflow)!
  assert.equal(
    steps.find((step) => step.nodeName === 'inspect_workspace')?.workspaceInspection?.revision,
    'rev'
  )
  assert.equal(
    steps.find((step) => step.nodeName === 'authorization_bootstrap')?.authorizationBootstrap
      ?.status,
    'manual_required'
  )
  assert.equal(steps.find((step) => step.nodeName === 'authorization_bootstrap')?.attempt, 2)
  assert.equal((workflow.events[1].data?.stateDelta as any).selected_entity_id, null)
  assert.equal(
    (workflow.events[0].data?.stateDelta as any).product_plan_path,
    'plans/product-plan.md'
  )
  assert.equal((workflow.events[0].data?.stateDelta as any).technical_plan_json_path, undefined)
})
