import { Button, Checkbox, Input, Progress, Radio, Tag, Typography } from "antd";
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
import ConfirmationArtifact from './ConfirmationArtifact';
import DetailReview from './DetailReview';
import './WorkflowRunCard.less';

const { Text } = Typography;
const { TextArea } = Input;

const OTHER_OPTION_VALUE = '__other__';

export type ClarificationAnswers = WorkflowClarificationAnswers;

type WorkflowRunCardProps = {
  disabled?: boolean;
  onSubmitClarification?: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers,
  ) => void;
  workflow: WorkflowRunPayload;
};

export default function WorkflowRunCard({
  disabled,
  onSubmitClarification,
  workflow,
}: WorkflowRunCardProps): ReactElement {
  const status = String(workflow.summary.status || "unknown");
  const artifacts = workflow.summary.artifacts || {};
  const buildExecutionSlice = workflowBuildExecutionSlice(workflow);
  const clarification = workflowClarification(workflow);
  const confirmationArtifact = workflowConfirmationArtifact(workflow, clarification);
  const clarificationQuestions = clarification?.questions || [];
  const detailReview = clarification?.mode === 'detail_review'
    ? clarification.review
    : undefined;
  const confirmationItemCount = detailReview
    ? (detailReview.pages?.length || 0) + (detailReview.data_sources?.length || 0)
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
            <Text className={cx("workflow-run-name")} strong>Workflow Run</Text>
          </div>
        </div>
        <Tag className={cx("workflow-run-status")} color={workflowStatusColor(status)}>
          {status}
        </Tag>
      </div>
      {workflow.summary.message && (
        <div className={cx("workflow-run-message")}>
          <Text>{String(workflow.summary.message)}</Text>
        </div>
      )}
      {Object.keys(artifacts).length > 0 && (
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
      {buildExecutionSlice && (
        <BuildExecutionSliceProgress executionSlice={buildExecutionSlice} />
      )}
      {(clarificationQuestions.length > 0 || detailReview) && (
        <div className={cx("workflow-clarification")}>
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
          {detailReview ? (
            <DetailReview
              disabled={disabled}
              message={clarification?.message}
              onConfirm={(submission) => onSubmitClarification?.(
                workflow,
                { detail_review: submission },
              )}
              review={detailReview}
            />
          ) : (
            <>
          {confirmationArtifact && (
            <ConfirmationArtifact artifact={confirmationArtifact} />
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

function BuildExecutionSliceProgress({
  executionSlice,
}: {
  executionSlice: WorkflowBuildExecutionSlice;
}): ReactElement | null {
  /** 展示当前页面或数据源范围的构建进度，不做应用级汇总。 */

  const scope = executionSlice.scope;
  if (!scope || scope.type === "application") return null;
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
  const reused = numberValue(summary.reused, executionSlice.reusable_task_ids?.length || 0);
  const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
  const targetLabel = scope.type === "page" ? "页面" : "数据源";
  const targetId = scope.targetId || executionSlice.target_unit_ids?.[0] || "";

  return (
    <div className={cx("workflow-build-progress")}>
      <div className={cx("workflow-build-progress-header")}>
        <div>
          <Text strong>{targetLabel}生成进度</Text>
          <Text type="secondary">
            {targetId ? `当前范围：${targetId}` : "当前范围"}
          </Text>
        </div>
        <Tag color={failed > 0 ? "red" : completed === total && total > 0 ? "green" : "blue"}>
          {completed}/{total}
        </Tag>
      </div>
      <Progress
        percent={percent}
        showInfo={false}
        status={failed > 0 ? "exception" : completed === total && total > 0 ? "success" : "active"}
      />
      <div className={cx("workflow-build-progress-stats")}>
        <BuildProgressStat label="已完成" value={completed} />
        <BuildProgressStat label="运行中" value={numberValue(summary.running, 0)} />
        <BuildProgressStat label="待执行" value={numberValue(summary.pending, 0)} />
        <BuildProgressStat label="已复用" value={reused} />
        <BuildProgressStat label="失败" tone={failed > 0 ? "danger" : undefined} value={failed} />
      </div>
      {tasks.length > 0 && (
        <div className={cx("workflow-build-task-list")}>
          {tasks.map((task) => (
            <BuildExecutionTaskRow key={taskId(task)} task={task} />
          ))}
        </div>
      )}
    </div>
  );
}

function BuildProgressStat({
  label,
  tone,
  value,
}: {
  label: string;
  tone?: "danger";
  value: number;
}): ReactElement {
  /** 渲染当前构建范围内的单项计数，避免上升到应用级统计。 */

  return (
    <span className={cx("workflow-build-progress-stat", tone)}>
      <Text type="secondary">{label}</Text>
      <Text strong>{value}</Text>
    </span>
  );
}

function BuildExecutionTaskRow({
  task,
}: {
  task: WorkflowBuildExecutionTask;
}): ReactElement {
  /** 渲染当前页面生成闭包内的单个任务状态。 */

  const dependencies = Array.isArray(task.dependencies)
    ? task.dependencies
    : Array.isArray(task.dependsOn)
      ? task.dependsOn
      : [];
  return (
    <div className={cx("workflow-build-task-row", task.status || "pending")}>
      <div className={cx("workflow-build-task-main")}>
        <Tag color={taskStatusColor(String(task.status || "pending"))}>
          {taskStatusText(String(task.status || "pending"))}
        </Tag>
        <div>
          <Text>{task.title || task.description || taskId(task)}</Text>
          <Text type="secondary">
            {task.unit_id || "application:root"}
            {task.owner ? ` · ${task.owner}` : ""}
          </Text>
        </div>
      </div>
      {dependencies.length > 0 && (
        <Text className={cx("workflow-build-task-deps")} type="secondary">
          依赖：{dependencies.join("、")}
        </Text>
      )}
    </div>
  );
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

export function workflowOriginalRequest(workflow: WorkflowRunPayload): string {
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

export function buildClarificationContinuationMessage(
  workflow: WorkflowRunPayload,
  answers: ClarificationAnswers,
): string {
  const clarification = workflowClarification(workflow);
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

function workflowClarification(
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

function workflowBuildExecutionSlice(
  workflow: WorkflowRunPayload,
): WorkflowBuildExecutionSlice | undefined {
  /** 从 AG-UI state/result 中读取当前页面或数据源执行切片。 */

  const candidates = [
    workflow.state?.buildExecutionSlice,
    workflow.state?.build_execution_slice,
    workflow.result?.buildExecutionSlice,
    workflow.result?.build_execution_slice,
  ];
  const slice = candidates.find((candidate) => candidate && typeof candidate === "object");
  if (!slice || typeof slice !== "object") return undefined;
  return slice as WorkflowBuildExecutionSlice;
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
