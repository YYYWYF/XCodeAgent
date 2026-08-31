import { useCallback, useState } from "react";
import { isRecord, recordItems } from "./entityDesignSerialization";

export type ExternalApiEndpointRefDraft = {
  api_contract_id: string
  endpoint_id: string
  method?: string
  path?: string
  summary?: string
}

export type ExternalApiOperationDraft = {
  operationId: string
  name: string
  endpointRefs: ExternalApiEndpointRefDraft[]
  overrideBaseUrl: string
  overrideBaseUrlConfigKey: string
  overrideTimeoutMs?: number
  method: string
  path: string
  parameters: Array<Record<string, unknown>>
  headers: Array<Record<string, unknown>>
  requestBody: string
  responseBody: string
  responseHandling: Record<string, unknown>
  mappings: Array<Record<string, unknown>>
}

export type ExternalApiDesignDraft = {
  connection: {
    baseUrl: string
    baseUrlConfigKey: string
    timeoutMs: number
    headers: Array<Record<string, unknown>>
  }
  operations: ExternalApiOperationDraft[]
  activeOperationId: string
}

type UseExternalApiDraftOptions = {
  existingDesign: Record<string, unknown>
  storedDraft?: ExternalApiDesignDraft
}

/** 生成稳定且无需额外依赖的本地操作 ID。 */
export function createExternalApiOperation(
  operationId = `external-op-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
): ExternalApiOperationDraft {
  return {
    operationId,
    name: "",
    endpointRefs: [],
    overrideBaseUrl: "",
    overrideBaseUrlConfigKey: "",
    method: "GET",
    path: "",
    parameters: [],
    headers: [],
    requestBody: "",
    responseBody: "",
    responseHandling: {
      entity_payload: true,
      cardinality: "object",
      payload_path: "",
      success_status_codes: [200],
    },
    mappings: [],
  };
}

/** 把正式契约中的单个操作恢复为可编辑草稿。 */
function operationSeed(value: Record<string, unknown>): ExternalApiOperationDraft {
  const apiInfo = isRecord(value.api_info) ? value.api_info : {};
  const override = isRecord(value.connection_override) ? value.connection_override : {};
  return {
    ...createExternalApiOperation(String(value.operation_id || "")),
    operationId: String(value.operation_id || ""),
    name: String(value.name || ""),
    endpointRefs: recordItems(value.endpoint_refs).map((item) => ({
      api_contract_id: String(item.api_contract_id || ""),
      endpoint_id: String(item.endpoint_id || ""),
    })),
    overrideBaseUrl: String(override.base_url || ""),
    overrideBaseUrlConfigKey: String(override.base_url_config_key || ""),
    overrideTimeoutMs: override.timeout_ms === undefined ? undefined : Number(override.timeout_ms),
    method: String(apiInfo.method || "GET"),
    path: String(apiInfo.path || ""),
    parameters: recordItems(apiInfo.parameters),
    headers: recordItems(apiInfo.headers),
    requestBody: apiInfo.request_body == null ? "" : JSON.stringify(apiInfo.request_body, null, 2),
    responseBody: apiInfo.response_body == null ? "" : JSON.stringify(apiInfo.response_body, null, 2),
    responseHandling: isRecord(value.response_handling) ? { ...value.response_handling } : {},
    mappings: recordItems(value.field_mappings),
  };
}

/** 从当前唯一多操作契约构造实体级外部 API 草稿。 */
export function externalApiDesignDraftSeed(
  existingDesign: Record<string, unknown>,
): ExternalApiDesignDraft {
  const connection = isRecord(existingDesign.connection) ? existingDesign.connection : {};
  const operations = recordItems(existingDesign.operations).map(operationSeed);
  return {
    connection: {
      baseUrl: String(connection.base_url || ""),
      baseUrlConfigKey: String(connection.base_url_config_key || ""),
      timeoutMs: Number(connection.timeout_ms || 10000),
      headers: recordItems(connection.headers),
    },
    operations,
    activeOperationId: operations[0]?.operationId || "",
  };
}

/** 管理共享连接与多上游操作草稿，并提供按 operation_id 的原子更新。 */
export function useExternalApiDraft({
  existingDesign,
  storedDraft,
}: UseExternalApiDraftOptions): {
  draft: ExternalApiDesignDraft
  updateDraft: (patch: Partial<ExternalApiDesignDraft>) => void
  updateOperation: (operationId: string, patch: Partial<ExternalApiOperationDraft>) => void
} {
  const [draft, setDraft] = useState<ExternalApiDesignDraft>(
    () => storedDraft || externalApiDesignDraftSeed(existingDesign),
  );

  const updateDraft = useCallback((patch: Partial<ExternalApiDesignDraft>): void => {
    setDraft((current) => ({ ...current, ...patch }));
  }, []);

  const updateOperation = useCallback(
    (operationId: string, patch: Partial<ExternalApiOperationDraft>): void => {
      setDraft((current) => ({
        ...current,
        operations: current.operations.map((operation) =>
          operation.operationId === operationId ? { ...operation, ...patch } : operation,
        ),
      }));
    },
    [],
  );

  return { draft, updateDraft, updateOperation };
}
