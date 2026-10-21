import { Button } from 'antd'
import type { ReactElement } from 'react'
import type { GeneratedMapping, MappingAssistArea } from '../../../../typings/endpointDesignAssist'
import { apiDesignFieldKey } from '../WorkflowRunCard/apiDesignSerialization'
import MappingAssistQueryPreview from './MappingAssistQueryPreview'
import MappingAssistReview from './MappingAssistReview'
import ReadOnlyValue from './ReadOnlyValue'

type Props = { mapping: GeneratedMapping; area: MappingAssistArea; onShowIssues: (key: string) => void }

/** 用独立只读视图审阅生成结果，保留原编辑表单的结构与交互。 */
export default function MappingAssistPreview({ mapping, area, onShowIssues }: Props): ReactElement {
  if (area === 'query') return <MappingAssistQueryPreview query={mapping.databaseQuery} />
  if (area === 'write') return <section aria-label="生成的写入字段">
    <div className="mapping-review-write-head"><span>数据表字段</span><span>值来源</span><span>取值内容</span></div>
    {mapping.databaseWrites?.length ? mapping.databaseWrites.map((write, index) => <div className="mapping-review-write-row" key={`${write.column}:${index}`}>
      <div><code>{write.column}</code>{write.description && <span className="mapping-assist-secondary">（{write.description}）</span>}<small className="mapping-assist-secondary">{write.type}</small><MappingAssistReview area="write" target={write.column} /></div>
      <ReadOnlyValue title={write.column} right={write.right} />
    </div>) : <p className="mapping-assist-secondary">尚未配置写入字段</p>}
  </section>
  if (area === 'description') {
    const description = mapping.implementationDescription?.trim() || ''
    const summary = description.match(/^[^。！？\n]+[。！？]?/)?.[0] || description
    return <section aria-label="生成的映射说明"><strong>接口映射说明</strong>
      <p className="mapping-review-description">{summary || '尚未填写映射说明'}</p>
      {description !== summary && <details><summary>展开完整说明</summary><p className="mapping-review-description">{description}</p></details>}
      <MappingAssistReview area="description" target="implementationDescription" />
    </section>
  }
  const fields = mapping.fieldMappings.filter((item) => item.endpointField.side === 'response')
  return <section aria-label="生成的返回字段">
    <div className="mapping-review-return-head"><span>返回字段</span><span>值来源</span><span>取值内容</span><span>状态</span></div>
    {fields.length ? fields.map((item) => {
      const source = item.mappingType === 'source_mapping' ? item : undefined
      const right = item.mappingType === 'value_mapping' ? item.right : undefined
      const description = item.mappingType === 'business_description' ? item.businessDescription : source?.businessDescription
      return <div className="mapping-review-return-row" key={apiDesignFieldKey(item.endpointField)}>
        <div><code>{item.endpointField.path}</code><small className="mapping-assist-secondary">{item.endpointField.type}{item.endpointField.required ? ' · 必填' : ''}</small>
          <MappingAssistReview area="return" target={apiDesignFieldKey(item.endpointField)} /></div>
        <ReadOnlyValue title={item.endpointField.path} right={right} sourceFields={source?.sourceFields} businessDescription={description}
          endpointFields={source?.endpointFields} builtinFields={source?.builtinFields} />
        {item.mappingType === 'unconfigured' ? <Button type="link" className="mapping-review-pending" onClick={() => onShowIssues(apiDesignFieldKey(item.endpointField))} aria-label={`处理 ${item.endpointField.path} 待明确问题`}>待明确</Button>
          : <span className="mapping-assist-secondary">已映射</span>}
      </div>
    }) : <p className="mapping-assist-secondary">无返回字段</p>}
  </section>
}
