import { Button, Empty } from 'antd'
import { ArrowRightOutlined } from '@ant-design/icons'
import ImplementationPanel from './ImplementationPanel'
import { implementationSummary, type BusinessObject, type BusinessOperation } from './model'

type Props = {
  object: BusinessObject
  operation?: BusinessOperation
  editableDefinition: boolean
  planning: boolean
  designConfirmed: boolean
  canConfigure: boolean
  /** intent=计划阶段意向；bind=开发/验证阶段绑定结果。 */
  mode: 'intent' | 'bind'
  setOperationId: (id: string) => void
  updateOperation: (operation: BusinessOperation) => void
  openEditor: (kind: 'operation' | 'binding', index?: number | null) => void
}

/** 渲染对象内的操作目录、输入输出、数据绑定和页面使用关系。 */
export default function OperationDetails({
  object,
  operation,
  editableDefinition,
  planning,
  designConfirmed,
  canConfigure,
  mode,
  setOperationId,
  updateOperation,
  openEditor
}: Props): JSX.Element {
  return (
    <div className="bo-operation-layout">
      <aside className="bo-operations" aria-label="实体操作">
        {(['builtin', 'custom'] as const).map((operationType) => (
          <div className="bo-operation-group" key={operationType}>
            <p>{operationType === 'builtin' ? '平台内置' : '需求自定义'}</p>
            {object.operations
              .filter((item) => item.operationType === operationType)
              .map((item) => (
                <button
                  key={item.id}
                  className={operation?.id === item.id ? 'selected' : ''}
                  onClick={() => setOperationId(item.id)}
                >
                  <strong>
                    {item.name}
                    <span> ( )</span>
                  </strong>
                  <small>{implementationSummary(item.implementation) || '未绑定'}</small>
                  <span className={item.implementation.confirmed ? 'bo-success' : 'bo-pending'}>
                    {item.implementation.confirmed ? '✓ 已绑定' : '○ 待处理'}
                  </span>
                </button>
              ))}
          </div>
        ))}
      </aside>
      {operation ? (
        <div className="bo-operation-detail">
          <div className="bo-section-heading">
            <h3>
              {operation.name}
              <span className="bo-muted"> ( )</span>
            </h3>
            {editableDefinition && (
              <Button
                size="small"
                onClick={() => openEditor('operation', object.operations.indexOf(operation))}
              >
                编辑操作
              </Button>
            )}
          </div>
          <p className="bo-muted">{operation.description}</p>
          <div className="bo-contract">
            <div>
              <small>操作输入</small>
              <p>{operation.inputs.join('、') || '无需输入'}</p>
            </div>
            <div>
              <small>返回结果</small>
              <p>{operation.outputs.join('、')}</p>
            </div>
          </div>
          {
            <>
              {!designConfirmed && (
                <p className="bo-pending">结构或操作有修改，请先确认当前计划草稿再配置实现。</p>
              )}
              <ImplementationPanel
                key={`${object.id}-${operation.id}`}
                mode={mode}
                operation={operation}
                editable={canConfigure}
                onChange={updateOperation}
              />
            </>
          }
          <section className="bo-page-bindings">
            <div className="bo-section-heading">
              <h3>页面</h3>
              {canConfigure && (
                <Button size="small" type="text" onClick={() => openEditor('binding')}>
                  关联页面
                </Button>
              )}
            </div>
            {operation.pages.length ? (
              operation.pages.map((page) => (
                <div className="bo-page-flow" key={page}>
                  <span>{page}</span>
                  <ArrowRightOutlined />
                  <strong>
                    {object.name}.{operation.name}()
                  </strong>
                </div>
              ))
            ) : (
              <p className="bo-muted">
                暂无页面使用此操作{planning && '，可关联需要该业务能力的页面'}。
              </p>
            )}
          </section>
        </div>
      ) : (
        <Empty description="为实体添加第一个操作" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
    </div>
  )
}
