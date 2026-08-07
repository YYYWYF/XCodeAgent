import { Input, Radio, Space, Typography } from "antd";
import type { ReactElement } from "react";
import { useMemo, useState } from "react";
import { cx } from "../../../../utils";

const { Text } = Typography;

type DataOriginDecisionFieldProps = {
  disabled?: boolean;
  onChange: (value: Record<string, unknown>) => void;
  value: Record<string, unknown>;
};

type DecisionStrategy = "mysql_new_table" | "mysql_existing";

type DecisionDraft = {
  column: string;
  database: string;
  seed: string;
  table: string;
};

// 渲染未决数据来源的结构化决策输入，把用户选择转换为后端可识别的数据来源结构。
export default function DataOriginDecisionField({
  disabled,
  onChange,
  value,
}: DataOriginDecisionFieldProps): ReactElement {
  const inferred = useMemo(() => inferDecisionDraft(value), [value]);
  const [strategy, setStrategy] = useState<DecisionStrategy | undefined>(
    resolvedStrategy(value),
  );
  const [draft, setDraft] = useState<DecisionDraft>(inferred);

  // 更新单个草稿字段，并在信息完整时同步结构化 data_origin。
  const updateDraft = (field: keyof DecisionDraft, nextValue: string): void => {
    const nextDraft = { ...draft, [field]: nextValue };
    setDraft(nextDraft);
    emitOrigin(strategy, nextDraft, value, onChange);
  };

  // 切换处理方案后，立即尝试生成对应的确定数据来源。
  const updateStrategy = (nextStrategy: DecisionStrategy): void => {
    const nextDraft = draftForStrategy(nextStrategy, draft, value);
    setStrategy(nextStrategy);
    setDraft(nextDraft);
    emitOrigin(nextStrategy, nextDraft, value, onChange);
  };

  return (
    <section
      className={cx(
        "workflow-detail-review-field",
        "workflow-detail-review-field-expanded",
        "workflow-data-origin-decision",
      )}
    >
      <Text type="secondary">二、数据来源处理方案</Text>
      <div className={cx("workflow-data-origin-decision-card")}>
        <div className={cx("workflow-data-origin-decision-context")}>
          <Text strong>当前需要确认数据库实现方式</Text>
          <Text type="secondary">{originDescription(value)}</Text>
        </div>
        <Radio.Group
          disabled={disabled}
          onChange={(event) => updateStrategy(event.target.value)}
          value={strategy}
        >
          <Space direction="vertical" size={8}>
            <Radio value="mysql_new_table">A. 新建数据库表</Radio>
            <Radio value="mysql_existing">B. 给现有表增加字段</Radio>
          </Space>
        </Radio.Group>
        {strategy === "mysql_new_table" && (
          <div className={cx("workflow-data-origin-decision-grid")}>
            <DecisionInput
              disabled={disabled}
              label="数据库"
              onChange={(nextValue) => updateDraft("database", nextValue)}
              placeholder="例如 xcode"
              value={draft.database}
            />
            <DecisionInput
              disabled={disabled}
              label="新建表名"
              onChange={(nextValue) => updateDraft("table", nextValue)}
              placeholder="例如 role"
              value={draft.table}
            />
            <DecisionInput
              disabled={disabled}
              label="初始化数据"
              onChange={(nextValue) => updateDraft("seed", nextValue)}
              placeholder="例如 管理员、普通用户"
              value={draft.seed}
            />
          </div>
        )}
        {strategy === "mysql_existing" && (
          <div className={cx("workflow-data-origin-decision-grid")}>
            <DecisionInput
              disabled={disabled}
              label="数据库"
              onChange={(nextValue) => updateDraft("database", nextValue)}
              placeholder="例如 xcode"
              value={draft.database}
            />
            <DecisionInput
              disabled={disabled}
              label="现有表名"
              onChange={(nextValue) => updateDraft("table", nextValue)}
              placeholder="例如 user"
              value={draft.table}
            />
            <DecisionInput
              disabled={disabled}
              label="新增字段"
              onChange={(nextValue) => updateDraft("column", nextValue)}
              placeholder="例如 role"
              value={draft.column}
            />
          </div>
        )}
        <Text
          className={cx("workflow-data-origin-decision-hint")}
          type="secondary"
        >
          选择并补全后，这里会提交为确定的数据来源，不再保留
          needs_user_confirmation。
        </Text>
      </div>
    </section>
  );
}

type DecisionInputProps = {
  disabled?: boolean;
  label: string;
  onChange: (value: string) => void;
  placeholder: string;
  value: string;
};

// 渲染决策卡里的紧凑文本输入，统一空白裁剪行为。
function DecisionInput({
  disabled,
  label,
  onChange,
  placeholder,
  value,
}: DecisionInputProps): ReactElement {
  return (
    <label className={cx("workflow-data-origin-decision-input")}>
      <Text type="secondary">{label}</Text>
      <Input
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        onBlur={(event) => onChange(event.target.value.trim())}
        placeholder={placeholder}
        value={value}
      />
    </label>
  );
}

// 从已有 data_origin 推断可回填的表单默认值，减少用户重复输入。
function inferDecisionDraft(origin: Record<string, unknown>): DecisionDraft {
  const effectiveSource = recordValue(origin.effective_source);
  const tables = arrayStrings(effectiveSource.tables);
  const description = originDescription(origin);
  return {
    column: suggestedColumn(origin),
    database: stringValue(effectiveSource.database),
    seed: description.includes("管理员") ? "管理员、普通用户" : "",
    table: suggestedTable(origin, tables),
  };
}

// 根据所选方案切换表名默认值：新表偏向业务实体，现有表偏向已发现表。
function draftForStrategy(
  strategy: DecisionStrategy,
  draft: DecisionDraft,
  origin: Record<string, unknown>,
): DecisionDraft {
  const effectiveSource = recordValue(origin.effective_source);
  const tables = arrayStrings(effectiveSource.tables);
  if (strategy === "mysql_existing" && tables[0]) {
    return {
      ...draft,
      column: draft.column || suggestedColumn(origin),
      table: tables[0],
    };
  }
  if (strategy === "mysql_new_table") {
    return {
      ...draft,
      table: suggestedTable(origin, []) || draft.table,
    };
  }
  return draft;
}

// 读取当前对象里已经确定过的来源类型，用于编辑已选方案时保持选中态。
function resolvedStrategy(
  origin: Record<string, unknown>,
): DecisionStrategy | undefined {
  const effectiveKind = stringValue(recordValue(origin.effective_source).kind);
  if (effectiveKind === "mysql_new_table") return "mysql_new_table";
  if (effectiveKind === "mysql_existing") return "mysql_existing";
  return undefined;
}

// 在草稿完整时输出最终 data_origin；不完整时保留当前输入等待用户补齐。
function emitOrigin(
  strategy: DecisionStrategy | undefined,
  draft: DecisionDraft,
  current: Record<string, unknown>,
  onChange: (value: Record<string, unknown>) => void,
): void {
  if (!strategy) return;
  const nextOrigin = buildDataOrigin(strategy, draft, current);
  if (nextOrigin) {
    onChange(nextOrigin);
    return;
  }
  onChange(incompleteDataOrigin(current));
}

// 根据选择的实现方案构造后端任务规划能识别的 data_origin 结构。
function buildDataOrigin(
  strategy: DecisionStrategy,
  draft: DecisionDraft,
  current: Record<string, unknown>,
): Record<string, unknown> | null {
  const dataSourceId = stringValue(
    recordValue(current.effective_source).data_source_id,
  );
  const database = draft.database.trim();
  const table = draft.table.trim();
  if (strategy === "mysql_new_table") {
    if (!database || !table) return null;
    return {
      source_type: "database",
      effective_source: {
        kind: "mysql_new_table",
        data_source_id: dataSourceId || undefined,
        database,
        tables: [table],
        description: `在 ${database} 数据库中新建 ${table} 表，作为该接口的数据来源。`,
      },
      field_mappings: [
        {
          target_field: "id",
          source: `${table}.id`,
          rule: "直接映射或转为字符串",
        },
        { target_field: "name", source: `${table}.name`, rule: "直接映射" },
      ],
      differences: [
        {
          field: table,
          expected: `存在 ${table} 表，至少包含 id/name 字段`,
          actual: "当前数据库中缺少该业务表",
          resolution_kind: "database_change",
          operation_refs: [`create-table-${table}`],
          backend_adaptation: null,
        },
      ],
      database_operations: [
        {
          id: `create-table-${table}`,
          operation: "create_table",
          database,
          table: {
            name: table,
            comment: "接口数据表",
            columns: [
              {
                name: "id",
                type: "varchar(64)",
                nullable: false,
                default: null,
                comment: "主键",
                auto_increment: false,
              },
              {
                name: "name",
                type: "varchar(255)",
                nullable: false,
                default: null,
                comment: "名称",
                auto_increment: false,
              },
            ],
            primary_key: ["id"],
            indexes: [],
            foreign_keys: [],
          },
          column: null,
          from: null,
          to: null,
          reason: `新增 ${table} 表承载接口数据`,
          source_fields: ["id", "name"],
        },
      ],
      notes: draft.seed.trim() ? [`初始化数据：${draft.seed.trim()}`] : [],
    };
  }
  if (strategy === "mysql_existing") {
    const column = draft.column.trim();
    if (!database || !table || !column) return null;
    return {
      source_type: "database",
      effective_source: {
        kind: "mysql_existing",
        data_source_id: dataSourceId || undefined,
        database,
        tables: [table],
        description: `复用 ${database}.${table} 表，并新增 ${column} 字段承载该接口所需数据。`,
      },
      field_mappings: [
        {
          target_field: "id",
          source: `${table}.id`,
          rule: "直接映射或转为字符串",
        },
        {
          target_field: "name",
          source: `${table}.${column}`,
          rule: "作为展示名称或角色值",
        },
      ],
      differences: [
        {
          field: `${table}.${column}`,
          expected: `现有 ${table} 表包含 ${column} 字段`,
          actual: "当前表缺少该字段",
          resolution_kind: "database_change",
          operation_refs: [`add-${table}-${column}`],
          backend_adaptation: null,
        },
      ],
      database_operations: [
        {
          id: `add-${table}-${column}`,
          operation: "add_column",
          database,
          table,
          column,
          from: null,
          to: {
            type: "varchar(255)",
            nullable: true,
            default: null,
            comment: "接口新增字段",
          },
          reason: `新增 ${column} 字段承载接口数据`,
          source_fields: [column],
        },
      ],
      notes: [`角色数据从 ${table}.${column} 派生`],
    };
  }
  return null;
}

// 构造未补全状态，确保用户清空必填项后不会沿用上一次的有效方案。
function incompleteDataOrigin(
  current: Record<string, unknown>,
): Record<string, unknown> {
  const effectiveSource = recordValue(current.effective_source);
  return {
    ...current,
    source_type: "database",
    effective_source: {
      ...effectiveSource,
      kind: "needs_user_confirmation",
      description: originDescription(current),
    },
  };
}

// 提取原始未决说明，作为用户判断 A/B/C 的上下文。
function originDescription(origin: Record<string, unknown>): string {
  const effectiveSource = recordValue(origin.effective_source);
  const description = stringValue(effectiveSource.description);
  if (description) return description;
  const differences = arrayRecords(origin.differences);
  return (
    differences
      .map((item) => stringValue(item.resolution || item.field))
      .filter(Boolean)
      .join("；") || "请确认该接口的数据实现方式。"
  );
}

// 从差异项和说明中推断常见的角色列名。
function suggestedColumn(origin: Record<string, unknown>): string {
  const description = originDescription(origin).toLowerCase();
  if (description.includes("role")) return "role";
  if (description.includes("角色")) return "role";
  return "";
}

// 从现有来源和差异项中推断默认表名，新建表优先使用 role。
function suggestedTable(
  origin: Record<string, unknown>,
  tables: string[],
): string {
  const description = originDescription(origin).toLowerCase();
  if (description.includes("role") || description.includes("角色"))
    return "role";
  return tables[0] || "";
}

// 安全读取普通对象，过滤数组和空值。
function recordValue(value: unknown): Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

// 安全读取字符串值，避免把 undefined/null 展示到输入框。
function stringValue(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

// 安全读取字符串数组，用于回填表名。
function arrayStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
}

// 安全读取对象数组，用于渲染差异说明。
function arrayRecords(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === "object" && !Array.isArray(item),
      )
    : [];
}
