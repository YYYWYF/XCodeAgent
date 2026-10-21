import type { MappingAssistRequest, MappingGenerationInput } from '../../../../typings/endpointDesignAssist'

export type MappingAssistAnswers = Record<string, { label: string; value: string }>
export const MAPPING_REQUIREMENTS_LIMIT = 4000

/** 规范对象键顺序用于比较，忽略 JSON 请求不会发送的 undefined。 */
export function mappingInputKey(value: unknown): string {
  return JSON.stringify(value, (_key, item) => item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map((key) => [key, item[key]])) : item)
}

/** 草稿仅用于过期校验和撤销，不作为模型生成依据。 */
export function assistInputKey(request?: MappingAssistRequest, metadata?: unknown): string {
  return request ? mappingInputKey([request, metadata]) : ''
}

/** 主需求与后续补充分开序列化，字段回答保留目标标签及稳定顺序。 */
export function mappingRequirementsInput(instruction: string, clarification: string, answers: MappingAssistAnswers = {}): MappingGenerationInput {
  const fieldAnswers = Object.keys(answers).sort().map((key) => answers[key])
    .filter((answer) => answer.value.trim()).map((answer) => `${answer.label}：${answer.value.trim()}`)
  return { instruction: instruction.trim(), clarification: [clarification.trim(), ...fieldAnswers].filter(Boolean).join('\n') }
}

/** 绑定分开的需求与补充，避免文本拼接碰撞或未生成的回答被应用。 */
export function mappingRequirementsKey(instruction: string, clarification: string, answers: MappingAssistAnswers = {}): string {
  return mappingInputKey(mappingRequirementsInput(instruction, clarification, answers))
}

/** 用稳定身份和基线阻止迟到结果与重复应用。 */
export function assistResultIsCurrent(captured: string, current: string, aborted = false): boolean {
  return Boolean(captured && captured === current && !aborted)
}

/** 当前应用完成后仅在没有后续编辑的条件下保留一次撤销。 */
export function canUndoMapping(appliedKey: string | undefined, current: string): boolean {
  return Boolean(appliedKey && appliedKey === current)
}
