import {
  CopyOutlined,
  DeleteOutlined,
  FormatPainterOutlined,
  PlusOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Divider,
  Input,
  InputNumber,
  Popconfirm,
  Select,
  Steps,
  Switch,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import type { ReactElement, ReactNode } from "react";
import { useMemo, useState } from "react";
import { cx } from "../../../../utils";
import {
  externalApiCollectionValidationErrors,
  externalApiConnectionValidationErrors,
  externalApiValidationErrors,
  formatJsonText,
  isRecord,
  isSensitiveApiHeader,
  parseJsonImport,
  responseFieldTypes,
  sameNameFieldMappings,
  tryParseJson,
} from "./entityDesignSerialization";
import {
  createExternalApiOperation,
  type ExternalApiDesignDraft,
  type ExternalApiEndpointRefDraft,
  type ExternalApiOperationDraft,
} from "./useExternalApiDraft";

const { Text, Title } = Typography;
const METHOD_OPTIONS = ["GET", "POST", "PUT", "DELETE"];

type ExternalApiDesignPanelProps = {
  disabled?: boolean
  draft: ExternalApiDesignDraft
  entityFields: Array<Record<string, unknown>>
  relatedEndpoints: ExternalApiEndpointRefDraft[]
  onChange: (patch: Partial<ExternalApiDesignDraft>) => void
  onUpdateOperation: (operationId: string, patch: Partial<ExternalApiOperationDraft>) => void
  onConfirm: () => void
  onRequestAiMapping?: (operationId: string) => void
  suggestionSlot?: ReactNode
}

/** JSON 编辑器提供导入、格式化及即时语法提示。 */
function JsonEditor({
  disabled,
  label,
  onChange,
  value,
}: {
  disabled?: boolean
  label: string
  onChange: (value: string) => void
  value: string
}): ReactElement {
  const parsed = value.trim() ? parseJsonImport(value) : null;
  /** 格式化当前合法 JSON，并保留错误提示而不覆盖原始输入。 */
  const format = (): void => {
    const result = formatJsonText(value);
    if (result.ok) onChange(result.text);
    else message.warning(result.error);
  };
  return (
    <section className={cx("entity-design-json-editor")}>
      <div className={cx("entity-design-json-editor-head")}>
        <Text strong>{label}</Text>
        <div className={cx("entity-design-json-editor-actions")}>
          {parsed?.ok ? <Tag color="green">JSON 有效</Tag> : value.trim() ? <Tag color="red">JSON 无效</Tag> : <Tag>待填写</Tag>}
          <Upload
            accept=".json,application/json"
            beforeUpload={(file) => {
              const reader = new FileReader();
              reader.onload = () => onChange(String(reader.result || ""));
              reader.onerror = () => message.error("读取 JSON 文件失败。");
              reader.readAsText(file);
              return false;
            }}
            disabled={disabled}
            showUploadList={false}
          >
            <Button disabled={disabled} icon={<UploadOutlined />} size="small">导入 JSON</Button>
          </Upload>
          <Button disabled={disabled || !parsed?.ok} icon={<FormatPainterOutlined />} onClick={format} size="small">格式化</Button>
        </div>
      </div>
      <Input.TextArea disabled={disabled} onChange={(event) => onChange(event.target.value)} rows={8} value={value} />
      {parsed && !parsed.ok ? <Text type="danger">{parsed.error}</Text> : null}
    </section>
  );
}

/** 维护 Path/Query 参数的结构化行。 */
function ParameterRows({ disabled, rows, onChange }: {
  disabled?: boolean
  rows: Array<Record<string, unknown>>
  onChange: (rows: Array<Record<string, unknown>>) => void
}): ReactElement {
  /** 更新指定参数行的单个结构化字段。 */
  const update = (index: number, key: string, value: unknown): void => {
    onChange(rows.map((row, rowIndex) => rowIndex === index ? { ...row, [key]: value } : row));
  };
  return (
    <div className={cx("entity-design-api-table")}>
      {rows.map((row, index) => (
        <div className={cx("entity-design-api-table-row")} key={`${index}-${String(row.name || "")}`}>
          <Input disabled={disabled} onChange={(event) => update(index, "name", event.target.value)} placeholder="参数名" value={String(row.name || "")} />
          <Select disabled={disabled} onChange={(value) => update(index, "in", value)} options={[{ label: "Path", value: "path" }, { label: "Query", value: "query" }]} value={String(row.in || "query")} />
          <Select disabled={disabled} onChange={(value) => update(index, "type", value)} options={["string", "number", "boolean"].map((value) => ({ label: value, value }))} value={String(row.type || "string")} />
          <Checkbox checked={row.required === true} disabled={disabled} onChange={(event) => update(index, "required", event.target.checked)}>必填</Checkbox>
          <Input disabled={disabled} onChange={(event) => update(index, "example", event.target.value)} placeholder="示例值" value={String(row.example || "")} />
          <Button danger disabled={disabled} icon={<DeleteOutlined />} onClick={() => onChange(rows.filter((_item, rowIndex) => rowIndex !== index))} type="text" />
        </div>
      ))}
      <Button block disabled={disabled} icon={<PlusOutlined />} onClick={() => onChange([...rows, { name: "", in: "query", type: "string", required: false }])} type="dashed">添加参数</Button>
    </div>
  );
}

/** 维护非敏感固定 Header 行。 */
function HeaderRows({ disabled, rows, onChange }: {
  disabled?: boolean
  rows: Array<Record<string, unknown>>
  onChange: (rows: Array<Record<string, unknown>>) => void
}): ReactElement {
  /** 更新指定 Header 行的名称或固定值。 */
  const update = (index: number, key: string, value: string): void => {
    onChange(rows.map((row, rowIndex) => rowIndex === index ? { ...row, [key]: value } : row));
  };
  return (
    <div className={cx("entity-design-api-table")}>
      {rows.map((row, index) => (
        <div className={cx("entity-design-api-table-row", isSensitiveApiHeader(row.name) && "is-invalid")} key={`${index}-${String(row.name || "")}`}>
          <Input disabled={disabled} onChange={(event) => update(index, "name", event.target.value)} placeholder="Header 名称" value={String(row.name || "")} />
          <Input disabled={disabled} onChange={(event) => update(index, "value", event.target.value)} placeholder="固定值（不得填写凭据）" value={String(row.value || "")} />
          {isSensitiveApiHeader(row.name) ? <Text type="danger">不支持敏感 Header</Text> : <span />}
          <Button danger disabled={disabled} icon={<DeleteOutlined />} onClick={() => onChange(rows.filter((_item, rowIndex) => rowIndex !== index))} type="text" />
        </div>
      ))}
      <Button block disabled={disabled} icon={<PlusOutlined />} onClick={() => onChange([...rows, { name: "", value: "" }])} type="dashed">添加 Header</Button>
    </div>
  );
}

/** 计算单个操作的阻塞错误，连接覆盖为空时使用共享连接。 */
function operationErrors(
  operation: ExternalApiOperationDraft,
  draft: ExternalApiDesignDraft,
  entityFields: Array<Record<string, unknown>>,
): string[] {
  const responseBody = tryParseJson(operation.responseBody);
  const identityErrors = [
    ...(!operation.name.trim() ? ["请填写操作名称。"] : []),
    ...(operation.endpointRefs.length === 0 ? ["请至少关联一个本系统 Endpoint。"] : []),
  ];
  const overrideErrors = externalApiConnectionValidationErrors({
    baseUrl: operation.overrideBaseUrl,
    baseUrlConfigKey: operation.overrideBaseUrlConfigKey,
    required: false,
  });
  return [...identityErrors, ...overrideErrors, ...externalApiValidationErrors({
    baseUrl: operation.overrideBaseUrl || draft.connection.baseUrl,
    baseUrlConfigKey: operation.overrideBaseUrlConfigKey || draft.connection.baseUrlConfigKey,
    method: operation.method,
    path: operation.path,
    parameters: operation.parameters,
    headers: operation.headers,
    requestBody: operation.requestBody,
    responseBody,
    responseHandling: operation.responseHandling,
    mappings: operation.mappings,
    entityFields,
    entityPayload: operation.responseHandling.entity_payload !== false,
  })];
}

/** 单个上游操作的四步编辑器。 */
function OperationEditor({
  disabled,
  draft,
  entityFields,
  operation,
  onChange,
  onRequestAiMapping,
  suggestionSlot,
}: {
  disabled?: boolean
  draft: ExternalApiDesignDraft
  entityFields: Array<Record<string, unknown>>
  operation: ExternalApiOperationDraft
  onChange: (patch: Partial<ExternalApiOperationDraft>) => void
  onRequestAiMapping?: () => void
  suggestionSlot?: ReactNode
}): ReactElement {
  const [step, setStep] = useState(0);
  const responseBody = tryParseJson(operation.responseBody);
  const pathTypes = useMemo(() => isRecord(responseBody) || Array.isArray(responseBody) ? responseFieldTypes(responseBody) : {}, [responseBody]);
  const paths = Object.keys(pathTypes);
  const errors = operationErrors(operation, draft, entityFields);
  const entityPayload = operation.responseHandling.entity_payload !== false;
  const requiredFields = entityFields.filter((field) => field.required === true);
  const mappedRequired = requiredFields.filter((field) => operation.mappings.some((row) => String(row.entity_field || "") === String(field.name || field.label || "") && paths.includes(String(row.source_field || "")))).length;
  /** 合并更新响应语义中的单个配置项。 */
  const updateResponse = (key: string, value: unknown): void => onChange({ responseHandling: { ...operation.responseHandling, [key]: value } });
  const byField = new Map(operation.mappings.map((row) => [String(row.entity_field || ""), row]));
  /** 按固定实体字段新增、替换或清除来源路径映射。 */
  const setMapping = (entityField: string, sourceField: string): void => {
    const next = operation.mappings.filter((row) => String(row.entity_field || "") !== entityField);
    if (sourceField) next.push({ entity_field: entityField, source_field: sourceField, rule: "manual" });
    onChange({ mappings: next });
  };
  return (
    <div className={cx("entity-design-wizard-step")}>
      <Steps current={step} onChange={(next) => !disabled && setStep(next)} size="small">
        <Steps.Step title="请求配置" /><Steps.Step title="响应解析" /><Steps.Step title="字段映射" /><Steps.Step title="操作确认" />
      </Steps>
      {step === 0 ? <>
        <label className={cx("entity-design-form-field")}><Text type="secondary">操作名称</Text><Input disabled={disabled} onChange={(event) => onChange({ name: event.target.value })} placeholder="例如：查询商品列表" value={operation.name} /></label>
        <div className={cx("entity-design-form-row")}><label className={cx("entity-design-form-field")}><Text type="secondary">请求路径</Text><Input disabled={disabled} onChange={(event) => onChange({ path: event.target.value })} placeholder="/v1/products" value={operation.path} /></label><label className={cx("entity-design-form-field")}><Text type="secondary">请求方式</Text><Select disabled={disabled} onChange={(value) => onChange({ method: String(value), ...(value === "GET" ? { requestBody: "" } : {}) })} options={METHOD_OPTIONS.map((value) => ({ label: value, value }))} value={operation.method} /></label></div>
        <Divider orientation="left">连接覆盖（可选）</Divider>
        <label className={cx("entity-design-form-field")}><Text type="secondary">覆盖 Base URL</Text><Input disabled={disabled} onChange={(event) => onChange({ overrideBaseUrl: event.target.value })} placeholder="留空使用共享连接" value={operation.overrideBaseUrl} /></label>
        <label className={cx("entity-design-form-field")}><Text type="secondary">覆盖 Base URL 配置键</Text><Input disabled={disabled} onChange={(event) => onChange({ overrideBaseUrlConfigKey: event.target.value })} placeholder="留空使用共享配置键" value={operation.overrideBaseUrlConfigKey} /><Text type="secondary">生成代码读取该配置项获得覆盖地址，不保存凭据。</Text></label>
        <label className={cx("entity-design-form-field")}><Text type="secondary">覆盖超时（毫秒）</Text><InputNumber disabled={disabled} max={120000} min={100} onChange={(value) => onChange({ overrideTimeoutMs: value == null ? undefined : Number(value) })} placeholder="使用共享超时" value={operation.overrideTimeoutMs} /></label>
        <Divider orientation="left">Path / Query 参数</Divider><ParameterRows disabled={disabled} onChange={(parameters) => onChange({ parameters })} rows={operation.parameters} />
        <Divider orientation="left">操作固定 Header</Divider><HeaderRows disabled={disabled} onChange={(headers) => onChange({ headers })} rows={operation.headers} />
        {operation.method !== "GET" ? <JsonEditor disabled={disabled} label="请求体（JSON，可选）" onChange={(requestBody) => onChange({ requestBody })} value={operation.requestBody} /> : null}
      </> : null}
      {step === 1 ? <>
        <JsonEditor disabled={disabled} label="返回体 JSON 样例" onChange={(responseBodyText) => onChange({ responseBody: responseBodyText, mappings: sameNameFieldMappings(entityFields, tryParseJson(responseBodyText), operation.mappings) })} value={operation.responseBody} />
        <div className={cx("entity-design-response-paths")}><Text strong>响应字段路径</Text><div className={cx("entity-design-response-path-list")}>{paths.map((path) => <Tag key={path}>{path} · {pathTypes[path]}</Tag>)}</div></div>
        <Divider orientation="left">响应语义</Divider>
        <label className={cx("entity-design-form-field")}><Text type="secondary">返回实体载荷</Text><Switch checked={entityPayload} disabled={disabled} onChange={(checked) => onChange({ responseHandling: { ...operation.responseHandling, entity_payload: checked, cardinality: "object", payload_path: "", ...(checked ? {} : { pagination: undefined, total_path: undefined }) }, ...(checked ? {} : { mappings: [] }) })} /></label>
        {entityPayload ? <div className={cx("entity-design-form-row", "is-three")}><label className={cx("entity-design-form-field")}><Text type="secondary">结果类型</Text><Select disabled={disabled} onChange={(value) => updateResponse("cardinality", value)} options={["object", "array", "page"].map((value) => ({ label: value, value }))} value={String(operation.responseHandling.cardinality || "object")} /></label><label className={cx("entity-design-form-field")}><Text type="secondary">实体载荷路径</Text><Select allowClear disabled={disabled} onChange={(value) => updateResponse("payload_path", value || "")} options={[{ label: "根节点", value: "" }, ...paths.map((path) => ({ label: path, value: path }))]} showSearch value={String(operation.responseHandling.payload_path || "")} /></label><label className={cx("entity-design-form-field")}><Text type="secondary">错误信息路径</Text><Select allowClear disabled={disabled} onChange={(value) => updateResponse("error_message_path", value || "")} options={paths.map((path) => ({ label: path, value: path }))} showSearch value={String(operation.responseHandling.error_message_path || "")} /></label></div> : <Alert message="该操作仅返回状态或确认信息，不配置实体载荷和字段映射。" type="info" />}
        <label className={cx("entity-design-form-field")}><Text type="secondary">成功状态码</Text><Select disabled={disabled} mode="tags" onChange={(values) => updateResponse("success_status_codes", values.map(Number).filter((value) => Number.isInteger(value) && value >= 100 && value <= 599))} tokenSeparators={[","]} value={(Array.isArray(operation.responseHandling.success_status_codes) ? operation.responseHandling.success_status_codes : [200]).map(String)} /></label>
        {entityPayload && operation.responseHandling.cardinality === "page" ? <div className={cx("entity-design-form-row", "is-three")}><label className={cx("entity-design-form-field")}><Text type="secondary">页码参数</Text><Select disabled={disabled} onChange={(value) => updateResponse("pagination", { ...(isRecord(operation.responseHandling.pagination) ? operation.responseHandling.pagination : {}), page_parameter: value })} options={operation.parameters.filter((item) => item.in === "query").map((item) => ({ label: String(item.name || ""), value: String(item.name || "") }))} /></label><label className={cx("entity-design-form-field")}><Text type="secondary">大小参数</Text><Select disabled={disabled} onChange={(value) => updateResponse("pagination", { ...(isRecord(operation.responseHandling.pagination) ? operation.responseHandling.pagination : {}), size_parameter: value })} options={operation.parameters.filter((item) => item.in === "query").map((item) => ({ label: String(item.name || ""), value: String(item.name || "") }))} /></label><label className={cx("entity-design-form-field")}><Text type="secondary">总数路径</Text><Select disabled={disabled} onChange={(value) => updateResponse("total_path", value)} options={paths.map((path) => ({ label: path, value: path }))} /></label></div> : null}
      </> : null}
      {step === 2 ? entityPayload ? <>
        <div className={cx("entity-design-mapping-toolbar")}><div><Text strong>实体字段 → 返回字段</Text><Tag color={mappedRequired === requiredFields.length ? "green" : "red"}>必填 {mappedRequired}/{requiredFields.length}</Tag></div><div><Button disabled={disabled || paths.length === 0} onClick={() => onChange({ mappings: sameNameFieldMappings(entityFields, responseBody, operation.mappings) })}>同名匹配</Button><Button disabled={disabled || paths.length === 0} onClick={onRequestAiMapping}>AI 接口映射</Button></div></div>
        <div className={cx("entity-design-mapping-table")}>{entityFields.map((field) => { const name = String(field.name || field.label || ""); const source = String(byField.get(name)?.source_field || ""); return <div className={cx("entity-design-mapping-row", source && !paths.includes(source) && "is-invalid")} key={name}><span><Text strong>{String(field.label || name)}</Text><code>{name}</code>{field.required === true ? <Tag color="red">必填</Tag> : null}</span><Select allowClear disabled={disabled} onChange={(value) => setMapping(name, String(value || ""))} options={paths.map((path) => ({ label: path, value: path }))} showSearch value={source || undefined} /><span>{source && paths.includes(source) ? <Tag color="green">已映射</Tag> : source ? <Tag color="red">路径失效</Tag> : <Tag>待映射</Tag>}</span></div>; })}</div>
        {suggestionSlot}
      </> : <Alert message="非实体响应无需字段映射。" type="info" /> : null}
      {step === 3 ? <><Alert message={errors.length === 0 ? "该操作配置完整。" : "该操作仍有阻塞项。"} type={errors.length === 0 ? "success" : "error"} showIcon />{errors.length > 0 ? <ul>{errors.map((error) => <li key={error}>{error}</li>)}</ul> : null}</> : null}
      <div className={cx("entity-design-wizard-actions")}><Button disabled={disabled || step === 0} onClick={() => setStep((value) => value - 1)}>上一步</Button><Button disabled={disabled || step === 3} onClick={() => setStep((value) => value + 1)} type="primary">下一步</Button></div>
    </div>
  );
}

/** 多上游 API 设计器：共享连接、纵向操作卡和一次性实体确认。 */
export function ExternalApiDesignPanel({
  disabled,
  draft,
  entityFields,
  relatedEndpoints,
  onChange,
  onUpdateOperation,
  onConfirm,
  onRequestAiMapping,
  suggestionSlot,
}: ExternalApiDesignPanelProps): ReactElement {
  const connectionErrors = externalApiConnectionValidationErrors({ ...draft.connection, required: true });
  const collectionErrors = externalApiCollectionValidationErrors({ operations: draft.operations, relatedEndpoints });
  const allErrors = [...new Set([...connectionErrors, ...collectionErrors, ...draft.operations.flatMap((operation) => operationErrors(operation, draft, entityFields).map((error) => `${operation.name || operation.operationId}：${error}`))])];
  const assigned = new Map<string, string>();
  draft.operations.forEach((operation) => operation.endpointRefs.forEach((ref) => assigned.set(`${ref.api_contract_id}::${ref.endpoint_id}`, operation.operationId)));
  const coverageComplete = relatedEndpoints.length === assigned.size;
  /** 新增空操作，并优先分配首个尚未覆盖的 Endpoint。 */
  const addOperation = (): void => {
    const operation = createExternalApiOperation();
    const firstFree = relatedEndpoints.find((ref) => !assigned.has(`${ref.api_contract_id}::${ref.endpoint_id}`));
    if (firstFree) operation.endpointRefs = [{ ...firstFree }];
    onChange({ operations: [...draft.operations, operation], activeOperationId: operation.operationId });
  };
  /** 复制请求、响应和映射配置，同时生成新 ID 并清空 Endpoint 关联。 */
  const duplicateOperation = (source: ExternalApiOperationDraft): void => {
    const copy = { ...source, operationId: createExternalApiOperation().operationId, name: `${source.name || "未命名操作"} 副本`, endpointRefs: [], parameters: source.parameters.map((item) => ({ ...item })), headers: source.headers.map((item) => ({ ...item })), mappings: source.mappings.map((item) => ({ ...item })), responseHandling: { ...source.responseHandling } };
    onChange({ operations: [...draft.operations, copy], activeOperationId: copy.operationId });
  };
  /** 删除指定操作并把展开状态切换到剩余首项。 */
  const deleteOperation = (operationId: string): void => {
    const operations = draft.operations.filter((operation) => operation.operationId !== operationId);
    onChange({ operations, activeOperationId: operations[0]?.operationId || "" });
  };
  return (
    <section className={cx("entity-design-external-api-wizard")}>
      <div className={cx("entity-design-wizard-header")}><div><Text type="secondary">实体数据源 · 外部 API</Text><Title level={4}>配置多个可执行公开 API 操作</Title></div><Tag color={allErrors.length === 0 ? "green" : "purple"}>{draft.operations.length} 个 API</Tag></div>
      <Alert message="本期只支持无鉴权公开 API；共享连接与操作 Header 均不得填写凭据。" showIcon type="info" />
      <div className={cx("entity-design-wizard-step")}><Divider orientation="left">共享连接</Divider><label className={cx("entity-design-form-field")}><Text type="secondary">Base URL</Text><Input disabled={disabled} onChange={(event) => onChange({ connection: { ...draft.connection, baseUrl: event.target.value } })} placeholder="https://api.example.com" value={draft.connection.baseUrl} /></label><label className={cx("entity-design-form-field")}><Text type="secondary">Base URL 配置键</Text><Input disabled={disabled} onChange={(event) => onChange({ connection: { ...draft.connection, baseUrlConfigKey: event.target.value } })} placeholder="integrations.product.base-url" value={draft.connection.baseUrlConfigKey} /><Text type="secondary">生成代码将通过该配置项读取 Base URL，便于不同环境替换地址；这里填写配置名称，不是密钥。</Text></label><label className={cx("entity-design-form-field")}><Text type="secondary">默认超时（毫秒）</Text><InputNumber disabled={disabled} max={120000} min={100} onChange={(value) => onChange({ connection: { ...draft.connection, timeoutMs: Number(value || 10000) } })} value={draft.connection.timeoutMs} /></label><Divider orientation="left">共享固定 Header</Divider><HeaderRows disabled={disabled} onChange={(headers) => onChange({ connection: { ...draft.connection, headers } })} rows={draft.connection.headers} /></div>
      <div className={cx("entity-design-mapping-toolbar")}><div><Text strong>上游 API 操作</Text><Text type="secondary">相关 Endpoint 覆盖 {assigned.size}/{relatedEndpoints.length} · 可分批设计</Text></div><Button disabled={disabled || draft.operations.length >= 50} icon={<PlusOutlined />} onClick={addOperation} type="primary">添加 API</Button></div>
      {draft.operations.length === 0 ? <Alert message="暂无上游 API，请先添加。" type="warning" /> : <Collapse accordion activeKey={draft.activeOperationId || undefined} onChange={(key) => onChange({ activeOperationId: String(Array.isArray(key) ? key[0] || "" : key || "") })}>{draft.operations.map((operation) => {
        const errors = operationErrors(operation, draft, entityFields);
        const endpointValues = operation.endpointRefs.map((ref) => `${ref.api_contract_id}::${ref.endpoint_id}`);
        return <Collapse.Panel header={<div className={cx("entity-design-operation-summary")}><Text strong>{operation.name || "未命名操作"}</Text><Tag>{operation.method} {operation.path || "待填写路径"}</Tag><Tag color={errors.length === 0 ? "green" : "red"}>{errors.length === 0 ? "配置完整" : `${errors.length} 项错误`}</Tag><Tag>{operation.endpointRefs.length} 个 Endpoint</Tag></div>} key={operation.operationId} extra={<div onClick={(event) => event.stopPropagation()}><Button disabled={disabled} icon={<CopyOutlined />} onClick={() => duplicateOperation(operation)} type="text" /><Popconfirm onConfirm={() => deleteOperation(operation.operationId)} title="删除该 API 操作？"><Button danger disabled={disabled} icon={<DeleteOutlined />} type="text" /></Popconfirm></div>}><label className={cx("entity-design-form-field")}><Text type="secondary">关联本系统 Endpoint</Text><Select disabled={disabled} mode="multiple" onChange={(values) => onUpdateOperation(operation.operationId, { endpointRefs: values.map((value) => { const [api_contract_id, endpoint_id] = String(value).split("::"); return { api_contract_id, endpoint_id }; }) })} options={relatedEndpoints.map((ref) => { const value = `${ref.api_contract_id}::${ref.endpoint_id}`; const owner = assigned.get(value); return { label: `${ref.method || ""} ${ref.path || ref.endpoint_id} · ${ref.summary || ref.endpoint_id}`, value, disabled: Boolean(owner && owner !== operation.operationId) }; })} placeholder="选择一个或多个 Endpoint" value={endpointValues} /></label><OperationEditor disabled={disabled} draft={draft} entityFields={entityFields} onChange={(patch) => onUpdateOperation(operation.operationId, patch)} onRequestAiMapping={() => onRequestAiMapping?.(operation.operationId)} operation={operation} suggestionSlot={draft.activeOperationId === operation.operationId ? suggestionSlot : null} /></Collapse.Panel>;
      })}</Collapse>}
      <div className={cx("entity-design-wizard-step")}><Divider orientation="left">整体确认</Divider><div className={cx("entity-design-review-grid")}><div><Text type="secondary">上游操作</Text><strong>{draft.operations.length}</strong></div><div><Text type="secondary">Endpoint 覆盖</Text><strong>{assigned.size}/{relatedEndpoints.length}</strong></div><div><Text type="secondary">阻塞问题</Text><strong>{allErrors.length}</strong></div></div>{allErrors.length > 0 ? <Alert description={<ul>{allErrors.slice(0, 20).map((error) => <li key={error}>{error}</li>)}</ul>} type="error" showIcon /> : <Alert message={coverageComplete ? "全部相关 Endpoint 已绑定上游操作。" : "当前操作配置完整，可先确认；其余 Endpoint 可后续继续设计。"} type={coverageComplete ? "success" : "warning"} showIcon />}</div>
      <div className={cx("entity-design-wizard-actions")}><Button disabled={disabled || allErrors.length > 0} onClick={onConfirm} type="primary">确认实体设计</Button></div>
    </section>
  );
}
