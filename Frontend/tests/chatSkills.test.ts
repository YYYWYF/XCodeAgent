import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  beginOptimisticSkillSend,
  normalizeChatSkills,
  rollbackSkillSelection,
  selectedSkillNames,
  skillsAfterEmptyBackspace
} from '../src/renderer/src/components/AiChatPanel/skillSelection'
import { buildWorkflowForwardedProps } from '../src/renderer/src/service/agUiAgent'
import { normalizeMessageSkills } from '../src/renderer/src/service/chatSessions'
import { DEFAULT_DIFF_PANEL_WIDTH } from '../src/renderer/src/components/AiChatPanel/constants'
import {
  splitWorkspacePath,
  workspaceCodeChangeDisplayPath
} from '../src/renderer/src/components/AiChatPanel/utils'
import {
  DEFAULT_SKILL_CATEGORY,
  enabledUserSkills,
  filterCatalogSkills,
  reconcileEnabledChatSkills
} from '../src/renderer/src/components/SkillsPage/skillCatalog'
import type { UserSkillCatalog } from '../src/renderer/src/typings'

const skillCatalog: UserSkillCatalog = {
  root: '~/.xcodeagent_dev/skills',
  builtinRoot: '/.xcodeagent/builtin-skills',
  skills: [
    {
      name: 'alpha',
      description: 'First user skill',
      directoryName: 'alpha',
      relativePath: 'alpha/SKILL.md',
      updatedAt: '2026-07-19T00:00:00Z',
      enabled: true
    },
    {
      name: 'beta',
      description: 'Disabled user skill',
      directoryName: 'beta',
      relativePath: 'beta/SKILL.md',
      updatedAt: '2026-07-19T00:00:00Z',
      enabled: false
    }
  ],
  builtinSkills: [
    {
      name: 'builtin-react',
      description: 'Built-in React skill',
      directoryName: 'builtin-react',
      relativePath: 'builtin-react/SKILL.md'
    }
  ],
  skippedCount: 0,
  issues: []
}

test('技能选择按名称去空白去重并保留首次顺序', () => {
  assert.deepEqual(
    normalizeChatSkills([
      { name: ' alpha ', description: ' first ' },
      { name: 'alpha', description: 'duplicate' },
      { name: 'beta', description: 'second' }
    ]),
    [
      { name: 'alpha', description: 'first' },
      { name: 'beta', description: 'second' }
    ]
  )
})

test('AG-UI forwardedProps 在约定字段发送技能名称', () => {
  const forwardedProps = buildWorkflowForwardedProps({
    editorMode: 'frontend',
    selectedSkillNames: ['alpha', 'beta']
  })

  assert.deepEqual(forwardedProps.selectedSkillNames, ['alpha', 'beta'])
})

test('发送清空草稿标签，认证失败可恢复独立快照', () => {
  const selected = [{ name: 'alpha', description: 'instructions' }]
  const optimistic = beginOptimisticSkillSend(selected)

  assert.deepEqual(optimistic.messageSkills, selected)
  assert.deepEqual(optimistic.nextDraftSkills, [])
  assert.deepEqual(rollbackSkillSelection(optimistic.messageSkills), selected)
  assert.notEqual(rollbackSkillSelection(optimistic.messageSkills), optimistic.messageSkills)
  assert.deepEqual(selectedSkillNames(optimistic.messageSkills), ['alpha'])
})

test('会话恢复只保留有效技能名称与描述字段', () => {
  assert.deepEqual(
    normalizeMessageSkills([
      { name: 'alpha', description: 'first', unsafe: true },
      { name: 'alpha', description: 'duplicate' },
      { name: '', description: 'invalid' }
    ]),
    [{ name: 'alpha', description: 'first' }]
  )
})

test('输入文本为空时 Backspace 依次删除最后一个技能标签', () => {
  const skills = [
    { name: 'alpha', description: 'first' },
    { name: 'beta', description: 'second' }
  ]

  assert.deepEqual(skillsAfterEmptyBackspace('Backspace', '', skills), [skills[0]])
  assert.equal(skillsAfterEmptyBackspace('Backspace', 'hello', skills), undefined)
  assert.equal(skillsAfterEmptyBackspace('Enter', '', skills), undefined)
  assert.equal(skillsAfterEmptyBackspace('Backspace', '', []), undefined)
})

test('技能页面默认展示用户分类并按当前分类搜索', () => {
  assert.equal(DEFAULT_SKILL_CATEGORY, 'user')
  assert.deepEqual(
    filterCatalogSkills(skillCatalog, 'user', 'disabled').map((skill) => skill.name),
    ['beta']
  )
  assert.deepEqual(
    filterCatalogSkills(skillCatalog, 'builtin', 'react').map((skill) => skill.name),
    ['builtin-react']
  )
})

test('聊天技能目录隐藏关闭项并清理陈旧选择', () => {
  assert.deepEqual(enabledUserSkills(skillCatalog.skills).map((skill) => skill.name), ['alpha'])
  assert.deepEqual(
    reconcileEnabledChatSkills(
      [
        { name: 'alpha', description: 'old description' },
        { name: 'beta', description: 'disabled' },
        { name: 'missing', description: 'missing' }
      ],
      skillCatalog.skills
    ),
    [{ name: 'alpha', description: 'First user skill' }]
  )
})

test('工作区文件路径拆分后保留完整目录和最终文件名', () => {
  assert.deepEqual(
    splitWorkspacePath(
      'Frontend/src/renderer/src/components/AiChatPanel/components/CodeDiffDetailPanel/index.tsx'
    ),
    {
      directory: 'Frontend/src/renderer/src/components/AiChatPanel/components/CodeDiffDetailPanel',
      fileName: 'index.tsx'
    }
  )
  assert.deepEqual(splitWorkspacePath('Backend\\app\\main.py'), {
    directory: 'Backend/app',
    fileName: 'main.py'
  })
})

test('历史变更路径包含工作区根目录且 Diff 默认宽度为 500px', () => {
  assert.equal(workspaceCodeChangeDisplayPath('aa/b.js', '/Users/example/c', 'c'), 'c/aa/b.js')
  assert.equal(workspaceCodeChangeDisplayPath('aa\\b.js', 'C:\\workspace\\c'), 'c/aa/b.js')
  assert.equal(DEFAULT_DIFF_PANEL_WIDTH, 500)
})
