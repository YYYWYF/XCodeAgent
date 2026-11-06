import assert from 'node:assert/strict'
import { createElement } from 'react'
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer'
import UiDesignConfirmationPanel from '../src/renderer/src/components/Welcome/UiDesignConfirmationPanel'
import type { DesignPreviewPage } from '../src/renderer/src/components/DesignRenderer/projectPreview'
import type { WorkflowClarificationAnswers, WorkflowRunPayload } from '../src/renderer/src/typings'

/** 构造后台刚完成的新资产；旧快照继续持有 queued 和旧修订。 */
function page(revision: string, status: string): DesignPreviewPage {
  return {
    pageId: 'chat',
    status,
    design_asset: {
      kind: 'agent-ui-project',
      revision,
      draft: revision,
      sourceDigest: revision,
      buildDigest: revision,
      pageIds: ['chat']
    }
  }
}

/** 使用真实面板消费的 clarification，而不在测试中改写工作流状态。 */
function workflow(pages: DesignPreviewPage[], runId: string): WorkflowRunPayload {
  return {
    runId,
    threadId: 'ui-review',
    events: [],
    state: {},
    result: {},
    summary: {
      status: 'requires_user_input',
      clarification: {
        mode: 'ui_design_confirmation',
        status: 'requires_user_input',
        pages
      }
    }
  } as WorkflowRunPayload
}

const previousWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
const previousDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')
const intervals = new Set<() => void>()
const submissions: WorkflowClarificationAnswers[] = []
const disk = [page('new', 'ready')]
let root: ReactTestRenderer | undefined

/** 无 DOM 的 renderer 不分发浏览器可见性事件。 */
function ignoreDomEvent(): undefined {
  return undefined
}

Object.defineProperty(globalThis, 'document', {
  configurable: true,
  value: {
    visibilityState: 'visible',
    addEventListener: ignoreDomEvent,
    removeEventListener: ignoreDomEvent
  }
})
Object.defineProperty(globalThis, 'window', {
  configurable: true,
  value: {
    devAgentStudio: { workspace: { readUiDesigns: async () => ({ uiDesigns: { pages: disk } }) } },
    addEventListener: ignoreDomEvent,
    removeEventListener: ignoreDomEvent,
    setTimeout,
    clearTimeout,
    setInterval(callback: () => void): () => void {
      intervals.add(callback)
      return callback
    },
    clearInterval(callback: () => void): void {
      intervals.delete(callback)
    }
  }
})

/** 捕获真实面板发出的结构化动作，故意延迟服务端新快照。 */
function onSubmit(_workflow: WorkflowRunPayload, answers: WorkflowClarificationAnswers): void {
  submissions.push(answers)
}

/** 从真实按钮读取下一步状态，避免只验证内部派生函数。 */
function nextButton(): ReactTestInstance {
  assert.ok(root)
  return root.root.findAllByType('button').find((button) => button.props.children === '下一步')!
}

try {
  await act(async () => {
    root = create(
      createElement(UiDesignConfirmationPanel, {
        workflow: workflow([page('old', 'queued')], 'old-run'),
        workspaceRoot: '/ui-review-test',
        onSubmit,
        showPreview: false
      })
    )
  })
  assert.equal(submissions.length, 1)
  assert.equal(
    submissions[0].__applicationPlanningAction,
    'ui_action',
    '生成结束必须显式恢复 Graph，空答案只会只读对账'
  )
  assert.deepEqual(submissions[0].ui_design_action, { action: 'refresh' })
  assert.equal(nextButton().props.disabled, true, '新稿显示就绪仍不能确认旧工作流版本')

  await act(async () => {
    root!.update(
      createElement(UiDesignConfirmationPanel, {
        workflow: workflow(disk, 'fresh-run'),
        workspaceRoot: '/ui-review-test',
        onSubmit,
        showPreview: false
      })
    )
  })
  assert.equal(nextButton().props.disabled, false)
  assert.equal(submissions.length, 1, '刷新完成不能自动确认产物')
  await act(async () => {
    nextButton().props.onClick()
  })
  assert.equal(submissions[1].__applicationPlanningAction, 'confirm')

  await act(async () => {
    root!.unmount()
  })
  submissions.length = 0
  const readyButOld = workflow([page('old', 'ready')], 'old-ready-run')
  await act(async () => {
    root = create(
      createElement(UiDesignConfirmationPanel, {
        workflow: readyButOld,
        workspaceRoot: '/ui-review-test',
        onSubmit,
        showPreview: false,
        disabled: true
      })
    )
  })
  assert.equal(submissions.length, 0, '正在执行其他动作时不得并发刷新')
  assert.equal(nextButton().props.disabled, true)
  await act(async () => {
    root!.update(
      createElement(UiDesignConfirmationPanel, {
        workflow: readyButOld,
        workspaceRoot: '/ui-review-test',
        onSubmit,
        showPreview: false
      })
    )
    for (const poll of intervals) poll()
  })
  assert.equal(submissions.length, 1, '即使前后都 ready，资产变化仍需刷新；被拦下的刷新必须重试')
  assert.deepEqual(submissions[0].ui_design_action, { action: 'refresh' })
  assert.equal(nextButton().props.disabled, true)

  await act(async () => {
    root!.unmount()
  })
  submissions.length = 0
  await act(async () => {
    root = create(
      createElement(UiDesignConfirmationPanel, {
        workflow: workflow(disk, 'matching-run'),
        workspaceRoot: '/ui-review-test',
        onSubmit,
        showPreview: false
      })
    )
  })
  assert.equal(submissions.length, 0, '已同步的终态不能在挂载时反复启动 Graph')
  assert.equal(nextButton().props.disabled, false)
} finally {
  await act(async () => {
    root?.unmount()
  })
  if (previousWindow) Object.defineProperty(globalThis, 'window', previousWindow)
  else Reflect.deleteProperty(globalThis, 'window')
  if (previousDocument) Object.defineProperty(globalThis, 'document', previousDocument)
  else Reflect.deleteProperty(globalThis, 'document')
}

console.log('UI design review synchronization passed')
