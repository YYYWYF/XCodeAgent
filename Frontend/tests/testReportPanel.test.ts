import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement, useCallback, useEffect, useRef, type ReactElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { useAssistantPreviewLayout } from '../src/renderer/src/components/AiChatPanel/hooks/useAssistantPreviewLayout'
import { useTestReportPanel } from '../src/renderer/src/components/AiChatPanel/hooks/useTestReportPanel'
import type {
  RightPanelLayout,
  RightPanelState
} from '../src/renderer/src/components/AiChatPanel/types'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'

/** 构造与真实测试会话一致的集成测试及审查确认门投影。 */
function reportWorkflow(runId: string, completed: boolean): WorkflowRunPayload {
  const phase = completed ? 'review_phase_confirmation' : 'integration_test'
  const testReportResult = completed ? { reportPath: '.devagentstudio/reports/test-report.md' } : {}
  return {
    runId,
    threadId: 'test-thread',
    summary: { phase, status: completed ? 'requires_user_input' : 'running', testReportResult },
    state: { phase, testReportResult },
    result: { phase, testReportResult },
    events: [
      { type: 'workflow.node.started', node: { id: 'integration_test' } },
      ...(completed ? [{ type: 'workflow.node.completed', node: { id: 'integration_test' } }] : [])
    ]
  }
}

type ProbeProps = {
  workflow: WorkflowRunPayload
  initialLayout?: RightPanelLayout
  resetOnCompletion?: boolean
}

type PanelActions = {
  select: (type: 'source' | 'outline') => void
  close: () => void
}

/** 使用生产布局及报告 hook 执行真实 React effect，不模拟 hook 或状态调度。 */
async function reportHarness(
  run: (
    render: (props: ProbeProps) => Promise<void>,
    panel: () => Record<string, unknown>,
    actions: () => PanelActions
  ) => Promise<void>
): Promise<void> {
  const savedWindow = globalThis.window
  const savedFetch = globalThis.fetch
  let root: ReactTestRenderer | undefined
  let controls!: PanelActions
  let version = 0
  const fakeWindow = Object.assign(new EventTarget(), {
    devAgentStudio: { agentBaseUrl: 'http://test-report.local' }
  })
  Object.defineProperty(globalThis, 'window', { configurable: true, value: fakeWindow })
  globalThis.fetch = async (url) => {
    assert.equal(String(url), 'http://test-report.local/tools/file/read')
    return Response.json({ content: `报告版本 ${++version}` })
  }

  /** 将状态暴露为可断言的渲染结果，并模拟同一提交周期中的默认面板回退。 */
  function Probe({
    workflow,
    initialLayout = 'split',
    resetOnCompletion
  }: ProbeProps): ReactElement {
    const layout = useAssistantPreviewLayout()
    const { setRightPanel, setRightPanelLayout } = layout
    const initialLayoutRef = useRef(initialLayout)
    useEffect(() => {
      setRightPanel({ type: 'outline' })
      setRightPanelLayout(initialLayoutRef.current)
    }, [setRightPanel, setRightPanelLayout])
    const ensureRightPanelVisible = useCallback(() => {
      setRightPanelLayout((current) => (current === 'hidden' ? 'split' : current))
    }, [setRightPanelLayout])
    const report = useTestReportPanel({
      applicationId: 'report-application',
      activeWorkflowPhase: String(workflow.summary.phase),
      isApplicationPlanningPhase: false,
      reportPhaseActive: true,
      workflow,
      workspaceRoot: '/workspace/report',
      rightPanel: layout.rightPanel,
      rightPanelOpen: layout.rightPanelLayout !== 'hidden',
      ensureRightPanelVisible,
      setRightPanel
    })
    useEffect(() => {
      if (resetOnCompletion && workflow.summary.phase === 'review_phase_confirmation') {
        setRightPanel({ type: 'outline' })
      }
    }, [resetOnCompletion, setRightPanel, workflow])
    controls = {
      select: (type) => setRightPanel({ type } as RightPanelState),
      close: () => {
        setRightPanel(undefined)
        setRightPanelLayout('hidden')
      }
    }
    return createElement('output', {
      panel: layout.rightPanel?.type,
      layout: layout.rightPanelLayout,
      content: report.content,
      loading: report.loading,
      error: report.error
    })
  }

  try {
    await run(
      async (props) => {
        await act(async () => {
          if (root) root.update(createElement(Probe, props))
          else root = create(createElement(Probe, props))
        })
      },
      () => root!.root.findByType('output').props,
      () => controls
    )
  } finally {
    await act(async () => {
      root?.unmount()
    })
    globalThis.fetch = savedFetch
    Object.defineProperty(globalThis, 'window', { configurable: true, value: savedWindow })
  }
}

test('真实报告 hook 在同批面板回退后仍完成测试报告跳转', async () => {
  await reportHarness(async (render, panel, actions) => {
    await render({ workflow: reportWorkflow('report-run', false), resetOnCompletion: true })
    assert.equal(panel().panel, 'outline')
    await render({ workflow: reportWorkflow('report-run', true), resetOnCompletion: true })
    assert.equal(panel().panel, 'test-report')
    assert.equal(panel().loading, false)
    assert.equal(panel().error, '')
    await act(async () => actions().select('source'))
    assert.equal(panel().panel, 'source')
  })
})

test('真实报告 hook 展开隐藏面板，用户关闭后不重开，新运行完成才再次打开', async () => {
  await reportHarness(async (render, panel, actions) => {
    await render({ workflow: reportWorkflow('report-run', false), initialLayout: 'hidden' })
    const completed = reportWorkflow('report-run', true)
    await render({ workflow: completed })
    assert.equal(panel().layout, 'split')
    assert.equal(panel().panel, 'test-report')
    await act(async () => actions().close())
    await render({ workflow: { ...completed } })
    assert.equal(panel().layout, 'hidden')
    await render({ workflow: reportWorkflow('report-run-2', true) })
    assert.equal(panel().layout, 'split')
    assert.equal(panel().panel, 'test-report')
  })
})

test('真实报告 hook 对同路径的新测试读取新内容且运行中不导航', async () => {
  await reportHarness(async (render, panel, actions) => {
    await render({ workflow: reportWorkflow('report-run', true) })
    const previous = panel().content
    await act(async () => actions().select('source'))
    await render({ workflow: reportWorkflow('report-run-2', false) })
    assert.equal(panel().panel, 'source')
    await render({ workflow: reportWorkflow('report-run-2', true) })
    assert.equal(panel().panel, 'test-report')
    assert.notEqual(panel().content, previous)
  })
})

test('报告投影暂时缺失再恢复时重新导航，不能停留在开发产物', async () => {
  await reportHarness(async (render, panel, actions) => {
    const completed = reportWorkflow('report-run', true)
    await render({ workflow: completed })
    assert.equal(panel().panel, 'test-report')
    await render({ workflow: {
      ...completed,
      summary: { ...completed.summary, testReportResult: {} },
      state: { phase: 'review_phase_confirmation' },
      result: { phase: 'review_phase_confirmation' }
    } })
    assert.equal(panel().panel, 'outline')
    await render({ workflow: completed })
    assert.equal(panel().panel, 'test-report')
    await act(async () => actions().select('source'))
    await render({ workflow: { ...completed } })
    assert.equal(panel().panel, 'source')
  })
})

test('同一运行重新进入集成测试后，完成时再次聚焦报告', async () => {
  await reportHarness(async (render, panel, actions) => {
    await render({ workflow: reportWorkflow('report-run', true) })
    await act(async () => actions().select('source'))
    await render({ workflow: reportWorkflow('report-run', false) })
    assert.equal(panel().panel, 'source')
    await render({ workflow: reportWorkflow('report-run', true) })
    assert.equal(panel().panel, 'test-report')
  })
})
