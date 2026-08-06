import { load } from 'js-yaml'

export type SkillFrontmatter = {
  description: string
  name: string
}

export function parseSkillFrontmatter(content: string): SkillFrontmatter {
  const lines = content.replace(/^\uFEFF/, '').split(/\r?\n/)
  if (lines[0]?.trim() !== '---') throw new Error('SKILL.md 缺少 YAML frontmatter。')
  const closingIndex = lines.findIndex(
    (line, index) => index > 0 && ['---', '...'].includes(line.trim())
  )
  if (closingIndex < 0) throw new Error('SKILL.md 的 YAML frontmatter 未结束。')

  let value: unknown
  try {
    value = load(lines.slice(1, closingIndex).join('\n'), { json: false })
  } catch {
    throw new Error('SKILL.md 的 YAML frontmatter 格式无效。')
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('SKILL.md 的 YAML frontmatter 必须是对象。')
  }
  const metadata = value as Record<string, unknown>
  const name = typeof metadata.name === 'string' ? metadata.name.trim() : ''
  const description = typeof metadata.description === 'string' ? metadata.description.trim() : ''
  if (!name || !description) throw new Error('SKILL.md 必须包含非空 name 和 description。')
  return { description, name }
}
