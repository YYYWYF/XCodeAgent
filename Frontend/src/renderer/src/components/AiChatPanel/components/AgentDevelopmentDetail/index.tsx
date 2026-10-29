import { MODULES, FIELDS, settingText } from './agentSettingsDisplay'
import {
  Alert,
  Button,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Modal,
  Space,
  Tabs,
  Tag,
  Typography
} from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type { BusinessAgentOption } from '../../../../../../shared/businessAgent'
import {
  abandonAgentSettingsRevision,
  confirmAgentSettingsRevision,
  getAgentSettingsRevision,
  prepareAgentSettingsRevision,
  type AgentSettingsRevisionPreview,
  type AgentSettingsRevisionState
} from '../../../../service/agentSettingsRevision'
import { cx } from '../../../../utils'
import './AgentDevelopmentDetail.less'

type Props = {
  agent: BusinessAgentOption
  workspaceRoot: string
  disabled: boolean
  onStart: () => void
  onRefresh: () => void
}
type Values = {
  role: string
  tone: string
  systemPrompt: string
  constraints: string
  temperature?: number
}
/** 从当前已确认配置建立表单，缺失可选的人设字段保持空白。 */
function formValues(agent: BusinessAgentOption): Values {
  const prompt = agent.agentSettings.prompt || {}
  const persona = prompt.persona as Record<string, unknown> | undefined
  const generation = agent.agentSettings.model?.generation as Record<string, unknown> | undefined
  return {
    role: String(persona?.role || ''),
    tone: String(persona?.tone || ''),
    systemPrompt: String(prompt.systemPrompt || ''),
    constraints: Array.isArray(prompt.constraints) ? prompt.constraints.join('\n') : '',
    temperature: typeof generation?.temperature === 'number' ? generation.temperature : undefined
  }
}

/** 展示智能体、依赖和七模块配置，并沿正式修订草稿完成编辑、预览、确认或放弃。 */
export default function AgentDevelopmentDetail({
  agent,
  workspaceRoot,
  disabled,
  onStart,
  onRefresh
}: Props): ReactElement {
  const [form] = Form.useForm<Values>()
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [reloadRevision, setReloadRevision] = useState(0)
  const [identity, setIdentity] = useState<AgentSettingsRevisionState>()
  const [preview, setPreview] = useState<AgentSettingsRevisionPreview>()
  useEffect(() => {
    let active = true
    setIdentity(undefined)
    setPreview(undefined)
    setEditing(false)
    setError('')
    getAgentSettingsRevision(workspaceRoot, agent.agentId)
      .then((state) => {
        if (active) {
          setIdentity(state)
          setPreview(state.preview)
        }
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : '读取配置失败')
      })
    return () => {
      active = false
    }
  }, [workspaceRoot, agent.agentId, reloadRevision])

  /** 创建待确认草稿，正式合同在用户确认之前保持原值。 */
  const prepare = async (): Promise<void> => {
    if (!identity || busy) return
    try {
      const values = await form.validateFields()
      setBusy(true)
      setError('')
      const current = formValues(agent)
      const promptChanged = ['role', 'tone', 'systemPrompt', 'constraints'].some(
        (key) => values[key as keyof Values] !== current[key as keyof Values]
      )
      const modelChanged =
        values.temperature !== current.temperature && values.temperature !== undefined
      const result = await prepareAgentSettingsRevision({
        action: 'prepare_agent_settings_revision',
        workspaceRoot,
        agentId: agent.agentId,
        basedOnContractHash: identity.contractHash,
        basedOnTechnicalPlanSha256: identity.technicalPlanSha256,
        changedSections: [
          ...(promptChanged ? ['prompt' as const] : []),
          ...(modelChanged ? ['model' as const] : [])
        ],
        settingsPatch: {
          ...(promptChanged
            ? {
                prompt: {
                  persona: { role: values.role, tone: values.tone },
                  systemPrompt: values.systemPrompt,
                  constraints: values.constraints
                    .split('\n')
                    .map((item) => item.trim())
                    .filter(Boolean)
                }
              }
            : {}),
          ...(modelChanged ? { model: { generation: { temperature: values.temperature! } } } : {})
        }
      })
      setPreview(result)
      setEditing(false)
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '请检查配置输入')
    } finally {
      setBusy(false)
    }
  }

  /** 对当前预览执行明确的确认或放弃，并重新读取服务端身份以防迟到更新。 */
  const finish = async (confirm: boolean): Promise<void> => {
    if (!preview || busy) return
    setBusy(true)
    setError('')
    try {
      const input = {
        workspaceRoot,
        agentId: agent.agentId,
        changeId: preview.changeId,
        basedOnLifecycleRevision: preview.basedOnLifecycleRevision,
        draftSha256: preview.draftSha256
      }
      if (confirm) await confirmAgentSettingsRevision(input)
      else await abandonAgentSettingsRevision(input)
      const state = await getAgentSettingsRevision(workspaceRoot, agent.agentId)
      setIdentity(state)
      setPreview(state.preview)
      onRefresh()
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '配置操作失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={cx('agent-development-detail')}>
      <Space>
        <Typography.Title level={4}>{agent.label}</Typography.Title>
        <Tag color="purple">业务智能体</Tag>
      </Space>
      <Typography.Paragraph>{agent.purpose}</Typography.Paragraph>
      {error ? (
        <Alert
          type="error"
          showIcon
          message={error}
          action={
            <Button
              size="small"
              disabled={busy}
              onClick={() => setReloadRevision((value) => value + 1)}
            >
              重新读取配置
            </Button>
          }
        />
      ) : null}
      <Space>
        <Button type="primary" disabled={disabled || busy || Boolean(preview)} onClick={onStart}>
          开发智能体
        </Button>
        <Button
          disabled={disabled || busy || !identity || Boolean(preview)}
          onClick={() => {
            form.setFieldsValue(formValues(agent))
            setEditing(true)
          }}
        >
          编辑配置
        </Button>
      </Space>
      <Tabs
        items={[
          {
            key: 'overview',
            label: '能力与依赖',
            children: (
              <Descriptions bordered column={1}>
                <Descriptions.Item label="业务能力">
                  {agent.capabilities.join('；') || '未声明'}
                </Descriptions.Item>
                <Descriptions.Item label="业务边界">
                  {agent.boundaries.join('；') || '未声明额外边界'}
                </Descriptions.Item>
                <Descriptions.Item label="入口页面">{agent.pageIds.join('、')}</Descriptions.Item>
                <Descriptions.Item label="工具接口">
                  {agent.tools
                    .map((tool) => `${tool.toolId} → ${tool.apiContractId}/${tool.endpointId}`)
                    .join('；') || '无'}
                </Descriptions.Item>
                <Descriptions.Item label="运行方式">
                  当前 Agent Runtime；工具复用业务服务，页面通过同源代理调用。
                </Descriptions.Item>
              </Descriptions>
            )
          },
          ...Object.entries(MODULES).map(([key, label]) => ({
            key,
            label,
            children: (
              <Descriptions bordered column={1}>
                {Object.entries(agent.agentSettings[key] || {}).map(([field, value]) => (
                  <Descriptions.Item key={field} label={FIELDS[field] || field}>
                    {settingText(value)}
                  </Descriptions.Item>
                ))}
              </Descriptions>
            )
          }))
        ]}
      />
      <Modal
        wrapClassName={cx('agent-settings-modal')}
        title="编辑智能体配置"
        open={editing}
        confirmLoading={busy}
        onCancel={() => {
          if (!busy) setEditing(false)
        }}
        onOk={() => void prepare()}
        okText="生成修改预览"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="role" label="角色（可选）">
            <Input />
          </Form.Item>
          <Form.Item name="tone" label="表达风格（可选）">
            <Input />
          </Form.Item>
          <Form.Item
            name="systemPrompt"
            label="系统提示词"
            rules={[{ required: true, whitespace: true }]}
          >
            <Input.TextArea rows={6} />
          </Form.Item>
          <Form.Item name="constraints" label="业务约束（每行一条）">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="temperature" label="Temperature">
            <InputNumber min={0} max={2} step={0.1} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        wrapClassName={cx('agent-settings-modal')}
        title="确认智能体配置修改"
        open={Boolean(preview)}
        closable={false}
        footer={
          <Space>
            <Button disabled={busy || disabled} onClick={() => void finish(false)}>
              放弃修改
            </Button>
            <Button
              type="primary"
              loading={busy}
              disabled={disabled}
              onClick={() => void finish(true)}
            >
              确认并应用
            </Button>
          </Space>
        }
      >
        <Alert type="info" message="确认后需要重新开发该智能体；修改预览尚未生效。" />
        {preview?.fieldDiffs.map((diff) => (
          <section key={diff.field}>
            <Typography.Text strong>{diff.label}</Typography.Text>
            <p>原值：{diff.before}</p>
            <p>新值：{diff.after}</p>
          </section>
        ))}
      </Modal>
    </div>
  )
}
