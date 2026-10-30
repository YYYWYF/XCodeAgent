import { Alert, Button, List, Space, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkbenchExecution } from '../../../../typings'
import type { RuntimeAgent } from '../../../../service/runtimeWorkbench'

const labels: Record<string, string> = {
  pending: '待开发', running: '执行中', completed: '已完成', already_satisfied: '已满足',
  failed: '失败', blocked: '受阻', stopped: '已停止', awaiting_user: '等待确认', stopping: '停止中'
}

/** 展示同一正式 DAG 的模块状态，继续、重试和结束统一回到原 Workflow 任务。 */
export default function AgentDevelopmentStatus({ agent, disabled, onStart, onOpen }: {
  agent: RuntimeAgent; disabled?: boolean; onStart?: (agent: RuntimeAgent) => Promise<void>
  onOpen?: (execution: WorkbenchExecution) => void
}): ReactElement {
  const development = agent.development
  const execution = development?.execution
  const progress = development?.progress
  const completed = progress?.initialDevelopmentStatus === 'completed'
  const changed = completed && progress?.changeStatus !== 'unchanged'
  return <section className="agent-detail-section">
    <div className="agent-detail-section-title"><h3>智能体开发</h3>
      <Tag>{changed ? '需更新实现' : completed ? '已完成开发' : '待开发'}</Tag></div>
    {execution && <Alert showIcon type={execution.status === 'failed' ? 'error' : 'info'}
      message={`当前任务：${labels[execution.status] || execution.status}`}
      description={execution.error?.message || '请在原任务中继续确认、重试或结束，保留原执行身份。'}
      action={<Button disabled={!onOpen} onClick={() => onOpen?.(execution)}>打开当前任务</Button>} />}
    {changed && <Typography.Paragraph type="secondary">{progress?.changeReason}</Typography.Paragraph>}
    <List size="small" dataSource={development?.modules || []} locale={{ emptyText: '尚未生成开发任务' }}
      renderItem={(item) => <List.Item><div>
        <Space><strong>{item.label}</strong><Tag>{labels[item.status] || item.status}</Tag></Space>
        {item.reason && <Typography.Paragraph type="secondary">{item.reason}</Typography.Paragraph>}
        {item.files.map((path) => <Typography.Paragraph key={path}><code>{path}</code></Typography.Paragraph>)}
      </div></List.Item>} />
    <Button type="primary" disabled={disabled || Boolean(execution) || !onStart}
      onClick={() => void onStart?.(agent)}>{completed ? '更新智能体实现' : '开始开发智能体'}</Button>
    <Typography.Paragraph type="secondary">开发沿原任务规划、确认、Build 和单测门禁执行；业务会话请在应用预览中验证。</Typography.Paragraph>
  </section>
}
