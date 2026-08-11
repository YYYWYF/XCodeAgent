import {
  DatabaseOutlined,
  DeleteOutlined,
  DesktopOutlined,
  PartitionOutlined,
  PlusOutlined,
  TeamOutlined
} from '@ant-design/icons'
import { Button, Input, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { cx } from '../../utils'
import RequirementSpecFlowEditor from './RequirementSpecFlowSteps'
import './RequirementSpecEditor.less'

const { Text, Title } = Typography
const { TextArea } = Input

type Props = {
  onChange: (spec: Record<string, unknown>) => void
  rootPath: string
  spec: Record<string, unknown>
}

type ListField = 'pages' | 'user_roles' | 'business_flows'

type EditableEntityField = {
  label: string
  description: string
}

type EditableEntity = {
  id: string
  name: string
  description: string
  fields: EditableEntityField[]
}

// 将未知值安全收窄为可编辑对象。
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

// 将需求集合收窄为对象数组，过滤无法编辑的历史值。
function recordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord).filter((item) => Object.keys(item).length) : []
}

// 把字段值转换为表单需要的文本。
function textValue(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : String(value)
}

// 把数据源实体（对象或旧字符串）归一为需求层可编辑结构，只保留展示信息。
function editorEntities(value: unknown): EditableEntity[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item, index) => {
      const record = asRecord(item)
      if (!Object.keys(record).length && typeof item === 'string' && item.trim()) {
        return { id: item.trim(), name: item.trim(), description: '', fields: [] }
      }
      const fields = (Array.isArray(record.fields) ? record.fields : []).map((field) => {
        const fieldRecord = asRecord(field)
        return {
          label: textValue(fieldRecord.label || fieldRecord.name),
          description: textValue(fieldRecord.description)
        }
      })
      return {
        id: textValue(record.id) || textValue(record.name) || `entity-${index}`,
        name: textValue(record.name) || textValue(record.id) || `实体 ${index + 1}`,
        description: textValue(record.description),
        fields
      }
    })
    .filter((entity) => entity.name || entity.fields.length)
}

// 为新增条目生成当前草稿内稳定的标识。
function draftId(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`
}

// 渲染带图标、新增操作和可编辑内容的模块。
function EditorSection({
  children,
  icon,
  onAdd,
  title
}: {
  children: ReactNode
  icon: ReactNode
  onAdd: () => void
  title: string
}): ReactElement {
  return (
    <section className={cx('requirement-editor-section')}>
      <header>
        <span className={cx('requirement-summary-section-icon')}>{icon}</span>
        <Title level={5}>{title}</Title>
        <Button icon={<PlusOutlined />} onClick={onAdd} size="small" type="text">
          新增
        </Button>
      </header>
      <div className={cx('requirement-editor-grid')}>{children}</div>
    </section>
  )
}

// 渲染单个可删除的需求条目容器。
function EditorItem({
  children,
  onRemove
}: {
  children: ReactNode
  onRemove: () => void
}): ReactElement {
  return (
    <article className={cx('requirement-editor-item')}>
      <Button
        aria-label="删除该需求项"
        className={cx('requirement-editor-remove')}
        icon={<DeleteOutlined />}
        onClick={onRemove}
        size="small"
        type="text"
      />
      {children}
    </article>
  )
}

// 为每个编辑控件展示明确的字段名称，避免仅依赖占位文本表达含义。
function EditorField({ children, label }: { children: ReactNode; label: string }): ReactElement {
  return (
    <div className={cx('requirement-editor-field')}>
      <Text>{label}</Text>
      {children}
    </div>
  )
}

// 以结构化表单编辑概览中的应用、页面、角色、流程和数据源。
export default function RequirementSpecEditor({
  onChange,
  rootPath,
  spec
}: Props): ReactElement {
  const appInfo = asRecord(spec.app_info)

  // 只更新应用定位字段，保留内部规划元数据。
  const updateApp = (field: string, value: string): void => {
    onChange({ ...spec, app_info: { ...appInfo, [field]: value } })
  }

  // 替换指定模块的整个条目列表。
  const replaceList = (field: ListField, items: Record<string, unknown>[]): void => {
    onChange({ ...spec, [field]: items })
  }

  // 修改指定模块的单个条目字段。
  const updateItem = (field: ListField, index: number, key: string, value: unknown): void => {
    const items = recordList(spec[field])
    replaceList(
      field,
      items.map((item, itemIndex) => (itemIndex === index ? { ...item, [key]: value } : item))
    )
  }

  // 在模块末尾追加一个带默认值的条目。
  const addItem = (field: ListField, item: Record<string, unknown>): void => {
    replaceList(field, [...recordList(spec[field]), item])
  }

  // 从模块中删除指定条目。
  const removeItem = (field: ListField, index: number): void => {
    replaceList(
      field,
      recordList(spec[field]).filter((_item, itemIndex) => itemIndex !== index)
    )
  }

  // 更新单个实体字段。
  const updateEntity = (
    entityIndex: number,
    key: keyof EditableEntity,
    value: unknown
  ): void => {
    const entities = editorEntities(spec.entities)
    const nextEntities = entities.map((entity, itemIndex) =>
      itemIndex === entityIndex ? { ...entity, [key]: value } : entity
    )
    onChange({ ...spec, entities: nextEntities })
  }

  // 更新实体下的单个展示信息项。
  const updateEntityField = (
    entityIndex: number,
    fieldIndex: number,
    key: keyof EditableEntityField,
    value: string
  ): void => {
    const entities = editorEntities(spec.entities)
    const entity = entities[entityIndex]
    if (!entity) return
    const nextFields = entity.fields.map((field, itemIndex) =>
      itemIndex === fieldIndex ? { ...field, [key]: value } : field
    )
    updateEntity(entityIndex, 'fields', nextFields)
  }

  // 在实体末尾追加一个展示信息项。
  const addEntityField = (entityIndex: number): void => {
    const entities = editorEntities(spec.entities)
    const entity = entities[entityIndex]
    if (!entity) return
    updateEntity(entityIndex, 'fields', [
      ...entity.fields,
      { label: '新信息', description: '' }
    ])
  }

  // 删除实体下的指定展示信息项。
  const removeEntityField = (entityIndex: number, fieldIndex: number): void => {
    const entities = editorEntities(spec.entities)
    const entity = entities[entityIndex]
    if (!entity) return
    updateEntity(
      entityIndex,
      'fields',
      entity.fields.filter((_field, itemIndex) => itemIndex !== fieldIndex)
    )
  }

  // 在实体列表末尾追加一个实体。
  const addEntity = (): void => {
    const entities = editorEntities(spec.entities)
    onChange({
      ...spec,
      entities: [
      ...entities,
      { id: draftId('entity'), name: '新实体', description: '', fields: [] }
      ]
    })
  }

  // 删除指定实体。
  const removeEntity = (entityIndex: number): void => {
    const entities = editorEntities(spec.entities)
    onChange({
      ...spec,
      entities: entities.filter((_entity, itemIndex) => itemIndex !== entityIndex)
    })
  }

  return (
    <div className={cx('requirement-editor')}>
      <section className={cx('requirement-editor-app')}>
        <Text type="secondary">应用定位</Text>
        <EditorField label="应用名称">
          <Input
            onChange={(event) => updateApp('name', event.target.value)}
            placeholder="请输入应用名称"
            value={textValue(appInfo.name)}
          />
        </EditorField>
        <EditorField label="应用目标和定位">
          <TextArea
            autoSize={{ minRows: 2, maxRows: 5 }}
            onChange={(event) => updateApp('target', event.target.value)}
            placeholder="请输入应用目标和定位"
            value={textValue(appInfo.target || appInfo.description || appInfo.summary)}
          />
        </EditorField>
      </section>

      <EditorSection
        icon={<DesktopOutlined />}
        onAdd={() =>
          addItem('pages', {
            pageId: draftId('page'),
            name: '新页面',
            path: rootPath && rootPath !== '/' ? `${rootPath}/` : '/',
            description: ''
          })
        }
        title="页面"
      >
        {recordList(spec.pages).map((item, index) => (
          <EditorItem
            key={textValue(item.pageId) || `page-${index}`}
            onRemove={() => removeItem('pages', index)}
          >
            <EditorField label="页面名称">
              <Input
                onChange={(event) => updateItem('pages', index, 'name', event.target.value)}
                placeholder="请输入页面名称"
                value={textValue(item.name)}
              />
            </EditorField>
            <EditorField label="页面路由">
              <Input
                addonBefore={rootPath && rootPath !== '/' ? rootPath : undefined}
                onChange={(event) => {
                  const userInput = event.target.value
                  const fullPath =
                    rootPath && rootPath !== '/'
                      ? userInput.startsWith('/')
                        ? `${rootPath}${userInput}`
                        : `${rootPath}/${userInput}`
                      : userInput
                  updateItem('pages', index, 'path', fullPath)
                }}
                placeholder="例如 /orders"
                value={
                  rootPath && rootPath !== '/'
                    ? String(item.path || '').startsWith(rootPath)
                      ? String(item.path).slice(rootPath.length) || '/'
                      : String(item.path)
                    : textValue(item.path)
                }
              />
            </EditorField>
            <EditorField label="页面说明">
              <TextArea
                autoSize={{ minRows: 2, maxRows: 4 }}
                onChange={(event) => updateItem('pages', index, 'description', event.target.value)}
                placeholder="请输入页面说明"
                value={textValue(item.description)}
              />
            </EditorField>
          </EditorItem>
        ))}
      </EditorSection>

      <EditorSection
        icon={<TeamOutlined />}
        onAdd={() =>
          addItem('user_roles', {
            id: draftId('role'),
            name: '新角色',
            description: '',
            permissions: []
          })
        }
        title="用户角色"
      >
        {recordList(spec.user_roles).map((item, index) => (
          <EditorItem
            key={textValue(item.id) || `role-${index}`}
            onRemove={() => removeItem('user_roles', index)}
          >
            <EditorField label="角色名称">
              <Input
                onChange={(event) => updateItem('user_roles', index, 'name', event.target.value)}
                placeholder="请输入角色名称"
                value={textValue(item.name)}
              />
            </EditorField>
            <EditorField label="角色说明">
              <TextArea
                autoSize={{ minRows: 2, maxRows: 4 }}
                onChange={(event) =>
                  updateItem('user_roles', index, 'description', event.target.value)
                }
                placeholder="请输入角色说明"
                value={textValue(item.description)}
              />
            </EditorField>
          </EditorItem>
        ))}
      </EditorSection>

      <EditorSection
        icon={<PartitionOutlined />}
        onAdd={() =>
          addItem('business_flows', {
            id: draftId('flow'),
            name: '新业务流程',
            description: '',
            steps: []
          })
        }
        title="核心业务流程"
      >
        {recordList(spec.business_flows).map((item, index) => (
          <RequirementSpecFlowEditor
            flowIndex={index}
            item={item}
            key={textValue(item.id) || `flow-${index}`}
            onRemove={(itemIndex) => removeItem('business_flows', itemIndex)}
            onUpdate={(itemIndex, key, value) =>
              updateItem('business_flows', itemIndex, key, value)
            }
          />
        ))}
      </EditorSection>

      <EditorSection
        icon={<DatabaseOutlined />}
        onAdd={addEntity}
        title="实体"
      >
        {editorEntities(spec.entities).map((entity, entityIndex) => (
          <EditorItem
            key={entity.id || `entity-${entityIndex}`}
            onRemove={() => removeEntity(entityIndex)}
          >
            <EditorField label="实体名称">
              <Input
                onChange={(event) =>
                  updateEntity(entityIndex, 'name', event.target.value)
                }
                placeholder="请输入实体名称"
                value={entity.name}
              />
            </EditorField>
            <EditorField label="实体说明">
              <TextArea
                autoSize={{ minRows: 2, maxRows: 4 }}
                onChange={(event) =>
                  updateEntity(entityIndex, 'description', event.target.value)
                }
                placeholder="请输入实体说明"
                value={entity.description}
              />
            </EditorField>
            <div className={cx('requirement-editor-entity-fields')}>
              <div className={cx('requirement-editor-entity-fields-head')}>
                <Text>需要展示的信息</Text>
                <Button
                  icon={<PlusOutlined />}
                  onClick={() => addEntityField(entityIndex)}
                  size="small"
                  type="text"
                >
                  新增信息
                </Button>
              </div>
              <div className={cx('requirement-editor-entity-table')}>
                <div
                  className={cx('requirement-editor-entity-table-row', 'is-head')}
                >
                  <Text strong>名称</Text>
                  <Text strong>说明</Text>
                  <span />
                </div>
                {entity.fields.map((field, fieldIndex) => (
                  <div
                    className={cx('requirement-editor-entity-table-row')}
                    key={`${entity.id || entityIndex}-${field.label || fieldIndex}`}
                  >
                    <Input
                      onChange={(event) =>
                        updateEntityField(
                          entityIndex,
                          fieldIndex,
                          'label',
                          event.target.value
                        )
                      }
                      placeholder="例如 书名"
                      value={field.label}
                    />
                    <Input
                      onChange={(event) =>
                        updateEntityField(
                          entityIndex,
                          fieldIndex,
                          'description',
                          event.target.value
                        )
                      }
                      placeholder="说明需要展示的实体信息"
                      value={field.description}
                    />
                    <Button
                      aria-label="删除该信息项"
                      icon={<DeleteOutlined />}
                      onClick={() => removeEntityField(entityIndex, fieldIndex)}
                      size="small"
                      type="text"
                    />
                  </div>
                ))}
              </div>
              {!entity.fields.length ? (
                <Text type="secondary">暂无展示信息，点击“新增信息”添加</Text>
              ) : null}
            </div>
          </EditorItem>
        ))}
      </EditorSection>
    </div>
  )
}
