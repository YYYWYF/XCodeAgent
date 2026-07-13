const MAX_SKILL_CONTENT_BYTES = 512 * 1024
const REQUIRED_FIELDS_ERROR =
  '技能内容的 YAML frontmatter 必须包含非空 name 和 description；格式正确后才能保存。'

export type SkillContentValidation = {
  valid: boolean
  name: string
  error?: string
}

export function validateSkillContent(content: string): SkillContentValidation {
  if (new TextEncoder().encode(content).length > MAX_SKILL_CONTENT_BYTES) {
    return { valid: false, name: '', error: '技能内容不能超过 512 KiB。' }
  }

  const lines = content.replace(/^\uFEFF/, '').split(/\r?\n/)
  if (lines[0]?.trim() !== '---') {
    return { valid: false, name: '', error: REQUIRED_FIELDS_ERROR }
  }
  const closingIndex = lines.findIndex(
    (line, index) => index > 0 && ['---', '...'].includes(line.trim())
  )
  if (closingIndex < 0) {
    return { valid: false, name: '', error: REQUIRED_FIELDS_ERROR }
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
