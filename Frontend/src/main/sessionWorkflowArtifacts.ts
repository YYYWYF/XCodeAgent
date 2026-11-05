import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'
import type { JsonRecord } from './sessionFileReader'

/** 编辑后的大块产物与后端共用内容地址目录，消息展示字段直接保存在会话内。 */
function artifactRoot(workspace: unknown): string {
  if (typeof workspace !== 'string' || !path.isAbsolute(workspace))
    throw new Error('会话缺少明确工作区。')
  return path.join(workspace, '.devagentstudio', 'artifacts', 'sha256')
}

/** 原子发布 JSON 资料；同一正文并发写入时只发布一份不可变文件。 */
async function publishJson(workspace: unknown, value: unknown): Promise<JsonRecord> {
  // 与后端一样按键排序，使正文不因对象字段插入顺序不同而重复落盘。
  const bytes = Buffer.from(
    JSON.stringify(value, (_key, item: unknown) => {
      if (item && typeof item === 'object' && !Array.isArray(item))
        return Object.fromEntries(
          Object.entries(item).sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
        )
      return item
    }),
    'utf8'
  )
  const artifactId = crypto.createHash('sha256').update(bytes).digest('hex')
  const root = artifactRoot(workspace)
  await fs.mkdir(root, { recursive: true })
  const target = path.join(root, `${artifactId}.json`)
  const temporary = path.join(root, `.artifact-${crypto.randomUUID()}.tmp`)
  try {
    const output = await fs.open(temporary, 'wx')
    try {
      await output.writeFile(bytes)
      await output.sync()
    } finally {
      await output.close()
    }
    await fs.link(temporary, target).catch((error: NodeJS.ErrnoException) => {
      if (error.code !== 'EEXIST') throw error
    })
  } finally {
    await fs.unlink(temporary).catch(() => undefined)
  }
  return { artifactId, byteLength: bytes.length, mediaType: 'application/json' }
}

/** 仅外置编辑后的正式产物正文，精简的 Workflow 展示数据直接留在消息中。 */
export async function storeWorkflowArtifacts(
  workspace: unknown,
  message: JsonRecord
): Promise<JsonRecord> {
  const original = message.workflow as JsonRecord | undefined
  if (!original) return message
  const workflow = { ...original }
  const state = { ...((workflow.state as JsonRecord) || {}) }
  const refs = { ...((state.artifactRefs as JsonRecord) || {}) }
  // 编辑器保存草稿后会产生新的局部正文；落盘仍必须使用新内容引用，不能保存旧引用或完整副本。
  for (const key of [
    'requirement_spec',
    'product_plan',
    'technical_plan',
    'project_plan',
    'pending_project_plan'
  ]) {
    if (state[key] != null) {
      refs[key] = await publishJson(workspace, state[key])
      delete state[key]
    }
  }
  if (Object.keys(refs).length) state.artifactRefs = refs
  workflow.state = state
  const artifact = workflow.confirmationArtifact as JsonRecord | undefined
  if (artifact && typeof artifact.content === 'string' && artifact.content) {
    const { content, ...metadata } = artifact
    workflow.confirmationArtifact = {
      ...metadata,
      contentRef: await publishJson(workspace, content)
    }
  }
  return { ...message, workflow }
}
