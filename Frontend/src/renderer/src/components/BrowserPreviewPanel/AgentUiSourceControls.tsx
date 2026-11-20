import { Alert, Button, Select, Space, Typography } from 'antd'
import { useState } from 'react'
import type { ChangeEvent, ReactElement } from 'react'
import type { AgentUiSourceSelection, AgentUiPreviewSource } from '../../service/previewRuntime'

/** 开发抽屉显式切换来源，JSONL 仅在内存中等候提交，不进入目标业务页面。 */
export default function AgentUiSourceControls({
  busy,
  onSwitch,
  initialSource = 'official-mock'
}: {
  busy: boolean
  onSwitch: (selection: AgentUiSourceSelection) => Promise<void>
  initialSource?: AgentUiPreviewSource
}): ReactElement {
  const [source, setSource] = useState<AgentUiPreviewSource>(initialSource)
  const [recording, setRecording] = useState('')
  const [error, setError] = useState('')
  const [reading, setReading] = useState(false)

  /** 导入有界文件，不展示或保存原始录制正文，实际 Schema 校验在服务端公开解析器完成。 */
  const readRecording = async (event: ChangeEvent<HTMLInputElement>): Promise<void> => {
    const file = event.target.files?.[0]
    event.target.value = ''
    setRecording('')
    setError('')
    if (!file) return
    if (file.size > 1024 * 1024) {
      setError('JSONL 文件不能超过 1 MiB。')
      return
    }
    setReading(true)
    try {
      setRecording(await file.text())
    } catch {
      setError('无法读取录制文件，请重新选择。')
    } finally {
      setReading(false)
    }
  }

  /** 仅点击时提交来源；完成或失败后清空原始录制，断线不自动重放切换动作。 */
  const apply = async (): Promise<void> => {
    try {
      await onSwitch({ source, ...(source === 'replay' ? { recording } : {}) })
    } finally {
      setRecording('')
    }
  }

  return (
    <section aria-label="Agent UI 开发来源">
      <Typography.Title level={5}>开发预览来源</Typography.Title>
      <Space wrap>
        <Select<AgentUiPreviewSource>
          aria-label="选择预览来源"
          value={source}
          disabled={busy || reading}
          onChange={(value) => {
            setSource(value)
            setRecording('')
            setError('')
          }}
          options={[
            { value: 'official-mock', label: '官方 Mock' },
            { value: 'replay', label: '脱敏录制回放' },
            { value: 'real', label: '真实 Python' }
          ]}
        />
        <Button
          disabled={busy || reading || (source === 'replay' && !recording)}
          loading={busy}
          onClick={() => void apply()}
        >
          停止旧预览并切换
        </Button>
      </Space>
      {source === 'replay' && (
        <p>
          <label>
            选择 JSONL（最多 1 MiB）
            <input
              type="file"
              accept=".jsonl"
              disabled={busy || reading}
              onChange={(event) => void readRecording(event)}
            />
          </label>
          {recording && ' 已读取，待脱敏校验'}
        </p>
      )}
      {error && <Alert type="error" showIcon message={error} />}
      <Typography.Paragraph type="secondary">
        切换会停止旧服务并开启新会话，旧问题和待办不会转入。
        回放隐藏正文、工具参数和结果，仅复现显示流程，不执行模型或业务工具。
      </Typography.Paragraph>
    </section>
  )
}
