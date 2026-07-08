import { Button, Checkbox, Input, Radio, Tag, Typography } from "antd";
import type { ReactElement } from "react";
import { useState } from "react";
import type {
  WorkflowClarification,
  WorkflowClarificationQuestion,
  WorkflowRunPayload,
} from "../../typings";
import { cx } from "../../utils";

const { Text } = Typography;
const { TextArea } = Input;

export type ClarificationAnswers = Record<string, string | string[]>;

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
  const recentEvents = workflow.events.slice(-8);
  const clarification = workflowClarification(workflow);
  const clarificationQuestions = clarification?.questions || [];
  const [answers, setAnswers] = useState<ClarificationAnswers>({});
  const canSubmitClarification =
    clarification?.status === "requires_user_input" &&
    clarificationQuestions.length > 0 &&
    clarificationQuestions.every((question, index) =>
      clarificationAnswerComplete(
        answers[clarificationQuestionKey(question, index)],
      ),
    );
  const updateAnswer = (key: string, value: string | string[]): void => {
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
      {clarificationQuestions.length > 0 && (
        <div className={cx("workflow-clarification")}>
          <div className={cx("workflow-clarification-header")}>
            <Text type="secondary">待确认需求</Text>
            <Tag
              color={
                clarification?.status === "requires_user_input"
                  ? "gold"
                  : "default"
              }
            >
              {clarificationQuestions.length}
            </Tag>
          </div>
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
        </div>
      )}
      {recentEvents.length > 0 && (
        <div className={cx("workflow-events")}>
          <Text type="secondary">最近事件</Text>
          {recentEvents.map((event, index) => (
            <div
              className={cx("workflow-event")}
              key={`${event.type}-${event.timestamp}-${index}`}
            >
              <Tag>{event.nodeName || event.type}</Tag>
              <Text>{event.message || event.status || event.type}</Text>
            </div>
          ))}
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
  onChange: (value: string | string[]) => void;
  question: WorkflowClarificationQuestion;
  value?: string | string[];
}): ReactElement {
  const options = (question.options || [])
    .filter((option) => option.label)
    .map((option) => ({
      label: option.label || "",
      value: option.label || "",
    }));

  if (question.type === "yesno") {
    return (
      <Radio.Group
        disabled={disabled}
        onChange={(event) => onChange(String(event.target.value))}
        value={typeof value === "string" ? value : undefined}
      >
        <Radio value="是">是</Radio>
        <Radio value="否">否</Radio>
      </Radio.Group>
    );
  }

  if (question.type === "choice" && options.length > 0) {
    if (question.multiSelect) {
      return (
        <Checkbox.Group
          disabled={disabled}
          onChange={(checkedValues) => onChange(checkedValues.map(String))}
          options={options}
          value={Array.isArray(value) ? value : []}
        />
      );
    }

    return (
      <Radio.Group
        disabled={disabled}
        onChange={(event) => onChange(String(event.target.value))}
        value={typeof value === "string" ? value : undefined}
      >
        {options.map((option) => (
          <Radio key={option.value} value={option.value}>
            {option.label}
          </Radio>
        ))}
      </Radio.Group>
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

function clarificationQuestionKey(
  question: WorkflowClarificationQuestion,
  index: number,
): string {
  return question.id || question.header || question.question || String(index);
}

function clarificationAnswerComplete(
  value: string | string[] | undefined,
): boolean {
  if (Array.isArray(value)) return value.length > 0;
  return typeof value === "string" && value.trim().length > 0;
}

export function workflowOriginalRequest(workflow: WorkflowRunPayload): string {
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
  const questions = clarification?.questions || [];
  const originalRequest = workflowOriginalRequest(workflow);
  if (!originalRequest || questions.length === 0) return "";

  const answerLines = questions
    .map((question, index) => {
      const key = clarificationQuestionKey(question, index);
      const value = answers[key];
      const answer = Array.isArray(value) ? value.join("、") : value;
      if (!answer || !String(answer).trim()) return "";
      return `- ${
        question.header || question.dimension || `问题${index + 1}`
      }：${question.question || "请补充需求细节。"}\n  回答：${answer}`;
    })
    .filter(Boolean);

  if (answerLines.length === 0) return "";

  return [
    "请基于原始需求和以下用户补充确认，继续生成需求文档并推进后续 workflow。",
    "",
    "原始需求：",
    originalRequest,
    "",
    "用户补充确认：",
    ...answerLines,
  ].join("\n");
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

  const requirementsEvent = workflow.events
    .slice()
    .reverse()
    .find((event) => event.nodeName === "requirements");
  const eventClarification = requirementsEvent?.data?.detail;
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

function workflowStatusColor(status: string): string {
  if (status === "completed" || status === "passed") return "green";
  if (status === "failed" || status === "error") return "red";
  if (status === "requires_user_input") return "gold";
  if (status === "running") return "blue";
  return "default";
}
