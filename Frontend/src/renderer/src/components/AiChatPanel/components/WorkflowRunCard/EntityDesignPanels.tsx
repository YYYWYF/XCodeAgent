import {
  ApiOutlined,
  ArrowLeftOutlined,
  BulbOutlined,
  CheckCircleOutlined,
  CheckOutlined,
  CloseOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  FileTextOutlined,
  PlusOutlined,
  ReloadOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Collapse,
  Input,
  message,
  Select,
  Spin,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import type { CSSProperties, ReactElement, ReactNode } from "react";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  WorkflowDetailReviewTarget,
  WorkflowEntityDesignAction,
  WorkflowEntityDesignSuggestion,
  WorkflowEntityDesignSummary,
} from "../../../../typings";
import {
  ExternalApiDesignPanel,
} from "./ExternalApiDesignPanel";
import {
  type ExternalApiDesignDraft,
  useExternalApiDraft,
} from "./useExternalApiDraft";
import {
  fetchDatabaseTableColumns,
  listDatabaseTables,
} from "../../../../service/workspaceTools";
import { cx } from "../../../../utils";
import {
  applyEntityDesignSuggestion,
  constraintRowsToFieldValues,
  defaultConstraintRows,
  isRecord,
  normalizeFieldValues,
  normalizeObjectRows,
  normalizeStringList,
  parseJsonImport,
  parseJsonList,
  parseJsonRecord,
  recordItems,
  responseFieldPaths,
  serializeExternalApiDesign,
  resolveEntityDesignFields,
  seedRowsFromSuggestions,
  serializeSeedRows,
  tryParseJson,
} from "./entityDesignSerialization";

const { Panel } = Collapse;
const { Text } = Typography;
const { TextArea } = Input;

const DATA_SOURCE_ICONS: Record<string, ReactElement> = {
  database: <DatabaseOutlined />,
  external_api: <ApiOutlined />,
  static: <FileTextOutlined />,
};

const DATA_SOURCE_LABELS: Record<string, string> = {
  database: "数据库",
  external_api: "外部 API",
  static: "静态数据",
};

// ---------- 共享小工具 ----------

function objectValue(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
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

function stringChange(changed: unknown, initial: unknown): string {
  return typeof changed === "string"
    ? changed
    : typeof initial === "string"
      ? initial
      : "";
}

// ---------- 通用结构化组件 ----------

type EntitySectionProps = {
  title: string
  extra?: ReactElement
  children: ReactNode
}

function EntitySectionCard({ title, extra, children }: EntitySectionProps): ReactElement {
  return (
    <section className={cx("entity-design-section")}>
      <header className={cx("entity-design-section-header")}>
        <Text strong>{title}</Text>
        {extra}
      </header>
      <div className={cx("entity-design-section-body")}>{children}</div>
    </section>
  );
}

type EntityMetricItem = {
  label: string
  value: string
  tone?: "neutral" | "success" | "warning" | "error"
}

function EntityMetricChips({ items }: { items: EntityMetricItem[] }): ReactElement {
  return (
    <div className={cx("entity-design-metric-chips")}>
      {items.map((item) => (
        <span
          className={cx(
            "entity-design-metric-chip",
            item.tone && `is-${item.tone}`,
          )}
          key={item.label}
        >
          <Text type="secondary">{item.label}</Text>
          <Text strong>{item.value}</Text>
        </span>
      ))}
    </div>
  );
}

type RowColumn = {
  key: string
  label: string
  placeholder?: string
  type?: "text" | "select"
  options?: Array<{ label: string; value: string }>
  readonly?: boolean
}

function RowListEditor({
  columns,
  disabled,
  disableAdd = false,
  emptyText,
  onChange,
  rows,
  addLabel = "添加一行",
}: {
  columns: RowColumn[]
  disabled?: boolean
  disableAdd?: boolean
  emptyText?: string
  onChange: (rows: Array<Record<string, unknown>>) => void
  rows: Array<Record<string, unknown>>
  addLabel?: string
}): ReactElement {
  const updateRow = (index: number, key: string, value: unknown): void => {
    onChange(rows.map((row, rowIndex) => (rowIndex === index ? { ...row, [key]: value } : row)));
  };
  const removeRow = (index: number): void => {
    onChange(rows.filter((_row, rowIndex) => rowIndex !== index));
  };
  const addRow = (): void => {
    onChange([
      ...rows,
      Object.fromEntries(columns.map((column) => [column.key, ""])),
    ]);
  };
  return (
    <div className={cx("entity-row-editor")}>
      {rows.length === 0 ? (
        <Text type="secondary" className={cx("entity-row-editor-empty")}>
          {emptyText || "暂无数据，点击下方添加。"}
        </Text>
      ) : (
        <div
          className={cx("entity-row-editor-grid")}
          style={{ "--entity-row-columns": columns.length } as CSSProperties}
        >
          {columns.map((column) => (
            <span className={cx("entity-row-editor-head")} key={column.key}>
              {column.label}
            </span>
          ))}
          <span className={cx("entity-row-editor-head", "is-actions")} aria-hidden="true" />
          {rows.map((row, index) => (
            <Fragment key={`${index}`}>
              {columns.map((column) => (
                <div className={cx("entity-row-editor-cell")} key={column.key}>
                  {column.type === "select" ? (
                    <Select
                      allowClear
                      disabled={disabled}
                      onChange={(value) => updateRow(index, column.key, value)}
                      options={column.options}
                      placeholder={column.placeholder || column.label}
                      value={String(row[column.key] ?? "") || undefined}
                    />
                  ) : column.readonly ? (
                    <Text className={cx("entity-row-editor-cell-value")}>
                      {String(row[column.key] ?? "") ||
                        column.placeholder ||
                        "—"}
                    </Text>
                  ) : (
                    <Input
                      disabled={disabled}
                      onChange={(event) =>
                        updateRow(index, column.key, event.target.value)
                      }
                      placeholder={column.placeholder || column.label}
                      value={String(row[column.key] ?? "")}
                    />
                  )}
                </div>
              ))}
              <div className={cx("entity-row-editor-cell", "is-actions")}>
                <Button
                  aria-label="删除该行"
                  danger
                  disabled={disabled}
                  icon={<DeleteOutlined />}
                  onClick={() => removeRow(index)}
                  size="small"
                  type="text"
                />
              </div>
            </Fragment>
          ))}
        </div>
      )}
      {!disableAdd ? (
        <Button
          block
          className={cx("entity-row-editor-add")}
          disabled={disabled}
          icon={<PlusOutlined />}
          onClick={addRow}
          type="dashed"
        >
          {addLabel}
        </Button>
      ) : null}
    </div>
  );
}

function ReadonlyGrid({
  columns,
  emptyText,
  rows,
}: {
  columns: Array<{ key: string; label: string; render?: (row: Record<string, unknown>) => string }>
  emptyText?: string
  rows: Array<Record<string, unknown>>
}): ReactElement {
  if (rows.length === 0) {
    return (
      <Text type="secondary" className={cx("entity-readonly-empty")}>
        {emptyText || "暂无数据"}
      </Text>
    );
  }
  return (
    <div
      className={cx("entity-readonly-grid")}
      style={{ "--entity-grid-columns": columns.length } as CSSProperties}
    >
      {columns.map((column) => (
        <span className={cx("entity-readonly-cell", "is-header")} key={column.key}>
          {column.label}
        </span>
      ))}
      {rows.map((row, rowIndex) =>
        columns.map((column) => (
          <span className={cx("entity-readonly-cell")} key={`${rowIndex}-${column.key}`}>
            {column.render ? column.render(row) : String(row[column.key] ?? "")}
          </span>
        )),
      )}
    </div>
  );
}

function AdvancedJsonCollapse({
  disabled,
  label,
  onChange,
  onImport,
  placeholder,
  value,
}: {
  disabled?: boolean
  label: string
  onChange: (value: string) => void
  onImport?: (text: string) => void
  placeholder?: string
  value: string
}): ReactElement {
  const importNode = onImport ? (
    <Upload
      accept=".json,application/json"
      beforeUpload={(file) => {
        const reader = new FileReader();
        reader.onload = () => {
          const text = String(reader.result || "");
          const parsed = parseJsonImport(text);
          if (!parsed.ok) {
            message.error(`JSON 导入失败：${parsed.error}`);
            return;
          }
          onChange(text);
          onImport(text);
        };
        reader.onerror = () => {
          message.error("读取 JSON 文件失败，请重试。");
        };
        reader.readAsText(file);
        return false;
      }}
      disabled={disabled}
      showUploadList={false}
    >
      <Button disabled={disabled} icon={<UploadOutlined />} size="small">
        导入 JSON
      </Button>
    </Upload>
  ) : null;
  return (
    <Collapse
      bordered={false}
      className={cx("entity-design-advanced-json")}
      ghost
    >
      <Panel extra={importNode} header={label} key="advanced">
        <TextArea
          autoSize={{ minRows: 4, maxRows: 10 }}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder || "高级 JSON 编辑，供精确调整使用"}
          value={value}
        />
      </Panel>
    </Collapse>
  );
}

function EntityDesignPanelHeader({
  eyebrow,
  onBack,
  title,
}: {
  eyebrow?: string
  onBack?: () => void
  title: string
}): ReactElement {
  return (
    <header className={cx("entity-design-panel-header")}>
      <div className={cx("entity-design-panel-heading")}>
        {eyebrow ? (
          <Text className={cx("entity-design-panel-eyebrow")} type="secondary">
            {eyebrow}
          </Text>
        ) : null}
        <Text className={cx("entity-design-panel-title")} strong>
          {title}
        </Text>
      </div>
      {onBack ? (
        <Button
          className={cx("entity-design-panel-back")}
          icon={<ArrowLeftOutlined />}
          onClick={onBack}
          size="small"
          type="text"
        >
          返回修改数据源
        </Button>
      ) : null}
    </header>
  );
}

// ---------- 第 ① 步：数据源选择 ----------

export function EntityDesignDataSourcePanel({
  disabled,
  entityDesign,
  onAction,
}: {
  disabled?: boolean
  entityDesign: WorkflowEntityDesignSummary | undefined
  onAction?: (action: WorkflowEntityDesignAction) => void
}): ReactElement {
  const [sourceType, setSourceType] = useState<string>("");
  const options = entityDesign?.data_source_options || [];
  return (
    <div className={cx("entity-design-panel")}>
      <EntityDesignPanelHeader
        title="实体设计 · 选择数据源"
      />
      <div className={cx("entity-design-source-cards")}>
        {options.map((option) => {
          const unavailable = option.available === false;
          const selected = sourceType === option.value;
          return (
            <button
              className={cx(
                "entity-design-source-card",
                selected && "is-selected",
                unavailable && "is-unavailable",
              )}
              disabled={disabled || unavailable}
              key={option.value}
              onClick={() => setSourceType(option.value)}
              type="button"
            >
              <span className={cx("entity-design-source-icon")}>
                {DATA_SOURCE_ICONS[option.value] || <DatabaseOutlined />}
              </span>
              <span className={cx("entity-design-source-copy")}>
                <span className={cx("entity-design-source-name")}>
                  {option.label}
                </span>
                {unavailable ? (
                  <span className={cx("entity-design-source-badge")}>
                    暂不可用
                  </span>
                ) : null}
              </span>
              <span className={cx("entity-design-source-check")} aria-hidden="true">
                {selected ? <CheckOutlined /> : null}
              </span>
            </button>
          );
        })}
      </div>
      <div className={cx("entity-design-panel-actions")}>
        <Button
          disabled={disabled || !sourceType || !entityDesign?.entity_id}
          onClick={() =>
            onAction?.({
              action: "select_data_source",
              entity_id: String(entityDesign?.entity_id || ""),
              data_source_type: sourceType as "database" | "external_api" | "static",
            })
          }
          type="primary"
        >
          确认数据源并生成方案
        </Button>
      </div>
    </div>
  );
}

// ---------- 第 ② 步：静态数据方案输入 ----------

export function StaticDataInputPanel({
  disabled,
  entityFields,
  entityId,
  onAction,
  onBackToSource,
}: {
  disabled?: boolean
  entityFields?: Array<Record<string, unknown>>
  entityId: string
  onAction?: (action: WorkflowEntityDesignAction) => void
  onBackToSource?: () => void
}): ReactElement {
  const fields = recordItems(entityFields);
  const columns: RowColumn[] =
    fields.length > 0
      ? fields.map((field) => ({
          key: String(field.name || field.label || ""),
          label: String(field.label || field.name || "字段"),
        }))
      : [{ key: "value", label: "值" }];
  const [seedRows, setSeedRows] = useState<Array<Record<string, unknown>>>([]);
  const [fieldValues, setFieldValues] = useState<Record<string, string[]>>({});
  const updateFieldValues = (fieldName: string, values: string[]): void => {
    setFieldValues((current) => ({ ...current, [fieldName]: values }));
  };
  return (
    <div className={cx("entity-design-panel")}>
      <EntityDesignPanelHeader
        eyebrow="实体设计 · 静态数据方案"
        onBack={onBackToSource}
        title="构建静态数据"
      />
      <EntitySectionCard title="种子数据">
        <RowListEditor
          columns={columns}
          disabled={disabled}
          emptyText="暂无种子记录，点击下方添加。"
          onChange={setSeedRows}
          rows={seedRows}
        />
        <AdvancedJsonCollapse
          disabled={disabled}
          label="原始 JSON（种子记录数组）"
          onChange={(value) => setSeedRows(parseJsonList(value))}
          value={JSON.stringify(seedRows, null, 2)}
        />
      </EntitySectionCard>
      <EntitySectionCard title="字段取值 / 枚举">
        {fields.length === 0 ? (
          <Text type="secondary">暂无字段，可在种子数据中直接填写取值。</Text>
        ) : (
          fields.map((field) => {
            const fieldName = String(field.name || field.label || "");
            return (
              <div className={cx("entity-field-values-row")} key={fieldName}>
                <Text strong>{String(field.label || field.name || "字段")}</Text>
                <Select
                  disabled={disabled}
                  mode="tags"
                  onChange={(values) => updateFieldValues(fieldName, normalizeStringList(values))}
                  placeholder="输入取值后回车"
                  value={normalizeStringList(fieldValues[fieldName])}
                />
              </div>
            );
          })
        )}
        <AdvancedJsonCollapse
          disabled={disabled}
          label="原始 JSON（字段名 -> 取值数组）"
          onChange={(value) => setFieldValues(parseJsonRecord(value))}
          value={JSON.stringify(normalizeFieldValues(fieldValues), null, 2)}
        />
      </EntitySectionCard>
      <div className={cx("entity-design-panel-actions")}>
        <Button
          disabled={disabled}
          onClick={() =>
            onAction?.({
              action: "submit_static_data",
              entity_id: entityId,
              seed_rows: serializeSeedRows(seedRows),
              field_values: normalizeFieldValues(fieldValues),
            })
          }
          type="primary"
        >
          保存静态数据并生成设计
        </Button>
      </div>
    </div>
  );
}

// ---------- 第 ② 步：数据库方案输入 ----------

export function EntityDatabaseDesignPanel({
  disabled,
  entityId,
  onAction,
  onBackToSource,
  target,
}: {
  disabled?: boolean
  entityId: string
  onAction?: (action: WorkflowEntityDesignAction) => void
  onBackToSource?: () => void
  target: WorkflowDetailReviewTarget
}): ReactElement {
  const databaseDesign = objectValue(target.database_design);
  const entityFields = recordItems(target.fields);
  const availableTables = recordItems(databaseDesign.available_tables);
  const selectedTable = objectValue(databaseDesign.selected_table);
  const selectedTableName = String(
    selectedTable.name || databaseDesign.matched_table || "",
  );
  const tableColumns = recordItems(selectedTable.columns);
  const tableQueryMessage = String(databaseDesign.table_query_message || "");
  const storedBindings = recordListChange(undefined, databaseDesign.bindings);
  // 初始绑定行：优先使用后端已存绑定，否则按实体字段预填空行等待用户选择表列。
  const bindingsInitial = useMemo(
    () =>
      storedBindings.length > 0
        ? storedBindings
        : entityFields.map((field) => ({
            entity_field: String(field.name || field.label || ""),
            table_column: "",
            rule: "",
          })),
    [entityFields, storedBindings],
  );
  const [bindings, setBindings] = useState<Array<Record<string, unknown>>>(
    bindingsInitial,
  );
  // 切换目标表后重置绑定行，避免残留绑定指向旧表。
  useEffect(() => {
    setBindings(bindingsInitial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTableName]);
  const tableSelectOptions = tableColumns.map((column) => ({
    label: String(column.name || ""),
    value: String(column.name || ""),
  }));
  return (
    <div className={cx("entity-design-panel")}>
      <EntityDesignPanelHeader
        eyebrow="实体设计 · 数据库方案"
        onBack={onBackToSource}
        title="补充数据库方案"
      />
      <EntitySectionCard
        extra={
          availableTables.length > 0 ? (
            <Button
              disabled={disabled}
              onClick={() =>
                onAction?.({ action: "list_tables", entity_id: entityId })
              }
              size="small"
              type="text"
            >
              重新查询
            </Button>
          ) : undefined
        }
        title="选择数据表"
      >
        {availableTables.length === 0 ? (
          <>
            <Text
              className={cx("entity-readonly-empty")}
              type="secondary"
            >
              {tableQueryMessage || "点击下方按钮查询当前数据库的表清单。"}
            </Text>
            <Button
              block
              className={cx("entity-row-editor-add")}
              disabled={disabled || !entityId}
              icon={<DatabaseOutlined />}
              onClick={() =>
                onAction?.({ action: "list_tables", entity_id: entityId })
              }
              type="dashed"
            >
              查询当前数据库表
            </Button>
          </>
        ) : (
          <div className={cx("entity-design-table-picker")}>
            {availableTables.map((table) => {
              const name = String(table.name || "");
              const selected = selectedTableName === name;
              return (
                <button
                  className={cx(
                    "entity-design-table-option",
                    selected && "is-selected",
                  )}
                  disabled={disabled}
                  key={name}
                  onClick={() =>
                    onAction?.({
                      action: "select_table",
                      entity_id: entityId,
                      table_name: name,
                    })
                  }
                  type="button"
                >
                  <span className={cx("entity-design-table-option-name")}>
                    {name}
                  </span>
                  {String(table.comment || "") ? (
                    <span className={cx("entity-design-table-option-comment")}>
                      {String(table.comment)}
                    </span>
                  ) : null}
                  {selected ? <CheckOutlined /> : null}
                </button>
              );
            })}
          </div>
        )}
      </EntitySectionCard>
      {selectedTableName ? (
        <EntitySectionCard title={`表字段信息 · ${selectedTableName}`}>
          {tableColumns.length === 0 ? (
            <Text className={cx("entity-readonly-empty")} type="secondary">
              {tableQueryMessage || "未读取到该表的字段信息，请返回重新选择。"}
            </Text>
          ) : (
            <ReadonlyGrid
              columns={[
                {
                  key: "name",
                  label: "列名",
                  render: (row) => String(row.name || ""),
                },
                {
                  key: "type",
                  label: "类型",
                  render: (row) => String(row.type || ""),
                },
                {
                  key: "nullable",
                  label: "可空",
                  render: (row) => (row.nullable ? "是" : "否"),
                },
                {
                  key: "comment",
                  label: "说明",
                  render: (row) => String(row.comment || ""),
                },
              ]}
              emptyText="该表暂无字段信息"
              rows={tableColumns}
            />
          )}
        </EntitySectionCard>
      ) : null}
      {selectedTableName && tableColumns.length > 0 ? (
        <EntitySectionCard title="字段绑定">
          <RowListEditor
            columns={[
              {
                key: "entity_field",
                label: "目标字段",
                placeholder: "例如 product_name",
              },
              {
                key: "table_column",
                label: "表列",
                options: tableSelectOptions,
                placeholder: "选择表列",
                type: "select",
              },
            ]}
            disableAdd
            disabled={disabled}
            emptyText="暂无绑定，请先选择数据表。"
            onChange={setBindings}
            rows={bindings}
          />
        </EntitySectionCard>
      ) : null}
      <div className={cx("entity-design-panel-actions")}>
        <Button
          disabled={
            disabled ||
            !selectedTableName ||
            !bindings.some(
              (row) => String(row.table_column || "").trim() !== "",
            )
          }
          onClick={() =>
            onAction?.({
              action: "submit_bindings",
              entity_id: entityId,
              matched_table: selectedTableName,
              bindings: normalizeObjectRows(bindings),
            })
          }
          type="primary"
        >
          确认数据库方案
        </Button>
      </div>
    </div>
  );
}

// ---------- 数据库方案（接口/页面确认卡内只读展示） ----------

/** 只读展示实体数据库方案：仅显示实体选定的数据库与目标表，以及字段绑定关系。 */
function EntityDatabaseDesignSection({
  databaseDesign,
}: {
  databaseDesign: Record<string, unknown>
}): ReactElement {
  const design = objectValue(databaseDesign);
  const bindings = recordItems(design.bindings);
  const schemaContext = objectValue(design.schema_context);
  const databaseName = String(
    design.database_name || schemaContext.database || "未选择"
  );
  return (
    <EntitySectionCard title="数据库方案">
      <EntityMetricChips
        items={[
          { label: "数据库", value: databaseName },
          { label: "目标表", value: String(design.matched_table || "未匹配") },
        ]}
      />
      <EntitySectionCard title="字段绑定">
        <ReadonlyGrid
          columns={[
            {
              key: "entity_field",
              label: "目标字段",
              render: (row) => String(row.entity_field || row.name || "-"),
            },
            {
              key: "table_column",
              label: "表列",
              render: (row) => String(row.table_column || row.column || "-"),
            },
          ]}
          emptyText="暂无绑定，请先选择数据表。"
          rows={bindings}
        />
      </EntitySectionCard>
    </EntitySectionCard>
  );
}

// ---------- 外部 API 方案（确认卡内展示与编辑） ----------

function EntityExternalApiDesignSection({
  changes,
  externalApiDesign,
  disabled,
  onChange,
}: {
  changes: Record<string, unknown>
  externalApiDesign: Record<string, unknown>
  disabled?: boolean
  onChange: (field: string, value: unknown) => void
}): ReactElement {
  const design = objectChange(changes.external_api_design, externalApiDesign);
  const operations = recordItems(design.operations);
  const connection = objectValue(design.connection);
  void disabled;
  void onChange;
  return (
    <EntitySectionCard
      title="外部 API 方案"
    >
      <EntityMetricChips
        items={[
          { label: "上游操作", value: String(operations.length) },
          { label: "Base URL 配置键", value: String(connection.base_url_config_key || "-") },
        ]}
      />
      {operations.map((operation) => {
        const apiInfo = objectValue(operation.api_info);
        const response = objectValue(operation.response_handling);
        return (
          <EntitySectionCard key={String(operation.operation_id || apiInfo.path || "operation")} title={`${String(operation.name || "未命名操作")} · ${String(apiInfo.method || "GET")} ${String(apiInfo.path || "")}`}>
            <EntityMetricChips items={[
              { label: "关联 Endpoint", value: String(recordItems(operation.endpoint_refs).length) },
              { label: "实体映射", value: response.entity_payload === true ? String(recordItems(operation.field_mappings).length) : "不适用" },
            ]} />
          </EntitySectionCard>
        );
      })}
    </EntitySectionCard>
  );
}

// ---------- 静态数据方案（确认卡内展示与编辑） ----------

// 字段取值约束编辑器：手动逐行添加“字段 + 允许取值”，不自动列全字段。
function FieldValueConstraintEditor({
  disabled,
  entityFields,
  fieldValues,
  onChange,
}: {
  disabled?: boolean
  entityFields?: Array<Record<string, unknown>>
  fieldValues: Record<string, string[]>
  onChange: (values: Record<string, string[]>) => void
}): ReactElement {
  const fieldOptions = (entityFields || [])
    .map((field) => ({
      label: String(field.label || field.name || ""),
      value: String(field.name || field.label || "").trim(),
    }))
    .filter((option) => option.value);
  const [rows, setRows] = useState<Array<{ field: string; values: string[] }>>(
    // enum 字段默认带入 ProjectPlan 枚举值，普通字段仍由用户手动添加。
    () => defaultConstraintRows(entityFields || [], fieldValues),
  );
  // entityFields 变化（载荷后到、字段补齐等）时合并缺失的 enum 默认行；
  // 只在字段签名变化时触发，不覆盖用户对既有行的编辑或删除。
  const processedFieldsSignatureRef = useRef<string>("");
  useEffect(() => {
    const signature = JSON.stringify(
      (entityFields || []).map((field) => [
        String(field.name || field.label || "").trim(),
        String(field.type || "").trim(),
        field.enum_values,
      ]),
    );
    if (signature === processedFieldsSignatureRef.current) return;
    processedFieldsSignatureRef.current = signature;
    setRows((current) => {
      const currentFields = new Set(
        current.map((row) => row.field.trim()).filter(Boolean),
      );
      const defaults = defaultConstraintRows(
        entityFields || [],
        constraintRowsToFieldValues(current),
      );
      const missing = defaults.filter(
        (row) => !currentFields.has(row.field),
      );
      return missing.length > 0 ? [...current, ...missing] : current;
    });
  }, [entityFields]);
  const updateRow = (
    index: number,
    patch: { field?: string; values?: string[] },
  ): void => {
    const next = rows.map((row, rowIndex) =>
      rowIndex === index ? { ...row, ...patch } : row,
    );
    setRows(next);
    onChange(constraintRowsToFieldValues(next));
  };
  const removeRow = (index: number): void => {
    const next = rows.filter((_row, rowIndex) => rowIndex !== index);
    setRows(next);
    onChange(constraintRowsToFieldValues(next));
  };
  const addRow = (): void => {
    const next = [...rows, { field: "", values: [] }];
    setRows(next);
    onChange(constraintRowsToFieldValues(next));
  };
  // 字段下拉排除已被其他约束行占用的字段，避免同一字段重复约束。
  const usedFields = new Set(rows.map((row) => row.field.trim()).filter(Boolean));
  const availableOptions = (rowIndex: number): Array<{ label: string; value: string }> =>
    fieldOptions.filter(
      (option) =>
        option.value === rows[rowIndex]?.field || !usedFields.has(option.value),
    );
  return (
    <div className={cx("entity-design-constraint-editor")}>
      {rows.length === 0 ? (
        <Text className={cx("entity-row-editor-empty")} type="secondary">
          暂无取值约束，点击下方添加约束。
        </Text>
      ) : (
        <div className={cx("entity-design-constraint-list")}>
          {rows.map((row, index) => (
            <div className={cx("entity-design-constraint-row")} key={index}>
              <Select
                allowClear
                disabled={disabled}
                onChange={(value) => updateRow(index, { field: String(value || "") })}
                options={availableOptions(index)}
                placeholder="选择字段"
                value={row.field || undefined}
              />
              <Select
                disabled={disabled}
                mode="tags"
                onChange={(values) =>
                  updateRow(index, { values: normalizeStringList(values) })
                }
                placeholder="输入允许取值后回车"
                value={row.values}
              />
              <Button
                aria-label="删除该约束"
                danger
                disabled={disabled}
                icon={<DeleteOutlined />}
                onClick={() => removeRow(index)}
                size="small"
                type="text"
              />
            </div>
          ))}
        </div>
      )}
      <Button
        block
        className={cx("entity-row-editor-add")}
        disabled={disabled}
        icon={<PlusOutlined />}
        onClick={addRow}
        type="dashed"
      >
        添加约束
      </Button>
    </div>
  );
}

function EntityStaticDesignSection({
  changes,
  disabled,
  entityFields,
  onChange,
  staticDesign,
}: {
  changes: Record<string, unknown>
  disabled?: boolean
  entityFields?: Array<Record<string, unknown>>
  onChange: (field: string, value: unknown) => void
  staticDesign: Record<string, unknown>
}): ReactElement {
  const design = objectChange(changes.static_design, staticDesign);
  const seedRows = recordListChange(design.seed_rows, staticDesign.seed_rows);
  const fields = recordItems(entityFields);
  const columns: RowColumn[] =
    fields.length > 0
      ? fields.map((field) => ({
          key: String(field.name || field.label || ""),
          label: String(field.label || field.name || "字段"),
        }))
      : [{ key: "value", label: "值" }];
  return (
    <EntitySectionCard title={`静态数据方案 · ${seedRows.length} 条种子记录`}>
      <EntitySectionCard title="种子数据">
        <RowListEditor
          columns={columns}
          disabled={disabled}
          emptyText="暂无种子记录，点击下方添加。"
          onChange={(rows) =>
            onChange("static_design", {
              ...design,
              seed_rows: serializeSeedRows(rows),
            })
          }
          rows={seedRows}
        />
        <AdvancedJsonCollapse
          disabled={disabled}
          label="原始 JSON（种子记录数组）"
          onChange={(value) =>
            onChange("static_design", {
              ...design,
              seed_rows: parseJsonList(value),
            })
          }
          value={JSON.stringify(seedRows, null, 2)}
        />
      </EntitySectionCard>
    </EntitySectionCard>
  );
}

// ---------- 第 ③ 步：实体确认卡 ----------

export function EntityReviewEditor({
  changes,
  disabled,
  onChange,
  target,
}: {
  changes: Record<string, unknown>
  disabled?: boolean
  onChange: (field: string, value: unknown) => void
  target: WorkflowDetailReviewTarget
}): ReactElement {
  const fields = recordItems(target.fields);
  const databaseDesign = objectValue(target.database_design);
  const externalApiDesign = objectValue(target.external_api_design);
  const staticDesign = objectValue(target.static_design);
  const entityName = String(target.name || target.entity_id || "未命名实体");
  const dataSourceType = stringChange(changes.data_source_type, target.data_source_type);
  return (
    <div className={cx("workflow-detail-review-fields")}>
      <section className={cx("entity-design-hero")}>
        <span className={cx("entity-design-hero-icon")} aria-hidden="true">
          <DatabaseOutlined />
        </span>
        <div className={cx("entity-design-hero-copy")}>
          <div className={cx("entity-design-hero-title-row")}>
            <Text strong>{entityName}</Text>
            {target.entity_id ? <code>{target.entity_id}</code> : null}
            {target.module_id ? <Tag>{target.module_id}</Tag> : null}
          </div>
          {target.description ? (
            <Text type="secondary">{target.description}</Text>
          ) : null}
        </div>
        <div className={cx("entity-design-hero-tags")}>
          <Tag color={dataSourceType ? "purple" : "default"}>
            {DATA_SOURCE_LABELS[dataSourceType] || "待选择数据源"}
          </Tag>
          <Tag>{String(target.design_stage || "待设计")}</Tag>
        </div>
      </section>

      <EntitySectionCard title="字段清单（项目计划契约，只读）">
        <ReadonlyGrid
          columns={[
            { key: "label", label: "展示名称", render: (row) => String(row.label || row.name || "") },
            { key: "name", label: "字段名", render: (row) => String(row.name || "") },
            { key: "type", label: "类型", render: (row) => String(row.type || "text") },
            {
              key: "required",
              label: "必填",
              render: (row) => (row.required ? "必填" : "可选"),
            },
            { key: "column_type", label: "列类型", render: (row) => String(row.column_type || "") },
            { key: "description", label: "说明", render: (row) => String(row.description || "") },
          ]}
          emptyText="暂无字段定义"
          rows={fields}
        />
      </EntitySectionCard>

      {Object.keys(databaseDesign).length > 0 ? (
        <EntityDatabaseDesignSection databaseDesign={databaseDesign} />
      ) : null}
      {Object.keys(externalApiDesign).length > 0 ? (
        <EntityExternalApiDesignSection
          changes={changes}
          externalApiDesign={externalApiDesign}
          disabled={disabled}
          onChange={onChange}
        />
      ) : null}
      {Object.keys(staticDesign).length > 0 ? (
        <EntityStaticDesignSection
          changes={changes}
          disabled={disabled}
          entityFields={target.fields}
          onChange={onChange}
          staticDesign={staticDesign}
        />
      ) : null}

    </div>
  );
}

// ---------- 单一实体设计卡片（本地连续操作 + 按需 AI 辅助） ----------

const DEFAULT_SOURCE_OPTIONS: Array<{
  value: string
  label: string
  available?: boolean
}> = [
  { value: "database", label: "数据库" },
  { value: "external_api", label: "外部 API" },
  { value: "static", label: "静态数据" },
];

const AI_ASSIST_LABELS: Record<string, string> = {
  table_selection: "AI 智能选表",
  bindings: "AI 字段映射",
  api_mapping: "AI 接口映射",
  seed_data: "AI 生成种子数据",
};

const AI_ASSIST_HINTS: Record<string, string> = {
  table_selection: "根据已有库表推荐目标表并生成字段映射",
  bindings: "根据已选表为实体字段生成映射",
  api_mapping: "根据返回体为实体字段生成映射",
  seed_data: "根据实体字段生成示例种子数据，生成结果将直接覆盖种子数据表",
};

function initialBindingsFrom(
  databaseDesign: Record<string, unknown>,
  entityFields: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  const stored = recordListChange(undefined, databaseDesign.bindings);
  if (stored.length > 0) return stored;
  return entityFields.map((field) => ({
    entity_field: String(field.name || field.label || ""),
    table_column: "",
    rule: "",
  }));
}

function AiAssistTrigger({
  assistType,
  disabled,
  loading,
  onSubmit,
}: {
  assistType: string
  disabled?: boolean
  loading?: boolean
  onSubmit: (assistType: string) => void
}): ReactElement {
  return (
    <Tooltip placement="bottom" title={AI_ASSIST_HINTS[assistType]}>
      <Button
        disabled={disabled}
        icon={<BulbOutlined />}
        loading={loading}
        onClick={() => onSubmit(assistType)}
        size="small"
      >
        {AI_ASSIST_LABELS[assistType] || "AI 辅助"}
      </Button>
    </Tooltip>
  );
}

function AiSuggestionArea({
  note,
  onAdopt,
  onAdoptAll,
  onDismiss,
  suggestions,
}: {
  note?: string
  onAdopt: (suggestion: WorkflowEntityDesignSuggestion) => void
  onAdoptAll: () => void
  onDismiss: (suggestion: WorkflowEntityDesignSuggestion) => void
  suggestions: WorkflowEntityDesignSuggestion[]
}): ReactElement | null {
  if (suggestions.length === 0 && !note) return null;
  return (
    <div className={cx("entity-design-ai-suggestions")}>
      {suggestions.map((suggestion) => (
        <div className={cx("entity-design-ai-suggestion")} key={suggestion.id || suggestion.label}>
          <div className={cx("entity-design-ai-suggestion-copy")}>
            <Text strong>{suggestion.label}</Text>
            {suggestion.note ? (
              <Text type="secondary">{suggestion.note}</Text>
            ) : null}
          </div>
          <Button
            onClick={() => onAdopt(suggestion)}
            size="small"
            type="primary"
            ghost
          >
            采纳
          </Button>
          <Button onClick={() => onDismiss(suggestion)} size="small" type="text">
            忽略
          </Button>
        </div>
      ))}
      {suggestions.length > 1 ? (
        <Button onClick={onAdoptAll} size="small">
          全部采纳
        </Button>
      ) : null}
      {note ? (
        <Text className={cx("entity-design-ai-note")} type="secondary">
          {note}
        </Text>
      ) : null}
    </div>
  );
}

// 绑定关系展示：实体字段 → 表列 的只读列表。
function TableSelectionBindings({
  bindings,
  entityFields,
}: {
  bindings: Array<Record<string, unknown>>
  entityFields: Array<Record<string, unknown>>
}): ReactElement {
  if (bindings.length === 0) {
    return <Text type="secondary">AI 未给出绑定建议，可确认后手动绑定。</Text>;
  }
  const fieldLabelByName = new Map<string, string>();
  entityFields.forEach((field) => {
    const name = String(field.name || "");
    if (name) {
      fieldLabelByName.set(name, String(field.label || field.name || name));
    }
  });
  const rows = bindings.map((binding) => {
    const entityField = String(binding.entity_field || "");
    return {
      label: fieldLabelByName.get(entityField) || entityField,
      entity_field: entityField,
      table_column: String(binding.table_column || ""),
    };
  });
  return (
    <ReadonlyGrid
      columns={[
        {
          key: "label",
          label: "实体字段名称",
          render: (row) => String(row.label || ""),
        },
        {
          key: "entity_field",
          label: "实体字段",
          render: (row) => String(row.entity_field || ""),
        },
        {
          key: "table_column",
          label: "数据库列名",
          render: (row) => String(row.table_column || ""),
        },
      ]}
      emptyText="暂无绑定关系"
      rows={rows}
    />
  );
}

// AI 智能选表推荐：展示所选数据库、推荐表的真实字段信息与绑定关系。
function TableSelectionRecommendView({
  databaseName,
  entityFields,
  missingFields,
  suggestion,
  workspaceRoot,
}: {
  databaseName?: string
  entityFields: Array<Record<string, unknown>>
  missingFields?: {
    table_name?: string
    eligible?: boolean
    fields?: Array<Record<string, unknown>>
  }
  suggestion: WorkflowEntityDesignSuggestion
  workspaceRoot?: string
}): ReactElement {
  const payload = suggestion.payload || {};
  const tableName = String(payload.table_name || "");
  const bindings = Array.isArray(payload.bindings)
    ? payload.bindings.filter(isRecord)
    : [];
  const missingFieldRows = (missingFields?.fields || []).filter(isRecord);
  const [columns, setColumns] = useState<Array<Record<string, unknown>>>([]);
  const [columnsLoading, setColumnsLoading] = useState(false);
  const [columnsError, setColumnsError] = useState("");
  // 推荐确认时按推荐表名拉取真实字段信息，供用户核对。
  useEffect(() => {
    if (!tableName || !workspaceRoot) return;
    let cancelled = false;
    setColumnsLoading(true);
    setColumnsError("");
    void fetchDatabaseTableColumns({
      workspace_root: workspaceRoot,
      table_name: tableName,
    })
      .then((result) => {
        if (cancelled) return;
        if (result.status === "ok") {
          setColumns(result.columns);
        } else {
          setColumns([]);
          setColumnsError(result.message || "未读取到该表的字段信息。");
        }
      })
      .catch((error) => {
        if (cancelled) return;
        setColumns([]);
        setColumnsError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (!cancelled) setColumnsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tableName, workspaceRoot]);
  return (
    <div className={cx("entity-design-table-confirm")}>
      <div className={cx("entity-design-table-confirm-target")}>
        <div className={cx("entity-design-table-confirm-field")}>
          <Text type="secondary">所选数据库</Text>
          <Text strong>{databaseName || "当前工作区数据库"}</Text>
        </div>
        <div className={cx("entity-design-table-confirm-field")}>
          <Text type="secondary">推荐数据表</Text>
          <Text strong>{tableName}</Text>
          {suggestion.note ? (
            <Text type="secondary">{suggestion.note}</Text>
          ) : null}
        </div>
      </div>
      <div className={cx("entity-design-table-confirm-section")}>
        <Text type="secondary">所选数据表的字段信息</Text>
        {columnsLoading ? (
          <Spin size="small" />
        ) : columnsError ? (
          <Text type="secondary">{columnsError}</Text>
        ) : (
          <ReadonlyGrid
            columns={[
              { key: "name", label: "列名", render: (row) => String(row.name || "") },
              { key: "type", label: "类型", render: (row) => String(row.type || "") },
              {
                key: "nullable",
                label: "可空",
                render: (row) => (row.nullable ? "是" : "否"),
              },
              {
                key: "comment",
                label: "说明",
                render: (row) => String(row.comment || ""),
              },
            ]}
            emptyText="该表暂无字段信息"
            rows={columns}
          />
        )}
      </div>
      <div className={cx("entity-design-table-confirm-section")}>
        <Text type="secondary">绑定关系</Text>
        <TableSelectionBindings bindings={bindings} entityFields={entityFields} />
      </div>
      {missingFieldRows.length > 0 ? (
        <div className={cx("entity-design-table-confirm-section")}>
          <Text type="secondary">所选表缺失字段</Text>
          <Alert
            message="以下实体字段在所选表中没有对应列"
            description={
              missingFields?.eligible
                ? "可在下方选择「补充字段」补全，或「忽略缺失，接受建议」。"
                : "当前表不是实体目标表，无法自动补列，请改选目标表或调整绑定。"
            }
            showIcon
            type={missingFields?.eligible ? "warning" : "info"}
          />
          <ReadonlyGrid
            columns={[
              {
                key: "label",
                label: "实体字段名称",
                render: (row) => String(row.label || row.entity_field || ""),
              },
              {
                key: "entity_field",
                label: "实体字段",
                render: (row) => String(row.entity_field || ""),
              },
              {
                key: "type",
                label: "列类型",
                render: (row) => String(row.type || ""),
              },
            ]}
            emptyText="暂无缺失字段"
            rows={missingFieldRows}
          />
        </div>
      ) : null}
    </div>
  );
}

// AI 智能选表：无合适库表时的新建表方案（表名与列结构由实体定义确定）。
function CreateTableProposalView({
  entityFields,
  proposal,
  suggestion,
}: {
  entityFields: Array<Record<string, unknown>>
  proposal: Record<string, unknown>
  suggestion: WorkflowEntityDesignSuggestion
}): ReactElement {
  const tableName = String(proposal.name || "");
  const columns = recordItems(proposal.columns);
  const payload = suggestion.payload || {};
  const bindings = Array.isArray(payload.bindings)
    ? payload.bindings.filter(isRecord)
    : [];
  return (
    <div className={cx("entity-design-table-confirm")}>
      <Alert
        message="当前库表没有与实体语义匹配的表"
        description={suggestion.note || "AI 建议按实体定义新建目标表。"}
        showIcon
        type="info"
      />
      <div className={cx("entity-design-table-confirm-target")}>
        <div className={cx("entity-design-table-confirm-field")}>
          <Text type="secondary">新建数据表</Text>
          <Text strong>{tableName}</Text>
        </div>
      </div>
      <div className={cx("entity-design-table-confirm-section")}>
        <Text type="secondary">目标表字段结构（按实体定义）</Text>
        <ReadonlyGrid
          columns={[
            { key: "name", label: "列名", render: (row) => String(row.name || "") },
            { key: "type", label: "类型", render: (row) => String(row.type || "") },
            {
              key: "nullable",
              label: "可空",
              render: (row) => (row.nullable ? "是" : "否"),
            },
            {
              key: "comment",
              label: "说明",
              render: (row) => String(row.comment || ""),
            },
          ]}
          emptyText="暂无字段"
          rows={columns}
        />
      </div>
      <div className={cx("entity-design-table-confirm-section")}>
        <Text type="secondary">绑定关系</Text>
        <TableSelectionBindings bindings={bindings} entityFields={entityFields} />
      </div>
    </div>
  );
}

// AI 智能选表流程卡：内嵌于工作流消息中，支持多轮追问；
// 推荐表或新建表方案经用户确认后应用到实体设计卡片。
function TableSelectionFlowCard({
  aiMessages,
  appliedId,
  appliedRecord,
  databaseName,
  ddlExecution,
  ddlPending,
  disabled,
  draft,
  entityFields,
  missingFields,
  onApply,
  onClose,
  onCreateTable,
  onDismiss,
  onDraftChange,
  onSend,
  onSupplementFields,
  pending,
  suggestion,
  workspaceRoot,
}: {
  aiMessages: Array<{ role: "user" | "assistant"; content: string }>
  appliedId?: string
  appliedRecord?: WorkflowEntityDesignSuggestion | null
  databaseName?: string
  ddlExecution?: {
    status?: string
    table_name?: string
    columns?: string[]
    message?: string
  }
  ddlPending?: boolean
  disabled?: boolean
  draft: string
  entityFields: Array<Record<string, unknown>>
  missingFields?: {
    table_name?: string
    eligible?: boolean
    fields?: Array<Record<string, unknown>>
  }
  onApply: (suggestion: WorkflowEntityDesignSuggestion) => void
  onClose: () => void
  onCreateTable: (suggestion: WorkflowEntityDesignSuggestion) => void
  onDismiss: (suggestion: WorkflowEntityDesignSuggestion) => void
  onDraftChange: (value: string) => void
  onSend: (text: string) => void
  onSupplementFields: (suggestion: WorkflowEntityDesignSuggestion) => void
  pending: boolean
  suggestion?: WorkflowEntityDesignSuggestion
  workspaceRoot?: string
}): ReactElement {
  const payload = suggestion?.payload || {};
  const createTable = isRecord(payload.create_table)
    ? payload.create_table
    : undefined;
  const send = (): void => {
    if (!draft.trim() || pending) return;
    onSend(draft.trim());
  };
  // 留痕模式：已确认的建议只静态展示，不提供输入与操作按钮。
  const recordMode = Boolean(appliedRecord) && !suggestion && !ddlPending && !ddlExecution;
  return (
    <section className={cx("entity-design-table-flow-card")}>
      <header className={cx("entity-design-table-flow-header")}>
        <span className={cx("entity-design-table-flow-title")}>
          <BulbOutlined aria-hidden="true" />
          <Text strong>AI 智能选表</Text>
        </span>
        {!recordMode ? (
          <Button
            aria-label="关闭 AI 智能选表"
            disabled={disabled}
            icon={<CloseOutlined />}
            onClick={onClose}
            size="small"
            type="text"
          />
        ) : null}
      </header>
      {recordMode && appliedRecord ? (
        <div className={cx("entity-design-table-flow-record")}>
          <TableSelectionRecommendView
            databaseName={databaseName}
            entityFields={entityFields}
            suggestion={appliedRecord}
            workspaceRoot={workspaceRoot}
          />
        </div>
      ) : (
        <>
      {aiMessages.length > 0 ? (
        <div className={cx("entity-design-table-flow-conversation")}>
          {aiMessages.map((message, index) => (
            <div
              className={cx(
                "entity-design-table-flow-message",
                `is-${message.role}`,
              )}
              key={`${index}`}
            >
              <Text>{message.content}</Text>
            </div>
          ))}
        </div>
      ) : null}
      {ddlExecution ? (
        ddlExecution.status === "completed" ||
        ddlExecution.status === "already_satisfied" ? (
          <>
            <Alert
              message={
                ddlExecution.status === "already_satisfied"
                  ? "无需执行 DDL"
                  : "DDL 执行完成"
              }
              description={
                ddlExecution.message ||
                `已补充字段：${(ddlExecution.columns || []).join("、") || "无"}`
              }
              showIcon
              type="success"
            />
            <div className={cx("entity-design-table-flow-actions")}>
              <div className={cx("entity-design-table-flow-actions-main")}>
                <Button
                  disabled={disabled}
                  icon={<CheckCircleOutlined />}
                  onClick={onClose}
                  size="small"
                  type="primary"
                >
                  完成
                </Button>
              </div>
            </div>
          </>
        ) : (
          <>
            <Alert
              message="DDL 执行失败"
              description={
                ddlExecution.message || "未能执行补列 DDL，请重试或忽略缺失。"
              }
              showIcon
              type="error"
            />
            <div className={cx("entity-design-table-flow-actions")}>
              <div className={cx("entity-design-table-flow-actions-main")}>
                <Button
                  disabled={disabled}
                  onClick={onClose}
                  size="small"
                >
                  取消
                </Button>
              </div>
            </div>
          </>
        )
      ) : (
        <>
          {pending ? (
            <div className={cx("entity-design-table-flow-pending")}>
              <Spin size="small" />
              <Text type="secondary">正在分析库表并生成方案…</Text>
            </div>
          ) : null}
          {suggestion ? (
            <>
              {createTable ? (
                <CreateTableProposalView
                  entityFields={entityFields}
                  proposal={createTable}
                  suggestion={suggestion}
                />
              ) : (
                <TableSelectionRecommendView
                  databaseName={databaseName}
                  entityFields={entityFields}
                  missingFields={missingFields}
                  suggestion={suggestion}
                  workspaceRoot={workspaceRoot}
                />
              )}
              <div className={cx("entity-design-table-flow-actions")}>
                <div className={cx("entity-design-table-flow-actions-main")}>
                  <Button
                    disabled={disabled}
                    onClick={() => onDismiss(suggestion)}
                    size="small"
                  >
                    取消
                  </Button>
                  {createTable ? (
                    <Button
                      disabled={disabled}
                      icon={<CheckCircleOutlined />}
                      onClick={() => onCreateTable(suggestion)}
                      size="small"
                      type="primary"
                    >
                      确认建表并应用
                    </Button>
                  ) : missingFields?.eligible &&
                    (missingFields.fields || []).length > 0 ? (
                    <>
                      <Button
                        disabled={disabled}
                        onClick={() => onApply(suggestion)}
                        size="small"
                        type="primary"
                      >
                        忽略缺失，接受建议
                      </Button>
                      <Button
                        disabled={disabled}
                        icon={<PlusOutlined />}
                        onClick={() => onSupplementFields(suggestion)}
                        size="small"
                        type="primary"
                      >
                        补充字段
                      </Button>
                    </>
                  ) : (
                    <Button
                      disabled={disabled}
                      icon={<CheckCircleOutlined />}
                      onClick={() => onApply(suggestion)}
                      size="small"
                      type="primary"
                    >
                      接受 AI 建议
                    </Button>
                  )}
                </div>
              </div>
            </>
          ) : appliedId ? (
            <div className={cx("entity-design-table-flow-applied")}>
              <CheckCircleOutlined aria-hidden="true" />
              <Text>已接受 AI 建议，可继续追问或关闭。</Text>
            </div>
          ) : null}
        </>
      )}
      {!recordMode ? (
        <div className={cx("entity-design-table-flow-input")}>
          <Input
            disabled={disabled || pending || ddlPending}
            onChange={(event) => onDraftChange(event.target.value)}
            onPressEnter={send}
            placeholder="追问：例如换一张更简单的表，或没有匹配就建表"
            value={draft}
          />
          <Button
            disabled={disabled || pending || ddlPending || !draft.trim()}
            loading={pending}
            onClick={send}
            type="primary"
          >
            发送
          </Button>
        </div>
      ) : null}
        </>
      )}
    </section>
  );
}

// AI 字段映射确认卡：把多条字段映射建议汇总成一张确认卡，
// 用户一次确认/忽略后应用到卡片，替代逐条采纳列表。
function BindingsConfirmCard({
  disabled,
  entityFields,
  missingFields,
  onApplyAll,
  onDismissAll,
  onSupplementFields,
  selectedTableName,
  suggestions,
}: {
  disabled?: boolean
  entityFields: Array<Record<string, unknown>>
  missingFields?: {
    table_name?: string
    eligible?: boolean
    fields?: Array<Record<string, unknown>>
  }
  onApplyAll: (suggestions: WorkflowEntityDesignSuggestion[]) => void
  onDismissAll: (suggestions: WorkflowEntityDesignSuggestion[]) => void
  onSupplementFields: (suggestions: WorkflowEntityDesignSuggestion[]) => void
  selectedTableName?: string
  suggestions: WorkflowEntityDesignSuggestion[]
}): ReactElement {
  const bindings = suggestions
    .map((suggestion) => suggestion.payload)
    .filter(isRecord);
  const note = suggestions.map((suggestion) => suggestion.note).find(Boolean);
  const missingFieldRows = (missingFields?.fields || []).filter(isRecord);
  return (
    <section className={cx("entity-design-table-flow-card")}>
      <header className={cx("entity-design-table-flow-header")}>
        <span className={cx("entity-design-table-flow-title")}>
          <BulbOutlined aria-hidden="true" />
          <Text strong>AI 字段映射</Text>
        </span>
        {selectedTableName ? (
          <Tag>{selectedTableName}</Tag>
        ) : null}
      </header>
      {note ? <Text type="secondary">{note}</Text> : null}
      <div className={cx("entity-design-table-confirm-section")}>
        <Text type="secondary">建议字段映射</Text>
        <TableSelectionBindings bindings={bindings} entityFields={entityFields} />
      </div>
      {missingFieldRows.length > 0 ? (
        <div className={cx("entity-design-table-confirm-section")}>
          <Text type="secondary">所选表缺失字段</Text>
          <Alert
            message={
              missingFields?.eligible
                ? "确认后将生成 DDL 补充以下缺失字段"
                : "以下字段在所选表中缺失"
            }
            description={
              missingFields?.eligible
                ? "将执行 ALTER TABLE ADD COLUMN 添加缺失字段，确认实体设计后执行。"
                : "当前表不是实体目标表，无法自动补列，请改选目标表或调整绑定。"
            }
            showIcon
            type={missingFields?.eligible ? "warning" : "info"}
          />
          <ReadonlyGrid
            columns={[
              {
                key: "label",
                label: "实体字段名称",
                render: (row) => String(row.label || row.entity_field || ""),
              },
              {
                key: "entity_field",
                label: "实体字段",
                render: (row) => String(row.entity_field || ""),
              },
              {
                key: "type",
                label: "列类型",
                render: (row) => String(row.type || ""),
              },
            ]}
            emptyText="暂无缺失字段"
            rows={missingFieldRows}
          />
        </div>
      ) : null}
      <div className={cx("entity-design-table-flow-actions")}>
        <div className={cx("entity-design-table-flow-actions-main")}>
          <Button
            disabled={disabled}
            onClick={() => onDismissAll(suggestions)}
            size="small"
          >
            取消
          </Button>
          {missingFields?.eligible && missingFieldRows.length > 0 ? (
            <>
              <Button
                disabled={disabled}
                onClick={() => onApplyAll(suggestions)}
                size="small"
                type="primary"
              >
                忽略缺失，接受建议
              </Button>
              <Button
                disabled={disabled}
                icon={<PlusOutlined />}
                onClick={() => onSupplementFields(suggestions)}
                size="small"
                type="primary"
              >
                补充字段
              </Button>
            </>
          ) : (
            <Button
              disabled={disabled}
              icon={<CheckCircleOutlined />}
              onClick={() => onApplyAll(suggestions)}
              size="small"
              type="primary"
            >
              接受 AI 建议
            </Button>
          )}
        </div>
      </div>
    </section>
  );
}

// 实体设计草稿存储：每次动作都会新建消息并重建卡片，选表、绑定、
// AI 多轮对话与新建表方案等本地草稿需要跨消息保留，直到提交设计。
type EntityDesignDraft = {
  sourceType: string
  dbTables: Array<{ name: string; comment: string }>
  dbDatabaseName: string
  selectedTableName: string
  tableColumns: Array<Record<string, unknown>>
  bindings: Array<Record<string, unknown>>
  externalApiDraft: ExternalApiDesignDraft
  seedRows: Array<Record<string, unknown>>
  // 已自动应用的 AI 种子数据生成键（内容序列化），用于防止重复覆盖用户编辑。
  seedDataAppliedKeys: string[]
  fieldValues: Record<string, string[]>
  createTableProposal: Record<string, unknown> | null
  ddlPending: boolean
  pendingDdlSuggestions: WorkflowEntityDesignSuggestion[]
  ddlExecutedColumns: string[]
  ddlCreatedTable: string
  appliedTableSuggestionRecord: WorkflowEntityDesignSuggestion | null
  aiMessages: Array<{ role: "user" | "assistant"; content: string }>
  aiDraft: string
  aiConversationOpen: boolean
  designLocked: boolean
  lastAppliedTableSuggestionId: string
}

const entityDesignDraftStore = new Map<string, EntityDesignDraft>();

// 草稿按“工作区 + 实体”键控，避免删除会话后新会话继承旧草稿。
function entityDesignDraftKey(
  workspaceRoot: string | undefined,
  entityId: string,
): string {
  return `${workspaceRoot || "unknown"}::${entityId}`;
}

/** 清理指定工作区的实体设计草稿；删除会话或项目后调用，避免跨会话缓存。 */
export function clearEntityDesignDraftStore(workspaceRoot?: string): void {
  if (!workspaceRoot) {
    entityDesignDraftStore.clear();
    return;
  }
  const prefix = `${workspaceRoot}::`;
  for (const key of [...entityDesignDraftStore.keys()]) {
    if (key.startsWith(prefix)) {
      entityDesignDraftStore.delete(key);
    }
  }
}

export function EntityDesignCard({
  disabled,
  entityDesign,
  entityTarget,
  onAction,
  onInteraction,
  workspaceRoot,
}: {
  disabled?: boolean
  entityDesign?: WorkflowEntityDesignSummary
  entityTarget?: WorkflowDetailReviewTarget
  onAction?: (action: WorkflowEntityDesignAction) => void
  onInteraction?: () => void
  workspaceRoot?: string
}): ReactElement {
  const entityId = String(entityTarget?.entity_id || entityDesign?.entity_id || "");
  const entityName = String(
    entityTarget?.name || entityDesign?.entity_name || entityId || "未命名实体",
  );
  const entityDescription = String(
    entityTarget?.description || entityDesign?.entity_description || "",
  );
  const entityFields = resolveEntityDesignFields(entityTarget, entityDesign);
  const sourceOptions =
    entityDesign?.data_source_options && entityDesign.data_source_options.length > 0
      ? entityDesign.data_source_options
      : DEFAULT_SOURCE_OPTIONS;
  const validationErrors = entityDesign?.validation_errors || [];
  const aiSuggestions = entityDesign?.ai_suggestions;
  const ddlExecution = entityDesign?.ddl_execution;
  const existingDatabaseDesign = objectValue(entityTarget?.database_design);
  const existingExternalApiDesign = objectValue(entityTarget?.external_api_design);
  const existingStaticDesign = objectValue(entityTarget?.static_design);
  // 从跨消息草稿存储恢复本地设计状态，避免动作往返后卡片被重建丢数据。
  const draftKey = entityDesignDraftKey(workspaceRoot, entityId);
  const storedDraft = entityId ? entityDesignDraftStore.get(draftKey) : undefined;
  // 后端每轮返回完整对话，优先作为对话区数据源；存储仅在无载荷时兜底。
  const payloadAiMessages =
    aiSuggestions?.assist_type === "table_selection"
      ? (aiSuggestions.messages || [])
          .filter(isRecord)
          .map((item) => ({
            role:
              String(item.role || "") === "user"
                ? ("user" as const)
                : ("assistant" as const),
            content: String(item.content || ""),
          }))
      : [];

  const [sourceType, setSourceType] = useState<string>(
    () =>
      storedDraft?.sourceType ||
      String(entityTarget?.data_source_type || entityDesign?.data_source_type || ""),
  );
  const [dbTables, setDbTables] = useState<Array<{ name: string; comment: string }>>(
    () => storedDraft?.dbTables || [],
  );
  const [dbDatabaseName, setDbDatabaseName] = useState(
    () => storedDraft?.dbDatabaseName || "",
  );
  const [dbQueryMessage, setDbQueryMessage] = useState("");
  const [dbQueryLoading, setDbQueryLoading] = useState(false);
  const selectedTableFromDesign = String(
    existingDatabaseDesign.matched_table ||
      objectValue(existingDatabaseDesign.selected_table).name ||
      "",
  );
  // 确认后的完成载荷只带 matched_table 与 bindings，不带 selected_table；
  // 回退到 schema_context 中目标表的真实列，保证字段绑定区仍能展示。
  const schemaContext = objectValue(existingDatabaseDesign.schema_context);
  const schemaTables = Array.isArray(schemaContext.tables)
    ? schemaContext.tables.filter(isRecord)
    : [];
  const matchedSchemaTable = selectedTableFromDesign
    ? schemaTables.find(
        (table) =>
          String(table.table_name || table.name || "") === selectedTableFromDesign,
      )
    : undefined;
  const designTableColumns = recordItems(
    objectValue(existingDatabaseDesign.selected_table).columns,
  );
  const fallbackTableColumns = recordItems(
    objectValue(matchedSchemaTable).columns,
  );
  const [selectedTableName, setSelectedTableName] = useState<string>(
    () => storedDraft?.selectedTableName || selectedTableFromDesign,
  );
  const [tableColumns, setTableColumns] = useState<Array<Record<string, unknown>>>(
    () =>
      storedDraft?.tableColumns ||
      (designTableColumns.length > 0 ? designTableColumns : fallbackTableColumns),
  );
  const [bindings, setBindings] = useState<Array<Record<string, unknown>>>(() =>
    storedDraft?.bindings || initialBindingsFrom(existingDatabaseDesign, entityFields),
  );
  const {
    draft: externalApiDraft,
    updateDraft: handleExternalApiDraftChange,
    updateOperation: handleExternalApiOperationChange,
  } = useExternalApiDraft({
    existingDesign: existingExternalApiDesign,
    storedDraft: storedDraft?.externalApiDraft,
  });
  const relatedEndpoints = (
    entityTarget?.related_endpoints || entityDesign?.related_endpoints || []
  ).map((item) => ({
    api_contract_id: String(item.api_contract_id || ""),
    endpoint_id: String(item.endpoint_id || ""),
    method: item.method,
    path: item.path,
    summary: item.summary,
  }));
  const activeExternalOperation = externalApiDraft.operations.find(
    (operation) => operation.operationId === externalApiDraft.activeOperationId,
  );
  const [seedRows, setSeedRows] = useState<Array<Record<string, unknown>>>(() =>
    storedDraft?.seedRows ||
    recordListChange(undefined, existingStaticDesign.seed_rows),
  );
  const [fieldValues, setFieldValues] = useState<Record<string, string[]>>(() =>
    storedDraft?.fieldValues || normalizeFieldValues(existingStaticDesign.field_values),
  );
  // 已自动应用的 AI 种子数据生成键，跨消息持久化以幂等覆盖、防止回看旧消息时覆盖用户编辑。
  const [seedDataAppliedKeys, setSeedDataAppliedKeys] = useState<string[]>(
    () => storedDraft?.seedDataAppliedKeys || [],
  );
  const [pendingAssistType, setPendingAssistType] = useState("");
  const [appliedSuggestionIds, setAppliedSuggestionIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [dismissedSuggestionIds, setDismissedSuggestionIds] = useState<Set<string>>(
    () => new Set(),
  );
  // AI 智能选表多轮对话：本地保留对话记录、输入草稿与卡片开关。
  const [aiMessages, setAiMessages] = useState<
    Array<{ role: "user" | "assistant"; content: string }>
  >(() =>
    payloadAiMessages.length > 0
      ? payloadAiMessages
      : storedDraft?.aiMessages || [],
  );
  const [aiDraft, setAiDraft] = useState(() => storedDraft?.aiDraft || "");
  const [aiConversationOpen, setAiConversationOpen] = useState(
    () => Boolean(storedDraft?.aiConversationOpen),
  );
  // 旧版本草稿可能残留锁定标记；新流程确认后不再锁定，卡片保持可编辑。
  const [designLocked] = useState(() => Boolean(storedDraft?.designLocked));
  // 已确认的新建表方案（提交实体设计时随载荷下发并执行）。
  const [createTableProposal, setCreateTableProposal] = useState<
    Record<string, unknown> | null
  >(() => storedDraft?.createTableProposal || null);
  // DDL 生成/审批在新消息中展示，旧卡片不再保留 DDL 加载状态。
  const [ddlPending, setDdlPending] = useState(false);
  const [pendingDdlSuggestions, setPendingDdlSuggestions] = useState<
    WorkflowEntityDesignSuggestion[]
  >(() => storedDraft?.pendingDdlSuggestions || []);
  const [ddlExecutedColumns, setDdlExecutedColumns] = useState<string[]>(
    () => storedDraft?.ddlExecutedColumns || [],
  );
  const [ddlCreatedTable, setDdlCreatedTable] = useState(
    () => storedDraft?.ddlCreatedTable || "",
  );
  const [appliedTableSuggestionRecord, setAppliedTableSuggestionRecord] =
    useState<WorkflowEntityDesignSuggestion | null>(
      () => storedDraft?.appliedTableSuggestionRecord || null,
    );
  const [lastAppliedTableSuggestionId, setLastAppliedTableSuggestionId] = useState(
    () => storedDraft?.lastAppliedTableSuggestionId || "",
  );

  // AI 建议返回后清除对应区域的等待状态。
  useEffect(() => {
    if (aiSuggestions?.assist_type) {
      setPendingAssistType("");
    }
  }, [aiSuggestions]);

  // 静态数据：AI 生成结果直接覆盖种子数据表，不逐条确认；
  // 同一生成内容只应用一次，避免消息重建/回看旧消息时覆盖用户后续编辑。
  useEffect(() => {
    if (aiSuggestions?.assist_type !== "seed_data") return;
    const rows = seedRowsFromSuggestions(aiSuggestions.suggestions);
    if (rows.length === 0) return;
    const key = JSON.stringify(rows);
    if (seedDataAppliedKeys.includes(key)) return;
    setSeedRows(rows);
    setSeedDataAppliedKeys((current) => [...current, key].slice(-10));
    // 表格内容变化后通知消息列表贴底，确保新内容可见。
    onInteraction?.();
  }, [aiSuggestions, onInteraction, seedDataAppliedKeys]);

  // 表选型 AI 的文本回复追加到对话记录，避免重复追加同一条消息。
  useEffect(() => {
    if (aiSuggestions?.assist_type !== "table_selection") return;
    const text = String(aiSuggestions.text || "").trim();
    if (!text) return;
    setAiMessages((current) => {
      const last = current[current.length - 1];
      if (last && last.role === "assistant" && last.content === text) {
        return current;
      }
      return [...current, { role: "assistant", content: text }];
    });
  }, [aiSuggestions]);

  // 本地草稿写入跨消息存储，供动作往返后重建的新卡片恢复。
  useEffect(() => {
    if (!entityId) return;
    entityDesignDraftStore.set(draftKey, {
      sourceType,
      dbTables,
      dbDatabaseName,
      selectedTableName,
      tableColumns,
      bindings,
      externalApiDraft,
      seedRows,
      seedDataAppliedKeys,
      fieldValues,
      createTableProposal,
      ddlPending,
      pendingDdlSuggestions,
      ddlExecutedColumns,
      ddlCreatedTable,
      appliedTableSuggestionRecord,
      aiMessages,
      aiDraft,
      aiConversationOpen,
      designLocked,
      lastAppliedTableSuggestionId,
    });
  }, [
    entityId,
    sourceType,
    dbTables,
    dbDatabaseName,
    selectedTableName,
    tableColumns,
    bindings,
    externalApiDraft,
    seedRows,
    seedDataAppliedKeys,
    fieldValues,
    createTableProposal,
    ddlPending,
    pendingDdlSuggestions,
    ddlExecutedColumns,
    ddlCreatedTable,
    appliedTableSuggestionRecord,
    aiMessages,
    aiDraft,
    aiConversationOpen,
    designLocked,
    lastAppliedTableSuggestionId,
  ]);

  // 拉取当前数据库表清单，供数据源为数据库时的下拉框使用（只读元数据）。
  const handleQueryTables = useCallback(async (): Promise<void> => {
    if (!entityId || !workspaceRoot) {
      setDbQueryMessage("缺少工作区路径，无法查询当前数据库的表清单。");
      return;
    }
    setDbQueryLoading(true);
    setDbQueryMessage("");
    try {
      const result = await listDatabaseTables({
        workspace_root: workspaceRoot,
        entity_id: entityId,
      });
      if (result.status === "ok") {
        setDbTables(result.tables);
        setDbDatabaseName(String(result.database || ""));
        setDbQueryMessage(result.message || "");
      } else {
        setDbTables([]);
        setDbDatabaseName("");
        setDbQueryMessage(result.message || "查询失败");
      }
    } catch (error) {
      setDbTables([]);
      setDbDatabaseName("");
      setDbQueryMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setDbQueryLoading(false);
    }
  }, [entityId, workspaceRoot]);

  // 选定数据库数据源后自动加载表清单到下拉框，无需用户手动点击查询。
  useEffect(() => {
    if (sourceType === "database") {
      void handleQueryTables();
    }
  }, [sourceType, handleQueryTables]);

  const handleSelectTable = async (
    tableName: string,
  ): Promise<Array<Record<string, unknown>>> => {
    if (!tableName || !workspaceRoot) return [];
    setDbQueryLoading(true);
    setDbQueryMessage("");
    try {
      const result = await fetchDatabaseTableColumns({
        workspace_root: workspaceRoot,
        table_name: tableName,
      });
      if (result.status === "ok" && result.columns.length > 0) {
        setSelectedTableName(tableName);
        setTableColumns(result.columns);
        setBindings(
          entityFields.map((field) => ({
            entity_field: String(field.name || field.label || ""),
            table_column: "",
            rule: "",
          })),
        );
        return result.columns;
      } else {
        setDbQueryMessage(result.message || "未读取到该表的字段信息。");
      }
    } catch (error) {
      setDbQueryMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setDbQueryLoading(false);
    }
    return [];
  };

  const buildAiAssistContext = (
    assistType: string,
    userMessage?: string,
  ): Record<string, unknown> => {
    // 多轮对话：把此前对话与当前用户消息一并交给 AI。
    const history = [...aiMessages];
    if (userMessage) {
      history.push({ role: "user", content: userMessage });
    }
    if (assistType === "table_selection") {
      return {
        fields: entityFields,
        available_tables: dbTables,
        current_table: selectedTableName,
        current_bindings: bindings,
        messages: history,
      };
    }
    if (assistType === "bindings") {
      return {
        fields: entityFields,
        table_name: selectedTableName,
        table_columns: tableColumns,
        current_bindings: bindings,
      };
    }
    if (assistType === "api_mapping") {
      const response = tryParseJson(activeExternalOperation?.responseBody || "");
      return {
        fields: entityFields,
        current_mappings: activeExternalOperation?.mappings || [],
        response_body: response || null,
        response_paths: responseFieldPaths(response),
      };
    }
    if (assistType === "seed_data") {
      return {
        fields: entityFields,
        current_seed_rows: seedRows,
        field_values: fieldValues,
      };
    }
    return { fields: entityFields };
  };

  const requestAiAssist = (assistType: string, userMessage?: string): void => {
    if (!entityId) return;
    const trimmedMessage = (userMessage || "").trim();
    if (trimmedMessage) {
      setAiMessages((current) => [
        ...current,
        { role: "user", content: trimmedMessage },
      ]);
    }
    if (assistType === "table_selection") {
      setAiConversationOpen(true);
    }
    setPendingAssistType(assistType);
    onAction?.({
      action: "ai_assist",
      entity_id: entityId,
      assist_type: assistType,
      ...(assistType === "api_mapping" && activeExternalOperation
        ? { operation_id: activeExternalOperation.operationId }
        : {}),
      context: buildAiAssistContext(assistType, trimmedMessage || undefined),
    });
    onInteraction?.();
  };

  // 点击“补充字段”：立即进入 DDL 生成/执行阶段，成功后由 applyDdlSuccess 写入映射。
  const requestSupplementFields = (
    missingFields:
      | {
          table_name?: string
          eligible?: boolean
          fields?: Array<Record<string, unknown>>
        }
      | undefined,
    suggestions: WorkflowEntityDesignSuggestion[],
  ): void => {
    if (!entityId || !missingFields?.eligible) return;
    const fields = (missingFields.fields || []).filter(isRecord);
    if (fields.length === 0) return;
    // 不置位本地 DDL 加载状态：DDL 生成/审批在新消息中展示，避免影响当前卡片。
    setPendingDdlSuggestions(suggestions);
    setDdlExecutedColumns([]);
    onAction?.({
      action: "execute_add_columns",
      entity_id: entityId,
      table_name: String(missingFields.table_name || ""),
      fields: fields.map((field) => ({
        entity_field: String(field.entity_field || ""),
        type: String(field.type || "VARCHAR(255)"),
        nullable: field.nullable !== false,
        comment: String(field.comment || ""),
      })),
    });
    onInteraction?.();
  };

  // 点击“确认建表并应用”：立即按 Database Agent 流程生成并执行建表 DDL。
  const requestCreateTable = (
    suggestion: WorkflowEntityDesignSuggestion,
  ): void => {
    if (!entityId) return;
    const proposal = isRecord(suggestion.payload?.create_table)
      ? suggestion.payload.create_table
      : null;
    if (!proposal) return;
    // 不置位本地 DDL 加载状态：DDL 生成/审批在新消息中展示，避免影响当前卡片。
    setPendingDdlSuggestions([suggestion]);
    setDdlExecutedColumns([]);
    setDdlCreatedTable("");
    onAction?.({
      action: "execute_create_table",
      entity_id: entityId,
      proposal,
    });
    onInteraction?.();
  };

  // DDL 执行成功后：写入目标表；建表与补列都按 AI 建议应用字段绑定，
  // 建表时以 AI 给出的目标表字段生成绑定关系（同名兜底）。
  const applyDdlSuccess = async (
    tableName: string,
    executedColumns: string[],
    status?: string,
  ): Promise<void> => {
    if (!entityId) return;
    const suggestions = pendingDdlSuggestions;
    const isCreateTable = suggestions.some((suggestion) =>
      isRecord(suggestion.payload?.create_table),
    );
    const realColumns = await handleSelectTable(tableName);
    const realColumnSet = new Set(
      realColumns.map((column) => String(column.name || "")),
    );
    const executedSet = new Set(executedColumns.map(String));
    if (isCreateTable) {
      // 建表：AI 识别无对应表并给出目标表字段时，同步生成实体字段绑定关系。
      const suggestion = suggestions.find((item) =>
        isRecord(item.payload?.create_table),
      );
      const payload = suggestion?.payload || {};
      const proposal = isRecord(payload.create_table)
        ? payload.create_table
        : null;
      const proposalColumns =
        proposal && Array.isArray(proposal.columns)
          ? proposal.columns.filter(isRecord)
          : [];
      const proposalColumnSet = new Set(
        proposalColumns.map((column) => String(column.name || "")),
      );
      const suggestedBindings = Array.isArray(payload.bindings)
        ? payload.bindings.filter(isRecord)
        : [];
      if (proposal) setCreateTableProposal(proposal);
      setBindings(
        entityFields.map((field) => {
          const fieldName = String(field.name || field.label || "");
          const match = suggestedBindings.find(
            (item) => String(item.entity_field || "") === fieldName,
          );
          const candidate = String(match?.table_column || "");
          return {
            entity_field: fieldName,
            table_column:
              candidate && proposalColumnSet.has(candidate)
                ? candidate
                : "",
            rule: "",
          };
        }),
      );
    } else {
      // 补列：应用 AI 绑定，并为已补列字段填上同名列映射。
      const candidateMappings: Array<Record<string, unknown>> = [];
      suggestions.forEach((suggestion) => {
        const payload = suggestion.payload || {};
        if (Array.isArray(payload.bindings)) {
          payload.bindings
            .filter(isRecord)
            .forEach((binding) => candidateMappings.push(binding));
        } else if (String(payload.entity_field || "")) {
          candidateMappings.push(payload);
        }
      });
      setBindings((current) => {
        const next = current.map((row) => ({ ...row }));
        candidateMappings.forEach((item) => {
          const entityField = String(item.entity_field || "").trim();
          const tableColumn = String(item.table_column || "").trim();
          if (!entityField || !tableColumn || !realColumnSet.has(tableColumn)) {
            return;
          }
          const target = next.find(
            (row) => String(row.entity_field || "") === entityField,
          );
          if (target) target.table_column = tableColumn;
        });
        next.forEach((row) => {
          const field = String(row.entity_field || "");
          if (executedSet.has(field) && realColumnSet.has(field)) {
            row.table_column = field;
          }
        });
        return next;
      });
    }
    setDdlExecutedColumns(executedColumns);
    setDdlCreatedTable(
      isCreateTable && status === "completed" ? tableName : "",
    );
    setPendingDdlSuggestions([]);
    setDdlPending(false);
  };

  // 收到 DDL 执行结果：成功则写入映射并锁定，失败保留错误供确认卡展示。
  useEffect(() => {
    if (!ddlExecution || pendingDdlSuggestions.length === 0) return;
    // 不再依赖本地 ddlPending：结果在新消息的卡片上到达时直接应用映射。
    setPendingDdlSuggestions([]);
    setDdlPending(false);
    if (
      ddlExecution.status === "completed" ||
      ddlExecution.status === "already_satisfied"
    ) {
      void applyDdlSuccess(
        String(ddlExecution.table_name || ""),
        ddlExecution.columns || [],
        ddlExecution.status,
      );
    }
  }, [ddlExecution, pendingDdlSuggestions]);

  const visibleSuggestions = (assistType: string): WorkflowEntityDesignSuggestion[] => {
    if (aiSuggestions?.assist_type !== assistType) return [];
    if (
      assistType === "api_mapping" &&
      String(aiSuggestions.operation_id || "") !== String(activeExternalOperation?.operationId || "")
    ) return [];
    return (aiSuggestions.suggestions || []).filter(
      (suggestion) =>
        !suggestion.id ||
        (!appliedSuggestionIds.has(suggestion.id) &&
          !dismissedSuggestionIds.has(suggestion.id)),
    );
  };

  const adoptSuggestion = async (
    assistType: string,
    suggestion: WorkflowEntityDesignSuggestion,
  ): Promise<void> => {
    if (assistType === "table_selection") {
      const payload = suggestion.payload || {};
      const createTable = isRecord(payload.create_table)
        ? payload.create_table
        : null;
      if (createTable) {
        // 无合适库表：确认 AI 建议的新建表方案，提交实体设计时随载荷创建。
        const tableName = String(
          createTable.name || payload.table_name || "",
        ).trim();
        const proposalColumns = recordItems(createTable.columns);
        const proposalColumnSet = new Set(
          proposalColumns.map((column) => String(column.name || "")),
        );
        const suggestedBindings = Array.isArray(payload.bindings)
          ? payload.bindings.filter(isRecord)
          : [];
        setCreateTableProposal(createTable);
        setSelectedTableName(tableName);
        setTableColumns(proposalColumns);
        setDbTables((current) => [
          { name: tableName, comment: "新建表（确认实体设计后创建）" },
          ...current.filter((table) => table.name !== tableName),
        ]);
        setBindings(
          entityFields.map((field) => {
            const fieldName = String(field.name || field.label || "");
            const match = suggestedBindings.find(
              (item) => String(item.entity_field || "") === fieldName,
            );
            const candidate = String(match?.table_column || "");
            return {
              entity_field: fieldName,
              table_column:
                candidate && proposalColumnSet.has(candidate)
                  ? candidate
                  : fieldName,
              rule: "",
            };
          }),
        );
      } else {
        const tableName = String(payload.table_name || "").trim();
        if (tableName && dbTables.some((table) => table.name === tableName)) {
          // 先加载真实列并重置绑定行，再应用 AI 绑定，避免异步覆盖导致绑定为空。
          await handleSelectTable(tableName);
        }
        const suggestedBindings = Array.isArray(payload.bindings)
          ? payload.bindings
          : [];
        suggestedBindings.forEach((item) => {
          if (!isRecord(item)) return;
          const entityField = String(item.entity_field || "").trim();
          if (!entityField) return;
          setBindings((current) =>
            applyEntityDesignSuggestion("bindings", current, {
              id: suggestion.id,
              label: suggestion.label,
              payload: item,
            }) as Array<Record<string, unknown>>,
          );
        });
      }
      if (suggestion.id) {
        setLastAppliedTableSuggestionId(suggestion.id);
      }
      // 保留已确认的建议作为对话留痕，确认卡只展示不再提供动作。
      setAppliedTableSuggestionRecord(suggestion);
    } else if (assistType === "bindings") {
      setBindings((current) =>
        applyEntityDesignSuggestion(assistType, current, suggestion) as Array<
          Record<string, unknown>
        >,
      );
    } else if (assistType === "api_mapping") {
      if (activeExternalOperation) {
        handleExternalApiOperationChange(activeExternalOperation.operationId, {
          mappings: applyEntityDesignSuggestion(
            assistType,
            activeExternalOperation.mappings,
            suggestion,
          ) as Array<Record<string, unknown>>,
        });
      }
    } else if (assistType === "seed_data") {
      setSeedRows((current) =>
        applyEntityDesignSuggestion(assistType, current, suggestion) as Array<
          Record<string, unknown>
        >,
      );
    }
    if (suggestion.id) {
      setAppliedSuggestionIds((current) => new Set(current).add(suggestion.id || ""));
    }
    // 采纳后内容高度变化，通知消息列表贴底，确保新内容与确认按钮可见。
    onInteraction?.();
  };

  const adoptAllSuggestions = (assistType: string): void => {
    visibleSuggestions(assistType).forEach((suggestion) =>
      adoptSuggestion(assistType, suggestion),
    );
    onInteraction?.();
  };

  const dismissSuggestion = (suggestion: WorkflowEntityDesignSuggestion): void => {
    if (suggestion.id) {
      setDismissedSuggestionIds((current) => new Set(current).add(suggestion.id || ""));
    }
    onInteraction?.();
  };

  const tableSelectOptions = tableColumns.map((column) => ({
    label: String(column.name || ""),
    value: String(column.name || ""),
  }));

  const canSubmit = Boolean(
    entityId &&
      sourceType &&
      !disabled &&
      (sourceType !== "database" ||
        (selectedTableName &&
          bindings.some((row) => String(row.table_column || "").trim() !== ""))) &&
      (sourceType !== "external_api" || externalApiDraft.operations.length > 0),
  );

  // 组装当前实体方案并通过既有 AG-UI 动作提交一次最终确认。
  const submitDesign = (): void => {
    if (!entityId || !sourceType) return;
    // 提交后设计进入后端持久化，清空本地草稿避免下次恢复旧状态。
    entityDesignDraftStore.delete(draftKey);
    // 汇总建表 DDL 操作（缺失字段补列已即时执行，不再随提交下发）。
    const ddlOperations = [
      ...(createTableProposal
        ? [
            {
              id: `create_${String(
                createTableProposal.name || "entity_table",
              )}`,
              operation: "create_table",
              table: createTableProposal,
              to: {},
              source: "entity_design_ai_create_table",
              approved_by_user: true,
            },
          ]
        : []),
    ];
    onAction?.({
      action: "submit_entity_design",
      entity_id: entityId,
      data_source_type: sourceType as "database" | "external_api" | "static",
      ...(sourceType === "database"
        ? {
            database_design: {
              matched_table: selectedTableName,
              ...(dbDatabaseName ? { database_name: dbDatabaseName } : {}),
              bindings: normalizeObjectRows(bindings),
              ...(createTableProposal
                ? {
                    table_generation: {
                      required: true,
                      proposal: createTableProposal,
                      approved: true,
                      approval_source: "entity_design_ai_create_table",
                    },
                  }
                : {}),
              ...(ddlOperations.length > 0
                ? { database_operations: ddlOperations }
                : {}),
            },
          }
        : {}),
      ...(sourceType === "external_api"
        ? {
            external_api_design: serializeExternalApiDesign(externalApiDraft),
          }
        : {}),
      ...(sourceType === "static"
        ? {
            static_design: {
              seed_rows: serializeSeedRows(seedRows),
              field_values: normalizeFieldValues(fieldValues),
            },
          }
        : {}),
    });
    onInteraction?.();
  };

  // AI 智能选表的独立确认卡：仅在存在待确认建议时弹出。
  const tableSelectionSuggestion = visibleSuggestions("table_selection")[0];
  // 字段映射 AI 建议同样以独立确认卡展示，不再逐条内嵌。
  const bindingsSuggestions = visibleSuggestions("bindings");
  // 静态数据：AI 生成结果是否已自动覆盖表格，用于展示成功提示。
  const generatedSeedRows =
    aiSuggestions?.assist_type === "seed_data"
      ? seedRowsFromSuggestions(aiSuggestions.suggestions)
      : [];
  const generatedSeedRowCount = generatedSeedRows.length;
  const seedDataApplied =
    generatedSeedRowCount > 0 &&
    seedDataAppliedKeys.includes(JSON.stringify(generatedSeedRows));
  // 种子表格列：已定义取值约束的字段列变为下拉选择（选项并入该列已有取值，
  // 避免隐藏既有数据），未约束字段保持自由文本。
  const seedRowColumns: RowColumn[] =
    entityFields.length > 0
      ? entityFields.map((field) => {
          const fieldName = String(field.name || field.label || "").trim();
          // enum 字段以 ProjectPlan 枚举值为准；其余字段以用户约束取值为准。
          const enumAllowed =
            String(field.type || "") === "enum"
              ? normalizeStringList(field.enum_values)
              : [];
          const constraintAllowed = normalizeStringList(fieldValues[fieldName]);
          const allowed = enumAllowed.length > 0 ? enumAllowed : constraintAllowed;
          if (allowed.length === 0) {
            return {
              key: fieldName,
              label: String(field.label || field.name || "字段"),
            };
          }
          const existing = Array.from(
            new Set(
              seedRows
                .map((row) => String(row[fieldName] ?? "").trim())
                .filter(Boolean),
            ),
          );
          const options = Array.from(new Set([...allowed, ...existing])).map(
            (value) => ({ label: value, value }),
          );
          return {
            key: fieldName,
            label: String(field.label || field.name || "字段"),
            type: "select",
            options,
          };
        })
      : [{ key: "value", label: "值" }];
  // 流程内确认卡：存在待确认建议，或对话已打开且有内容时展示；
  // 展示期间不再渲染主设计卡片，避免两张卡片叠加。
  const tableSelectionFlowVisible =
    Boolean(tableSelectionSuggestion) ||
    (aiConversationOpen && aiMessages.length > 0);
  const bindingsFlowVisible = bindingsSuggestions.length > 0;
  // AI 智能选表确认或实体设计已确认后锁定主卡片，全部编辑入口不可变更。
  const designConfirmed =
    String(entityTarget?.design_stage || entityDesign?.stage || "") === "confirmed";
  const cardDisabled = disabled || designLocked || designConfirmed;
  // 确认后保留 AI 确认卡在对话中，同时展示主卡片；DDL 执行中仍只展示流程卡。
  const flowCardVisible = tableSelectionFlowVisible || bindingsFlowVisible;
  const ddlInProgress = Boolean(ddlPending) || Boolean(ddlExecution);
  const aiFlowApplied =
    Boolean(lastAppliedTableSuggestionId) && !tableSelectionSuggestion;
  const showMainCard = !flowCardVisible || (aiFlowApplied && !ddlInProgress);

  return (
    <>
      {tableSelectionFlowVisible ? (
        <TableSelectionFlowCard
          aiMessages={aiMessages}
          appliedId={lastAppliedTableSuggestionId}
          appliedRecord={appliedTableSuggestionRecord}
          databaseName={dbDatabaseName}
          ddlExecution={ddlExecution}
          ddlPending={ddlPending}
          disabled={disabled}
          draft={aiDraft}
          entityFields={entityFields}
          missingFields={
            aiSuggestions?.assist_type === "table_selection"
              ? aiSuggestions.missing_fields
              : undefined
          }
          onApply={(suggestion) =>
            adoptSuggestion("table_selection", suggestion)
          }
          onClose={() => {
            setAiConversationOpen(false);
            if (tableSelectionSuggestion) {
              dismissSuggestion(tableSelectionSuggestion);
            } else {
              onInteraction?.();
            }
          }}
          onCreateTable={requestCreateTable}
          onDismiss={dismissSuggestion}
          onDraftChange={setAiDraft}
          onSend={(text) => {
            setAiDraft("");
            requestAiAssist("table_selection", text);
          }}
          onSupplementFields={(suggestion) =>
            requestSupplementFields(
              aiSuggestions?.assist_type === "table_selection"
                ? aiSuggestions.missing_fields
                : undefined,
              [suggestion],
            )
          }
          pending={pendingAssistType === "table_selection"}
          suggestion={tableSelectionSuggestion}
          workspaceRoot={workspaceRoot}
        />
      ) : bindingsFlowVisible ? (
        <BindingsConfirmCard
          disabled={disabled}
          entityFields={entityFields}
          missingFields={
            aiSuggestions?.assist_type === "bindings"
              ? aiSuggestions.missing_fields
              : undefined
          }
          onApplyAll={(_suggestions) => {
            adoptAllSuggestions("bindings");
          }}
          onDismissAll={(suggestions) => {
            suggestions.forEach(dismissSuggestion);
          }}
          onSupplementFields={(suggestions) =>
            requestSupplementFields(
              aiSuggestions?.assist_type === "bindings"
                ? aiSuggestions.missing_fields
                : undefined,
              suggestions,
            )
          }
          selectedTableName={selectedTableName}
          suggestions={bindingsSuggestions}
        />
      ) : null}
      {aiFlowApplied && showMainCard ? (
        <div className={cx("entity-design-ai-accepted-bubble")}>
          <CheckCircleOutlined aria-hidden="true" />
          <Text>已接受 AI 建议，建议已应用到主卡片。</Text>
        </div>
      ) : null}
      {showMainCard ? (
        <div className={cx("entity-design-card", "workflow-detail-review-fields")}>
      {designLocked || designConfirmed ? (
        <Alert
          message="设计已确认并锁定，内容不可修改，可继续后续页面/接口设计与构建。"
          showIcon
          type="info"
        />
      ) : null}
      {validationErrors.length > 0 ? (
        <Alert
          message="实体设计校验未通过，请先修订后再确认"
          description={validationErrors.join("\n")}
          showIcon
          type="error"
        />
      ) : null}
      <section className={cx("entity-design-hero")}>
        <span className={cx("entity-design-hero-icon")} aria-hidden="true">
          <DatabaseOutlined />
        </span>
        <div className={cx("entity-design-hero-copy")}>
          <div className={cx("entity-design-hero-title-row")}>
            <Text strong>{entityName}</Text>
            {entityId ? <code>{entityId}</code> : null}
            {entityTarget?.module_id ? <Tag>{entityTarget.module_id}</Tag> : null}
          </div>
          {entityDescription ? (
            <Text type="secondary">{entityDescription}</Text>
          ) : null}
        </div>
        <div className={cx("entity-design-hero-tags")}>
          <Tag color={sourceType ? "purple" : "default"}>
            {DATA_SOURCE_LABELS[sourceType] || "待选择数据源"}
          </Tag>
        </div>
      </section>

      <EntitySectionCard title="数据源类型">
        <div className={cx("entity-design-source-cards")}>
          {sourceOptions.map((option) => {
            const unavailable = option.available === false;
            const selected = sourceType === option.value;
            return (
              <button
                className={cx(
                  "entity-design-source-card",
                  selected && "is-selected",
                  unavailable && "is-unavailable",
                )}
                disabled={cardDisabled || unavailable}
                key={option.value}
                onClick={() => setSourceType(option.value)}
                type="button"
              >
                <span className={cx("entity-design-source-icon")}>
                  {DATA_SOURCE_ICONS[option.value] || <DatabaseOutlined />}
                </span>
                <span className={cx("entity-design-source-copy")}>
                  <span className={cx("entity-design-source-name")}>{option.label}</span>
                </span>
                <span className={cx("entity-design-source-check")} aria-hidden="true">
                  {selected ? <CheckOutlined /> : null}
                </span>
              </button>
            );
          })}
        </div>
      </EntitySectionCard>

      {sourceType === "database" ? (
        <>
          <EntitySectionCard
            extra={
              <div className={cx("entity-design-section-extra")}>
                <Button
                  disabled={cardDisabled || dbQueryLoading}
                  icon={<ReloadOutlined />}
                  onClick={() => void handleQueryTables()}
                  size="small"
                  type="text"
                >
                  重新查询
                </Button>
                <AiAssistTrigger
                  assistType="table_selection"
                  disabled={cardDisabled || !entityId || dbTables.length === 0}
                  loading={pendingAssistType === "table_selection"}
                  onSubmit={requestAiAssist}
                />
              </div>
            }
            title="选择数据表"
          >
            <Select
              disabled={cardDisabled || dbQueryLoading}
              loading={dbQueryLoading}
              onChange={(value) => {
                if (value) {
                  void handleSelectTable(String(value));
                }
              }}
              options={dbTables.map((table) => ({
                label: table.comment
                  ? `${table.name}（${table.comment}）`
                  : table.name,
                value: table.name,
              }))}
              placeholder={dbQueryLoading ? "正在加载表清单…" : "请选择数据表"}
              showSearch
              style={{ width: "100%" }}
              value={selectedTableName || undefined}
            />
            {dbQueryMessage ? (
              <Text className={cx("entity-readonly-empty")} type="secondary">
                {dbQueryMessage}
              </Text>
            ) : null}
            {createTableProposal ? (
              <Alert
                message={`已确认新建表 ${String(
                  createTableProposal.name || "",
                )}，确认实体设计后执行`}
                showIcon
                type="success"
              />
            ) : null}
            {ddlExecutedColumns.length > 0 ? (
              <Alert
                message={`已通过 DDL 补充字段：${ddlExecutedColumns.join(
                  "、",
                )}，字段映射已写入`}
                showIcon
                type="success"
              />
            ) : null}
            {ddlCreatedTable ? (
              <Alert
                message={`已通过 DDL 创建数据表 ${ddlCreatedTable}，字段映射已写入`}
                showIcon
                type="success"
              />
            ) : null}
            {aiSuggestions?.assist_type === "table_selection" &&
            !visibleSuggestions("table_selection")[0] &&
            aiSuggestions.note ? (
              <Text className={cx("entity-design-ai-note")} type="secondary">
                {aiSuggestions.note}
              </Text>
            ) : null}
          </EntitySectionCard>
          {selectedTableName ? (
            <EntitySectionCard title={`表字段信息 · ${selectedTableName}`}>
              {tableColumns.length === 0 ? (
                <Text className={cx("entity-readonly-empty")} type="secondary">
                  {dbQueryMessage || "未读取到该表的字段信息，请返回重新选择。"}
                </Text>
              ) : (
                <ReadonlyGrid
                  columns={[
                    { key: "name", label: "列名", render: (row) => String(row.name || "") },
                    { key: "type", label: "类型", render: (row) => String(row.type || "") },
                    {
                      key: "nullable",
                      label: "可空",
                      render: (row) => (row.nullable ? "是" : "否"),
                    },
                    {
                      key: "comment",
                      label: "说明",
                      render: (row) => String(row.comment || ""),
                    },
                  ]}
                  emptyText="该表暂无字段信息"
                  rows={tableColumns}
                />
              )}
            </EntitySectionCard>
          ) : null}
          {selectedTableName &&
          (tableColumns.length > 0 ||
            bindings.some(
              (row) => String(row.table_column || "").trim() !== "",
            )) ? (
            <EntitySectionCard
              extra={
                tableColumns.length > 0 ? (
                  <AiAssistTrigger
                    assistType="bindings"
                    disabled={cardDisabled || !entityId}
                    loading={pendingAssistType === "bindings"}
                    onSubmit={requestAiAssist}
                  />
                ) : undefined
              }
              title="字段绑定"
            >
              {tableColumns.length > 0 ? (
                <RowListEditor
                  columns={[
                    {
                      key: "entity_field",
                      label: "实体字段名称",
                      placeholder: "例如 product_name",
                      readonly: true,
                    },
                    {
                      key: "table_column",
                      label: "表列",
                      options: tableSelectOptions,
                      placeholder: "选择表列",
                      type: "select",
                    },
                  ]}
                  disableAdd
                  disabled={cardDisabled}
                  emptyText="暂无绑定，请先在下方选择数据表。"
                  onChange={setBindings}
                  rows={bindings}
                />
              ) : (
                // 确认后的完成载荷只带 bindings，不带表列结构；
                // 此时以只读网格展示已确认的字段绑定。
                <ReadonlyGrid
                  columns={[
                    {
                      key: "entity_field",
                      label: "实体字段",
                      render: (row) => String(row.entity_field || ""),
                    },
                    {
                      key: "table_column",
                      label: "表列",
                      render: (row) => String(row.table_column || ""),
                    },
                  ]}
                  emptyText="暂无绑定"
                  rows={bindings}
                />
              )}
              {aiSuggestions?.assist_type === "bindings" &&
              !bindingsFlowVisible &&
              aiSuggestions.note ? (
                <Text className={cx("entity-design-ai-note")} type="secondary">
                  {aiSuggestions.note}
                </Text>
              ) : null}
            </EntitySectionCard>
          ) : null}
        </>
      ) : null}

      {sourceType === "external_api" ? (
        <ExternalApiDesignPanel
          disabled={cardDisabled}
          draft={externalApiDraft}
          entityFields={entityFields}
          onChange={handleExternalApiDraftChange}
          onUpdateOperation={handleExternalApiOperationChange}
          onConfirm={submitDesign}
          onRequestAiMapping={(operationId) => {
            handleExternalApiDraftChange({ activeOperationId: operationId });
            requestAiAssist("api_mapping");
          }}
          relatedEndpoints={relatedEndpoints}
          suggestionSlot={
            <AiSuggestionArea
              note={
                aiSuggestions?.assist_type === "api_mapping"
                  ? aiSuggestions.note
                  : undefined
              }
              onAdopt={(suggestion) => adoptSuggestion("api_mapping", suggestion)}
              onAdoptAll={() => adoptAllSuggestions("api_mapping")}
              onDismiss={dismissSuggestion}
              suggestions={visibleSuggestions("api_mapping")}
            />
          }
        />
      ) : null}

      {sourceType === "static" ? (
        <>
          <EntitySectionCard title="字段取值约束">
            <Text type="secondary">
              约束字段在种子表格中以下拉选择填写，并作为 AI 生成种子数据的取值依据；enum 字段默认带入 ProjectPlan 枚举值。
            </Text>
            <FieldValueConstraintEditor
              disabled={cardDisabled}
              entityFields={entityFields}
              fieldValues={fieldValues}
              onChange={setFieldValues}
            />
          </EntitySectionCard>
          <EntitySectionCard
            extra={
              <AiAssistTrigger
                assistType="seed_data"
                disabled={cardDisabled || !entityId}
                loading={pendingAssistType === "seed_data"}
                onSubmit={requestAiAssist}
              />
            }
            title="种子数据"
          >
            <RowListEditor
              columns={seedRowColumns}
              disabled={cardDisabled}
              emptyText="暂无种子记录，点击下方添加。"
              onChange={setSeedRows}
              rows={seedRows}
            />
            <AdvancedJsonCollapse
              disabled={cardDisabled}
              label="原始 JSON（种子记录数组）"
              onChange={(value) => setSeedRows(parseJsonList(value))}
              value={JSON.stringify(seedRows, null, 2)}
            />
            {aiSuggestions?.assist_type === "seed_data" ? (
              <div className={cx("entity-design-ai-suggestions")}>
                {seedDataApplied ? (
                  <Text type="success">
                    已生成 {generatedSeedRowCount} 条种子记录并填入表格，可继续编辑。
                  </Text>
                ) : null}
                {aiSuggestions.note ? (
                  <Text className={cx("entity-design-ai-note")} type="secondary">
                    {aiSuggestions.note}
                  </Text>
                ) : null}
              </div>
            ) : null}
          </EntitySectionCard>
        </>
      ) : null}

      {sourceType !== "external_api" ? <div className={cx("entity-design-panel-actions")}>
        <Button
          disabled={!canSubmit}
          icon={<CheckCircleOutlined />}
          onClick={submitDesign}
          size="large"
          type="primary"
        >
          确认实体设计
        </Button>
      </div> : null}
      </div>
      ) : null}
    </>
  );
}
