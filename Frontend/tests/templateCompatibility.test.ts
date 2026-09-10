import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import test from 'node:test'
import { compileTsx } from '../src/renderer/src/components/DesignRenderer/compileTsx'
import { getAvailableTemplates } from '../src/renderer/src/service/templateService'
import {
  filterCompatibleTemplates,
  resolvePageSurface
} from '../src/renderer/src/service/templateCompatibility'

const templates = [
  {
    manifest: {
      category: 'business' as const,
      supportedSurfaces: ['standard_page', 'floating_panel'] as const
    }
  },
  {
    manifest: {
      category: 'agent' as const,
      supportedSurfaces: ['standalone_page'] as const,
      agentUi: {
        module: '@xcodeagent/agent-ui-design' as const,
        component: 'AgentConversationTemplate' as const,
        version: 'agent-ui.v1' as const
      }
    }
  }
]

/** 验证普通页和浮层页只展示业务模板。 */
test('业务页面模板候选按 Surface 过滤', () => {
  assert.equal(filterCompatibleTemplates(templates as never, 'standard_page').length, 1)
  assert.equal(filterCompatibleTemplates(templates as never, 'floating_panel').length, 1)
})

/** 验证独立会话页只展示 Agent 页面模板。 */
test('独立会话页只展示 Agent 模板', () => {
  const result = filterCompatibleTemplates(templates as never, 'standalone_page')
  assert.equal(result.length, 1)
  assert.equal(result[0].manifest.category, 'agent')
})

/** 验证未知或冲突的服务端事实不会回退为全部模板。 */
test('未知和多 Surface 页面安全拒绝', () => {
  assert.equal(
    resolvePageSurface({ bindings: { agent_surfaces: [{ type: 'unknown' }] } }),
    undefined
  )
  assert.equal(
    resolvePageSurface({
      bindings: {
        agent_surfaces: [{ type: 'floating_panel' }, { type: 'floating_panel' }]
      }
    }),
    undefined
  )
  assert.deepEqual(filterCompatibleTemplates(templates as never, undefined), [])
  assert.deepEqual(
    filterCompatibleTemplates(
      [
        {
          manifest: {
            category: 'agent',
            supportedSurfaces: ['standalone_page']
          }
        }
      ] as never,
      'standalone_page'
    ),
    []
  )
})

/** 验证独立智能体模板可由 Electron 当前 DesignRenderer 编译器直接处理。 */
test('独立智能体模板通过设计稿编译器', async () => {
  const source = await fs.readFile(
    path.resolve('src/renderer/src/templates/agentConversation/index.tsx'),
    'utf8'
  )
  const compiled = compileTsx(source)

  assert.match(compiled, /window\.__DESIGN_COMPONENT__/)
  assert.match(compiled, /window\.__DESIGN_RUNTIME__\.agentUi/)
  assert.match(source, /AgentConversationTemplate/)
  assert.match(source, /@xcodeagent\/agent-ui-design/)
  assert.doesNotMatch(source, /x-agent-message|agent-message-bubble|function AgentChatCore/)
})

/** 验证智能体模板的本地预览图会被 Vite 解析为可显示资源。 */
test('独立智能体模板包含打包内预览图', () => {
  const template = getAvailableTemplates().find((item) => item.manifest.id === 'agentConversation')

  assert.ok(template)
  assert.ok(template.manifest.previewImage)
  assert.notEqual(template.manifest.previewImage, './preview.svg')
  assert.deepEqual(template.manifest.agentUi, {
    module: '@xcodeagent/agent-ui-design',
    component: 'AgentConversationTemplate',
    version: 'agent-ui.v1'
  })
})
