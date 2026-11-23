import { Alert, Button, Input, Spin, Tag } from 'antd'
import { BulbOutlined } from '@ant-design/icons'
import { useEffect, useState, type ReactElement, type ReactNode } from 'react'
import type { MappingAssistState } from './useMappingAssist'
import { DATABASE_OPERATION_LABELS } from './model'
import MappingAssistResult from './MappingAssistResult'
import { MappingAssistSavedAnswers } from './MappingAssistIssues'
import { MAPPING_REQUIREMENTS_LIMIT } from './mappingAssistModel'
import './MappingAssist.less'

const AREA_LABELS = { operation: '数据库操作', query: '查询条件', write: '写入字段', request: '外部入参', return: '返回字段', description: '映射说明' }

/** 展示生成进度、补充说明和预览决策，映射内容由工作台原卡片展示。 */
export default function MappingAssistPanel({ assist, disabled, context, onShowReview }: {
  assist: MappingAssistState; disabled: boolean; onShowReview?: () => void
  context?: { endpointName: string; endpointPath: string; table: string; details: ReactNode }
}): ReactElement | null {
  const [editingRequirements, setEditingRequirements] = useState(false)
  useEffect(() => { if (assist.phase === 'generating' || !assist.open) setEditingRequirements(false) }, [assist.phase, assist.open])
  if (!assist.open) return null
  const running = assist.phase !== 'idle'
  const result = assist.result
  const showInput = assist.phase !== 'generating' && (!result || editingRequirements)
  return <section className="mapping-assist-panel" aria-label="AI 生成映射">
    <div className="mapping-assist-heading"><strong><BulbOutlined /> {result?.mapping ? 'AI 映射预览' : 'AI 生成映射'} {result && <Tag>{result.mapping ? '尚未采纳' : '操作待明确'}</Tag>}</strong>
      {result ? <div className="mapping-assist-heading-actions">
        <Button type="link" disabled={running} onClick={() => setEditingRequirements(!editingRequirements)}>{editingRequirements ? '收起补充信息' : '编辑补充信息'}</Button>
        {result.mapping && onShowReview && <Button type="link" onClick={onShowReview}>查看采纳选项</Button>}
      </div>
        : <Button type="text" onClick={assist.close}>收起</Button>}</div>
    {context && <div className="mapping-assist-context">
      <span>API 契约 · {context.endpointName} <code>{context.endpointPath}</code> ＋ {assist.isStatic ? '静态数据' : assist.isExternal ? '外部 API' : '数据表'} <strong>{context.table}</strong>
        {result?.operation && <> · 操作 <strong className="mapping-assist-operation-value">{DATABASE_OPERATION_LABELS[result.operation]}</strong></>}</span>
      <details><summary>输入与判断依据</summary><div>{context.details}
        {result && <><p>{result.operationReason}</p>{result.operation && <p className="mapping-assist-secondary">{assist.currentOperation === result.operation ? '与当前草稿的操作一致。' : `当前草稿：${assist.currentOperation ? DATABASE_OPERATION_LABELS[assist.currentOperation] : '未选择'} → 生成操作：${DATABASE_OPERATION_LABELS[result.operation]}。`}</p>}</>}
        <p className="mapping-assist-secondary">根据当前契约与{assist.isStatic ? '已应用的静态结构' : assist.isExternal ? '外部 API 结构' : '表结构'}重新生成，已有映射内容不参与判断。</p></div></details>
    </div>}
    {showInput && <div className="mapping-assist-input"><label>补充信息 <span className="mapping-assist-secondary">（选填）</span><Input.TextArea aria-label="AI 映射补充信息" maxLength={MAPPING_REQUIREMENTS_LIMIT} autoSize={{ minRows: 2, maxRows: 6 }} disabled={running || disabled}
      value={assist.instruction} placeholder={assist.isStatic ? '可补充静态字段如何返回，例如将 items 映射为接口的 list。' : assist.isExternal ? '可补充外部接口的入参取值或返回规则，例如将商品关键词传给外部接口。' : '可补充接口说明中未写明的规则，例如名称和编号模糊匹配，分类和状态精确匹配。'} onChange={(event) => assist.setInstruction(event.target.value)} /></label>
      <MappingAssistSavedAnswers assist={assist} disabled={disabled} />
      <div className="mapping-assist-toolbar"><span className="mapping-assist-secondary">{assist.isStatic ? 'AI 根据已应用的静态数据生成返回映射。需要样例时，可在数据内容卡片中生成 Mock 数据。' : assist.isExternal ? 'AI 依次生成外部入参、应用返回字段和说明，完成后统一预览。' : 'AI 先识别数据库操作，再生成对应映射，完成后统一预览。'}</span>
        <Button type="primary" disabled={disabled || running || assist.requirementsLength > MAPPING_REQUIREMENTS_LIMIT} onClick={() => void assist.generate()}>{result ? '重新生成' : '生成映射'}</Button>
      </div></div>}
    {assist.phase === 'generating' && <div className="mapping-assist-progress" aria-live="polite">
      <div className="mapping-assist-toolbar"><strong><Spin size="small" /> {assist.progress?.message || '正在准备生成'}</strong><Button onClick={assist.cancel}>取消生成</Button></div>
      <ol>{assist.progressSteps.map((step, index) => <li key={step.stage} className={index === assist.progressSteps.length - 1 ? 'is-running' : 'is-completed'}>
        <span aria-hidden="true">{index === assist.progressSteps.length - 1 ? '◉' : '✓'}</span> {step.message.replace(/正在|（\d+\/\d+）/g, '')}
      </li>)}</ol><p className="mapping-assist-secondary">完成后统一预览，当前草稿尚未修改。</p>
    </div>}
    {assist.error && <Alert type="error" showIcon message={assist.error} description={assist.failure && <>
      <p>{assist.failure.completedStages.length ? `已完成：${assist.failure.completedStages.map((area) => AREA_LABELS[area]).join('、')}。` : ''}尚未修改当前草稿。可复用本轮前序结果，从失败步骤继续。</p>
      <Button disabled={disabled || running} onClick={() => void assist.generate(true)}>从{AREA_LABELS[assist.failure.failedStage]}继续</Button>
    </>} />}
    {assist.stale && <Alert type="warning" showIcon message="当前草稿或来源已变化，请重新生成映射。" />}
    {assist.requirementsChanged && <Alert type="warning" showIcon message="补充信息已变更" description="当前预览仍为上一次结果，重新生成后才能采纳。" />}
    {assist.requirementsLength > MAPPING_REQUIREMENTS_LIMIT && <Alert type="warning" showIcon message="补充信息合计超过 4000 字，请精简后重新生成。" />}
    {result && !result.mapping && <MappingAssistResult assist={assist} result={result} disabled={disabled} />}
  </section>
}
