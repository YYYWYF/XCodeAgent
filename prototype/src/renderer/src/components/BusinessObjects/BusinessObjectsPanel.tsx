import { useEffect, useState } from 'react'
import { Button, Empty, Input } from 'antd'
import {
  AppstoreOutlined,
  ArrowRightOutlined,
  PlusOutlined,
  SearchOutlined
} from '@ant-design/icons'
import { useWorkbenchPhase } from '../../context'
import { cx } from '../../utils'
import OperationDetails from './OperationDetails'
import BusinessObjectEditor from './BusinessObjectEditor'
import EmbeddedBusinessObjectPlan from './EmbeddedBusinessObjectPlan'
import {
  emptyImplementation,
  makeOperation,
  type BusinessObject,
  type BusinessOperation
} from './model'
import { useBusinessObjects } from './store'
import './BusinessObjects.less'

/** 将对象设计和操作实现放在同一右侧面板中，按当前阶段逐步展开。 */
export default function BusinessObjectsPanel({
  requirementSpec,
  embedded = false,
  compact = false,
  focusObjectId
}: {
  requirementSpec: Record<string, unknown>
  embedded?: boolean
  compact?: boolean
  focusObjectId?: string
}): JSX.Element {
  const { viewingPhase } = useWorkbenchPhase()
  const editableDefinition = viewingPhase === 'planning'
  const planning = viewingPhase === 'planning'
  const implementation = viewingPhase === 'development'
  const validation =
    viewingPhase === 'testing' || viewingPhase === 'review' || viewingPhase === 'acceptance'
  // 计划阶段记录来源级意向；开发及验证阶段展示表/接口级绑定结果（验证阶段只读）。
  const implementationMode: 'intent' | 'bind' = planning ? 'intent' : 'bind'
  const [objects, saveObjects] = useBusinessObjects(requirementSpec)
  const [selectedId, setSelectedId] = useState(() => focusObjectId || objects[0]?.id || '')
  const [operationId, setOperationId] = useState('my')
  const [section, setSection] = useState<'fields' | 'operations'>('operations')
  const [search, setSearch] = useState('')
  const [editor, setEditor] = useState<'object' | 'field' | 'operation' | 'binding' | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [fieldType, setFieldType] = useState('文本')
  const [required, setRequired] = useState(true)
  const [inputs, setInputs] = useState<string[]>([])
  const [outputs, setOutputs] = useState<string[]>([])
  const [pages, setPages] = useState<string[]>([])
  const [editIndex, setEditIndex] = useState<number | null>(null)
  const [unconfirmed, setUnconfirmed] = useState<string[]>([])
  const object = objects.find((item) => item.id === selectedId) ||
    objects[0] || { id: '', name: '', description: '', fields: [], operations: [] }
  const operation =
    object.operations.find((item) => item.id === operationId) || object.operations[0]
  const pendingCount = object.operations.filter((item) => !item.implementation.confirmed).length
  const implementationKinds = new Set(
    objects.flatMap((item) => item.operations.map((operation) => operation.implementation.kind))
  )
  const designConfirmed = !unconfirmed.includes(object.id)
  const canConfigure = implementation || (planning && designConfirmed)

  /** 开发产物树切换对象时，让右侧详情定位到同一个实体。 */
  useEffect(() => {
    if (
      focusObjectId &&
      focusObjectId !== selectedId &&
      objects.some((item) => item.id === focusObjectId)
    ) {
      setSelectedId(focusObjectId)
      setOperationId(objects.find((item) => item.id === focusObjectId)?.operations[0]?.id || '')
      setSection('operations')
    }
  }, [focusObjectId, objects, selectedId])

  /** 更新当前对象的局部草稿；业务结构发生变化时重新要求设计确认。 */
  const updateObject = (next: BusinessObject, invalidate = false): void => {
    saveObjects(objects.map((item) => (item.id === next.id ? next : item)))
    if (invalidate) setUnconfirmed((current) => [...new Set([...current, next.id])])
  }
  /** 仅替换当前操作，保持同一对象下其他操作的数据来源独立。 */
  const updateOperation = (next: BusinessOperation): void => {
    updateObject({
      ...object,
      operations: object.operations.map((item) => (item.id === next.id ? next : item))
    })
  }
  /** 打开局部编辑表单，并从当前选中项初始化草稿。 */
  const openEditor = (kind: NonNullable<typeof editor>, index: number | null = null): void => {
    setEditor(kind)
    setEditIndex(index)
    setName('')
    setDescription('')
    setFieldType('文本')
    setRequired(true)
    setInputs([])
    setOutputs(object.fields.map((field) => field.name))
    setPages(operation?.pages || [])
    if (kind === 'field' && index !== null) {
      const field = object.fields[index]
      setName(field.name)
      setFieldType(field.type)
      setRequired(field.required)
    }
    if (kind === 'operation' && index !== null) {
      const item = object.operations[index]
      setName(item.name)
      setDescription(item.description)
      setInputs(item.inputs)
      setOutputs(item.outputs)
    }
  }
  const duplicate =
    editor === 'object'
      ? objects.some((item) => item.name === name.trim())
      : editor === 'field'
        ? object.fields.some((item, index) => item.name === name.trim() && index !== editIndex)
        : editor === 'operation'
          ? object.operations.some(
              (item, index) => item.name === name.trim() && index !== editIndex
            )
          : false
  /** 提交演示表单，结构变更同步操作字段名称并使受影响的数据实现待确认。 */
  const saveEditor = (): void => {
    if (editor === 'binding') updateOperation({ ...operation, pages })
    if (editor === 'object') {
      const id = `object-${Date.now()}`
      saveObjects([
        ...objects,
        {
          id,
          name: name.trim(),
          description: description.trim(),
          sourceCategory: 'database',
          fields: [],
          operations: []
        }
      ])
      setSelectedId(id)
      setSection('fields')
      setUnconfirmed((current) => [...current, id])
    }
    if (editor === 'field') {
      const field = { name: name.trim(), type: fieldType, required }
      const oldName = editIndex !== null ? object.fields[editIndex].name : ''
      updateObject(
        {
          ...object,
          fields:
            editIndex === null
              ? [...object.fields, field]
              : object.fields.map((item, index) => (index === editIndex ? field : item)),
          operations: object.operations.map((item) =>
            oldName && item.outputs.includes(oldName)
              ? {
                  ...item,
                  outputs: item.outputs.map((output) => (output === oldName ? field.name : output)),
                  implementation: { ...item.implementation, confirmed: false }
                }
              : item
          )
        },
        true
      )
    }
    if (editor === 'operation') {
      const previous = editIndex !== null ? object.operations[editIndex] : undefined
      const next = {
        ...(previous || makeOperation(`op-${Date.now()}`, name.trim(), '数据库', [], outputs)),
        name: name.trim(),
        description: description.trim(),
        inputs,
        outputs,
        implementation: previous
          ? { ...previous.implementation, confirmed: false }
          : emptyImplementation('数据库')
      }
      updateObject(
        {
          ...object,
          operations:
            editIndex === null
              ? [...object.operations, next]
              : object.operations.map((item, index) => (index === editIndex ? next : item))
        },
        true
      )
      setOperationId(next.id)
    }
    setEditor(null)
  }

  return (
    <div className={cx('bo-panel', embedded && 'bo-panel-embedded', compact && 'bo-panel-compact')}>
      {!embedded && !compact && (
        <header className="bo-header">
          <div>
            <span className="bo-eyebrow">应用数据层</span>
            <h2>
              实体 <span>{objects.length}</span>
            </h2>
          </div>
          <span className="bo-demo">来自需求说明书 · 实现为演示草稿</span>
        </header>
      )}
      {embedded && (
        <div className="bo-review-summary">
          <div>
            <strong>{objects.length}</strong>
            <span>个实体</span>
          </div>
          <div>
            <strong>{objects.reduce((total, item) => total + item.operations.length, 0)}</strong>
            <span>项实体操作</span>
          </div>
          <div>
            <strong>{implementationKinds.size}</strong>
            <span>类数据实现</span>
          </div>
          <p>审阅重点：实体是否准确、操作是否完整、每项操作的数据实现是否符合业务预期。</p>
        </div>
      )}
      {!embedded && !compact && (
        <div className="bo-stage-strip">
          <span>01 需求 · 业务描述</span>
          <ArrowRightOutlined />
          <span className={planning ? 'active' : ''}>02 计划 · 连接数据</span>
          <ArrowRightOutlined />
          <span className={implementation ? 'active' : ''}>03 开发 · 使用操作</span>
          <ArrowRightOutlined />
          <span className={validation ? 'active' : ''}>04 验证 · 检查结果</span>
        </div>
      )}
      {!embedded && !compact && (
        <p className="bo-stage-copy">
          {planning
            ? '实体来自需求规格说明书。在这里细化字段、操作输入与返回结果，并配置每个操作的数据实现。'
            : implementation
              ? '页面通过实体.操作()使用数据能力；这里查看操作实现来源和开发状态。'
              : validation
                ? '按实体的字段、操作和数据实现验证页面行为与返回结果，问题回到对应阶段修正。'
                : '查看计划阶段确定的业务能力；业务范围调整回到需求说明书，结构与实现调整回到计划阶段。'}
        </p>
      )}
      {!embedded && !compact && (
        <div className="bo-toolbar">
          <Input
            aria-label="搜索实体"
            prefix={<SearchOutlined />}
            placeholder="搜索实体"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
      )}
      <nav className="bo-object-list" aria-label="实体列表">
        {objects
          .filter((item) => item.name.includes(search))
          .map((item) => (
            <button
              key={item.id}
              className={item.id === selectedId ? 'selected' : ''}
              onClick={() => {
                setSelectedId(item.id)
                setOperationId(item.operations[0]?.id || '')
              }}
            >
              <AppstoreOutlined />
              <strong>{item.name}</strong>
              <small>
                {item.fields.length} 字段 · {item.operations.length} 操作
              </small>
            </button>
          ))}
      </nav>
      {!objects.some((item) => item.name.includes(search)) ? (
        <Empty
          description={
            objects.length
              ? '没有匹配的实体，试试其他关键词'
              : '请先在需求规格说明书中描述实体'
          }
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      ) : (
        <>
          <div className="bo-object-heading">
            <div>
              <h3>{object.name}</h3>
              <p>{object.description}</p>
            </div>
            {editableDefinition && !embedded ? (
              <Button
                size="small"
                type={designConfirmed ? 'default' : 'primary'}
                disabled={designConfirmed || !object.fields.length || !object.operations.length}
                onClick={() =>
                  setUnconfirmed((current) => current.filter((id) => id !== object.id))
                }
              >
                {designConfirmed ? '结构与操作已确认' : '确认结构与操作'}
              </Button>
            ) : !embedded ? (
              <span className={pendingCount ? 'bo-pending' : 'bo-success'}>
                {pendingCount ? `${pendingCount} 个操作待确认` : '数据实现已就绪'}
              </span>
            ) : null}
          </div>
          {embedded ? (
            <EmbeddedBusinessObjectPlan
              canConfigure={canConfigure}
              editable={false}
              object={object}
              onChange={updateOperation}
              onEdit={openEditor}
              onSelectOperation={setOperationId}
              operation={operation}
            />
          ) : (
            <>
              <div className="bo-local-tabs">
                <button
                  className={section === 'fields' ? 'active' : ''}
                  onClick={() => setSection('fields')}
                >
                  字段 <small>{object.fields.length}</small>
                </button>
                <button
                  className={section === 'operations' ? 'active' : ''}
                  onClick={() => setSection('operations')}
                >
                  操作 <small>{object.operations.length}</small>
                </button>
                {editableDefinition && (
                  <Button
                    type="text"
                    size="small"
                    icon={<PlusOutlined />}
                    onClick={() => openEditor(section === 'fields' ? 'field' : 'operation')}
                  >
                    添加{section === 'fields' ? '字段' : '操作'}
                  </Button>
                )}
              </div>
              {section === 'fields' ? (
                <div className="bo-fields">
                  <div className="bo-field-row bo-muted">
                    <span>业务字段</span>
                    <span>类型</span>
                    <span>必填</span>
                    <span />
                  </div>
                  {object.fields.map((field, index) => (
                    <div className="bo-field-row" key={field.name}>
                      <strong>{field.name}</strong>
                      <span>{field.type}</span>
                      <span>{field.required ? '是' : '否'}</span>
                      {editableDefinition && (
                        <Button size="small" type="text" onClick={() => openEditor('field', index)}>
                          编辑
                        </Button>
                      )}
                    </div>
                  ))}
                  {!object.fields.length && (
                    <Empty description="添加第一个业务字段" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                  )}
                  <p className="bo-muted">
                    字段描述实体本身。联查产生的附加字段保留在操作返回结果中。
                  </p>
                </div>
              ) : (
                <OperationDetails
                  {...{
                    object,
                    operation,
                    editableDefinition,
                    planning,
                    designConfirmed,
                    canConfigure,
                    mode: implementationMode,
                    setOperationId,
                    updateOperation,
                    openEditor
                  }}
                />
              )}
            </>
          )}
        </>
      )}
      {!embedded && (
        <BusinessObjectEditor
          {...{
            editor,
            editIndex,
            object,
            operation,
            name,
            description,
            fieldType,
            required,
            duplicate,
            inputs,
            outputs,
            pages,
            setEditor,
            setName,
            setDescription,
            setFieldType,
            setRequired,
            setInputs,
            setOutputs,
            setPages,
            saveEditor
          }}
        />
      )}
    </div>
  )
}
