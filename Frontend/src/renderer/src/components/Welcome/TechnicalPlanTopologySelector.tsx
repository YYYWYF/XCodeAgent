import { Alert, Button, Form, Radio, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkflowClarification } from '../../typings'
import { cx } from '../../utils'
import './TechnicalPlanTopologySelector.less'

type Props = {
  disabled?: boolean
  options: NonNullable<WorkflowClarification['topologyOptions']>
  onSelect: (topologyType: string) => void
}

/** 展示当前架构选项；Runtime 验证入口已开放，不被待确认卡片保存的旧可用性锁住。 */
export default function TechnicalPlanTopologySelector({ disabled, options, onSelect }: Props): ReactElement {
  // 待确认卡片会保存生成时的选项。Runtime 本次已明确开放验证，展示按当前产品状态刷新；
  // 提交仍沿原 AG-UI 门禁，由后端检查应用配置、正式选择和产物版本。
  const selectableOptions = options.map((option) => option.type === 'agent_runtime_direct'
    ? {
        ...option,
        label: 'Agent 应用',
        available: true,
        reason: undefined,
        description: '前端 + Python Runtime，支持业务接口与七模块智能体。已接入数据库、模型注入和受控启动，需同步当前 Runtime 模板后验证。当前仅支持关闭登录和授权的应用。'
      }
    : option)

  /** 只提交用户实际选择的架构，后端继续验证选项、门禁与产物版本。 */
  const submit = (values: { topologyType: string }): void => {
    onSelect(values.topologyType)
  }

  return (
    <section className={cx('technical-topology-selector')}>
      <Typography.Title level={4}>选择应用架构</Typography.Title>
      <Typography.Paragraph type="secondary">
        选择后将更新技术规划，请审阅并确认后再生成应用模板。
      </Typography.Paragraph>
      {!selectableOptions.length ? (
        <Alert type="error" showIcon message="架构选项尚未加载，请重新同步当前规划。" />
      ) : (
        <Form layout="vertical" onFinish={submit}>
          <Form.Item name="topologyType" rules={[{ required: true, message: '请选择应用架构' }]}>
            <Radio.Group disabled={disabled}>
              {selectableOptions.map((option) => (
                <article key={option.type} className={cx('technical-topology-option')}>
                  <Radio value={option.type} disabled={!option.available}>
                    <Typography.Text strong>{option.label}</Typography.Text>
                  </Radio>
                  <Typography.Paragraph type="secondary">{option.description}</Typography.Paragraph>
                  {!option.available && (
                    <Alert type="info" showIcon message="暂不可用" description={option.reason} />
                  )}
                </article>
              ))}
            </Radio.Group>
          </Form.Item>
          <Button type="primary" htmlType="submit" disabled={disabled || !selectableOptions.some((option) => option.available)}>
            选择架构，审阅技术规划
          </Button>
        </Form>
      )}
    </section>
  )
}
