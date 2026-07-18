import { CheckCircleOutlined } from "@ant-design/icons";
import { Alert, Button, Collapse, Input, Tag, Typography } from "antd";
import type { ReactElement } from "react";
import { useMemo, useState } from "react";
import type {
  WorkflowDetailReview,
  WorkflowDetailReviewSubmission,
  WorkflowDetailReviewTarget,
} from "../../../../typings";
import { cx } from "../../../../utils";

const { Panel } = Collapse;
const { Text } = Typography;
const { TextArea } = Input;

type DetailReviewProps = {
  disabled?: boolean;
  message?: string;
  onConfirm: (submission: WorkflowDetailReviewSubmission) => void;
  review: WorkflowDetailReview;
};

export default function DetailReview({
  disabled,
  message,
  onConfirm,
  review,
}: DetailReviewProps): ReactElement {
  const targets = useMemo(
    () => [...(review.pages || []), ...(review.data_sources || [])],
    [review],
  );
  const [changes, setChanges] = useState<
    Record<string, Record<string, unknown>>
  >({});
  const [overallNote, setOverallNote] = useState("");
  const missingSelectedPagePlan = Boolean(
    review.summary?.missingSelectedPagePlan,
  );

  // 记录单个审核对象的字段改动，并保留同对象此前已编辑的内容。
  const updateField = (
    target: WorkflowDetailReviewTarget,
    field: string,
    value: unknown,
  ): void => {
    setChanges((current) => ({
      ...current,
      [target.target_id]: {
        ...(current[target.target_id] || {}),
        [field]: value,
      },
    }));
  };

  // 汇总实际发生的修改并提交本轮细节确认。
  const confirm = (): void => {
    onConfirm({
      review_status: "confirmed",
      target_changes: targets
        .filter(
          (target) => Object.keys(changes[target.target_id] || {}).length > 0,
        )
        .map((target) => ({
          target_type: target.target_type,
          target_id: target.target_id,
          changes: changes[target.target_id],
        })),
      overall_note: overallNote.trim() || undefined,
    });
  };

  return (
    <div className={cx("workflow-detail-review")}>
      <div className={cx("workflow-detail-review-summary")}>
        <Tag>页面 {review.summary?.page_count || 0}</Tag>
        <Tag>数据源 {review.summary?.data_source_count || 0}</Tag>
        <Tag>API 契约 {review.summary?.api_contract_count || 0}</Tag>
        <Text type="secondary">
          本轮生成的设计如下；只需展开需要调整的对象。
        </Text>
      </div>
      {missingSelectedPagePlan || targets.length === 0 ? (
        <Alert
          message={
            message ||
            `页面 ${review.summary?.selectedPageId || ""} 还没有生成细节设计，请先生成该页面的 plan。`
          }
          showIcon
          type="warning"
        />
      ) : (
        <Collapse bordered={false}>
          {targets.map((target) => (
            <Panel
              header={
                <div className={cx("workflow-detail-review-title")}>
                  <Tag>{target.target_type === "page" ? "页面" : "数据源"}</Tag>
                  <Text strong>{target.name || target.target_id}</Text>
                  <Text type="secondary">{target.target_id}</Text>
                  {changes[target.target_id] && <Tag color="purple">已修改</Tag>}
                </div>
              }
              key={`${target.target_type}:${target.target_id}`}
            >
              {target.target_type === "page" ? (
                <PageReviewEditor
                  changes={changes[target.target_id] || {}}
                  disabled={disabled}
                  onChange={(field, value) => updateField(target, field, value)}
                  target={target}
                />
              ) : (
                <DataSourceReviewEditor
                  changes={changes[target.target_id] || {}}
                  disabled={disabled}
                  onChange={(field, value) => updateField(target, field, value)}
                  target={target}
                />
              )}
            </Panel>
          ))}
        </Collapse>
      )}
      <div className={cx("workflow-detail-review-actions")}>
        <label className={cx("workflow-detail-review-note")}>
          <Text strong>整体补充说明</Text>
          <TextArea
            autoSize={{ minRows: 2, maxRows: 4 }}
            disabled={disabled}
            onChange={(event) => setOverallNote(event.target.value)}
            placeholder="可选：补充跨页面规则、统一交互或其他全局调整"
            value={overallNote}
          />
        </label>
        <Button
          disabled={disabled || missingSelectedPagePlan || targets.length === 0}
          icon={<CheckCircleOutlined />}
          onClick={confirm}
          size="large"
          type="primary"
        >
          确认全部设计并继续
        </Button>
      </div>
    </div>
  );
}

function PageReviewEditor({
  changes,
  disabled,
  onChange,
  target,
}: ReviewEditorProps): ReactElement {
  const layout = objectValue(target.basic_layout);
  return (
    <div className={cx("workflow-detail-review-fields")}>
      <ReviewTextField
        disabled={disabled}
        label="页面目标"
        onChange={(value) => onChange("page_goal", value)}
        value={stringChange(changes.page_goal, target.page_goal)}
      />
      <ReviewListField
        disabled={disabled}
        label="基本布局"
        onChange={(value) =>
          onChange("basic_layout", { ...layout, structure: value })
        }
        value={listChange(
          objectValue(changes.basic_layout).structure,
          layout.structure,
        )}
      />
      <ReviewSummaryField
        disabled={disabled}
        label="页面布局设计"
        onChange={(value) =>
          onChange(
            "layout_design",
            parseLayoutDesignSummary(value, target.layout_design),
          )
        }
        value={layoutDesignSummary(
          objectChange(changes.layout_design, target.layout_design),
          target.basic_layout,
        )}
      />
      <ReviewListField
        disabled={disabled}
        label="页面交互"
        onChange={(value) => onChange("interactions", value)}
        value={listChange(changes.interactions, target.interactions)}
      />
      <ReviewSummaryField
        disabled={disabled}
        label="主要操作交互"
        onChange={(value) =>
          onChange("operation_interactions", parseOperationSummary(value))
        }
        value={operationSummary(
          recordListChange(
            changes.operation_interactions,
            target.operation_interactions,
          ),
        )}
      />
      <ReviewSummaryField
        disabled={disabled}
        label="状态反馈"
        onChange={(value) =>
          onChange("state_feedback", parseStateFeedbackSummary(value))
        }
        value={stateFeedbackSummary(
          recordListChange(changes.state_feedback, target.state_feedback),
        )}
      />
      <ReviewSummaryField
        disabled={disabled}
        label="API 依赖"
        onChange={(value) =>
          onChange("api_dependencies", parseApiDependencySummary(value))
        }
        value={apiDependencySummary(
          recordListChange(changes.api_dependencies, target.api_dependencies),
        )}
      />
      <ReviewSummaryField
        disabled={disabled}
        label="响应字段绑定"
        onChange={(value) =>
          onChange("response_bindings", parseResponseBindingSummary(value))
        }
        value={responseBindingSummary(
          recordListChange(changes.response_bindings, target.response_bindings),
        )}
      />
      <ReviewSummaryField
        disabled={disabled}
        label="页面跳转与依赖"
        onChange={(value) =>
          onChange("page_navigation", parseNavigationSummary(value))
        }
        value={navigationSummary(
          recordListChange(changes.page_navigation, target.page_navigation),
        )}
      />
      <ReviewListField
        disabled={disabled}
        label="页面权限"
        onChange={(value) => onChange("permissions", value)}
        value={listChange(changes.permissions, target.permissions)}
      />
      <ReviewSummaryField
        disabled={disabled}
        label="操作可见性"
        onChange={(value) =>
          onChange(
            "operation_visibility",
            parseOperationVisibilitySummary(value),
          )
        }
        value={operationVisibilitySummary(
          recordListChange(
            changes.operation_visibility,
            target.operation_visibility,
          ),
        )}
      />
      <ReviewListField
        disabled={disabled}
        label="验收标准"
        onChange={(value) => onChange("acceptance_criteria", value)}
        value={listChange(
          changes.acceptance_criteria,
          target.acceptance_criteria,
        )}
      />
    </div>
  );
}

function DataSourceReviewEditor({
  changes,
  disabled,
  onChange,
  target,
}: ReviewEditorProps): ReactElement {
  return (
    <div className={cx("workflow-detail-review-fields")}>
      <ReviewTextField
        disabled={disabled}
        label="数据源类型"
        onChange={(value) => onChange("source_type", value)}
        value={stringChange(changes.source_type, target.source_type)}
      />
      <ReviewListField
        disabled={disabled}
        label="实体"
        onChange={(value) => onChange("entities", value)}
        value={listChange(changes.entities, target.entities)}
      />
      <ReviewListField
        disabled={disabled}
        label="Schema 引用"
        onChange={(value) => onChange("schema_refs", value)}
        value={listChange(changes.schema_refs, target.schema_refs)}
      />
      <ReviewListField
        disabled={disabled}
        label="实体关系"
        onChange={(value) => onChange("relationships", value)}
        value={listChange(changes.relationships, target.relationships)}
      />
      <ReviewListField
        disabled={disabled}
        label="校验规则"
        onChange={(value) => onChange("validation_rules", value)}
        value={listChange(changes.validation_rules, target.validation_rules)}
      />
      <ReviewTextField
        disabled={disabled}
        label="Seed / Mock 策略"
        onChange={(value) => onChange("seed_strategy", value)}
        value={stringChange(changes.seed_strategy, target.seed_strategy)}
      />
      <ReviewListField
        disabled={disabled}
        label="验收标准"
        onChange={(value) => onChange("acceptance_criteria", value)}
        value={listChange(
          changes.acceptance_criteria,
          target.acceptance_criteria,
        )}
      />
      <ReviewListField
        disabled={disabled}
        label="API 契约"
        onChange={(value) =>
          onChange(
            "api_contracts",
            value.map((id) => ({ id })),
          )
        }
        value={contractIdChange(changes.api_contracts, target.api_contracts)}
      />
      <ReviewSummaryField
        disabled={disabled}
        label="依赖页面"
        onChange={(value) =>
          onChange("dependent_pages", parseDependentPages(value))
        }
        value={dependentPagesSummary(
          recordListChange(changes.dependent_pages, target.dependent_pages),
        )}
      />
    </div>
  );
}

type ReviewEditorProps = {
  changes: Record<string, unknown>;
  disabled?: boolean;
  onChange: (field: string, value: unknown) => void;
  target: WorkflowDetailReviewTarget;
};

function ReviewTextField({
  disabled,
  label,
  onChange,
  value,
}: FieldProps<string>): ReactElement {
  return (
    <label className={cx("workflow-detail-review-field")}>
      <Text type="secondary">{label}</Text>
      <TextArea
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        value={value}
      />
    </label>
  );
}

function ReviewListField({
  disabled,
  label,
  onChange,
  value,
}: FieldProps<string[]>): ReactElement {
  return (
    <label className={cx("workflow-detail-review-field")}>
      <Text type="secondary">{label}</Text>
      <TextArea
        autoSize={{ minRows: 2, maxRows: 5 }}
        disabled={disabled}
        onChange={(event) => onChange(splitLines(event.target.value))}
        value={value.join("\n")}
      />
    </label>
  );
}

function ReviewSummaryField({
  disabled,
  label,
  onChange,
  value,
}: FieldProps<string>): ReactElement {
  return (
    <label className={cx("workflow-detail-review-field")}>
      <Text type="secondary">{label}</Text>
      <TextArea
        autoSize={{ minRows: 3, maxRows: 8 }}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        value={value}
      />
    </label>
  );
}

type FieldProps<T> = {
  disabled?: boolean;
  label: string;
  onChange: (value: T) => void;
  value: T;
};

function splitLines(value: string): string[] {
  return value
    .split(/\n|，|；/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function listChange(changed: unknown, initial: unknown): string[] {
  return Array.isArray(changed)
    ? changed.map(String)
    : Array.isArray(initial)
    ? initial.map(String)
    : [];
}

function stringChange(changed: unknown, initial: unknown): string {
  return typeof changed === "string"
    ? changed
    : typeof initial === "string"
    ? initial
    : "";
}

function objectChange(
  changed: unknown,
  initial: unknown,
): Record<string, unknown> {
  return isRecord(changed) ? changed : objectValue(initial);
}

function recordListChange(
  changed: unknown,
  initial: unknown,
): Array<Record<string, unknown>> {
  const changedItems = recordItems(changed);
  return changedItems.length > 0 || Array.isArray(changed)
    ? changedItems
    : recordItems(initial);
}

function objectValue(value: unknown): Record<string, unknown> {
  return isRecord(value) ? (value as Record<string, unknown>) : {};
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function recordItems(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === "object" && !Array.isArray(item),
      )
    : [];
}

function stringItems(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
}

function summaryLines(value: string): string[] {
  return value
    .split(/\n+/)
    .map((item) => item.trim())
    .filter((item) => item && item !== "无");
}

function splitPair(value: string, separator: string): [string, string] {
  const index = value.indexOf(separator);
  return index >= 0
    ? [
        value.slice(0, index).trim(),
        value.slice(index + separator.length).trim(),
      ]
    : [value.trim(), ""];
}

function contractIds(value: unknown): string[] {
  return recordItems(value)
    .map((item) => String(item.id || ""))
    .filter(Boolean);
}

function contractIdChange(changed: unknown, initial: unknown): string[] {
  return Array.isArray(changed) ? contractIds(changed) : contractIds(initial);
}

function layoutDesignSummary(value: unknown, fallbackLayout: unknown): string {
  const layout = objectValue(value);
  const fallback = objectValue(fallbackLayout);
  const regions = recordItems(layout.regions);
  const regionText =
    regions.length > 0
      ? regions
          .map(
            (item) =>
              `${String(item.name || "页面区域")}：${String(
                item.responsibility || "待补充区域职责",
              )}`,
          )
          .join("\n")
      : stringItems(fallback.structure)
          .map((item) => `${item}：待补充区域职责`)
          .join("\n");
  return [
    `整体布局：${String(layout.overall_layout || "待补充")}`,
    regionText ? `区域划分：\n${regionText}` : "区域划分：待补充",
    `主要内容呈现：${String(layout.primary_content_presentation || "待补充")}`,
    `操作入口位置：${String(layout.operation_entry_position || "待补充")}`,
    `响应式与信息密度：${String(
      layout.responsive_strategy || fallback.responsive || "待补充",
    )}`,
  ].join("\n");
}

function parseLayoutDesignSummary(
  value: string,
  current: unknown,
): Record<string, unknown> {
  const layout = objectValue(current);
  const regions: Array<Record<string, unknown>> = [];
  const next = { ...layout };
  let readingRegions = false;
  summaryLines(value).forEach((line) => {
    if (line.startsWith("整体布局：")) {
      next.overall_layout = line.replace("整体布局：", "").trim();
      readingRegions = false;
      return;
    }
    if (line.startsWith("区域划分：")) {
      readingRegions = true;
      return;
    }
    if (line.startsWith("主要内容呈现：")) {
      next.primary_content_presentation = line
        .replace("主要内容呈现：", "")
        .trim();
      readingRegions = false;
      return;
    }
    if (line.startsWith("操作入口位置：")) {
      next.operation_entry_position = line.replace("操作入口位置：", "").trim();
      readingRegions = false;
      return;
    }
    if (line.startsWith("响应式与信息密度：")) {
      next.responsive_strategy = line.replace("响应式与信息密度：", "").trim();
      readingRegions = false;
      return;
    }
    if (readingRegions) {
      const [name, responsibility] = splitPair(
        line.replace(/^-+\s*/, ""),
        "：",
      );
      regions.push({
        name: name || "页面区域",
        responsibility: responsibility || "待补充区域职责",
      });
    }
  });
  if (regions.length > 0) {
    next.regions = regions;
  }
  return next;
}

function operationSummary(value: unknown): string {
  const lines = recordItems(value).map((item) => {
    const action = String(item.action || item.name || "页面操作");
    const behavior = String(item.behavior || item.description || "待补充行为");
    const endpoint = item.endpoint_id
      ? `；API ${String(item.endpoint_id)}`
      : "";
    return `${action}：${behavior}${endpoint}`;
  });
  return lines.join("\n") || "无";
}

function parseOperationSummary(value: string): Array<Record<string, unknown>> {
  return summaryLines(value).map((line) => {
    const [action, detail] = splitPair(line, "：");
    const [behavior, endpointText] = splitPair(detail, "；API ");
    return {
      action: action || "页面操作",
      behavior: behavior || detail || "待补充行为",
      ...(endpointText ? { endpoint_id: endpointText } : {}),
    };
  });
}

function stateFeedbackSummary(value: unknown): string {
  const lines = recordItems(value).map((item) => {
    const state = String(item.state || item.name || "反馈状态");
    const behavior = String(item.behavior || item.description || "待补充反馈");
    const scope = String(item.scope || "相关业务区域");
    return `${state}：${scope}；${behavior}`;
  });
  return lines.join("\n") || "无";
}

function parseStateFeedbackSummary(
  value: string,
): Array<Record<string, unknown>> {
  return summaryLines(value).map((line) => {
    const [state, detail] = splitPair(line, "：");
    const [scope, behavior] = splitPair(detail, "；");
    return {
      state: state || "反馈状态",
      scope: scope || "相关业务区域",
      behavior: behavior || detail || "待补充反馈",
    };
  });
}

function apiDependencySummary(value: unknown): string {
  const lines = recordItems(value).map((item) => {
    const endpoint = String(item.endpoint_id || "endpoint");
    const method = String(item.method || "GET");
    const path = String(item.path || "");
    const usage = String(item.usage || "read");
    return `${endpoint}：${method} ${path}；${usage}`;
  });
  return lines.join("\n") || "无";
}

function parseApiDependencySummary(
  value: string,
): Array<Record<string, unknown>> {
  return summaryLines(value).map((line) => {
    const [endpoint, detail] = splitPair(line, "：");
    const [request, usage] = splitPair(detail, "；");
    const [method, ...pathParts] = request.split(/\s+/);
    return {
      endpoint_id: endpoint || "endpoint",
      method: method || "GET",
      path: pathParts.join(" "),
      usage: usage || "read",
    };
  });
}

function responseBindingSummary(value: unknown): string {
  const lines = recordItems(value).map((item) => {
    const endpoint = String(item.endpoint_id || "endpoint");
    const source = String(item.source_path || "");
    const field = String(item.page_field || source || "页面字段");
    return `${endpoint}：${source} -> ${field}`;
  });
  return lines.join("\n") || "无";
}

function parseResponseBindingSummary(
  value: string,
): Array<Record<string, unknown>> {
  return summaryLines(value).map((line) => {
    const [endpoint, detail] = splitPair(line, "：");
    const [source, field] = splitPair(detail, " -> ");
    return {
      endpoint_id: endpoint || "endpoint",
      source_path: source,
      page_field: field || source || "页面字段",
    };
  });
}

function navigationSummary(value: unknown): string {
  const lines = recordItems(value).map((item) => {
    const trigger = String(item.trigger || item.action || "页面跳转");
    const target = String(
      item.targetPageId || item.target_path || "待补充目标页面",
    );
    const behavior = String(item.behavior || item.description || "待补充行为");
    return `${trigger}：${target}；${behavior}`;
  });
  return lines.join("\n") || "无";
}

function parseNavigationSummary(value: string): Array<Record<string, unknown>> {
  return summaryLines(value).map((line) => {
    const [trigger, detail] = splitPair(line, "：");
    const [target, behavior] = splitPair(detail, "；");
    return {
      trigger: trigger || "页面跳转",
      targetPageId: target || "待补充目标页面",
      behavior: behavior || detail || "待补充行为",
    };
  });
}

function operationVisibilitySummary(value: unknown): string {
  const lines = recordItems(value).map((item) => {
    const action = String(item.action || "页面操作");
    const visibleTo = stringItems(item.visible_to).join("、") || "待补充";
    const unauthorized = String(
      item.unauthorized_behavior || "隐藏操作入口或展示无权限提示",
    );
    return `${action}：${visibleTo}；${unauthorized}`;
  });
  return lines.join("\n") || "无";
}

function parseOperationVisibilitySummary(
  value: string,
): Array<Record<string, unknown>> {
  return summaryLines(value).map((line) => {
    const [action, detail] = splitPair(line, "：");
    const [visibleTo, unauthorized] = splitPair(detail, "；");
    return {
      action: action || "页面操作",
      visible_to: visibleTo
        ? visibleTo
            .split(/、|,|，/)
            .map((item) => item.trim())
            .filter(Boolean)
        : [],
      unauthorized_behavior: unauthorized || "隐藏操作入口或展示无权限提示",
    };
  });
}

function dependentPagesSummary(value: unknown): string {
  const lines = recordItems(value).map((item) => {
    const id = String(item.pageId || item.id || item.name || "页面");
    const reason = String(item.reason || item.description || "");
    return reason ? `${id}：${reason}` : id;
  });
  return lines.join("\n") || "无";
}

function parseDependentPages(value: string): Array<Record<string, unknown>> {
  return summaryLines(value).map((line) => {
    const [page, reason] = splitPair(line, "：");
    return {
      pageId: page || "页面",
      ...(reason ? { reason } : {}),
    };
  });
}
