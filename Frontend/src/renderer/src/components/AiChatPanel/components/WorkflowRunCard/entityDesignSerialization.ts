// 实体设计结构化表单与后端载荷之间的纯序列化工具（无 React/antd 依赖，供单测直接导入）。

import type {
  WorkflowEntityDesignSuggestion,
  WorkflowExternalApiDesign,
} from "../../../../typings";
import type {
  ExternalApiDesignDraft,
  ExternalApiOperationDraft,
} from "./useExternalApiDraft";

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function recordItems(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === "object" && !Array.isArray(item),
      )
    : [];
}

// 丢弃完全空白的行，保证提交载荷与旧 JSON 编辑结果等价且无空壳行。
export function normalizeObjectRows(
  value: unknown,
): Array<Record<string, unknown>> {
  return recordItems(value).filter((row) =>
    Object.values(row).some((item) => String(item ?? "").trim() !== ""),
  );
}

export function normalizeStringList(value: unknown): string[] {
  const result: string[] = [];
  if (!Array.isArray(value)) return result;
  value.forEach((item) => {
    const text = String(item ?? "").trim();
    if (text && !result.includes(text)) result.push(text);
  });
  return result;
}

export function normalizeFieldValues(value: unknown): Record<string, string[]> {
  const result: Record<string, string[]> = {};
  if (!isRecord(value)) return result;
  Object.entries(value).forEach(([key, items]) => {
    const normalized = normalizeStringList(items);
    if (key.trim() && normalized.length > 0) result[key.trim()] = normalized;
  });
  return result;
}

export function serializeSeedRows(
  rows: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  return normalizeObjectRows(rows).map((row) =>
    Object.fromEntries(
      Object.entries(row)
        .filter(([key, value]) => key.trim() !== "" && String(value ?? "").trim() !== "")
        .map(([key, value]) => [key, String(value ?? "")]),
    ),
  );
}

export function parseJsonList(value: string): Array<Record<string, unknown>> {
  try {
    const parsed: unknown = JSON.parse(value);
    return Array.isArray(parsed) ? recordItems(parsed) : [];
  } catch {
    return [];
  }
}

export function parseJsonRecord(value: string): Record<string, string[]> {
  try {
    const parsed: unknown = JSON.parse(value);
    if (!isRecord(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).map(([key, items]) => [
        key,
        Array.isArray(items) ? items.map(String) : [],
      ]),
    );
  } catch {
    return {};
  }
}

export function tryParseJson(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  try {
    return JSON.parse(trimmed);
  } catch {
    return trimmed;
  }
}

// 把 JSON 文本格式化为稳定的缩进文本；非法或空文本返回可读错误，供编辑器即时提示。
export function formatJsonText(value: string): { ok: true; text: string } | { ok: false; error: string } {
  const trimmed = value.trim();
  if (!trimmed) return { ok: false, error: "JSON 内容不能为空。" };
  try {
    return { ok: true, text: JSON.stringify(JSON.parse(trimmed), null, 2) };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "JSON 解析失败。" };
  }
}

export type JsonImportResult =
  | { ok: true; value: unknown }
  | { ok: false; error: string };

// 解析用户导入的 JSON 文件文本：合法返回解析值，非法返回错误信息。
export function parseJsonImport(text: string): JsonImportResult {
  const trimmed = text.trim();
  if (!trimmed) return { ok: false, error: "文件内容为空。" };
  try {
    return { ok: true, value: JSON.parse(trimmed) };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "JSON 解析失败。",
    };
  }
}

// 收集 JSON 的字段路径：顶层键原样输出，嵌套对象用点路径，
// 数组取首个元素继续展开；限制深度与数量防止超大结构失控。
export function responseFieldPaths(
  value: unknown,
  maxDepth = 3,
  maxPaths = 300,
): string[] {
  const paths: string[] = [];
  const visit = (node: unknown, prefix: string, depth: number): void => {
    if (depth > maxDepth || paths.length >= maxPaths) return;
    if (Array.isArray(node)) {
      const arrayPath = prefix
        ? (prefix.endsWith("[]") ? prefix : `${prefix}[]`)
        : "[]";
      if (arrayPath && !paths.includes(arrayPath)) paths.push(arrayPath);
      if (node.length > 0) visit(node[0], arrayPath, depth);
      return;
    }
    if (!isRecord(node)) return;
    Object.keys(node).forEach((key) => {
      const path = prefix ? `${prefix}.${key}` : key;
      paths.push(path);
      visit(node[key], path, depth + 1);
    });
  };
  visit(value, "", 0);
  return paths;
}

// 提取响应字段路径及基础类型，供映射表展示类型提示但不暴露样例值。
export function responseFieldTypes(
  value: unknown,
  maxDepth = 3,
  maxPaths = 300,
): Record<string, string> {
  const result: Record<string, string> = {};
  const valueType = (item: unknown): string => {
    if (item === null) return "null";
    if (Array.isArray(item)) return "array";
    if (isRecord(item)) return "object";
    return typeof item === "number" ? "number" : typeof item === "boolean" ? "boolean" : "string";
  };
  const visit = (node: unknown, prefix: string, depth: number): void => {
    if (depth > maxDepth || Object.keys(result).length >= maxPaths) return;
    if (Array.isArray(node)) {
      const arrayPath = prefix
        ? (prefix.endsWith("[]") ? prefix : `${prefix}[]`)
        : "[]";
      if (arrayPath) result[arrayPath] = "array";
      if (node.length > 0) visit(node[0], arrayPath, depth);
      return;
    }
    if (!isRecord(node)) return;
    Object.keys(node).forEach((key) => {
      const path = prefix ? `${prefix}.${key}` : key;
      result[path] = valueType(node[key]);
      visit(node[key], path, depth + 1);
    });
  };
  visit(value, "", 0);
  return result;
}

// 判断规范化响应路径是否存在于样例结构中；数组节点统一使用 [] 标记。
export function responsePathExists(value: unknown, path: string): boolean {
  const normalized = path.trim();
  return normalized === "" || responseFieldPaths(value).includes(normalized);
}

/** 将共享连接和多操作草稿裁剪为当前 AG-UI 外部 API 契约。 */
export function serializeExternalApiDesign(
  draft: ExternalApiDesignDraft,
): WorkflowExternalApiDesign {
  return {
    connection: {
      base_url: draft.connection.baseUrl.trim(),
      base_url_config_key: draft.connection.baseUrlConfigKey.trim(),
      timeout_ms: draft.connection.timeoutMs,
      headers: normalizeObjectRows(draft.connection.headers).map((item) => ({
        name: String(item.name || "").trim(),
        value: String(item.value || ""),
      })),
    },
    operations: draft.operations.map(serializeExternalApiOperation),
  };
}

/** 把单个操作草稿裁剪为当前多 API 公共契约。 */
function serializeExternalApiOperation(
  operation: ExternalApiOperationDraft,
): WorkflowExternalApiDesign["operations"][number] {
  const response = isRecord(operation.responseHandling) ? operation.responseHandling : {};
  const entityPayload = response.entity_payload !== false;
  const rawCardinality = String(response.cardinality || "object");
  const cardinality = entityPayload && ["object", "array", "page"].includes(rawCardinality)
    ? rawCardinality as "object" | "array" | "page"
    : "object";
  const successCodes = Array.isArray(response.success_status_codes)
    ? response.success_status_codes
        .map(Number)
        .filter((value) => Number.isInteger(value) && value >= 100 && value <= 599)
    : [200];
  const pagination = entityPayload && isRecord(response.pagination)
    ? {
        page_parameter: String(response.pagination.page_parameter || ""),
        size_parameter: String(response.pagination.size_parameter || ""),
        page_index_base: (Number(response.pagination.page_index_base) === 0 ? 0 : 1) as 0 | 1,
      }
    : undefined;
  return {
    operation_id: operation.operationId,
    name: operation.name.trim(),
    endpoint_refs: operation.endpointRefs.map((ref) => ({
      api_contract_id: ref.api_contract_id,
      endpoint_id: ref.endpoint_id,
    })),
    ...(operation.overrideBaseUrl || operation.overrideBaseUrlConfigKey || operation.overrideTimeoutMs
      ? {
          connection_override: {
            ...(operation.overrideBaseUrl ? { base_url: operation.overrideBaseUrl.trim() } : {}),
            ...(operation.overrideBaseUrlConfigKey
              ? { base_url_config_key: operation.overrideBaseUrlConfigKey.trim() }
              : {}),
            ...(operation.overrideTimeoutMs ? { timeout_ms: operation.overrideTimeoutMs } : {}),
          },
        }
      : {}),
    api_info: {
      method: operation.method,
      path: operation.path.trim(),
      parameters: normalizeObjectRows(operation.parameters).map((item) => ({
        name: String(item.name || "").trim(),
        in: item.in === "path" ? "path" : "query",
        type: item.type === "number" || item.type === "boolean" ? item.type : "string",
        required: item.required === true,
        ...(item.example !== undefined && item.example !== "" ? { example: item.example } : {}),
      })),
      headers: normalizeObjectRows(operation.headers).map((item) => ({
        name: String(item.name || "").trim(),
        value: String(item.value || ""),
      })),
      request_body: tryParseJson(operation.requestBody),
      response_body: tryParseJson(operation.responseBody),
    },
    response_handling: {
      entity_payload: entityPayload,
      cardinality,
      payload_path: entityPayload ? String(response.payload_path || "").trim() : "",
      success_status_codes: successCodes.length > 0 ? successCodes : [200],
      ...(String(response.error_message_path || "").trim()
        ? { error_message_path: String(response.error_message_path).trim() }
        : {}),
      ...(entityPayload && String(response.total_path || "").trim()
        ? { total_path: String(response.total_path).trim() }
        : {}),
      ...(pagination ? { pagination } : {}),
    },
    field_mappings: entityPayload ? serializeExternalApiMappings(operation.mappings) : [],
  };
}

// 识别常见鉴权 Header 及其连字符、下划线变体，避免凭据进入正式契约。
export function isSensitiveApiHeader(value: unknown): boolean {
  const lowered = String(value || "").trim().toLowerCase();
  const compact = lowered.replace(/[^a-z0-9]/g, "");
  return ["authorization", "proxyauthorization", "cookie", "setcookie"].includes(compact)
    || compact.endsWith("apikey")
    || compact.endsWith("authtoken")
    || compact.endsWith("accesstoken")
    || compact.endsWith("bearertoken");
}

// 校验外部 API 的本地设计草稿，返回前端可直接展示的阻塞错误。
export function externalApiValidationErrors(input: {
  baseUrl: string
  baseUrlConfigKey: string
  method: string
  path: string
  parameters: Array<Record<string, unknown>>
  headers: Array<Record<string, unknown>>
  requestBody: unknown
  responseBody: unknown
  responseHandling: Record<string, unknown>
  mappings: Array<Record<string, unknown>>
  entityFields: Array<Record<string, unknown>>
  entityPayload?: boolean
}): string[] {
  const errors: string[] = [];
  let parsedUrl: URL | undefined;
  try {
    parsedUrl = new URL(input.baseUrl.trim());
  } catch {
    errors.push("Base URL 必须是合法的 HTTP(S) 地址。");
  }
  if (!input.baseUrl.trim()) errors.push("请填写 Base URL。");
  if (parsedUrl && !["http:", "https:"].includes(parsedUrl.protocol)) {
    errors.push("Base URL 必须使用 HTTP 或 HTTPS。");
  }
  if (parsedUrl && (parsedUrl.username || parsedUrl.password || parsedUrl.search || parsedUrl.hash)) {
    errors.push("Base URL 不得包含用户信息、查询参数或片段。");
  }
  if (!['GET', 'POST', 'PUT', 'DELETE'].includes(input.method.trim().toUpperCase())) {
    errors.push("请求方式必须是 GET、POST、PUT 或 DELETE。");
  }
  if (!/^[A-Za-z][A-Za-z0-9_.-]{0,127}$/.test(input.baseUrlConfigKey.trim())) {
    errors.push("Base URL 配置键必须以字母开头，只能包含字母、数字、点、下划线和连字符。");
  }
  if (!input.path.trim().startsWith("/")) errors.push("接口路径必须以 / 开头。");
  const placeholders = new Set([...input.path.matchAll(/\{([^{}]+)\}/g)].map((match) => match[1]));
  const pathWithoutPlaceholders = input.path.replace(/\{[^{}]+\}/g, "");
  if (pathWithoutPlaceholders.includes("{") || pathWithoutPlaceholders.includes("}")) {
    errors.push("路径占位符格式不合法。");
  }
  const pathParams = new Set(
    input.parameters
      .filter((item) => String(item.in || "") === "path")
      .map((item) => String(item.name || "").trim())
      .filter(Boolean),
  );
  if (placeholders.size !== pathParams.size || [...placeholders].some((name) => !pathParams.has(name))) {
    errors.push("路径占位符必须与 Path 参数一一对应。");
  }
  const parameterKeys = new Set<string>();
  input.parameters.forEach((item) => {
    const name = String(item.name || "").trim();
    const location = String(item.in || "").trim();
    if (!name || !["path", "query"].includes(location)) {
      errors.push("参数必须填写名称并选择 Path 或 Query。");
      return;
    }
    const key = `${location}:${name}`;
    if (parameterKeys.has(key)) errors.push(`参数重复：${location}.${name}。`);
    parameterKeys.add(key);
    if (location === "path" && item.required !== true) errors.push(`Path 参数 ${name} 必须为必填。`);
  });
  const headers = new Set<string>();
  input.headers.forEach((item) => {
    const name = String(item.name || "").trim();
    const value = String(item.value || "").trim();
    if (!name) {
      if (value) errors.push("Header 填写固定值时必须同时填写名称。");
      return;
    }
    if (isSensitiveApiHeader(name)) errors.push(`首版不支持敏感 Header：${name}。`);
    if (headers.has(name.toLowerCase())) errors.push(`Header 重复：${name}。`);
    headers.add(name.toLowerCase());
  });
  const hasRequestBody = typeof input.requestBody === "string"
    ? input.requestBody.trim() !== ""
    : input.requestBody !== undefined && input.requestBody !== null;
  if (typeof input.requestBody === "string" && input.requestBody.trim()) {
    try {
      JSON.parse(input.requestBody);
    } catch {
      errors.push("请求体必须是合法 JSON。");
    }
  }
  if (input.method.trim().toUpperCase() === "GET" && hasRequestBody) {
    errors.push("GET 请求不应配置请求体。");
  }
  if (!isRecord(input.responseBody) && !Array.isArray(input.responseBody)) {
    errors.push("返回体必须是 JSON 对象或数组。");
  }
  const responseHandling = input.responseHandling;
  const entityPayload = input.entityPayload !== false;
  const cardinality = String(responseHandling.cardinality || "object");
  const payloadPath = String(responseHandling.payload_path || "").trim();
  if (!entityPayload && (cardinality !== "object" || payloadPath || input.mappings.length > 0 || responseHandling.pagination || responseHandling.total_path)) {
    errors.push("非实体响应不得配置载荷、分页或字段映射。");
  }
  if (entityPayload && ["array", "page"].includes(cardinality) && !payloadPath) errors.push("array/page 必须填写实体载荷路径。");
  if (entityPayload && payloadPath && !responsePathExists(input.responseBody, payloadPath)) errors.push(`实体载荷路径不存在：${payloadPath}。`);
  const totalPath = String(responseHandling.total_path || "").trim();
  if (entityPayload && cardinality === "page" && !totalPath) errors.push("page 必须填写总数路径。");
  if (entityPayload && totalPath && !responsePathExists(input.responseBody, totalPath)) errors.push(`总数路径不存在：${totalPath}。`);
  const errorPath = String(responseHandling.error_message_path || "").trim();
  if (errorPath && !responsePathExists(input.responseBody, errorPath)) errors.push(`错误信息路径不存在：${errorPath}。`);
  if (entityPayload && cardinality === "page") {
    const pagination = isRecord(responseHandling.pagination) ? responseHandling.pagination : {};
    const queryNames = new Set(
      input.parameters
        .filter((item) => String(item.in || "") === "query")
        .map((item) => String(item.name || "").trim())
        .filter(Boolean),
    );
    const pageParameter = String(pagination.page_parameter || "").trim();
    const sizeParameter = String(pagination.size_parameter || "").trim();
    if (!pageParameter || !queryNames.has(pageParameter)) errors.push("分页页码参数必须引用已声明的 Query 参数。");
    if (!sizeParameter || !queryNames.has(sizeParameter)) errors.push("分页大小参数必须引用已声明的 Query 参数。");
  }
  const mappingFields = new Set<string>();
  const entityFieldNames = new Set(
    input.entityFields
      .map((field) => String(field.name || field.label || "").trim())
      .filter(Boolean),
  );
  if (entityPayload) input.mappings.forEach((item) => {
    const field = String(item.entity_field || "").trim();
    const source = String(item.source_field || "").trim();
    if (!field || mappingFields.has(field)) {
      if (field) errors.push(`字段重复映射：${field}。`);
      return;
    }
    mappingFields.add(field);
    if (!entityFieldNames.has(field)) errors.push(`映射字段不在实体定义内：${field}。`);
    if (source && !responsePathExists(input.responseBody, source)) errors.push(`来源字段路径不存在：${source}。`);
  });
  if (entityPayload) input.entityFields.forEach((field) => {
    const name = String(field.name || field.label || "").trim();
    if (field.required === true && !String(input.mappings.find((row) => String(row.entity_field || "") === name)?.source_field || "").trim()) {
      errors.push(`必填字段尚未映射：${name}。`);
    }
  });
  return [...new Set(errors)];
}

/** 校验共享连接或操作连接覆盖，覆盖模式允许全部留空。 */
export function externalApiConnectionValidationErrors(input: {
  baseUrl: string
  baseUrlConfigKey: string
  headers?: Array<Record<string, unknown>>
  required?: boolean
}): string[] {
  const errors: string[] = [];
  const baseUrl = input.baseUrl.trim();
  const configKey = input.baseUrlConfigKey.trim();
  if (input.required || baseUrl) {
    try {
      const parsed = new URL(baseUrl);
      if (!["http:", "https:"].includes(parsed.protocol)) errors.push("Base URL 必须使用 HTTP 或 HTTPS。");
      if (parsed.username || parsed.password || parsed.search || parsed.hash) errors.push("Base URL 不得包含用户信息、查询参数或片段。");
    } catch {
      errors.push("Base URL 必须是合法的 HTTP(S) 地址。");
    }
  }
  if (input.required && !baseUrl) errors.push("请填写 Base URL。");
  if (input.required && !configKey) errors.push("请填写 Base URL 配置键。");
  if (Boolean(baseUrl) !== Boolean(configKey)) errors.push("覆盖 Base URL 时必须同时提供配置键。");
  if (configKey && !/^[A-Za-z][A-Za-z0-9_.-]{0,127}$/.test(configKey)) errors.push("Base URL 配置键格式不合法。");
  const headerNames = new Set<string>();
  recordItems(input.headers).forEach((header) => {
    const name = String(header.name || "").trim();
    if (!name) return;
    const key = name.toLowerCase();
    if (isSensitiveApiHeader(name)) errors.push(`首版不支持敏感 Header：${name}。`);
    if (headerNames.has(key)) errors.push(`Header 重复：${name}。`);
    headerNames.add(key);
  });
  return [...new Set(errors)];
}

/** 校验多操作集合中的稳定 ID、Endpoint 唯一分配；未覆盖接口允许后续分批设计。 */
export function externalApiCollectionValidationErrors(input: {
  operations: Array<{
    operationId?: unknown
    operation_id?: unknown
    name?: unknown
    endpointRefs?: unknown
    endpoint_refs?: unknown
  }>
  relatedEndpoints: Array<{ api_contract_id?: unknown; endpoint_id?: unknown }>
}): string[] {
  const errors: string[] = [];
  if (input.operations.length === 0) errors.push("至少添加一个上游 API 操作。");
  if (input.operations.length > 50) errors.push("上游 API 操作最多 50 个。");
  const operationIds = new Set<string>();
  const assigned = new Map<string, string>();
  const related = new Set(input.relatedEndpoints.map((item) => `${String(item.api_contract_id || "")}::${String(item.endpoint_id || "")}`));
  input.operations.forEach((operation) => {
    const operationId = String(operation.operationId || operation.operation_id || "").trim();
    const name = String(operation.name || "").trim();
    if (!operationId) errors.push("API 操作缺少 operation_id。");
    else if (operationIds.has(operationId)) errors.push(`operation_id 重复：${operationId}。`);
    operationIds.add(operationId);
    if (!name) errors.push(`操作 ${operationId || "<未命名>"} 缺少名称。`);
    const refs = recordItems(operation.endpointRefs || operation.endpoint_refs);
    const operationRefs = new Set<string>();
    if (refs.length === 0) errors.push(`操作 ${operationId || "<未命名>"} 尚未关联本系统 Endpoint。`);
    refs.forEach((ref) => {
      const key = `${String(ref.api_contract_id || "")}::${String(ref.endpoint_id || "")}`;
      if (operationRefs.has(key)) errors.push(`操作 ${operationId} 重复关联同一 Endpoint。`);
      operationRefs.add(key);
      if (!related.has(key)) errors.push(`操作 ${operationId} 引用了无关 Endpoint。`);
      const owner = assigned.get(key);
      if (owner && owner !== operationId) errors.push(`Endpoint 不能同时绑定操作 ${owner} 和 ${operationId}。`);
      assigned.set(key, operationId);
    });
  });
  return [...new Set(errors)];
}

// 根据返回体计算实体字段的同名映射：顶层同名优先，其次唯一嵌套叶子路径；
// 只补空 source_field 的行，不覆盖用户已填写的映射。
export function sameNameFieldMappings(
  entityFields: Array<Record<string, unknown>>,
  responseBody: unknown,
  current: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  const paths = responseFieldPaths(responseBody);
  const topLevel = new Set(paths.filter((path) => !path.includes(".")));
  const nestedByLeaf = new Map<string, string[]>();
  paths.forEach((path) => {
    const leaf = path.slice(path.lastIndexOf(".") + 1);
    const list = nestedByLeaf.get(leaf) || [];
    if (!list.includes(path)) list.push(path);
    nestedByLeaf.set(leaf, list);
  });
  const sourceFor = (fieldName: string): string => {
    if (topLevel.has(fieldName)) return fieldName;
    const candidates = (nestedByLeaf.get(fieldName) || []).filter(
      (path) => path.split(".").length > 1,
    );
    return candidates.length === 1 ? candidates[0] : "";
  };
  const existingByField = new Map<string, Record<string, unknown>>();
  current.forEach((row) => {
    const field = String(row.entity_field || "").trim();
    if (field) existingByField.set(field, row);
  });
  const rows: Array<Record<string, unknown>> = [];
  entityFields.forEach((field) => {
    const entityField = String(field.name || field.label || "").trim();
    if (!entityField) return;
    const existing = existingByField.get(entityField);
    const existingSource = String(existing?.source_field || "").trim();
    const suggested = sourceFor(entityField);
    if (existingSource) {
      rows.push({ ...existing });
    } else if (existing && suggested) {
      rows.push({
        ...existing,
        source_field: suggested,
        rule: topLevel.has(suggested) ? "same_name" : "nested_match",
      });
    } else if (suggested) {
      rows.push({
        entity_field: entityField,
        source_field: suggested,
        rule: suggested ? (topLevel.has(suggested) ? "same_name" : "nested_match") : "",
      });
    }
  });
  return rows;
}

// 只序列化真实存在的外部 API 字段映射，避免选填字段的空行进入正式契约和生成验收。
export function serializeExternalApiMappings(
  value: unknown,
): Array<{
  entity_field: string
  source_field: string
  rule: "same_name" | "nested_match" | "ai" | "manual"
}> {
  return recordItems(value).flatMap((item) => {
    const entityField = String(item.entity_field || "").trim();
    const sourceField = String(item.source_field || "").trim();
    if (!entityField || !sourceField) return [];
    const rule = item.rule === "same_name" || item.rule === "nested_match" || item.rule === "ai"
      ? item.rule
      : "manual";
    return [{ entity_field: entityField, source_field: sourceField, rule }];
  });
}

// 从 AI 种子数据建议中提取有效记录：过滤无 seed_row 或空对象的项并保持顺序；
// 空结果返回空数组，供“直接覆盖种子数据表”的自动应用逻辑使用。
export function seedRowsFromSuggestions(
  suggestions: unknown,
): Array<Record<string, unknown>> {
  return recordItems(suggestions)
    .map((suggestion) => {
      const payload = isRecord(suggestion.payload) ? suggestion.payload : {};
      const seedRow = payload.seed_row;
      return isRecord(seedRow) && Object.keys(seedRow).length > 0 ? seedRow : null;
    })
    .filter((row): row is Record<string, unknown> => row !== null);
}

export type FieldValueConstraintRow = {
  field: string
  values: string[]
};

// 把字段取值记录转成约束行列表：丢弃空字段与空取值，保持声明顺序。
export function fieldValuesToConstraintRows(
  fieldValues: Record<string, string[]>,
): FieldValueConstraintRow[] {
  return Object.entries(fieldValues)
    .filter(([field, values]) => field.trim() !== "" && values.length > 0)
    .map(([field, values]) => ({
      field: field.trim(),
      values: normalizeStringList(values),
    }));
}

// 选择实体设计字段来源：entityTarget.fields 非空才优先，空数组回退 entityDesign.fields，
// 避免空数组真值导致兜底失效。
export function resolveEntityDesignFields(
  entityTarget: { fields?: unknown } | undefined,
  entityDesign: { fields?: unknown } | undefined,
): Array<Record<string, unknown>> {
  const targetFields = recordItems(entityTarget?.fields);
  if (targetFields.length > 0) return targetFields;
  return recordItems(entityDesign?.fields);
}

// 把约束行列表转回字段取值记录：丢弃空字段与空取值行。
export function constraintRowsToFieldValues(
  rows: unknown,
): Record<string, string[]> {
  const result: Record<string, string[]> = {};
  recordItems(rows).forEach((row) => {
    const field = String(row.field || "").trim();
    const values = normalizeStringList(row.values);
    if (field && values.length > 0) {
      result[field] = values;
    }
  });
  return result;
}

// 生成默认约束行：仅对 ProjectPlan 已声明枚举值的 enum 字段生成，
// 与用户已有约束按字段去重合并；普通字段不自动生成。
export function defaultConstraintRows(
  entityFields: Array<Record<string, unknown>>,
  fieldValues: Record<string, string[]>,
): FieldValueConstraintRow[] {
  const rows = fieldValuesToConstraintRows(fieldValues);
  const usedFields = new Set(rows.map((row) => row.field));
  entityFields.forEach((field) => {
    const fieldName = String(field.name || field.label || "").trim();
    if (!fieldName || usedFields.has(fieldName)) return;
    const values = normalizeStringList(field.enum_values);
    if (values.length > 0) {
      // enum 判定以 enum_values 非空为准；type 存在且明确非 enum 时才跳过，
      // 避免字段对象缺失 type 导致默认约束不显示。
      const fieldType = String(field.type || "").trim();
      if (fieldType && fieldType !== "enum") return;
      rows.push({ field: fieldName, values });
    }
  });
  return rows;
}

// 合并后端下发的默认约束与用户已有约束：按字段去重，用户约束优先；
// 默认约束为空时返回用户约束行，两者皆空返回空数组。
export function mergeDefaultConstraintRows(
  defaultConstraints: unknown,
  fieldValues: Record<string, string[]>,
): FieldValueConstraintRow[] {
  const rows = fieldValuesToConstraintRows(fieldValues);
  const usedFields = new Set(rows.map((row) => row.field));
  recordItems(defaultConstraints).forEach((item) => {
    const field = String(item.field || "").trim();
    const values = normalizeStringList(item.values);
    if (field && !usedFields.has(field) && values.length > 0) {
      rows.push({ field, values });
    }
  });
  return rows;
}

// 把 AI 辅助建议按类型合并进当前表单草稿；未命中的类型原样返回。
export function applyEntityDesignSuggestion(
  assistType: string,
  current: unknown,
  suggestion: WorkflowEntityDesignSuggestion,
): unknown {
  if (assistType === "bindings" || assistType === "api_mapping") {
    const rows = recordItems(current);
    const payload = suggestion.payload || {};
    const entityField = String(payload.entity_field || "").trim();
    if (!entityField) return current;
    const next = rows.map((row) =>
      String(row.entity_field || "").trim() === entityField
        ? { ...row, ...payload }
        : row,
    );
    if (!next.some((row) => String(row.entity_field || "").trim() === entityField)) {
      next.push({ ...payload });
    }
    return next;
  }
  if (assistType === "seed_data") {
    const rows = recordItems(current);
    const seedRow = (suggestion.payload || {}).seed_row;
    if (isRecord(seedRow)) {
      return [...rows, { ...seedRow }];
    }
    return current;
  }
  if (assistType === "business_rules" || assistType === "relationships") {
    const rows = recordItems(current);
    if (suggestion.payload && Object.keys(suggestion.payload).length > 0) {
      return [...rows, { ...suggestion.payload }];
    }
    return current;
  }
  if (assistType === "acceptance" || assistType === "risks") {
    const items = Array.isArray(current) ? current.map(String) : [];
    const value = String(suggestion.value || "").trim();
    if (value && !items.includes(value)) return [...items, value];
    return current;
  }
  return current;
}
