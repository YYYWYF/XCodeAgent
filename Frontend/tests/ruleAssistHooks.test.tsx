import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement, useState, type ReactElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { apiDesignFieldKey } from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/apiDesignSerialization'
import { RuleAssistContext, type RuleAssistContextValue } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/useMappingAssist'
import { RuleEditingContext, useRuleEditingLock } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/RuleEditor'
import { useRuleAssist } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/useRuleAssist'
import { assistRequest } from './mappingAssistModel.test'
import { withAssistServer } from './mappingAssistTestHarness'

/** 采用候选只修改规则说明编辑框，整个弹窗生命周期持续占用外层保存锁。 */
test('真实规则 hook 采用候选后保留依赖和隐藏策略，取消不提交并释放保存锁', async () => {
  await withAssistServer(async (server) => {
    const parameter = { id: 'request-id', side: 'request' as const, location: 'path' as const, path: 'id', type: 'integer', required: true, description: '商品 ID' }
    const context: RuleAssistContextValue = { request: assistRequest(), fields: [parameter], sources: [] }
    const initial = { dependencies: [`endpoint:${apiDesignFieldKey(parameter)}`], description: '保留两位小数', missing: 'default', defaultValue: 9 }
    let root: ReactTestRenderer | undefined, rule!: ReturnType<typeof useRuleAssist>
    let draft = initial, editing = 0, adoptions = 0
    /** 使用生产规则锁及候选 hook，父层只允许原应用规则动作提交。 */
    function RuleProbe(): ReactElement {
      useRuleEditingLock()
      const [value, setValue] = useState(initial)
      draft = value
      rule = useRuleAssist({ target: { area: 'query', key: 'id' }, dependencies: value.dependencies, description: value.description,
        disabled: false, onAdopt: (description) => { adoptions++; setValue((current) => ({ ...current, description })) } })
      return createElement('output', { description: value.description })
    }
    /** 接收真实弹窗挂载锁，断言候选生成及采用不会提前释放。 */
    const track = (delta: number): void => { editing += delta }
    try {
      await act(async () => { root = create(createElement(RuleEditingContext.Provider, { value: track },
        createElement(RuleAssistContext.Provider, { value: context }, createElement(RuleProbe)))) })
      assert.equal(editing, 1)
      await act(async () => { await rule.generate() })
      assert.equal(adoptions, 0)
      assert.equal(draft.description, initial.description)
      await act(async () => rule.adopt())
      assert.equal(adoptions, 1)
      assert.equal(draft.description, '按用户要求保留两位小数')
      assert.deepEqual(draft.dependencies, initial.dependencies)
      assert.equal(draft.missing, 'default')
      assert.equal(draft.defaultValue, 9)
      assert.equal(editing, 1)
      server.held = true
      let pending!: Promise<void>
      await act(async () => { pending = rule.generate() })
      await act(async () => { root?.unmount(); root = undefined })
      await act(async () => { server.requests[1].release(); await pending })
      assert.equal(editing, 0)
      assert.equal(adoptions, 1)
      assert.deepEqual(server.requests.map((row) => row.input.action), ['refine_rule', 'refine_rule'])
    } finally { await act(async () => root?.unmount()) }
  })
})
