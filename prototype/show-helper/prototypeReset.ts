const RESETTABLE_PREFIXES = [
  'aistudio:prototype:',
  'aistudio-sessions:',
  'aistudio-active-session:',
  'aistudio-workbench:'
]

/** 清理原型在浏览器中产生的会话、任务与初始化状态，保留静态演示应用。 */
export function clearPrototypeCache(): number {
  if (typeof window === 'undefined') return 0
  const keys = Array.from({ length: window.localStorage.length }, (_, index) =>
    window.localStorage.key(index)
  ).filter(
    (key): key is string =>
      Boolean(key && RESETTABLE_PREFIXES.some((prefix) => key.startsWith(prefix)))
  )
  keys.forEach((key) => window.localStorage.removeItem(key))
  return keys.length
}
