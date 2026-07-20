import assert from 'node:assert/strict'
import { test } from 'node:test'
import { workflowPreviewTarget } from '../src/renderer/src/components/AiChatPanel/utils'
import { navigatePreviewHistory } from '../src/renderer/src/utils/previewUrl'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'

/** 构造指定运行状态的最小 Workflow 预览测试数据。 */
function previewWorkflow(
  overrides: Partial<WorkflowRunPayload['summary']> = {},
  runId = 'run-1'
): WorkflowRunPayload {
  return {
    runId,
    threadId: 'thread-1',
    events: [],
    summary: {
      phase: 'launch_project',
      status: 'requires_user_input',
      previewUrl: 'http://127.0.0.1:3000',
      ...overrides
    }
  }
}

test('实时成功 launch 会生成可去重的预览目标', () => {
  const target = workflowPreviewTarget(previewWorkflow(), true)

  assert.equal(target?.url, 'http://127.0.0.1:3000')
  assert.equal(target?.key, 'thread-1:run-1:http://127.0.0.1:3000')
})

test('历史、失败、非启动阶段和缺少地址的 Workflow 不触发预览', () => {
  assert.equal(workflowPreviewTarget(previewWorkflow(), false), undefined)
  assert.equal(workflowPreviewTarget(previewWorkflow({ status: 'failed' }), true), undefined)
  assert.equal(
    workflowPreviewTarget(previewWorkflow({ phase: 'integration_test' }), true),
    undefined
  )
  assert.equal(workflowPreviewTarget(previewWorkflow({ previewUrl: '' }), true), undefined)
})

test('不同运行返回相同 URL 时仍生成不同的一次性目标', () => {
  const first = workflowPreviewTarget(previewWorkflow({}, 'run-1'), true)
  const second = workflowPreviewTarget(previewWorkflow({}, 'run-2'), true)

  assert.notEqual(first?.key, second?.key)
  assert.equal(first?.url, second?.url)
})

test('重复地址不追加历史，新地址会截断旧前进记录', () => {
  const initial = {
    history: ['https://first.example', 'https://second.example'],
    index: 0
  }
  const duplicate = navigatePreviewHistory(initial, 'https://first.example')
  const next = navigatePreviewHistory(initial, '127.0.0.1:3000')

  assert.equal(duplicate, initial)
  assert.deepEqual(next, {
    history: ['https://first.example', 'http://127.0.0.1:3000'],
    index: 1
  })
})
