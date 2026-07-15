import { Button, Checkbox, Input, Radio, Tag, Typography } from "antd";
import type { ReactElement } from "react";
import { useState } from "react";
import type {
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
    <div className={cx("workflow-run-card")}>
      <div className={cx("workflow-run-header")}>
        <Text strong>Workflow Run</Text>
        <Tag color={workflowStatusColor(status)}>{status}</Tag>
      </div>
      {workflow.summary.message && (
        <Text>{String(workflow.summary.message)}</Text>
      )}
      {Object.keys(artifacts).length > 0 && (
        <div className={cx("workflow-artifacts")}>
          <Text type="secondary">产物</Text>
          {Object.entries(artifacts).map(([name, path]) => (
            <Text code key={name}>
              {name}: {path}
            </Text>
          ))}
        </div>
      )}
      {(clarificationQuestions.length > 0 || detailReview) && (
        <div className={cx("workflow-clarification")}>
          <div className={cx("workflow-clarification-header")}>
            <Text type="secondary">待确认事项</Text>
            <Tag
              color={
                clarification?.status === "requires_user_input"
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

function workflowStatusColor(status: string): string {
  if (status === "completed" || status === "passed") return "green";
  if (status === "failed" || status === "error") return "red";
  if (status === "requires_user_input") return "gold";
  if (status === "running") return "blue";
  return "default";
}
