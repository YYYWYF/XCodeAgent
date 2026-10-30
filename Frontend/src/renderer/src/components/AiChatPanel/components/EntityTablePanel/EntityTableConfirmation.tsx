import { ExclamationCircleOutlined } from '@ant-design/icons'
import { Checkbox, Modal } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import type useEntityTable from './useEntityTable'

/** 在当前主题上下文中确认建表或清空重建，每次重新勾选丢弃数据授权。 */
export default function EntityTableConfirmation({ state }: {
  state: ReturnType<typeof useEntityTable>
}): ReactElement {
  const rebuilding = state.confirming === 'rebuild'
  return (
    <Modal
      open={Boolean(state.confirming)}
      className={cx('entity-table-confirmation')}
      getContainer={false}
      title={<span className={cx('entity-table-confirm-title')}>
        <ExclamationCircleOutlined />{rebuilding ? '重建数据表？' : '创建数据表？'}
      </span>}
      width={440}
      maskClosable={false}
      onCancel={state.cancelConfirmation}
      onOk={() => { if (state.confirming) void state.runAction(state.confirming) }}
      cancelText="取消"
      okText={rebuilding ? '确认清空并重建' : '确认创建'}
      okButtonProps={{ danger: rebuilding, disabled: state.loading || (rebuilding && !state.allowDataLoss) }}
    >
      <p>{rebuilding ? '将删除当前数据表，再按最新字段设计重新创建。' : '将按当前已确认的字段设计创建数据表。'}</p>
      <div className={cx('entity-table-confirm-name')}>{state.data?.preview?.tableName}</div>
      {rebuilding ? <>
        <p className={cx('entity-table-confirm-warning')}>表中的现有数据将被清空，此操作无法撤销。</p>
        <p>请先停止应用预览服务，重建完成后再启动。</p>
        <Checkbox checked={state.allowDataLoss} disabled={state.loading}
          onChange={(event) => state.setAllowDataLoss(event.target.checked)}>
          我确认清空该表全部数据并重建
        </Checkbox>
      </> : <p>若已有表与当前设计不一致，系统会拒绝创建并提示结构差异。</p>}
    </Modal>
  )
}
