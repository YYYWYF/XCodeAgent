// 工作流底层 DAG 注册表（原型）。
// 设计理念：真实工程中底层 DAG 庞大且交织，对话面板只呈现本次选中执行的一段；
// 因此同一种工作流在界面上有多个表现形式（如开发工作流的详细设计段、后台执行段、
// 回来验收与调整的段），不要求展示的节点一致，但要求底层 DAG 是同一套。
// 剧本（mock/scripts/*）只负责"从 DAG 选段并播放"，节点标题与连线关系以本注册表为唯一来源。

/** DAG 节点：标题与详情是各表现段共享的统一口径。 */
export type WorkflowGraphNode = {
  id: string
  title: string
  detail: string
  /** 节点的执行面：conversation=前台对话内执行，background=后台任务引擎执行。 */
  surface: 'conversation' | 'background'
}

/** 表现段：一次对话执行从 DAG 中选出的一段节点轨迹。 */
export type WorkflowGraphSegment = {
  id: string
  /** 段落展示名，例如「详细设计」「产物验收」。 */
  label: string
  nodeIds: string[]
}

/** 一种工作流的完整底层定义：一张 DAG 与它支持的若干表现段。 */
export type WorkflowDefinition = {
  id: string
  name: string
  nodes: WorkflowGraphNode[]
  /** 有向边 [from, to]；只描述依赖关系，播放顺序由表现段决定。 */
  edges: Array<[string, string]>
  segments: Record<string, WorkflowGraphSegment>
}

/** 开发工作流的完整底层 DAG：设计 → 执行（同步/后台两分支）→ 验收 → 调整回流。 */
const DEVELOPMENT_WORKFLOW: WorkflowDefinition = {
  id: 'development',
  name: '开发工作流',
  nodes: [
    {
      id: 'design_context',
      title: '汇总应用上下文',
      detail: '整合已确认的应用约束、项目计划与页面目标。',
      surface: 'conversation'
    },
    {
      id: 'design_scope',
      title: '梳理页面范围',
      detail: '明确页面职责、核心功能与关键用户路径。',
      surface: 'conversation'
    },
    {
      id: 'design_breakdown',
      title: '拆解功能与数据',
      detail: '整理功能点、数据展示与交互依赖。',
      surface: 'conversation'
    },
    {
      id: 'design_edge',
      title: '补齐边界与验收',
      detail: '定义异常态、边界约束与验收标准。',
      surface: 'conversation'
    },
    {
      id: 'endpoint_design_context',
      title: '汇总接口上下文',
      detail: '读取已确认的应用约束、项目计划和接口契约。',
      surface: 'conversation'
    },
    {
      id: 'endpoint_design_scope',
      title: '梳理请求与响应',
      detail: '明确查询参数、响应结构和页面调用关系。',
      surface: 'conversation'
    },
    {
      id: 'endpoint_design_edge',
      title: '补齐数据来源与边界',
      detail: '确认数据来源、异常返回和接口验收标准。',
      surface: 'conversation'
    },
    {
      id: 'choose_execution',
      title: '选择页面执行方式',
      detail: '页面产物：同步任务在当前对话中直接完成；异步/潮汐任务转入对应任务系统后台执行。',
      surface: 'conversation'
    },
    {
      id: 'choose_execution_endpoint',
      title: '选择接口执行方式',
      detail: '接口产物单独选择执行方式；同步任务在当前对话中直接完成，异步/潮汐任务转入对应任务系统后台执行。',
      surface: 'conversation'
    },
    {
      id: 'background_dispatch',
      title: '派发后台实现任务',
      detail: '已创建后台代码实现任务，可在对应任务系统查看执行进度。',
      surface: 'conversation'
    },
    {
      id: 'build_dag',
      title: '生成构建计划',
      detail: '解析详细设计产物，理清要生成哪些文件及其依赖关系，排出代码生成与构建顺序。',
      surface: 'background'
    },
    {
      id: 'generate_code',
      title: '生成代码',
      detail: '按模板生成页面与依赖接口源码。',
      surface: 'background'
    },
    {
      id: 'confirm_changes',
      title: '确认代码变更',
      detail: '在右侧源码区确认本次生成的代码 Diff，接受后继续构建。',
      surface: 'conversation'
    },
    {
      id: 'build_and_test',
      title: '构建及单元检查',
      detail: '执行构建与单元检查，确认无阻塞问题。',
      surface: 'background'
    },
    {
      id: 'launch_preview',
      title: '启动页面预览',
      detail: '启动当前页面预览。',
      surface: 'background'
    },
    {
      id: 'acceptance_preview',
      title: '打开产物预览',
      detail: '右侧工作区切换到开发产物，打开页面预览或接口调试。',
      surface: 'conversation'
    },
    {
      id: 'acceptance_confirm',
      title: '确认验收',
      detail: '在预览中确认实现内容，确认后产物交付。',
      surface: 'conversation'
    },
    {
      id: 'adjustment_plan',
      title: '制定调整方案',
      detail: '根据验收反馈确定需要调整的页面与接口范围。',
      surface: 'conversation'
    },
    {
      id: 'adjustment_apply',
      title: '应用调整并重新构建',
      detail: '提交调整后的实现，重新走执行与验收链路。',
      surface: 'background'
    }
  ],
  edges: [
    ['design_context', 'design_scope'],
    ['design_scope', 'design_breakdown'],
    ['design_breakdown', 'design_edge'],
    ['design_edge', 'choose_execution'],
    // 接口设计分支与页面设计分支在同一张 DAG 上交织；两个产物各自有「选择执行方式」决策点。
    ['endpoint_design_context', 'endpoint_design_scope'],
    ['endpoint_design_scope', 'endpoint_design_edge'],
    ['endpoint_design_edge', 'choose_execution_endpoint'],
    // 同步分支：当场执行构建链，生成代码后先经代码变更确认再继续。
    ['choose_execution', 'build_dag'],
    ['build_dag', 'generate_code'],
    ['generate_code', 'confirm_changes'],
    ['confirm_changes', 'build_and_test'],
    ['build_and_test', 'launch_preview'],
    // 后台分支：派发后由任务系统无人值守执行同一条构建链（无人看着，无需代码确认节点）。
    ['choose_execution', 'background_dispatch'],
    ['background_dispatch', 'build_dag'],
    ['build_dag', 'build_and_test'],
    // 完成后进入验收；验收反馈可回流到调整段并再次执行。
    ['launch_preview', 'acceptance_preview'],
    ['acceptance_preview', 'acceptance_confirm'],
    ['acceptance_confirm', 'adjustment_plan'],
    ['adjustment_plan', 'adjustment_apply'],
    ['adjustment_apply', 'build_dag']
  ],
  segments: {
    /** 详细设计段：前台设计四步 + 选择执行方式（交互节点）。 */
    design: {
      id: 'design',
      label: '详细设计',
      nodeIds: [
        'design_context',
        'design_scope',
        'design_breakdown',
        'design_edge',
        'choose_execution'
      ]
    },
    /** 派发收口段：选择后台通道后的派发确认节点。 */
    dispatch: {
      id: 'dispatch',
      label: '派发',
      nodeIds: ['background_dispatch']
    },
    /** 前台构建段：同步执行时在对话内当场播放的构建链（生成代码后经代码变更确认）。 */
    foreground_build: {
      id: 'foreground_build',
      label: '同步任务',
      nodeIds: [
        'build_dag',
        'generate_code',
        'confirm_changes',
        'build_and_test',
        'launch_preview'
      ]
    },
    /** 验收段：异步任务完成后回到主对话的确认链路。 */
    acceptance: {
      id: 'acceptance',
      label: '产物验收',
      nodeIds: ['acceptance_preview', 'acceptance_confirm']
    },
    /** 调整段：验收后需要修改时回流执行的预留链路。 */
    adjustment: {
      id: 'adjustment',
      label: '验收调整',
      nodeIds: ['adjustment_plan', 'adjustment_apply']
    }
  }
}

/** 工作流注册表：后续新增工作流（测试/审查等）在这里登记各自的完整 DAG。 */
const WORKFLOW_REGISTRY: Record<string, WorkflowDefinition> = {
  [DEVELOPMENT_WORKFLOW.id]: DEVELOPMENT_WORKFLOW
}

/** 读取一种工作流的完整底层定义；未注册的工作流返回 undefined。 */
export function getWorkflowDefinition(workflowId: string): WorkflowDefinition | undefined {
  return WORKFLOW_REGISTRY[workflowId]
}

/**
 * 读取一种工作流某个表现段的节点轨迹（深拷贝，避免播放器改写共享定义）。
 * 剧本可用返回节点 id 覆盖 detail，实现同一节点在不同目标下的差异化说明。
 */
export function workflowSegmentNodes(
  workflowId: string,
  segmentId: string
): WorkflowGraphNode[] {
  const definition = WORKFLOW_REGISTRY[workflowId]
  const segment = definition?.segments[segmentId]
  if (!definition || !segment) return []
  return segment.nodeIds.map((nodeId) => {
    const node = definition.nodes.find((candidate) => candidate.id === nodeId)
    if (!node) {
      throw new Error(`工作流 ${workflowId} 的表现段 ${segmentId} 引用了不存在的节点 ${nodeId}`)
    }
    return { ...node }
  })
}

/** 按 id 读取单个 DAG 节点（深拷贝）；剧本播放单节点轨迹（如选择执行方式）时使用。 */
export function workflowNode(workflowId: string, nodeId: string): WorkflowGraphNode {
  const definition = WORKFLOW_REGISTRY[workflowId]
  const node = definition?.nodes.find((candidate) => candidate.id === nodeId)
  if (!node) {
    throw new Error(`工作流 ${workflowId} 不存在节点 ${nodeId}`)
  }
  return { ...node }
}
