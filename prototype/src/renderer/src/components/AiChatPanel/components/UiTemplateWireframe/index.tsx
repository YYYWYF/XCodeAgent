import { cx } from '../../../../utils'
import { getAvailableTemplates } from '../../../../service/templateService'
import './UiTemplateWireframe.less'

/** 模板名 → 线框类型 ID：优先精确匹配模板清单，再按关键词兜底，未识别一律按列表版式画。 */
// eslint-disable-next-line react-refresh/only-export-components
export function templateIdForName(name: string): string {
  const templates = getAvailableTemplates()
  const matched = templates.find((template) => template.manifest.name === name)
  if (matched) return matched.manifest.id
  if (name.includes('表单')) return 'multiForm'
  if (name.includes('标签')) return 'tabsTable'
  return 'commonTable'
}

/**
 * UI 设计稿模板的低保真线框：与开发阶段同一套模板目录，但只画版式骨架、损失全部视觉细节，
 * 表达“设计稿阶段只定结构，不定像素”的保真度差异（骨架样式复用模板选择器的全局类）。
 * 对话卡的选模板翻页与右侧设计稿预览共用本组件，保证“所选即所见”。
 */
export default function UiTemplateWireframe({ templateId }: { templateId: string }): JSX.Element {
  const isForm = templateId === 'multiForm'
  const isTabs = templateId === 'tabsTable'
  return (
    <div className={cx('workflow-ui-template-wireframe')}>
      <div className={cx('template-preview-topbar')}>
        <span className={cx('template-preview-brand')} />
        <span className={cx('template-preview-topbar-line', 'short')} />
        <span className={cx('template-preview-topbar-line')} />
      </div>
      {isForm ? (
        <div className={cx('template-preview-form')}>
          <div className={cx('template-preview-heading', 'wide')} />
          <div className={cx('template-preview-form-grid')}>
            {Array.from({ length: 6 }).map((_, index) => (
              <span className={cx('template-preview-input')} key={index} />
            ))}
          </div>
          <span className={cx('template-preview-submit')} />
        </div>
      ) : (
        <div className={cx('template-preview-table')}>
          <div className={cx('template-preview-heading')} />
          {isTabs && (
            <div className={cx('template-preview-tabs')}>
              <span className={cx('active')} />
              <span />
              <span />
            </div>
          )}
          <div className={cx('template-preview-filters')}>
            <span />
            <span />
            <b />
          </div>
          <div className={cx('template-preview-table-head')} />
          {Array.from({ length: 4 }).map((_, index) => (
            <div className={cx('template-preview-table-row')} key={index}>
              <span />
              <span />
              <span />
              <span />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
