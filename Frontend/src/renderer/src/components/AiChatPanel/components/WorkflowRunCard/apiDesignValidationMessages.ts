import type { WorkflowApiValueRight } from '../../../../typings'

/** 将未完成或类型不匹配的取值配置转为包含目标字段的操作提示。 */
export function valueConfigurationError(right: WorkflowApiValueRight | undefined, field: string, query = false): string {
  const target = `${query ? '查询字段' : '字段'}「${field}」`
  if (!right) return `请配置${target}的取值内容。`
  if (right.kind === 'business') return `请完善字段「${field}」的业务规则。`
  if (right.kind === 'endpoint') return right.endpointField
    ? `${target}与所选接口参数的类型不匹配，请重新选择或配置业务转换规则。`
    : `请为${target}选择接口参数。`
  // false、0 和合法空字符串均为已填写值，不能误报为未填写。
  return right.value === undefined || right.value === null
    ? `请为字段「${field}」填写固定值。`
    : `字段「${field}」的固定值格式或类型不正确，请检查。`
}
