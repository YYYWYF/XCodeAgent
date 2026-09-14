import { ApiOutlined, AppstoreOutlined, DatabaseOutlined, FunctionOutlined } from '@ant-design/icons'
import { Empty, Tag } from 'antd'
import { cx } from '../../utils'
import { useBusinessObjects } from './store'
import './TechnicalBusinessObjectsReview.less'

/** 技术规划方案中的实体审阅：逐对象展示字段、平台内置操作与需求自定义操作。 */
export default function TechnicalBusinessObjectsReview({
  requirementSpec
}: {
  requirementSpec: Record<string, unknown>
}): JSX.Element {
  const [objects] = useBusinessObjects(requirementSpec)
  if (!objects.length) {
    return <Empty description="需求说明书中尚未定义实体" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }

  return (
    <div className={cx('technical-business-objects')}>
      <div className={cx('technical-business-objects-intro')}>
        <AppstoreOutlined />
        <div>
          <strong>实体是页面唯一消费的数据结构</strong>
          <span>先确认实体字段与可用操作；开发实体时，再把字段映射到数据库或外部服务。</span>
        </div>
      </div>
      {objects.map((object, objectIndex) => {
        const builtinOperations = object.operations.filter(
          (operation) => operation.operationType === 'builtin'
        )
        const customOperations = object.operations.filter(
          (operation) => operation.operationType === 'custom'
        )
        return (
          <article className={cx('technical-business-object')} key={object.id}>
            <header>
              <span>{String(objectIndex + 1).padStart(2, '0')}</span>
              <div>
                <h3>{object.name}</h3>
                <p>{object.description}</p>
              </div>
              <Tag>{object.fields.length} 个字段</Tag>
              <Tag>{object.operations.length} 个操作</Tag>
            </header>
            <div className={cx('technical-business-object-body')}>
              <section>
                <div className={cx('technical-business-object-section-title')}>
                  <DatabaseOutlined />
                  <div><strong>业务字段</strong><small>后续映射到来源字段</small></div>
                </div>
                <div className={cx('technical-business-fields')}>
                  {object.fields.map((field) => (
                    <div key={field.name}>
                      <strong>{field.name}</strong>
                      <span>{field.type}</span>
                      <small>{field.required ? '必填' : '选填'}</small>
                    </div>
                  ))}
                </div>
              </section>
              <section>
                <div className={cx('technical-business-object-section-title')}>
                  <FunctionOutlined />
                  <div><strong>实体操作</strong><small>页面直接调用</small></div>
                </div>
                <div className={cx('technical-business-operation-group')}>
                  <h4><Tag color="purple">平台内置</Tag>通用数据操作</h4>
                  {builtinOperations.map((operation) => (
                    <div className={cx('technical-business-operation')} key={operation.id}>
                      <strong>{operation.name}()</strong>
                      <span>{operation.description}</span>
                      <small>{operation.implementation.intentSources.join(' + ') || '开发时选择数据来源'}</small>
                    </div>
                  ))}
                </div>
                <div className={cx('technical-business-operation-group')}>
                  <h4><Tag>需求自定义</Tag>围绕业务流程生成</h4>
                  {customOperations.map((operation) => (
                    <div className={cx('technical-business-operation')} key={operation.id}>
                      <strong>{operation.name}()</strong>
                      <span>{operation.description}</span>
                      <small>
                        {operation.pages.length ? `页面：${operation.pages.join('、')}` : '由业务流程或其它操作调用'}
                        {operation.implementation.kind === '外部服务' ? <ApiOutlined /> : null}
                      </small>
                    </div>
                  ))}
                </div>
              </section>
            </div>
            <footer>
              开发「{object.name}」时逐个处理操作：选择数据来源 → AI 匹配实体字段 → 确认异常字段与数据流程。
            </footer>
          </article>
        )
      })}
    </div>
  )
}
