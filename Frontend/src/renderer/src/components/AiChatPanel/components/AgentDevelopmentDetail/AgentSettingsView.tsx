import { InfoCircleOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Tag,
  Tooltip,
  Typography,
  message
} from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useState } from 'react'
import type {
  ApplicationLifecycle,
  DevelopmentPlanningAgentOption,
  WorkbenchExecution,
  WorkbenchExecutionStatus
} from '../../../../typings'
import {
  abandonAgentSettingsRevision,
  confirmAgentSettingsRevision,
  getAgentSettingsRevision,
  prepareAgentSettingsRevision,
  type AgentPromptSettingsInput,
  type AgentSettingsRevisionState,
  type AgentSettingsRevisionPreview
} from '../../../../service/agentSettingsRevision'
import { cx } from '../../../../utils'
import AgentSettingsSummary from './AgentSettingsSummary'

const { Text, Paragraph } = Typography
const { TextArea } = Input

type Props = {
  agent: DevelopmentPlanningAgentOption
  applicationLifecycle?: ApplicationLifecycle
  onEndAgentExecution?: (execution: WorkbenchExecution) => Promise<boolean>
  onOpenAgentExecution?: (execution: WorkbenchExecution) => Promise<void>
  onApplied: () => void
  workspaceRoot?: string
}

type AgentSettingsFormValue = {
  role: string
  tone: string
  systemPrompt: string
  constraintsText: string
  temperature: number
}

/** 把未知配置值收敛为普通对象。 */
function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/** 从正式 Agent Settings 构造可编辑表单初值。 */
function formValueFromAgent(agent: DevelopmentPlanningAgentOption): AgentSettingsFormValue {
  const prompt = record(agent.agentSettings.prompt)
  const persona = record(prompt.persona)
  const model = record(agent.agentSettings.model)
  const generation = record(model.generation)
  return {
    role: String(persona.role || ''),
    tone: String(persona.tone || ''),
    systemPrompt: String(prompt.systemPrompt || ''),
    constraintsText: Array.isArray(prompt.constraints)
      ? prompt.constraints.map((item) => String(item || '').trim()).filter(Boolean).join('\n')
      : '',
    temperature: Number(generation.temperature ?? 0.2)
  }
}

/** 把表单值规范化为严格 Prompt Patch。 */
function promptFromForm(value: AgentSettingsFormValue): AgentPromptSettingsInput {
  return {
    persona: { role: value.role.trim(), tone: value.tone.trim() },
    systemPrompt: value.systemPrompt.trim(),
    constraints: value.constraintsText
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean)
  }
}

/** 比较两个表单字段值是否保持相同。 */
function sameValue(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

/** 按 Agent 资源锁定位真实占用 execution，避免用页面布尔值猜测任务状态。 */
function agentBlockingExecution(
  lifecycle: ApplicationLifecycle | undefined,
  agentId: string
): WorkbenchExecution | undefined {
  const runId = lifecycle?.resourceLocks?.agents?.[agentId]?.runId
  return runId ? lifecycle?.activeExecutions?.[runId] : undefined
}

/** 把工作台 execution 状态转换为面向用户的简短中文。 */
function executionStatusLabel(status: WorkbenchExecutionStatus): string {
  return {
    running: '正在运行',
    stopping: '正在停止',
    awaiting_user: '等待用户确认',
    failed: '执行失败，尚未结束',
    stopped: '已停止，尚未结束',
    completed: '已完成，等待释放'
  }[status]
}

/** 管理 Agent Settings 的可视化阅读、编辑、预览和正式确认。 */
export default function AgentSettingsView({
  agent,
  applicationLifecycle,
  onEndAgentExecution,
  onOpenAgentExecution,
  onApplied,
  workspaceRoot
}: Props): ReactElement {
  const [form] = Form.useForm<AgentSettingsFormValue>()
  const [editing, setEditing] = useState(false)
  const [loading, setLoading] = useState(false)
  const [restoring, setRestoring] = useState(false)
  const [endingExecution, setEndingExecution] = useState(false)
  const [endedExecutionRunId, setEndedExecutionRunId] = useState<string>()
  const [preview, setPreview] = useState<AgentSettingsRevisionPreview>()
  const [revisionState, setRevisionState] = useState<AgentSettingsRevisionState>()
  const lifecycleExecution = agentBlockingExecution(applicationLifecycle, agent.agentId)
  const blockingExecution =
    lifecycleExecution?.runId === endedExecutionRunId ? undefined : lifecycleExecution
  const executionRunning = ['running', 'stopping'].includes(blockingExecution?.status || '')

  useEffect(() => {
    setEditing(false)
    setPreview(undefined)
    setRevisionState(undefined)
    form.setFieldsValue(formValueFromAgent(agent))
    if (!workspaceRoot) return
    let cancelled = false
    setRestoring(true)
    void getAgentSettingsRevision(workspaceRoot, agent.agentId)
      .then((result) => {
        if (cancelled) return
        setRevisionState(result)
        setPreview(result.preview)
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setRestoring(false)
      })
    return () => {
      cancelled = true
    }
  }, [agent.agentId, agent.contractHash, agent.technicalPlanSha256, form, workspaceRoot])

  /** 进入编辑态并重新载入当前正式值。 */
  const handleEdit = (): void => {
    form.setFieldsValue(formValueFromAgent(agent))
    setEditing(true)
  }

  /** 取消尚未提交到服务端的本地表单修改。 */
  const handleCancelLocalEdit = (): void => {
    form.setFieldsValue(formValueFromAgent(agent))
    setEditing(false)
  }

  /** 校验表单、计算实际变化段并请求服务端生成正式修改预览。 */
  const handlePrepare = async (): Promise<void> => {
    if (!workspaceRoot) {
      message.error('缺少工作区路径，无法修改 Agent Settings。')
      return
    }
    if (!revisionState) {
      message.error('尚未读取到当前正式配置，请稍后重试。')
      return
    }
    if (blockingExecution) {
      message.warning('请先处理当前智能体的开发任务，再生成配置修改预览。')
      return
    }
    try {
      const value = await form.validateFields()
      const original = formValueFromAgent(agent)
      const prompt = promptFromForm(value)
      const originalPrompt = promptFromForm(original)
      const changedSections: Array<'prompt' | 'model'> = []
      const settingsPatch: {
        prompt?: AgentPromptSettingsInput
        model?: { generation: { temperature: number } }
      } = {}
      if (!sameValue(prompt, originalPrompt)) {
        changedSections.push('prompt')
        settingsPatch.prompt = prompt
      }
      if (Number(value.temperature) !== Number(original.temperature)) {
        changedSections.push('model')
        settingsPatch.model = { generation: { temperature: Number(value.temperature) } }
      }
      if (!changedSections.length) {
        message.info('Agent Settings 没有变化。')
        return
      }
      setLoading(true)
      const result = await prepareAgentSettingsRevision({
        action: 'prepare_agent_settings_revision',
        workspaceRoot,
        agentId: agent.agentId,
        basedOnContractHash: revisionState.contractHash,
        basedOnTechnicalPlanSha256: revisionState.technicalPlanSha256,
        changedSections,
        settingsPatch
      })
      setPreview(result)
    } catch (error) {
      if (error && typeof error === 'object' && 'errorFields' in error) return
      message.error(error instanceof Error ? error.message : 'Agent Settings 修改预览生成失败。')
    } finally {
      setLoading(false)
    }
  }

  /** 经用户二次确认后结束非运行中的 Agent 开发任务，并保留当前表单输入。 */
  const handleEndExecution = (): void => {
    if (!blockingExecution || !onEndAgentExecution || executionRunning) return
    Modal.confirm({
      title: '结束当前智能体开发任务？',
      content:
        '结束后会释放当前智能体的开发锁；尚未完成的 DAG 或等待确认状态将被放弃，当前 Agent Settings 表单会保留。',
      okText: '结束任务并修改配置',
      okButtonProps: { danger: true },
      cancelText: '暂不结束',
      onOk: async () => {
        setEndingExecution(true)
        try {
          const ended = await onEndAgentExecution(blockingExecution)
          if (!ended) throw new Error('当前任务未能结束，请打开原开发任务后重试。')
          setEndedExecutionRunId(blockingExecution.runId)
          message.success('当前智能体开发任务已结束，可以生成配置修改预览。')
        } catch (error) {
          message.error(error instanceof Error ? error.message : '结束当前开发任务失败。')
          throw error
        } finally {
          setEndingExecution(false)
        }
      }
    })
  }

  /** 确认应用服务端候选，并刷新当前工作台 Agent 投影。 */
  const handleConfirm = async (): Promise<void> => {
    if (!workspaceRoot || !preview) return
    setLoading(true)
    try {
      await confirmAgentSettingsRevision({
        workspaceRoot,
        changeId: preview.changeId,
        agentId: preview.agentId,
        basedOnLifecycleRevision: preview.basedOnLifecycleRevision,
        draftSha256: preview.draftSha256
      })
      setPreview(undefined)
      setEditing(false)
      message.success('Agent Settings 已应用，请重新开发当前智能体。')
      onApplied()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Agent Settings 应用失败。')
    } finally {
      setLoading(false)
    }
  }

  /** 放弃服务端修改预览；按调用意图决定是否继续保留本地编辑态。 */
  const handleAbandon = async (continueEditing: boolean): Promise<void> => {
    if (!workspaceRoot || !preview) return
    setLoading(true)
    try {
      await abandonAgentSettingsRevision({
        workspaceRoot,
        changeId: preview.changeId,
        agentId: preview.agentId,
        basedOnLifecycleRevision: preview.basedOnLifecycleRevision,
        draftSha256: preview.draftSha256
      })
      setPreview(undefined)
      setEditing(continueEditing)
      if (!continueEditing) form.setFieldsValue(formValueFromAgent(agent))
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Agent Settings 修改放弃失败。')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className={cx('agent-detail-section')}>
      <div className={cx('agent-detail-section-title')}>
        <div className={cx('agent-detail-section-heading-main')}>
          <h3>Agent Settings</h3>
          <Tooltip title="配置来源：已确认的 TechnicalPlan；确认修改后会回写正式技术规划。">
            <button
              aria-label="查看 Agent Settings 来源"
              className={cx('agent-detail-info-trigger')}
              type="button"
            >
              <InfoCircleOutlined />
            </button>
          </Tooltip>
        </div>
        <Space>
          {editing ? <Tag>正在编辑正式配置</Tag> : null}
          {!editing ? (
            <Button
              disabled={
                restoring || !workspaceRoot || Boolean(preview) || Boolean(blockingExecution)
              }
              loading={restoring}
              onClick={handleEdit}
              size="small"
            >
              编辑配置
            </Button>
          ) : null}
        </Space>
      </div>
      {blockingExecution ? (
        <Alert
          action={
            executionRunning ? (
              <Button
                disabled={!onOpenAgentExecution}
                onClick={() => void onOpenAgentExecution?.(blockingExecution)}
                size="small"
              >
                打开当前任务
              </Button>
            ) : (
              <Button
                disabled={!onEndAgentExecution}
                loading={endingExecution}
                onClick={handleEndExecution}
                size="small"
              >
                结束任务并修改配置
              </Button>
            )
          }
          className={cx('agent-settings-execution-alert')}
          description={`阶段：${blockingExecution.phase || '未知'}。配置预览会占用正式修订锁，请先安全结束这项任务。`}
          message={`当前智能体开发任务${executionStatusLabel(blockingExecution.status)}`}
          showIcon
          type="warning"
        />
      ) : null}
      {editing ? (
        <Form className={cx('agent-settings-form')} form={form} layout="vertical">
          <Alert
            message="只修改 Prompt 与 Temperature；平台安全规则和其他配置保持只读。"
            showIcon
            type="info"
          />
          <div className={cx('agent-settings-form-grid')}>
            <Form.Item label="角色" name="role" rules={[{ required: true, whitespace: true }]}>
              <Input maxLength={2000} />
            </Form.Item>
            <Form.Item label="表达风格" name="tone" rules={[{ required: true, whitespace: true }]}>
              <Input maxLength={2000} />
            </Form.Item>
          </div>
          <Form.Item
            label="System Prompt"
            name="systemPrompt"
            rules={[{ required: true, whitespace: true }]}
          >
            <TextArea autoSize={{ minRows: 6, maxRows: 16 }} maxLength={100000} showCount />
          </Form.Item>
          <Form.Item extra="每行一条业务约束；平台安全规则会单独锁定注入。" label="业务约束" name="constraintsText">
            <TextArea autoSize={{ minRows: 3, maxRows: 8 }} placeholder="每行一条约束" />
          </Form.Item>
          <div className={cx('agent-settings-form-grid')}>
            <Form.Item extra="平台模型列表接入后可选择" label="模型策略">
              <Select
                disabled
                options={[{ label: '跟随项目默认模型', value: 'project_default' }]}
                value="project_default"
              />
            </Form.Item>
            <Form.Item
              label="Temperature"
              name="temperature"
              rules={[{ required: true, type: 'number', min: 0, max: 2 }]}
            >
              <InputNumber max={2} min={0} precision={2} step={0.1} />
            </Form.Item>
          </div>
          <Space>
            <Button loading={loading} onClick={() => void handlePrepare()} type="primary">
              生成修改预览
            </Button>
            <Button disabled={loading} onClick={handleCancelLocalEdit}>取消</Button>
          </Space>
        </Form>
      ) : (
        <AgentSettingsSummary agentSettings={agent.agentSettings} />
      )}

      <Modal
        cancelButtonProps={{ disabled: loading }}
        cancelText="放弃修改"
        closable={false}
        confirmLoading={loading}
        keyboard={false}
        maskClosable={false}
        onCancel={() => void handleAbandon(false)}
        onOk={() => void handleConfirm()}
        okText="确认并应用"
        open={Boolean(preview)}
        title="确认 Agent Settings 修改"
        width={760}
        footer={
          <Space>
            <Button disabled={loading} onClick={() => void handleAbandon(false)}>放弃修改</Button>
            <Button disabled={loading} onClick={() => void handleAbandon(true)}>继续修改</Button>
            <Button loading={loading} onClick={() => void handleConfirm()} type="primary">
              确认并应用
            </Button>
          </Space>
        }
      >
        {preview ? (
          <div className={cx('agent-settings-preview')}>
            <Alert
              message="确认后会更新正式 TechnicalPlan，并使当前智能体的旧 Build 证据失效。"
              showIcon
              type="warning"
            />
            <div className={cx('agent-settings-diff-list')}>
              {preview.fieldDiffs.map((diff) => (
                <article key={diff.field}>
                  <strong>{diff.label}</strong>
                  <div>
                    <Text type="secondary">修改前</Text>
                    <Paragraph>{diff.before}</Paragraph>
                  </div>
                  <div>
                    <Text type="secondary">修改后</Text>
                    <Paragraph>{diff.after}</Paragraph>
                  </div>
                </article>
              ))}
            </div>
            <div className={cx('agent-setting-tags')}>
              <Tag color="orange">当前 Agent 需要重新 Build</Tag>
              <Tag>无关 Agent 不受影响</Tag>
              <Tag>页面与 Endpoint 设计不变</Tag>
            </div>
          </div>
        ) : null}
      </Modal>
    </section>
  )
}
