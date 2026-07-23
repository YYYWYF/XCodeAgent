import {
  DatabaseOutlined,
  PlayCircleOutlined,
  RocketOutlined,
} from "@ant-design/icons";
import { Button, Radio, Skeleton, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import type {
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageOption,
  WorkflowEvent,
} from "../../typings";
import { cx } from "../../utils";
import PageDesignProgress from "./PageDesignProgress";
import "./DetailConfirmationPageSelector.less";

const { Text, Title } = Typography;

type DetailTargetType = "page" | "endpoint";

type Props = {
  apiContracts?: DevelopmentPlanningApiContract[];
  disabled: boolean;
  generating?: boolean;
  loading: boolean;
  onStart: (
    targetType: "page" | "endpoint",
    targetId: string,
    targetLabel: string,
    hasDetailPlan: boolean,
    targetContext?: {
      apiContractId?: string;
      endpointId?: string;
    },
  ) => Promise<void>;
  pages: DevelopmentPlanningPageOption[];
  selectedEndpoint?: {
    apiContractId: string;
    endpointId: string;
    label: string;
  };
  selectedPage?: DevelopmentPlanningPageOption;
  workflowEvents?: WorkflowEvent[];
};

type EndpointOption = {
  apiContractId: string;
  description: string;
  endpointKey: string;
  endpointId: string;
  hasDetailPlan: boolean;
  label: string;
  method: string;
  path: string;
  rawEndpointId: string;
  summary?: string;
};

/** 生成页面与接口共用的单选值，确保两个栏目只能选中一个对象。 */
function targetSelectionKey(type: DetailTargetType, id: string): string {
  return `${type}:${id}`;
}

/** 从单选值解析当前目标类型。 */
function targetTypeFromSelection(value: string): DetailTargetType {
  return value.startsWith("endpoint:") ? "endpoint" : "page";
}

/** 在首次进入或选择待设计页面时提供唯一的详细设计入口。 */
export default function DetailConfirmationPageSelector({
  apiContracts = [],
  disabled,
  generating = false,
  loading,
  onStart,
  pages,
  selectedEndpoint: progressEndpoint,
  selectedPage: progressPage,
  workflowEvents,
}: Props): JSX.Element {
  const [selectedTargetKey, setSelectedTargetKey] = useState("");
  const endpointOptions = useMemo(() => {
    return apiContracts.flatMap((contract) => {
      return contract.endpoints.map((endpoint, endpointIndex) => {
        const rawEndpointId = endpoint.id || String(endpointIndex + 1);
        const apiContractId = endpoint.apiContractId || contract.id;
        const endpointId = `${apiContractId}:${rawEndpointId}`;
        const label = `${endpoint.method} ${endpoint.path}`.trim();
        return {
          apiContractId,
          endpointId,
          endpointKey: targetSelectionKey("endpoint", endpointId),
          rawEndpointId,
          label,
          method: endpoint.method,
          path: endpoint.path,
          summary: endpoint.summary,
          description:
            endpoint.summary || `来自 ${contract.label || contract.id}`,
          hasDetailPlan: Boolean(endpoint.hasDetailPlan || endpoint.designed),
        } satisfies EndpointOption;
      });
    });
  }, [apiContracts]);
  const selectedPage = useMemo(
    () =>
      pages.find(
        (page) => targetSelectionKey("page", page.pageId) === selectedTargetKey,
      ),
    [pages, selectedTargetKey],
  );
  const selectedEndpoint = useMemo(
    () =>
      endpointOptions.find(
        (source) => source.endpointKey === selectedTargetKey,
      ),
    [endpointOptions, selectedTargetKey],
  );
  const selectedTargetType = selectedTargetKey
    ? targetTypeFromSelection(selectedTargetKey)
    : undefined;
  const selectedTarget =
    selectedTargetType === "endpoint" ? selectedEndpoint : selectedPage;
  const selectedTargetId =
    selectedTargetType === "endpoint"
      ? selectedEndpoint?.endpointId
      : selectedPage?.pageId;

  // 页面/API 清单刷新后只保留用户显式选择；生成过程中不清空当前目标，避免进度页被产物刷新打断。
  useEffect(() => {
    if (generating) return;
    const pageKeys = pages.map((page) => targetSelectionKey("page", page.pageId));
    const endpointKeys = endpointOptions.map((endpoint) => endpoint.endpointKey);
    const availableKeys = [...pageKeys, ...endpointKeys];
    if (!selectedTargetKey || availableKeys.includes(selectedTargetKey)) return;
    setSelectedTargetKey("");
  }, [endpointOptions, generating, pages, selectedTargetKey]);

  const progressTarget = progressEndpoint || progressPage;
  const progressTargetType: DetailTargetType | undefined = progressEndpoint
    ? "endpoint"
    : progressPage
      ? "page"
      : undefined;

  if (generating && progressTarget && progressTargetType) {
    return (
      <section className={cx("detail-page-selector")}>
        <div className={cx("detail-page-selector-aurora")} />
        <main className={cx("detail-page-selector-panel", "progress-panel")}>
          <PageDesignProgress
            events={workflowEvents}
            pageLabel={progressTarget.label}
            targetType={progressTargetType}
          />
        </main>
      </section>
    );
  }

  return (
    <section className={cx("detail-page-selector")}>
      <div className={cx("detail-page-selector-aurora")} />
      <main className={cx("detail-page-selector-panel")}>
        <header className={cx("detail-page-selector-heading")}>
          <span className={cx("detail-page-selector-logo")}>
            <RocketOutlined />
          </span>
          <Text className={cx("detail-page-selector-eyebrow")}>
            DETAIL DESIGN
          </Text>
          <Title level={2}>选择要开始设计的对象</Title>
          <Text type="secondary">
            页面和 API
            接口目录来自项目计划。你可以自行选择先开始页面设计，还是接口设计。
          </Text>
        </header>

        {loading ? (
          <Skeleton active paragraph={{ rows: 4 }} title={false} />
        ) : pages.length || endpointOptions.length ? (
          <Radio.Group
            aria-label="选择要开始设计的页面或接口"
            className={cx("detail-page-selector-target-choice")}
            onChange={(event) => setSelectedTargetKey(String(event.target.value))}
            value={selectedTargetKey || undefined}
          >
            <div className={cx("detail-page-selector-target-grid")}>
              <section className={cx("detail-page-selector-target-section")}>
                <Text className={cx("detail-page-selector-section-title")} strong>
                  选择要开始设计的页面
                </Text>
                {pages.length ? (
                  <div className={cx("detail-page-selector-options")}>
                    {pages.map((page) => (
                      <Radio.Button
                        key={page.pageId}
                        value={targetSelectionKey("page", page.pageId)}
                      >
                        <span className={cx("detail-page-selector-name")}>
                          {page.label}
                        </span>
                        <span className={cx("detail-page-selector-path")}>
                          {page.path}
                        </span>
                        <span className={cx("detail-page-selector-purpose")}>
                          {page.purpose}
                        </span>
                      </Radio.Button>
                    ))}
                  </div>
                ) : (
                  <Text
                    className={cx("detail-page-selector-empty")}
                    type="secondary"
                  >
                    项目计划中暂无可设计页面。
                  </Text>
                )}
              </section>

              <section className={cx("detail-page-selector-target-section")}>
                <Text className={cx("detail-page-selector-section-title")} strong>
                  选择要开始设计的接口
                </Text>
                {endpointOptions.length ? (
                  <div className={cx("detail-page-selector-options")}>
                    {endpointOptions.map((source) => (
                      <Radio.Button
                        key={source.endpointId}
                        value={source.endpointKey}
                      >
                        <span className={cx("detail-page-selector-name")}>
                          <DatabaseOutlined />
                          <span className={cx("detail-page-selector-method")}>
                            {source.method}
                          </span>
                          {source.path}
                        </span>
                        <span className={cx("detail-page-selector-purpose")}>
                          {source.description}
                        </span>
                      </Radio.Button>
                    ))}
                  </div>
                ) : (
                  <Text
                    className={cx("detail-page-selector-empty")}
                    type="secondary"
                  >
                    项目计划中暂无可设计接口。
                  </Text>
                )}
              </section>
            </div>
          </Radio.Group>
        ) : (
          <Text type="secondary">项目计划中暂无可设计页面或接口。</Text>
        )}

        <Button
          className={cx("detail-page-selector-action")}
          disabled={disabled || !selectedTarget || !selectedTargetId}
          icon={<PlayCircleOutlined />}
          loading={disabled}
          onClick={() =>
            selectedTarget &&
            selectedTargetType &&
            selectedTargetId &&
            void onStart(
              selectedTargetType,
              selectedTargetId,
              selectedTarget.label,
              Boolean(selectedTarget.hasDetailPlan),
              selectedTargetType === "endpoint" && selectedEndpoint
                ? {
                    apiContractId: selectedEndpoint.apiContractId,
                    endpointId: selectedEndpoint.rawEndpointId,
                  }
                : undefined,
            )
          }
          size="large"
          type="primary"
        >
          开始生成「{selectedTarget?.label || "所选对象"}」
        </Button>
      </main>
    </section>
  );
}
