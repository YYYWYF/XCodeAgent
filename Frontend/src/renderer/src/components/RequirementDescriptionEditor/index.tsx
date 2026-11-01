import { Button, Input, Modal, Popover, Tooltip } from 'antd'
import { FullscreenOutlined, InfoCircleOutlined } from '@ant-design/icons'
import { useState, type ReactElement } from 'react'
import {
  APPLICATION_REQUIREMENTS_PLACEHOLDER,
  FULL_REQUIREMENTS_TEMPLATE,
  PAGE_REQUIREMENTS_TEMPLATE
} from '../../constants/applicationRequirements'
import { cx } from '../../utils'
import './index.less'

export type RequirementDescriptionEditorProps = {
  value?: string
  onChange?: (value: string) => void
  disabled?: boolean
  readOnly?: boolean
  placeholder?: string
  rows?: number
  id?: string
}

/** 追加文本时保留已有内容和换行，仅补足段落之间的空行。 */
function appendSection(value: string, section: string): string {
  if (!value) return section
  return `${value}${value.endsWith('\n\n') ? '' : value.endsWith('\n') ? '\n' : '\n\n'}${section}`
}

/** 展示完整填写指引，打开后仍保留输入中的需求内容。 */
function WritingGuide(): ReactElement {
  return <pre className={cx('requirement-editor-guide')}>{APPLICATION_REQUIREMENTS_PLACEHOLDER}</pre>
}

/** 将悬浮提示挂载到触发按钮所在区域，继承编辑器的明暗主题变量。 */
function popupContainer(trigger: HTMLElement): HTMLElement {
  return trigger.parentElement ?? trigger
}

/** 提供独立受控的需求编辑、模板插入、页面追加及可取消的放大编辑。 */
export default function RequirementDescriptionEditor({
  value = '', onChange, disabled = false, readOnly = false,
  placeholder = APPLICATION_REQUIREMENTS_PLACEHOLDER, rows = 18, id
}: RequirementDescriptionEditorProps): ReactElement {
  const [expandedDraft, setExpandedDraft] = useState<string | null>(null)
  const [insertTarget, setInsertTarget] = useState<'main' | 'expanded' | null>(null)
  const editable = !disabled && !readOnly

  /** 主输入只通过父层受控值更新，放大弹窗内只维护待确认的临时内容。 */
  function update(nextValue: string, expanded: boolean): void {
    if (!editable) return
    if (expanded) setExpandedDraft(nextValue)
    else onChange?.(nextValue)
  }

  /** 非空需求先打开选择窗口，禁止未经确认覆盖用户内容。 */
  function requestTemplate(expanded: boolean): void {
    if (!editable) return
    const current = expanded ? expandedDraft ?? '' : value
    if (!current.trim()) update(FULL_REQUIREMENTS_TEMPLATE, expanded)
    else setInsertTarget(expanded ? 'expanded' : 'main')
  }

  /** 按用户选择追加或替换模板；取消时不触发任何内容更新。 */
  function insertTemplate(replace: boolean): void {
    if (!insertTarget || !editable) return
    const expanded = insertTarget === 'expanded'
    const current = expanded ? expandedDraft ?? '' : value
    update(replace ? FULL_REQUIREMENTS_TEMPLATE : appendSection(current, FULL_REQUIREMENTS_TEMPLATE), expanded)
    setInsertTarget(null)
  }

  /** 每次只在当前编辑位置末尾追加一个页面，不清理或重排现有需求。 */
  function addPage(expanded: boolean): void {
    update(appendSection(expanded ? expandedDraft ?? '' : value, PAGE_REQUIREMENTS_TEMPLATE), expanded)
  }

  /** 放大编辑只有确认时才回写父层，关闭窗口即丢弃未确认的草稿。 */
  function confirmExpanded(): void {
    if (editable && expandedDraft !== null) onChange?.(expandedDraft)
    setExpandedDraft(null)
    setInsertTarget(null)
  }

  /** 渲染主输入及弹窗共用的模板与页面操作，按钮均不提交外层表单。 */
  function actions(expanded: boolean): ReactElement {
    return (
      <div className={cx('requirement-editor-actions')}>
        <Tooltip title="插入应用整体与页面需求模板；已有内容时可选择追加或替换。" color="var(--req-bg)"
          getPopupContainer={popupContainer} overlayClassName={cx('requirement-editor-tooltip')}>
          <Button htmlType="button" onClick={() => requestTemplate(expanded)}>插入需求模板</Button>
        </Tooltip>
        <Tooltip title="在现有内容末尾追加一个页面描述模板，保留已填写的需求。" color="var(--req-bg)"
          getPopupContainer={popupContainer} overlayClassName={cx('requirement-editor-tooltip')}>
          <Button htmlType="button" onClick={() => addPage(expanded)}>+ 添加页面</Button>
        </Tooltip>
      </div>
    )
  }

  return (
    <div className={cx('requirement-editor', readOnly && 'requirement-editor--readonly')}>
      {editable && <div className={cx('requirement-editor-heading')}>
        <span className={cx('requirement-editor-guide-anchor')}>
          <Popover title="需求填写指引" content={<WritingGuide />} trigger={['hover', 'focus']}
            placement="bottomRight" autoAdjustOverflow={{ adjustX: 1, adjustY: 0 }}
            getPopupContainer={popupContainer} overlayClassName={cx('requirement-editor-popover')}>
            <Button type="link" htmlType="button" icon={<InfoCircleOutlined />}>填写指引</Button>
          </Popover>
        </span>
        <Button type="link" htmlType="button" icon={<FullscreenOutlined />} onClick={() => setExpandedDraft(value)}>放大编辑</Button>
      </div>}
      <Input.TextArea
        id={id} aria-label="应用需求描述" value={value} rows={rows}
        disabled={disabled} readOnly={readOnly} placeholder={readOnly ? undefined : placeholder}
        onChange={(event) => update(event.target.value, false)}
      />
      {editable && actions(false)}
      {editable && <>
        <Modal
          title="放大编辑应用需求描述" open={expandedDraft !== null}
          onCancel={() => { setExpandedDraft(null); setInsertTarget(null) }} onOk={confirmExpanded}
          okText="确认" cancelText="取消" width="min(1200px, calc(100vw - 32px))"
          getContainer={false} zIndex={1100} destroyOnClose maskClosable={false}
          transitionName="" maskTransitionName="" style={{ top: 24 }}
          wrapClassName={cx('requirement-editor-overlay', 'requirement-editor-expanded')}
        >
          <div className={cx('requirement-editor-columns')}>
            <div className={cx('requirement-editor-draft')}>
              <Input.TextArea aria-label="放大编辑需求内容" value={expandedDraft ?? ''}
                onChange={(event) => update(event.target.value, true)} placeholder={placeholder} />
              {actions(true)}
            </div>
            <aside aria-label="需求填写指引"><WritingGuide /></aside>
          </div>
        </Modal>
        <Modal
          title="插入需求模板" open={insertTarget !== null}
          onCancel={() => setInsertTarget(null)} getContainer={false} zIndex={1200}
          transitionName="" maskTransitionName="" wrapClassName={cx('requirement-editor-overlay')}
          footer={[
            <Button key="cancel" htmlType="button" onClick={() => setInsertTarget(null)}>取消</Button>,
            <Button key="replace" htmlType="button" onClick={() => insertTemplate(true)}>替换内容</Button>,
            <Button key="append" type="primary" htmlType="button" onClick={() => insertTemplate(false)}>追加模板</Button>
          ]}
        >已有需求内容，请选择追加模板或替换内容。替换会覆盖当前编辑内容。</Modal>
      </>}
    </div>
  )
}
