import { Validator } from '@cfworker/json-schema'
import type { OutputUnit, Schema } from '@cfworker/json-schema'

export type DebugJsonCheck = { value?: unknown; syntaxValid: boolean; errors: string[]; validatorError?: string }
export type DebugJsonOptions = {
  schema?: Record<string, unknown> | null; objectOnly?: boolean; validate?: (value: unknown) => void
}

const validators = new WeakMap<object, Validator>()

/** 根据解析器给出的偏移补充行列，不猜测无法确定的位置。 */
function syntaxMessage(text: string, reason: unknown): string {
  const message = reason instanceof Error ? reason.message : '请检查引号、逗号和括号。'
  const offset = /position (\d+)/i.exec(message)
  if (!offset) return `JSON 语法错误：${message}`
  const prefix = text.slice(0, Number(offset[1]))
  const lines = prefix.split('\n')
  return `第 ${lines.length} 行，第 ${lines[lines.length - 1].length + 1} 列：${message}`
}

/** 将 Schema 错误定位到业务字段，展示必填、类型和未声明字段等明确原因。 */
function schemaMessage(error: OutputUnit): string {
  const path = error.instanceLocation.replace(/^#/, '')
  const location = path ? `${path}：` : ''
  // 库将字段名放在 JSON 引号中；只取字段标识，不把底层英文诊断拼进产品提示。
  const quoted = /"((?:\\.|[^"\\])*)"/.exec(error.error)
  let field = ''
  if (quoted) { try { field = JSON.parse(quoted[0]) } catch { field = '' } }
  if (error.keyword === 'required') return `${location}请填写必填字段${field ? `：${field}` : ''}。`
  if (error.keyword === 'type') {
    const expected = /Expected "([^"]+)"/.exec(error.error)?.[1] || ''
    const types: Record<string, string> = { string: '字符串', integer: '整数', number: '数字', boolean: '布尔值', object: '对象', array: '数组', null: 'null' }
    return `${location}应为${types[expected] || '契约指定的类型'}。`
  }
  if (error.keyword === 'additionalProperties') return `${location}包含未声明字段${field ? `：${field}` : ''}。`
  const messages: Record<string, string> = {
    enum: '请选择允许的值。', const: '必须使用契约指定的值。', format: '格式不正确。', pattern: '格式不符合要求。',
    minLength: '内容长度不足。', maxLength: '内容过长。', minimum: '数值低于允许的最小值。', maximum: '数值超过允许的最大值。',
    exclusiveMinimum: '数值必须大于下限。', exclusiveMaximum: '数值必须小于上限。', multipleOf: '数值不符合步长要求。',
    minItems: '数组项目不足。', maxItems: '数组项目过多。', uniqueItems: '数组中不能包含重复项。',
    anyOf: '不符合任一允许的结构。', oneOf: '必须符合一种允许的结构。'
  }
  return `${location}${messages[error.keyword] || '不符合接口契约，请检查该字段。'}`
}

/** 同时校验 JSON 语法和正式 Schema，不修改输入、不强制转换类型，也不填写默认值。 */
export function checkDebugJson(text: string, options: DebugJsonOptions = {}): DebugJsonCheck {
  if (!text.trim()) return { syntaxValid: false, errors: ['请输入 JSON；空对象请填写 {}。'] }
  let value: unknown
  try { value = JSON.parse(text) } catch (reason) { return { syntaxValid: false, errors: [syntaxMessage(text, reason)] } }
  const errors: string[] = []
  if (options.objectOnly && (!value || typeof value !== 'object' || Array.isArray(value))) errors.push('必须是 JSON 对象。')
  try { options.validate?.(value) } catch (reason) { errors.push(reason instanceof Error ? reason.message : '输入校验失败。') }
  try {
    if (options.schema) {
      let validate = validators.get(options.schema)
      // 解释执行 Schema，避免 Electron CSP 禁止运行时生成函数；资源解析只修改私有副本。
      if (!validate) { validate = new Validator(structuredClone(options.schema) as Schema, '2020-12', false); validators.set(options.schema, validate) }
      const result = validate.validate(value)
      if (!result.valid) errors.push(...result.errors.filter((error) => !['properties', 'items', 'prefixItems', 'false'].includes(error.keyword)).map(schemaMessage))
      if (!result.valid && !errors.length) errors.push('输入不符合当前接口契约。')
    }
  } catch (reason) { return { value, syntaxValid: true, errors, validatorError: `契约校验器不可用：${reason instanceof Error ? reason.message : '请检查接口 Schema。'}` } }
  return { value, syntaxValid: true, errors }
}
