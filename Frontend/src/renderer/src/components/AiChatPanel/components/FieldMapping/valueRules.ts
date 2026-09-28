import type { WorkflowApiBusinessValue, WorkflowApiValueRight } from '../../../../typings'

export const BUILTIN_OPTIONS = [{ value: 'current_user_id', label: '当前用户 ID' }, { value: 'current_time', label: '当前时间' }]
export const VALUE_SOURCE_OPTIONS = [{ value: 'endpoint', label: '接口参数' }, { value: 'fixed', label: '固定值' }, { value: 'builtin', label: '内置参数', disabled: true }, { value: 'business', label: '业务处理' }]

/** 为规则编辑生成独立草稿，保留直接参数作为初始依赖。 */
export function businessValue(right?: WorkflowApiValueRight, origin: WorkflowApiBusinessValue['origin'] = 'business'): WorkflowApiBusinessValue {
  if (right?.kind === 'business') return { ...right, endpointFields: [...right.endpointFields], builtinFields: [...right.builtinFields] }
  return { kind: 'business', origin, endpointFields: right?.kind === 'endpoint' && right.endpointField ? [right.endpointField] : [], builtinFields: [], businessDescription: '', missingBehavior: 'error' }
}

/** 将类型化固定值恢复为输入文本，保留 false 和零值。 */
export function valueText(value: unknown): string { return value === undefined ? '' : typeof value === 'string' ? value : JSON.stringify(value) }

/** 根据目标类型和集合运算符解析编辑文本，不执行任何表达式。 */
export function parseValue(raw: string, type: string, collection = false): unknown {
  if (!raw.trim()) return raw
  if (collection || /array|\[\]|object/i.test(type)) { try { return JSON.parse(raw) } catch { return raw } }
  if (/int|number|decimal|numeric|float|double/i.test(type)) { const n = Number(raw); return Number.isFinite(n) ? n : raw }
  if (/bool|^bit/i.test(type)) return raw === 'true' ? true : raw === 'false' ? false : raw
  return raw
}

/** 生成精简取值摘要，界面不再展示已隐藏的缺值策略。 */
export function valueSummary(right?: WorkflowApiValueRight): string {
  if (!right) return '未配置'
  if (right.kind === 'endpoint') return right.endpointField ? `${right.endpointField.path} · ${right.endpointField.type}` : '待选接口参数'
  if (right.kind === 'fixed') return `固定值：${valueText(right.value)}`
  const dependencies = [...right.endpointFields.map((field) => field.path), ...right.builtinFields.map((key) => BUILTIN_OPTIONS.find((item) => item.value === key)?.label || key)]
  return `${right.businessDescription}${dependencies.length ? `（输入：${dependencies.join('、')}）` : ''}`
}
