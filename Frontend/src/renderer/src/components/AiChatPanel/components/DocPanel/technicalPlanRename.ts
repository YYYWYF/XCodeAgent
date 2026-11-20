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
    const signature = (value: unknown): string | undefined => {
      const record = asRecord(value)
      const content = named
        ? Object.fromEntries(Object.entries(record).filter(([key]) => key !== 'name'))
        : record
      return Object.keys(content).length ? JSON.stringify(normalize(content)) : undefined
    }
    const oldSignatures = new Map(removed.map((key) => [key, signature(before.get(key))]))
    const newSignatures = new Map(added.map((key) => [key, signature(after.get(key))]))
    for (const [oldKey, value] of oldSignatures) {
      if (value === undefined) continue
      const candidates = [...newSignatures].filter(([, candidate]) => value === candidate)
      if (
        candidates.length === 1 &&
        [...oldSignatures.values()].filter((candidate) => candidate === value).length === 1
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
