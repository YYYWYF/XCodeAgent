export type DebugKeyValueRow = { id: string; enabled: boolean; key: string; value: string }

/** 将契约中的参数名投影为初始表格行，停用与未填写状态仅存在当前面板。 */
export function debugKeyValueRows(values: Record<string, string>): DebugKeyValueRow[] {
  return Object.entries(values).map(([key, value], index) => ({ id: `${index}:${key}`, enabled: true, key, value }))
}

/** 只发送启用行，明确拒绝缺少 Key 或重复 Key，避免悄悄覆盖用户输入。 */
export function debugKeyValueMap(rows: DebugKeyValueRow[], label: string): Record<string, string> {
  const names = new Set<string>()
  const entries: [string, string][] = []
  for (const row of rows) {
    if (!row.enabled || (!row.key.trim() && !row.value)) continue
    const key = row.key.trim()
    if (!key) throw new Error(`${label}中有已填写 Value 但缺少 Key 的行。`)
    const identity = label === 'Headers' ? key.toLowerCase() : key
    if (names.has(identity)) throw new Error(`${label}中存在重复 Key：${key}`)
    names.add(identity)
    entries.push([key, row.value])
  }
  return Object.fromEntries(entries)
}
