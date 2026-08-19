import { QuestionCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { Button, InputNumber, Slider, Switch, Tooltip, Typography } from 'antd'
import { useState, type ReactElement } from 'react'
import { cx } from '../../../../utils'
import type { AgentConfigModelSettings } from './types'

const { Text } = Typography

type Props = {
  readOnly?: boolean
  settings: AgentConfigModelSettings
  onChange: (settings: AgentConfigModelSettings) => void
}

type PresetKey = 'custom' | 'creative' | 'balanced' | 'precise'

const PRESETS: Record<PresetKey, { label: string; temperature: number; topP: number }> = {
  custom: { label: '自定义', temperature: 0.7, topP: 0.5 },
  creative: { label: '创意', temperature: 0.9, topP: 0.8 },
  balanced: { label: '平衡', temperature: 0.7, topP: 0.5 },
  precise: { label: '精确', temperature: 0.3, topP: 0.2 }
}

type RangeSettingRowProps = {
  readOnly?: boolean
  help: string
  label: string
  max: number
  min: number
  step: number
  value: number
  onChange: (value: number) => void
}

/** 渲染一个带滑杆和数值输入的模型效果参数。 */
function RangeSettingRow({
  help,
  label,
  readOnly = false,
  max,
  min,
  step,
  value,
  onChange
}: RangeSettingRowProps): ReactElement {
  return (
    <div className={cx('agent-config-range-row')}>
      <span className={cx('agent-config-field-label')}>
        {label}
        <Tooltip title={help}>
          <QuestionCircleOutlined aria-label={`${label}说明`} />
        </Tooltip>
      </span>
      <Slider
        className={cx('agent-config-range-slider')}
        max={max}
        min={min}
        onChange={(nextValue) => {
          if (typeof nextValue === 'number') onChange(nextValue)
        }}
        step={step}
        disabled={readOnly}
        value={value}
      />
      <InputNumber
        aria-label={label}
        className={cx('agent-config-number-input')}
        disabled={readOnly}
        max={max}
        min={min}
        onChange={(nextValue) => {
          if (typeof nextValue === 'number') onChange(nextValue)
        }}
        step={step}
        value={value}
      />
    </div>
  )
}

/** 渲染深度思考开关和生成效果参数。 */
export default function ModelGenerationSettings({
  readOnly = false,
  settings,
  onChange
}: Props): ReactElement {
  const [selectedPreset, setSelectedPreset] = useState<PresetKey>('custom')

  /** 应用图片中的效果预设，并保留其他模型参数不变。 */
  const applyPreset = (preset: PresetKey): void => {
    const nextPreset = PRESETS[preset]
    setSelectedPreset(preset)
    onChange({
      ...settings,
      temperature: nextPreset.temperature,
      topP: nextPreset.topP
    })
  }

  /** 恢复生成效果参数的自定义默认值。 */
  const resetGenerationSettings = (): void => {
    setSelectedPreset('custom')
    onChange({
      ...settings,
      temperature: PRESETS.custom.temperature,
      topP: PRESETS.custom.topP,
      frequencyPenalty: 0,
      presencePenalty: 0
    })
  }

  /** 修改任意生成效果参数，并将预设切换为自定义。 */
  const updateRangeSetting = (update: Partial<AgentConfigModelSettings>): void => {
    setSelectedPreset('custom')
    onChange({ ...settings, ...update })
  }

  return (
    <>
      <section className={cx('agent-config-subsection')}>
        <header className={cx('agent-config-subsection-header')}>
          <Text strong>深度思考</Text>
          <Tooltip title="开启后允许模型使用更长的推理过程">
            <QuestionCircleOutlined aria-label="深度思考说明" />
          </Tooltip>
        </header>
        <div className={cx('agent-config-switch-row')}>
          <Switch
            aria-label="深度思考"
            checked={settings.deepThinking}
            disabled={readOnly}
            onChange={(deepThinking) => onChange({ ...settings, deepThinking })}
            size="small"
          />
          <Text type="secondary">深度思考</Text>
          <Tooltip title="控制模型是否使用深度思考能力">
            <QuestionCircleOutlined aria-label="深度思考开关说明" />
          </Tooltip>
        </div>
      </section>

      <section className={cx('agent-config-subsection')}>
        <header className={cx('agent-config-subsection-header')}>
          <Text strong>生成效果设置</Text>
          <Button
            className={cx('agent-config-reset-button')}
            disabled={readOnly}
            icon={<ReloadOutlined />}
            onClick={resetGenerationSettings}
            type="link"
          >
            重置
          </Button>
        </header>
        <div className={cx('agent-config-presets')} role="group" aria-label="生成效果预设">
          {(Object.keys(PRESETS) as PresetKey[]).map((preset) => (
            <button
              className={cx('agent-config-preset', selectedPreset === preset && 'active')}
              disabled={readOnly}
              key={preset}
              onClick={() => applyPreset(preset)}
              type="button"
            >
              {PRESETS[preset].label}
              {preset !== 'custom' ? (
                <Tooltip title={`${PRESETS[preset].label}预设`}>
                  <QuestionCircleOutlined aria-label={`${PRESETS[preset].label}预设说明`} />
                </Tooltip>
              ) : null}
            </button>
          ))}
        </div>
        <RangeSettingRow
          help="控制回答的随机性，数值越高越发散。"
          label="回答多样性(temperature)"
          max={1}
          min={0}
          onChange={(temperature) => updateRangeSetting({ temperature })}
          readOnly={readOnly}
          step={0.01}
          value={settings.temperature}
        />
        <RangeSettingRow
          help="控制采样候选范围，数值越高可选词越多。"
          label="采样范围(topP)"
          max={1}
          min={0}
          onChange={(topP) => updateRangeSetting({ topP })}
          readOnly={readOnly}
          step={0.01}
          value={settings.topP}
        />
        <RangeSettingRow
          help="降低同一词汇在回答中重复出现的概率。"
          label="词汇重复惩罚(frequencyPenalty)"
          max={2}
          min={-2}
          onChange={(frequencyPenalty) => updateRangeSetting({ frequencyPenalty })}
          readOnly={readOnly}
          step={0.1}
          value={settings.frequencyPenalty}
        />
        <RangeSettingRow
          help="降低已经出现过的主题再次出现的概率。"
          label="主题重复惩罚(presencePenalty)"
          max={2}
          min={-2}
          onChange={(presencePenalty) => updateRangeSetting({ presencePenalty })}
          readOnly={readOnly}
          step={0.1}
          value={settings.presencePenalty}
        />
      </section>
    </>
  )
}
