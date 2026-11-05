import { Alert, Button } from 'antd'
import type { ReactElement } from 'react'
import type { WorkbenchExecution } from '../../../../typings'
import type { RuntimeAgent } from '../../../../service/runtimeWorkbench'
import type { DevelopmentPlanningApiContract } from '../../../../typings'
import { agentDevelopmentPresentation, developmentStatusLabels as labels } from './agentDevelopmentPresentation'
import './AgentDevelopmentStatus.less'

/** 先展示智能体开发事项与业务工具，再按需展开实现证据；所有操作沿用原任务和调试入口。 */
export default function AgentDevelopmentStatus({ agent, disabled, onStart, onOpen, onDebug, onOpenFile, apiContracts = [] }: {
  agent: RuntimeAgent; disabled?: boolean; onStart?: (agent: RuntimeAgent) => Promise<void>
  onOpen?: (execution: WorkbenchExecution) => void
  onDebug?: () => void; apiContracts?: DevelopmentPlanningApiContract[]
  onOpenFile?: (path: string) => void
}): ReactElement {
  const execution = agent.development?.execution
  const view = agentDevelopmentPresentation(agent)
  const { modules, items, readyCount, completed, changed, unavailable, canDebug } = view
  const bindings = Array.isArray(agent.agentSettings.tools.bindings)
    ? agent.agentSettings.tools.bindings as { toolId: string; apiContractId: string; endpointId: string }[] : []
  return <section className="agent-development-status" aria-label="智能体开发进度">
    <header className="agent-development-heading">
      <div><span className="agent-development-state" data-status={view.state}>开发进度</span>
        <h3>{view.title}</h3><p>{view.summary}</p></div>
      <div className="agent-development-actions">
        {execution ? <Button type="primary" disabled={!onOpen} onClick={() => onOpen?.(execution)}>打开当前任务</Button>
          : canDebug ? <Button type="primary" disabled={disabled || !onDebug} onClick={onDebug}>开始调试</Button>
            : <Button type="primary" disabled={disabled || !onStart}
              onClick={() => void onStart?.(agent)}>{completed ? '更新智能体实现' : '开始开发智能体'}</Button>}
        {!execution && canDebug && <Button type="text" disabled={disabled || !onStart}
          onClick={() => void onStart?.(agent)}>更新实现</Button>}
      </div>
    </header>
    {execution && <Alert className="agent-development-execution" showIcon type={execution.status === 'failed' ? 'error' : 'info'}
      message={`当前任务${labels[execution.status] || execution.status}`}
      description={<div><p>{execution.error?.message || '请打开当前任务，继续确认、重试或结束。'}</p>
        {completed && <p className="agent-development-execution-note">开发产物已登记完成；当前任务仍需单独处理。</p>}</div>} />}
    {(changed || unavailable) && agent.development?.progress?.changeReason
      && <p className="agent-development-change">{agent.development.progress.changeReason}</p>}
    <section className="agent-development-checklist" aria-label="开发事项">
      <div className="agent-development-modules-heading"><h4>开发事项</h4>
        <span>{readyCount}/{items.length} 项已完成</span></div>
      {items.length > 0 && <div className="agent-development-track" role="progressbar" aria-label="开发事项完成进度"
        aria-valuemin={0} aria-valuemax={items.length} aria-valuenow={readyCount}>
        <span style={{ width: `${readyCount / items.length * 100}%` }} /></div>}
      {items.length ? <ul className="agent-development-items">{items.map((item) => <li key={item.module}>
        <div><strong>{item.title || item.label}</strong><p>{item.description}</p></div>
        <span className="agent-development-module-status" data-status={item.status}>{labels[item.status] || item.status}</span>
      </li>)}</ul> : <div className="agent-development-empty"><strong>尚无开发事项记录</strong>
        <p>开始开发后，在这里查看实现进展。</p></div>}
    </section>
    <section className="agent-development-capabilities" aria-label="业务能力">
      <div className="agent-development-modules-heading"><h4>业务能力</h4><span>{bindings.length} 个已配置工具</span></div>
      <p className="agent-development-caption">{agent.purpose || '尚未提供用途说明'}</p>
      {bindings.length ? <ul className="agent-development-tools">{bindings.map((binding) => {
        const endpoint = apiContracts.find((api) => api.id === binding.apiContractId)?.endpoints.find((item) => item.id === binding.endpointId)
        return <li key={binding.toolId}><strong>{endpoint?.summary || binding.toolId}</strong>
          <span>已配置业务接口 · 接入效果请在调试中验证</span></li>
      })}</ul> : <p className="agent-development-caption">当前智能体未配置业务工具。</p>}
    </section>
    <div className="agent-development-validation"><div><strong>运行与效果验证</strong>
      <p>进入调试，查看服务状态并验证实际业务效果。</p></div>
      {!canDebug && <Button disabled={disabled || !onDebug || Boolean(execution)} onClick={onDebug}>查看调试</Button>}
    </div>
    <details className="agent-development-implementation"><summary>实现详情<span>模块记录与源码文件</span></summary>
    <section className="agent-development-modules" aria-label="模块实现记录">
      <div className="agent-development-modules-heading"><h4>模块实现</h4>
        {modules.length > 0 && <span>{modules.length} 项模块记录（含未启用项）</span>}</div>
      {modules.length ? <table className="agent-development-table">
        <thead><tr><th scope="col">模块</th><th scope="col">状态</th><th scope="col">实现文件与说明</th></tr></thead>
        <tbody>{modules.map((item) => <tr key={item.module}>
          <th scope="row">{item.label}</th>
          <td><span className="agent-development-module-status" data-status={
            ['tools', 'skills', 'knowledge'].includes(item.module)
              && agent.agentSettings[item.module as keyof RuntimeAgent['agentSettings']].enabled !== true ? 'disabled' : item.status}>{
            ['tools', 'skills', 'knowledge'].includes(item.module)
              && agent.agentSettings[item.module as keyof RuntimeAgent['agentSettings']].enabled !== true
              ? '未启用' : labels[item.status] || item.status}</span></td>
          <td>{item.files.length > 0 && <ul>{item.files.map((path) => <li className="agent-development-file" key={path}>
            <code>{path}</code><Button type="link" size="small" disabled={!onOpenFile}
              aria-label={`查看文件 ${path}`} onClick={() => onOpenFile?.(path)}>查看文件</Button>
          </li>)}</ul>}
            {item.reason && <p>{item.reason}</p>}
            {!item.files.length && !item.reason && <span className="agent-development-no-files">暂无实现文件记录</span>}
          </td>
        </tr>)}</tbody>
      </table> : <div className="agent-development-empty"><strong>尚未生成开发任务</strong><p>开始开发后，可在这里查看各模块的实现状态与文件。</p></div>}
    </section>
    </details>
  </section>
}
