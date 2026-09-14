import { Empty, Tag, Typography } from 'antd'
import { cx } from '../../../../utils'

type RequirementPage = {
  pageId?: string
  name?: string
  path?: string
}

type RequirementObject = {
  id: string
  name: string
  description: string
  fields?: Array<{ name: string; label?: string; description?: string }>
  business_operations?: unknown[]
  /** 需求阶段记录的实体与页面关系：页面通过“对象.操作()”使用业务能力。 */
  used_by_pages?: string[]
}

/** 将需求中的业务操作文本拆成用户可读的函数名、业务目的与结果。 */
function operationParts(value: unknown): { name: string; description: string } {
  if (value && typeof value === 'object') {
    const item = value as { name?: unknown; description?: unknown; expected_result?: unknown }
    return {
      name: String(item.name || '未命名操作'),
      description: [
        item.description,
        item.expected_result ? '预期结果：' + String(item.expected_result) : ''
      ]
        .filter(Boolean)
        .join('；')
    }
  }
  const [name, description = ''] = String(value || '').split(/[：:]/, 2)
  return { name: name.trim(), description: description.trim() }
}

/** 统一以实体附属函数的形式显示操作名称。 */
function operationName(value: string): string {
  const name = value.trim()
  return name.endsWith('()') ? name : name + '()'
}

/** 以需求语言展示实体，结构类型、输入输出和数据来源留给计划阶段。 */
export default function RequirementBusinessObjects({
  entities,
  pages
}: {
  entities: RequirementObject[]
  pages: RequirementPage[]
}): JSX.Element {
  /** 把页面对象收窄为“ID → 名称”索引，用于显示对象被哪些页面使用。 */
  const pageNameById = new Map(pages.map((page) => [String(page.pageId || ''), page.name || '']))
  return (
    <div className={cx('planning-review-document')}>
      <Typography.Paragraph type="secondary">
        实体是需求的一部分：明确它的用途、需要记录的信息、需要支持的操作，以及哪些页面会使用它。
      </Typography.Paragraph>
      <div className={cx('planning-review-collection')}>
        {entities.map((entity) => {
          const usedPages = (entity.used_by_pages || [])
            .map((pageId) => pageNameById.get(pageId) || pageId)
            .filter(Boolean)
          return (
            <article key={entity.id}>
              <Typography.Title level={4}>{entity.name}</Typography.Title>
              <Typography.Title level={5}>这是什么</Typography.Title>
              <Typography.Paragraph>{entity.description}</Typography.Paragraph>
              <Typography.Title level={5}>业务字段（需要记录的信息）</Typography.Title>
              <ul>
                {(entity.fields || []).map((field, index) => (
                  <li key={index}>
                    <Typography.Text strong>{field.label || field.name}</Typography.Text>
                    {field.description ? `：${field.description}` : ''}
                  </li>
                ))}
              </ul>
              <Typography.Title level={5}>需要支持哪些业务操作</Typography.Title>
              {entity.business_operations?.filter((item) => String(item).trim()).length ? (
                <ul className={cx('requirement-business-operation-list')}>
                  {entity.business_operations
                    .filter((item) => String(item).trim())
                    .map((item, index) => {
                      const parsed = operationParts(item)
                      return (
                        <li key={index}>
                          <Typography.Text strong>{operationName(parsed.name)}</Typography.Text>
                          {parsed.description && (
                            <Typography.Paragraph>{parsed.description}</Typography.Paragraph>
                          )}
                        </li>
                      )
                    })}
                </ul>
              ) : (
                <Typography.Paragraph type="secondary">
                  待补充：描述用户需要完成的业务，以及操作后的预期结果。
                </Typography.Paragraph>
              )}
              <Typography.Title level={5}>哪些页面会使用</Typography.Title>
              {usedPages.length ? (
                <div className={cx('requirement-business-page-tags')}>
                  {usedPages.map((name) => (
                    <Tag key={name}>{name}</Tag>
                  ))}
                  <Typography.Paragraph type="secondary">
                    页面通过「{entity.name}.操作()」使用上述业务能力；具体的数据来源在计划与开发阶段确定。
                  </Typography.Paragraph>
                </div>
              ) : (
                <Typography.Paragraph type="secondary">
                  待补充：可在编辑态关联使用该实体的页面，帮助下游规划页面数据来源。
                </Typography.Paragraph>
              )}
            </article>
          )
        })}
        {!entities.length && (
          <Empty
            description="暂无实体，请在需求说明书中补充"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        )}
      </div>
      <Typography.Paragraph type="secondary">
        组合查询通常是已有实体的一项业务操作。只有具备独立业务身份与生命周期时，才定义新的实体。
      </Typography.Paragraph>
    </div>
  )
}
