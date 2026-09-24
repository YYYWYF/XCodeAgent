import { Alert, Button, Spin, Table, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useState } from 'react'
import type {
  ApplicationLifecycle,
  DevelopmentArtifactProgress,
  DevelopmentPlanningEntityOption
} from '../../../../typings'
import {
  confirmDirectEntityDesign,
  readDirectEntityDesign
} from '../../../../service/directDevelopment'
import type { DirectEntityDesign } from '../../../../service/directDevelopment'
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

/** 在截图所示的实体详情区预览 SQL，明确区分确认与目标库执行。 */
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
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    if (!workspaceRoot) return undefined
    setLoading(true)
    setError('')
    setDesign(undefined)
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

  const fields = Array.isArray(entity.fields) ? entity.fields : []
  const confirmed = design?.status === 'confirmed'
  const lifecycleComplete = progress?.initialDevelopmentStatus === 'completed'
  return (
    <DevelopmentTargetDetail
      actionLabel={confirming ? '正在确认…' : confirmed ? '同步实体完成状态' : '确认实体 SQL'}
      description={entity.purpose}
      disabled={disabled || loading || confirming || !design}
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
            message="SQL 只在隔离的临时 SQLite 库校验；确认后保存到生成项目，目标业务库仍需你自行执行。"
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
              <Button onClick={() => navigator.clipboard.writeText(design.sql)}>复制 SQL</Button>
            </>
          ) : null}
        </div>
      }
      hint="确认建表 SQL 后即完成实体开发；无需绑定数据源。SQL 不会在目标业务库自动执行。"
      kind="entity"
      progress={progress}
      title={entity.label}
      onStart={() => { void handleConfirm() }}
    />
  )
}
