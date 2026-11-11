import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement, type ComponentProps } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Button, Input } from 'antd'
import UiDesignConfirmationPanel from '../src/renderer/src/components/Welcome/UiDesignConfirmationPanel'
import type { WorkflowClarificationAnswers, WorkflowRunPayload } from '../src/renderer/src/typings'
import { installRendererDom } from './rendererDomHarness'
import { useUiDesignReviewWorkflow } from '../src/renderer/src/components/AiChatPanel/components/MessageList/planningMessageWorkflow'

type Page = { pageId: string; name: string; status: string }

/** 沿消息列表的真实展示选择器挂载卡片，覆盖运行中快照曾导致的卸载重建。 */
function ReviewHost(props: ComponentProps<typeof UiDesignConfirmationPanel>) {
  const workflow = useUiDesignReviewWorkflow(props.workflow, props.workspaceRoot)
  return workflow ? createElement(UiDesignConfirmationPanel, { ...props, workflow }) : null
}

/** 挂载真实设计稿确认面板，只控制上游工作流回执，不替换按钮或提交逻辑。 */
async function withPanel(run: (context: {
  root: () => ReactTestRenderer
  submissions: WorkflowClarificationAnswers[]
  render: (pages: Page[], status?: string, disabled?: boolean, includePages?: boolean, phase?: string) => Promise<void>
}) => Promise<void>): Promise<void> {
  const restore = installRendererDom()
  // React Test Renderer 没有真实布局节点，尺寸观察器沿无 Element 宿主分支跳过测量。
  Reflect.deleteProperty(globalThis, 'Element')
  const createNode = document.createElement.bind(document)
  // 输入框自适应高度只需要固定尺寸宿主，不参与本次按钮和并发行为断言。
  document.createElement = ((tag: string) => Object.assign(createNode(tag), {
    removeAttribute: () => {}, scrollHeight: 24, style: {}
  })) as typeof document.createElement
  Object.assign(window, { getComputedStyle: () => ({
    getPropertyValue: (name: string) => name === 'box-sizing' ? 'border-box' : '0px'
  }) })
  // 不访问真实工作区；声明 IPC 可用，避免组件启用无 IPC 的刷新定时器。
  Object.assign(window, { devAgentStudio: { workspace: { readUiDesigns: async () => ({}) } } })
  const submissions: WorkflowClarificationAnswers[] = []
  let root: ReactTestRenderer | undefined
  /** 用真实状态帧驱动入队 Run 的开始和结束。 */
  const render = async (pages: Page[], status = 'requires_user_input', disabled = false, includePages = true,
    phase = 'ui_confirmation'): Promise<void> => {
    const workflow = {
      runId: 'design-run', threadId: 'design-thread',
      summary: { phase, status,
        clarification: includePages ? { mode: 'ui_design_confirmation', pages } : undefined }
    } as unknown as WorkflowRunPayload
    await act(async () => {
      const element = createElement(ReviewHost, { workflow, disabled, showPreview: false,
        onSubmit: (_workflow, answers) => { submissions.push(answers) } })
      if (root) root.update(element)
      else root = create(element, {
        /** 为真实 TextArea 的 ref 提供测量和选区所需的最小宿主。 */
        createNodeMock: () => Object.assign(new HTMLElement(), {
          getAttribute: () => null, value: '', placeholder: '', style: {},
          setSelectionRange: () => {}, focus: () => {}
        })
      })
    })
  }
  try { await run({ root: () => root!, submissions, render }) } finally {
    if (root) await act(async () => { root!.unmount() })
    restore()
  }
}

/** 按样式角色定位真实 Ant Design 按钮，避免依赖页面布局或组件内部状态。 */
function buttons(root: ReactTestRenderer, role: string) {
  return root.root.findAllByType(Button).filter((button) => String(button.props.className).includes(role))
}

test('部分已生成和未生成、失败、取消页面均允许下一步并提交显式确认', async () => {
  await withPanel(async ({ root, render, submissions }) => {
    for (const status of ['pending', 'generation_failed', 'cancelled']) {
      await render([{ pageId: 'a', name: '页面甲', status: 'confirmed' }, { pageId: 'b', name: '页面乙', status }])
      const next = buttons(root(), 'ui-design-confirm-all-btn')[0]
      assert.equal(Boolean(next.props.disabled), false)
      await act(async () => { next.props.onClick() })
      assert.equal(submissions.at(-1)?.__applicationPlanningAction, 'confirm')
      assert.doesNotMatch(JSON.stringify(root().toJSON()), /请先完成全部页面设计稿/)
    }
  })
})

test('单页入队 Run 的中间空快照保留同一个面板及修改意见，刷新不提交规划动作', async () => {
  await withPanel(async ({ root, render, submissions }) => {
    const pages = [{ pageId: 'a', name: '页面甲', status: 'pending' }]
    await render(pages)
    const original = root().root.findByType(UiDesignConfirmationPanel)
    await act(async () => { root().root.findByType(Input.TextArea).props.onChange({ target: { value: '保留修改意见' } }) })
    for (const phase of ['', 'ui_confirmation_review', 'ui_confirmation']) {
      await render([], 'running', true, false, phase)
      assert.equal(root().root.findByType(UiDesignConfirmationPanel), original)
      assert.equal(root().root.findByType(Input.TextArea).props.value, '保留修改意见')
    }
    await render([{ ...pages[0], status: 'queued' }])
    assert.equal(root().root.findByType(UiDesignConfirmationPanel), original)
    await act(async () => { buttons(root(), 'ui-design-refresh-btn')[0].props.onClick() })
    assert.equal(submissions.length, 0, '展示刷新不能通过 onSubmit 触发整轮规划同步')
    await render(pages, 'failed')
    assert.equal(root().root.findAllByType(UiDesignConfirmationPanel).length, 0, '真实失败不能被旧审阅卡遮住')
  })
})

test('排队、生成及停止未完成时禁止下一步，停止完成后可以继续', async () => {
  await withPanel(async ({ root, render, submissions }) => {
    for (const status of ['queued', 'generating', 'stopping']) {
      await render([{ pageId: 'a', name: '页面甲', status }, { pageId: 'b', name: '页面乙', status: 'pending' }])
      assert.equal(buttons(root(), 'ui-design-confirm-all-btn')[0].props.disabled, true)
      assert.equal(Boolean(buttons(root(), 'ui-design-action-btn-regenerate')[0].props.disabled), false)
      const before = submissions.length
      await act(async () => { buttons(root(), 'ui-design-confirm-all-btn')[0].props.onClick() })
      assert.equal(submissions.length, before)
    }
    await render([{ pageId: 'a', name: '页面甲', status: 'cancelled' }, { pageId: 'b', name: '页面乙', status: 'pending' }])
    const next = buttons(root(), 'ui-design-confirm-all-btn')[0]
    assert.equal(Boolean(next.props.disabled), false)
    await act(async () => { next.props.onClick() })
    assert.equal(submissions.length, 1)
    assert.equal(submissions[0].__applicationPlanningAction, 'confirm')
  })
})

test('连续提交三个页面，后两页在首个入队回执后提交，第四页等待空位', async () => {
  await withPanel(async ({ root, render, submissions }) => {
    const pages = ['a', 'b', 'c', 'd'].map((pageId) => ({ pageId, name: pageId, status: 'pending' }))
    await render(pages)
    for (let index = 0; index < 3; index++) {
      const generate = buttons(root(), 'ui-design-action-btn-regenerate')[0]
      assert.equal(Boolean(generate.props.disabled), false, `第 ${index + 1} 个页面应可生成`)
      await act(async () => { generate.props.onClick() })
    }
    assert.equal(submissions.length, 1, '同线程只串行提交入队 Run，不能并发写 checkpoint')
    assert.equal(root().root.findAllByType(Button).filter((button) => button.props.children === '停止').length, 3,
      '尚未入队的生成意图沿用原来的停止按钮名称')
    assert.doesNotMatch(JSON.stringify(root().toJSON()), /正在提交|取消提交/)
    // Tooltip 会把禁用按钮的样式转移到包装层，第四页按实际按钮文案定位。
    const fourth = root().root.findAllByType(Button).find((button) => button.props.children === '生成')!
    assert.equal(fourth.props.disabled, true,
      '三个名额占满后，第四页才禁用')
    const active = pages.map((page, index) => index === 0 ? { ...page, status: 'generating' } : page)
    await render(active, 'running')
    await render(active)
    assert.equal(submissions.length, 2, '首个入队 Run 完成后不等待其页面生成结束，就提交后两页')
    assert.deepEqual(submissions[1].ui_design_action, { action: 'multi', actions: [
      { pageId: 'b', action: 'regenerate', templateId: undefined },
      { pageId: 'c', action: 'regenerate', templateId: undefined }
    ] })
    const generating = pages.map((page, index) => index < 3 ? { ...page, status: 'generating' } : page)
    await render(generating, 'running')
    await render(generating)
    await render(generating.map((page, index) => index === 0 ? { ...page, status: 'confirmed' } : page))
    assert.equal(Boolean(buttons(root(), 'ui-design-action-btn-regenerate').at(-1)!.props.disabled), false,
      '任一页生成完成后，第四页立即获得空位')
  })
})
