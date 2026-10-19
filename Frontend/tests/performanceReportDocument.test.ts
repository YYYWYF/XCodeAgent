import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import TestReportDocument from '../src/renderer/src/components/AiChatPanel/components/TestReportDocument'
import {
  localLighthouseReportPath,
  openLocalReportFile
} from '../src/renderer/src/utils/reportFile'

test('测试报告相对链接只解析当前工作区内的性能 HTML 报告', () => {
  const prefix = './.devagentstudio/runtime/tests/frontend_performance/'
  assert.equal(
    localLighthouseReportPath('/workspace/app', `${prefix}report%20name.html`),
    '/workspace/app/.devagentstudio/runtime/tests/frontend_performance/report name.html'
  )
  for (const href of [
    `${prefix}../../secret.html`,
    `${prefix}%2e%2e/secret.html`,
    `${prefix}report.html?token=1`,
    `${prefix}report%00.html`,
    `${prefix}report.html#section`,
    `${prefix}report.json`,
    `${prefix}%ZZ.html`,
    `${prefix}..%5csecret.html`,
    '/tmp/report.html',
    'file:///tmp/report.html',
    'https://example.com/report.html',
    'javascript:alert(1)'
  ])
    assert.equal(localLighthouseReportPath('/workspace/app', href), undefined, href)
  assert.equal(localLighthouseReportPath(undefined, `${prefix}report.html`), undefined)
})

test('可读测试报告渲染解释表格与完整报告入口，并沿现有 Electron IPC 打开', async () => {
  const relative = './.devagentstudio/runtime/tests/frontend_performance/frontend-performance.html'
  const markup = renderToStaticMarkup(
    createElement(TestReportDocument, {
      content: `> **整体结论：页面性能良好**\n\n| 指标 | 本次结果 | 含义 |\n| --- | --- | --- |\n| 首次内容显示（FCP） | 0.49 秒 | 第一段文字或图像 |\n\n[查看完整 Lighthouse 报告](${relative})`,
      error: '',
      generating: false,
      title: '测试报告',
      workspaceRoot: '/workspace/app'
    })
  )
  assert.match(markup, /<blockquote><strong>整体结论/)
  assert.match(markup, /<table>/)
  assert.match(markup, /首次内容显示（FCP）/)
  assert.match(markup, /查看完整 Lighthouse 报告/)
  const previousWindow = globalThis.window
  let openedPath = ''
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: {
      devAgentStudio: {
        browser: {
          openReportFile: async (path: string) => {
            openedPath = path
            return { ok: true }
          }
        }
      }
    }
  })
  try {
    await openLocalReportFile(localLighthouseReportPath('/workspace/app', relative)!)
    assert.equal(
      openedPath,
      '/workspace/app/.devagentstudio/runtime/tests/frontend_performance/frontend-performance.html'
    )
  } finally {
    Object.defineProperty(globalThis, 'window', { configurable: true, value: previousWindow })
  }
})
