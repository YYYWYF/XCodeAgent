import { createContext, useCallback, useEffect, useRef, useState } from 'react'
import type { WorkflowApiDesignDraft, WorkflowApiField, WorkflowApiSourceField } from '../../../../typings'
import type {
  MappingApplied, MappingAssistance, MappingAssistFailure, MappingAssistProgress, MappingAssistRequest
} from '../../../../typings/endpointDesignAssist'
import { MappingAssistError, requestMappingAssist } from '../../../../service/endpointDesigns'
import { assistInputKey, assistResultIsCurrent, canUndoMapping, mappingRequirementsInput, mappingRequirementsKey, MAPPING_REQUIREMENTS_LIMIT, type MappingAssistAnswers } from './mappingAssistModel'

export type RuleAssistContextValue = {
  request: MappingAssistRequest; fields: WorkflowApiField[]; sources: WorkflowApiSourceField[]
}
export const RuleAssistContext = createContext<RuleAssistContextValue | undefined>(undefined)

/** 管理单个 Endpoint 的建议运行，所有异步结果必须匹配当前输入基线。 */
export function useMappingAssist(request: MappingAssistRequest | undefined, metadata: unknown,
  onEdit: (draft: WorkflowApiDesignDraft) => void) {
  const [open, setOpen] = useState(false)
  const [instruction, updateInstruction] = useState('')
  const [phase, setPhase] = useState<'idle' | 'generating' | 'applying'>('idle')
  const [progress, setProgress] = useState<MappingAssistProgress>()
  const [progressSteps, setProgressSteps] = useState<MappingAssistProgress[]>([])
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ inputKey: string; requirementsKey: string; value: MappingAssistance }>()
  const [failure, setFailure] = useState<{ inputKey: string; requirementsKey: string; value: MappingAssistFailure }>()
  const [preview, setPreview] = useState(true)
  const [clarification, updateClarification] = useState('')
  const [answers, updateAnswers] = useState<MappingAssistAnswers>({})
  const [receipt, setReceipt] = useState<{ inputKey: string; value: MappingApplied; serial: number }>()
  const [undo, setUndo] = useState<{ inputKey: string; draft: WorkflowApiDesignDraft }>()
  const controller = useRef<AbortController>()
  const locked = useRef(false)
  const serial = useRef(0)
  const currentKey = assistInputKey(request, metadata)
  const requirements = mappingRequirementsInput(instruction, clarification, answers)
  const requirementsKey = mappingRequirementsKey(instruction, clarification, answers)
  const current = useRef({ key: currentKey, request, metadata, onEdit, requirementsKey, instruction, clarification, answers })
  current.current = { key: currentKey, request, metadata, onEdit, requirementsKey, instruction, clarification, answers }
  const targetKey = request ? JSON.stringify([request.workspaceRoot, request.apiContractId, request.endpointId]) : ''

  /** 显式取消独立 AG-UI 调用，迟到结果不能修改草稿。 */
  const cancel = useCallback((): void => {
    controller.current?.abort(); controller.current = undefined; locked.current = false
    setPhase('idle'); setProgress(undefined); setProgressSteps([])
  }, [])

  useEffect(() => {
    cancel(); setOpen(false); updateInstruction(''); updateClarification(''); updateAnswers({}); setResult(undefined); setFailure(undefined); setError(''); setUndo(undefined); setReceipt(undefined)
    return cancel
  }, [targetKey, cancel])

  /** 用户编辑、保存及来源切换都撤销未应用建议与撤销权限。 */
  const invalidate = useCallback((): void => {
    cancel(); setResult(undefined); setFailure(undefined); setUndo(undefined); setReceipt(undefined); setError('')
  }, [cancel])

  /** 要求变化时保留旧预览供核对，但立即使旧结果和阶段重试失效。 */
  const setInstruction = (value: string): void => {
    current.current.instruction = value
    current.current.requirementsKey = mappingRequirementsKey(value, current.current.clarification, current.current.answers)
    updateInstruction(value); setFailure(undefined); setError('')
  }
  /** 补充答案必须重新生成后才能应用，同一事件内调用 apply 也会被阻止。 */
  const setClarification = (value: string): void => {
    current.current.clarification = value
    current.current.requirementsKey = mappingRequirementsKey(current.current.instruction, value, current.current.answers)
    updateClarification(value); setFailure(undefined); setError('')
  }

  /** 按字段保存回答并同步运行基线，输入本身不改变预览配置。 */
  const setAnswer = (key: string, label: string, value: string): void => {
    const next = { ...current.current.answers }
    if (value) next[key] = { label, value }
    else delete next[key]
    current.current.answers = next
    current.current.requirementsKey = mappingRequirementsKey(current.current.instruction, current.current.clarification, next)
    updateAnswers(next); setFailure(undefined); setError('')
  }

  /** 打开完整映射生成入口，保留用户已输入的要求。 */
  const show = (): void => {
    if (locked.current || !request) return
    setOpen(true); setPreview(true)
  }

  /** 关闭建议区同时取消生成，不触发保存或自动重试。 */
  const close = (): void => { cancel(); setOpen(false) }

  /** 生成过程允许编辑；用户变更会使本次输入基线失效。 */
  const generate = async (resume = false): Promise<void> => {
    const input = { ...current.current }
    if (!input.request || locked.current) return
    const values = mappingRequirementsInput(input.instruction, input.clarification, input.answers)
    if (!values.instruction) { setError('请先填写需求说明。'); return }
    if (values.instruction.length + values.clarification.length > MAPPING_REQUIREMENTS_LIMIT) { setError('需求说明与补充内容合计不能超过 4000 字，请精简后重新生成。'); return }
    const retry = failure?.inputKey === input.key && failure.requirementsKey === input.requirementsKey ? failure.value : undefined
    if (resume && !retry) return
    const run = new AbortController(); controller.current = run; locked.current = true
    setPhase('generating'); setError(''); setResult(undefined); setFailure(undefined); setProgress(undefined); setProgressSteps([]); setPreview(true)
    try {
      const response = await requestMappingAssist('generate_mapping', input.request, { ...values, ...(resume && retry ? { retryToken: retry.retryToken } : {}) },
        { signal: run.signal, onProgress: (value) => {
          if (!assistResultIsCurrent(input.key, current.current.key, run.signal.aborted)) return
          setProgress(value)
          setProgressSteps((steps) => [...steps.filter((item) => item.stage !== value.stage), value])
        } })
      if (!assistResultIsCurrent(input.key, current.current.key, run.signal.aborted)) return
      if (!response.assistance) throw new Error('AI 辅助未返回建议。')
      setResult({ inputKey: input.key, requirementsKey: input.requirementsKey, value: response.assistance })
    } catch (reason) {
      if (assistResultIsCurrent(input.key, current.current.key, run.signal.aborted)) {
        setError(reason instanceof Error ? reason.message : String(reason))
        if (reason instanceof MappingAssistError) setFailure({ inputKey: input.key, requirementsKey: input.requirementsKey, value: reason.failure })
      }
    } finally {
      if (controller.current === run) { locked.current = false; controller.current = undefined; setPhase('idle'); setProgress(undefined) }
    }
  }

  /** 后端重新验证完整配置后，一次回填原草稿并保存撤销快照。 */
  const apply = async (): Promise<void> => {
    const input = { ...current.current }
    if (!input.request || !result?.value.mapping || !assistResultIsCurrent(result.inputKey, input.key) || result.requirementsKey !== input.requirementsKey || locked.current) return
    const run = new AbortController(); controller.current = run; locked.current = true
    setPhase('applying'); setError('')
    try {
      const response = await requestMappingAssist('replace_mapping', input.request, { baseline: result.value.baseline, mapping: result.value.mapping }, { signal: run.signal })
      if (!assistResultIsCurrent(input.key, current.current.key, run.signal.aborted) || input.requirementsKey !== current.current.requirementsKey) return
      if (!response.applied) throw new Error('AI 辅助未返回应用结果。')
      const nextKey = assistInputKey({ ...input.request, draft: response.applied.draft }, input.metadata)
      setUndo({ inputKey: nextKey, draft: input.request.draft }); setReceipt({ inputKey: nextKey, value: response.applied, serial: ++serial.current })
      input.onEdit(response.applied.draft); setResult(undefined); setOpen(false)
    } catch (reason) {
      if (assistResultIsCurrent(input.key, current.current.key, run.signal.aborted)) setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      if (controller.current === run) { locked.current = false; controller.current = undefined; setPhase('idle') }
    }
  }

  /** 仅撤销本次应用，任何后续用户修改都使撤销资格失效。 */
  const undoApply = (): void => {
    if (!undo || !canUndoMapping(undo.inputKey, current.current.key) || locked.current) return
    current.current.onEdit(undo.draft); setUndo(undefined); setReceipt(undefined)
  }

  return { open, instruction, setInstruction, clarification, setClarification, answers, setAnswer,
    requirementsLength: requirements.instruction.length + requirements.clarification.length, preview, setPreview, phase, progress, progressSteps, error,
    currentOperation: request?.draft.databaseOperation,
    requirementsChanged: Boolean(result && result.requirementsKey !== requirementsKey),
    failure: failure?.inputKey === currentKey && failure.requirementsKey === requirementsKey ? failure.value : undefined,
    canApply: Boolean(request && result?.value.mapping && result.inputKey === currentKey && result.requirementsKey === requirementsKey && phase === 'idle'),
    previewDraft: request && result?.inputKey === currentKey && result.value.mapping ? { ...request.draft, ...result.value.mapping, databaseQuery: result.value.mapping.databaseQuery || undefined } : undefined,
    result: result?.inputKey === currentKey ? result.value : undefined,
    stale: Boolean(result && result.inputKey !== currentKey),
    receipt: receipt?.inputKey === currentKey ? receipt : undefined,
    canUndo: canUndoMapping(undo?.inputKey, currentKey), show, close, cancel, invalidate, generate, apply, undoApply }
}

export type MappingAssistState = ReturnType<typeof useMappingAssist>
