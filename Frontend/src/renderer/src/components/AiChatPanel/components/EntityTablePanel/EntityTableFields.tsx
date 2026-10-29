import { KeyOutlined } from '@ant-design/icons'
import { Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import type { EntityTableColumn } from './useEntityTable'

const COLUMNS: ColumnsType<EntityTableColumn> = [
  {
    title: '字段名称', width: '24%', key: 'description',
    // 实际字段备注优先展示，主键通过图标标记，缺省说明时使用字段名。
    render: (_, column) => (
      <span className={cx('entity-table-column-label')}>
        {column.primaryKey ? <KeyOutlined aria-label="主键" /> : null}
        {column.description || column.name}
      </span>
    )
  },
  {
    title: '字段标识', dataIndex: 'name', width: '32%',
    // 技术标识沿用普通实体的分列表格和等宽文本。
    render: (name: string) => <span className={cx('entity-info-field-code')}>{name}</span>
  },
  {
    title: '数据库类型', dataIndex: 'type', width: '28%',
    // 展示元数据接口返回的实际类型，不用规划中的预期类型替代。
    render: (type: string) => <Tag className={cx('entity-info-field-type')}>{type}</Tag>
  },
  {
    title: '必填', key: 'required', width: '16%',
    // 仅以实际列的可空标记决定必填，不读取规划字段的 required。
    render: (_, column) => {
      const required = column.nullable === false
      return <span className={cx('entity-info-required')} data-required={required}>
        {required ? '必填' : '选填'}
      </span>
    }
  }
]

/** 仅在读取到已建表的真实列后展示字段列表，不展示规划占位数据。 */
export default function EntityTableFields({ columns }: { columns: EntityTableColumn[] }): ReactElement {
  const primaryKeyCount = columns.filter((column) => column.primaryKey).length
  return (
    <section className={cx('entity-table-fields')} aria-label="数据表字段">
      <div className={cx('entity-info-section-head')}>
        <div className={cx('entity-info-section-heading')}>
          <span>数据表字段</span><span className={cx('entity-info-count')}>{columns.length}</span>
        </div>
        {primaryKeyCount > 0 ? <span className={cx('entity-info-field-summary')}>
          含 {primaryKeyCount} 个主键字段
        </span> : null}
      </div>
      <Table
        className={cx('entity-info-fields-table', 'entity-table-columns')}
        columns={COLUMNS}
        dataSource={columns}
        pagination={false}
        rowKey="name"
        size="small"
        tableLayout="fixed"
        scroll={{ x: 460 }}
      />
    </section>
  )
}
