import { Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { ReactElement } from 'react'
import type { DevelopmentPlanningEntityOption } from '../../../../typings'
import { cx } from '../../../../utils'

type EntityField = NonNullable<DevelopmentPlanningEntityOption['fields']>[number]

const FIELD_TYPE_LABELS: Record<string, string> = {
  text: '文本',
  long_text: '长文本',
  number: '数字',
  decimal: '小数',
  date: '日期',
  datetime: '日期时间',
  enum: '枚举',
  boolean: '布尔'
}

const FIELD_COLUMNS: ColumnsType<EntityField> = [
  {
    title: '字段名称',
    dataIndex: 'label',
    key: 'label',
    width: '26%',
    // 中文名称与技术标识分列，未填写名称时仍显示原字段标识。
    render: (label: string | undefined, field: EntityField) => label || field.name || '-'
  },
  {
    title: '字段标识',
    dataIndex: 'name',
    key: 'name',
    width: '34%',
    // 技术标识使用纯等宽文本，避免逐个代码框抢占视觉重点。
    render: (name: string) => <span className={cx('entity-info-field-code')}>{name || '-'}</span>
  },
  {
    title: '类型',
    dataIndex: 'type',
    key: 'type',
    width: '23%',
    // 字段类型沿用已有中文映射，使用中性标签展示。
    render: (type: string) => (
      <Tag className={cx('entity-info-field-type')}>
        {FIELD_TYPE_LABELS[type] || type || '文本'}
      </Tag>
    )
  },
  {
    title: '必填',
    dataIndex: 'required',
    key: 'required',
    width: '17%',
    // 文字与圆点共同区分必填和选填，保持无颜色依赖的可读性。
    render: (required: boolean | undefined) => (
      <span className={cx('entity-info-required')} data-required={Boolean(required)}>
        {required ? '必填' : '选填'}
      </span>
    )
  }
]

/** 展示实体的字段名称、技术标识、类型与必填统计，仅消费现有规划数据。 */
export default function EntityFieldList({
  fields,
  showHeading = true
}: {
  fields: EntityField[]
  showHeading?: boolean
}): ReactElement {
  // 统计与每行使用相同的必填判断，缺省 required 仍按选填展示。
  const requiredCount = fields.filter((field) => field.required).length

  return (
    <section aria-label="实体字段清单" className={cx('entity-info-fields')}>
      <div className={cx('entity-info-section-head')}>
        {showHeading ? (
          <div className={cx('entity-info-section-heading')}>
            <Typography.Text className={cx('entity-info-section-title')} strong>
              字段清单
            </Typography.Text>
            <span className={cx('entity-info-count')}>{fields.length}</span>
          </div>
        ) : null}
        {fields.length > 0 ? (
          <span className={cx('entity-info-field-summary')}>
            {requiredCount} 项必填 · {fields.length - requiredCount} 项选填
          </span>
        ) : null}
      </div>
      {fields.length > 0 ? (
        <Table
          className={cx('entity-info-fields-table', 'entity-info-planned-fields')}
          columns={FIELD_COLUMNS}
          dataSource={fields.map((field, index) => ({ ...field, key: field.name || index }))}
          pagination={false}
          rowKey="key"
          size="small"
          tableLayout="fixed"
          scroll={{ x: 460 }}
        />
      ) : (
        <Typography.Text className={cx('entity-info-fields-empty')} type="secondary">
          项目计划尚未生成字段清单。
        </Typography.Text>
      )}
    </section>
  )
}
