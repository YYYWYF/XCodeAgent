import { ApiOutlined } from '@ant-design/icons'
import { Button, TreeSelect } from 'antd'
import type { ReactElement } from 'react'
import type { BindingDraft } from '../../../AppApis/model'
import { missingRequiredFeeders } from '../../../AppApis/model'
import DatabaseMapping from './DatabaseMapping'
import ExternalMappingCards from './ExternalMapping'
import { cx } from '../../../../utils'
import type { FieldMappingApiItem, FieldMappingContext, MappingSourceTreeNode } from './types'
import './index.less'

export type { FieldMappingApiItem, FieldMappingContext, MappingFieldOption, MappingSourceTreeNode } from './types'

type Props = {
  /** 应用API目录：以应用的全部接口为导航主体。 */
  apis: FieldMappingApiItem[]
  /** 当前绑定工作流的对象：编辑器只承载它，其余条目展示只读说明。 */
  activeApiId: string
  /** 目录当前选中的条目：由面板层持有，「打开字段映射」用它定位到具体API。 */
  selectedApiId: string
  onSelectApi: (id: string) => void
  /** 当前对象的契约上下文与来源结构；来源未选定时 sourceName 为空（呈现来源选择卡）。 */
  context: FieldMappingContext | null
  draft: BindingDraft | null
  submitting?: boolean
  /** 只读态：已确认绑定的常驻查看，控件禁用且不显示保存/确认动作。 */
  readOnly?: boolean
  /** 确认按钮文案：工作流内为「保存并确认」，侧面板直连为「确认绑定」。 */
  confirmLabel?: string
  /** 直连绑定的三级来源树（类型 → 连接/域 → 表/接口）：来源未选定时内容区呈现选择卡。 */
  sourceTree?: MappingSourceTreeNode[]
  /** 直连选定来源：由面板层落定绑定并重建草稿。 */
  onSelectSource?: (targetKey: string) => void
  onChange: (draft: BindingDraft) => void
  /** 保存当前草稿：写回应用API状态但不触发下一步。 */
  onSave: (draft: BindingDraft) => void
  /** 保存并确认：提交当前草稿，工作流进入下一步。 */
  onConfirm: (draft: BindingDraft) => void
}

/**
 * 字段映射面板：应用API映射绑定的编辑工作台。左侧以应用的全部API为目录
 * （状态点标注已确认/绑定中/未开始），右侧是当前对象的映射编辑区——按来源类型分发：
 * 数据库为 DatabaseMapping 模板编辑器，外部API为 ExternalMapping 双列连线画布，
 * 来源未选定时先呈现三级来源选择卡。支持两条路径：工作流绑定卡打开（确认推进工作流），
 * 或右侧面板直连发起（确认后直接定稿，不依赖工作流节点）。
 */
export default function FieldMappingPanel({
  apis,
  activeApiId,
  selectedApiId,
  onSelectApi,
  context,
  draft,
  submitting = false,
  readOnly = false,
  confirmLabel,
  sourceTree = [],
  onSelectSource,
  onChange,
  onSave,
  onConfirm
}: Props): ReactElement {
  const selected = apis.find((item) => item.id === selectedApiId)
  // 有上下文与草稿即可渲染（活动绑定为可编辑，已确认绑定为只读）；activeApiId 仅用于行高亮。
  const editing = Boolean(selected && context && draft)

  return (
    <section aria-label="字段映射" className={cx('field-mapping-panel')} role="group">
      <aside aria-label="应用API目录" className={cx('field-mapping-directory')}>
        <div className={cx('field-mapping-directory-body')}>
          <div className={cx('field-mapping-directory-title')}>应用API</div>
          <div className={cx('field-mapping-directory-list')}>
            {apis.map((item) => (
              <button
                key={item.id}
                aria-label={item.name}
                className={cx(
                  'field-mapping-api-row',
                  selectedApiId === item.id && 'selected',
                  item.id === activeApiId && 'active'
                )}
                onClick={() => onSelectApi(item.id)}
                type="button"
              >
                <ApiOutlined aria-hidden="true" />
                <span className={cx('field-mapping-api-name')}>{item.name}</span>
                {/* 绑定进度点：重开对话后据此快速定位还有绑定未完成的API。 */}
                <em
                  aria-hidden="true"
                  className={cx(
                    'field-mapping-api-state',
                    item.state === 'confirmed' && 'is-confirmed',
                    item.state === 'binding' && 'is-binding'
                  )}
                  title={
                    item.state === 'confirmed'
                      ? '已确认'
                      : item.state === 'binding'
                        ? '绑定进行中'
                        : '未开始'
                  }
                />
              </button>
            ))}
          </div>
        </div>
      </aside>
      <main className={cx('field-mapping-content')}>
        {!selected || !editing || !context || !draft ? (
          <div className={cx('field-mapping-empty-state')}>
            <strong>{selected ? selected.name : '字段映射'}</strong>
            <span>
              {readOnly
                ? '该应用API尚未开始数据绑定；完成映射绑定后，这里会常驻展示其映射配置。'
                : '该应用API当前没有进行中的映射绑定；已确认的绑定与接口调试视图可在「开发产物」查看。'}
            </span>
          </div>
        ) : (
          <FieldMappingEditor
            confirmLabel={confirmLabel}
            context={context}
            draft={draft}
            // 画布与控件只在提交瞬间锁定；已确认绑定为「可调整」态（保存更新配置）。
            locked={submitting}
            readOnly={readOnly}
            sourceTree={sourceTree}
            submitting={submitting}
            onSelectSource={onSelectSource}
            onChange={onChange}
            onSave={onSave}
            onConfirm={onConfirm}
          />
        )}
      </main>
    </section>
  )
}

/** 编辑区入参：当前对象的契约上下文 + 共享草稿；locked（提交中）时控件禁用。 */
type EditorProps = {
  context: FieldMappingContext
  draft: BindingDraft
  locked: boolean
  /** 已确认绑定：可继续调整并保存，但不出现「保存并确认」（无待推进的工作流）。 */
  readOnly: boolean
  /** 确认按钮文案：工作流内「保存并确认」，侧面板直连「确认绑定」。 */
  confirmLabel?: string
  /** 直连绑定的三级来源树：来源未选定时呈现选择卡。 */
  sourceTree: MappingSourceTreeNode[]
  onSelectSource?: (targetKey: string) => void
  submitting: boolean
  onChange: (draft: BindingDraft) => void
  onSave: (draft: BindingDraft) => void
  onConfirm: (draft: BindingDraft) => void
}

/** 映射编辑区：契约头（动作按钮跟随标题行右侧）+ 来源选择卡 / 按类型分发的映射编辑器。 */
function FieldMappingEditor({
  context,
  draft,
  locked,
  readOnly,
  confirmLabel,
  sourceTree,
  onSelectSource,
  submitting,
  onChange,
  onSave,
  onConfirm
}: EditorProps): ReactElement {
  const isExternal = context.kind === '外部服务'
  // 直连绑定的起点：来源尚未选定时没有可映射目标，先呈现来源选择卡。
  const needsSource = !context.sourceName
  /** 更新草稿并上抛：草稿正本由面板层持有，切目录/Tab 不丢。 */
  const patchDraft = (patch: Partial<BindingDraft>): void => {
    onChange({ ...draft, ...patch })
  }
  // 外部必填入参未连接的名单：连接登记是唯一事实，命中即拦截「保存并确认」，
  // 避免外部调用缺参数的隐患随确认进入应用。
  const missingFeeders = isExternal ? missingRequiredFeeders(context.requestParams, draft) : []

  return (
    <div className={cx('field-mapping-editor')}>
      <header className={cx('field-mapping-head')}>
        <div className={cx('field-mapping-head-row')}>
          <code className={cx('field-mapping-method')}>{context.appMethod}</code>
          <strong>{context.appPath}</strong>
          <span className={cx('field-mapping-head-name')}>{context.objectName}</span>
          {/* 保存跟随主标题行右侧（已确认绑定为「保存更新」），与接口调试视图的发送按钮同位；
              「保存并确认」只在有待推进工作流时出现，且被外部必填门禁拦截；
              来源未选定（直连起点）时没有可保存的映射，动作区整体隐藏。 */}
          {!locked && !needsSource && (
            <div className={cx('field-mapping-head-actions')}>
              <Button onClick={() => onSave(draft)}>{readOnly ? '保存更新' : '保存'}</Button>
              {!readOnly && (
                <Button
                  disabled={missingFeeders.length > 0}
                  loading={submitting}
                  onClick={() => onConfirm(draft)}
                  title={
                    missingFeeders.length > 0
                      ? `「${missingFeeders.join('、')}」为外部接口必填入参，尚未连接取值来源`
                      : undefined
                  }
                  type="primary"
                >
                  {confirmLabel || '保存并确认'}
                </Button>
              )}
            </div>
          )}
        </div>
        {/* 数据来源行：主标题始终是应用API，外部API/数据表作为来源标记放在次行。 */}
        <p className={cx('field-mapping-head-source')}>
          <span className={cx('field-mapping-head-source-label')}>数据来源</span>
          <em className={cx('field-mapping-head-role', 'ext')}>{isExternal ? '外部API' : '数据表'}</em>
          <span className={cx('field-mapping-head-source-text')}>
            {context.sourceName
              ? `${context.sourceName}${context.targetName ? ` · ${context.targetName}` : ''}`
              : '尚未选定 · 在下方选择数据来源后开始映射'}
          </span>
        </p>
      </header>
      {needsSource ? (
        // 直连绑定起点：先选定数据来源——三级树（类型 → 连接/域 → 表/接口）与目录抽屉、
        // 工作流「选类型 → 选来源」同一套层级，来源多时逐级展开或搜索定位。
        <section className={cx('field-mapping-card', 'source-pick')}>
          <div className={cx('field-mapping-card-title')}>选择数据来源</div>
          <TreeSelect
            aria-label="选择数据来源"
            className={cx('field-mapping-source-select')}
            disabled={locked}
            dropdownMatchSelectWidth={false}
            placeholder="搜索或逐级选择数据表 / 外部接口"
            showSearch
            treeNodeFilterProp="filterTitle"
            treeData={sourceTree}
            treeDefaultExpandAll
            onChange={(key) => onSelectSource?.(String(key))}
          />
          <p className={cx('field-mapping-source-hint')}>
            按「类型 → 连接/域 → 数据表/接口」逐级选择；选定后自动生成模板槽位与初步字段映射，草稿自动保存。
          </p>
        </section>
      ) : isExternal ? (
        // 外部服务：双列连线映射画布（入参适配 / 出参适配 / 请求预览）。
        <ExternalMappingCards confirmed={readOnly} context={context} draft={draft} locked={locked} patch={patchDraft} />
      ) : (
        // 数据库：模板槽位编辑器（条件/写入/返回字段/SQL 预览）。
        <DatabaseMapping context={context} draft={draft} locked={locked} onChange={patchDraft} />
      )}
    </div>
  )
}
