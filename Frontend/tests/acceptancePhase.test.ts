import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { test } from 'node:test'
import {
  derivePlanExecutionMode,
  type PlanExecutionMode
} from '../src/renderer/src/components/AiChatPanel/planExecutionMode'
import {
  currentRoundAcceptancePassed,
  shouldShowAcceptanceDecisionDock,
  workflowPreviewTarget,
  workflowShouldShowCodeReview,
  workflowShouldShowProjectLaunch
} from '../src/renderer/src/components/AiChatPanel/utils'
import { workflowClarification } from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/workflowClarification'
import { projectLaunchProgress } from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/projectLaunchProgress'
import {
  preparePhaseTransitionSession,
  sessionsForWorkbenchPhase
} from '../src/renderer/src/components/AiChatPanel/hooks/phaseSessionSelection'
import { DEFAULT_PREVIEW_ASSISTANT_PANEL_RATIO } from '../src/renderer/src/components/AiChatPanel/constants'
import { acceptancePreviewCanFocus } from '../src/renderer/src/components/AiChatPanel/globalFallbackState'
import { phaseEntryRightPanelLayout } from '../src/renderer/src/components/AiChatPanel/hooks/useAssistantPreviewLayout'
import type { RightPanelLayout } from '../src/renderer/src/components/AiChatPanel/types'
import { revisionStartTarget } from '../src/renderer/src/components/AiChatPanel/hooks/revisionSession'
import type {
  ApplicationLifecycle,
  WorkbenchExecution,
  WorkflowLaunchProgress,
  WorkflowRunPayload
} from '../src/renderer/src/typings'
import {
  deriveWorkbenchPhase,
  isObjectEditableInPhase,
  resolveWorkbenchPhase,
  WORKBENCH_PHASE_AGENTS,
  workbenchPhaseForNode
} from '../src/renderer/src/workbenchPhase'

/** 构造验收阶段测试所需的最小生命周期执行。 */
function acceptanceExecution(overrides: Partial<WorkbenchExecution> = {}): WorkbenchExecution {
  return {
    scope: 'page',
    targetId: 'orders',
    pageId: 'orders',
    threadId: 'acceptance-thread',
    runId: 'acceptance-run',
    phase: 'acceptance',
    status: 'awaiting_user',
    startedAt: '2026-08-26T00:00:00Z',
    updatedAt: '2026-08-26T00:00:00Z',
    ...overrides
  }
}

/** 构造验收等待态 Workflow 快照。 */
function acceptanceWorkflow(): WorkflowRunPayload {
  return {
    runId: 'acceptance-run',
    threadId: 'acceptance-thread',
    events: [],
    summary: {
      phase: 'acceptance',
      status: 'requires_user_input',
      previewUrl: 'http://127.0.0.1:3000',
      codeReviewResult: {
        status: 'completed',
        issueCount: 0,
        truncated: false,
        loadedSkills: [],
        targets: [],
        issues: []
      }
    }
  }
}

test('验收阶段节点归属、Agent 身份和编辑权限正确', () => {
  assert.equal(workbenchPhaseForNode('acceptance_phase_confirmation', 'development'), 'review')
  assert.equal(workbenchPhaseForNode('launch_project', 'review'), 'acceptance')
  assert.equal(workbenchPhaseForNode('acceptance', 'review'), 'acceptance')
  assert.equal(workbenchPhaseForNode('finalize_project', 'review'), 'acceptance')
  assert.equal(WORKBENCH_PHASE_AGENTS.acceptance.role, '验收 Agent')
  assert.equal(isObjectEditableInPhase('acceptance', 'acceptance'), true)
  assert.equal(isObjectEditableInPhase('acceptance', 'review'), false)
})

test('验收 execution 驱动顶部阶段和待交互模式', () => {
  const lifecycle: ApplicationLifecycle = {
    application: { id: 'app-1', name: '验收应用' },
    updatedAt: '2026-08-26T00:00:00Z',
    revision: 1,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: { 'acceptance-run': acceptanceExecution() },
    extensions: {}
  }
  assert.equal(deriveWorkbenchPhase(lifecycle), 'acceptance')
  const mode: PlanExecutionMode = derivePlanExecutionMode(
    acceptanceExecution({
      phase: 'acceptance_phase_confirmation',
      pendingInteraction: {
        id: 'acceptance-confirmation',
        type: 'acceptance_phase_confirmation',
        basedOnRevision: 1,
        payload: {},
        artifactRefs: [],
        createdAt: '2026-08-26T00:00:00Z'
      }
    })
  )
  assert.equal(mode, 'awaiting_acceptance_phase_confirmation')
})

test('验收等待态自动生成预览目标且不展示代码审查结果', () => {
  const workflow = acceptanceWorkflow()
  assert.equal(workflowShouldShowCodeReview(workflow), false)
  assert.equal(workflowShouldShowProjectLaunch(workflow, 'acceptance'), false)
  assert.deepEqual(workflowPreviewTarget(workflow, true), {
    key: 'acceptance-thread:acceptance-run:http://127.0.0.1:3000',
    url: 'http://127.0.0.1:3000'
  })
})

test('验收通过后不再展示底部验收操作', () => {
  const decide = (acceptancePassed: boolean): boolean =>
    shouldShowAcceptanceDecisionDock({
      activePhase: 'acceptance',
      planExecutionMode: 'awaiting_acceptance',
      acceptancePassed
    })

  assert.equal(decide(false), true, '等待验收且尚未通过时应展示两个验收按钮')
  assert.equal(decide(true), false, '生命周期已验收通过时必须移除底部验收按钮')
  assert.equal(
    shouldShowAcceptanceDecisionDock({
      activePhase: 'review',
      planExecutionMode: 'awaiting_acceptance',
      acceptancePassed: false
    }),
    false,
    '非验收阶段不得展示验收底栏'
  )
})

test('新执行和正式修改不展示旧验收结果，只读结束仍可保留原结果', () => {
  const lifecycle = { extensions: { acceptanceStatus: 'passed' }, activeExecutions: {} } as ApplicationLifecycle
  assert.equal(currentRoundAcceptancePassed(lifecycle), true)
  assert.equal(currentRoundAcceptancePassed({ ...lifecycle, activeExecutions: {
    run: acceptanceExecution({ phase: 'execute_frontend', status: 'running' })
  } }), false)
  assert.equal(currentRoundAcceptancePassed({ ...lifecycle, pendingRevisionImpact: { interactionId: 'impact' } }), false)
  assert.equal(currentRoundAcceptancePassed({ ...lifecycle, extensions: { acceptanceStatus: 'pending' } }), false)
})

test('空目标不会遮蔽确认卡绑定目标，页面目标不随右侧选择改变', () => {
  const workflow = acceptanceWorkflow()
  workflow.summary.target = {}
  workflow.state = { change_target: {} }
  assert.deepEqual(revisionStartTarget(workflow), { type: 'application' })
  workflow.summary.revisionImpact = { interactionId: 'impact' } as NonNullable<typeof workflow.summary.revisionImpact>
  const lifecycle = { pendingRevisionImpact: { interactionId: 'impact', target: { type: 'page', pageId: 'journey' } } } as ApplicationLifecycle
  assert.deepEqual(revisionStartTarget(workflow, lifecycle), { type: 'page', pageId: 'journey' })
  workflow.summary.target = { type: 'endpoint', apiContractId: 'api', endpointId: 'get' }
  assert.deepEqual(revisionStartTarget(workflow), workflow.summary.target)
})

test('验收启动中无需等待 launchResult 即展示项目启动步骤', () => {
  const workflow = acceptanceWorkflow()
  workflow.summary.status = 'running'
  workflow.summary.previewUrl = undefined
  workflow.summary.launchResult = undefined
  workflow.events = [
    {
      type: 'workflow.node.progress',
      nodeName: 'launch_project',
      data: {
        launchProgress: {
          stage: 'backend',
          status: 'running',
          message: '正在启动后端服务'
        }
      }
    }
  ]
  assert.equal(workflowShouldShowProjectLaunch(workflow, 'acceptance'), true)
  assert.equal(workflowShouldShowProjectLaunch(workflow, 'review'), false)
  assert.equal(workflowShouldShowCodeReview(workflow), false)
})

test('首次从审查进入验收先展示会话和启动步骤，待验收后才全屏预览', async () => {
  let layout: RightPanelLayout = 'full'
  let mode: PlanExecutionMode = 'awaiting_acceptance_phase_confirmation'
  const workflow = acceptanceWorkflow()
  workflow.summary.status = 'running'
  // 即使审查阶段已打开旧预览，也不能将它视为当前验收已经就绪。
  assert.equal(acceptancePreviewCanFocus('review', true, false, mode), false)
  const session = await preparePhaseTransitionSession(
    async () => ({ id: 'first-acceptance-session', workbenchPhase: 'acceptance' as const }),
    () => {
      layout = phaseEntryRightPanelLayout('acceptance', layout)
      mode = 'idle'
    }
  )
  assert.equal(session.workbenchPhase, 'acceptance')
  assert.equal(layout, 'split')
  assert.equal(acceptancePreviewCanFocus('acceptance', true, false, mode), false)

  mode = derivePlanExecutionMode(acceptanceExecution({ status: 'running' }))
  for (const stage of ['structure', 'backend', 'frontend', 'ready'] as const) {
    const progress: WorkflowLaunchProgress = {
      stage,
      status: stage === 'ready' ? 'completed' : 'running'
    }
    workflow.summary.launchProgress = progress
    assert.equal(workflowShouldShowProjectLaunch(workflow, 'acceptance'), true)
    assert.deepEqual(projectLaunchProgress(workflow), progress)
    assert.equal(workflowPreviewTarget(workflow, true), undefined)
    assert.equal(acceptancePreviewCanFocus('acceptance', true, false, mode), false)
  }

  workflow.summary.status = 'requires_user_input'
  mode = derivePlanExecutionMode(
    acceptanceExecution({
      pendingInteraction: {
        id: 'first-page-acceptance',
        type: 'page_acceptance',
        basedOnRevision: 1,
        payload: {},
        artifactRefs: [],
        createdAt: '2026-10-16T00:00:00Z'
      }
    })
  )
  assert.equal(mode, 'awaiting_acceptance')
  assert.ok(workflowPreviewTarget(workflow, true))
  assert.equal(acceptancePreviewCanFocus('acceptance', true, false, mode), true)
})

test('恢复验收时按当前执行状态显示步骤或全屏，不沿用旧预览的完成状态', () => {
  const awaiting = acceptanceExecution({
    pendingInteraction: {
      id: 'restored-page-acceptance',
      type: 'page_acceptance',
      basedOnRevision: 1,
      payload: {},
      artifactRefs: [],
      createdAt: '2026-10-16T00:00:00Z'
    }
  })
  const layout = phaseEntryRightPanelLayout('acceptance', 'hidden')
  assert.equal(layout, 'split')
  assert.equal(
    acceptancePreviewCanFocus('acceptance', true, false, derivePlanExecutionMode(awaiting)),
    true
  )
  for (const status of ['running', 'stopping', 'failed', 'stopped', 'completed'] as const) {
    const mode = derivePlanExecutionMode(acceptanceExecution({ status }))
    assert.equal(acceptancePreviewCanFocus('acceptance', true, false, mode), false)
  }
  assert.equal(phaseEntryRightPanelLayout('development', 'full'), 'full')
})

test('项目启动进度选择后续子步骤，不被旧工程识别事件覆盖', () => {
  const workflow = acceptanceWorkflow()
  workflow.summary.status = 'running'
  workflow.summary.launchProgress = {
    stage: 'backend',
    status: 'completed',
    message: '后端服务已就绪'
  }
  workflow.state = {
    launchProgress: {
      stage: 'frontend',
      status: 'running',
      message: '正在启动前端服务'
    }
  }
  workflow.events = [
    {
      type: 'workflow.node.progress',
      nodeName: 'launch_project',
      data: {
        launchProgress: {
          stage: 'structure',
          status: 'running',
          message: '正在识别工程结构'
        }
      }
    }
  ]

  assert.deepEqual(projectLaunchProgress(workflow), workflow.state.launchProgress)
})

test('验收阶段允许用户手动返回审查或测试阶段', () => {
  assert.equal(resolveWorkbenchPhase('acceptance', 'review'), 'review')
  assert.equal(resolveWorkbenchPhase('acceptance', 'test'), 'test')
  assert.equal(resolveWorkbenchPhase('acceptance', null), 'acceptance')
})

test('验收确认 phase 覆盖流式快照中遗留的进入审查确认', () => {
  const workflow = acceptanceWorkflow()
  workflow.summary.phase = 'acceptance_phase_confirmation'
  workflow.summary.status = 'requires_user_input'
  workflow.summary.clarification = {
    mode: 'review_phase_confirmation',
    status: 'requires_user_input',
    questions: []
  }
  workflow.state = {
    clarification: {
      mode: 'acceptance_phase_confirmation',
      status: 'requires_user_input',
      questions: []
    }
  }

  assert.equal(workflowClarification(workflow)?.mode, 'acceptance_phase_confirmation')
})

test('会话恢复只保留当前工作台阶段，审查确认不会混入验收阶段', () => {
  const sessions = [
    { id: 'test-session', workbenchPhase: 'test' as const },
    { id: 'review-session', workbenchPhase: 'review' as const },
    { id: 'acceptance-session', workbenchPhase: 'acceptance' as const }
  ]

  assert.deepEqual(sessionsForWorkbenchPhase(sessions, 'review'), [sessions[1]])
  assert.deepEqual(sessionsForWorkbenchPhase(sessions, 'acceptance'), [sessions[2]])
})

test('验收底栏参与布局并允许内容变高，避免遮挡预览', () => {
  const panelStyles = readFileSync(
    path.join(process.cwd(), 'src/renderer/src/components/AiChatPanel/AiChatPanel.less'),
    'utf8'
  )
  const dockStyles = readFileSync(
    path.join(
      process.cwd(),
      'src/renderer/src/components/AiChatPanel/components/AcceptanceDecisionDock.less'
    ),
    'utf8'
  )

  assert.match(panelStyles, /browser-preview-panel\s*\{\s*flex:\s*1;\s*height:\s*auto/)
  assert.match(dockStyles, /max-height:\s*55%/)
  assert.match(dockStyles, /overflow-y:\s*auto/)
  assert.doesNotMatch(dockStyles, /position:\s*absolute/)
})

test('验收 Agent 头像拥有可见的阶段背景', () => {
  const messageListStyles = readFileSync(
    path.join(
      process.cwd(),
      'src/renderer/src/components/AiChatPanel/components/MessageList/MessageList.less'
    ),
    'utf8'
  )

  assert.match(
    messageListStyles,
    /\.@\{class-prefix\}-ai-message-agent\.@\{class-prefix\}-acceptance\s+\.@\{class-prefix\}-ai-message-agent-avatar\s*\{[^}]*background:/s
  )
})

test('验收预览占满主区域，普通预览首次打开时右侧占六成', () => {
  const panelStyles = readFileSync(
    path.join(process.cwd(), 'src/renderer/src/components/AiChatPanel/AiChatPanel.less'),
    'utf8'
  )

  assert.equal(DEFAULT_PREVIEW_ASSISTANT_PANEL_RATIO, 0.4)
  assert.match(
    panelStyles,
    /acceptance-preview-focus[\s\S]*ai-chat-assistant,[\s\S]*panel-split-handle\s*\{\s*display:\s*none/
  )
  assert.match(
    panelStyles,
    /acceptance-preview-focus[\s\S]*embedded-preview-pane\s*\{[^}]*width:\s*100%/
  )
})

test('阶段交接等待会话创建完成后才切换阶段', async () => {
  const events: string[] = []
  let finishCreation: (() => void) | undefined
  const creationPending = new Promise<void>((resolve) => {
    finishCreation = resolve
  })

  const transition = preparePhaseTransitionSession(
    async () => {
      events.push('session:create')
      await creationPending
      events.push('session:ready')
      return 'acceptance-session'
    },
    () => {
      events.push('phase:acceptance')
    }
  )

  await Promise.resolve()
  assert.deepEqual(events, ['session:create'])
  finishCreation?.()

  assert.equal(await transition, 'acceptance-session')
  assert.deepEqual(events, ['session:create', 'session:ready', 'phase:acceptance'])
})
