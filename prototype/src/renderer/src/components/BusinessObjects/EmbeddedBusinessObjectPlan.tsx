import { Button, Empty } from 'antd'
import { ArrowRightOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import { cx } from '../../utils'
import ImplementationPanel from './ImplementationPanel'
import { implementationSummary, type BusinessObject, type BusinessOperation } from './model'

type Props = {
  object: BusinessObject
  operation?: BusinessOperation
  editable: boolean
  canConfigure: boolean
  onChange: (operation: BusinessOperation) => void
  onSelectOperation: (operationId: string) => void
  onEdit: (kind: 'field' | 'operation' | 'binding', index?: number | null) => void
}

/** 在技术规划方案子 Tab 中以紧凑双栏展示业务字段、操作和当前操作的数据实现。 */
export default function EmbeddedBusinessObjectPlan({
  object,
  operation,
  editable,
  canConfigure,
  onChange,
  onSelectOperation,
  onEdit
}: Props): JSX.Element {
  return (
    <div className={cx('bo-embedded-plan')}>
      <div className={cx('bo-embedded-columns')}>
        <section className={cx('bo-embedded-section')}>
          <div className={cx('bo-section-heading')}>
            <div>
              <h3>业务字段</h3>
              <p className={cx('bo-muted')}>来自需求规格说明书的业务信息</p>
            </div>
            {editable && (
              <Button
                size="small"
                type="text"
                icon={<PlusOutlined />}
                onClick={() => onEdit('field')}
              >
                添加字段
              </Button>
            )}
          </div>
          {object.fields.length ? (
            <div className={cx('bo-embedded-field-list')}>
              {object.fields.map((field, index) => (
                <div className={cx('bo-embedded-field')} key={field.name}>
                  <div>
                    <strong>{field.name}</strong>
                    <span>{field.type}</span>
                  </div>
                  <small>{field.required ? '必填' : '选填'} · 业务含义由需求阶段定义</small>
                  {editable && (
                    <Button
                      size="small"
                      type="text"
                      icon={<EditOutlined />}
                      onClick={() => onEdit('field', index)}
                      aria-label={'编辑字段 ' + field.name}
                    />
                  )}
                </div>
              ))}
            </div>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="需求中尚未定义字段" />
          )}
        </section>
        <section className={cx('bo-embedded-section')}>
          <div className={cx('bo-section-heading')}>
            <div>
              <h3>操作函数</h3>
              <p className={cx('bo-muted')}>页面通过“实体.操作()”使用</p>
            </div>
            {editable && (
              <Button
                size="small"
                type="text"
                icon={<PlusOutlined />}
                onClick={() => onEdit('operation')}
              >
                添加操作
              </Button>
            )}
          </div>
          {object.operations.length ? (
            <div className={cx('bo-embedded-operation-list')}>
              {object.operations.map((item) => (
                <button
                  className={cx('bo-embedded-operation', item.id === operation?.id && 'selected')}
                  key={item.id}
                  onClick={() => onSelectOperation(item.id)}
                  type="button"
                >
                  <div>
                    <strong>{item.name}()</strong>
                    <span
                      className={
                        item.implementation.intentSources.length ? 'bo-success' : 'bo-pending'
                      }
                    >
                      {item.implementation.intentSources.length ? '意向已记录' : '待记录意向'}
                    </span>
                  </div>
                  <small>{item.description}</small>
                  <em>{implementationSummary(item.implementation) || '尚未选择数据来源'}</em>
                </button>
              ))}
            </div>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="需求中尚未定义操作函数" />
          )}
        </section>
      </div>
      {operation ? (
        <section className={cx('bo-embedded-operation-detail')}>
          <div className={cx('bo-section-heading')}>
            <div>
              <h3>{operation.name}()</h3>
              <p className={cx('bo-muted')}>{operation.description}</p>
            </div>
            {editable && (
              <Button
                size="small"
                icon={<EditOutlined />}
                onClick={() => onEdit('operation', object.operations.indexOf(operation))}
              >
                编辑操作
              </Button>
            )}
          </div>
          <div className={cx('bo-contract')}>
            <div>
              <small>操作输入</small>
              <p>{operation.inputs.join('、') || '无需输入'}</p>
            </div>
            <div>
              <small>返回结果</small>
              <p>{operation.outputs.join('、') || '按业务结果返回'}</p>
            </div>
          </div>
          <ImplementationPanel
            mode="intent"
            operation={operation}
            editable={canConfigure}
            onChange={onChange}
          />
          <div className={cx('bo-embedded-page-bindings')}>
            <div className={cx('bo-section-heading')}>
              <h3>页面使用</h3>
              {canConfigure && (
                <Button size="small" type="text" onClick={() => onEdit('binding')}>
                  关联页面
                </Button>
              )}
            </div>
            {operation.pages.length ? (
              operation.pages.map((page) => (
                <div className={cx('bo-page-flow')} key={page}>
                  <span>{page}</span>
                  <ArrowRightOutlined />
                  <strong>
                    {object.name}.{operation.name}()
                  </strong>
                </div>
              ))
            ) : (
              <p className={cx('bo-muted')}>暂无页面使用此操作。</p>
            )}
          </div>
        </section>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择一个操作查看数据实现" />
      )}
    </div>
  )
}
