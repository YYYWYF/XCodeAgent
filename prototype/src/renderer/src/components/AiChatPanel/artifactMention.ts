/**
 * 开发阶段的产物目标模型与消息提及渲染工具。
 * 开发阶段通过「产物」按钮直接选择页面 / 接口发起实施（不再经输入框 @ 键入）；
 * 这里集中维护候选目标的状态描述与历史消息中 @ 提及的轻格式渲染，便于独立复用与测试。
 */

/** 产物候选与直接发起共用的目标描述。 */
export type ComposerArtifactTarget = {
  /** 领域产物 ID（page:xxx / endpoint:contract:endpoint）。 */
  artifactId: string
  kind: 'page' | 'endpoint'
  /** 候选项展示名，页面为页面名，接口为 `METHOD path`。 */
  label: string
  /** 候选项第二行提示，页面显示路由，接口显示归属契约。 */
  hint: string
  /** 页面目标的 pageId。 */
  pageId?: string
  /** 接口目标的契约与 endpoint 身份。 */
  apiContractId?: string
  endpointId?: string
  /** 产物当前实施状态，决定可否再次发起并渲染为面板状态徽标。 */
  state: ComposerArtifactState
  /** 产物已被后台任务接管、实施中或已交付时禁用再次发起，并给出原因。 */
  disabled: boolean
  disabledReason: string
  /** 非空表示该产物的实施工作流已完成但留有后续步骤：点击行直接继续该任务，而不是发起新实施。 */
  continuationTaskId?: string
}

/**
 * 产物实施状态：ready 待实施（可发起）/ in-progress 实施中 / queued 后台任务接管 /
 * delivered 已交付 / continue 工作流已完成但留有后续步骤（行内即「继续处理」按钮）。
 */
export type ComposerArtifactState = 'continue' | 'delivered' | 'in-progress' | 'queued' | 'ready'

/**
 * 把消息文本拆成普通片段与 @ 提及片段，用于消息渲染时给提及加行内代码样式的轻格式。
 * 历史消息可能仍包含旧的 `@产物名` 文本，渲染时保留其行内代码样式。
 * 返回顺序片段数组，`mentioned: true` 的片段按行内代码样式渲染。
 */
export function splitTextByMentions(
  text: string,
  labels: string[]
): Array<{ text: string; mentioned: boolean }> {
  if (!text || labels.length === 0) return text ? [{ text, mentioned: false }] : []
  const sortedLabels = [...labels].sort((left, right) => right.length - left.length)
  const escapedLabels = sortedLabels.map((label) =>
    label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  )
  const pattern = new RegExp(`(@(?:${escapedLabels.join('|')}))`, 'g')
  return text
    .split(pattern)
    .filter((part) => part.length > 0)
    .map((part) => ({ text: part, mentioned: part.startsWith('@') }))
}
