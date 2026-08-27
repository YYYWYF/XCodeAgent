import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const stylePath = resolve(
  new URL(
    '../src/renderer/src/components/AiChatPanel/components/AgentConfigPanel/AgentConfigPanel.less',
    import.meta.url
  ).pathname
)
const styleSource = await readFile(stylePath, 'utf8')

const requiredStyles = [
  ['background: var(--wb-canvas, #ffffff);', '资源弹窗内容必须提供不透明背景回退值'],
  ['color: var(--wb-text, #202124);', '资源弹窗文字必须提供 Portal 环境下的颜色回退值'],
  [
    'body:has(.@{class-prefix}-agent-resource-modal) .ant-modal-mask',
    '资源弹窗必须显式覆盖 Portal 遮罩样式'
  ]
]

// 检查 Portal 弹窗样式是否包含脱离工作台主题变量后的安全回退。
for (const [style, message] of requiredStyles) {
  assert.ok(styleSource.includes(style), message)
}

console.log('agent-config style tests passed')
