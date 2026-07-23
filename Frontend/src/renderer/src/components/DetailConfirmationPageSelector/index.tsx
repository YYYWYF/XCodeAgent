import {
  DatabaseOutlined,
  LockOutlined,
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

type Props = {
  apiContracts?: DevelopmentPlanningApiContract[];
  disabled: boolean;
  generating?: boolean;
  loading: boolean;
  mode?: "initial" | "locked";
  onStart: (
    targetType: "page" | "data_source",
    targetId: string,
    targetLabel: string,
    hasDetailPlan: boolean,
  ) => Promise<void>;
  pages: DevelopmentPlanningPageOption[];
  selectedPage?: DevelopmentPlanningPageOption;
  workflowEvents?: WorkflowEvent[];
};

/** 在首次进入或选择待设计页面时提供唯一的详细设计入口。 */
export default function DetailConfirmationPageSelector({
  apiContracts = [],
  disabled,
  generating = false,
  loading,
  mode = "initial",
  onStart,
  pages,
  selectedPage: lockedPage,
  workflowEvents,
}: Props): JSX.Element {
  const [selectedTargetType, setSelectedTargetType] = useState<
    "page" | "data_source"
  >("page");
  const [selectedPageId, setSelectedPageId] = useState("");
  const [selectedEndpointId, setSelectedEndpointId] = useState("");
  const endpointOptions = useMemo(() => {
    return apiContracts.flatMap((contract) => {
      const dataSourceIds = contract.dataSourceIds?.length
        ? contract.dataSourceIds
        : [contract.id];
      return contract.endpoints.map((endpoint, endpointIndex) => {
        const endpointId = `${contract.id}:${endpoint.id || endpointIndex + 1}`;
        const dataSourceLabel =
          dataSourceIds.filter(Boolean).join(" / ") || contract.id;
        return {
          endpointId,
          dataSourceId: dataSourceIds[0] || contract.id,
          label: `${endpoint.method} ${endpoint.path}`.trim(),
          method: endpoint.method,
          path: endpoint.path,
          summary: endpoint.summary,
          contractLabel: contract.label || contract.id,
          description:
            endpoint.summary || `来自 ${contract.label || contract.id}`,
          dataSourceLabel,
          hasDetailPlan: false,
        };
      });
    });
  }, [apiContracts]);
  const selectedPage = useMemo(
    () => lockedPage || pages.find((page) => page.pageId === selectedPageId),
    [lockedPage, pages, selectedPageId],
  );
  const selectedEndpoint = useMemo(
    () =>
      endpointOptions.find(
        (source) => source.endpointId === selectedEndpointId,
      ),
    [endpointOptions, selectedEndpointId],
  );
  const selectedTarget =
    selectedTargetType === "data_source" && !lockedPage
      ? selectedEndpoint
      : selectedPage;
  const selectedTargetId =
    selectedTargetType === "data_source" && !lockedPage
      ? selectedEndpoint?.endpointId
      : selectedPage?.pageId;

  // 页面清单刷新后保留有效选择，否则默认选择第一个页面。
  useEffect(() => {
    if (!lockedPage && !pages.some((page) => page.pageId === selectedPageId)) {
      setSelectedPageId(pages[0]?.pageId || "");
    }
  }, [lockedPage, pages, selectedPageId]);

  // 接口清单刷新后保留有效选择，否则默认选择第一个 endpoint。
  useEffect(() => {
    if (
      !endpointOptions.some(
        (source) => source.endpointId === selectedEndpointId,
      )
    ) {
      setSelectedEndpointId(endpointOptions[0]?.endpointId || "");
    }
  }, [endpointOptions, selectedEndpointId]);

  // 如果当前类型没有可选项，自动切到另一个可选类型。
  useEffect(() => {
    if (lockedPage) return;
    if (
      selectedTargetType === "page" &&
      pages.length === 0 &&
      endpointOptions.length > 0
    ) {
      setSelectedTargetType("data_source");
    }
    if (
      selectedTargetType === "data_source" &&
      endpointOptions.length === 0 &&
      pages.length > 0
    ) {
      setSelectedTargetType("page");
    }
  }, [endpointOptions.length, lockedPage, pages.length, selectedTargetType]);

  if (generating && selectedTarget) {
    return (
      <section
        className={cx(
          "detail-page-selector",
          mode === "locked" && "locked-mode",
        )}
      >
        {mode === "locked" ? (
          <div className={cx("detail-page-selector-backdrop")} />
        ) : (
          <div className={cx("detail-page-selector-aurora")} />
        )}
        <main className={cx("detail-page-selector-panel", "progress-panel")}>
          <PageDesignProgress
            events={workflowEvents}
            pageLabel={selectedTarget.label}
          />
        </main>
      </section>
    );
  }

  if (mode === "locked" && selectedPage) {
    return (
      <section className={cx("detail-page-selector", "locked-mode")}>
        <div className={cx("detail-page-selector-backdrop")} />
        <main className={cx("detail-page-selector-panel", "locked-panel")}>
          <span className={cx("detail-page-selector-lock-icon")}>
            <LockOutlined />
          </span>
          <Text className={cx("detail-page-selector-eyebrow")}>
            DETAIL DESIGN REQUIRED
          </Text>
          <Title level={3}>「{selectedPage.label}」尚未进行详细设计</Title>
          <Text
            className={cx("detail-page-selector-locked-copy")}
            type="secondary"
          >
            为避免自由对话跳过页面设计，请先生成该页面的布局、状态、交互与验收标准。
          </Text>
          <div className={cx("detail-page-selector-target")}>
            <div>
              <Text strong>{selectedPage.label}</Text>
              <Text code>{selectedPage.path}</Text>
            </div>
            <Text type="secondary">{selectedPage.purpose}</Text>
          </div>
          <Button
            className={cx("detail-page-selector-action")}
            disabled={disabled}
            icon={<PlayCircleOutlined />}
            loading={disabled}
            onClick={() =>
              void onStart(
                "page",
                selectedPage.pageId,
                selectedPage.label,
                Boolean(selectedPage.hasDetailPlan),
              )
            }
            size="large"
            type="primary"
          >
            开始详细设计
          </Button>
          <Text
            className={cx("detail-page-selector-lock-hint")}
            type="secondary"
          >
            完成生成后将自动解锁当前对话区
          </Text>
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
          <div className={cx("detail-page-selector-target-grid")}>
            <section className={cx("detail-page-selector-target-section")}>
              <Text className={cx("detail-page-selector-section-title")} strong>
                选择要开始设计的页面
              </Text>
              {pages.length ? (
                <Radio.Group
                  aria-label="选择要开始设计的页面"
                  className={cx("detail-page-selector-options")}
                  onChange={(event) => {
                    setSelectedTargetType("page");
                    setSelectedPageId(String(event.target.value));
                  }}
                  value={
                    selectedTargetType === "page" ? selectedPageId : undefined
                  }
                >
                  {pages.map((page) => (
                    <Radio.Button key={page.pageId} value={page.pageId}>
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
                </Radio.Group>
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
                <Radio.Group
                  aria-label="选择要开始设计的接口"
                  className={cx("detail-page-selector-options")}
                  onChange={(event) => {
                    setSelectedTargetType("data_source");
                    setSelectedEndpointId(String(event.target.value));
                  }}
                  value={
                    selectedTargetType === "data_source"
                      ? selectedEndpointId
                      : undefined
                  }
                >
                  {endpointOptions.map((source) => (
                    <Radio.Button
                      key={source.endpointId}
                      value={source.endpointId}
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
                </Radio.Group>
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
            selectedTargetId &&
            void onStart(
              selectedTargetType,
              selectedTargetId,
              selectedTarget.label,
              Boolean(selectedTarget.hasDetailPlan),
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
