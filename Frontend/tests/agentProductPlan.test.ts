import assert from 'node:assert/strict'
import { promises as fs } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import { projectWorkbenchAgents } from '../src/main/agentPlanningArtifactProjection'
import { requirementAgentRows } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/RequirementDocPanelData'
import { selectedAgentSurfaceProductPlan } from '../src/renderer/src/components/AiChatPanel/agentSurfaceSelectionState'
import {
  reconcileActingPageIdsAfterRefresh,
  settleUiDesignRefresh,
  shouldShowUiDesignRefresh
} from '../src/renderer/src/components/Welcome/uiDesignProgress'

/** 验证当前规划轮次保存的浮窗选择优先于尚未刷新的 Workflow 快照。 */
test('浮窗保存成功后立即使用当前规划轮次的最新 ProductPlan', () => {
  const staleWorkflowPlan = { confirmation_status: 'pending_user_confirmation', enabled: true }
  const savedProductPlan = { confirmation_status: 'pending_user_confirmation', enabled: false }
  const selection = {
    runId: 'planning-run-1',
    threadId: 'planning-thread-1',
    sourceProductPlanKey: 'product-plan.v8:1:2026-09-10T10:00:00Z:requirement-hash',
    productPlan: savedProductPlan
  }
  Object.assign(staleWorkflowPlan, {
    schema_version: 'product-plan.v8',
    version: 1,
    generated_at: '2026-09-10T10:00:00Z',
    requirement_spec_sha256: 'requirement-hash'
  })

  assert.equal(
    selectedAgentSurfaceProductPlan(
      selection,
      { runId: 'planning-run-1', threadId: 'planning-thread-1' },
      staleWorkflowPlan
    ),
    savedProductPlan
  )
  assert.equal(
    selectedAgentSurfaceProductPlan(
      selection,
      { runId: 'planning-run-2', threadId: 'planning-thread-1' },
      staleWorkflowPlan
    ),
    staleWorkflowPlan
  )
  const confirmedWorkflowPlan = { ...staleWorkflowPlan, confirmation_status: 'confirmed' }
  assert.equal(
    selectedAgentSurfaceProductPlan(
      selection,
      { runId: 'planning-run-1', threadId: 'planning-thread-1' },
      confirmedWorkflowPlan
    ),
    confirmedWorkflowPlan
  )
})

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
            {
              pageId: 'inventory_home',
              actionIds: ['inventory_home_ask_assistant'],
              surface: {
                type: 'floating_panel',
                enabled: true,
                contextItemIds: ['inventory_summary']
              }
            }
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
    pageName: 'inventory_home',
    pagePath: '',
    actionIds: ['inventory_home_ask_assistant'],
    surface: {
      type: 'floating_panel',
      label: '悬浮问答面板',
      enabled: true,
      contextItemIds: ['inventory_summary']
    }
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
  assert.equal(rows[0].pageActionBindings.length, 0)
})

/** 验证确认视图把非法 Surface 收敛为只读未知状态，并仅保留字符串上下文项。 */
test('智能体产品规划对未知 Surface 安全降级', () => {
  const rows = requirementAgentRows(
    {
      agents: [
        {
          agentId: 'inventory_assistant',
          name: '库存助手',
          pageActionBindings: [
            {
              pageId: 'inventory_home',
              actionIds: [],
              surface: {
                type: 'future_surface',
                enabled: false,
                contextItemIds: ['inventory_summary', 42, '', ' inventory_alerts ']
              }
            }
          ]
        }
      ]
    },
    {}
  )

  assert.deepEqual(rows[0].pageActionBindings[0]?.surface, {
    type: 'unknown',
    label: '未知载体（只读）',
    enabled: false,
    contextItemIds: ['inventory_summary', 'inventory_alerts']
  })
})

/** 验证开发工作台投影携带页面名称、Surface 与严格过滤后的上下文白名单。 */
test('开发工作台投影智能体页面交互载体', async () => {
  const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-agent-surface-'))
  const technicalPlan = {
    api_contracts: [],
    agent_contracts: [
      {
        agentId: 'inventory_assistant',
        identity: { name: '库存助手' },
        invocation: {},
        agentSettings: {},
        capabilities: [],
        artifacts: {}
      }
    ]
  }
  try {
    await fs.mkdir(path.join(workspaceRoot, '.xcodeagent', 'plans'), { recursive: true })
    await fs.writeFile(
      path.join(workspaceRoot, '.xcodeagent', 'plans', 'technical-plan.json'),
      JSON.stringify(technicalPlan),
      'utf8'
    )
    const result = await projectWorkbenchAgents(
      workspaceRoot,
      {
        pages: [{ pageId: 'inventory_home', name: '库存首页' }],
        agents: [
          {
            agentId: 'inventory_assistant',
            name: '库存助手',
            entryPageIds: ['inventory_home'],
            pageActionBindings: [
              {
                pageId: 'inventory_home',
                actionIds: ['inventory_home_ask_assistant'],
                surface: {
                  type: 'floating_panel',
                  enabled: true,
                  contextItemIds: ['inventory_summary', 'inventory_summary', 42]
                }
              }
            ]
          }
        ]
      },
      technicalPlan
    )

    assert.equal(result.invalid.length, 0)
    assert.deepEqual(result.agents[0]?.entryActions[0], {
      pageId: 'inventory_home',
      pageLabel: '库存首页',
      actionIds: ['inventory_home_ask_assistant'],
      surface: {
        type: 'floating_panel',
        label: '悬浮问答面板',
        enabled: true,
        contextItemIds: ['inventory_summary']
      }
    })
  } finally {
    await fs.rm(workspaceRoot, { force: true, recursive: true })
  }
})

/** 验证关闭的候选浮窗不会进入开发工作台的可用页面入口。 */
test('开发工作台过滤已关闭的智能体浮窗', async () => {
  const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-disabled-surface-'))
  const technicalPlan = {
    api_contracts: [],
    agent_contracts: [
      {
        agentId: 'inventory_assistant',
        identity: { name: '库存助手' },
        invocation: {},
        agentSettings: {},
        capabilities: [],
        artifacts: {}
      }
    ]
  }
  try {
    await fs.mkdir(path.join(workspaceRoot, '.xcodeagent', 'plans'), { recursive: true })
    await fs.writeFile(
      path.join(workspaceRoot, '.xcodeagent', 'plans', 'technical-plan.json'),
      JSON.stringify(technicalPlan),
      'utf8'
    )
    const result = await projectWorkbenchAgents(
      workspaceRoot,
      {
        pages: [{ pageId: 'inventory_home', name: '库存首页' }],
        agents: [
          {
            agentId: 'inventory_assistant',
            entryPageIds: ['inventory_home'],
            pageActionBindings: [
              {
                pageId: 'inventory_home',
                actionIds: ['inventory_home_ask_assistant'],
                surface: { type: 'floating_panel', enabled: false, contextItemIds: [] }
              }
            ]
          }
        ]
      },
      technicalPlan
    )

    assert.deepEqual(result.agents[0]?.entryPageIds, [])
    assert.deepEqual(result.agents[0]?.entryActions, [])
  } finally {
    await fs.rm(workspaceRoot, { force: true, recursive: true })
  }
})

/** 验证本地动作态与后台生成态都能显示手动刷新入口，避免界面卡在生成中。 */
test('UI 设计本地生成态也提供刷新入口', () => {
  assert.equal(shouldShowUiDesignRefresh(['qa_page'], []), true)
  assert.equal(shouldShowUiDesignRefresh([], ['qa_page']), true)
  assert.equal(shouldShowUiDesignRefresh([], []), false)
})

/** 验证刷新后只保留服务端仍在排队或生成的页面动作标记。 */
test('UI 设计刷新后按最新清单收敛本地生成态', () => {
  assert.deepEqual(
    reconcileActingPageIdsAfterRefresh(
      ['qa_page', 'order_page', 'adjust'],
      [
        { pageId: 'qa_page', status: 'confirmed' },
        { pageId: 'order_page', status: 'generating' }
      ]
    ),
    ['order_page', 'adjust']
  )
  assert.deepEqual(
    reconcileActingPageIdsAfterRefresh(
      ['qa_page', 'adjust'],
      [{ pageId: 'qa_page', status: 'generation_failed' }]
    ),
    []
  )
})

/** 验证只读恢复请求结束时立即收敛刷新 loading，不依赖 Workflow 状态跳变。 */
test('UI 设计刷新跟随恢复请求完成而结束', async () => {
  let resolveRecovery: (() => void) | undefined
  let settled = false
  const recovery = new Promise<void>((resolve) => {
    resolveRecovery = resolve
  })

  const task = settleUiDesignRefresh(
    () => recovery,
    () => {
      settled = true
    }
  )

  assert.equal(settled, false)
  resolveRecovery?.()
  await task
  assert.equal(settled, true)
})

/** 验证恢复请求失败时也会释放刷新 loading，错误仍由上层呈现。 */
test('UI 设计刷新失败时仍结束 loading', async () => {
  let settled = false

  await assert.rejects(
    settleUiDesignRefresh(
      () => Promise.reject(new Error('recovery failed')),
      () => {
        settled = true
      }
    ),
    /recovery failed/
  )

  assert.equal(settled, true)
})
