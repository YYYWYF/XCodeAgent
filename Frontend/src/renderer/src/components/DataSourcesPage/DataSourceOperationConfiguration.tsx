import { ApiOutlined, DownOutlined, FileTextOutlined, LinkOutlined, PlusOutlined, SearchOutlined, SettingOutlined, UpOutlined } from '@ant-design/icons'
import { Button, Checkbox, Popover } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { useState } from 'react'
import type { DataSourceOperationSection } from '../../typings'
import { cx } from '../../utils'
import { sectionMeta } from './dataSourceOperationConfig'

type SectionOption = { key: DataSourceOperationSection; icon: ReactNode; title: string; description: string }

const SECTION_OPTIONS: SectionOption[] = [
  { key: 'path', icon: <LinkOutlined />, ...sectionMeta('path') },
  { key: 'query', icon: <SearchOutlined />, ...sectionMeta('query') },
  { key: 'header', icon: <ApiOutlined />, ...sectionMeta('header') },
  { key: 'requestBody', icon: <ApiOutlined />, ...sectionMeta('requestBody') },
  { key: 'responseBody', icon: <FileTextOutlined />, ...sectionMeta('responseBody') },
]

const ADD_SECTION_ORDER: DataSourceOperationSection[] = ['query', 'path', 'header', 'requestBody', 'responseBody']

/** 渲染新建接口第一步的配置模块选择卡。 */
export function OperationContentPicker({ selected, onChange }: { selected: ReadonlySet<DataSourceOperationSection>; onChange: (sections: Set<DataSourceOperationSection>) => void }): ReactElement {
  /** 切换一项配置并保持集合不可变，避免影响表单脏状态比较。 */
  const toggle = (section: DataSourceOperationSection): void => {
    const next = new Set(selected)
    if (next.has(section)) next.delete(section)
    else next.add(section)
    onChange(next)
  }
  return <section className={cx('data-source-operation-selection')}>
    <header className={cx('data-source-operation-selection-header')}><span className={cx('data-source-operation-selection-icon')}><SettingOutlined /></span><span><strong>配置接口内容</strong><small>请选择需要配置的内容（可多选）</small></span></header>
    <div className={cx('data-source-operation-selection-list')}>
      {SECTION_OPTIONS.map((option) => <label className={cx('data-source-operation-selection-item')} key={option.key}>
        <Checkbox checked={selected.has(option.key)} onChange={() => toggle(option.key)} />
        <span className={cx('data-source-operation-selection-item-icon', `tone-${option.key === 'responseBody' ? 'response' : option.key === 'requestBody' ? 'request' : option.key}`)}>{option.icon}</span>
        <span className={cx('data-source-operation-selection-copy')}><strong>{option.title}</strong><small>{option.description}</small></span>
      </label>)}
    </div>
  </section>
}

/** 渲染编辑态的全局配置栏和批量添加入口。 */
export default function OperationConfigurationBar({ selected, onChange }: { selected: ReadonlySet<DataSourceOperationSection>; onChange: (sections: Set<DataSourceOperationSection>) => void }): ReactElement {
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState<Set<DataSourceOperationSection>>(new Set())
  const available = ADD_SECTION_ORDER.map((key) => SECTION_OPTIONS.find((option) => option.key === key)).filter((option): option is SectionOption => Boolean(option && !selected.has(option.key)))

  /** 根据弹层勾选项一次性追加多个配置模块。 */
  const addSelected = (): void => {
    onChange(new Set([...selected, ...pending]))
    setPending(new Set())
    setOpen(false)
  }

  /** 弹层打开时从空选择开始，关闭时丢弃未确认的临时选择。 */
  const handleVisibleChange = (visible: boolean): void => {
    setOpen(visible)
    if (!visible) setPending(new Set())
  }

  const content = <div className={cx('data-source-operation-add-popover')}>
    <strong>添加接口配置</strong>
    <small>选择需要配置的内容，仅展示当前未配置的项</small>
    <div className={cx('data-source-operation-add-options')}>
      {available.map((option) => <label className={cx('data-source-operation-add-option')} key={option.key}><span className={cx('data-source-operation-add-option-icon', `tone-${option.key === 'responseBody' ? 'response' : option.key === 'requestBody' || option.key === 'header' ? 'request' : 'default'}`)}>{option.icon}</span><span className={cx('data-source-operation-add-option-copy')}><strong>{option.title}</strong><small>{option.description}</small></span><Checkbox checked={pending.has(option.key)} onChange={(event) => {
        const next = new Set(pending)
        if (event.target.checked) next.add(option.key)
        else next.delete(option.key)
        setPending(next)
      }} /></label>)}
    </div>
    <div className={cx('data-source-operation-add-actions')}><Button onClick={() => handleVisibleChange(false)} size="small">取消</Button><Button disabled={!pending.size} onClick={addSelected} size="small" type="primary">添加</Button></div>
  </div>

  return <section className={cx('data-source-operation-configuration-bar')}>
    <span className={cx('data-source-operation-configuration-icon')}><SettingOutlined /></span>
    <span className={cx('data-source-operation-configuration-copy')}><strong>接口内容</strong></span>
    <Popover content={content} getPopupContainer={(trigger) => trigger.parentElement || document.body} placement="bottomRight" trigger="click" visible={open} onVisibleChange={handleVisibleChange}>
      <Button className={cx('data-source-editor-add-button', 'data-source-operation-add-trigger')} disabled={!available.length} size="small"><PlusOutlined /><span>添加配置项</span><span className={cx('data-source-operation-add-trigger-arrow')}>{open ? <UpOutlined /> : <DownOutlined />}</span></Button>
    </Popover>
  </section>
}

/** 暴露配置选项供编辑器和测试保持统一顺序。 */
