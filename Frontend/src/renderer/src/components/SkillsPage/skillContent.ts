const MAX_SKILL_CONTENT_BYTES = 512 * 1024
const REQUIRED_FIELDS_ERROR =
  '技能内容的 YAML frontmatter 必须包含非空 name 和 description；格式正确后才能保存。'
const CREATE_DELIMITER_ERROR = '技能内容必须以 --- 开始，并使用第二个 --- 结束 YAML frontmatter。'
const CREATE_NAME_ERROR = 'name 必须以英文小写字母开头，且仅包含英文小写字母、数字和下划线。'

export const CREATE_SKILL_CONTENT_PLACEHOLDER = `【格式要求】
---
name: 技能名称（必填，英文小写下划线）
description: 功能描述（必填，简洁清晰）
---

技能实现内容（Markdown格式）

【示例】
---
name: weather_query
description: 查询指定城市的天气信息
---`

export type SkillContentMode = 'create' | 'edit'

export type SkillContentValidation = {
  valid: boolean
  name: string
  error?: string
}

export function readSkillNameFromContent(content: string): string {
  const lines = content.replace(/^\uFEFF/, '').split(/\r?\n/)
  if (lines[0]?.trim() !== '---') return ''

  const closingIndex = lines.findIndex(
    (line, index) => index > 0 && ['---', '...'].includes(line.trim())
  )
  const frontmatterEnd = closingIndex < 0 ? lines.length : closingIndex
  const nameLine = lines
    .slice(1, frontmatterEnd)
    .find((line) => line.includes(':') && line.slice(0, line.indexOf(':')).trim() === 'name')
  if (!nameLine?.includes(':')) return ''

  try {
    return parseScalar(nameLine.slice(nameLine.indexOf(':') + 1).trim(), 'name')
  } catch {
    return ''
  }
}

export function syncSkillNameToContent(content: string, name: string): string {
  const byteOrderMark = content.startsWith('\uFEFF') ? '\uFEFF' : ''
  const normalizedContent = content.replace(/^\uFEFF/, '')
  const newline = normalizedContent.includes('\r\n') ? '\r\n' : '\n'
  const lines = normalizedContent.split(/\r?\n/)

  if (lines[0]?.trim() !== '---') {
    const body = normalizedContent ? `${newline}${newline}${normalizedContent}` : ''
    return `${byteOrderMark}---${newline}name: ${name}${newline}description: ${newline}---${body}`
  }

  const closingIndex = lines.findIndex(
    (line, index) => index > 0 && ['---', '...'].includes(line.trim())
  )
  const frontmatterEnd = closingIndex < 0 ? lines.length : closingIndex
  const nameIndex = lines.findIndex(
    (line, index) =>
      index > 0 &&
      index < frontmatterEnd &&
      line.includes(':') &&
      line.slice(0, line.indexOf(':')).trim() === 'name'
  )

  if (nameIndex >= 0) {
    const indentation = lines[nameIndex].match(/^\s*/)?.[0] || ''
    lines[nameIndex] = `${indentation}name: ${name}`
  } else {
    lines.splice(1, 0, `name: ${name}`)
  }
  return byteOrderMark + lines.join(newline)
}

export function validateSkillContent(
  content: string,
  mode: SkillContentMode = 'edit'
): SkillContentValidation {
  if (new TextEncoder().encode(content).length > MAX_SKILL_CONTENT_BYTES) {
    return { valid: false, name: '', error: '技能内容不能超过 512 KiB。' }
  }

  const lines = content.replace(/^\uFEFF/, '').split(/\r?\n/)
  if (lines[0]?.trim() !== '---') {
    return {
      valid: false,
      name: '',
      error: mode === 'create' ? CREATE_DELIMITER_ERROR : REQUIRED_FIELDS_ERROR
    }
  }
  const closingIndex = lines.findIndex(
    (line, index) => index > 0 && ['---', '...'].includes(line.trim())
  )
  if (closingIndex < 0) {
    return {
      valid: false,
      name: '',
      error: mode === 'create' ? CREATE_DELIMITER_ERROR : REQUIRED_FIELDS_ERROR
    }
  }
  if (mode === 'create' && lines[closingIndex].trim() !== '---') {
    return { valid: false, name: '', error: CREATE_DELIMITER_ERROR }
  }

  const metadata: Record<string, string> = {}
  try {
    lines.slice(1, closingIndex).forEach((line) => {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith('#') || !line.includes(':')) return
      const separatorIndex = line.indexOf(':')
      const key = line.slice(0, separatorIndex).trim()
      if (!['name', 'description'].includes(key)) return
      const value = parseScalar(line.slice(separatorIndex + 1).trim(), key)
      if (value) metadata[key] = value
    })
  } catch {
    return { valid: false, name: metadata.name || '', error: REQUIRED_FIELDS_ERROR }
  }

  const name = metadata.name || ''
  if (!name || !metadata.description) {
    return { valid: false, name, error: REQUIRED_FIELDS_ERROR }
  }
  if (mode === 'create' && !/^[a-z][a-z0-9_]*$/.test(name)) {
    return { valid: false, name, error: CREATE_NAME_ERROR }
  }
  return { valid: true, name }
}

function parseScalar(rawValue: string, key: string): string {
  if (!rawValue) return ''
  if (rawValue.startsWith('|') || rawValue.startsWith('>')) {
    throw new Error(`${key} does not support multiline values`)
  }
  if (rawValue.startsWith('"')) {
    const value: unknown = JSON.parse(rawValue)
    return String(value).trim()
  }
  if (rawValue.startsWith("'")) {
    if (rawValue.length < 2 || !rawValue.endsWith("'")) {
      throw new Error(`${key} has invalid quotes`)
    }
    return rawValue.slice(1, -1).replace(/''/g, "'").trim()
  }
  return rawValue.split(' #', 1)[0].trim()
}
