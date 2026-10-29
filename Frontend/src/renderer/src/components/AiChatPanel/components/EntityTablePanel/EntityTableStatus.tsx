import {
  CheckOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  LoadingOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RetweetOutlined,
  TableOutlined
} from '@ant-design/icons'
import { Button, Table } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import type useEntityTable from './useEntityTable'

type State = ReturnType<typeof useEntityTable>
type Props = { state: State }
const TITLES = {
  SYNCED: '结构一致', NOT_CREATED: '尚未建表',
  STRUCTURE_MISMATCH: '发现结构差异', TABLE_CONFLICT: '表名冲突'
}
const DESCRIPTIONS = {
  SYNCED: '数据表结构与当前字段设计一致', NOT_CREATED: '按当前字段设计创建数据表',
  STRUCTURE_MISMATCH: '数据表与当前设计不一致，请查看差异', TABLE_CONFLICT: '当前表名存在冲突，请核对后刷新结果'
}

/** 将已确认结构、运行中及未知结果投影到同一状态栏，明确区分受理与成功。 */
export default function EntityTableStatus({ state }: Props): ReactElement {
  const { data, busyAction, loading, error, issue, mustRefresh, autoRefreshScheduled } = state
  const [showDifferences, setShowDifferences] = useState(false)
  const preview = data?.preview
  const table = data?.readiness?.tables.find((item) => item.tableName === preview?.tableName)
  const running = [data?.operation?.status, data?.receipt?.status, data?.readiness?.operationStatus].includes('RUNNING')
  const knownTable = table && ['SYNCED', 'STRUCTURE_MISMATCH'].includes(table.status)
  const canRebuild = Boolean(knownTable || issue?.action === 'rebuild')
  let title = table ? TITLES[table.status] : '当前结构未检查'
  let description = table ? DESCRIPTIONS[table.status] : '请刷新结果，确认当前数据表状态'
  let tone = table?.status === 'SYNCED' ? 'success' : table?.status === 'NOT_CREATED' ? 'neutral' : 'warning'
  if (loading) {
    title = busyAction === 'execute' ? '正在创建数据表' : busyAction === 'rebuild' ? '正在重建数据表' : '正在读取数据表状态'
    description = busyAction === 'execute' || busyAction === 'rebuild' ? '正在提交操作，请等待受理结果' : '正在读取当前实体的结构检查结果'
    tone = 'busy'
  } else if (running || mustRefresh) {
    title = running ? '数据表操作执行中' : '执行结果待确认'
    description = autoRefreshScheduled ? '已受理，将于 10 秒后自动刷新一次' : '请刷新原操作结果，确认完成后再进行操作'
    tone = 'warning'
  } else if (issue?.action === 'rebuild') {
    title = '发现结构差异'
    description = issue.message
  } else if (error) {
    title = '操作未完成'
    description = '请查看错误提示并刷新结果'
    tone = 'danger'
  }
  return (
    <section className={cx('entity-table-status')} data-tone={tone} aria-label="建表状态与结构检查">
      <div className={cx('entity-table-status-row')}>
        <div className={cx('entity-table-status-copy')} role="status" aria-live="polite">
          <span className={cx('entity-table-status-icon')}>
            {loading ? <LoadingOutlined /> : running || mustRefresh ? <ClockCircleOutlined /> : tone === 'success' ? <CheckOutlined /> : tone === 'neutral' ? <TableOutlined /> : <ExclamationCircleOutlined />}
          </span>
          <div><strong>{title}</strong><span>{description}</span></div>
        </div>
        <div className={cx('entity-table-actions')}>
          {table?.differences.length ? <Button type="text" disabled={loading} onClick={() => setShowDifferences((value) => !value)}>
            {showDifferences ? '收起差异' : '查看差异'}
          </Button> : null}
          <Button disabled={loading} icon={<ReloadOutlined />} onClick={() => void state.runAction('refresh')}>刷新结果</Button>
          {canRebuild ? <Button disabled={loading || mustRefresh} icon={<RetweetOutlined />} onClick={() => state.beginConfirmation('rebuild')}>重建表</Button> : null}
          {!knownTable && !canRebuild && table?.status !== 'TABLE_CONFLICT' ? <Button
            type="primary" icon={<PlayCircleOutlined />}
            disabled={loading || mustRefresh || !preview || table?.status !== 'NOT_CREATED'}
            onClick={() => state.beginConfirmation('execute')}
          >创建表</Button> : null}
        </div>
      </div>
      {showDifferences && table?.differences.length ? <div className={cx('entity-table-differences')}>
        <Table size="small" pagination={false} rowKey={(_, index) => String(index)}
          scroll={{ x: 400 }} dataSource={table.differences} columns={[
            { title: '字段 / 约束', dataIndex: 'target' },
            { title: '当前设计', dataIndex: 'expected', render: (value: string | undefined) => value || '-' },
            { title: '实际结构', dataIndex: 'actual', render: (value: string | undefined) => value || '-' }
          ]} />
      </div> : null}
    </section>
  )
}
