import { BulbOutlined, EditOutlined, RightOutlined } from '@ant-design/icons'
import { Button } from 'antd'
import type { ReactElement } from 'react'
import './MappingModePicker.less'

type Props = {
  aiActive: boolean
  manualActive: boolean
  aiDisabled: boolean
  manualDisabled: boolean
  needsStaticData: boolean
  onAi: () => void
  onManual: () => void
}

/** 在数据源下方提供 AI 生成与手动配置两个入口。 */
export default function MappingModePicker({ aiActive, manualActive, aiDisabled, manualDisabled, needsStaticData, onAi, onManual }: Props): ReactElement {
  return <section className="binding-section mapping-mode-picker" aria-label="配置映射">
    <div className="database-section-heading"><div className="database-section-title is-card"><h4>配置映射</h4></div></div>
    <div className="mapping-mode-options">
      <Button className="mapping-mode-option" aria-pressed={aiActive} disabled={aiDisabled} onClick={onAi}>
        <span className="mapping-mode-icon"><BulbOutlined aria-hidden="true" /></span>
        <span className="mapping-mode-copy">
          <strong>AI 生成</strong>
          <span>{needsStaticData ? '请先应用静态数据，或在数据内容中生成 Mock 数据。' : '根据接口契约和数据来源生成映射，预览后采纳。'}</span>
        </span>
        <RightOutlined className="mapping-mode-arrow" aria-hidden="true" />
      </Button>
      <Button className="mapping-mode-option" aria-pressed={manualActive} disabled={manualDisabled} onClick={onManual}>
        <span className="mapping-mode-icon"><EditOutlined aria-hidden="true" /></span>
        <span className="mapping-mode-copy">
          <strong>手动配置</strong>
          <span>自行设置字段映射和取值规则。</span>
        </span>
        <RightOutlined className="mapping-mode-arrow" aria-hidden="true" />
      </Button>
    </div>
  </section>
}
