import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  PauseCircleOutlined,
} from "@ant-design/icons";
import { Alert, Button, Checkbox, Collapse, Input, Progress, Radio, Tag, Typography } from "antd";
import type { ReactElement } from "react";
import { useState } from "react";
import type {
  WorkflowBuildExecutionSlice,
  WorkflowBuildExecutionTask,
  WorkflowClarification,
  WorkflowClarificationQuestion,
  WorkflowClarificationSelectionGroup,
  WorkflowClarificationAnswer,
  WorkflowClarificationAnswers,
  WorkflowConfirmationArtifact,
  WorkflowRunPayload,
} from "../../../../typings";
import { cx } from "../../../../utils";
import {
  entityDesignActionContinuationMessage,
  pageAcceptanceContinuationMessage,
} from '../../workflowContinuation';
import type { WorkflowInteractionAvailability } from '../../planExecutionMode';
import AgentApprovalCard from '../AgentApprovalCard';
import {
  approveToolRequest,
  rejectToolRequest,
} from '../../../../service/workspaceTools';
import type { ToolApproval } from '../../../../service/workspaceTools';
import ConfirmationArtifact from './ConfirmationArtifact';
import DetailReview from './DetailReview';
import EntityDesignGateCard from './EntityDesignGateCard';
import './WorkflowRunCard.less';

const { Text } = Typography;
const { TextArea } = Input;

const OTHER_OPTION_VALUE = '__other__';

export type ClarificationAnswers = WorkflowClarificationAnswers;

type WorkflowRunCardProps = {
  disabled?: boolean;
  interactionAvailability: WorkflowInteractionAvailability;
  onEntityDesignGateJump?: (entityId: string) => void;
  onSubmitClarification?: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers,
  ) => void;
  workflow: WorkflowRunPayload;
  workspaceRoot?: string;
};

export default function WorkflowRunCard({
  disabled,
  interactionAvailability,
  onEntityDesignGateJump,
  onSubmitClarification,
  workflow,
  workspaceRoot,
}: WorkflowRunCardProps): ReactElement {
  const status = String(workflow.summary.status || "unknown");
  const artifacts = workflow.summary.artifacts || {};
  const clarification = workflowClarification(workflow);
  const confirmationArtifact = workflowConfirmationArtifact(workflow, clarification);
  const clarificationQuestions = clarification?.questions || [];
  const entityDesignGate = clarification?.mode === 'entity_design_required';
  const gateQuestion = clarification?.questions?.[0];
  const entityGateEntities = (clarification?.missing_entities || []).filter(
    (item) =>
      Boolean(
        item &&
          typeof item === 'object' &&
          String((item as { entity_id?: string }).entity_id || '').trim(),
      ),
  );
  const entityGateAnswerKey = gateQuestion
    ? clarificationQuestionKey(gateQuestion, 0)
    : 'entity_design_required';
  const detailReview = clarification?.mode === 'detail_review'
    ? clarification.review
    : undefined;
  // 实体设计评审：确认对象包含实体目标或实体设计摘要时，视为实体设计场景，
  // 此时裁剪卡片顶部的等待提示与“已生成产物”列表，避免与实体面板信息重复。
  const entityDesignReview = Boolean(
    detailReview &&
      ((detailReview.entities?.length || 0) > 0 ||
        Boolean(detailReview.summary?.entityDesign)),
  );
  const databaseApproval = workflowDatabaseApproval(clarification);
  const databaseApprovalAnswerKey = clarificationQuestions[0]
    ? clarificationQuestionKey(clarificationQuestions[0], 0)
    : "database_approval";
  const confirmationItemCount = detailReview
    ? (detailReview.pages?.length || 0) + (detailReview.endpoints?.length || 0)
    : clarificationQuestions.length;
  const requiresConfirmation = clarification?.status === "requires_user_input";
  const [answers, setAnswers] = useState<ClarificationAnswers>({});
  const canSubmitClarification =
    clarification?.status === "requires_user_input" &&
    clarificationQuestions.length > 0 &&
    clarificationQuestions.every((question, index) =>
      clarificationAnswerComplete(
        question,
        answers[clarificationQuestionKey(question, index)],
      ),
    );
  const updateAnswer = (key: string, value: WorkflowClarificationAnswer): void => {
    setAnswers((currentAnswers) => ({
      ...currentAnswers,
      [key]: value,
    }));
  };

  return (
    <div
      className={cx(
        "workflow-run-card",
        requiresConfirmation && "workflow-run-card-pending",
      )}
    >
      <div className={cx("workflow-run-header")}>
        <div className={cx("workflow-run-title")}>
          <span className={cx("workflow-run-signal")} aria-hidden="true" />
          <div>
            <Text className={cx("workflow-run-name")} strong>工作流执行</Text>
          </div>
        </div>
        <Tag className={cx("workflow-run-status")} color={workflowStatusColor(status)}>
          {workflowStatusText(status)}
        </Tag>
      </div>
      {!entityDesignReview && workflow.summary.message && (
        <div className={cx("workflow-run-message")}>
          <Text>{String(workflow.summary.message)}</Text>
        </div>
      )}
      {!entityDesignReview && Object.keys(artifacts).length > 0 && (
        <div className={cx("workflow-artifacts")}>
          <div className={cx("workflow-section-heading")}>
            <Text type="secondary">已生成产物</Text>
            <span>{Object.keys(artifacts).length} 个</span>
          </div>
          {Object.entries(artifacts).map(([name, path]) => (
            <div className={cx("workflow-artifact-item")} key={name}>
              <span className={cx("workflow-artifact-marker")} aria-hidden="true" />
              <Text code>{name}: {path}</Text>
            </div>
          ))}
        </div>
      )}
      {(clarificationQuestions.length > 0 || detailReview) && (
        <div className={cx("workflow-clarification")}>
          {!entityDesignReview && !entityDesignGate && (
            <div className={cx("workflow-clarification-header")}>
              <div>
                <Text strong>待确认事项</Text>
              </div>
              <Tag
                className={cx("workflow-confirmation-count")}
                color={
                  requiresConfirmation
                    ? "gold"
                    : "default"
                }
              >
                {confirmationItemCount}
              </Tag>
            </div>
          )}
          {requiresConfirmation && interactionAvailability !== 'active' && (
            <Alert
              message={
                interactionAvailability === 'unavailable'
                  ? '正在校准确认状态，请稍候。'
                  : '该确认已提交或已失效，请使用当前计划操作继续。'
              }
              showIcon
              type="info"
            />
          )}
          {detailReview ? (
            <DetailReview
              disabled={disabled}
              message={clarification?.message}
              onDesignAction={(action) =>
                onSubmitClarification?.(workflow, { entity_design: action })
              }
              onConfirm={(submission) => onSubmitClarification?.(
                workflow,
                { detail_review: submission },
              )}
              review={detailReview}
              workspaceRoot={workspaceRoot}
            />
          ) : databaseApproval ? (
            <DatabaseApprovalDecision
              approval={databaseApproval.approval}
              disabled={disabled}
              onSubmit={(answer) =>
                onSubmitClarification?.(workflow, {
                  [databaseApprovalAnswerKey]: answer,
                })
              }
              statements={databaseApproval.statements}
            />
          ) : entityDesignGate ? (
            <EntityDesignGateCard
              disabled={disabled}
              entities={entityGateEntities}
              explanation={
                clarification?.message ||
                String(gateQuestion?.question || '')
              }
              onJump={(entityId) => onEntityDesignGateJump?.(entityId)}
              onRetry={() =>
                onSubmitClarification?.(workflow, {
                  [entityGateAnswerKey]:
                    '已完成实体设计，请重新检测并继续生成页面/接口详细设计。',
                })
              }
            />
          ) : (
            <>
          {confirmationArtifact && (
            <ConfirmationArtifact
              artifact={confirmationArtifact}
            />
          )}
          <ClarificationContext clarification={clarification} />
          {clarificationQuestions.map((question, index) => (
            <div
              className={cx("workflow-clarification-question")}
              key={question.id || index}
            >
              <div className={cx("workflow-clarification-title")}>
                <Tag>{question.header || question.dimension || "需求"}</Tag>
                <Text>{question.question || "请补充需求细节。"}</Text>
              </div>
              <ClarificationQuestionControl
                disabled={disabled}
                onChange={(value) =>
                  updateAnswer(clarificationQuestionKey(question, index), value)
                }
                question={question}
                value={answers[clarificationQuestionKey(question, index)]}
              />
              {question.default_assumption && (
                <Text type="secondary">{question.default_assumption}</Text>
              )}
            </div>
          ))}
          {clarification?.status === "requires_user_input" && (
            <Button
              disabled={disabled || !canSubmitClarification}
              onClick={() => onSubmitClarification?.(workflow, answers)}
              type="primary"
            >
              确认并继续
            </Button>
          )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function DatabaseApprovalDecision({
  approval,
  disabled,
  onSubmit,
  statements,
}: {
  approval: ToolApproval;
  disabled?: boolean;
  onSubmit: (answer: string) => void;
  statements: string[];
}): ReactElement {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // 先落审批记录再恢复工作流：同意时携带审批授权，拒绝时标记拒绝并回传答案。
  const decide = async (
    action: "reject" | "once" | "operation",
  ): Promise<void> => {
    if (disabled || loading) return;
    setLoading(true);
    setError("");
    try {
      if (action === "reject") {
        await rejectToolRequest(approval.id);
        onSubmit("拒绝执行");
        return;
      }
      await approveToolRequest(
        approval.id,
        action === "operation" ? "operation" : "once",
      );
      onSubmit(
        action === "operation"
          ? "同意执行，后续相同操作不再询问"
          : "同意执行，仅本次",
      );
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "数据库审批操作失败。",
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <AgentApprovalCard
        allowFeedback={false}
        approval={approval}
        loading={loading}
        onApproveAlways={() => void decide("operation")}
        onApproveOnce={() => void decide("once")}
        onFeedback={() => undefined}
        onReject={() => void decide("reject")}
        statements={statements}
      />
      {error && (
        <Text className={cx("workflow-database-approval-error")} type="danger">
          {error}
        </Text>
      )}
    </>
  );
}

function BuildExecutionSliceProgress({
  executionSlice,
}: {
  executionSlice: WorkflowBuildExecutionSlice;
}): ReactElement | null {
  /** 展示当前页面或数据源范围的构建进度，不做应用级汇总。 */

  const [activeTaskKeys, setActiveTaskKeys] = useState<string[]>([]);
  const scope = executionSlice.scope;
  if (!scope) return null;
  const tasks = Array.isArray(executionSlice.tasks) ? executionSlice.tasks : [];
  const summary = executionSlice.summary || {};
  const total = numberValue(summary.total, tasks.length);
  const completed = numberValue(
    summary.completed,
    tasks.filter((task) => task.status === "completed").length,
  );
  const failed = numberValue(
    summary.failed,
    tasks.filter((task) => task.status === "failed").length,
  );
  const running = numberValue(summary.running, tasks.filter((task) => task.status === "running").length);
  const pending = numberValue(summary.pending, tasks.filter((task) => !task.status || task.status === "pending").length);
  const reused = numberValue(summary.reused, executionSlice.reusable_task_ids?.length || 0);
  const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
  const targetLabel =
    scope.type === "page"
      ? "页面"
      : scope.type === "data_source"
        ? "数据源"
        : scope.type === "endpoint"
          ? "接口"
          : "应用";
  const targetId = scope.targetId || executionSlice.target_unit_ids?.[0] || "";
  const progressStatus = failed > 0 ? "exception" : completed === total && total > 0 ? "success" : "active";
  const displayTasks = sortBuildTasksForDisplay(tasks);
  const expandedTaskKeys = new Set(activeTaskKeys);

  return (
    <div className={cx("workflow-build-progress")}>
      <div className={cx("workflow-build-progress-header")}>
        <div>
          <Text strong>执行进度</Text>
          <Text type="secondary">
            {targetId ? `${targetLabel}：${targetId}` : `${targetLabel}执行范围`}
          </Text>
        </div>
        <Tag
          className={cx(
            "workflow-build-progress-count-tag",
            failed > 0 ? "failed" : completed === total && total > 0 ? "completed" : "running",
          )}
          color={failed > 0 ? "red" : completed === total && total > 0 ? "green" : "purple"}
        >
          {completed}/{total}
        </Tag>
      </div>
      <Progress
        percent={percent}
        showInfo={false}
        status={progressStatus}
        strokeColor={failed > 0 ? "var(--wb-danger)" : "var(--wb-accent)"}
        trailColor="var(--wb-surface-subtle)"
      />
      <Text className={cx("workflow-build-progress-percent")} type="secondary">
        {percent}% 完成
      </Text>
      <div className={cx("workflow-build-progress-stats")}>
        <BuildProgressStat icon={<PauseCircleOutlined />} label="待执行" tone="pending" value={pending} />
        <BuildProgressStat icon={<LoadingOutlined />} label="执行中" tone="running" value={running} />
        <BuildProgressStat icon={<CheckCircleOutlined />} label="已完成" tone="completed" value={completed} />
        <BuildProgressStat icon={<CloseCircleOutlined />} label="失败" tone="failed" value={failed} />
        <BuildProgressStat icon={<ClockCircleOutlined />} label="已复用" tone="reused" value={reused} />
      </div>
      {tasks.length > 0 && (
        <div className={cx("workflow-build-task-section")}>
          <Text strong>任务详情</Text>
          <Collapse
            activeKey={activeTaskKeys}
            className={cx("workflow-build-task-list")}
            expandIconPosition="right"
            onChange={(keys) => {
              const nextKeys = Array.isArray(keys) ? keys : [keys];
              setActiveTaskKeys(nextKeys.map(String));
            }}
          >
          {displayTasks.map((task) => (
            <Collapse.Panel
              className={cx("workflow-build-task-panel", task.status || "pending")}
              header={(
                <BuildExecutionTaskHeader
                  expanded={expandedTaskKeys.has(taskId(task))}
                  task={task}
                />
              )}
              key={taskId(task)}
            >
              <BuildExecutionTaskDetails task={task} />
            </Collapse.Panel>
          ))}
          </Collapse>
        </div>
      )}
    </div>
  );
}

export function BuildExecutionRunCard({
  executionSlice,
  status,
}: {
  executionSlice: WorkflowBuildExecutionSlice;
  status: "running" | "completed" | "failed" | "requires_user_input";
}): ReactElement {
  /** 在对应构建步骤内部渲染独立的构建轮次卡片。 */

  return (
    <section className={cx("workflow-run-card", "workflow-build-run-card", status)}>
      <div className={cx("workflow-run-header")}>
        <div className={cx("workflow-run-title")}>
          <span className={cx("workflow-run-signal")} aria-hidden="true" />
          <Text className={cx("workflow-run-name")} strong>构建执行</Text>
        </div>
        <Tag className={cx("workflow-run-status")} color={workflowStatusColor(status)}>
          {workflowStatusText(status)}
        </Tag>
      </div>
      <BuildExecutionSliceProgress executionSlice={executionSlice} />
    </section>
  );
}

function BuildProgressStat({
  icon,
  label,
  tone,
  value,
}: {
  icon: ReactElement;
  label: string;
  tone: "pending" | "running" | "completed" | "failed" | "reused";
  value: number;
}): ReactElement {
  /** 渲染当前构建范围内的单项计数，避免上升到应用级统计。 */

  return (
    <span className={cx("workflow-build-progress-stat", tone)}>
      <span className={cx("workflow-build-progress-stat-icon")} aria-hidden="true">
        {icon}
      </span>
      <Text strong>{value}</Text>
      <Text type="secondary">{label}</Text>
    </span>
  );
}

function BuildExecutionTaskHeader({
  expanded,
  task,
}: {
  expanded: boolean;
  task: WorkflowBuildExecutionTask;
}): ReactElement {
  /** 渲染可折叠任务卡片的头部摘要。 */

  const status = String(task.status || "pending");
  const title = displayTaskTitle(task);
  const description = displayTaskDescription(task);
  return (
    <div className={cx("workflow-build-task-header-shell")}>
      <div className={cx("workflow-build-task-header", status)}>
        <span className={cx("workflow-build-task-status-icon")} aria-hidden="true">
          {taskStatusIcon(status)}
        </span>
        <div className={cx("workflow-build-task-title")}>
          <Text strong>{title}</Text>
          <Text type="secondary">{description}</Text>
        </div>
        <Tag className={cx("workflow-build-task-status-tag", status)} color={taskStatusColor(status)}>
          {taskStatusText(status)}
        </Tag>
      </div>
      {buildToolActivityPlacement(task, expanded) === "header" && (
        <BuildToolActivity activity={task.activeToolActivity!} />
      )}
    </div>
  );
}

function BuildExecutionTaskDetails({
  task,
}: {
  task: WorkflowBuildExecutionTask;
}): ReactElement {
  /** 展示单个构建任务的定位、失败原因、文件范围和验收点。 */

  const dependencies = taskDependencies(task);
  const paths = [
    ...stringList(task.targetFiles),
    ...stringList(task.target_files),
    ...stringList(task.allowed_paths),
    ...stringList(task.allowedPaths),
  ];
  const acceptance = dedupeLocalizedTaskTexts([
    ...stringList(task.acceptanceCriteria),
    ...stringList(task.acceptance_criteria),
  ], task);
  const failureReason = taskFailureReason(task);
  const failureCategory = taskFailureCategoryText(task.failure_category);
  return (
    <div className={cx("workflow-build-task-details")}>
      <div className={cx("workflow-build-task-detail-grid")}>
        <BuildTaskDetailItem label="任务 ID" value={taskId(task)} />
        <BuildTaskDetailItem label="依赖" value={dependencies.length > 0 ? dependencies.join("、") : "无"} />
      </div>
      {task.status === "failed" && (
        <div className={cx("workflow-build-task-detail-block", "workflow-build-task-failure")}>
          <Text type="secondary">失败原因</Text>
          <Text>{failureReason || "任务执行失败，但后端未返回具体原因。"}</Text>
          {failureCategory && <Tag color="red">{failureCategory}</Tag>}
        </div>
      )}
      {paths.length > 0 && (
        <div className={cx("workflow-build-task-detail-block")}>
          <Text type="secondary">文件范围</Text>
          <div className={cx("workflow-build-task-tags")}>
            {dedupeStrings(paths).map((path) => (
              <Tag key={path}>{path}</Tag>
            ))}
          </div>
        </div>
      )}
      {acceptance.length > 0 && (
        <div className={cx("workflow-build-task-detail-block")}>
          <Text type="secondary">验收点</Text>
          <ul className={cx("workflow-build-task-detail-list")}>
            {acceptance.map((item) => (
              <li key={item}>
                <Text>{item}</Text>
              </li>
            ))}
          </ul>
        </div>
      )}
      {buildToolActivityPlacement(task, true) === "details" && (
        <BuildToolActivity activity={task.activeToolActivity!} />
      )}
    </div>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function buildToolActivityPlacement(
  task: WorkflowBuildExecutionTask,
  expanded: boolean,
): "header" | "details" | undefined {
  /** 决定实时工具活动的唯一渲染位置，任务终态或无活动时不展示。 */

  if (task.status !== "running" || !task.activeToolActivity) return undefined;
  return expanded ? "details" : "header";
}

function BuildToolActivity({
  activity,
}: {
  activity: NonNullable<WorkflowBuildExecutionTask["activeToolActivity"]>;
}): ReactElement {
  /** 以单行高亮样式展示当前任务最新工具操作，不展开原始工具参数。 */

  return (
    <div
      aria-label={activity.message}
      aria-live="polite"
      className={cx("workflow-build-tool-activity", activity.status)}
      title={activity.message}
    >
      <span className={cx("workflow-build-tool-activity-icon")} aria-hidden="true">
        {activity.status === "running" ? <LoadingOutlined spin /> : <CloseCircleOutlined />}
      </span>
      <Text>{activity.message}</Text>
    </div>
  );
}

function BuildTaskDetailItem({
  label,
  value,
}: {
  label: string;
  value: string;
}): ReactElement {
  /** 渲染任务详情中的单个键值项。 */

  return (
    <div className={cx("workflow-build-task-detail-item")}>
      <Text type="secondary">{label}</Text>
      <Text>{value}</Text>
    </div>
  );
}

function taskDependencies(task: WorkflowBuildExecutionTask): string[] {
  /** 读取任务依赖，兼容 dependencies 与 dependsOn 字段。 */

  return dedupeStrings(Array.isArray(task.dependencies)
    ? task.dependencies
    : Array.isArray(task.dependsOn)
      ? task.dependsOn
      : []);
}

function displayTaskTitle(task: WorkflowBuildExecutionTask): string {
  /** 返回任务标题的中文展示文本，避免历史英文任务原样出现在 UI 中。 */

  const title = localizeTaskText(task.title || "", task);
  if (title) return title;
  return `构建任务 ${taskId(task)}`;
}

function displayTaskDescription(task: WorkflowBuildExecutionTask): string {
  /** 返回任务说明的中文展示文本，英文历史数据会被转换为中文摘要。 */

  const description = localizeTaskText(task.description || "", task);
  if (description) return description;
  return "暂无任务说明";
}

function localizeTaskText(value: string, task: WorkflowBuildExecutionTask): string {
  /** 将任务标题、说明和验收点转换为中文；中文原文直接保留。 */

  const text = value.trim();
  if (!text) return "";
  if (containsChinese(text)) return text;
  const exact = exactTaskTranslation(text);
  if (exact) return exact;
  const generated = generatedTaskTranslation(text, task);
  if (generated) return generated;
  return `请完成任务 ${taskId(task)} 的实现与验证。`;
}

function dedupeLocalizedTaskTexts(values: string[], task: WorkflowBuildExecutionTask): string[] {
  /** 先中文化再去重，避免多条英文兜底翻成同一句后重复展示。 */

  return dedupeStrings(
    values
      .map((item) => localizeTaskText(item, task))
      .filter((item) => item.trim()),
  );
}

function containsChinese(value: string): boolean {
  /** 判断文本是否已经包含中文，避免重复加工中文任务内容。 */

  return /[\u4e00-\u9fa5]/.test(value);
}

function exactTaskTranslation(value: string): string {
  /** 翻译已知的任务规划英文模板，覆盖历史会话中常见的任务详情。 */

  const translations: Record<string, string> = {
    "Create Express backend with employee CRUD API endpoints": "创建 Express 员工 CRUD 后端 API 接口",
    "Set up a Node.js + Express server with in-memory storage for employees. Implement endpoints: GET /api/employees (list), GET /api/employees/:employeeId (detail), POST /api/employees (create), PUT /api/employees/:employeeId (update), DELETE /api/employees/:employeeId (delete/mark departed). Use the schemas from employee_api contract. Include a /health endpoint. Server listens on port 8000.": "搭建 Node.js + Express 服务，使用内存存储管理员工数据。实现 GET /api/employees（列表）、GET /api/employees/:employeeId（详情）、POST /api/employees（创建）、PUT /api/employees/:employeeId（更新）、DELETE /api/employees/:employeeId（删除或标记离职），并遵循 employee_api 契约中的 schema。服务需要提供 /health 健康检查接口，并监听 8000 端口。",
    "Server starts and responds to GET /health with 200 OK.": "服务启动后，GET /health 返回 200 OK。",
    "All five employee endpoints return correct responses as per the API contract.": "五个员工接口均按照 API 契约返回正确响应。",
    "Create, read, update, delete operations work correctly on in-memory store.": "基于内存存储的创建、读取、更新、删除流程可正常工作。",
  };
  return translations[value] || "";
}

function generatedTaskTranslation(value: string, task: WorkflowBuildExecutionTask): string {
  /** 按任务文本中的接口、资源和技术栈生成中文说明，兜底处理历史英文任务。 */

  const lowerValue = value.toLowerCase();
  const entityLabel = taskEntityLabel(value);
  const endpoints = taskEndpointTexts(value);
  if (lowerValue.includes("crud") && lowerValue.includes("api")) {
    return `实现${entityLabel}的 CRUD API 接口${endpoints.length > 0 ? `：${endpoints.join("、")}` : ""}。`;
  }
  if (lowerValue.includes("express") && lowerValue.includes("backend")) {
    return `创建 Express 后端服务，完成${entityLabel}相关接口、内存数据存储和健康检查。`;
  }
  if (lowerValue.includes("in-memory")) {
    return `使用内存存储完成${entityLabel}数据的增删改查逻辑。`;
  }
  if (lowerValue.includes("health")) {
    return "服务需要提供 /health 健康检查接口，并返回成功状态。";
  }
  if (endpoints.length > 0) {
    return `实现并验证接口：${endpoints.join("、")}。`;
  }
  if (task.owner === "data_source") {
    return `完成${entityLabel}后端数据与接口实现，并通过相关验证。`;
  }
  if (task.owner === "frontend") {
    return `完成页面功能实现，并通过相关验证。`;
  }
  return "";
}

function taskEntityLabel(value: string): string {
  /** 从英文任务内容推导业务对象的中文名称。 */

  const lowerValue = value.toLowerCase();
  if (lowerValue.includes("employee")) return "员工";
  if (lowerValue.includes("user")) return "用户";
  if (lowerValue.includes("order")) return "订单";
  if (lowerValue.includes("product")) return "商品";
  if (lowerValue.includes("customer")) return "客户";
  return "业务数据";
}

function taskEndpointTexts(value: string): string[] {
  /** 提取任务说明中的 HTTP 接口，生成中文可读接口列表。 */

  const matches = value.match(/\b(GET|POST|PUT|PATCH|DELETE)\s+\/[A-Za-z0-9_/:.-]+/g) || [];
  return dedupeStrings(matches.map((endpoint) => endpoint.replace(/\s+/, " ")));
}

function taskFailureReason(task: WorkflowBuildExecutionTask): string {
  /** 提取失败原因，兼容后端后续扩展的 failure_detail 文本字段。 */

  if (typeof task.failure_reason === "string" && task.failure_reason.trim()) {
    return task.failure_reason.trim();
  }
  const detail = objectValue(task.failure_detail);
  for (const key of ["reason", "message", "agent_note"]) {
    const value = detail[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function taskFailureCategoryText(category: unknown): string {
  /** 将后端失败分类转成中文标签，帮助用户定位后续修复方向。 */

  const value = typeof category === "string" ? category : "";
  const labels: Record<string, string> = {
    runner_crash: "执行器异常",
    runner_protocol_error: "执行协议异常",
    timeout: "执行超时",
    tool_error: "工具调用失败",
    model_error: "模型调用失败",
    sandbox_error: "沙箱权限问题",
    network_error: "网络问题",
    compile_error: "编译失败",
    type_error: "类型检查失败",
    test_failure: "测试失败",
    lint_failure: "代码检查失败",
    runtime_error: "运行时错误",
    acceptance_failed: "验收未通过",
    no_file_changes: "未产生文件变更",
    contract_mismatch: "契约不匹配",
    plan_mismatch: "计划不匹配",
    workspace_snapshot_stale: "工作区快照过期",
    implementation_failure: "实现失败",
  };
  return labels[value] || value;
}

function sortBuildTasksForDisplay(tasks: WorkflowBuildExecutionTask[]): WorkflowBuildExecutionTask[] {
  /** 按用户阅读进度排序任务，让已完成、运行中、失败和待执行形成稳定的执行轨迹。 */

  const originalIndex = new Map(tasks.map((task, index) => [taskId(task), index]));
  return [...tasks].sort((left, right) => {
    const statusDiff = taskStatusRank(left) - taskStatusRank(right);
    if (statusDiff !== 0) return statusDiff;

    const leftTime = taskSortTime(left);
    const rightTime = taskSortTime(right);
    if (leftTime !== rightTime) return leftTime - rightTime;

    return (originalIndex.get(taskId(left)) || 0) - (originalIndex.get(taskId(right)) || 0);
  });
}

function taskStatusRank(task: WorkflowBuildExecutionTask): number {
  /** 返回任务状态展示优先级，完成项沉淀在顶部，未开始项留在底部。 */

  const status = String(task.status || "pending");
  if (status === "completed") return 0;
  if (status === "running") return 1;
  if (status === "failed") return 2;
  if (status === "pending") return 3;
  return 4;
}

function taskSortTime(task: WorkflowBuildExecutionTask): number {
  /** 优先使用调度时间排序，同一批任务再退回到原始顺序。 */

  const taskRecord = task as WorkflowBuildExecutionTask & {
    scheduler?: Record<string, unknown>;
    updated_at?: string;
  };
  const candidates = [
    taskRecord.scheduler?.started_at,
    taskRecord.updated_at,
  ];
  for (const candidate of candidates) {
    if (typeof candidate !== "string") continue;
    const timestamp = Date.parse(candidate);
    if (Number.isFinite(timestamp)) return timestamp;
  }
  return Number.MAX_SAFE_INTEGER;
}

function dedupeStrings(values: string[]): string[] {
  /** 按出现顺序去重字符串，避免文件范围重复展示。 */

  const seen = new Set<string>();
  return values.filter((value) => {
    if (seen.has(value)) return false;
    seen.add(value);
    return true;
  });
}

function taskStatusIcon(status: string): ReactElement {
  /** 将任务状态映射为卡片头部图标。 */

  if (status === "completed") return <CheckCircleOutlined />;
  if (status === "failed") return <CloseCircleOutlined />;
  if (status === "running") return <LoadingOutlined />;
  return <PauseCircleOutlined />;
}

function ClarificationContext({
  clarification,
}: {
  clarification?: WorkflowClarification;
}): ReactElement | null {
  const groups = (clarification?.selection_groups || []).filter(
    (group) => Array.isArray(group.items) && group.items.length > 0,
  );
  const context = clarification?.context;
  if (groups.length === 0 && !context) return null;

  return (
    <div className={cx("workflow-clarification-context")}>
      {groups.map((group, index) => (
        <SelectionGroup group={group} key={`${group.type || group.title}-${index}`} />
      ))}
      {context && <WorkflowContext context={context} />}
    </div>
  );
}

function SelectionGroup({
  group,
}: {
  group: WorkflowClarificationSelectionGroup;
}): ReactElement {
  return (
    <div className={cx("workflow-selection-group")}>
      <Text strong>{group.title || group.type || "候选项"}</Text>
      <ul className={cx("workflow-selection-list")}>
        {(group.items || []).map((item) => (
          <li className={cx("workflow-selection-item")} key={item.id || item.label}>
            <Text>{item.label || item.name || item.id}</Text>
            <ul className={cx("workflow-selection-item-meta")}>
              {item.id && (
                <li>
                  <Text type="secondary">id: </Text>
                  <Text code>{item.id}</Text>
                </li>
              )}
              {item.description && (
                <li>
                  <Text type="secondary">{item.description}</Text>
                </li>
              )}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  );
}

function WorkflowContext({
  context,
}: {
  context: Record<string, unknown>;
}): ReactElement {
  const page = objectValue(context.page);
  const layout = objectValue(context.layout);
  const interactions = stringList(context.interactions);
  const dataSources = objectList(context.data_sources);
  const permissions = stringList(context.permissions);

  return (
    <div className={cx("workflow-page-context")}>
      {Object.keys(page).length > 0 && (
        <div className={cx("workflow-page-context-row")}>
          <Text strong>{stringValue(page.name) || "页面"}</Text>
          <Text type="secondary">
            {stringValue(page.path)}
            {stringValue(page.goal) ? `：${stringValue(page.goal)}` : ""}
          </Text>
        </div>
      )}
      {stringList(layout.structure).length > 0 && (
        <div className={cx("workflow-page-context-row")}>
          <Text type="secondary">布局</Text>
          <Text>{stringList(layout.structure).join("、")}</Text>
        </div>
      )}
      {interactions.length > 0 && (
        <div className={cx("workflow-page-context-row")}>
          <Text type="secondary">交互</Text>
          <Text>{interactions.join("、")}</Text>
        </div>
      )}
      {dataSources.length > 0 && (
        <div className={cx("workflow-page-context-row")}>
          <Text type="secondary">数据源</Text>
          <Text>
            {dataSources
              .map((source) => stringValue(source.name) || stringValue(source.id))
              .filter(Boolean)
              .join("、")}
          </Text>
        </div>
      )}
      {permissions.length > 0 && (
        <div className={cx("workflow-page-context-row")}>
          <Text type="secondary">权限</Text>
          <Text>{permissions.join("、")}</Text>
        </div>
      )}
    </div>
  );
}

function ClarificationQuestionControl({
  disabled,
  onChange,
  question,
  value,
}: {
  disabled?: boolean;
  onChange: (value: WorkflowClarificationAnswer) => void;
  question: WorkflowClarificationQuestion;
  value?: WorkflowClarificationAnswer;
}): ReactElement {
  const options = (question.options || [])
    .filter((option) => option.label)
    .map((option) => ({
      label: option.label || "",
      value: option.value || option.label || "",
    }));
  const optionsWithOther =
    question.allowOther !== false && !options.some((option) => option.value === OTHER_OPTION_VALUE)
      ? [...options, { label: "其他", value: OTHER_OPTION_VALUE }]
      : options;
  const selectedValues = selectedAnswerValues(value);
  const otherSelected = selectedValues.includes(OTHER_OPTION_VALUE);
  const otherValue = answerOtherText(value);
  const setSelectedValues = (selected: string[]): void => {
    onChange({ selected, other: otherValue || undefined });
  };
  const setOtherValue = (other: string): void => {
    onChange({ selected: selectedValues, other });
  };

  if (question.type === "yesno") {
    return (
      <>
        <Radio.Group
          disabled={disabled}
          onChange={(event) => setSelectedValues([String(event.target.value)])}
          value={selectedValues[0]}
        >
          <Radio value="是">是</Radio>
          <Radio value="否">否</Radio>
          {question.allowOther !== false && <Radio value={OTHER_OPTION_VALUE}>其他</Radio>}
        </Radio.Group>
        {otherSelected && <OtherInput disabled={disabled} onChange={setOtherValue} value={otherValue} />}
      </>
    );
  }

  if (question.type === "choice" && optionsWithOther.length > 0) {
    if (question.multiSelect) {
      return (
        <>
          <Checkbox.Group
            disabled={disabled}
            onChange={(checkedValues) => setSelectedValues(checkedValues.map(String))}
            options={optionsWithOther}
            value={selectedValues}
          />
          {otherSelected && <OtherInput disabled={disabled} onChange={setOtherValue} value={otherValue} />}
        </>
      );
    }

    return (
      <>
        <Radio.Group
          disabled={disabled}
          onChange={(event) => setSelectedValues([String(event.target.value)])}
          value={selectedValues[0]}
        >
          {optionsWithOther.map((option) => (
            <Radio key={option.value} value={option.value}>
              {option.label}
            </Radio>
          ))}
        </Radio.Group>
        {otherSelected && <OtherInput disabled={disabled} onChange={setOtherValue} value={otherValue} />}
      </>
    );
  }

  return (
    <TextArea
      autoSize={{ minRows: 2, maxRows: 4 }}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      placeholder={question.placeholder || "请输入你的补充说明"}
      value={typeof value === "string" ? value : ""}
    />
  );
}

function OtherInput({
  disabled,
  onChange,
  value,
}: {
  disabled?: boolean;
  onChange: (value: string) => void;
  value: string;
}): ReactElement {
  return (
    <TextArea
      autoSize={{ minRows: 2, maxRows: 4 }}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      placeholder="请补充其他选择或说明"
      value={value}
    />
  );
}

function clarificationQuestionKey(
  question: WorkflowClarificationQuestion,
  index: number,
): string {
  return question.id || question.header || question.question || String(index);
}

function clarificationAnswerComplete(
  question: WorkflowClarificationQuestion,
  value: WorkflowClarificationAnswer | undefined,
): boolean {
  if (question.type === "choice" || question.type === "yesno") {
    const selected = selectedAnswerValues(value);
    if (selected.length === 0) return false;
    return !selected.includes(OTHER_OPTION_VALUE) || Boolean(answerOtherText(value).trim());
  }
  if (Array.isArray(value)) return value.length > 0;
  return typeof value === "string" && value.trim().length > 0;
}

function selectedAnswerValues(value: WorkflowClarificationAnswer | undefined): string[] {
  if (typeof value === "object" && value && !Array.isArray(value) && "selected" in value) {
    const selected = value.selected;
    return Array.isArray(selected) ? selected.map(String) : [String(selected)];
  }
  if (Array.isArray(value)) return value.map(String);
  return typeof value === "string" && value ? [value] : [];
}

function answerOtherText(value: WorkflowClarificationAnswer | undefined): string {
  return typeof value === "object" && value && !Array.isArray(value) && "other" in value
    ? String(value.other || "")
    : "";
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function objectList(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
    : [];
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map(String).filter((item) => item.trim())
    : [];
}

// eslint-disable-next-line react-refresh/only-export-components
export function workflowOriginalRequest(workflow: WorkflowRunPayload): string {
  const summaryRequest = workflow.summary.request
  if (typeof summaryRequest === "string" && summaryRequest.trim()) {
    return summaryRequest.trim()
  }

  for (const source of [workflow.result, workflow.state]) {
    const requirementSpec = objectValue(source?.requirement_spec);
    const sourceRequest = requirementSpec.source_request;
    if (typeof sourceRequest === "string" && sourceRequest.trim())
      return sourceRequest.trim();
  }

  const resultRequest = workflow.result?.request;
  if (typeof resultRequest === "string" && resultRequest.trim())
    return resultRequest.trim();

  const stateRequest = workflow.state?.request;
  if (typeof stateRequest === "string" && stateRequest.trim())
    return stateRequest.trim();

  const startedEvent = workflow.events.find(
    (event) => event.type === "workflow.run.started",
  );
  const eventRequest = startedEvent?.data?.request;
  return typeof eventRequest === "string" ? eventRequest.trim() : "";
}

/** 根据当前结构化交互生成恢复 Workflow 所需的用户可见消息。 */
// eslint-disable-next-line react-refresh/only-export-components
export function buildClarificationContinuationMessage(
  workflow: WorkflowRunPayload,
  answers: ClarificationAnswers,
): string {
  const clarification = workflowClarification(workflow);
  const acceptanceMessage = pageAcceptanceContinuationMessage(clarification, answers);
  if (acceptanceMessage) return acceptanceMessage;
  if (clarification?.mode === 'detail_review' && answers.detail_review) {
    const submission = answers.detail_review;
    if (
      typeof submission === 'object' &&
      !Array.isArray(submission) &&
      'review_status' in submission
    ) {
      return '已整体审阅并确认全部页面和数据源设计，请合并本次结构化修改后继续。';
    }
  }
  if (clarification?.mode === 'detail_review' && answers.entity_design) {
    const actionMessage = entityDesignActionContinuationMessage(answers.entity_design);
    if (actionMessage) return actionMessage;
  }
  const questions = clarification?.questions || [];
  const originalRequest = workflowOriginalRequest(workflow);
  if (!originalRequest || questions.length === 0) return "";

  const answerLines = questions
    .map((question, index) => {
      const key = clarificationQuestionKey(question, index);
      const value = answers[key];
      const answer = clarificationAnswerText(value);
      if (!answer || !String(answer).trim()) return "";
      return `- ${
        question.header || question.dimension || `问题${index + 1}`
      }：${question.question || "请补充需求细节。"}\n  回答：${answer}`;
    })
    .filter(Boolean);

  if (answerLines.length === 0) return "";

  return answerLines.join("\n");
}

function clarificationAnswerText(value: WorkflowClarificationAnswer | undefined): string {
  if (typeof value === "object" && value && !Array.isArray(value) && "selected" in value) {
    const selected = selectedAnswerValues(value).filter((item) => item !== OTHER_OPTION_VALUE);
    const parts = selected.length > 0 ? [`已选：${selected.join("、")}`] : [];
    const other = answerOtherText(value).trim();
    if (other) parts.push(`其他补充：${other}`);
    return parts.join("；");
  }
  if (Array.isArray(value)) return value.join("、");
  return typeof value === "string" ? value : "";
}

// 从 Workflow payload 的多个位置读取待确认载荷，兼容流式快照、最终结果和自定义事件。
export function workflowClarification(
  workflow: WorkflowRunPayload,
): WorkflowClarification | undefined {
  const fromSummary = workflow.summary.clarification;
  if (fromSummary && typeof fromSummary === "object") return fromSummary;

  const stateClarification = workflow.state?.clarification;
  if (stateClarification && typeof stateClarification === "object") {
    return stateClarification as WorkflowClarification;
  }

  const resultClarification = workflow.result?.clarification;
  if (resultClarification && typeof resultClarification === "object") {
    return resultClarification as WorkflowClarification;
  }

  const clarificationEvent = workflow.events
    .slice()
    .reverse()
    .find((event) => {
      const detail = event.data?.detail;
      return Boolean(
        detail && typeof detail === "object" && "clarification" in detail,
      );
    });
  const eventClarification = clarificationEvent?.data?.detail;
  if (
    eventClarification &&
    typeof eventClarification === "object" &&
    "clarification" in eventClarification
  ) {
    const clarification = (eventClarification as { clarification?: unknown })
      .clarification;
    if (clarification && typeof clarification === "object") {
      return clarification as WorkflowClarification;
    }
  }

  return undefined;
}

// 提取高危数据库审批载荷：必须是 agent_approval 模式且包含可执行的 SQL 语句。
function workflowDatabaseApproval(
  clarification?: WorkflowClarification,
): { approval: ToolApproval; statements: string[] } | undefined {
  if (!clarification || clarification.mode !== "agent_approval") return undefined;
  const approval = clarification.approval;
  if (!approval || typeof approval !== "object" || Array.isArray(approval)) {
    return undefined;
  }
  const plan = clarification.database_change_plan;
  const planRecord =
    plan && typeof plan === "object" && !Array.isArray(plan)
      ? (plan as Record<string, unknown>)
      : {};
  const rawStatements = planRecord.statements;
  const statements = Array.isArray(rawStatements)
    ? rawStatements.map(String).filter(Boolean)
    : [];
  if (statements.length === 0) return undefined;
  return { approval: approval as ToolApproval, statements };
}

function workflowConfirmationArtifact(
  workflow: WorkflowRunPayload,
  clarification?: WorkflowClarification,
): WorkflowConfirmationArtifact | undefined {
  const expectedArtifactId = clarification?.mode === 'requirement_spec_confirmation'
    ? 'requirement_spec'
    : clarification?.mode === 'project_plan_confirmation'
      ? 'project_plan'
      : undefined;
  const artifact = workflow.confirmationArtifact;

  if (
    !expectedArtifactId ||
    clarification?.status !== 'requires_user_input' ||
    !artifact ||
    artifact.id !== expectedArtifactId ||
    artifact.format !== 'markdown' ||
    !artifact.content.trim()
  ) {
    return undefined;
  }

  return artifact;
}

function taskId(task: WorkflowBuildExecutionTask): string {
  /** 读取 task 的稳定 ID，兼容 task_id 字段。 */

  return task.id || task.task_id || "unknown-task";
}

function numberValue(value: unknown, fallback: number): number {
  /** 将后端计数字段规整为有限数字。 */

  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function taskStatusColor(status: string): string {
  /** 将任务状态映射为 Ant Design 标签颜色。 */

  if (status === "completed") return "green";
  if (status === "failed") return "red";
  if (status === "running") return "blue";
  return "default";
}

function taskStatusText(status: string): string {
  /** 将任务状态映射为用户可读中文文案。 */

  if (status === "completed") return "完成";
  if (status === "failed") return "失败";
  if (status === "running") return "运行中";
  return "待执行";
}

/** 将工作流状态映射为符合工作区语义色的标签颜色。 */
function workflowStatusColor(status: string): string {
  if (status === "completed" || status === "passed") return "green";
  if (status === "failed" || status === "error") return "red";
  if (status === "requires_user_input") return "gold";
  if (status === "running") return "purple";
  return "default";
}

function workflowStatusText(status: string): string {
  /** 将工作流状态映射为中文标签。 */

  if (status === "completed" || status === "passed") return "完成";
  if (status === "failed" || status === "error") return "失败";
  if (status === "requires_user_input") return "待确认";
  if (status === "running") return "运行中";
  return status || "未知";
}
