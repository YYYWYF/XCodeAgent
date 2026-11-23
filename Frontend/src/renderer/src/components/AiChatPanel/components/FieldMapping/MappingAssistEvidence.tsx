import { DownOutlined, InfoCircleOutlined, ProfileOutlined } from '@ant-design/icons'
import { Tooltip } from 'antd'
import { useEffect, useRef, useState, type ReactElement } from 'react'
import type { MappingAssistState } from './useMappingAssist'

type FieldRow = { label: string; fields: string[] }

export type MappingAssistEvidenceContext = {
  sourceName: string
  apiFieldRows: FieldRow[]
  sourceFieldRow: FieldRow
}

/** 每组字段仅展示一行，按实际宽度保留完整字段和省略提示。 */
function EvidenceFieldRow({ label, fields }: FieldRow): ReactElement {
  const row = useRef<HTMLDivElement>(null)
  const measure = useRef<HTMLDivElement>(null)
  const [visibleCount, setVisibleCount] = useState(fields.length)

  useEffect(() => {
    const viewport = row.current
    const rail = measure.current
    if (!viewport || !rail || !fields.length) return

    /** 用隐藏的完整字段行计算可见数量，并在溢出时预留计数标签的宽度。 */
    const updateVisibleCount = (): void => {
      const availableWidth = viewport.getBoundingClientRect().width
      if (availableWidth <= 0) return
      const railLeft = rail.getBoundingClientRect().left
      const lastFieldRight = rail.children[fields.length - 1].getBoundingClientRect().right
      let count = fields.length
      if (lastFieldRight - railLeft > availableWidth) {
        const badgeRight = rail.children[fields.length].getBoundingClientRect().right
        const fieldsWidth = availableWidth - (badgeRight - lastFieldRight)
        while (count > 0 && rail.children[count - 1].getBoundingClientRect().right - railLeft > fieldsWidth) count -= 1
      }
      setVisibleCount(count)
    }

    updateVisibleCount()
    const observer = new ResizeObserver(updateVisibleCount)
    observer.observe(viewport)
    observer.observe(rail)
    return () => observer.disconnect()
  }, [fields])

  const omitted = fields.slice(visibleCount)
  const omittedNames = omitted.join('、')
  return <div className="mapping-assist-evidence-row mapping-assist-field-row">
    <dt>{label}</dt>
    <dd>
      <div className="mapping-assist-field-chips" ref={row}>
        {fields.length ? fields.slice(0, visibleCount).map((field, index) => <code key={index}>{field}</code>) : <span className="mapping-assist-secondary">无</span>}
        {omitted.length > 0 && <Tooltip title={omittedNames} trigger={['hover', 'focus']} overlayClassName="mapping-assist-field-tooltip">
          <button type="button" className="mapping-assist-field-more" aria-label={`省略 ${omitted.length} 个${label}：${omittedNames}`}>… +{omitted.length}</button>
        </Tooltip>}
      </div>
      {fields.length > 0 && <div className="mapping-assist-field-chips is-measuring" ref={measure} aria-hidden="true">
        {fields.map((field, index) => <code key={index}>{field}</code>)}
        <span className="mapping-assist-field-more">… +{fields.length}</span>
      </div>}
    </dd>
  </div>
}

/** 展示本轮映射使用的数据来源和字段。 */
export default function MappingAssistEvidence({ context, assist }: {
  context: MappingAssistEvidenceContext
  assist: MappingAssistState
}): ReactElement {
  const { result } = assist
  const sourceLabel = assist.isStatic ? '静态数据' : assist.isExternal ? '外部 API' : '数据表'
  const sourceStructure = assist.isStatic ? '已应用的静态结构' : assist.isExternal ? '外部 API 结构' : '表结构'
  return <details className="mapping-assist-evidence" open={!result}>
    <summary>
      <ProfileOutlined aria-hidden="true" className="mapping-assist-evidence-icon" />
      <strong>判断依据</strong>
      <DownOutlined aria-hidden="true" className="mapping-assist-evidence-chevron" />
    </summary>
    <div className="mapping-assist-evidence-body">
      <dl className="mapping-assist-evidence-api-fields">
        {context.apiFieldRows.map((row) => <EvidenceFieldRow key={row.label} {...row} />)}
      </dl>
      <dl className="mapping-assist-evidence-sources">
        <div className="mapping-assist-evidence-row">
          <dt>{sourceLabel}</dt>
          <dd className="mapping-assist-evidence-source"><code>{context.sourceName}</code></dd>
        </div>
        <EvidenceFieldRow {...context.sourceFieldRow} />
      </dl>
      <p className="mapping-assist-evidence-note mapping-assist-secondary"><InfoCircleOutlined aria-hidden="true" />本次生成使用当前 API 契约和{sourceStructure}。</p>
    </div>
  </details>
}
