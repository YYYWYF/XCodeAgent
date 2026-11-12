import { DownOutlined } from '@ant-design/icons'
import { useState, type CSSProperties, type ReactElement } from 'react'
import { cx } from '../../../../utils'
import {
  asRecord,
  schemaProperties,
  schemaType,
  stringItems,
  textValue,
  type JsonRecord
} from './TechnicalPlanDocPanelData'

type SchemaTreeProps = {
  name: string
  schema: JsonRecord
  description?: string
  schemas?: JsonRecord
  depth?: number
  isDependency?: boolean
  referencedBy?: string
  referenceTrail?: ReadonlySet<string>
  isField?: boolean
  fieldRequired?: boolean
  fieldOptional?: boolean
  showDefault?: boolean
}

/** 收集字段、数组和组合类型的引用依赖，循环引用只访问一次。 */
export function schemaDependencyNames(schema: JsonRecord, schemas: JsonRecord): string[] {
  const dependencies = new Set<string>()
  const pendingSchemas = [schema]
  while (pendingSchemas.length) {
    const current = pendingSchemas.pop() || {}
    const reference = textValue(current.$ref).replace(/^#\/components\/schemas\//, '')
    if (reference && !dependencies.has(reference)) {
      dependencies.add(reference)
      pendingSchemas.push(asRecord(schemas[reference]))
    }
    pendingSchemas.push(...schemaProperties(current).map(([, value]) => value))
    pendingSchemas.push(asRecord(current.items), asRecord(current.additionalProperties))
    for (const key of ['allOf', 'oneOf', 'anyOf', 'prefixItems']) {
      if (Array.isArray(current[key])) pendingSchemas.push(...current[key].map(asRecord))
    }
    // 空叶子不再入栈，确保没有引用的类型也能结束遍历。
    for (let index = pendingSchemas.length - 1; index >= 0; index -= 1) {
      if (!Object.keys(pendingSchemas[index]).length) pendingSchemas.splice(index, 1)
    }
  }
  return Array.from(dependencies)
}

const constraintLabels: Array<[string, string]> = [
  ['format', '格式'],
  ['maximum', '最大值'],
  ['exclusiveMinimum', '不含下限'],
  ['exclusiveMaximum', '不含上限'],
  ['multipleOf', '倍数'],
  ['minLength', '最短长度'],
  ['maxLength', '最长长度'],
  ['pattern', '匹配规则'],
  ['minItems', '最少元素'],
  ['maxItems', '最多元素'],
  ['uniqueItems', '元素唯一'],
  ['minProperties', '最少属性'],
  ['maxProperties', '最多属性'],
  ['default', '默认值'],
  ['const', '固定值'],
  ['readOnly', '只读'],
  ['writeOnly', '只写'],
  ['additionalProperties', '允许额外属性']
]

/** 将当前 Schema 的约束紧跟在字段后展示，保留 false、零和空值。 */
function SchemaDetails({
  schema,
  hideDefault = false
}: {
  schema: JsonRecord
  hideDefault?: boolean
}): ReactElement | null {
  const constraints = constraintLabels.filter(
    ([key]) =>
      key in schema &&
      !(hideDefault && key === 'default') &&
      (key !== 'additionalProperties' || typeof schema[key] === 'boolean')
  )
  if (!constraints.length) return null
  return (
    <span className={cx('technical-plan-schema-details', 'technical-plan-schema-constraints')}>
      {constraints.map(([key, label]) => (
        <span key={key}>
          {label}：{JSON.stringify(schema[key])}
        </span>
      ))}
    </span>
  )
}

/** 渲染完整类型树，展开对象、根数组、引用及组合类型；循环引用保留明确提示。 */
export function SchemaTree({
  name,
  schema,
  description,
  schemas = {},
  depth = 0,
  isDependency = false,
  referencedBy,
  referenceTrail,
  isField = false,
  fieldRequired = false,
  fieldOptional = false,
  showDefault = false
}: SchemaTreeProps): ReactElement {
  const [expanded, setExpanded] = useState(true)
  // 参数说明属于 Parameter，其他字段说明来自 Schema；只展示契约已有文字。
  const displayDescription = description?.trim() || textValue(schema.description).trim()
  const required = new Set(stringItems(schema.required))
  const properties = schemaProperties(schema)
  const activeReferenceTrail = referenceTrail || new Set([name])
  const reference = textValue(schema.$ref).replace(/^#\/components\/schemas\//, '')
  const referencedSchema = asRecord(schemas[reference])
  const circular = activeReferenceTrail.has(reference)
  const items = asRecord(schema.items)
  const itemReference = textValue(items.$ref).replace(/^#\/components\/schemas\//, '')
  const itemDefinition = asRecord(schemas[itemReference])
  // 纯引用数组直接展示命名元素类型；带额外约束、缺失或循环引用仍完整展示原节点。
  const inlineItemReference = Boolean(
    itemReference &&
      Object.keys(items).length === 1 &&
      Object.keys(itemDefinition).length &&
      !activeReferenceTrail.has(itemReference)
  )
  const extraProperties = asRecord(schema.additionalProperties)
  const compositions = ['allOf', 'oneOf', 'anyOf', 'prefixItems'].flatMap((kind) =>
    Array.isArray(schema[kind])
      ? schema[kind].map((item, index) => ({
          kind,
          index,
          schema: asRecord(item)
        }))
      : []
  )
  const hasChildren = Boolean(
    properties.length ||
      reference ||
      Object.keys(items).length ||
      Object.keys(extraProperties).length ||
      compositions.length
  )
  const treeStyle = { '--schema-depth': depth } as CSSProperties

  /** 仅切换当前类型分支的显示状态，不修改接口契约。 */
  function toggleExpanded(): void {
    setExpanded(!expanded)
  }

  return (
    <div
      className={cx(
        'technical-plan-schema-node',
        isDependency && 'technical-plan-schema-dependency'
      )}
      style={treeStyle}
    >
      <div
        className={cx(isField ? 'technical-plan-schema-field' : 'technical-plan-schema-node-head')}
      >
        <span className={cx('technical-plan-schema-name')}>
          {hasChildren ? (
            <button
              type="button"
              className={cx('technical-plan-schema-caret')}
              aria-label={`${expanded ? '收起' : '展开'} ${name}`}
              aria-expanded={expanded}
              onClick={toggleExpanded}
            >
              <DownOutlined rotate={expanded ? 0 : -90} />
            </button>
          ) : (
            <span className={cx('technical-plan-schema-caret-space')} aria-hidden="true" />
          )}
          <code>{name}</code>
        </span>
        <span className={cx('technical-plan-type')}>{schemaType(schema)}</span>
        {isField || fieldRequired || fieldOptional ? (
          <span className={cx('technical-plan-schema-status')}>
            {fieldRequired ? <span className={cx('technical-plan-required')}>必填</span> : null}
            {fieldOptional ? <span className={cx('technical-plan-type')}>可选</span> : null}
          </span>
        ) : null}
        {showDefault && isField ? (
          <span className={cx('technical-plan-schema-default')} aria-label="默认值">
            {'default' in schema ? JSON.stringify(schema.default) : '—'}
          </span>
        ) : null}
        <SchemaDetails schema={schema} hideDefault={showDefault && isField} />
        {referencedBy ? (
          <span className={cx('technical-plan-schema-reference-context')}>
            被 {referencedBy} 引用
          </span>
        ) : null}
        {displayDescription || (showDefault && isField) ? (
          <span className={cx('technical-plan-schema-description')}>
            {displayDescription || '—'}
          </span>
        ) : null}
      </div>
      {hasChildren && expanded ? (
        <div className={cx('technical-plan-schema-children')}>
          {reference ? (
            circular ? (
              <span className={cx('technical-plan-schema-details')}>循环引用：{reference}</span>
            ) : Object.keys(referencedSchema).length ? (
              <div className={cx('technical-plan-schema-dependency-rail')}>
                <SchemaTree
                  isDependency
                  name={reference}
                  referencedBy={name}
                  schema={referencedSchema}
                  schemas={schemas}
                  showDefault={showDefault}
                  depth={depth + 1}
                  referenceTrail={new Set([...activeReferenceTrail, reference])}
                />
              </div>
            ) : (
              <span className={cx('technical-plan-schema-details')}>
                引用类型暂不可读取：{reference}
              </span>
            )
          ) : null}
          {properties.map(([fieldName, fieldSchema]) => (
            <div className={cx('technical-plan-schema-field-group')} key={fieldName}>
              <SchemaTree
                name={fieldName}
                schema={fieldSchema}
                schemas={schemas}
                showDefault={showDefault}
                depth={depth + 1}
                referenceTrail={activeReferenceTrail}
                isField
                fieldRequired={required.has(fieldName)}
              />
            </div>
          ))}
          {Object.keys(items).length ? (
            inlineItemReference ? (
              <div className={cx('technical-plan-schema-dependency-rail')}>
                <SchemaTree
                  isDependency
                  name={itemReference}
                  schema={itemDefinition}
                  schemas={schemas}
                  showDefault={showDefault}
                  depth={depth + 1}
                  referenceTrail={new Set([...activeReferenceTrail, itemReference])}
                />
              </div>
            ) : (
              <SchemaTree
                name="数组元素"
                schema={items}
                schemas={schemas}
                showDefault={showDefault}
                depth={depth + 1}
                referenceTrail={activeReferenceTrail}
              />
            )
          ) : null}
          {Object.keys(extraProperties).length ? (
            <SchemaTree
              name="任意属性值"
              schema={extraProperties}
              schemas={schemas}
              showDefault={showDefault}
              depth={depth + 1}
              referenceTrail={activeReferenceTrail}
            />
          ) : null}
          {compositions.map((item) => (
            <SchemaTree
              key={`${item.kind}:${item.index}`}
              name={`${item.kind} · ${item.index + 1}`}
              schema={item.schema}
              schemas={schemas}
              showDefault={showDefault}
              depth={depth + 1}
              referenceTrail={activeReferenceTrail}
            />
          ))}
        </div>
      ) : null}
    </div>
  )
}
