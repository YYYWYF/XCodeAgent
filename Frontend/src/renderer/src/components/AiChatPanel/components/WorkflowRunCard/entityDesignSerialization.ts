// 实体设计结构化表单与后端载荷之间的纯序列化工具（无 React/antd 依赖，供单测直接导入）。

import type { WorkflowEntityDesignSuggestion } from "../../../../typings";

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
      if (node.length > 0) visit(node[0], prefix, depth);
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
    if (existing) {
      rows.push(
        existingSource
          ? { ...existing }
          : {
              ...existing,
              source_field: suggested,
              rule: suggested ? (topLevel.has(suggested) ? "same_name" : "nested_match") : String(existing.rule || ""),
            },
      );
    } else {
      rows.push({
        entity_field: entityField,
        source_field: suggested,
        rule: suggested ? (topLevel.has(suggested) ? "same_name" : "nested_match") : "",
      });
    }
  });
  return rows;
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
