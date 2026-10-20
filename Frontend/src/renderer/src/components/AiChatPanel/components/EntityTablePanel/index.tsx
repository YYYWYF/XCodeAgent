import { DatabaseOutlined, PlayCircleOutlined, ReloadOutlined, RetweetOutlined } from '@ant-design/icons'
import { Alert, Button, Checkbox, Space, Spin, Table, Tag, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { EntityTableActionError, requestEntityTable } from '../../../../service/entityTables'
import type { EntityTableIssue, EntityTablePayload } from '../../../../service/entityTables'
import { notifyDataSourcesChanged } from '../../../../service/dataSourceEvents'
import { cx } from '../../../../utils'
import DatabaseOperationTag from '../../../DatabaseOperationTag'
import './EntityTablePanel.less'

type Props = { workspaceRoot: string; entityId: string; label: string }
const { Text } = Typography
const TABLE_LABELS: Record<string, string> = {
  SYNCED: '当前结构一致', NOT_CREATED: '尚未建表',
  STRUCTURE_MISMATCH: '结构不一致', TABLE_CONFLICT: '表名冲突'
}

/** 展示当前实体的字段设计与建表结果，内部 SQL 和资源标识不直接展示。 */
export default function EntityTablePanel({ workspaceRoot, entityId, label }: Props): ReactElement {
  const [data, setData] = useState<EntityTablePayload>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [issue, setIssue] = useState<EntityTableIssue>()
  const [confirming, setConfirming] = useState<'execute' | 'rebuild'>()
  const [allowDataLoss, setAllowDataLoss] = useState(false)
  const [mustRefresh, setMustRefresh] = useState(false)
  const [autoRefreshScheduled, setAutoRefreshScheduled] = useState(false)
  const active = useRef(true)
  const pending = useRef(false)
  const autoRefreshTimer = useRef<number>()
  const notifiedOperation = useRef<string>()
  const preview = data?.preview
  const readiness = data?.readiness
  const tableState = readiness?.tables[0]
  const operation = data?.operation
  const operationId = operation?.operationId || data?.receipt?.operationId
  const operationState = operation?.status || data?.receipt?.status

  useEffect(() => {
    active.current = true
    // 首次只生成预览，不查库、不提交 SQL；切换实体后丢弃原视图的异步结果。
    void runAction('prepare')
    return () => {
      active.current = false
      cancelAutoRefresh()
    }
  }, [])

  /** 取消尚未触发的单次刷新，卸载时不再更新组件状态。 */
  function cancelAutoRefresh(): void {
    if (autoRefreshTimer.current !== undefined) {
      window.clearTimeout(autoRefreshTimer.current)
      autoRefreshTimer.current = undefined
    }
    if (active.current) setAutoRefreshScheduled(false)
  }

  /** 运行一次动作；创建及重建受理后只安排一次查询，异常不自动重发。 */
  async function runAction(action: 'prepare' | 'execute' | 'rebuild' | 'refresh'): Promise<void> {
    if (pending.current) return
    const submitting = action === 'execute' || action === 'rebuild'
    if (action === 'rebuild' && !allowDataLoss) return
    // 手动刷新、重新生成或再次执行会取消旧定时器，避免重复查询。
    cancelAutoRefresh()
    pending.current = true
    setLoading(true)
    setError('')
    setIssue(undefined)
    setConfirming(undefined)
    setAllowDataLoss(false)
    if (submitting) {
      setMustRefresh(true)
      // 新提交尚未得到本次结构证据，不继续展示旧的结构检查结果。
      setData((current) => current ? { ...current, readiness: undefined } : current)
    }
    try {
      const result = await requestEntityTable(workspaceRoot, entityId, action, preview?.previewHash, allowDataLoss)
      if (!active.current) return
      setData(result)
      if (submitting && result.receipt?.status === 'RUNNING') {
        setAutoRefreshScheduled(true)
        autoRefreshTimer.current = window.setTimeout(() => {
          autoRefreshTimer.current = undefined
          if (!active.current) return
          setAutoRefreshScheduled(false)
          void runAction('refresh')
        }, 10_000)
      }
      if (action === 'refresh') {
        setMustRefresh(['RUNNING', 'UNKNOWN'].includes(result.readiness?.operationStatus || '')
          || ['RUNNING', 'UNKNOWN'].includes(result.operation?.status || ''))
        // 重建失败也可能已删除旧表，确定终态后让目录与 API 映射回读实际结构。
        const operation = result.operation
        if (operation && ['SUCCEEDED', 'FAILED'].includes(operation.status)
          && notifiedOperation.current !== operation.operationId) {
          notifiedOperation.current = operation.operationId
          notifyDataSourcesChanged(workspaceRoot)
        }
      }
    } catch (caught) {
      if (active.current) {
        setError(caught instanceof Error ? caught.message : '实体建表操作失败。')
        if (caught instanceof EntityTableActionError) {
          setIssue(caught.issue)
          // 仅明确的 CREATE 结构冲突表示本次未受理，允许用户另行确认重建。
          // 网络中断、资源占用或未知冲突仍要求先刷新，不能解锁破坏性操作。
          if (action === 'execute' && caught.issue.action === 'rebuild') setMustRefresh(false)
        }
      }
    } finally {
      pending.current = false
      if (active.current) setLoading(false)
    }
  }

  /** 每次打开创建或重建确认时清除上次的数据丢弃勾选，避免复用授权。 */
  function beginConfirmation(action: 'execute' | 'rebuild'): void {
    setAllowDataLoss(false)
    setConfirming(action)
  }

  return (
    <div className={cx('entity-table-panel')}>
      <header className={cx('entity-table-header')}>
        <span className={cx('entity-table-icon')}><DatabaseOutlined /></span>
        <div className={cx('entity-table-heading')}><h3>{preview?.entityName || label}</h3><Text type="secondary">平台内置数据库 · 实体表设计</Text></div>
        <Space wrap>
          <Button disabled={loading} icon={<ReloadOutlined />} onClick={() => void runAction('prepare')}>刷新设计</Button>
          <Button disabled={loading || !preview} icon={<ReloadOutlined />} onClick={() => void runAction('refresh')}>刷新结果</Button>
        </Space>
      </header>
      {error ? <Alert showIcon type={issue ? 'warning' : 'error'}
        message={issue?.title || '操作未完成'} description={error} /> : null}
      <Spin spinning={loading}>
        {preview ? <>
          <div className={cx('entity-table-meta')}>
            <div className={cx('entity-table-name')}><Text type="secondary">表名</Text><span className={cx('entity-table-field')}>{preview.tableName}</span></div>
            <div><Text type="secondary">数据源</Text><Text>平台内置数据库</Text></div>
          </div>
          <section>
            <div className={cx('entity-table-section-head')}>
              <div className={cx('entity-table-section-title')}><h4>字段设计</h4><Text type="secondary">{preview.columns.length} 个字段</Text></div>
              <Space wrap>
                <Button type="primary" disabled={loading || mustRefresh} icon={<PlayCircleOutlined />} onClick={() => beginConfirmation('execute')}>创建表</Button>
                <Button disabled={loading || mustRefresh} icon={<RetweetOutlined />} onClick={() => beginConfirmation('rebuild')}>重建表</Button>
              </Space>
            </div>
            <Table size="small" pagination={false} rowKey="name" scroll={{ x: 480 }} dataSource={preview.columns} columns={[
              { title: '字段', dataIndex: 'name', render: (name: string) => <span className={cx('entity-table-field')}>{name}</span> },
              { title: '类型', dataIndex: 'type' },
              { title: '必填', dataIndex: 'nullable', render: (nullable: boolean) => nullable ? '否' : '是' },
              { title: '说明', dataIndex: 'comment' }
            ]} />
            <p className={cx('entity-table-hint')}><Text type="secondary">字段来自已确认的技术规划。修改后，请重新确认技术规划并刷新设计。</Text></p>
          </section>
          {confirming ? <Alert showIcon type={confirming === 'rebuild' ? 'warning' : 'info'}
            message={confirming === 'rebuild' ? '确认清空数据并重建表？' : '确认按当前字段设计创建表？'}
            description={<div>
              {confirming === 'rebuild' ? <>
                <p>将删除 <Text code>{preview.tableName}</Text> 的全部数据，并按当前字段设计重建。此操作无法自动恢复数据。</p>
                <p>请先停止应用预览服务；重建完成前请勿启动应用。</p>
                <Checkbox checked={allowDataLoss} disabled={loading} onChange={(event) => setAllowDataLoss(event.target.checked)}>
                  我确认清空该表全部数据并重建
                </Checkbox>
              </> : <>将在模拟数据库创建 <Text code>{preview.tableName}</Text>。已有表结构不一致时会拒绝执行。</>}
              <Space className={cx('entity-table-confirm-actions')}>
                <Button disabled={loading} onClick={() => { setConfirming(undefined); setAllowDataLoss(false) }}>取消</Button>
                <Button type="primary" disabled={loading || (confirming === 'rebuild' && !allowDataLoss)} onClick={() => void runAction(confirming)}>
                  {confirming === 'rebuild' ? '确认清空并重建' : '确认创建'}
                </Button>
              </Space>
            </div>} /> : null}
          <section className={cx('entity-table-result')}>
            <h4>执行与结构检查</h4>
            <Space wrap>
              <Tag>{tableState ? TABLE_LABELS[tableState.status] : '当前结构未检查'}</Tag>
              <DatabaseOperationTag operation={operation} status={operationState} />
            </Space>
            {operationId ? <p>最近操作 ID：<Text code>{operationId}</Text></p> : null}
            {operation?.executionResult.errorSummary ? <p>{operation.executionResult.errorSummary} {operation.executionResult.errorCode}</p> : null}
            {operation?.executionResult.recoveryState === 'MANUALLY_ABANDONED' ? <p>该操作已由人工结束，请以当前结构检查结果为准。</p> : null}
            {readiness?.reason ? <p>{readiness.reason}</p> : null}
            {tableState?.differences.map((difference, index) => <p key={index}>
              {difference.target} · {difference.category}：期望 {difference.expected || '-'}，实际 {difference.actual || '-'}
            </p>)}
            {mustRefresh ? <Text type="secondary">
              {autoRefreshScheduled ? '已受理，将于 10 秒后自动刷新一次执行结果。' : '请刷新原操作结果。执行中或结果未知时不能再次提交。'}
            </Text> : null}
            <p><Text type="secondary">检查只针对当前实体；建表成功不代表应用调试连接已接通。</Text></p>
          </section>
        </> : <div className={cx('entity-table-empty')}>确认技术规划后可查看实体表设计。</div>}
      </Spin>
    </div>
  )
}
