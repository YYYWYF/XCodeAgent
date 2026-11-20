import { CodeOutlined, DatabaseOutlined } from '@ant-design/icons'
import { Tabs, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { useEffect, useState } from 'react'
import { cx } from '../../../../utils'
import EntityTablePanel from '../EntityTablePanel'
import EntityDdlPreview from './EntityDdlPreview'

type Props = {
  workspaceRoot?: string
  entityId: string
  fieldCount: number
  useBuiltinDatabase?: boolean
  initialTab?: 'fields' | 'database'
  children: ReactNode
}

/** 共用实体详情页签，模拟数据表保留在途操作，DDL 按工作区和实体隔离审阅状态。 */
export default function EntityContentTabs({
  workspaceRoot,
  entityId,
  fieldCount,
  useBuiltinDatabase = false,
  initialTab = 'fields',
  children
}: Props): ReactElement {
  const [activeKey, setActiveKey] = useState(initialTab === 'database' && useBuiltinDatabase ? 'database' : 'fields')
  const [savedDdl, setSavedDdl] = useState<{ scope: string; draftId: string }>()
  const scope = JSON.stringify([workspaceRoot, entityId])
  // 导航意图只定位页签；实际创建仍由原面板与确认弹窗控制。
  useEffect(() => { setActiveKey(initialTab === 'database' && useBuiltinDatabase ? 'database' : 'fields') }, [initialTab, useBuiltinDatabase, entityId])

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
            <EntityTablePanel
              key={scope}
              workspaceRoot={workspaceRoot}
              entityId={entityId}
              active={activeKey === 'database'}
              confirmedDraftId={savedDdl?.scope === scope ? savedDdl.draftId : undefined}
              onReviewDdl={() => setActiveKey('ddl')}
            />
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
            <EntityDdlPreview
              key={scope}
              workspaceRoot={workspaceRoot}
              entityId={entityId}
              onConfirmed={(draftId) => setSavedDdl({ scope, draftId })}
            />
          ) : null
        }
      ]}
    />
  )
}
