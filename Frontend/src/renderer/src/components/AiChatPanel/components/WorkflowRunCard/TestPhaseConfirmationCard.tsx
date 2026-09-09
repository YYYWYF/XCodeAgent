import { CheckCircleOutlined, RightOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkflowTestTarget } from '../../../../typings'
import { cx } from '../../../../utils'
import { useTestEntryGate } from '../../../../context'
import { testEntryGateReason } from '../../../../developmentArtifacts'
import './TestPhaseConfirmationCard.less'

const { Text } = Typography

type Props = {
  disabled?: boolean
  target?: WorkflowTestTarget
  onSubmit: () => void
}

const TEST_PHASE_CONFIRMATION_DESCRIPTION =
  '代码生成、Build 与单元测试门禁已完成，确认后将进入测试阶段，执行测试与失败修复'

/** 渲染 Build 完成后的测试阶段进入确认卡。 */
export default function TestPhaseConfirmationCard({
  disabled,
  target,
  onSubmit
}: Props): ReactElement {
  const gate = useTestEntryGate()
  return (
    <div className={cx('workflow-test-phase-confirmation')}>
      <div className={cx('workflow-test-phase-confirmation-title')}>
        <span className={cx('workflow-test-phase-confirmation-icon')} aria-hidden="true">
          <CheckCircleOutlined />
        </span>
        <div>
          <Text strong>当前产物初次开发已完成</Text>
          <Text type="secondary">
            {gate?.allowed ? TEST_PHASE_CONFIRMATION_DESCRIPTION : testEntryGateReason(gate)}
          </Text>
        </div>
      </div>
      {gate?.allowed && target?.label ? (
        <div className={cx('workflow-test-phase-confirmation-target')}>
          <Text type="secondary">测试目标</Text>
          <Text>{target.label}</Text>
        </div>
      ) : null}
      {!gate?.allowed && gate?.blockers.length ? (
        <section
          className={cx('workflow-test-phase-confirmation-remaining')}
          aria-label="未完成产物"
        >
          <div className={cx('workflow-test-phase-confirmation-remaining-heading')}>
            <Text strong>未完成产物</Text>
            <span>{gate.blockers.length} 项</span>
          </div>
          <ul>
            {gate.blockers.map((item) => (
              <li key={JSON.stringify(item)}>
                <span className={cx('workflow-test-phase-confirmation-kind')}>
                  {item.type === 'page' ? '页面' : item.type === 'entity' ? '实体' : '接口'}
                </span>
                <span className={cx('workflow-test-phase-confirmation-name')}>
                  {item.type === 'page'
                    ? item.pageId
                    : item.type === 'entity'
                      ? item.entityId
                      : `${item.apiContractId}/${item.endpointId}`}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {gate?.allowed ? (
        <Button
          block
          disabled={disabled}
          icon={<RightOutlined />}
          onClick={onSubmit}
          type="primary"
        >
          进入测试阶段
        </Button>
      ) : null}
    </div>
  )
}
