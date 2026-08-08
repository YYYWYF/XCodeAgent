import { CheckCircleOutlined, DatabaseOutlined } from "@ant-design/icons";
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
    () => [...(review.pages || []), ...(review.endpoints || [])],
    [review],
  );
  const [changes, setChanges] = useState<
    Record<string, Record<string, unknown>>
  >({});
  const [overallNote, setOverallNote] = useState("");
  const missingSelectedPagePlan = Boolean(
    review.summary?.missingSelectedPagePlan,
  );
  const missingSelectedEndpointPlan = Boolean(
    review.summary?.missingSelectedEndpointPlan,
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
        <div className={cx("workflow-detail-review-intro")}>
          <Text strong>本轮设计已生成</Text>
        </div>
        <div className={cx("workflow-detail-review-metrics")}>
          <Tag>
            页面 <strong>{review.summary?.page_count || 0}</strong>
          </Tag>
          <Tag>
            接口 <strong>{review.summary?.endpoint_count || 0}</strong>
          </Tag>
          <Tag>
            API 契约 <strong>{review.summary?.api_contract_count || 0}</strong>
          </Tag>
        </div>
      </div>
      {missingSelectedPagePlan ||
      missingSelectedEndpointPlan ||
      targets.length === 0 ? (
        <Alert
          message={
            message ||
            `目标 ${review.summary?.selectedEndpointId || review.summary?.selectedPageId || ""} 还没有生成细节设计，请先生成该目标的 plan。`
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
                  {target.target_type !== "page" && (
                    <span className={cx("workflow-detail-review-target-icon")}>
                      <DatabaseOutlined />
                    </span>
                  )}
                  <span className={cx("workflow-detail-review-target-kind")}>
                    {targetKindLabel(target.target_type)}
                  </span>
                  <Text
                    className={cx("workflow-detail-review-target-name")}
                    strong
                  >
                    {target.name || target.target_id}
                  </Text>
                  {changes[target.target_id] && (
                    <Tag color="purple">已修改</Tag>
                  )}
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
              ) : target.target_type === "endpoint" ? (
                <EndpointReviewEditor
                  changes={changes[target.target_id] || {}}
                  disabled={disabled}
                  onChange={(field, value) => updateField(target, field, value)}
                  target={target}
                />
              ) : (
                <></>
              )}
            </Panel>
          ))}
        </Collapse>
      )}
      <div className={cx("workflow-detail-review-actions")}>
        <label className={cx("workflow-detail-review-note")}>
          <div className={cx("workflow-detail-review-note-heading")}>
            <Text strong>整体补充说明</Text>
          </div>
          <TextArea
            autoSize={false}
            disabled={disabled}
            onChange={(event) => setOverallNote(event.target.value)}
            placeholder="可选：补充跨页面规则、统一交互或其他全局调整"
            value={overallNote}
          />
        </label>
        <Button
          disabled={
            disabled ||
            missingSelectedPagePlan ||
            missingSelectedEndpointPlan ||
            targets.length === 0
          }
          icon={<CheckCircleOutlined />}
          onClick={confirm}
          size="large"
          type="primary"
        >
          确认设计并进入构建
        </Button>
      </div>
    </div>
  );
}

// 将后端审核对象类型转换为用户可读标签。
function targetKindLabel(
  targetType: WorkflowDetailReviewTarget["target_type"],
): string {
  if (targetType === "page") return "页面";
  if (targetType === "endpoint") return "接口";
  return "对象";
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

// 渲染单个 endpoint 详细设计的审核输入项。
function EndpointReviewEditor({
  changes,
  disabled,
  onChange,
  target,
}: ReviewEditorProps): ReactElement {
  const dataOrigin = objectChange(changes.data_origin, target.data_origin);
  return (
    <div className={cx("workflow-detail-review-fields")}>
      <ReviewSummaryField
        disabled={disabled}
        label="一、数据用途（数据服务于什么）"
        onChange={(value) => onChange("data_usage", parseJsonObject(value))}
        value={jsonSummary(objectChange(changes.data_usage, target.data_usage))}
      />
      <ReviewSummaryField
        disabled={disabled}
        label="二、数据来源"
        onChange={(value) =>
          onChange(
            "data_origin",
            parseDataOriginSummary(value, target.data_origin),
          )
        }
        value={dataOriginSummary(dataOrigin)}
      />
      <ReviewSummaryField
        disabled={disabled}
        label="接口行为决策（处理逻辑与验收标准的唯一来源）"
        onChange={(value) =>
          onChange("endpoint_decision", parseJsonObject(value))
        }
        value={jsonSummary(
          objectChange(changes.endpoint_decision, target.endpoint_decision),
        )}
      />
      <ReviewSummaryField
        disabled={disabled}
        label="三、接口设计"
        onChange={(value) =>
          onChange("interface_design", parseJsonObject(value))
        }
        value={jsonSummary(
          objectChange(changes.interface_design, target.interface_design),
        )}
      />
      <ReviewListField
        disabled
        label="验收标准（由接口行为决策自动生成）"
        onChange={() => undefined}
        value={target.acceptance_criteria || []}
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

// 渲染简短文本配置项，并保留标准受控输入行为。
function ReviewTextField({
  disabled,
  label,
  onChange,
  value,
}: FieldProps<string>): ReactElement {
  return (
    <label
      className={cx(
        "workflow-detail-review-field",
        "workflow-detail-review-field-compact",
      )}
    >
      <Text type="secondary">{label}</Text>
      <TextArea
        autoSize={{ minRows: 4, maxRows: 4 }}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        value={value}
      />
    </label>
  );
}

// 渲染多项配置输入，并将每行内容同步为字符串列表。
function ReviewListField({
  disabled,
  label,
  onChange,
  value,
}: FieldProps<string[]>): ReactElement {
  return (
    <label
      className={cx(
        "workflow-detail-review-field",
        "workflow-detail-review-field-structured",
      )}
    >
      <Text type="secondary">{label}</Text>
      <TextArea
        autoSize={{ minRows: 4, maxRows: 4 }}
        disabled={disabled}
        onChange={(event) => onChange(splitLines(event.target.value))}
        value={value.join("\n")}
      />
    </label>
  );
}

// 渲染需要完整展示的设计说明输入。
function ReviewSummaryField({
  disabled,
  label,
  onChange,
  value,
}: FieldProps<string>): ReactElement {
  return (
    <label
      className={cx(
        "workflow-detail-review-field",
        "workflow-detail-review-field-expanded",
      )}
    >
      <Text type="secondary">{label}</Text>
      <TextArea
        autoSize={{ minRows: 4, maxRows: 4 }}
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

// 判断 endpoint 当前是否仍处于待用户决策的数据来源状态。
function isNeedsUserConfirmationDataOrigin(value: unknown): boolean {
  const origin = objectValue(value);
  const effectiveSource = objectValue(origin.effective_source);
  const effectiveKind = String(effectiveSource.kind || "");
  const hasPendingDifference =
    Array.isArray(origin.differences) &&
    origin.differences.some(
      (item) =>
        isRecord(item) &&
        String(item.resolution_kind || "") === "needs_user_confirmation",
    );
  return effectiveKind === "needs_user_confirmation" || hasPendingDifference;
}

// 判断详情确认是否还缺少数据来源决策，防止用户直接跳过未决数据库方案。
function requiresDataOriginDecision(
  target: WorkflowDetailReviewTarget,
  changedDataOrigin: unknown,
): boolean {
  if (target.target_type !== "endpoint") return false;
  if (!isNeedsUserConfirmationDataOrigin(target.data_origin)) return false;
  return !isResolvedDataOrigin(changedDataOrigin);
}

// 校验前端提交的数据来源是否已经转为任务规划可识别的确定方案。
function isResolvedDataOrigin(value: unknown): boolean {
  const origin = objectValue(value);
  const effectiveSource = objectValue(origin.effective_source);
  const sourceType = String(origin.source_type || "");
  const effectiveKind = String(effectiveSource.kind || "");
  if (
    sourceType === "database" &&
    (effectiveKind === "mysql_new_table" || effectiveKind === "mysql_existing")
  ) {
    return Boolean(
      effectiveSource.database &&
        Array.isArray(effectiveSource.tables) &&
        effectiveSource.tables.length > 0,
    );
  }
  return false;
}

// 把结构化 endpoint 字段转换为便于用户编辑的 JSON 文本。
function jsonSummary(value: unknown): string {
  return JSON.stringify(objectValue(value), null, 2);
}

// 解析用户编辑后的 JSON 对象；解析失败时保留原始文本为说明，避免输入丢失。
function parseJsonObject(value: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(value);
    return objectValue(parsed);
  } catch {
    return { note: value };
  }
}

// 把 endpoint 数据来源压缩为用户可读摘要，只展示唯一有效来源与差异项。
function dataOriginSummary(value: unknown): string {
  const origin = objectValue(value);
  const effectiveSource = objectValue(origin.effective_source);
  const fieldMappings = recordItems(origin.field_mappings);
  const differences = recordItems(origin.differences);
  const notes = stringItems(origin.notes);
  return [
    `来源类型：${String(origin.source_type || effectiveSource.kind || "待确认")}`,
    `有效来源：${compactRecordSummary(effectiveSource) || "待确认"}`,
    `字段映射：${fieldMappings.length > 0 ? fieldMappings.map(fieldMappingLine).join("；") : "无"}`,
    `差异项：${differences.length > 0 ? differences.map(differenceLine).join("；") : "无"}`,
    `备注：${notes.length > 0 ? notes.join("；") : "无"}`,
  ].join("\n");
}

// 将用户编辑的数据来源摘要还原为后端可接收的精简结构。
function parseDataOriginSummary(
  value: string,
  current: unknown,
): Record<string, unknown> {
  const origin = objectValue(current);
  const next: Record<string, unknown> = {
    ...origin,
    effective_source: objectValue(origin.effective_source),
  };
  summaryLines(value).forEach((line) => {
    if (line.startsWith("来源类型：")) {
      // 数据源大类由 ProjectPlan 决定，摘要编辑不得改变 source_type 或实现来源。
      return;
    }
    if (line.startsWith("有效来源：")) {
      next.effective_source = {
        ...objectValue(next.effective_source),
        description: line.replace("有效来源：", "").trim(),
      };
      return;
    }
    if (line.startsWith("字段映射：")) {
      next.field_mappings = parseFieldMappingLines(
        line.replace("字段映射：", ""),
      );
      return;
    }
    if (line.startsWith("差异项：")) {
      next.differences = parseDifferenceLines(line.replace("差异项：", ""));
      return;
    }
    if (line.startsWith("备注：")) {
      next.notes = splitInlineItems(line.replace("备注：", ""));
    }
  });
  return next;
}

// 生成紧凑对象摘要，过滤空值避免展示无关字段。
function compactRecordSummary(value: Record<string, unknown>): string {
  return Object.entries(value)
    .filter(([, item]) => {
      if (Array.isArray(item)) return item.length > 0;
      return item !== undefined && item !== null && String(item).trim() !== "";
    })
    .map(([key, item]) => {
      const rendered = Array.isArray(item) ? item.join(", ") : String(item);
      return `${key}=${rendered}`;
    })
    .join("，");
}

// 渲染单条字段映射，保持 target/source/rule 三列信息。
function fieldMappingLine(value: Record<string, unknown>): string {
  const target = String(value.target_field || value.field || "目标字段");
  const source = String(value.source || value.source_field || "来源待确认");
  const rule = String(value.rule || value.description || "");
  return rule ? `${target} <- ${source}（${rule}）` : `${target} <- ${source}`;
}

// 渲染单条差异项，突出实际缺口与处理建议。
function differenceLine(value: Record<string, unknown>): string {
  const field = String(value.field || value.name || "待确认项");
  const expected = value.expected ? `期望 ${String(value.expected)}` : "";
  const actual = value.actual ? `实际 ${String(value.actual)}` : "";
  const resolution = value.resolution ? `处理 ${String(value.resolution)}` : "";
  return [field, expected, actual, resolution].filter(Boolean).join("，");
}

// 解析一行内用分号分隔的字段映射摘要。
function parseFieldMappingLines(value: string): Array<Record<string, unknown>> {
  return splitInlineItems(value).map((item) => {
    const [target, sourceWithRule] = splitPair(item, "<-");
    const [source, rule] = splitPair(
      sourceWithRule.replace(/[（）]/g, ""),
      "，",
    );
    return {
      target_field: target,
      source,
      rule,
    };
  });
}

// 解析一行内用分号分隔的差异摘要。
function parseDifferenceLines(value: string): Array<Record<string, unknown>> {
  return splitInlineItems(value).map((item) => ({
    field: item,
    expected: "",
    actual: "",
    resolution: "",
  }));
}

// 按中文或英文分号切分单行摘要，并忽略“无”。
function splitInlineItems(value: string): string[] {
  return value
    .split(/；|;/)
    .map((item) => item.trim())
    .filter((item) => item && item !== "无");
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
    `响应式与信息密度：${String(layout.responsive_strategy || fallback.responsive || "待补充")}`,
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
