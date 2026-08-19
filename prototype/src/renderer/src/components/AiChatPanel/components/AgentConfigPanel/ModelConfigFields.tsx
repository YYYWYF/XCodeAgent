import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import ModelGenerationSettings from './ModelGenerationSettings'
import ModelOutputSettings from './ModelOutputSettings'
import type { AgentConfigModelSettings } from './types'

type ModelConfigFieldsProps = {
  readOnly?: boolean
  settings: AgentConfigModelSettings
  onChange: (settings: AgentConfigModelSettings) => void
}

/** 组合模型展开区的生成效果和输出参数，保持面板组件只负责布局。 */
export default function ModelConfigFields({
  readOnly = false,
  settings,
  onChange
}: ModelConfigFieldsProps): ReactElement {
  return (
    <div className={cx('agent-config-model-fields')}>
      <ModelGenerationSettings onChange={onChange} readOnly={readOnly} settings={settings} />
      <ModelOutputSettings onChange={onChange} readOnly={readOnly} settings={settings} />
    </div>
  )
}
