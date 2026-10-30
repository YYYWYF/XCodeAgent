import { Alert, Button, Modal, message, Space, Tag } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { requestRuntimeSettings } from '../../../../service/runtimeWorkbench'
import type { AgentSettingsPatch, EditableAgentSettings, RuntimeAgent, RuntimeSettingsResult, SettingsPreview } from '../../../../service/runtimeWorkbench'
import AgentSettingsFields, { normalizedSettings } from './AgentSettingsFields'
import AgentSettingsSummary from './AgentSettingsSummary'

/** 将字段差异转成可读文本，不直接展示内部 JSON。 */
function diffText(value: unknown, field: string): string {
  if (value === null || value === undefined) return field === 'temperature' ? '跟随项目默认' : '未设置'
  if (Array.isArray(value)) return value.length ? value.join('\n') : '未设置'
  return String(value)
}

/** 沿原分支切换只读与编辑态，并在独立弹窗内确认差异，不重复渲染两份配置。 */
export default function AgentPromptEditor({ agent, workspaceRoot, onSaved }: {
  agent: RuntimeAgent; workspaceRoot: string; onSaved: (result: RuntimeSettingsResult) => void
}): ReactElement {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState<EditableAgentSettings>({ prompt: { systemPrompt: '' }, temperature: null })
  const [baseline, setBaseline] = useState<EditableAgentSettings>(value)
  const [identity, setIdentity] = useState<{ contractHash: string; technicalPlanSha256: string }>()
  const [preview, setPreview] = useState<SettingsPreview>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const active = useRef(true)
  const submitting = useRef(false)

  /** 恢复服务端当前预览，页面重开后仍可以确认或放弃同一份草稿。 */
  async function reload(): Promise<void> {
    if (submitting.current) return
    submitting.current = true
    setBusy(true)
    setError('')
    try {
      const result = await requestRuntimeSettings(workspaceRoot, { action: 'get_settings', agentId: agent.agentId })
      if (!result.contractHash || !result.technicalPlanSha256 || !result.editableSettings) throw new Error('未读取到当前正式配置身份。')
      if (!active.current) return
      setIdentity({ contractHash: result.contractHash, technicalPlanSha256: result.technicalPlanSha256 })
      setPreview(result.preview || undefined)
      setBaseline(result.editableSettings)
      setValue(result.preview?.after || result.editableSettings)
      setEditing(Boolean(result.preview))
    } catch (cause) {
      if (active.current) setError(cause instanceof Error ? cause.message : '配置读取失败。')
    } finally {
      submitting.current = false
      if (active.current) setBusy(false)
    }
  }

  useEffect(() => {
    active.current = true
    void reload()
    return () => { active.current = false }
  }, [workspaceRoot, agent.agentId, agent.contractHash])

  /** 提交配置生成字段差异预览，或确认／放弃服务端签发的唯一草稿。 */
  async function submit(action: 'preview_settings' | 'confirm_settings' | 'cancel_settings', continueEditing = false): Promise<void> {
    if (!identity || submitting.current) return
    submitting.current = true
    setBusy(true)
    setError('')
    try {
      if (action !== 'preview_settings' && !preview) throw new Error('当前没有可提交的修改预览。')
      const normalized = normalizedSettings(value)
      if (normalized.prompt.persona && (!normalized.prompt.persona.role || !normalized.prompt.persona.tone)) throw new Error('请同时填写角色和表达风格，或将两项全部清空。')
      const settingsPatch: AgentSettingsPatch = {}
      if (JSON.stringify(normalized.prompt) !== JSON.stringify(normalizedSettings(baseline).prompt)) settingsPatch.prompt = normalized.prompt
      if (normalized.temperature !== baseline.temperature) settingsPatch.model = {
        generation: normalized.temperature === null ? null : { temperature: normalized.temperature } }
      const result = await requestRuntimeSettings(workspaceRoot, action === 'preview_settings'
        ? { action, agentId: agent.agentId, ...identity, settingsPatch }
        : { action, agentId: agent.agentId, changeId: preview!.changeId,
            lifecycleRevision: preview!.lifecycleRevision, draftSha256: preview!.draftSha256 })
      if (!active.current) return
      if (action === 'preview_settings') {
        if (!result.preview) throw new Error('服务端未返回完整修改预览。')
        setPreview(result.preview)
      } else if (action === 'confirm_settings') {
        if (!result.saved || !result.agent || !result.lifecycle) throw new Error('未收到完整保存结果，请重新加载配置核对。')
        setPreview(undefined)
        setEditing(false)
        message.success('配置已保存，请通过关联页面更新当前智能体后在预览中验证。')
        onSaved(result)
      } else {
        if (!result.cancelled) throw new Error('未收到完整放弃结果，请重新加载配置核对。')
        setValue(continueEditing ? preview!.after : baseline)
        setPreview(undefined)
        setEditing(continueEditing)
      }
    } catch (cause) {
      if (active.current) setError(cause instanceof Error ? cause.message : '配置操作失败。')
    } finally {
      submitting.current = false
      if (active.current) setBusy(false)
    }
  }

  const errorAlert = error ? <Alert showIcon type="error" message={error}
    action={<Button size="small" disabled={busy} onClick={() => void reload()}>重新加载</Button>} /> : null

  return <section className="agent-detail-section agent-settings-view" aria-busy={busy}>
    <div className="agent-detail-section-title">
      <h3>Agent Settings</h3>
      {editing ? <Tag>正在编辑正式配置</Tag>
        : <Button size="small" disabled={busy || !identity} loading={busy} onClick={() => setEditing(true)}>编辑配置</Button>}
    </div>
    {!preview && errorAlert}
    {editing ? <div className="agent-prompt-editor">
      <Alert showIcon type="info" message="可修改 Prompt 与 Temperature，其他模块保持只读。" />
      <AgentSettingsFields value={value} disabled={busy || Boolean(preview)} onChange={setValue} />
      <Space wrap><Button type="primary" loading={busy} disabled={busy || Boolean(preview) || !value.prompt.systemPrompt.trim()
        || JSON.stringify(normalizedSettings(value)) === JSON.stringify(normalizedSettings(baseline))}
        onClick={() => void submit('preview_settings')}>生成修改预览</Button>
        <Button disabled={busy || Boolean(preview)} onClick={() => { setValue(baseline); setError(''); setEditing(false) }}>取消</Button></Space>
    </div> : <AgentSettingsSummary agent={agent} />}
    <Modal className="agent-settings-modal" getContainer={false} open={Boolean(preview)} title="确认 Agent Settings 修改"
      width={760} closable={false} keyboard={false} maskClosable={false}
      footer={<Space wrap>
        <Button disabled={busy} onClick={() => void submit('cancel_settings')}>放弃修改</Button>
        <Button disabled={busy} onClick={() => void submit('cancel_settings', true)}>继续修改</Button>
        <Button type="primary" loading={busy} disabled={busy} onClick={() => void submit('confirm_settings')}>确认并应用</Button>
      </Space>}>
      {preview && <div className="agent-settings-preview">
        {errorAlert}
        <Alert showIcon type="info" message="确认后更新正式技术规划。请通过关联页面更新 Agent 实现，再从预览中验证。" />
        {preview.fieldDiffs.map((diff) => <section key={diff.field}><h4>{diff.label}</h4><div className="agent-settings-diff-grid">
          <article><h4>修改前</h4><p>{diffText(diff.before, diff.field)}</p></article>
          <article><h4>修改后</h4><p>{diffText(diff.after, diff.field)}</p></article>
        </div></section>)}
      </div>}
    </Modal>
  </section>
}
