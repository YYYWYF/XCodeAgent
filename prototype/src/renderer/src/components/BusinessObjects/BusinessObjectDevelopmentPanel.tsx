import { useCallback, useEffect, useRef, useState } from 'react'
import { DeleteOutlined, DatabaseOutlined, EditOutlined, LinkOutlined } from '@ant-design/icons'
import { Button, Empty, Input, Modal, Select, Tag } from 'antd'
import { cx } from '../../utils'
import { bindingKey, useDataSourceIndex } from '../DataSources/catalog'
import ImplementationPanel from './ImplementationPanel'
import {
  emptyImplementation,
  matchObjectFields,
  type BusinessObject,
  type BusinessOperation,
  type SourceCategory
} from './model'
import { useBusinessObjects } from './store'
import './BusinessObjects.less'
import './BusinessObjectDevelopmentPanel.less'

type OperationDraft = {
  name: string
  description: string
  inputs: string[]
  outputs: string[]
}

/** 数据来源类别的展示文案。 */
const SOURCE_CATEGORY_LABEL: Record<SourceCategory, string> = {
  database: '数据库',
  external_api: '外部 API',
  mixed: '混合'
}

/**
 * 实体开发工作台：目录树选中对象时呈现字段维护，选中方法时呈现该方法的
 * 出入参契约与数据实现定义（字段绑定 / 外部 API 适配 / 本地实现）。
 */
export default function BusinessObjectDevelopmentPanel({
  objectId,
  methodId,
  addMethodTick = 0,
  onMethodSelect,
  requirementSpec,
  versionKey
}: {
  objectId: string
  /** 当前在右侧打开定义的方法；为空时呈现对象的字段维护。 */
  methodId?: string
  /** 目录树「新增方法」的自增信号：计数变化即打开一次新增方法弹窗。 */
  addMethodTick?: number
  onMethodSelect?: (methodId: string) => void
  requirementSpec: Record<string, unknown>
  /** 实体绑定缓存所属版本；与产物目录、开发剧本共用同一把版本键。 */
  versionKey?: string
}): JSX.Element {
  const [objects, saveObjects] = useBusinessObjects(requirementSpec, versionKey)
  const catalog = useDataSourceIndex()
  const object = objects.find((item) => item.id === objectId) || objects[0]
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingOperationId, setEditingOperationId] = useState('')
  const lastHandledAddTick = useRef(0)
  const [draft, setDraft] = useState<OperationDraft>({
    name: '',
    description: '',
    inputs: [],
    outputs: []
  })
  const method = object?.operations.find((item) => item.id === methodId)
  const boundTarget = object?.tableBinding
    ? catalog.targetByKey.get(
        bindingKey(object.tableBinding.sourceId, object.tableBinding.targetName)
      )
    : undefined
  const fieldMatches =
    object && boundTarget ? matchObjectFields(object.fields, boundTarget.fields) : []

  // 新增信号以外统一走这个入口打开方法编辑弹窗；useCallback 保证新增信号 effect 的依赖稳定。
  const openOperationEditor = useCallback(
    (target?: BusinessOperation): void => {
      setEditingOperationId(target?.id || '')
      setDraft({
        name: target?.name || '',
        description: target?.description || '',
        inputs: target?.inputs || [],
        outputs: target?.outputs || object?.fields.map((field) => field.name) || []
      })
      setEditorOpen(true)
    },
    [object]
  )

  // 目录树的「新增方法」只发一次信号；用 ref 防止对象切换等重渲染重复弹窗。
  useEffect(() => {
    if (addMethodTick && addMethodTick !== lastHandledAddTick.current) {
      lastHandledAddTick.current = addMethodTick
      openOperationEditor()
    }
  }, [addMethodTick, object, openOperationEditor])

  /** 保存一个方法的数据实现，不影响同对象内其它方法。 */
  const updateOperation = (next: BusinessOperation): void => {
    if (!object) return
    saveObjects(
      objects.map((item) =>
        item.id === object.id
          ? {
              ...item,
              operations: item.operations.map((candidate) =>
                candidate.id === next.id ? next : candidate
              )
            }
          : item
      )
    )
  }

  /** 删除一个需求自定义方法；内置方法由平台模板提供，不提供删除入口。 */
  const deleteOperation = (target: BusinessOperation): void => {
    if (!object) return
    Modal.confirm({
      cancelText: '取消',
      content: `删除后页面将无法再调用 ${target.name}()，确定删除？`,
      okButtonProps: { danger: true },
      okText: '删除',
      onOk: () => {
        saveObjects(
          objects.map((item) =>
            item.id === object.id
              ? { ...item, operations: item.operations.filter((op) => op.id !== target.id) }
              : item
          )
        )
        onMethodSelect?.('')
      },
      title: `删除方法 ${target.name}()？`
    })
  }

  /** 将方法草稿写回实体；任何结构调整都会使原数据绑定重新待确认。 */
  const saveOperationDraft = (): void => {
    if (!object || !draft.name.trim()) return
    const existing = object.operations.find((item) => item.id === editingOperationId)
    const next: BusinessOperation = existing
      ? {
          ...existing,
          name: existing.operationType === 'builtin' ? existing.name : draft.name.trim(),
          description: draft.description.trim(),
          inputs: draft.inputs,
          outputs: draft.outputs,
          implementation: { ...existing.implementation, confirmed: false }
        }
      : {
          id: `custom-${Date.now()}`,
          name: draft.name.trim(),
          description: draft.description.trim(),
          inputs: draft.inputs,
          outputs: draft.outputs,
          pages: [],
          operationType: 'custom',
          implementation: emptyImplementation()
        }
    const nextObject: BusinessObject = {
      ...object,
      operations: existing
        ? object.operations.map((item) => (item.id === existing.id ? next : item))
        : [...object.operations, next]
    }
    saveObjects(objects.map((item) => (item.id === object.id ? nextObject : item)))
    onMethodSelect?.(next.id)
    setEditorOpen(false)
  }

  if (!object) {
    return <Empty description="未找到实体" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }

  return (
    <section className={cx('business-object-development')}>
      {/* 头部只保留对象身份：眉题、长描述与绑定进度属于后置状态信息，先聚焦静态数据。 */}
      <header className={cx('bod-header')}>
        <div className={cx('bod-title')}>
          <span>
            <DatabaseOutlined />
          </span>
          <h2>{object.name}</h2>
        </div>
      </header>

      {method ? (
        /* 方法定义：出入参契约 + 数据实现与字段绑定，对应目录树中的方法节点。 */
        <div className={cx('bod-content')}>
          <header className={cx('bod-operation-header')}>
            <div>
              <Tag color={method.operationType === 'builtin' ? 'purple' : 'default'}>
                {method.operationType === 'builtin' ? '平台内置' : '需求自定义'}
              </Tag>
              <h3>{method.name}()</h3>
              <p>{method.description}</p>
            </div>
            <div className={cx('bod-operation-actions')}>
              <Button
                icon={<EditOutlined />}
                onClick={() => openOperationEditor(method)}
                size="small"
              >
                调整方法
              </Button>
              {method.operationType === 'custom' ? (
                <Button
                  danger
                  icon={<DeleteOutlined />}
                  onClick={() => deleteOperation(method)}
                  size="small"
                  type="text"
                >
                  删除
                </Button>
              ) : null}
            </div>
          </header>
          {/* 出入参契约结构化展示；映射来源的逐字段维护在下方绑定工作台完成。 */}
          <div className={cx('bod-contract')}>
            <div>
              <small>操作输入</small>
              <div className={cx('bod-chips')}>
                {(method.inputs.length ? method.inputs : ['无需输入']).map((item) => (
                  <span key={item}>{item}</span>
                ))}
              </div>
            </div>
            <div>
              <small>返回字段</small>
              <div className={cx('bod-chips')}>
                {(method.outputs.length ? method.outputs : ['无返回字段']).map((item) => (
                  <span key={item}>{item}</span>
                ))}
              </div>
            </div>
            <div>
              <small>页面使用</small>
              <div className={cx('bod-chips')}>
                {(method.pages.length ? method.pages : ['暂未关联页面']).map((item) => (
                  <span key={item}>{item}</span>
                ))}
              </div>
            </div>
          </div>
          <div className={cx('bod-binding-stage')}>
            <div className={cx('bod-stage-heading')}>
              <span>01</span>
              <div>
                <strong>数据实现与字段绑定</strong>
                <small>选择连接后才读取具体表或 API，由 AI 匹配返回字段</small>
              </div>
            </div>
            <ImplementationPanel
              editable
              mode="bind"
              onChange={updateOperation}
              operation={method}
            />
          </div>
        </div>
      ) : (
        /* 字段维护：只读呈现实体开发工作流确认后的数据来源与字段对应，不在面板内编辑。 */
        <div className={cx('bod-content')}>
          <section className={cx('bod-source-card')}>
            <header className={cx('bod-source-head')}>
              <div>
                <DatabaseOutlined />
                <strong>数据来源</strong>
              </div>
              <Tag color="purple">{SOURCE_CATEGORY_LABEL[object.sourceCategory]}</Tag>
            </header>
            {object.sourceCategory === 'database' ? (
              object.tableBinding && boundTarget ? (
                <>
                  <p className={cx('bod-bound-line')}>
                    <LinkOutlined /> {object.tableBinding.sourceName} ·{' '}
                    {object.tableBinding.targetName}（{object.tableBinding.targetComment}）
                  </p>
                  <table className={cx('bod-field-table', 'bod-match-table')}>
                    <thead>
                      <tr>
                        <th>实体字段</th>
                        <th>表字段</th>
                        <th>说明</th>
                        <th>对应</th>
                      </tr>
                    </thead>
                    <tbody>
                      {fieldMatches.map((match) => (
                        <tr key={match.field}>
                          <td>
                            <code>{match.field}</code>
                          </td>
                          <td>{match.columnName ? <code>{match.columnName}</code> : '—'}</td>
                          <td>{match.columnComment || '—'}</td>
                          <td>
                            {match.matched ? (
                              <span className={cx('bod-match-ok')}>已对应</span>
                            ) : (
                              <span className={cx('bod-match-pending')}>待确认</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <small className={cx('bod-bind-note')}>
                    内置增删查改方法由平台按该表结构模板生成；方法页的字段映射沿用这份对应关系。
                  </small>
                </>
              ) : (
                <small className={cx('bod-bind-note')}>
                  尚未绑定数据表：来源与表在实体开发工作流中确认。
                </small>
              )
            ) : (
              <small className={cx('bod-bind-note')}>
                {object.sourceCategory === 'external_api'
                  ? '外部 API 不做实体级绑定：每个自定义方法逐个绑定固定接口，并完成出参翻译与入参适配。'
                  : '混合来源的方法走高级自定义：以 SQL 或 Java 自由编排库表与多个接口，平台不做可视化映射。'}
              </small>
            )}
            <small className={cx('bod-bind-note')}>
              绑定在实体开发工作流中确认；需要调整时请在对话中重新发起调整。
            </small>
          </section>

          <header className={cx('bod-fields-head')}>
            <div>
              <DatabaseOutlined />
              <strong>实体字段</strong>
            </div>
            <span>字段结构来自已确认的需求说明书，页面统一消费这一份稳定结构。</span>
          </header>
          <table className={cx('bod-field-table')}>
            <thead>
              <tr>
                <th>字段名</th>
                <th>类型</th>
                <th>必填</th>
              </tr>
            </thead>
            <tbody>
              {object.fields.map((field) => (
                <tr key={field.name}>
                  <td>
                    <code>{field.name}</code>
                  </td>
                  <td>{field.type}</td>
                  <td>{field.required ? '是' : '否'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Modal
        cancelText="取消"
        okButtonProps={{ disabled: !draft.name.trim() }}
        okText="保存方法"
        onCancel={() => setEditorOpen(false)}
        onOk={saveOperationDraft}
        open={editorOpen}
        title={editingOperationId ? '调整方法' : '新增自定义方法'}
      >
        <div className={cx('bod-operation-form')}>
          <label>
            方法名称
            <Input
              disabled={
                object.operations.find((item) => item.id === editingOperationId)?.operationType ===
                'builtin'
              }
              onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              value={draft.name}
            />
          </label>
          <label>
            业务说明
            <Input.TextArea
              autoSize={{ minRows: 2, maxRows: 4 }}
              onChange={(event) => setDraft({ ...draft, description: event.target.value })}
              value={draft.description}
            />
          </label>
          <label>
            操作输入
            <Select
              mode="tags"
              onChange={(value) => setDraft({ ...draft, inputs: value })}
              options={object.fields.map((field) => ({ label: field.name, value: field.name }))}
              value={draft.inputs}
            />
          </label>
          <label>
            返回字段
            <Select
              mode="tags"
              onChange={(value) => setDraft({ ...draft, outputs: value })}
              options={object.fields.map((field) => ({ label: field.name, value: field.name }))}
              value={draft.outputs}
            />
          </label>
        </div>
      </Modal>
    </section>
  )
}
