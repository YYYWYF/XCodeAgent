import {
  ApartmentOutlined,
  ArrowRightOutlined,
  BranchesOutlined,
  FileTextOutlined
} from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import { useMemo, type ReactElement } from 'react'
import { cx } from '../../../../utils'
import {
  asRecord,
  behaviorTypeLabel,
  mergeDeletedRecords,
  modulePriorityLabel,
  recordItems,
  requirementFlowRows,
  requirementPageRows,
  stringItems,
  textValue,
  type JsonRecord,
  type RequirementPageRow
} from './RequirementDocPanelData'

const { Text } = Typography

export type RequirementSectionKey = 'overview' | 'pages' | 'flows' | 'page-design-overview' | 'authorization'

type SectionProps = {
  sectionKey: RequirementSectionKey
}

/** 渲染应用摘要和功能模块；角色及默认授权统一放在角色与权限章节。 */
export function RequirementOverviewSection({
  spec,
  specDiff,
  sectionKey
}: {
  spec: JsonRecord
  /** 跨迭代增量合并产物，用于给功能模块加变更徽标。 */
  specDiff?: JsonRecord
} & SectionProps): ReactElement {
  const appInfo = asRecord(spec.app_info)
  const modules = mergeDeletedRecords(spec.feature_modules, specDiff?.feature_modules, 'id')
  const moduleStatusMap = useMemo(
    () => buildChangeStatusMap(specDiff, 'feature_modules', 'id'),
    [specDiff]
  )
  return (
    <section
      aria-label="概览"
      className={cx('requirement-doc-section')}
      id={`requirement-doc-panel-${sectionKey}`}
      role="tabpanel"
    >
      <div className={cx('requirement-doc-section-title')}>
        <FileTextOutlined /> <span>应用概览</span>
      </div>
      <article className={cx('requirement-doc-app-card')}>
        <strong>{textValue(appInfo.name, '未命名应用')}</strong>
        {textValue(appInfo.summary) ? (
          <Text type="secondary">{textValue(appInfo.summary)}</Text>
        ) : null}
        <div className={cx('requirement-doc-app-meta')}>
          {textValue(appInfo.target_platform) ? (
            <Tag>{textValue(appInfo.target_platform)}</Tag>
          ) : null}
          {textValue(appInfo.navigation_layout) ? (
            <Tag>{textValue(appInfo.navigation_layout)}</Tag>
          ) : null}
        </div>
      </article>
      <div className={cx('requirement-doc-section-title')}>
        <ApartmentOutlined /> <span>功能模块</span>
      </div>
      {(() => {
        const modulesWithStatus = modules.map((module, index) => {
          const moduleId = textValue(module.id, `module-${index}`)
          return {
            module,
            moduleId,
            changeStatus: resolveChangeStatus(moduleStatusMap.get(moduleId), Boolean(specDiff)),
          }
        })
        const changed = modulesWithStatus.filter((m) => m.changeStatus !== 'unchanged')
        const unchanged = modulesWithStatus.filter((m) => m.changeStatus === 'unchanged')
        const showGroups = changed.length > 0 && unchanged.length > 0
        const renderCard = (m: typeof modulesWithStatus[number], index: number): ReactElement => (
          <article className={cx('requirement-doc-card')} key={m.moduleId}>
            <div className={cx('requirement-doc-card-heading')}>
              <strong>{textValue(m.module.name, `模块 ${index + 1}`)}</strong>
              <em
                className={cx(
                  'requirement-doc-priority',
                  `is-${textValue(m.module.priority).toLowerCase() || 'must'}`
                )}
              >
                {modulePriorityLabel(m.module.priority)}
              </em>
              <ChangeBadge changeStatus={m.changeStatus} />
            </div>
            {textValue(m.module.description) ? (
              <Text type="secondary">{textValue(m.module.description)}</Text>
            ) : null}
          </article>
        )
        if (!modules.length) return <Text type="secondary">暂无功能模块</Text>
        if (!showGroups) return <div className={cx('requirement-doc-card-list')}>{modulesWithStatus.map((m, i) => renderCard(m, i))}</div>
        return (
          <>
            <div className={cx('requirement-doc-change-group', 'is-changed')}>
              <div className={cx('requirement-doc-page-group-title')}>
                <span className={cx('requirement-doc-page-group-badge', 'is-changed')}>
                  本次迭代变更（{changed.length}）
                </span>
              </div>
              <div className={cx('requirement-doc-card-list')}>
                {changed.map((m, i) => renderCard(m, i))}
              </div>
            </div>
            <div className={cx('requirement-doc-change-group', 'is-unchanged')}>
              <div className={cx('requirement-doc-page-group-title')}>
                <span className={cx('requirement-doc-page-group-badge', 'is-unchanged')}>
                  未变更内容（{unchanged.length}）
                </span>
              </div>
              <div className={cx('requirement-doc-card-list')}>
                {unchanged.map((m, i) => renderCard(m, i))}
              </div>
            </div>
          </>
        )
      })()}
    </section>
  )
}

/** 变更标记徽标的视觉映射：added=新增（绿色+）、modified=修改（黄色~）、
 *  unchanged=未变更（灰色）、deleted=删除（红色-）。
 *  deleted 暂不会从后端产生（merger 默认保留），但保留渲染能力兜底。 */
export type ChangeStatus = 'added' | 'modified' | 'unchanged' | 'deleted'

export const CHANGE_STATUS_META: Record<ChangeStatus, { label: string; className: string }> = {
  added: { label: '新增', className: 'is-added' },
  modified: { label: '有变更', className: 'is-modified' },
  unchanged: { label: '未变更', className: 'is-unchanged' },
  deleted: { label: '待删除', className: 'is-deleted' },
}

/** 变更徽标标题文案映射，供各 tab 复用。 */
const CHANGE_STATUS_TITLE: Record<ChangeStatus, string> = {
  added: '本轮迭代新增',
  modified: '本轮迭代有变更（与基线版本不同）',
  unchanged: '本轮迭代未变更，继承自基线',
  deleted: '本轮迭代删除',
}

/** 渲染变更徽标；changeStatus 不传时返回 null（向后兼容）。 */
export function ChangeBadge({ changeStatus }: { changeStatus?: ChangeStatus }): ReactElement | null {
  if (!changeStatus) return null
  const meta = CHANGE_STATUS_META[changeStatus]
  return (
    <span
      className={cx('requirement-doc-change-badge', meta.className)}
      title={CHANGE_STATUS_TITLE[changeStatus]}
    >
      {meta.label}
    </span>
  )
}

/** 从 specDiff 的指定数组字段里读每项的 _change_status，按 idKey 构建 Map。
 *  供概览(feature_modules)/业务流程(business_flows)/角色(user_roles)/页面(pages) 各 tab 复用。 */
export function buildChangeStatusMap(
  specDiff: JsonRecord | undefined,
  field: string,
  idKey: string = 'id',
): Map<string, ChangeStatus> {
  const map = new Map<string, ChangeStatus>()
  const items = recordItems((specDiff as Record<string, unknown> | undefined)?.[field])
  items.forEach((item, index) => {
    const id = textValue(item[idKey], `${field}-${index + 1}`)
    if (!id) return
    const status = textValue((item as Record<string, unknown>)._change_status, '')
    if (status === 'added' || status === 'modified' || status === 'unchanged' || status === 'deleted') {
      map.set(id, status)
    }
  })
  return map
}

/** 兜底：specDiff 完全没值→added（首轮创建）；有值但该 id 没标→unchanged。 */
export function resolveChangeStatus(
  status: ChangeStatus | undefined,
  hasDiff: boolean,
): ChangeStatus {
  return status ?? (hasDiff ? 'unchanged' : 'added')
}

function PageCard({
  page,
  index,
  changeStatus,
}: {
  page: RequirementPageRow
  index: number
  /** 跨迭代变更标记；不传时不渲染徽标（向后兼容）。 */
  changeStatus?: ChangeStatus
}): ReactElement {
  return (
    <article className={cx('requirement-doc-page-card')} key={page.key}>
      <header className={cx('requirement-doc-page-head')}>
        <span className={cx('requirement-doc-page-index')} aria-hidden="true">
          {index + 1}
        </span>
        <div className={cx('requirement-doc-page-title')}>
          <strong>{page.name}</strong>
          {page.path || page.pageId ? (
            <code className={cx('requirement-doc-page-path')}>{page.path || page.pageId}</code>
          ) : null}
        </div>
        <ChangeBadge changeStatus={changeStatus} />
      </header>
      {page.description ? (
        <p className={cx('requirement-doc-page-desc')}>{page.description}</p>
      ) : null}

      {/* 主区：每块套一个浅底内嵌面板，让层次从"平铺的小标题"变成"可分辨的区块"。 */}
      {page.goal ? (
        <section className={cx('requirement-doc-page-zone')}>
          <span className={cx('requirement-doc-page-zone-title')}>页面目标</span>
          <p className={cx('requirement-doc-page-zone-body')}>{page.goal}</p>
        </section>
      ) : null}

      {page.informationItems.length ? (
        <section className={cx('requirement-doc-page-zone')}>
          <span className={cx('requirement-doc-page-zone-title')}>业务信息</span>
          {/* 磁贴网格而非列表：信息项之间彼此独立，网格比竖排列表更易扫读。 */}
          <div className={cx('requirement-doc-page-tiles')}>
            {page.informationItems.map((item) => (
              <div className={cx('requirement-doc-page-tile')} key={item.key}>
                <strong>{item.label}</strong>
                {item.description ? <span>{item.description}</span> : null}
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {page.actions.length ? (
        <section className={cx('requirement-doc-page-zone')}>
          <span className={cx('requirement-doc-page-zone-title')}>核心操作</span>
          <div className={cx('requirement-doc-action-list')}>
            {page.actions.map((action) => (
              <div className={cx('requirement-doc-action')} key={action.key}>
                <div className={cx('requirement-doc-action-heading')}>
                  <strong>{action.name}</strong>
                  <em className={cx(`is-${action.behaviorType}`)}>
                    {behaviorTypeLabel(action.behaviorType)}
                  </em>
                </div>
                {action.description ? <Text type="secondary">{action.description}</Text> : null}
                {action.expectedResult ? (
                  <div className={cx('requirement-doc-action-result')}>
                    <ArrowRightOutlined aria-hidden="true" />
                    <span>{action.expectedResult}</span>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {page.navigationTargets.length ? (
        <section className={cx('requirement-doc-page-zone')}>
          <span className={cx('requirement-doc-page-zone-title')}>页面跳转</span>
          <div className={cx('requirement-doc-tag-row')}>
            {page.navigationTargets.map((target) => (
              <Tag key={target}>{target}</Tag>
            ))}
          </div>
        </section>
      ) : null}

      {/* 规则区：验收标准是长尾细节，合并成一个弱化区（分隔线而非面板底），
          不与目标 / 操作抢注意力，但内容一条不少、不折叠。
          state_requirements（状态要求）不再渲染：页面状态属于 UI 设计实现细节，
          不该出现在产品规划文档中。 */}
      {page.acceptanceCriteria.length ? (
        <section className={cx('requirement-doc-page-zone', 'is-muted')}>
          {page.acceptanceCriteria.length ? (
            <>
              <span className={cx('requirement-doc-page-zone-title')}>产品验收标准</span>
              <ul className={cx('requirement-doc-page-rules', 'requirement-doc-acceptance')}>
                {page.acceptanceCriteria.map((criterion, criterionIndex) => (
                  <li key={`${page.key}-ac-${criterionIndex}`}>{criterion}</li>
                ))}
              </ul>
            </>
          ) : null}
        </section>
      ) : null}
    </article>
  )
}

/** 渲染产品规划页面卡片：目标、业务信息、操作、跳转、状态与验收标准。
 *  `specDiff` 是跨迭代增量合并产物（带 `_change_status` 标记），用于把页面
 *  分成"本次迭代变更"（added/modified）与"未变更内容"（unchanged）两段展示，
 *  让用户在基于历史迭代的新迭代里清楚看到哪些页面是本轮新增/调整、哪些是从基线继承。 */
export function RequirementPagesSection({
  productPlan,
  spec,
  specDiff,
  sectionKey
}: { productPlan: JsonRecord; spec: JsonRecord; specDiff?: JsonRecord } & SectionProps): ReactElement {
  const pages = requirementPageRows(
    productPlan,
    recordItems(spec.pages),
    recordItems(specDiff?.pages)
  )
  // 把 specDiff 里每页的 _change_status 注入到 rows：以 pageId 为键。
  // 没拿到 specDiff（首轮创建）时全部按"unchanged"处理，UI 上不分区。
  const changeStatusByPageId = useMemo(() => {
    const map = new Map<string, 'added' | 'modified' | 'unchanged' | 'deleted'>()
    const diffPages = recordItems(specDiff?.pages)
    diffPages.forEach((page, index) => {
      const pageId = textValue(page.pageId, `page-${index + 1}`)
      if (!pageId) return
      const status = textValue(page._change_status, '')
      if (status === 'added' || status === 'modified' || status === 'unchanged' || status === 'deleted') {
        map.set(pageId, status)
      }
    })
    return map
  }, [specDiff])
  const pagesWithStatus = useMemo(
    () => pages.map((page) => ({
      ...page,
      // 找不到时的兜底：
      // - 如果 specDiff 完全没值（首轮创建），所有页面都是本轮新增；
      // - 如果 specDiff 有值但该页没标（理论上 merger 应该每页都标，缺失视为 unchanged 兜底）。
      changeStatus: changeStatusByPageId.get(page.pageId)
        ?? (specDiff ? ('unchanged' as const) : ('added' as const)),
    })),
    [pages, changeStatusByPageId, specDiff],
  )
  // 分区：变更（added/modified/deleted）一组、未变更（unchanged）一组。
  const changedPages = pagesWithStatus.filter((p) => p.changeStatus !== 'unchanged')
  const unchangedPages = pagesWithStatus.filter((p) => p.changeStatus === 'unchanged')
  const showSections = changedPages.length > 0 && unchangedPages.length > 0
  return (
    <section
      aria-label="页面"
      className={cx('requirement-doc-section')}
      id={`requirement-doc-panel-${sectionKey}`}
      role="tabpanel"
    >
      <div className={cx('requirement-doc-section-title', 'is-pages')}>
        <FileTextOutlined /> <span>页面与用户操作</span>
        <span className={cx('requirement-doc-section-count')}>{pages.length}</span>
      </div>
      {showSections ? (
        <>
          <div className={cx('requirement-doc-change-group', 'is-changed')}>
            <div className={cx('requirement-doc-page-group-title')}>
              <span className={cx('requirement-doc-page-group-badge', 'is-changed')}>
                本次迭代变更（{changedPages.length}）
              </span>
            </div>
            <div className={cx('requirement-doc-page-list')}>
              {changedPages.map((page, index) => (
                <PageCard
                  index={index}
                  key={page.key}
                  page={page}
                  changeStatus={page.changeStatus}
                />
              ))}
            </div>
          </div>
          <div className={cx('requirement-doc-change-group', 'is-unchanged')}>
            <div className={cx('requirement-doc-page-group-title')}>
              <span className={cx('requirement-doc-page-group-badge', 'is-unchanged')}>
                未变更内容（{unchangedPages.length}）
              </span>
            </div>
            <div className={cx('requirement-doc-page-list')}>
              {unchangedPages.map((page, index) => (
                <PageCard
                  index={index}
                  key={page.key}
                  page={page}
                  changeStatus={page.changeStatus}
                />
              ))}
            </div>
          </div>
        </>
      ) : (
        <div className={cx('requirement-doc-page-list')}>
          {pagesWithStatus.length ? (
            pagesWithStatus.map((page, index) => (
              <PageCard
                index={index}
                key={page.key}
                page={page}
                changeStatus={page.changeStatus}
              />
            ))
          ) : (
            <Text type="secondary">暂无页面规划</Text>
          )}
        </div>
      )}
    </section>
  )
}

/** 渲染业务流程步骤，帮助用户理解跨页面的业务串联。 */
export function RequirementFlowsSection({
  spec,
  specDiff,
  sectionKey
}: { spec: JsonRecord; specDiff?: JsonRecord } & SectionProps): ReactElement {
  const flows = requirementFlowRows({
    ...spec,
    business_flows: mergeDeletedRecords(spec.business_flows, specDiff?.business_flows, 'id')
  })
  const flowStatusMap = useMemo(
    () => buildChangeStatusMap(specDiff, 'business_flows', 'id'),
    [specDiff]
  )
  return (
    <section
      aria-label="业务流程"
      className={cx('requirement-doc-section')}
      id={`requirement-doc-panel-${sectionKey}`}
      role="tabpanel"
    >
      <div className={cx('requirement-doc-section-title', 'is-flows')}>
        <BranchesOutlined /> <span>业务流程</span>
        <span className={cx('requirement-doc-section-count')}>{flows.length}</span>
      </div>
      <div className={cx('requirement-doc-card-list')}>
        {flows.length ? (() => {
          const flowsWithStatus = flows.map((flow) => ({
            flow,
            changeStatus: resolveChangeStatus(flowStatusMap.get(flow.key), Boolean(specDiff)),
          }))
          const changed = flowsWithStatus.filter((f) => f.changeStatus !== 'unchanged')
          const unchanged = flowsWithStatus.filter((f) => f.changeStatus === 'unchanged')
          const showGroups = changed.length > 0 && unchanged.length > 0
          const renderCard = (f: typeof flowsWithStatus[number]): ReactElement => (
            <article className={cx('requirement-doc-card')} key={f.flow.key}>
              <div className={cx('requirement-doc-card-heading')}>
                <strong>{f.flow.name}</strong>
                <ChangeBadge changeStatus={f.changeStatus} />
              </div>
              {f.flow.description ? <Text type="secondary">{f.flow.description}</Text> : null}
              {f.flow.steps.length ? (
                <ol className={cx('requirement-doc-flow-steps')}>
                  {f.flow.steps.map((step, index) => (
                    <li key={`${f.flow.key}-step-${index}`}>{step}</li>
                  ))}
                </ol>
              ) : null}
            </article>
          )
          if (!showGroups) return <>{flowsWithStatus.map((f) => renderCard(f))}</>
          return (
            <>
              <div className={cx('requirement-doc-change-group', 'is-changed')}>
                <div className={cx('requirement-doc-page-group-title')}>
                  <span className={cx('requirement-doc-page-group-badge', 'is-changed')}>
                    本次迭代变更（{changed.length}）
                  </span>
                </div>
                <div className={cx('requirement-doc-card-list')}>
                  {changed.map((f) => renderCard(f))}
                </div>
              </div>
              <div className={cx('requirement-doc-change-group', 'is-unchanged')}>
                <div className={cx('requirement-doc-page-group-title')}>
                  <span className={cx('requirement-doc-page-group-badge', 'is-unchanged')}>
                    未变更内容（{unchanged.length}）
                  </span>
                </div>
                <div className={cx('requirement-doc-card-list')}>
                  {unchanged.map((f) => renderCard(f))}
                </div>
              </div>
            </>
          )
        })() : (
          <Text type="secondary">暂无业务流程</Text>
        )}
      </div>
    </section>
  )
}

export { stringItems }
