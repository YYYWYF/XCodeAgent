import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { renderToStaticMarkup } from 'react-dom/server'
import { usePlanningDocumentView } from '../src/renderer/src/components/AiChatPanel/hooks/usePlanningDocumentView'
import DocPanel from '../src/renderer/src/components/AiChatPanel/components/DocPanel'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'
import type { ArtifactReference } from '../src/renderer/src/service/workflowArtifacts'

/** 为不同版本构造当前内容寻址引用，不使用文件路径或完整执行状态。 */
function reference(character: string): ArtifactReference {
  return { artifactId: character.repeat(64), byteLength: 100, mediaType: 'application/json' }
}

/** 构造与真实联合确认一致的仅含资料引用的 Workflow。 */
function workflow(character = 'a'): WorkflowRunPayload {
  return {
    runId: 'run',
    threadId: 'thread',
    events: [],
    summary: { status: 'requires_user_input', phase: 'requirement_document' },
    state: {
      artifactRefs: { requirement_spec: reference(character), product_plan: reference('b') }
    }
  }
}

type HookArguments = Parameters<typeof usePlanningDocumentView>
type View = ReturnType<typeof usePlanningDocumentView>

/** 用真实 React effect 驱动生产 hook，支持切换源状态和受控的迟到响应。 */
async function harness(
  read: NonNullable<HookArguments[3]>,
  run: (
    render: (
      source: WorkflowRunPayload,
      workspace?: string,
      kind?: HookArguments[2] | null
    ) => Promise<void>,
    view: () => View
  ) => Promise<void>
): Promise<void> {
  let root: ReactTestRenderer | undefined
  let current!: View
  /** 暴露局部读取结果，保持正文与输入 Workflow 分离。 */
  function Probe({
    source,
    workspace,
    kind
  }: {
    source: WorkflowRunPayload
    workspace: string
    kind: HookArguments[2]
  }): null {
    current = usePlanningDocumentView(source, workspace, kind, read)
    return null
  }
  try {
    await run(
      async (source, workspace = '/workspace/a', kind = 'requirement-doc') => {
        await act(async () => {
          const element = createElement(Probe, { source, workspace, kind: kind ?? undefined })
          if (root) root.update(element)
          else root = create(element)
        })
      },
      () => current
    )
  } finally {
    await act(async () => root?.unmount())
  }
}

test('右侧在确认前读取需求、产品规划与 Markdown；同引用快照不重读且不污染源状态', async () => {
  const calls: string[] = []
  const source = workflow()
  source.confirmationArtifact = {
    id: 'product_plan',
    name: 'product-plan.md',
    path: '/workspace/a/drafts/product-plan.md',
    format: 'markdown',
    content: '',
    contentRef: reference('c')
  }
  const serialized = JSON.stringify(source)
  await harness(
    async (_workspace, ref) => {
      calls.push(ref.artifactId)
      return ref.artifactId.startsWith('c')
        ? '# 完整需求文档'
        : { pages: [{ pageId: 'work_logs' }] }
    },
    async (render, view) => {
      await render(source)
      assert.equal(view().loading, false)
      assert.equal(view().error, undefined)
      assert.deepEqual(view().workflow?.state?.requirement_spec, {
        pages: [{ pageId: 'work_logs' }]
      })
      assert.equal(view().workflow?.confirmationArtifact?.content, '# 完整需求文档')
      assert.equal(JSON.stringify(source), serialized)
      await render({ ...source, summary: { ...source.summary, message: '同一版本快照' } })
      assert.equal(calls.length, 3)
      assert.equal(view().workflow?.summary.message, '同一版本快照')
    }
  )
})

test('读取失败显示错误并可重试，不拿旧正文冒充当前版本', async () => {
  let failing = true
  await harness(
    async () => {
      if (failing) throw new Error('引用的资料不存在')
      return { summary: '重新读取成功' }
    },
    async (render, view) => {
      await render(workflow())
      assert.match(view().error || '', /资料不存在/)
      assert.equal(view().loading, false)
      assert.equal(view().workflow?.state?.requirement_spec, undefined)
      failing = false
      await act(async () => view().retry())
      assert.equal(view().error, undefined)
      assert.deepEqual(view().workflow?.state?.requirement_spec, { summary: '重新读取成功' })
    }
  )
})

test('新 hash、工作区及运行切换隔离正文，迟到结果不能覆盖当前文档', async () => {
  let resolveOld!: (value: unknown) => void
  const calls: string[] = []
  await harness(
    async (workspace, ref) => {
      calls.push(`${workspace}:${ref.artifactId[0]}`)
      if (ref.artifactId.startsWith('a'))
        return new Promise((resolve) => {
          resolveOld = resolve
        })
      return { summary: workspace, version: ref.artifactId[0] }
    },
    async (render, view) => {
      await render(workflow())
      assert.equal(view().loading, true)
      await render({ ...workflow('d'), runId: 'new-run' }, '/workspace/b')
      assert.deepEqual(view().workflow?.state?.requirement_spec, {
        summary: '/workspace/b',
        version: 'd'
      })
      await act(async () => resolveOld({ summary: '过期响应' }))
      assert.equal(view().workflow?.runId, 'new-run')
      assert.deepEqual(view().workflow?.state?.requirement_spec, {
        summary: '/workspace/b',
        version: 'd'
      })
      await render({ ...workflow('e'), runId: 'new-run' }, '/workspace/b')
      assert.deepEqual(view().workflow?.state?.requirement_spec, {
        summary: '/workspace/b',
        version: 'e'
      })
      assert.equal(calls.filter((call) => call === '/workspace/b:b').length, 1)
    }
  )
})

test('技术规划只读取当前面板资料，文档关闭时不读取', async () => {
  const calls: string[] = []
  const source = workflow()
  source.state!.artifactRefs = {
    technical_plan: reference('c'),
    product_plan: reference('b'),
    requirement_spec: reference('a')
  }
  source.confirmationArtifact = {
    id: 'technical_plan', name: 'ignored.md', path: 'ignored.md', format: 'markdown', content: '',
    contentRef: reference('d')
  }
  await harness(
    async (_workspace, ref) => {
      calls.push(ref.artifactId[0])
      return { architecture: { frontend: 'React' } }
    },
    async (render, view) => {
      await render(source, '/workspace/a', 'technical-plan')
      assert.deepEqual(calls, ['c', 'b'])
      assert.ok(view().workflow?.state?.technical_plan)
      await render(source, '/workspace/a', null)
      assert.equal(calls.length, 2)
    }
  )
})

test('无效引用和正文格式错误显式报错，不能误报待生成', async () => {
  await harness(
    async () => '错误的结构化正文',
    async (render, view) => {
      await render(workflow())
      assert.match(view().error || '', /正文格式无效/)
      const source = workflow()
      source.state!.artifactRefs = { requirement_spec: { ...reference('a'), artifactId: '../bad' } }
      await render(source)
      assert.match(view().error || '', /文档引用无效/)
    }
  )
})

test('右侧加载与错误态可见，空态不要求先确认才能查看', () => {
  const loading = renderToStaticMarkup(
    createElement(DocPanel, {
      structuredDocument: 'requirement-doc',
      structuredDocumentLoading: true
    })
  )
  assert.match(loading, /正在读取文档/)
  assert.doesNotMatch(loading, /待生成/)
  const error = renderToStaticMarkup(
    createElement(DocPanel, {
      structuredDocument: 'requirement-doc',
      error: '引用缺失',
      onRetry: () => undefined
    })
  )
  assert.match(error, /文档读取失败/)
  assert.match(error, /重新读取/)
  const empty = renderToStaticMarkup(createElement(DocPanel, { docName: '需求文档' }))
  assert.match(empty, /待生成/)
  assert.doesNotMatch(empty, /完成当前阶段确认后/)
})
