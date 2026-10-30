import {
  CheckOutlined,
  CopyOutlined,
  DatabaseOutlined,
  InfoCircleOutlined,
  ReloadOutlined
} from '@ant-design/icons'
import { Alert, Button, Spin, Tooltip, Typography } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import DatabaseOperationTag from '../../../DatabaseOperationTag'
import EntityTableConfirmation from './EntityTableConfirmation'
import EntityTableFields from './EntityTableFields'
import EntityTableStatus from './EntityTableStatus'
import useEntityTable from './useEntityTable'
import './EntityTablePanel.less'

type Props = { workspaceRoot: string; entityId: string }
const { Text } = Typography

/** 在模拟数据表页签内展示真实结构、受控建表与结果，未建表时不展示字段。 */
export default function EntityTablePanel({ workspaceRoot, entityId }: Props): ReactElement {
  const state = useEntityTable(workspaceRoot, entityId)
  const { data, columns, columnsError, loading, error, issue } = state
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState('')
  const preview = data?.preview
  const operation = data?.operation
  const operationId = operation?.operationId || data?.receipt?.operationId
  const operationState = operation?.status || data?.receipt?.status

  // 复制成功提示短暂显示，组件卸载后清理计时器。
  useEffect(() => {
    if (!copied) return undefined
    const timer = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])

  /** 使用 Electron 原生剪贴板复制实际表名，只在写入成功后提示完成。 */
  async function copyTableName(): Promise<void> {
    if (!preview) return
    try {
      const clipboard = window.devAgentStudio?.clipboard
      if (!clipboard) throw new Error('Native clipboard is unavailable')
      await clipboard.writeText(preview.tableName)
      setCopied(true)
      setCopyError('')
    } catch {
      setCopied(false)
      setCopyError('复制失败，请重试或选中表名手动复制。')
    }
  }

  return (
    <div className={cx('entity-table-panel')} aria-label="模拟数据表">
      <div className={cx('entity-table-toolbar')}>
        <div className={cx('entity-table-meta')}>
          <Text type="secondary">表名</Text>
          <span className={cx('entity-table-name')}>{preview?.tableName || '待读取'}</span>
          <Tooltip title={copied ? '已复制' : '复制表名'} getPopupContainer={(trigger) => trigger.parentElement || trigger}>
            <Button type="text" size="small" aria-label={copied ? '表名已复制' : '复制表名'}
              disabled={!preview} icon={copied ? <CheckOutlined /> : <CopyOutlined />}
              onClick={() => void copyTableName()} />
          </Tooltip>
          <span className={cx('entity-table-source')}><DatabaseOutlined />平台内置数据库</span>
        </div>
        <Button type="text" disabled={loading} icon={<ReloadOutlined />} onClick={() => void state.runAction('prepare')}>刷新设计</Button>
      </div>
      {error ? <Alert showIcon type={issue ? 'warning' : 'error'}
        message={issue?.title || '操作未完成'} description={error} /> : null}
      {copyError ? <Alert showIcon type="error" message={copyError} /> : null}
      <EntityTableStatus state={state} />
      {loading ? <div className={cx('entity-table-loading')} role="status"><Spin size="small" /><Text type="secondary">正在同步模拟数据表…</Text></div> : null}
      {!loading && columnsError ? <Alert showIcon type="error" message="数据表字段读取失败" description={columnsError} /> : null}
      {!loading && columns.length > 0 ? <EntityTableFields columns={columns} /> : null}
      {operationId ? <details className={cx('entity-table-operation')}>
        <summary>最近操作<DatabaseOperationTag operation={operation} status={operationState} /></summary>
        <p>操作 ID：<span className={cx('entity-table-name')}>{operationId}</span></p>
        {operation?.executionResult.errorSummary ? <p>{operation.executionResult.errorSummary} {operation.executionResult.errorCode}</p> : null}
        {operation?.executionResult.recoveryState === 'MANUALLY_ABANDONED' ? <p>该操作已由人工结束，请以当前结构检查结果为准。</p> : null}
        {data?.readiness?.reason ? <p>{data.readiness.reason}</p> : null}
      </details> : null}
      <p className={cx('entity-table-hint')}><InfoCircleOutlined />字段设计来自已确认的技术规划，更新后请刷新设计。</p>
      <EntityTableConfirmation state={state} />
    </div>
  )
}
