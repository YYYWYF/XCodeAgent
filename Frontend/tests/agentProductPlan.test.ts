import assert from 'node:assert/strict'
import { test } from 'node:test'
import { requirementAgentRows } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/RequirementDocPanelData'

/** 验证 ProductPlan 智能体产品契约被拍平为可确认的稳定展示数据。 */
test('智能体产品规划展示能力、入口操作、交互状态与边界', () => {
  const rows = requirementAgentRows(
    {
      agents: [
        {
          agentId: 'inventory_assistant',
          name: '库存助手',
          purpose: '帮助用户理解库存状态并获得处理建议。',
          capabilities: [
            {
              capabilityId: 'explain_inventory_status',
              name: '解释库存状态',
              expectedResult: '用户获得明确答复。'
            }
          ],
          entryPageIds: ['inventory_home'],
          pageActionBindings: [
            { pageId: 'inventory_home', actionIds: ['inventory_home_ask_assistant'] }
          ],
          interaction: {
            mode: 'conversation',
            supportsMultiTurn: true,
            inputDescription: '用户输入库存问题。',
            outputDescription: '返回库存解释或补货建议。',
            stateRequirements: {
              loading: '显示处理中。',
              empty: '展示可提问范围。',
              error: '说明失败并允许重试。',
              success: '展示完整回复。',
              validation: '空问题不能发送。'
            }
          },
          boundaries: ['不得直接修改库存数据'],
          acceptanceCriteria: ['支持连续追问并保持上下文。']
        }
      ]
    },
    {}
  )

  assert.equal(rows.length, 1)
  assert.equal(rows[0].agentId, 'inventory_assistant')
  assert.equal(rows[0].capabilities[0].expectedResult, '用户获得明确答复。')
  assert.deepEqual(rows[0].pageActionBindings[0], {
    key: 'inventory_home',
    pageId: 'inventory_home',
    actionIds: ['inventory_home_ask_assistant']
  })
  assert.equal(rows[0].supportsMultiTurn, true)
  assert.equal(rows[0].stateRequirements.length, 5)
  assert.deepEqual(rows[0].boundaries, ['不得直接修改库存数据'])
})

/** 验证 ProductPlan 尚未生成时仍可从 RequirementSpec 显示智能体需求摘要。 */
test('需求草稿阶段回退展示 RequirementSpec 智能体', () => {
  const rows = requirementAgentRows(
    {},
    {
      agent_requirements: [
        {
          agentId: 'inventory_assistant',
          name: '库存助手',
          purpose: '帮助用户处理库存事项。',
          capabilities: ['解释库存状态'],
          entryPageIds: ['inventory_home'],
          interactionMode: 'conversation',
          boundaries: ['不得直接修改库存数据']
        }
      ]
    }
  )

  assert.equal(rows.length, 1)
  assert.equal(rows[0].name, '库存助手')
  assert.equal(rows[0].capabilities[0].name, '解释库存状态')
  assert.equal(rows[0].interactionMode, 'conversation')
  assert.deepEqual(rows[0].entryPageIds, ['inventory_home'])
})
