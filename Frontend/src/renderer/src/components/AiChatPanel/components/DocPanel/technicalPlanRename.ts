import { asRecord } from './TechnicalPlanDocPanelData'

type Entries = Map<string, unknown>
type Pair = [string | undefined, string | undefined]

/** 保留原身份匹配，仅将内容完全一致且双方唯一的增删项配成名称变更。 */
export function pairEntries(
  before: Entries,
  after: Entries,
  normalize?: (value: unknown) => unknown,
  named = false
): Pair[] {
  const removed = [...before.keys()].filter((key) => !after.has(key))
  const added = [...after.keys()].filter((key) => !before.has(key))
  const renames = new Map<string, string>()
  if (normalize) {
    /** 仅比较非空对象；列表项去掉名称后，其余属性必须完全相同。 */
    const signature = (value: unknown, key: string, syncReference = false): string | undefined => {
      const record = asRecord(value)
      const content = named
        ? Object.fromEntries(Object.entries(record).filter(([key]) => key !== 'name'))
        : { ...record }
      // API 字段改名会同步修改关联实体字段；只规范与自身 key 完全对应的末段。
      if (
        syncReference &&
        !named &&
        typeof content.entity_field_ref === 'string' &&
        content.entity_field_ref.endsWith(`.${key}`)
      )
        content.entity_field_ref = content.entity_field_ref.slice(0, -key.length) + '$field'
      return Object.keys(content).length ? JSON.stringify(normalize(content)) : undefined
    }
    /** 同时保留原引用和同步改名引用的签名，避免单边规范化破坏原本相同的引用。 */
    const signatures = (entries: Entries, key: string): (string | undefined)[] => [
      signature(entries.get(key), key),
      signature(entries.get(key), key, true)
    ]
    const oldSignatures = new Map(removed.map((key) => [key, signatures(before, key)]))
    const newSignatures = new Map(added.map((key) => [key, signatures(after, key)]))
    /** 两种匹配方式共享唯一性检查，多个候选时仍保留原始增删。 */
    const matches = (old: (string | undefined)[], next: (string | undefined)[]): boolean =>
      old.some((value, index) => value !== undefined && value === next[index])
    for (const [oldKey, value] of oldSignatures) {
      const candidates = [...newSignatures].filter(([, candidate]) => matches(value, candidate))
      if (
        candidates.length === 1 &&
        [...oldSignatures.values()].filter((v) => matches(v, candidates[0][1])).length === 1
      )
        renames.set(oldKey, candidates[0][0])
    }
  }
  const paired = new Set(renames.values())
  return [
    ...[...before.keys()].map((key): Pair => [key, after.has(key) ? key : renames.get(key)]),
    ...added.filter((key) => !paired.has(key)).map((key): Pair => [undefined, key])
  ]
}

/** 仅当必填列表的变化完全由当前对象内已确认的字段改名产生时，合并该差异。 */
export function isRequiredRenameOnly(
  before: Record<string, unknown>,
  after: Record<string, unknown>,
  normalize: (value: unknown) => unknown
): boolean {
  if (!Array.isArray(before.required) || !Array.isArray(after.required)) return false
  const renames = new Map(
    pairEntries(
      new Map(Object.entries(asRecord(before.properties))),
      new Map(Object.entries(asRecord(after.properties))),
      normalize
    ).filter(
      ([oldKey, newKey]) => oldKey !== undefined && newKey !== undefined && oldKey !== newKey
    )
  )
  if (!renames.size) return false
  const required = before.required.map((name) => renames.get(name) ?? name)
  return (
    JSON.stringify(normalize({ required })) ===
    JSON.stringify(normalize({ required: after.required }))
  )
}
