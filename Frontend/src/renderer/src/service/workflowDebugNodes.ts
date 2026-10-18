import type { WorkflowDebugStage } from '../typings'

export type WorkflowDebugNodeOption = {
  value: string
  title: string
}

const WORKFLOW_DEBUG_NODES_BY_STAGE: Record<WorkflowDebugStage, readonly WorkflowDebugNodeOption[]> = {
  development: [
    { value: 'api_design_readiness_gate', title: '接口设计检查' },
    { value: 'development_readiness_gate', title: '开发前置检查' },
    { value: 'entity_source_binding', title: '实体数据源绑定' },
    { value: 'project_planning', title: '项目规划' },
    { value: 'inspect_workspace', title: '检查工作区' },
    { value: 'prepare_build_tasks', title: '生成构建任务' },
    { value: 'authorization_bootstrap', title: '初始化权限配置' },
    { value: 'build', title: '代码生成与构建' },
    { value: 'unit_test', title: '运行单元测试' },
    { value: 'unit_test_repair', title: '修复单元测试' },
    { value: 'test_phase_confirmation', title: '确认开发完成' },
    { value: 'integration_test', title: '运行集成测试' },
    { value: 'small_task_repair', title: '修复集成测试问题' },
    { value: 'code_review', title: '执行代码审查' },
    { value: 'launch_project', title: '启动本地预览' },
    { value: 'acceptance', title: '项目验收' },
    { value: 'finalize_project', title: '完成项目' }
  ],
  design: [
    { value: 'requirements', title: '需求与产品规划' },
    { value: 'ui_confirmation', title: '生成 UI 设计稿' }
  ],
  // 确认节点只能由正式确认卡触发，避免调试入口越过用户确认。
  planning: [
    { value: 'planning_stage_entry', title: '进入计划阶段' },
    { value: 'technical_planning', title: '生成技术规划' }
  ]
}

/** 返回指定工作阶段可见的调试节点，避免不同阶段共享节点列表。 */
export function workflowDebugNodesForStage(
  stage: WorkflowDebugStage
): readonly WorkflowDebugNodeOption[] {
  return WORKFLOW_DEBUG_NODES_BY_STAGE[stage]
}

/** 检查调试节点是否属于调用方当前工作阶段。 */
export function isWorkflowDebugNodeForStage(stage: WorkflowDebugStage, node: string): boolean {
  return WORKFLOW_DEBUG_NODES_BY_STAGE[stage].some((option) => option.value === node)
}
