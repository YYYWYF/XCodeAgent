import { FileTextOutlined, PlusOutlined, QuestionCircleOutlined } from '@ant-design/icons'
import { Button, Input, InputNumber, Tooltip, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useState } from 'react'
import { cx } from '../../../../utils'
import type { AgentConfigModelSettings } from './types'

const { Text } = Typography

type Props = {
  readOnly?: boolean
  settings: AgentConfigModelSettings
  onChange: (settings: AgentConfigModelSettings) => void
}

/** 渲染输出 Token、上下文提示和其他自定义参数。 */
export default function ModelOutputSettings({
  readOnly = false,
  settings,
  onChange
}: Props): ReactElement {
  const [parameterDraft, setParameterDraft] = useState('')
  const [parameterEditing, setParameterEditing] = useState(false)

  /** 把用户输入的其他模型参数加入当前配置草稿。 */
  const addParameter = (): void => {
    const parameter = parameterDraft.trim()
    if (!parameter) return
    onChange({
      ...settings,
      otherParameters: [...settings.otherParameters, parameter]
    })
    setParameterDraft('')
    setParameterEditing(false)
  }

  return (
    <>
      <section className={cx('agent-config-subsection')}>
        <header className={cx('agent-config-subsection-header')}>
          <Text strong>输出设置</Text>
        </header>
        <div className={cx('agent-config-output-row')}>
          <span className={cx('agent-config-field-label')}>
            输出Token(maxToken)
            <Tooltip title="限制单次回复可生成的最大 Token 数">
              <QuestionCircleOutlined aria-label="输出 Token 说明" />
            </Tooltip>
          </span>
          <InputNumber
            aria-label="输出Token(maxToken)"
            className={cx('agent-config-number-input')}
            disabled={readOnly}
            max={32768}
            min={1}
            onChange={(maxTokens) => {
              if (typeof maxTokens === 'number') onChange({ ...settings, maxTokens })
            }}
            step={100}
            value={settings.maxTokens}
          />
        </div>
        <Text className={cx('agent-config-output-hint')} type="secondary">
          最大上下文长度（输入+输出）：196607&nbsp;&nbsp;&nbsp;最大输出Token数：32768
        </Text>
      </section>

      <section className={cx('agent-config-subsection')}>
        <header className={cx('agent-config-subsection-header')}>
          <Text strong>其他参数</Text>
          <Button
            className={cx('agent-config-add-parameter')}
            disabled={readOnly}
            icon={<PlusOutlined />}
            onClick={() => setParameterEditing(true)}
            type="link"
          >
            添加
          </Button>
        </header>
        {settings.otherParameters.length > 0 ? (
          <div className={cx('agent-config-parameter-list')}>
            {settings.otherParameters.map((parameter) => (
              <Text className={cx('agent-config-parameter')} key={parameter}>
                {parameter}
              </Text>
            ))}
          </div>
        ) : (
          <Text className={cx('agent-config-empty-hint')} type="secondary">
            暂无其他参数
          </Text>
        )}
        {parameterEditing ? (
          <div className={cx('agent-config-parameter-editor')}>
            <Input
              aria-label="其他参数名称"
              disabled={readOnly}
              onChange={(event) => setParameterDraft(event.target.value)}
              onPressEnter={addParameter}
              placeholder="输入参数名称"
              value={parameterDraft}
            />
            <Button disabled={readOnly} onClick={addParameter} size="small" type="primary">
              添加
            </Button>
            <Button onClick={() => setParameterEditing(false)} size="small">
              取消
            </Button>
          </div>
        ) : null}
      </section>

      <div className={cx('agent-config-doc-link-row')}>
        <Typography.Link
          href="#agent-model-config-documentation"
          onClick={(event) => event.preventDefault()}
        >
          <FileTextOutlined /> 模型配置接口文档
        </Typography.Link>
      </div>
    </>
  )
}
