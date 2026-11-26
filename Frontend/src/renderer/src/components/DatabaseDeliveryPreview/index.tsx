import { DatabaseOutlined } from '@ant-design/icons'
import { cx } from '../../utils'
import { databaseDeliveryPaths } from './databaseDeliveryPreview'
import type { DeliveryFileName } from './databaseDeliveryPreview'
import DatabaseDeliveryFileCard from './DatabaseDeliveryFileCard'
import './DatabaseDeliveryPreview.less'

type Props = { workspaceRoot: string; branchName: string }
const names: DeliveryFileName[] = ['create.sql', 'alter.sql']

/** 以独立卡片提供交付文件的只读预览，读取状态不影响版本提交。 */
export default function DatabaseDeliveryPreview({
  workspaceRoot,
  branchName
}: Props): JSX.Element {
  const sourceKey = JSON.stringify([workspaceRoot, branchName])
  let paths: ReturnType<typeof databaseDeliveryPaths> | undefined,
    pathError = ''
  try {
    paths = databaseDeliveryPaths(branchName)
  } catch (error) {
    pathError = (error as Error).message
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
        />
      ))}
    </section>
  )
}
