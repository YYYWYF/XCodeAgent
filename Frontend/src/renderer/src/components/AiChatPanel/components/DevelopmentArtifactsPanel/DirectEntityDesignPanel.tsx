import { Alert, Button, Space, Spin, Table, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useState } from 'react'
import type {
  ApplicationLifecycle,
  DevelopmentArtifactProgress,
  DevelopmentPlanningEntityOption
} from '../../../../typings'
import {
  confirmDirectEntityDesign,
  executeDirectEntitySql,
  readDirectEntityDesign
} from '../../../../service/directDevelopment'
import type { DirectEntityDesign, DirectEntityExecution } from '../../../../service/directDevelopment'
import { cx } from '../../../../utils'
import DevelopmentTargetDetail from './DevelopmentTargetDetail'

const { Text } = Typography

type Props = {
  disabled?: boolean
  entity: DevelopmentPlanningEntityOption
  progress?: DevelopmentArtifactProgress
  workspaceRoot?: string
  onLifecycleChange: (lifecycle: ApplicationLifecycle) => void
}

/** 在实体详情区预览、确认并按用户动作执行当前 SQL。 */
export default function DirectEntityDesignPanel({
  disabled,
  entity,
  progress,
  workspaceRoot,
  onLifecycleChange
}: Props): ReactElement {
  const [design, setDesign] = useState<DirectEntityDesign>()
  const [loading, setLoading] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [executing, setExecuting] = useState(false)
  const [execution, setExecution] = useState<DirectEntityExecution>()
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    if (!workspaceRoot) return undefined
    setLoading(true)
    setError('')
    setDesign(undefined)
    setExecution(undefined)
    readDirectEntityDesign(workspaceRoot, entity.id)
      .then((result) => {
        if (active) setDesign(result)
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : '实体 SQL 生成失败。')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [entity.id, workspaceRoot])

  /** 使用本次预览摘要确认实体设计，再同步服务端权威开发状态。 */
  const handleConfirm = async (): Promise<void> => {
    if (!workspaceRoot || !design || confirming) return
    setConfirming(true)
    setError('')
    setExecution(undefined)
    try {
      const saved = await confirmDirectEntityDesign(workspaceRoot, entity.id, design.sqlSha256)
      setDesign(saved.entity)
      onLifecycleChange(saved.lifecycle)
      if (saved.lifecycle.developmentArtifacts?.entities[entity.id]?.initialDevelopmentStatus !== 'completed') {
        setError(
          saved.lifecycle.developmentArtifacts?.catalogError ||
          'SQL 已确认，但开发产物状态尚未同步为完成。请再次点击以重试同步。'
        )
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '实体 SQL 确认失败。')
    } finally {
      setConfirming(false)
    }
  }

  /** 执行当前已确认的 SQL，并显示数据库位置或具体失败原因。 */
  const handleExecute = async (): Promise<void> => {
    if (!workspaceRoot || !design || design.status !== 'confirmed' || executing) return
    setExecuting(true)
    setError('')
    setExecution(undefined)
    try {
      setExecution(await executeDirectEntitySql(workspaceRoot, entity.id, design.sqlSha256))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '实体 SQL 执行失败。')
    } finally {
      setExecuting(false)
    }
  }

  const fields = Array.isArray(entity.fields) ? entity.fields : []
  const confirmed = design?.status === 'confirmed'
  const lifecycleComplete = progress?.initialDevelopmentStatus === 'completed'
  return (
    <DevelopmentTargetDetail
      actionLabel={confirming ? '正在确认…' : confirmed ? '同步实体完成状态' : '确认实体 SQL'}
      description={entity.purpose}
      disabled={disabled || loading || confirming || executing || !design}
      notice={
        error ? <Alert message={error} showIcon type="error" />
          : confirming ? <Alert message="正在保存实体 SQL 并同步开发完成状态…" showIcon type="info" />
            : confirmed && lifecycleComplete ? <Alert message="当前 SQL 已确认，实体开发已完成。" showIcon type="success" />
              : confirmed ? <Alert message="SQL 已确认，开发产物计数仍待同步。点击右侧按钮重试同步。" showIcon type="warning" />
              : null
      }
      extra={
        <div className={cx('direct-entity-design')}>
          <Alert
            message="SQL 先在隔离 SQLite 中校验。确认后可点击执行 SQL，写入当前项目的业务 SQLite。"
            showIcon
            type="info"
          />
          {loading ? <p><Spin /> 正在生成并校验建表 SQL…</p> : null}
          <h3>实体字段</h3>
          <Table
            columns={[
              { title: '字段', dataIndex: 'name', key: 'name' },
              { title: '名称', dataIndex: 'label', key: 'label' },
              { title: '类型', dataIndex: 'type', key: 'type' },
              { title: '必填', dataIndex: 'required', key: 'required', render: (value: boolean) => value ? '是' : '否' }
            ]}
            dataSource={fields.map((field) => ({ ...field, key: field.name }))}
            pagination={false}
            size="small"
          />
          {design ? (
            <>
              <h3>建表 SQL</h3>
              <Text code>{design.sqlPath}</Text>
              <pre className={cx('direct-entity-sql')}><code>{design.sql}</code></pre>
              <Space>
                <Button onClick={() => navigator.clipboard.writeText(design.sql)}>复制 SQL</Button>
                <Button
                  disabled={disabled || !confirmed || confirming || executing}
                  loading={executing}
                  onClick={() => { void handleExecute() }}
                >
                  执行 SQL
                </Button>
              </Space>
              {execution ? (
                <Alert
                  message={execution.status === 'applied' ? 'SQL 已执行' : 'SQL 此前已执行'}
                  description={`数据库：${execution.databasePath}；迁移：${execution.migrationName}`}
                  showIcon
                  type="success"
                />
              ) : null}
            </>
          ) : null}
        </div>
      }
      hint="确认建表 SQL 后即完成实体开发；点击执行 SQL 可写入当前项目业务 SQLite，重复执行会显示已应用。"
      kind="entity"
      progress={progress}
      title={entity.label}
      onStart={() => { void handleConfirm() }}
    />
  )
}
