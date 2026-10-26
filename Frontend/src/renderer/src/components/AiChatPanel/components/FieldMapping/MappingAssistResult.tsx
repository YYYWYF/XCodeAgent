import { Button } from 'antd'
import { ExclamationCircleFilled } from '@ant-design/icons'
import { useId, useState, type ReactElement } from 'react'
import type { MappingAssistance, MappingAssistArea } from '../../../../typings/endpointDesignAssist'
import type { MappingAssistState } from './useMappingAssist'
import { mappingAssistIssues, mappingAssistSections } from './mappingAssistReviewModel'
import { mappingInputKey, MAPPING_REQUIREMENTS_LIMIT } from './mappingAssistModel'
import { MappingReviewContext } from './MappingAssistReview'
import MappingAssistIssues from './MappingAssistIssues'
import MappingAssistPreview from './MappingAssistPreview'
import './MappingAssistResult.less'

/** 以分区审阅替代长表单，整体替换仍由原 AG-UI 动作完成。 */
export default function MappingAssistResult({ assist, result, disabled }: {
  assist: MappingAssistState; result: MappingAssistance; disabled: boolean
}): ReactElement {
  const issues = mappingAssistIssues(result)
  const sections = mappingAssistSections(result)
  const initialArea: 'issues' | MappingAssistArea = issues.length ? 'issues' : sections[0]?.key || 'issues'
  const [review, setReview] = useState<{ result: MappingAssistance; area: typeof initialArea; focusKey?: string }>({ result, area: initialArea })
  const [confirmation, setConfirmation] = useState<{ result: MappingAssistance; inputKey: string }>()
  // 新一轮结果即使被 React 合并提交，也不能沿用上一轮页签和替换确认。
  const area = review.result === result ? review.area : initialArea
  const focusKey = review.result === result ? review.focusKey : undefined
  const id = useId()
  const running = assist.phase !== 'idle'
  const showRegenerate = issues.length > 0 || assist.requirementsChanged
  const canRegenerate = !disabled && !running && Boolean(assist.instruction.trim()) && assist.requirementsChanged && assist.requirementsLength <= MAPPING_REQUIREMENTS_LIMIT
  const pending = result.mapping?.fieldMappings.filter((item) => item.endpointField.side === 'response' && item.mappingType === 'unconfigured') || []
  const pendingRequests = result.mapping?.externalApiBindings?.filter((item) => !item.right) || []
  // 确认仅对应本次输入，继续填写、切换视图或失效后必须重新核对。
  const confirmationKey = mappingInputKey([result.baseline, assist.instruction, assist.clarification, assist.answers])
  const confirming = !disabled && assist.canApply && !running && confirmation?.result === result && confirmation.inputKey === confirmationKey

  /** 切换审阅区时关闭尚未执行的替换确认。 */
  const selectArea = (next: typeof area, target?: string): void => { setReview({ result, area: next, focusKey: target }); setConfirmation(undefined) }

  return <div className="mapping-assist-result">
    {issues.length > 0 && <div className="mapping-review-notice" role="status"><ExclamationCircleFilled aria-hidden="true" /><span>{issues.length} 项信息待明确{pending.length > 0 ? `，其中 ${pending.length} 个返回字段尚未配置。替换草稿时将保持未配置，不保留原有取值。` : '，请核对下方问题后继续。'}</span></div>}
    <nav className="mapping-review-nav" aria-label="映射结果分区">
      <Button type="text" aria-pressed={area === 'issues'} onClick={() => selectArea('issues')}>待明确 · {issues.length}</Button>
      {sections.map((section) => <Button key={section.key} type="text" aria-pressed={area === section.key} onClick={() => selectArea(section.key)}>{section.label}{section.count !== undefined ? ` · ${section.count}` : ''}</Button>)}
    </nav>
    <div className="mapping-review-content"><MappingReviewContext.Provider value={result.reviews}>
      {area === 'issues' ? <MappingAssistIssues issues={issues} assist={assist} disabled={disabled} focusKey={focusKey} />
        : result.mapping && <MappingAssistPreview mapping={result.mapping} area={area} onShowIssues={(key) => selectArea('issues', key)} />}
    </MappingReviewContext.Provider></div>
    <footer className="mapping-assist-replace">
      {result.mapping && confirming && <section className="mapping-review-confirm" role="alertdialog" aria-labelledby={`${id}-title`} aria-describedby={`${id}-description`}>
        <strong id={`${id}-title`}>确认整体替换当前草稿？</strong>
        <p id={`${id}-description`}>{assist.isStatic ? '全部返回映射将替换为本轮结果，静态内容沿用当前草稿。' : assist.isExternal ? '外部入参、返回字段和映射说明将一并替换。' : '数据库操作及全部映射将一并替换。'}{pending.length > 0 && <>以下返回字段将置为未配置：{pending.map((item) => item.endpointField.path).join('、')}。</>}{pendingRequests.length > 0 && <>以下外部入参将置为未配置：{pendingRequests.map((item) => `${item.externalField.section}.${item.externalField.path}`).join('、')}。</>}正式生效仍需保存并确认。</p>
        <div className="mapping-assist-preview-actions"><Button autoFocus type="primary" onClick={() => { setConfirmation(undefined); void assist.apply() }}>确认替换草稿</Button>
          <Button onClick={() => setConfirmation(undefined)}>返回预览</Button></div>
      </section>}
      <div className="mapping-assist-toolbar"><div><strong>{result.mapping ? '整体替换当前草稿' : '补充后继续生成'}</strong>
        <p className="mapping-assist-secondary">{result.mapping ? `${assist.isStatic ? '全部返回映射' : assist.isExternal ? '入参、返回和说明' : '操作及全部映射'}一并回填，可撤销一次；正式生效仍需保存并确认。` : '请先明确数据库操作，重新生成后统一预览映射。'}</p></div>
        <div className="mapping-review-footer-actions">
          {showRegenerate && <Button disabled={!canRegenerate} onClick={() => { setConfirmation(undefined); void assist.generate() }}>补充并重新生成</Button>}
          {result.mapping && <Button type="primary" loading={assist.phase === 'applying'} disabled={disabled || running || !assist.canApply}
            onClick={() => setConfirmation({ result, inputKey: confirmationKey })}>替换草稿</Button>}
        </div>
      </div>
    </footer>
  </div>
}
