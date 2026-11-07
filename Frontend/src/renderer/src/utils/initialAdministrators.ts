/** 新格式的确定性解析结果，员工号始终保留为字符串。 */
export type AdminMember = { value: string; memberId: string; memberName: string }

/** 使用三端一致的 Unicode 空白集合，避免原生 trim 对 BOM 等字符的差异。 */
function strip(value: string): string {
  return value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/g, '')
}

/** 校验新输入的八位员工号及 Unicode 姓名，不用于判断历史资格。 */
export function parseNewInitialAdministrator(value: string): AdminMember {
  if (typeof value !== 'string') throw new Error('初始管理员必须是字符串。')
  const normalized = strip(value)
  if (normalized === 'current-user') throw new Error('初始管理员不能使用 current-user。')
  const parts = normalized.split('/')
  if (parts.length !== 2 || !/^[0-9]{8}$/.test(parts[0])) {
    throw new Error('初始管理员请使用“8位员工号/姓名”格式。')
  }
  const name = parts[1]
  if (Array.from(name).length < 1 || Array.from(name).length > 32 || name !== strip(name) || /[/／,，\p{Cc}\p{Cs}\p{Zl}\p{Zp}]/u.test(name)) {
    throw new Error('初始管理员姓名须为1至32个字符，且不能有首尾空白、斜线、逗号或控制字符。')
  }
  return { value: normalized, memberId: parts[0], memberName: name }
}

/** 只把已保存原样项视作历史值；新增项严格校验并按成员身份检测冲突。 */
export function validateAdminChange(previous: string[], next: string[]): string[] {
  if (!Array.isArray(next) || next.length > 100) throw new Error('初始管理员数量不得超过100。')
  if (next.some((value) => typeof value !== 'string' || !strip(value) || strip(value) === 'current-user')) {
    throw new Error('初始管理员配置包含无效成员，不能使用 current-user。')
  }
  if (JSON.stringify(previous) === JSON.stringify(next)) return [...next]
  const members = new Map<string, string>()
  const result: string[] = []
  for (const raw of next) {
    const value = typeof raw === 'string' && previous.includes(raw) ? raw : parseNewInitialAdministrator(raw).value
    let memberId = value
    let identityValue = value
    try {
      const member = parseNewInitialAdministrator(value)
      memberId = member.memberId
      identityValue = member.value
    } catch { /* 已保存旧项沿用原身份。 */ }
    if (members.has(memberId) && members.get(memberId) !== identityValue) {
      throw new Error('初始管理员员工号重复或与已有成员冲突，请替换原项。')
    }
    members.set(memberId, identityValue)
    if (!result.includes(value)) result.push(value)
  }
  return result
}

/** 识别已有旧项以展示格式升级提示，不改变表单内容。 */
export function hasLegacyAdministrators(values: string[]): boolean {
  return values.some((value) => {
    try { parseNewInitialAdministrator(value); return false } catch { return true }
  })
}
