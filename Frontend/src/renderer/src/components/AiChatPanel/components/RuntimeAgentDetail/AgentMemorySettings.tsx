import { Radio, Switch } from 'antd'
import type { ReactElement } from 'react'
import type { AgentMemorySettings as MemorySettings } from '../../../../service/runtimeWorkbench'
import './AgentMemorySettings.less'

/** 缺少能力选项的原配置继续沿用模板，只为界面读取默认值。 */
export function normalizedMemory(value?: Record<string, unknown> | MemorySettings): MemorySettings {
  const shortTerm = (value?.shortTerm || {}) as Partial<MemorySettings['shortTerm']>
  const longTerm = (value?.longTerm || {}) as Partial<MemorySettings['longTerm']>
  return {
    shortTerm: { enabled: shortTerm.enabled ?? true, storage: shortTerm.storage || 'template' },
    longTerm: { enabled: longTerm.enabled ?? false }
  }
}

/** 用开关及存储单选展示记忆能力，连接信息由生成代码从环境变量读取。 */
export default function AgentMemorySettings({ value, disabled = false, onChange }: {
  value: MemorySettings; disabled?: boolean; onChange?: (value: MemorySettings) => void
}): ReactElement {
  const locked = disabled || !onChange
  return <div className="agent-memory-settings">
    <section>
      <div className="agent-memory-heading"><div><h4>短期记忆</h4><p>保存当前会话的对话和执行进度</p></div>
        <div className="agent-memory-toggle"><span>{value.shortTerm.enabled ? '已启用' : '未启用'}</span>
          <Switch aria-label="启用短期记忆" checked={value.shortTerm.enabled} disabled={locked}
            onChange={(enabled) => onChange?.({ ...value, shortTerm: { ...value.shortTerm, enabled } })} /></div>
      </div>
      {value.shortTerm.enabled && <div className="agent-memory-storage"><span>存储方式</span>
        <div><Radio.Group aria-label="短期记忆存储方式" value={value.shortTerm.storage} disabled={locked}
            onChange={(event) => onChange?.({ ...value, shortTerm: { ...value.shortTerm, storage: event.target.value } })}>
            <Radio value="object_storage">对象存储</Radio><Radio value="tdsql">TDSQL</Radio>
          </Radio.Group>{value.shortTerm.storage === 'template' && <p>当前沿用模板，选择后应用对应存储方式。</p>}</div>
      </div>}
    </section>
    <section>
      <div className="agent-memory-heading"><div><h4>长期记忆</h4><p>接入云服务，跨会话保留用户偏好和重要信息</p></div>
        <div className="agent-memory-toggle"><span>{value.longTerm.enabled ? '已启用' : '未启用'}</span>
          <Switch aria-label="启用长期记忆" checked={value.longTerm.enabled} disabled={locked}
            onChange={(enabled) => onChange?.({ ...value, longTerm: { ...value.longTerm, enabled } })} /></div>
      </div>
    </section>
    <p className="agent-memory-hint">连接参数统一读取环境变量，按所选能力生成 Agent 配置与装配代码。</p>
  </div>
}
