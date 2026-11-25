import assert from 'node:assert/strict'
import { test } from 'node:test'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { saveSession, readSessionPage } from '../src/main/sessionHistory'
import { appendTechnicalPlanningOutput } from '../src/renderer/src/service/technicalPlanningOutput'
import { readWorkflowPayload } from '../src/renderer/src/service/agUiAgent'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'

test('过程记录只留在当前内存，保存和重读会话保留确认门但不包含输出正文', async () => {
  const workspace = await fs.mkdtemp(path.join(os.tmpdir(), 'session-transient-output-'))
  try {
    const file = path.join(workspace, 'session.json')
    const workflow: WorkflowRunPayload = {
      runId: 'output-run', threadId: 'output-thread', events: [],
      summary: { phase: 'technical_planning', status: 'requires_user_input' },
      state: { application_planning_interrupt: {
        gateId: 'technical-gate', artifact: 'technical_plan', artifactRevision: 'draft-revision'
      } }
    }
    const text = 'TRANSIENT_PROCESS_BODY_ONLY'
    const live = appendTechnicalPlanningOutput(workflow, {
      runId: workflow.runId, threadId: workflow.threadId,
      kind: 'text', text, timestamp: '2026-11-25T04:00:00Z'
    })!
    const output = live.technicalPlanningOutput
    await saveSession(file, {
      workspaceRoot: workspace,
      messages: [{ id: 1, role: 'assistant', content: '等待确认技术规划', workflow: live }]
    })
    const stored = await fs.readFile(file, 'utf8')
    assert.doesNotMatch(stored, /technicalPlanningOutput|TRANSIENT_PROCESS_BODY_ONLY/)
    assert.equal(live.technicalPlanningOutput, output)
    assert.equal(live.technicalPlanningOutput?.entries[0].text, text)

    const page = await readSessionPage(file)
    const restored = readWorkflowPayload(page.session.messages[0].workflow)!
    assert.equal(restored.technicalPlanningOutput, undefined)
    assert.equal(restored.runId, live.runId)
    assert.equal(restored.threadId, live.threadId)
    assert.equal(restored.summary.status, 'requires_user_input')
    assert.deepEqual(restored.state?.application_planning_interrupt, live.state?.application_planning_interrupt)
    assert.deepEqual(await fs.readdir(workspace), ['session.json'])
  } finally {
    await fs.rm(workspace, { recursive: true, force: true })
  }
})

test('精简展示直接保存在消息中，打开历史不依赖独立展示文件或产物正文', async () => {
  const workspace = await fs.mkdtemp(path.join(os.tmpdir(), 'session-workflow-'))
  try {
    const file = path.join(workspace, 'session.json')
    const workflow = {
      runId: 'run',
      threadId: 'thread',
      summary: {
        status: 'requires_user_input',
        clarification: { mode: 'build_task_plan_confirmation' }
      },
      events: [],
      state: {
        selectedPageId: 'home',
        artifactRefs: {
          build_task_plan: {
            artifactId: 'a'.repeat(64),
            byteLength: 123,
            mediaType: 'application/json'
          }
        }
      }
    }
    await saveSession(file, {
      id: 'session',
      workspaceRoot: workspace,
      messages: Array.from({ length: 20 }, (_, index) => ({
        id: index + 1,
        role: 'assistant',
        content: '结果',
        workflow
      }))
    })
    const stored = JSON.parse(await fs.readFile(file, 'utf8'))
    assert.deepEqual(
      Object.keys(stored.messages[0].workflow).sort(),
      ['runId', 'summary', 'threadId', 'events', 'state'].sort()
    )
    assert.equal(stored.messages[0].workflow.summary.clarification, undefined)
    const root = path.join(workspace, '.devagentstudio/artifacts/sha256')
    await assert.rejects(fs.access(root), { code: 'ENOENT' })
    const page = await readSessionPage(file)
    assert.equal(page.session.messages.length, 10)
    assert.equal(page.session.messages[0].workflow.state.selectedPageId, 'home')
    assert.equal(page.session.messages[0].workflow.result, undefined)
    assert.equal(
      page.session.messages[0].workflow.state.clarification.mode,
      'build_task_plan_confirmation'
    )
    assert.equal(stored.messages[0].workflow.state.selectedPageId, 'home')
    assert.equal(stored.messages[0].workflow.viewRef, undefined)
  } finally {
    await fs.rm(workspace, { recursive: true, force: true })
  }
})

test('编辑后的大块产物仍外置引用，精简展示数据直接保存在会话里', async () => {
  const workspace = await fs.mkdtemp(path.join(os.tmpdir(), 'session-edited-material-'))
  try {
    const file = path.join(workspace, 'session.json')
    const spec = { description: '新内容'.repeat(200_000) }
    await saveSession(file, {
      workspaceRoot: workspace,
      messages: [
        {
          id: 1,
          role: 'assistant',
          content: '草稿',
          workflow: {
            runId: 'run',
            threadId: 'thread',
            summary: { status: 'requires_user_input' },
            events: [],
            state: {
              requirement_spec: spec,
              artifactRefs: { requirement_spec: { artifactId: 'a'.repeat(64) } }
            }
          }
        }
      ]
    })
    const page = await readSessionPage(file)
    const state = page.session.messages[0].workflow.state
    assert.equal(state.requirement_spec, undefined)
    assert.notEqual(state.artifactRefs.requirement_spec.artifactId, 'a'.repeat(64))
    const material = path.join(
      workspace,
      '.devagentstudio/artifacts/sha256',
      `${state.artifactRefs.requirement_spec.artifactId}.json`
    )
    assert.deepEqual(JSON.parse(await fs.readFile(material, 'utf8')), spec)
    assert.ok((await fs.stat(file)).size < 2000)
  } finally {
    await fs.rm(workspace, { recursive: true, force: true })
  }
})

test('较大展示状态直接写入消息，尾页读取无需打开任何展示文件', async () => {
  const workspace = await fs.mkdtemp(path.join(os.tmpdir(), 'session-large-view-'))
  const originalOpen = fs.open
  try {
    const file = path.join(workspace, 'session.json')
    /** 构造带页面代码的真实展示状态，正文不属于正式文档的外置字段。 */
    const workflow = (code: string): Record<string, unknown> => ({
      runId: 'run',
      threadId: 'thread',
      summary: { status: 'completed' },
      events: [],
      state: { ui_designs: { pages: [{ pageId: 'home', code }] } }
    })
    const recentCode = 'x'.repeat(200_000)
    await saveSession(file, {
      workspaceRoot: workspace,
      messages: Array.from({ length: 22 }, (_, index) => ({
        id: index + 1,
        role: 'assistant',
        content: '设计稿',
        ...(index === 0 ? { workflow: workflow('p'.repeat(5_000_000)) } : {}),
        ...(index === 21 ? { workflow: workflow(recentCode) } : {})
      }))
    })
    const stored = JSON.parse(await fs.readFile(file, 'utf8'))
    fs.open = (async (...args: Parameters<typeof fs.open>) => {
      assert.equal(String(args[0]), file, '读取历史只需打开会话文件')
      return originalOpen(...args)
    }) as typeof fs.open
    const page = await readSessionPage(file)
    assert.deepEqual(
      page.session.messages.map((message: { id: number }) => message.id),
      Array.from({ length: 10 }, (_, index) => index + 13)
    )
    assert.equal(page.session.messages.at(-1).workflow.state.ui_designs.pages[0].code, recentCode)
    assert.equal(stored.messages.at(-1).workflow.state.ui_designs.pages[0].code, recentCode)
    assert.equal(stored.messages[0].workflow.state.ui_designs.pages[0].code.length, 5_000_000)
  } finally {
    fs.open = originalOpen
    await fs.rm(workspace, { recursive: true, force: true })
  }
})
