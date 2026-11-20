import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import ts from 'typescript'

const require = createRequire(import.meta.url)
const source = await readFile(new URL('../../agent-ui-design/businessValue.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext,
  target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText
const moduleSource = compiled.replaceAll('"react/jsx-runtime"', JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href))
const { BusinessValue } = await import('data:text/javascript;base64,' + Buffer.from(moduleSource).toString('base64'))

/** 卡片展示正式字段标题及类型值，内部身份不能因嵌套结构泄漏。 */
test('business cards format schema titles and hide nested diagnostic identities', () => {
  const html = renderToStaticMarkup(createElement(BusinessValue, { value: { updated: false, runId: 'secret-run',
    detail: { interactionId: 'secret-pending', amount: 0 }, items: ['<script>bad</script>', true, null] },
  schema: { properties: { updated: { title: '已更新' }, detail: { title: '明细' } } } }))
  assert.match(html, /已更新：/)
  assert.match(html, /否/)
  assert.match(html, /amount：/)
  assert.match(html, /0/)
  assert.match(html, /&lt;script&gt;/)
  assert.doesNotMatch(html, /secret-run|secret-pending|<script>/)
})

/** 深层或大量结果有展示边界，避免卡片挤满对话滚动区。 */
test('business cards bound nesting and collections and handle empty results', () => {
  assert.equal(renderToStaticMarkup(createElement(BusinessValue, { value: null })), '')
  const value = Array.from({ length: 40 }, (_, index) => `entry-${index}`)
  const html = renderToStaticMarkup(createElement(BusinessValue, { value }))
  assert.match(html, /entry-29/)
  assert.doesNotMatch(html, /entry-30/)
  const deep = { child: { child: { child: { child: { child: { child: 'hidden-deep-value' } } } } } }
  assert.doesNotMatch(renderToStaticMarkup(createElement(BusinessValue, { value: deep })), /hidden-deep-value/)
})
