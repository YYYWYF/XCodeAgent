import { Alert, Button, Space, Spin } from 'antd'
import { BulbOutlined } from '@ant-design/icons'
import type { ReactElement } from 'react'
import type { RuleAssistTarget } from '../../../../typings/endpointDesignAssist'
import { useRuleAssist } from './useRuleAssist'
import './MappingAssist.less'

/** 在现有规则弹窗中完善说明，采用候选只更新编辑框。 */
export default function RuleAssistSection({ target, dependencies, description, disabled, onAdopt }: {
  target?: RuleAssistTarget; dependencies: string[]; description: string; disabled: boolean; onAdopt: (description: string) => void
}): ReactElement | null {
  const { available, running, error, candidate, generate, cancel, adopt } = useRuleAssist({ target, dependencies, description, disabled, onAdopt })
  if (!available) return null
  return <div className="rule-assist-section">
    <Space><Button type="link" icon={<BulbOutlined />} disabled={disabled || running || !description.trim()} onClick={() => void generate()}>AI 完善规则</Button>
      {running && <><Spin size="small" /><Button type="text" onClick={cancel}>取消</Button></>}
    </Space>
    {error && <Alert type="error" message={error} showIcon />}
    {candidate && <div className="rule-assist-candidate"><strong>建议规则</strong><span className="mapping-assist-secondary">输入字段与缺值策略保持原值</span>
      {candidate.description && <><p>{candidate.description}</p><Button disabled={disabled} onClick={adopt}>采用到编辑框</Button></>}
      {candidate.questions.length > 0 && <Alert type="warning" message="需要你明确" description={<ul>{candidate.questions.map((text, index) => <li key={index}>{text}</li>)}</ul>} />}
    </div>}
  </div>
}
