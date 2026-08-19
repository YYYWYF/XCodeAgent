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
const personaStylePath = resolve(
  new URL(
    '../src/renderer/src/components/AiChatPanel/components/AgentConfigPanel/AgentPersonaReplyLogic.less',
    import.meta.url
  ).pathname
)
const personaStyleSource = await readFile(personaStylePath, 'utf8')

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

const requiredPersonaStyles = [
  ['.@{class-prefix}-agent-persona-reply-logic', '人设与回复逻辑模块必须有独立的布局样式'],
  ['var(--wb-canvas)', '人设编辑区必须使用工作台画布主题变量'],
  ['var(--wb-accent-border)', '人设编辑区聚焦态必须使用工作台强调色变量'],
  ['@media (max-width: 480px)', '人设编辑区必须提供移动宽度适配']
]

// 检查新增编辑模块是否沿用双主题变量并包含小屏幕布局规则。
for (const [style, message] of requiredPersonaStyles) {
  assert.ok(personaStyleSource.includes(style), message)
}

console.log('agent-config style tests passed')
