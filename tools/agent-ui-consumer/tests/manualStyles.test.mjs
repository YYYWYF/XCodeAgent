import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { test } from 'node:test'
import { JSDOM } from 'jsdom'
import ts from 'typescript'

const require = createRequire(import.meta.url)
const postcss = createRequire(require.resolve('vite/package.json'))('postcss')
const source = await readFile(new URL('../t27-manual.mjs', import.meta.url), 'utf8')
const ast = ts.createSourceFile('manual.mjs', source, ts.ScriptTarget.Latest, true)
let css = ''
/** 从实际手测生成器提取 CSS，避免复制一份已修正夹具而漏测真实入口。 */
function visit(node) {
  if (ts.isCallExpression(node) && node.expression.getText(ast) === 'writeFile'
    && node.arguments[0]?.getText(ast).includes("'src/manual.css'")) css = node.arguments[1].text
  ts.forEachChild(node, visit)
}
visit(ast)
assert.ok(css)

/** 官方 PlatformShell 也是 main，正文样式不能压缩它或覆盖其消息排版。 */
test('manual page styles do not constrain the public Agent main shell or conversation text', () => {
  const dom = new JSDOM('<div class="manual-app"><main class="manual-business"><p>正文</p></main><section><main class="agent-ui-platform-shell"><p>回复</p><select><option>会话</option></select></main></section></div>')
  try {
    const shell = dom.window.document.querySelector('.agent-ui-platform-shell')
    const protectedElements = [shell, shell.querySelector('p'), shell.querySelector('select')]
    postcss.parse(css).walkRules(rule => {
      if (!rule.nodes.some(node => ['padding', 'max-width', 'line-height', 'font', 'color', 'background'].includes(node.prop))) return
      for (const element of protectedElements) {
        assert.equal(rule.selectors.some(selector => element.matches(selector)), false, `Leaked manual selector: ${rule.selector}`)
      }
    })
  } finally { dom.window.close() }
})

/** 两种手测主题由当前页面显式声明，不能让正文独立跟随操作系统。 */
test('manual canvas has matched explicit light and dark schemes', () => {
  const schemes = new Map()
  postcss.parse(css).walkRules(rule => {
    for (const node of rule.nodes) if (node.prop === 'color-scheme') schemes.set(rule.selector, node.value)
  })
  assert.equal(schemes.get('.manual-app'), 'light')
  assert.equal(schemes.get('.manual-app[data-manual-theme="dark"]'), 'dark')
})
