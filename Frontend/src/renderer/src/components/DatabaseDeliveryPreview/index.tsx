import { DatabaseOutlined } from '@ant-design/icons'
import { useRef } from 'react'
import { cx } from '../../utils'
import { databaseDeliveryPaths } from './databaseDeliveryPreview'
import type { DeliveryFileName } from './databaseDeliveryPreview'
import DatabaseDeliveryFileCard from './DatabaseDeliveryFileCard'
import './DatabaseDeliveryPreview.less'

type Props = { workspaceRoot: string; branchName: string; onReadyChange?: (ready: boolean) => void }
const names: DeliveryFileName[] = ['create.sql', 'alter.sql']

/** 汇总两份真实交付文件的读取状态，并以独立卡片提供只读预览。 */
export default function DatabaseDeliveryPreview({
  workspaceRoot,
  branchName,
  onReadyChange
}: Props): JSX.Element {
  const sourceKey = JSON.stringify([workspaceRoot, branchName])
  const readiness = useRef({ sourceKey, 'create.sql': false, 'alter.sql': false })
  if (readiness.current.sourceKey !== sourceKey) {
    readiness.current = { sourceKey, 'create.sql': false, 'alter.sql': false }
  }
  let paths: ReturnType<typeof databaseDeliveryPaths> | undefined,
    pathError = ''
  try {
    paths = databaseDeliveryPaths(branchName)
  } catch (error) {
    pathError = (error as Error).message
  }

  /** 任一文件重读或失败即禁用提交；旧工作区回调不能影响当前状态。 */
  function updateReady(name: DeliveryFileName, ready: boolean): void {
    if (readiness.current.sourceKey !== sourceKey) return
    readiness.current[name] = ready
    onReadyChange?.(readiness.current['create.sql'] && readiness.current['alter.sql'])
  }

  return (
    <section className={cx('database-delivery-preview')} aria-label="数据库交付脚本">
      <h3>
        <DatabaseOutlined aria-hidden="true" />
        数据库交付脚本
      </h3>
      <div className={cx('database-delivery-paths')}>
        {names.map((name, index) => (
          <div className={cx('database-delivery-path')} key={name}>
            <span>{index === 0 ? '完整建表：' : '增量更新：'}</span>
            <code>{paths?.[name] || `（版本名无效）/${name}`}</code>
          </div>
        ))}
      </div>
      {names.map((name) => (
        <DatabaseDeliveryFileCard
          key={JSON.stringify([sourceKey, name])}
          name={name}
          workspaceRoot={workspaceRoot}
          path={paths?.[name] || ''}
          pathError={pathError}
          onReadyChange={(ready) => updateReady(name, ready)}
        />
      ))}
    </section>
  )
}
