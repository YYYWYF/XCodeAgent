import { useContext, useEffect, useRef, useState } from 'react'
import type { RefinedMappingRule, RuleAssistDependency, RuleAssistTarget } from '../../../../typings/endpointDesignAssist'
import { requestMappingAssist } from '../../../../service/endpointDesigns'
import { apiDesignFieldKey, endpointFieldSnapshot } from '../WorkflowRunCard/apiDesignSerialization'
import { sourceFieldKey } from './model'
import { mappingInputKey } from './mappingAssistModel'
import { RuleAssistContext } from './useMappingAssist'

/** 隔离规则候选运行与采用边界，只有原规则编辑器负责最终修改草稿。 */
export function useRuleAssist({ target, dependencies, description, disabled, onAdopt }: {
  target?: RuleAssistTarget; dependencies: string[]; description: string; disabled: boolean; onAdopt: (description: string) => void
}) {
  const context = useContext(RuleAssistContext)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ key: string; value: RefinedMappingRule }>()
  const controller = useRef<AbortController>()
  const key = mappingInputKey([context?.request, target, dependencies, description])
  const currentKey = useRef(key); currentKey.current = key
  useEffect(() => () => controller.current?.abort(), [])
  useEffect(() => {
    controller.current?.abort(); controller.current = undefined
    setRunning(false); setError(''); setResult(undefined)
  }, [key])

  /** 将原编辑器的依赖键还原成受限快照，不接收未完成输入行。 */
  const resolveInputs = (): RuleAssistDependency[] => dependencies.map((value) => {
    const source = context?.sources.find((field) => `source:${sourceFieldKey(field)}` === value)
    if (source) return { kind: 'source', field: source as unknown as Record<string, unknown> }
    const endpoint = context?.fields.find((field) => field.side === 'request' && [apiDesignFieldKey(field), `endpoint:${apiDesignFieldKey(field)}`].includes(value))
    if (endpoint) return { kind: 'endpoint', field: endpointFieldSnapshot(endpoint) as Record<string, unknown> }
    if (value.startsWith('builtin:')) return { kind: 'builtin', field: { key: value.slice(8) } }
    throw new Error('请先为每项输入选择有效字段。')
  })

  /** 输入基线未变且运行未取消时才展示候选，不自动采用。 */
  const generate = async (): Promise<void> => {
    if (!context || !target || controller.current || disabled || !description.trim()) return
    const run = new AbortController(); controller.current = run; setRunning(true); setError(''); setResult(undefined)
    try {
      const response = await requestMappingAssist('refine_rule', context.request, { target, dependencies: resolveInputs(), description }, { signal: run.signal })
      if (run.signal.aborted || currentKey.current !== key) return
      if (!response.refinedRule) throw new Error('AI 没有返回规则建议。')
      setResult({ key, value: response.refinedRule })
    } catch (reason) {
      if (!run.signal.aborted && currentKey.current === key) setError(reason instanceof Error ? reason.message : String(reason))
    } finally { if (controller.current === run) { controller.current = undefined; setRunning(false) } }
  }

  /** 取消后立即解除运行态，迟到的候选继续受 AbortSignal 拦截。 */
  const cancel = (): void => { controller.current?.abort(); controller.current = undefined; setRunning(false) }
  const candidate = result?.key === key ? result.value : undefined
  /** 采用只更新说明编辑框，不触碰依赖、隐藏策略或外层草稿。 */
  const adopt = (): void => {
    if (disabled || !candidate?.description || result?.key !== currentKey.current) return
    onAdopt(candidate.description); setResult(undefined)
  }
  return { available: Boolean(context && target), running, error, candidate, generate, cancel, adopt }
}
