import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  PauseCircleOutlined,
} from "@ant-design/icons";
import { Button, Checkbox, Collapse, Input, Progress, Radio, Tag, Typography } from "antd";
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
  const targetLabel = scope.type === "page" ? "页面" : scope.type === "data_source" ? "数据源" : "应用";
  const targetId = scope.targetId || executionSlice.target_unit_ids?.[0] || "";
  const progressStatus = failed > 0 ? "exception" : completed === total && total > 0 ? "success" : "active";
  const displayTasks = sortBuildTasksForDisplay(tasks);
  const runningTaskKeys = displayTasks
    .filter((task) => task.status === "running")
    .map(taskId);

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
            className={cx("workflow-build-task-list")}
            defaultActiveKey={runningTaskKeys}
            expandIconPosition="right"
          >
          {displayTasks.map((task) => (
            <Collapse.Panel
              className={cx("workflow-build-task-panel", task.status || "pending")}
              header={<BuildExecutionTaskHeader task={task} />}
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
          <Text className={cx("workflow-run-name")} strong>Build Run</Text>
        </div>
        <Tag className={cx("workflow-run-status")} color={workflowStatusColor(status)}>
          {status}
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
  task,
}: {
  task: WorkflowBuildExecutionTask;
}): ReactElement {
  /** 渲染可折叠任务卡片的头部摘要。 */

  const status = String(task.status || "pending");
  const description = task.description || task.unit_id || taskId(task);
  return (
    <div className={cx("workflow-build-task-header", status)}>
      <span className={cx("workflow-build-task-status-icon")} aria-hidden="true">
        {taskStatusIcon(status)}
      </span>
      <div className={cx("workflow-build-task-title")}>
        <Text strong>{task.title || taskId(task)}</Text>
        <Text type="secondary">{description}</Text>
      </div>
      <Tag className={cx("workflow-build-task-status-tag", status)} color={taskStatusColor(status)}>
        {taskStatusText(status)}
      </Tag>
    </div>
  );
}

function BuildExecutionTaskDetails({
  task,
}: {
  task: WorkflowBuildExecutionTask;
}): ReactElement {
  /** 展示单个构建任务的依赖、执行归属和文件范围等细节。 */

  const dependencies = taskDependencies(task);
  const paths = [
    ...stringList(task.targetFiles),
    ...stringList(task.target_files),
    ...stringList(task.allowed_paths),
    ...stringList(task.allowedPaths),
  ];
  const acceptance = stringList(task.acceptanceCriteria);
  const sourceRefs = objectValue(task.source_refs);
  return (
    <div className={cx("workflow-build-task-details")}>
      <div className={cx("workflow-build-task-detail-grid")}>
        <BuildTaskDetailItem label="任务 ID" value={taskId(task)} />
        <BuildTaskDetailItem label="Unit" value={task.unit_id || "application:root"} />
        <BuildTaskDetailItem label="执行方" value={task.owner || "未指定"} />
        <BuildTaskDetailItem label="依赖" value={dependencies.length > 0 ? dependencies.join("、") : "无"} />
      </div>
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
      {Object.keys(sourceRefs).length > 0 && (
        <div className={cx("workflow-build-task-detail-block")}>
          <Text type="secondary">来源引用</Text>
          <div className={cx("workflow-build-task-detail-list")}>
            {Object.entries(sourceRefs).map(([key, value]) => (
              <Text key={key}>
                {key}: {String(value)}
              </Text>
            ))}
          </div>
        </div>
      )}
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

  return Array.isArray(task.dependencies)
    ? task.dependencies
    : Array.isArray(task.dependsOn)
      ? task.dependsOn
      : [];
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

// eslint-disable-next-line react-refresh/only-export-components
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
