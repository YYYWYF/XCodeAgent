import { CheckCircleOutlined, DatabaseOutlined, ExclamationCircleOutlined, LoadingOutlined, RightOutlined } from '@ant-design/icons'
import { Button } from 'antd'
import type { ReactElement } from 'react'
import type { PreviewDatabaseCheck } from '../../service/previewRuntime'
import './DatabaseCheckPanel.less'

type Props = {
  report?: PreviewDatabaseCheck
  onOpenEntity?: (entityId: string) => void
}

const tableLabels = {
  SYNCED: '结构一致', NOT_CREATED: '尚未建表', STRUCTURE_MISMATCH: '结构不一致',
  TABLE_CONFLICT: '表归属异常', CHECKING: '检查中', NOT_CHECKED: '未完成检查'
}

/** 只显示有效检查时间，保留“最近检查”语义，避免被理解为实时监控。 */
function checkedTime(value?: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { hour12: false })
}

/** 默认折叠本轮数据库检查，展开后查看异常和实体；原生折叠支持鼠标及键盘操作。 */
export default function DatabaseCheckPanel({ report, onOpenEntity }: Props): ReactElement {
  const entities = report?.entities || []
  const passed = entities.filter((entity) => entity.status === 'SYNCED').length
  const problems = entities.filter((entity) => ['NOT_CREATED', 'STRUCTURE_MISMATCH', 'TABLE_CONFLICT'].includes(entity.status)).length
  const rows = [...entities].sort((left, right) => Number(left.status === 'SYNCED') - Number(right.status === 'SYNCED'))
  const status = report?.status || 'not_checked'
  const time = checkedTime(report?.checkedAt)
  const Icon = status === 'checking' ? LoadingOutlined : status === 'ready' ? CheckCircleOutlined : status === 'not_checked' ? DatabaseOutlined : ExclamationCircleOutlined

  return <section className={`preview-database-check is-${status}`} aria-labelledby="preview-database-check-heading">
    <details className="preview-database-check__collapse">
      <summary className="preview-database-check__header" id="preview-database-check-heading">
        <span className="preview-database-check__title"><DatabaseOutlined /><strong>数据库检查</strong></span>
        <span className="preview-database-check__status"><Icon spin={status === 'checking'} />
          {status === 'checking' ? '检查中' : status === 'ready' ? `已通过 ${passed}/${entities.length}` : status === 'blocked' ? `待处理${problems ? ` ${problems}` : ''}` : status === 'error' ? '检查未完成' : '尚未检查'}
        </span>
        <span className="preview-database-check__toggle" aria-hidden="true">
          <span className="when-collapsed">展开</span><span className="when-expanded">收起</span><RightOutlined />
        </span>
      </summary>
      <div className="preview-database-check__details">
        {status !== 'ready' && <p className="preview-database-check__message">{report?.message || '启动服务时会检查当前应用的全部实体表。'}</p>}
        {entities.length > 0 && status !== 'ready' && <div className="preview-database-check__summary">
          <span>已通过 <strong>{passed}/{entities.length}</strong></span>
        </div>}
        {rows.length > 0 && <ul className="preview-database-check__list">
          {rows.map((entity) => <li key={entity.entityId} className={`preview-database-check__entity is-${entity.status.toLowerCase()}`}>
            <div className="preview-database-check__entity-heading">
              <strong title={entity.label}>{entity.label}</strong>
              <span>{tableLabels[entity.status]}</span>
              {onOpenEntity && <Button type="link" size="small" aria-label={`查看实体：${entity.label}`}
                onClick={() => onOpenEntity(entity.entityId)}>查看实体</Button>}
            </div>
            <code className="preview-database-check__table">{entity.tableName}</code>
            {entity.differences.length > 0 && <ul className="preview-database-check__differences">
              {entity.differences.slice(0, 3).map((difference, index) => <li key={`${difference.category}:${difference.target}:${index}`}>
                <strong>{difference.target}</strong>
                <span>期望：{difference.expected ?? '未设置'}；实际：{difference.actual ?? '未设置'}</span>
              </li>)}
              {entity.differenceCount > 3 && <li>另有 {entity.differenceCount - 3} 项差异，请进入实体查看。</li>}
            </ul>}
          </li>)}
        </ul>}
        {time && <p className="preview-database-check__time" title="这是最近一次启动检查，修改实体或执行建表后需重新启动复核。">最近检查：{time}</p>}
      </div>
    </details>
  </section>
}
