import { Alert, Button } from 'antd'
import type { ReactElement } from 'react'
import type { WorkbenchExecution } from '../../../../typings'
import type { RuntimeAgent } from '../../../../service/runtimeWorkbench'
import './AgentDevelopmentStatus.less'

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
  const modules = development?.modules || []
  // 汇总仅依据真实模块记录，不用产物完成状态推断当前任务成功。
  const readyCount = modules.filter((item) => ['completed', 'already_satisfied'].includes(item.status)).length
  return <section className="agent-development-status">
    <header className="agent-development-heading">
      <div><h3>开发进度</h3><p>模块实现记录与当前任务结果分别展示。</p></div>
      <span className="agent-development-summary" data-changed={changed}>开发产物：{changed ? '需更新实现' : completed ? '已完成开发' : '待开发'}</span>
    </header>
    {execution && <Alert className="agent-development-execution" showIcon type={execution.status === 'failed' ? 'error' : 'info'}
      message={`当前任务${labels[execution.status] || execution.status}`}
      description={<div><p>{execution.error?.message || '请打开当前任务，继续确认、重试或结束。'}</p>
        {execution.status === 'failed' && completed && <p className="agent-development-execution-note">开发产物已登记完成，本次任务失败原因仍需在任务中处理。</p>}</div>}
      action={<Button disabled={!onOpen} onClick={() => onOpen?.(execution)}>打开当前任务</Button>} />}
    {changed && progress?.changeReason && <p className="agent-development-change">{progress.changeReason}</p>}
    <section className="agent-development-modules" aria-label="模块实现记录">
      <div className="agent-development-modules-heading"><h4>模块实现</h4>
        {modules.length > 0 && <span>{readyCount}/{modules.length} 项已完成或已满足</span>}</div>
      {modules.length ? <table className="agent-development-table">
        <thead><tr><th scope="col">模块</th><th scope="col">状态</th><th scope="col">实现文件与说明</th></tr></thead>
        <tbody>{modules.map((item) => <tr key={item.module}>
          <th scope="row">{item.label}</th>
          <td><span className="agent-development-module-status" data-status={item.status}>{labels[item.status] || item.status}</span></td>
          <td>{item.files.length > 0 && <ul>{item.files.map((path) => <li key={path}><code>{path}</code></li>)}</ul>}
            {item.reason && <p>{item.reason}</p>}
            {!item.files.length && !item.reason && <span className="agent-development-no-files">暂无实现文件记录</span>}
          </td>
        </tr>)}</tbody>
      </table> : <div className="agent-development-empty"><strong>尚未生成开发任务</strong><p>开始开发后，可在这里查看各模块的实现状态与文件。</p></div>}
    </section>
    <footer className="agent-development-footer">
      {!execution && <Button type="primary" disabled={disabled || !onStart}
        onClick={() => void onStart?.(agent)}>{completed ? '更新智能体实现' : '开始开发智能体'}</Button>}
      <p>{execution ? '继续或重试请打开当前任务。' : '开发需经过任务规划、确认、构建和单测。'}业务会话请在应用预览中验证。</p>
    </footer>
  </section>
}
