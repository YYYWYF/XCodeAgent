import type {
  WorkflowClarification,
  WorkflowClarificationAnswer,
  WorkflowClarificationAnswers,
} from '../../typings'

/** 为页面最终验收生成不依赖问题列表的稳定继续消息。 */
export function pageAcceptanceContinuationMessage(
  clarification: WorkflowClarification | undefined,
  answers: WorkflowClarificationAnswers
): string {
  if (clarification?.mode !== 'page_acceptance' || answers.page_acceptance !== 'accepted') {
    return ''
  }
  return '已完成页面预览，确认验收通过并完成计划。'
}

/** 根据实体设计动作生成恢复 Workflow 所需的用户可见消息。 */
export function entityDesignActionContinuationMessage(
  action: WorkflowClarificationAnswer | undefined
): string {
  if (typeof action !== "object" || !action || Array.isArray(action)) return "";
  const actionType = String((action as Record<string, unknown>).action || "");
  const labels: Record<string, string> = {
    select_data_source: "已选择数据源，请生成实体设计方案后继续。",
    submit_static_data: "已补充静态数据设计，请继续生成实体设计。",
    submit_bindings: "已提交字段绑定，请继续生成实体设计。",
    approve_table_generation: "已批准生成目标表结构，请继续。",
    list_tables: "已查询当前数据库的表清单，请选择目标表。",
    select_table: "已选择目标表，请绑定实体字段后确认数据库方案。",
    ai_assist: "已请求 AI 辅助，请查看表单内的建议并采纳。",
    execute_add_columns: "已请求执行补列 DDL，正在生成并执行中。",
    execute_create_table: "已请求执行建表 DDL，正在生成并执行中。",
    submit_entity_design: "已确认实体设计，实体设计完成。",
  };
  return labels[actionType] || "已提交实体设计动作，请继续。";
}
