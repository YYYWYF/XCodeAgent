import assert from 'node:assert/strict'
import { createElement } from 'react'
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer'
import AgentUiPreparationReview from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/AgentUiPreparationReview'
import type { AgentUiDependencyPlan } from '../src/renderer/src/service/agentUiDependencies'
import type { AgentUiTransferPlan } from '../src/renderer/src/service/agentUiDesignTransfer'
import type {
  WorkflowAgentUiPreparation,
  WorkflowAgentUiPreparationAction
} from '../src/renderer/src/typings/workflow'

const dependency: AgentUiDependencyPlan = {
  planId: 'a'.repeat(64),
  reviewId: 'b'.repeat(32),
  source: 'local-vendor',
  sourceDependencies: [
    { name: 'lucide-react', version: '1.41.0' },
    { name: 'zod', version: '4.5.4' }
  ],
  manager: 'pnpm',
  managerVersion: '11.9.0',
  lockfile: 'pnpm-lock.yaml',
  packages: [
    {
      name: '@za38.03/agent-ui-core',
      version: '0.0.1',
      specifier: 'file:reviewed.tgz',
      integrity: 'reviewed'
    }
  ],
  changes: [
    {
      path: 'src/agent-ui-package.css',
      beforeSha256: null,
      after: 'reviewed style contents',
      afterSha256: 'c'.repeat(64)
    }
  ],
  checks: ['build'],
  recovery: '失败保留文件与日志'
}

/** 提供当前正式转入计划，避免将初始化确认伪装为已经完成设计转入。 */
function transferPlan(required: boolean): AgentUiTransferPlan {
  return {
    planId: (required ? 'd' : 'e').repeat(64),
    readOnly: true,
    executionAvailable: true,
    workspace: '/current-workspace',
    status: 'reviewable',
    blockers: [],
    conflicts: [],
    source: {
      projectRoot: '/design-project',
      draft: 'draft',
      revision: 'reviewed-revision',
      sourceRoot: 'src/agent-ui',
      sourceDigest: 'source-digest',
      buildDigest: 'build-digest',
      controls: {}
    },
    target: {
      projectRoot: '/current-workspace/frontend',
      sourceRoot: 'src/正式助手',
      snapshotSha256: 'snapshot'
    },
    formal: {},
    installation: { planId: 'f'.repeat(64), evidence: {}, styles: [] },
    publicApi: { packages: [], catalogSha256: 'catalog', sharedApiSha256: 'api' },
    tool: { creatorVersion: '0.0.1', installedDigest: 'tool', hostIntegrationSha256: 'host' },
    hostIntegration: {
      status: 'reviewable',
      reason: '页面静态开发可继续，真实发送仍待独立验收。',
      connection: 'blocked_T27'
    },
    changes: [
      {
        sourcePath: 'src/agent-ui/app-ui.json',
        targetPath: 'src/正式助手/app-ui.json',
        operation: 'add',
        sourceSha256: 'source',
        beforeSha256: null,
        afterSha256: 'after',
        after: 'reviewed model',
        diff: 'reviewed design diff',
        importAdjustments: []
      },
      {
        sourcePath: 'src/agent-ui/unchanged.json',
        targetPath: 'src/正式助手/unchanged.json',
        operation: 'unchanged',
        sourceSha256: 'same',
        beforeSha256: 'same',
        afterSha256: 'same',
        after: 'unchanged contents',
        diff: '',
        importAdjustments: []
      }
    ],
    excluded: [],
    protected: {},
    generatedReferences: [],
    initialization: {
      required,
      requiresReplan: required,
      owner: 'target-public-tool',
      input: { sourceRoot: 'src/正式助手', mode: 'platform' }
    },
    validation: ['typecheck', 'build'],
    recovery: '不自动重放未知结果'
  }
}

const workflow = {
  runId: 'current-main-workflow-run',
  threadId: 'current-main-thread',
  pageId: 'chat_assistant'
}
const submissions: Array<{
  workflow: typeof workflow
  answers: { agent_ui_preparation: WorkflowAgentUiPreparationAction }
}> = []
const originalFetch = globalThis.fetch
let networkCalls = 0
let root: ReactTestRenderer | undefined
globalThis.fetch = async (): Promise<Response> => {
  networkCalls += 1
  throw new Error('The review component must continue the main workflow through its callback.')
}

/** 主工作流边界绑定当前审阅身份；计划或阶段变化时重新挂载以撤销旧勾选。 */
function render(preparation?: WorkflowAgentUiPreparation, disabled = false): void {
  const component = createElement(AgentUiPreparationReview, {
    key: `${preparation?.stage}:${preparation?.plan?.planId || workflow.runId}`,
    preparation,
    disabled,
    onAction: (answer: WorkflowAgentUiPreparationAction): void => {
      submissions.push({ workflow, answers: { agent_ui_preparation: answer } })
    }
  })
  act(() => {
    if (root) root.update(component)
    else root = create(component)
  })
}

/** 使用真实按钮文本观察审阅门，Antd 替身只隔离 DOM 和动画边界。 */
function button(label: string): ReactTestInstance {
  assert.ok(root)
  const selected = root.root.findAllByType('button').find((item) => item.props.children === label)
  assert.ok(selected, `missing review button: ${label}`)
  return selected
}

/** 勾选本轮审阅，不复用前一计划或历史工作流的用户选择。 */
function review(): void {
  assert.ok(root)
  const checkbox = root.root.findByType('input')
  assert.equal(Boolean(checkbox.props.disabled), false)
  act(() => checkbox.props.onChange({ target: { checked: true } }))
}

/** 只操作已启用按钮；禁用态的内部确认也另行验证不会提交。 */
function click(label: string): void {
  const selected = button(label)
  assert.equal(Boolean(selected.props.disabled), false, `disabled review button: ${label}`)
  act(() => selected.props.onClick())
}

try {
  render({ stage: 'dependency', plan: dependency })
  assert.equal(button('确认依赖与样式准备').props.disabled, true)
  act(() => button('确认依赖与样式准备').props.onClick())
  assert.equal(
    submissions.length,
    0,
    'unchecked review must not submit even if a callback is invoked'
  )
  assert.match(JSON.stringify(root?.toJSON()), /pnpm/)
  assert.match(JSON.stringify(root?.toJSON()), /11\.9\.0/)
  assert.match(JSON.stringify(root?.toJSON()), /reviewed style contents/)
  review()
  click('确认依赖与样式准备')
  assert.deepEqual(submissions.at(-1), {
    workflow,
    answers: {
      agent_ui_preparation: {
        action: 'confirm',
        planId: dependency.planId,
        reviewId: dependency.reviewId
      }
    }
  })

  const nextDependency = { ...dependency, planId: '1'.repeat(64), reviewId: '2'.repeat(32) }
  render({ stage: 'dependency', plan: nextDependency })
  assert.equal(
    button('确认依赖与样式准备').props.disabled,
    true,
    'new plan identity must revoke previous review'
  )
  assert.equal(root?.root.findByType('input').props.checked, false)
  click('重新检查当前工程')
  assert.deepEqual(submissions.at(-1)?.answers, { agent_ui_preparation: { action: 'refresh' } })

  review()
  render({ stage: 'dependency', plan: nextDependency }, true)
  assert.equal(root?.root.findByType('input').props.disabled, true)
  assert.equal(button('确认依赖与样式准备').props.disabled, true)
  assert.equal(button('重新检查当前工程').props.disabled, true)
  const historyCount = submissions.length
  act(() => button('确认依赖与样式准备').props.onClick())
  assert.equal(
    submissions.length,
    historyCount,
    'disabled or historical review cannot submit a write'
  )

  const initialization = transferPlan(true)
  render({ stage: 'initialize', plan: initialization })
  assert.equal(button('确认初始化，随后重新审阅设计差异').props.disabled, true)
  assert.match(JSON.stringify(root?.toJSON()), /初始化后会再次展示/)
  review()
  click('确认初始化，随后重新审阅设计差异')
  assert.deepEqual(submissions.at(-1)?.answers, {
    agent_ui_preparation: {
      action: 'confirm',
      planId: initialization.planId,
      installationPlanId: initialization.installation.planId,
      targetSourceRoot: initialization.target.sourceRoot
    }
  })

  const transfer = transferPlan(false)
  render({ stage: 'transfer', plan: transfer })
  assert.equal(
    button('确认转入设计并继续页面开发').props.disabled,
    true,
    'initialization must not approve the later transfer'
  )
  assert.match(JSON.stringify(root?.toJSON()), /reviewed design diff/)
  assert.doesNotMatch(JSON.stringify(root?.toJSON()), /unchanged contents/)
  assert.match(JSON.stringify(root?.toJSON()), /真实发送仍待独立验收/)
  review()
  click('确认转入设计并继续页面开发')
  assert.deepEqual(submissions.at(-1)?.answers, {
    agent_ui_preparation: {
      action: 'confirm',
      planId: transfer.planId,
      installationPlanId: transfer.installation.planId,
      targetSourceRoot: transfer.target.sourceRoot
    }
  })

  render({
    stage: 'blocked',
    plan: { ...transfer, status: 'blocked', blockers: ['保留已有自定义配置'] },
    error: '当前计划需要重新审阅'
  })
  assert.equal(root?.root.findAllByType('input').length, 0)
  assert.equal(root?.root.findAllByType('button').length, 1)
  assert.match(JSON.stringify(root?.toJSON()), /保留已有自定义配置/)
  assert.match(JSON.stringify(root?.toJSON()), /当前计划需要重新审阅/)
  click('重新检查当前工程')
  render(undefined, true)
  assert.equal(root?.root.findAllByType('input').length, 0)
  assert.equal(button('重新检查当前工程').props.disabled, true)
  assert.equal(networkCalls, 0, 'the review must not invoke standalone install or transfer APIs')
  console.log(
    'Agent UI preparation review tests passed (main-workflow identity, review gate, plan reset, initialization, transfer, blocked/history).'
  )
} finally {
  act(() => root?.unmount())
  globalThis.fetch = originalFetch
}
