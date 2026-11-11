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
    const signature = (value: unknown, key: string): string | undefined => {
      const record = asRecord(value)
      const content = named
        ? Object.fromEntries(Object.entries(record).filter(([key]) => key !== 'name'))
        : { ...record }
      // API 字段改名会同步修改关联实体字段；只规范与自身 key 完全对应的末段。
      if (
        !named &&
        typeof content.entity_field_ref === 'string' &&
        content.entity_field_ref.endsWith(`.${key}`)
      )
        content.entity_field_ref = content.entity_field_ref.slice(0, -key.length) + '$field'
      return Object.keys(content).length ? JSON.stringify(normalize(content)) : undefined
    }
    const oldSignatures = new Map(removed.map((key) => [key, signature(before.get(key), key)]))
    const newSignatures = new Map(added.map((key) => [key, signature(after.get(key), key)]))
    for (const [oldKey, value] of oldSignatures) {
      if (!value) continue
      const candidates = [...newSignatures].filter(([, candidate]) => candidate === value)
      if (
        candidates.length === 1 &&
        [...oldSignatures.values()].filter((v) => v === value).length === 1
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
