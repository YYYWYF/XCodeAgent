import { DownOutlined } from '@ant-design/icons'
import type { CSSProperties, ReactElement } from 'react'
import { cx } from '../../../../utils'
import {
  asRecord,
  schemaProperties,
  schemaReferenceName,
  schemaType,
  stringItems,
  textValue,
  type JsonRecord
} from './TechnicalPlanDocPanelData'

type SchemaTreeProps = {
  name: string
  schema: JsonRecord
  schemas?: JsonRecord
  depth?: number
  isDependency?: boolean
  referencedBy?: string
  referenceTrail?: ReadonlySet<string>
}

/** 收集当前 Schema 可解析的全部引用依赖，并通过集合避免循环引用。 */
export function schemaDependencyNames(schema: JsonRecord, schemas: JsonRecord): string[] {
  const dependencies = new Set<string>()
  const pendingSchemas = [schema]

  while (pendingSchemas.length) {
    const currentSchema = pendingSchemas.pop() || {}
    schemaProperties(currentSchema).forEach(([, fieldSchema]) => {
      const referenceName = schemaReferenceName(fieldSchema)
      if (referenceName && !dependencies.has(referenceName)) {
        dependencies.add(referenceName)
        const referencedSchema = asRecord(schemas[referenceName])
        if (Object.keys(referencedSchema).length) pendingSchemas.push(referencedSchema)
      }

      const inlineSchema =
        textValue(fieldSchema.type) === 'array' ? asRecord(fieldSchema.items) : fieldSchema
      if (schemaProperties(inlineSchema).length) pendingSchemas.push(inlineSchema)
    })
  }

  return Array.from(dependencies)
}

/** 渲染 Schema 字段树，并把可解析的引用类型就地展开为浅色依赖卡片。 */
export function SchemaTree({
  name,
  schema,
  schemas = {},
  depth = 0,
  isDependency = false,
  referencedBy,
  referenceTrail
}: SchemaTreeProps): ReactElement {
  const required = new Set(stringItems(schema.required))
  const properties = schemaProperties(schema)
  const treeStyle = { '--schema-depth': depth } as CSSProperties
  const activeReferenceTrail = referenceTrail || new Set([name])

  return (
    <div
      className={cx(
        'technical-plan-schema-node',
        isDependency && 'technical-plan-schema-dependency'
      )}
      style={treeStyle}
    >
      <div className={cx('technical-plan-schema-node-head')}>
        <span className={cx('technical-plan-schema-caret')} aria-hidden="true">
          {properties.length ? <DownOutlined /> : <span />}
        </span>
        <code>{name}</code>
        <span className={cx('technical-plan-type')}>{schemaType(schema)}</span>
        {referencedBy ? (
          <span className={cx('technical-plan-schema-reference-context')}>
            被 {referencedBy} 引用
          </span>
        ) : null}
      </div>
      {properties.map(([fieldName, fieldSchema]) => {
        const entityFieldRef = textValue(fieldSchema.entity_field_ref)
        const referenceName = schemaReferenceName(fieldSchema)
        const referencedSchema = asRecord(schemas[referenceName])
        const canExpandReference =
          Boolean(referenceName) &&
          Object.keys(referencedSchema).length > 0 &&
          !activeReferenceTrail.has(referenceName)
        const inlineSchema =
          textValue(fieldSchema.type) === 'array' ? asRecord(fieldSchema.items) : fieldSchema
        const nestedProperties = schemaProperties(inlineSchema)
        const referenceLabel =
          textValue(fieldSchema.type) === 'array' ? `${fieldName}[]` : fieldName

        return (
          <div className={cx('technical-plan-schema-field-group')} key={fieldName}>
            <div
              className={cx('technical-plan-schema-field')}
              style={{ '--schema-depth': depth + 1 } as CSSProperties}
            >
              <span className={cx('technical-plan-schema-branch')} aria-hidden="true" />
              {canExpandReference ? (
                <span className={cx('technical-plan-schema-reference-caret')} aria-hidden="true">
                  <DownOutlined />
                </span>
              ) : null}
              <code>{fieldName}</code>
              <span
                className={cx(
                  'technical-plan-type',
                  referenceName && 'technical-plan-schema-reference-type'
                )}
              >
                {schemaType(fieldSchema)}
              </span>
              {required.has(fieldName) ? (
                <span className={cx('technical-plan-required')}>必填</span>
              ) : null}
              {entityFieldRef ? (
                <span className={cx('technical-plan-lineage')}>{entityFieldRef}</span>
              ) : null}
            </div>
            {canExpandReference ? (
              <div className={cx('technical-plan-schema-dependency-rail')}>
                <SchemaTree
                  isDependency
                  name={referenceName}
                  referencedBy={referenceLabel}
                  referenceTrail={new Set([...activeReferenceTrail, referenceName])}
                  schema={referencedSchema}
                  schemas={schemas}
                />
              </div>
            ) : nestedProperties.length ? (
              <SchemaTree
                depth={depth + 1}
                name={`${fieldName}.*`}
                referenceTrail={activeReferenceTrail}
                schema={inlineSchema}
                schemas={schemas}
              />
            ) : null}
          </div>
        )
      })}
    </div>
  )
}
