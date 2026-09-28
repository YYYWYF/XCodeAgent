import Icon, { ApiOutlined, DatabaseOutlined, FunctionOutlined, LinkOutlined } from '@ant-design/icons'
import type { ReactElement } from 'react'
import type { WorkflowApiBuiltinField, WorkflowApiEndpointFieldSnapshot, WorkflowApiSourceField, WorkflowApiValueRight } from '../../../../typings'
import BusinessRuleEntry, { type BusinessRuleInput } from './BusinessRuleEntry'
import { BUILTIN_OPTIONS, valueText } from './valueRules'

type Props = { title: string; right?: WorkflowApiValueRight; sourceFields?: WorkflowApiSourceField[]; businessDescription?: string; endpointFields?: WorkflowApiEndpointFieldSnapshot[]; builtinFields?: WorkflowApiBuiltinField[]; showDescriptions?: boolean }
const LOCATION_LABELS: Record<string, string> = { path: 'Path', query: 'Query', header: 'Header', request_body: 'Body' }
const PARAMETER_LOCATION_LABELS: Record<string, string> = { path: '路径参数', query: '查询参数', header: '请求头', request_body: '请求体', response_body: '响应体' }

/** 在详情行中并列展示值来源与取值内容，复杂规则仅保留查看入口。 */
export default function ReadOnlyValue({ title, right, sourceFields = [], businessDescription, endpointFields, builtinFields, showDescriptions = true }: Props): ReactElement {
  const business = Boolean(businessDescription) || right?.kind === 'business'
  const fixed = right?.kind === 'fixed'
  const endpoint = right?.kind === 'endpoint' ? right.endpointField : undefined
  const externalSource = sourceFields.some((source) => source.sourceType === 'external_api')
  const sources = sourceFields.map((source) => ({
    name: source.sourceType === 'database' ? source.column : source.path,
    description: source.description,
    location: source.sourceType === 'external_api' ? source.section : undefined
  }))
  // 接口参数仅展示字段名和归属标签，释义只用于数据源字段。
  const values = endpoint ? [{ name: endpoint.path, description: undefined, location: endpoint.location }] : sources
  const inputEndpoints = endpointFields ?? (right?.kind === 'business' ? right.endpointFields : [])
  const inputBuiltins = builtinFields ?? (right?.kind === 'business' ? right.builtinFields : [])
  const ruleInputs: BusinessRuleInput[] = [
    ...sourceFields.map((source): BusinessRuleInput => ({ source: source.sourceType, name: source.sourceType === 'database' ? source.column : source.path,
      description: source.description, type: source.type,
      dataSource: source.sourceType === 'database' ? [source.schema, source.table].filter(Boolean).join('.') : source.operationId,
      parameterLocation: source.sourceType === 'external_api' ? PARAMETER_LOCATION_LABELS[source.section] : undefined })),
    ...inputEndpoints.map((field): BusinessRuleInput => ({ source: 'endpoint', name: field.path, type: field.type, parameterLocation: PARAMETER_LOCATION_LABELS[field.location] })),
    ...inputBuiltins.map((key): BusinessRuleInput => ({ source: 'builtin', name: BUILTIN_OPTIONS.find((item) => item.value === key)?.label || key }))
  ]
  return <>
    <span className="field-detail-source">
      {business ? <FunctionOutlined aria-hidden="true" /> : fixed ? <Icon aria-hidden="true"><svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" strokeWidth="1.6"><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M7 9h10M7 15h10" /></svg></Icon>
        : endpoint || externalSource ? <ApiOutlined aria-hidden="true" /> : sources.length ? <DatabaseOutlined aria-hidden="true" /> : <LinkOutlined aria-hidden="true" />}
      {business ? '业务处理' : fixed ? '固定值' : endpoint ? '接口参数' : sources.length ? '数据源字段' : '未配置'}
    </span>
    <div className="field-detail-content">
      {business ? <BusinessRuleEntry title={title} configured={Boolean(businessDescription || (right?.kind === 'business' && right.businessDescription))}
        inputCount={ruleInputs.length} disabled={false} inline description={businessDescription || (right?.kind === 'business' ? right.businessDescription : '')}
        inputs={ruleInputs} />
        : fixed ? <span className="field-detail-value" title={valueText(right?.kind === 'fixed' ? right.value : undefined)}>{valueText(right?.kind === 'fixed' ? right.value : undefined) || '—'}</span>
          : values.length ? values.map((value, index) => <span className="field-detail-value" key={`${index}:${value.name}`} title={`${value.name}${showDescriptions && value.description?.trim() ? `（${value.description.trim()}）` : ''}`}>
            <code>{value.name}</code>{showDescriptions && value.description?.trim() ? <span className="field-detail-meaning">（{value.description.trim()}）</span> : null}
            {value.location && LOCATION_LABELS[value.location] ? <span className="field-detail-location">{LOCATION_LABELS[value.location]}</span> : null}
          </span>) : <span className="field-detail-empty">—</span>}
    </div>
  </>
}
