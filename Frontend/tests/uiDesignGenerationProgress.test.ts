import assert from 'node:assert/strict'
import { test } from 'node:test'
import { uiDesignGenerationProgress } from '../src/renderer/src/service/uiDesignGenerationProgress'
import type { WorkflowEvent } from '../src/renderer/src/typings'

/** 造一条 UI 确认节点的进度事件（runtime 把节点的 ui_confirmation.progress 转成这个）。 */
function progress(detail: Record<string, unknown>, message = ''): WorkflowEvent {
  return {
    type: 'workflow.node.progress',
    nodeName: 'ui_confirmation',
    message,
    data: { phase: 'ui_confirmation', detail }
  }
}

test('多页 adjust：认得出当前正在生成的那一页', () => {
  // 用户没 @ 指定页面时，这是前端唯一能点亮「生成中」的来源。
  const events: WorkflowEvent[] = [
    progress({ ready: 0, total: 2 }, '开始调整 2 个页面的设计稿'),
    progress({ ready: 0, total: 2, pageId: 'overview' }, '正在调整设计稿（第 1/2 页）：概览页')
  ]
  const result = uiDesignGenerationProgress(events)
  assert.equal(result?.pageId, 'overview')
  assert.equal(result?.message, '正在调整设计稿（第 1/2 页）：概览页')
})

test('该页收尾后不再算作生成中', () => {
  // 收尾那条的 ready 比开始那条大 1；不据此收口，那一页会一直挂着「生成中」。
  const events: WorkflowEvent[] = [
    progress({ ready: 0, total: 2, pageId: 'overview' }, '正在调整设计稿（第 1/2 页）：概览页'),
    progress({ ready: 1, total: 2 }, '设计稿已调整：概览页（第 1/2 页完成）')
  ]
  assert.equal(uiDesignGenerationProgress(events)?.pageId, undefined)
  assert.equal(uiDesignGenerationProgress(events)?.message, '设计稿已调整：概览页（第 1/2 页完成）')
})

test('逐页推进时跟着切到下一页', () => {
  const events: WorkflowEvent[] = [
    progress({ ready: 0, total: 2, pageId: 'overview' }, '正在调整设计稿（第 1/2 页）：概览页'),
    progress({ ready: 1, total: 2 }, '设计稿已调整：概览页（第 1/2 页完成）'),
    progress(
      { ready: 1, total: 2, pageId: 'hello_world' },
      '正在调整设计稿（第 2/2 页）：Hello World'
    )
  ]
  assert.equal(uiDesignGenerationProgress(events)?.pageId, 'hello_world')
})

test('分析目标页阶段：有文案但还没有具体页', () => {
  const events: WorkflowEvent[] = [progress({ ready: 0, total: 0 }, '正在分析需要调整的页面…')]
  const result = uiDesignGenerationProgress(events)
  assert.equal(result?.pageId, undefined)
  assert.equal(result?.message, '正在分析需要调整的页面…')
})

test('单页换一换：ready 不再前进，保持生成中', () => {
  const events: WorkflowEvent[] = [
    progress({ ready: 1, total: 2, pageId: 'home_page' }, '正在重新生成设计稿：首页')
  ]
  assert.equal(uiDesignGenerationProgress(events)?.pageId, 'home_page')
})

test('忽略别的节点与别的事件类型', () => {
  const events: WorkflowEvent[] = [
    progress({ ready: 0, total: 1, pageId: 'x' }),
    {
      type: 'workflow.node.progress',
      nodeName: 'launch_project',
      message: '启动中',
      data: { detail: { pageId: 'y' } }
    },
    {
      type: 'workflow.node.finish',
      nodeName: 'ui_confirmation',
      message: '结束',
      data: { detail: { pageId: 'z' } }
    }
  ]
  // launch_project 的 pageId 不能被当成 UI 页；非 progress 类型同样不算进度。
  assert.equal(uiDesignGenerationProgress(events)?.pageId, 'x')
  assert.equal(uiDesignGenerationProgress(events)?.message, '')
})

test('没有相关事件时返回 undefined', () => {
  assert.equal(uiDesignGenerationProgress([]), undefined)
  assert.equal(uiDesignGenerationProgress(undefined), undefined)
  assert.equal(uiDesignGenerationProgress([{ type: 'message', message: '普通消息' }]), undefined)
})

test('detail 缺失或字段类型不对时不崩', () => {
  const events: WorkflowEvent[] = [
    { type: 'workflow.node.progress', nodeName: 'ui_confirmation', message: '进度' },
    progress({ pageId: 123, ready: 'x' })
  ]
  const result = uiDesignGenerationProgress(events)
  // pageId 非字符串时按空处理（不点亮任何页），文案仍然透出。
  assert.equal(result?.pageId, undefined)
  assert.equal(result?.message, '进度')
})
