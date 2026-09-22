import type { PlanningArtifactRecoveryKey } from './planningArtifactRecovery'
import { WORKSPACE_ARTIFACT_DIR_NAME } from '../../constants/branding'

const ARTIFACT_ROOT = WORKSPACE_ARTIFACT_DIR_NAME

/**
 * 规划产物的唯一路径表。
 *
 * 设计与计划阶段会把同一份产物写到两个位置（草稿目录与正式目录），取值时按顺序取
 * 第一个存在的。工作台的冷恢复与历史版本的「应用文件」共用这一份，避免各自维护
 * 一张路径表而漂移。
 *
 * `markdownPaths` 与 `contentPaths` 语义不同，不要混用：
 * - `markdownPaths`：可直接按 Markdown 渲染的文档；
 * - `contentPaths`：以 JSON 承载的结构化产物（如 UI 设计稿），需要走结构化视图。
 *   提供时优先于 `markdownPaths`（与工作台既有取值口径一致）。
 */
export type DesignArtifactEntry = {
  key: PlanningArtifactRecoveryKey
  /** 面向用户的名称，与工作台文档页签口径一致。 */
  label: string
  markdownPaths: readonly string[]
  contentPaths?: readonly string[]
}

export const LOCAL_DESIGN_ARTIFACTS: readonly DesignArtifactEntry[] = [
  {
    key: 'requirement-spec',
    label: '需求文档',
    markdownPaths: [
      `${ARTIFACT_ROOT}/drafts/specs/requirement-spec.md`,
      `${ARTIFACT_ROOT}/specs/requirement-spec.md`
    ]
  },
  {
    key: 'product-plan',
    label: '产品规划',
    markdownPaths: [`${ARTIFACT_ROOT}/drafts/plans/product-plan.md`, `${ARTIFACT_ROOT}/plans/product-plan.md`]
  },
  {
    key: 'ui-design',
    label: 'UI 设计',
    // UI 设计稿以 JSON 承载（逐页设计），没有 Markdown 形态。
    markdownPaths: [],
    contentPaths: [`${ARTIFACT_ROOT}/specs/ui-designs.json`]
  },
  {
    key: 'technical-plan',
    label: '技术规划',
    markdownPaths: [`${ARTIFACT_ROOT}/plans/technical-plan.md`]
  }
]

/**
 * 可浏览的规划文档：只保留有 Markdown 形态的产物。
 *
 * 结构化产物（UI 设计稿）按 Markdown 渲染会显示成一坨 JSON，因此不进「应用文件」的文档分组。
 */
export const BROWSABLE_DESIGN_DOCS: readonly DesignArtifactEntry[] = LOCAL_DESIGN_ARTIFACTS.filter(
  (entry) => entry.markdownPaths.length > 0
)
