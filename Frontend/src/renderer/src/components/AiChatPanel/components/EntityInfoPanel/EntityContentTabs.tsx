import { CodeOutlined, DatabaseOutlined } from '@ant-design/icons'
import { Tabs, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { useState } from 'react'
import { cx } from '../../../../utils'
import EntityTablePanel from '../EntityTablePanel'
import EntityDdlPreview from './EntityDdlPreview'

type Props = {
  workspaceRoot?: string
  entityId: string
  fieldCount: number
  useBuiltinDatabase?: boolean
  children: ReactNode
}

/** 共用实体详情页签，模拟数据表独立懒加载并保留在途操作，DDL 离开后释放。 */
export default function EntityContentTabs({
  workspaceRoot,
  entityId,
  fieldCount,
  useBuiltinDatabase = false,
  children
}: Props): ReactElement {
  const [activeKey, setActiveKey] = useState('fields')

  return (
    <Tabs
      className={cx('entity-info-tabs')}
      activeKey={activeKey}
      onChange={setActiveKey}
      items={[
        {
          key: 'fields',
          label: (
            <span className={cx('entity-info-tab-label')}>
              字段设计<span className={cx('entity-info-count')}>{fieldCount}</span>
            </span>
          ),
          children
        },
        ...(useBuiltinDatabase ? [{
          key: 'database',
          label: (
            <span className={cx('entity-info-tab-label')}>
              <DatabaseOutlined />
              模拟数据表
            </span>
          ),
          children: workspaceRoot ? (
            <EntityTablePanel workspaceRoot={workspaceRoot} entityId={entityId} />
          ) : (
            <Typography.Text type="secondary">
              请先打开当前应用工作区，再管理模拟数据表。
            </Typography.Text>
          )
        }] : []),
        {
          key: 'ddl',
          label: (
            <span className={cx('entity-info-tab-label')}>
              <CodeOutlined />
              建表 DDL
            </span>
          ),
          children: activeKey === 'ddl' ? (
            <EntityDdlPreview workspaceRoot={workspaceRoot} entityId={entityId} />
          ) : null
        }
      ]}
    />
  )
}
