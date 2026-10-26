import { useCallback, useEffect, useRef, useState } from 'react'
import type { WorkflowApiDesignDraft } from '../../../../typings'
import type { MappingAssistProgress, MappingAssistRequest, MockDataAssistance } from '../../../../typings/endpointDesignAssist'
import { requestMappingAssist } from '../../../../service/endpointDesigns'
import { assistInputKey, mappingInputKey, MAPPING_REQUIREMENTS_LIMIT } from './mappingAssistModel'

/** Mock 与映射独立运行；只经原草稿回填，并隔离取消、迟到响应和一次撤销。 */
export function useMockDataAssist(request: MappingAssistRequest | undefined, disabled: boolean, onEdit: (draft: WorkflowApiDesignDraft) => void) {
  const [open, setOpen] = useState(false)
  const [instruction, updateInstruction] = useState('')
  const [rowCount, updateRowCount] = useState(3)
  const [phase, setPhase] = useState<'idle' | 'generating' | 'applying'>('idle')
  const [progress, setProgress] = useState<MappingAssistProgress>()
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ key: string; requirements: string; value: MockDataAssistance }>()
  const [receipt, setReceipt] = useState<{ key: string; before: WorkflowApiDesignDraft; serial: number }>()
  const controller = useRef<AbortController>()
  const serial = useRef(0)
  const key = assistInputKey(request)
  const requirements = mappingInputKey([instruction.trim(), rowCount])
  const current = useRef({ key, request, disabled, onEdit, instruction, rowCount, requirements })
  current.current = { key, request, disabled, onEdit, instruction, rowCount, requirements }
  const targetKey = request ? mappingInputKey([request.workspaceRoot, request.apiContractId, request.endpointId, request.selection]) : ''

  /** 关闭标准 AG-UI 运行，迟到的结果仍必须通过当前输入与 AbortSignal 检查。 */
  const cancel = useCallback((): void => {
    controller.current?.abort(); controller.current = undefined
    setPhase('idle'); setProgress(undefined)
  }, [])
  useEffect(() => {
    cancel(); setOpen(false); setResult(undefined); setReceipt(undefined); setError(''); updateInstruction(''); updateRowCount(3)
    return () => controller.current?.abort()
  }, [targetKey, cancel])

  /** 用户编辑或保存使旧候选和撤销资格失效，但保留需求输入便于重试。 */
  const invalidate = useCallback((): void => { cancel(); setResult(undefined); setReceipt(undefined); setError('') }, [cancel])
  /** 同一事件内变更需求也立即使旧候选不可采用。 */
  const setInstruction = (value: string): void => {
    current.current.instruction = value; current.current.requirements = mappingInputKey([value.trim(), current.current.rowCount])
    updateInstruction(value); setError('')
  }
  /** 列表规模显式受限，值变化参与生成和应用基线。 */
  const setRowCount = (value: number): void => {
    current.current.rowCount = value; current.current.requirements = mappingInputKey([current.current.instruction.trim(), value])
    updateRowCount(value); setError('')
  }
  /** 生成只得到候选，不回填静态数据或自动配置返回。 */
  const generate = async (): Promise<void> => {
    const input = { ...current.current }
    if (!input.request || input.disabled || controller.current) return
    if (!input.instruction.trim()) { setError('请填写 Mock 数据需求说明。'); return }
    if (input.instruction.trim().length > MAPPING_REQUIREMENTS_LIMIT) { setError('Mock 数据需求不能超过 4000 字。'); return }
    if (!Number.isInteger(input.rowCount) || input.rowCount < 1 || input.rowCount > 20) { setError('每组列表条数须为 1 至 20。'); return }
    const run = new AbortController(); controller.current = run
    setPhase('generating'); setError(''); setResult(undefined); setProgress(undefined)
    try {
      const response = await requestMappingAssist('generate_mock_data', input.request,
        { instruction: input.instruction.trim(), rowCount: input.rowCount }, { signal: run.signal, onProgress: (value) => {
          if (!run.signal.aborted && current.current.key === input.key) setProgress(value)
        } })
      if (run.signal.aborted || current.current.key !== input.key) return
      if (!response.mockData) throw new Error('AI 未返回 Mock 数据候选。')
      setResult({ key: input.key, requirements: input.requirements, value: response.mockData })
    } catch (reason) {
      if (!run.signal.aborted && current.current.key === input.key) setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      if (controller.current === run) { controller.current = undefined; setPhase('idle'); setProgress(undefined) }
    }
  }
  /** 应用重新校验当前契约和基线，仅替换数据内容并保存一次撤销快照。 */
  const apply = async (): Promise<void> => {
    const input = { ...current.current }
    if (!input.request || input.disabled || controller.current || !result?.value.data || result.key !== input.key || result.requirements !== input.requirements) return
    const run = new AbortController(); controller.current = run; setPhase('applying'); setError('')
    try {
      const response = await requestMappingAssist('apply_mock_data', input.request,
        { baseline: result.value.baseline, data: result.value.data }, { signal: run.signal })
      if (run.signal.aborted || current.current.key !== input.key || current.current.requirements !== input.requirements) return
      if (!response.applied) throw new Error('Mock 数据应用没有返回草稿。')
      setReceipt({ key: assistInputKey({ ...input.request, draft: response.applied.draft }), before: input.request.draft, serial: ++serial.current })
      input.onEdit(response.applied.draft); setResult(undefined); setOpen(false)
    } catch (reason) {
      if (!run.signal.aborted && current.current.key === input.key) setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      if (controller.current === run) { controller.current = undefined; setPhase('idle') }
    }
  }
  /** 后续编辑、保存或切换前，允许撤销本次数据替换。 */
  const undo = (): void => {
    if (!receipt || receipt.key !== current.current.key || current.current.disabled || controller.current) return
    current.current.onEdit(receipt.before); setReceipt(undefined)
  }
  return { open, instruction, rowCount, phase, progress, error,
    result: result?.key === key ? result.value : undefined,
    stale: Boolean(result && result.key !== key), requirementsChanged: Boolean(result && result.requirements !== requirements),
    receipt: receipt?.key === key ? receipt : undefined,
    canApply: Boolean(request && !disabled && result?.value.data && result.key === key && result.requirements === requirements && phase === 'idle'),
    blocking: open && (phase !== 'idle' || Boolean(result?.key === key)),
    show: (): void => { if (!current.current.disabled && request && !controller.current) setOpen(true) },
    close: (): void => { cancel(); setOpen(false) },
    setInstruction, setRowCount, cancel, invalidate, generate, apply, undo }
}

export type MockDataAssistState = ReturnType<typeof useMockDataAssist>
