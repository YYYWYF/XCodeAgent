import {
  CaretDownOutlined,
  FileTextOutlined,
  FolderOutlined,
  LoadingOutlined
} from '@ant-design/icons'
import { useEffect, useMemo, useState, type ReactElement } from 'react'
import { cx } from '../../../../utils'
import type {
  TestCaseBlueprint,
  TestCaseGroup,
  TestCaseExecutionSnapshot,
  TestCasePreparationSnapshot
} from '../../../../testCasePreparation'
import { TEST_CASE_BLUEPRINTS } from '../../../../testCasePreparation'
import './TestCasesPanel.less'

type TestExecutionStatus = 'idle' | 'running' | 'passed' | 'failed'
type GenerationStatus = 'not-started' | 'in-progress' | 'completed'
type CaseResult = 'not-run' | 'running' | 'passed' | 'failed'

type TestCaseDefinition = TestCaseBlueprint & { groupLabel: string }

type Props = {
  executionStatus: TestExecutionStatus
  execution?: TestCaseExecutionSnapshot
  onRetry: () => void
  snapshot: TestCasePreparationSnapshot
}

/** 根据异步分组快照生成稳定的演示用例目录，保证每次刷新顺序一致。 */
function buildDefinitions(groups: TestCaseGroup[]): TestCaseDefinition[] {
  return groups.flatMap((group) => {
    const knownCases = TEST_CASE_BLUEPRINTS.filter((item) => item.groupId === group.id)
    return Array.from({ length: group.total }, (_, index) => {
      const sequence = index + 1
      const knownCase = knownCases[index]
      if (knownCase) return { ...knownCase, groupLabel: group.label }
      return {
        id: `${group.id}-${sequence}`,
        groupId: group.id,
        groupLabel: group.label,
        title: `${group.label}核心路径`,
        scenario: group.label,
        preconditions: ['需求文档和项目计划已确认', '应用测试环境已启动'],
        steps: [
          `进入“${group.label}”业务场景`,
          `执行第 ${sequence} 条验证操作`,
          '记录页面反馈与接口响应'
        ],
        testScript: `runCase('${group.id}-${sequence}'); expectBusinessRule();`,
        expected: '页面反馈符合需求文档，接口返回结构和业务状态正确。'
      }
    })
  })
}

/** 将分组生成数量映射为单条用例的生成状态。 */
function generationStatus(group: TestCaseGroup | undefined, index: number): GenerationStatus {
  if (!group || index >= group.generated) {
    return group?.status === 'generating' && index === group.generated
      ? 'in-progress'
      : 'not-started'
  }
  return 'completed'
}

/** 将用例生成状态和测试执行状态合并成右侧结果状态。 */
function caseResult(
  generated: GenerationStatus,
  caseId: string,
  caseIndex: number,
  execution: TestCaseExecutionSnapshot
): CaseResult {
  if (generated !== 'completed') return 'not-run'
  // 只有当前用例 Workflow 明确指向该用例时才显示紫色进行中；
  // 非功测试没有 activeCaseId，不能按 completed 索引猜测第一条用例正在执行。
  if (execution.activeCaseId === caseId && execution.status === 'running') return 'running'
  const storedResult = execution.results?.[caseId]
  if (storedResult === 'passed' || storedResult === 'failed' || storedResult === 'running') {
    return storedResult
  }
  if (execution.status === 'failed' && caseIndex === Math.max(0, execution.completed - 1))
    return 'failed'
  if (caseIndex < execution.completed) return 'passed'
  return 'not-run'
}

/** 测试阶段右侧用例工作台：左侧目录、右侧用例内容与执行结果。 */
export default function TestCasesPanel({
  executionStatus,
  execution,
  onRetry,
  snapshot
}: Props): ReactElement {
  const definitions = useMemo(() => buildDefinitions(snapshot.groups), [snapshot.groups])
  const [activeCaseId, setActiveCaseId] = useState('')
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(snapshot.groups.map((group) => [group.id, false]))
  )

  const groupById = useMemo(
    () => new Map(snapshot.groups.map((group) => [group.id, group])),
    [snapshot.groups]
  )
  const activeCase = definitions.find((item) => item.id === activeCaseId)

  // 用例选择只跟随当前用例 Workflow；非功测试期间不自动选中任何用例。
  useEffect(() => {
    const workflowCaseId = execution?.activeCaseId || ''
    const workflowCase = definitions.find((item) => item.id === workflowCaseId)
    if (workflowCase) {
      setActiveCaseId(workflowCase.id)
      setCollapsedGroups((current) => ({ ...current, [workflowCase.groupId]: false }))
      return
    }
    if ((execution?.completed || 0) === 0) setActiveCaseId('')
  }, [definitions, execution?.activeCaseId, execution?.completed])

  /** 切换业务分组的展开状态，保持目录层级但不增加滚动负担。 */
  const toggleGroup = (groupId: string): void => {
    setCollapsedGroups((current) => ({ ...current, [groupId]: !current[groupId] }))
  }

  /** 切换当前用例并同步右侧内容区。 */
  const selectCase = (item: TestCaseDefinition): void => {
    setActiveCaseId(item.id)
  }

  const activeGroup = activeCase ? groupById.get(activeCase.groupId) : undefined
  const activeGeneration = activeCase
    ? generationStatus(activeGroup, Number(activeCase.id.split('-').pop()) - 1)
    : 'not-started'
  const executionSnapshot = execution || {
    completed: executionStatus === 'passed' ? snapshot.total : 0,
    status: executionStatus,
    total: snapshot.total
  }
  const activeCaseIndex = activeCase
    ? definitions.findIndex((item) => item.id === activeCase.id)
    : -1
  const activeResult = caseResult(
    activeGeneration,
    activeCase?.id || '',
    activeCaseIndex,
    executionSnapshot
  )
  const activeDefects = activeCase ? executionSnapshot.defects?.[activeCase.id] || [] : []

  return (
    <section aria-label="测试用例" className={cx('test-cases-panel')}>
      <div className={cx('test-cases-workspace')}>
        <aside aria-label="测试用例目录" className={cx('test-cases-directory')}>
          <div className={cx('test-cases-directory-body')}>
            <div className={cx('test-cases-group-list')}>
              {snapshot.groups.map((group) => {
                const collapsed = collapsedGroups[group.id]
                return (
                  <div className={cx('test-cases-tree-node')} key={group.id}>
                    <button
                      aria-expanded={!collapsed}
                      className={cx('test-cases-group-row')}
                      onClick={() => toggleGroup(group.id)}
                      type="button"
                    >
                      <CaretDownOutlined className={cx(collapsed && 'collapsed')} />
                      <FolderOutlined />
                      <strong>{group.label}</strong>
                      <small>
                        {group.generated}/{group.total}
                      </small>
                    </button>
                    {!collapsed ? (
                      <div className={cx('test-cases-tree-children')}>
                        {definitions
                          .filter((item) => item.groupId === group.id)
                          .map((item, index) => {
                            const status = generationStatus(group, index)
                            const caseIndex = definitions.findIndex(
                              (candidate) => candidate.id === item.id
                            )
                            const result = caseResult(status, item.id, caseIndex, executionSnapshot)
                            return (
                              <button
                                className={cx(
                                  'test-cases-case-row',
                                  activeCaseId === item.id && 'selected',
                                  result
                                )}
                                disabled={status !== 'completed'}
                                key={item.id}
                                onClick={() => selectCase(item)}
                                title={item.title}
                                type="button"
                              >
                                <FileTextOutlined />
                                <span>{item.title}</span>
                                {/* 状态圆点只描述执行情况；用例尚未生成时没有执行状态，不渲染圆点。 */}
                                {status === 'completed' ? (
                                  <i
                                    className={cx(
                                      'test-cases-status-dot',
                                      result === 'passed' && 'completed',
                                      result === 'running' && 'in-progress',
                                      result === 'failed' && 'failed'
                                    )}
                                  />
                                ) : null}
                              </button>
                            )
                          })}
                      </div>
                    ) : null}
                  </div>
                )
              })}
            </div>
          </div>
        </aside>

        <main className={cx('test-cases-content')}>
          {activeCase ? (
            <article className={cx('test-case-detail')}>
              {activeGeneration !== 'completed' ? (
                <div className={cx('test-case-not-ready')}>
                  <LoadingOutlined /> 当前用例正在后台生成，生成完成后会展示完整步骤。
                  {snapshot.status === 'failed' || snapshot.status === 'stale' ? (
                    <button onClick={onRetry} type="button">
                      重新生成
                    </button>
                  ) : null}
                </div>
              ) : (
                <>
                  <section className={cx('test-case-section')}>
                    <h2>前置条件</h2>
                    <ul>
                      {activeCase.preconditions.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </section>
                  <section className={cx('test-case-section')}>
                    <h2>执行步骤</h2>
                    <ol>
                      {activeCase.steps.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ol>
                  </section>
                  <section className={cx('test-case-section')}>
                    <h2>预期结果</h2>
                    <p>{activeCase.expected}</p>
                  </section>
                  <section className={cx('test-case-section')}>
                    <h2>测试脚本</h2>
                    <pre>{activeCase.testScript}</pre>
                  </section>
                  <section className={cx('test-case-section test-case-evidence')}>
                    <h2>测试结果</h2>
                    <p>
                      {activeResult === 'passed'
                        ? activeDefects.length > 0
                          ? '本轮回归验证通过，关联缺陷已关闭。'
                          : '本轮验证通过，未发现与该用例相关的缺陷。'
                        : activeResult === 'failed'
                          ? '本轮验证未通过，请查看缺陷清单并回到开发阶段修复。'
                          : activeResult === 'running'
                            ? '测试执行器正在运行该用例，结果将在完成后更新。'
                            : '测试尚未开始，进入测试阶段后自动执行。'}
                    </p>
                  </section>
                  <section className={cx('test-case-section', 'test-case-defects')}>
                    <h2>缺陷情况</h2>
                    {activeDefects.length > 0 ? (
                      <div className={cx('test-case-defect-list')}>
                        {activeDefects.map((defect) => (
                          <article className={cx('test-case-defect-item')} key={defect.id}>
                            <div>
                              <strong>{defect.id} · {defect.title}</strong>
                              <span>{defect.severity}</span>
                            </div>
                            <p>{defect.summary}</p>
                            <small>
                              {defect.target} · {
                                defect.status === 'resolved'
                                  ? '已修复'
                                  : defect.status === 'repairing'
                                    ? '修复中'
                                    : '待修复'
                              }
                            </small>
                          </article>
                        ))}
                      </div>
                    ) : (
                      <p>
                        {activeResult === 'passed'
                          ? '本用例未发现缺陷。'
                          : activeResult === 'running'
                            ? '正在执行用例并扫描缺陷。'
                            : '用例尚未执行，暂无缺陷记录。'}
                      </p>
                    )}
                  </section>
                </>
              )}
            </article>
          ) : (
            <div className={cx('test-cases-empty')}>
              <FileTextOutlined />
              <strong>等待用例测试工作流</strong>
              <span>当前用例 Workflow 启动后会自动选中对应内容。</span>
            </div>
          )}
        </main>
      </div>
    </section>
  )
}
