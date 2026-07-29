import {
  DatabaseOutlined,
  FileTextOutlined,
  LockOutlined,
  PlayCircleOutlined,
  RocketOutlined,
  ZoomInOutlined,
} from "@ant-design/icons";
import { Button, Radio, Skeleton, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type {
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption,
  WorkflowEvent,
} from "../../typings";
import { cx } from "../../utils";
import type { PageTemplate } from "../../service/templateService";
import { getAvailableTemplates } from "../../service/templateService";
import PageDesignProgress from "./PageDesignProgress";
import "./DetailConfirmationPageSelector.less";

const { Text, Title } = Typography;

type DetailTargetType = "page" | "endpoint";

type Props = {
  apiContracts?: DevelopmentPlanningApiContract[];
  disabled: boolean;
  generating?: boolean;
  loading: boolean;
  mode?: "initial" | "locked";
  onStart: (
    targetType: "page" | "endpoint",
    targetId: string,
    targetLabel: string,
    hasDetailPlan: boolean,
    targetContext?: {
      apiContractId?: string;
      endpointId?: string;
      /** 选中的页面模板 ID（可选） */
      templateId?: string;
      /** 模板名称 */
      templateName?: string;
      /** 模板源码路径 */
      templateSourcePath?: string;
    },
  ) => Promise<void>;
  pages: DevelopmentPlanningPageOption[];
  pageTree?: DevelopmentPlanningPageTreeNode[];
  selectedEndpoint?: {
    apiContractId: string;
    endpointId: string;
    hasDetailPlan?: boolean;
    label: string;
    path?: string;
    purpose?: string;
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

/** 规范化弹窗中的菜单或页面名称，避免空白名称只渲染出占位块。 */
function normalizeTargetLabel(value: unknown, fallback: string): string {
  const label = String(value || "").trim();
  return label || fallback;
}

/** 递归渲染菜单树中的页面选项，保留项目计划中的目录层级。 */
function renderPageTreeOptions(
  nodes: DevelopmentPlanningPageTreeNode[],
  selectedTargetKey: string,
): ReactNode {
  return nodes.map((node) => {
    if (node.type === "menu") {
      const menuLabel = normalizeTargetLabel(node.label, "未命名菜单");
      return (
        <div className={cx("detail-page-selector-menu-group")} key={node.key}>
          <div className={cx("detail-page-selector-menu-header")}>
            <span className={cx("detail-page-selector-menu-name")}>
              {menuLabel}
            </span>
            {node.uniquePath ? (
              <span className={cx("detail-page-selector-menu-path")}>
                {node.uniquePath}
              </span>
            ) : null}
          </div>
          <div className={cx("detail-page-selector-menu-children")}>
            {renderPageTreeOptions(node.children || [], selectedTargetKey)}
          </div>
        </div>
      );
    }
    const pageId = node.pageId || node.key;
    if (!pageId) return null;
    const nextTargetKey = targetSelectionKey("page", pageId);
    const pageLabel = normalizeTargetLabel(node.label, pageId);
    return (
      <Radio.Button
        key={pageId}
        value={nextTargetKey}
        className={cx(
          "detail-page-selector-page-option",
          selectedTargetKey === nextTargetKey && "is-selected",
        )}
      >
        <span className={cx("detail-page-selector-name")}>{pageLabel}</span>
        <span className={cx("detail-page-selector-path")}>{node.path || "/"}</span>
        <span className={cx("detail-page-selector-purpose")}>
          {node.purpose || "业务页面"}
        </span>
      </Radio.Button>
    );
  });
}

/** 在首次进入或选择待设计页面时提供唯一的详细设计入口。 */
export default function DetailConfirmationPageSelector({
  apiContracts = [],
  disabled,
  generating = false,
  loading,
  mode = "initial",
  onStart,
  pages,
  pageTree = [],
  selectedEndpoint: progressEndpoint,
  selectedPage: progressPage,
  workflowEvents,
}: Props): JSX.Element {
  const [selectedTargetKey, setSelectedTargetKey] = useState("");
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | undefined>();
  const [hoveredTemplateId, setHoveredTemplateId] = useState<string | undefined>();
  const [previewingTemplate, setPreviewingTemplate] = useState<PageTemplate | undefined>();
  const templates = useMemo(() => getAvailableTemplates(), []);
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
            pageLabel={progressTarget.label}
            targetType={progressTargetType}
          />
        </main>
      </section>
    );
  }

  if (mode === "locked" && progressTarget && progressTargetType) {
    const lockedTargetId =
      progressTargetType === "endpoint"
        ? progressEndpoint?.endpointId
        : progressPage?.pageId;
    const lockedTargetPath =
      progressTargetType === "endpoint"
        ? progressEndpoint?.path || progressEndpoint?.label
        : progressPage?.path;
    const lockedTargetPurpose =
      progressTargetType === "endpoint"
        ? progressEndpoint?.purpose || "补充接口用途、处理逻辑和数据来源设计。"
        : progressPage?.purpose;
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
          <Title level={3}>「{progressTarget.label}」尚未进行详细设计</Title>
          <Text
            className={cx("detail-page-selector-locked-copy")}
            type="secondary"
          >
            {progressTargetType === "endpoint"
              ? "为避免接口实现跳过契约细化，请先生成该接口的用途、处理逻辑与数据来源设计。"
              : "为避免自由对话跳过页面设计，请先生成该页面的布局、状态、交互与验收标准。"}
          </Text>
          <div className={cx("detail-page-selector-target")}>
            <div>
              <Text strong>{progressTarget.label}</Text>
              <Text code>{lockedTargetPath}</Text>
            </div>
            <Text type="secondary">{lockedTargetPurpose}</Text>
          </div>
          <Button
            className={cx("detail-page-selector-action")}
            disabled={disabled}
            icon={<PlayCircleOutlined />}
            loading={disabled}
            onClick={() =>
              lockedTargetId &&
              void onStart(
                progressTargetType,
                lockedTargetId,
                progressTarget.label,
                Boolean(progressTarget.hasDetailPlan),
                progressTargetType === "endpoint" && progressEndpoint
                  ? {
                    apiContractId: progressEndpoint.apiContractId,
                    endpointId: progressEndpoint.endpointId,
                  }
                  : undefined,
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
            完成生成并确认后将自动解锁当前对话区
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
                    {pageTree.length
                      ? renderPageTreeOptions(pageTree, selectedTargetKey)
                      : pages.map((page) => (
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

              {templates.length > 0 ? (
                <section className={cx("detail-page-selector-target-section", "detail-page-selector-template-section")}>
                  <Text className={cx("detail-page-selector-section-title")} strong>
                    <FileTextOutlined style={{ marginRight: 6 }} />
                    选择页面模板（可选）
                  </Text>
                  <div className={cx("detail-page-selector-template-cards")}>
                    {templates.map((tpl) => {
                      const isSelected = selectedTemplateId === tpl.manifest.id;
                      const desc = tpl.manifest.description || '';
                      const previewImg = tpl.manifest.previewImage;
                      return (
                        <div
                          key={tpl.manifest.id}
                          className={cx(
                            "detail-page-selector-template-card",
                            isSelected && "selected",
                          )}
                          onClick={() => {
                            setSelectedTemplateId(isSelected ? undefined : tpl.manifest.id);
                          }}
                        >
                          <div
                            className={cx("detail-page-selector-template-thumb")}
                            onMouseEnter={() => previewImg && setHoveredTemplateId(tpl.manifest.id)}
                            onMouseLeave={() => setHoveredTemplateId(undefined)}
                          >
                            {previewImg ? (
                              <img
                                src={previewImg}
                                alt={tpl.manifest.name}
                                draggable={false}
                              />
                            ) : (
                              <div className={cx("detail-page-selector-template-thumb-empty")}>
                                <FileTextOutlined />
                                <Text type="secondary" style={{ fontSize: 11 }}>
                                  暂无预览图
                                </Text>
                              </div>
                            )}
                            {previewImg && hoveredTemplateId === tpl.manifest.id && (
                              <div
                                className={cx("detail-page-selector-template-thumb-overlay")}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setPreviewingTemplate(tpl);
                                }}
                              >
                                <ZoomInOutlined />
                                <span>预览</span>
                              </div>
                            )}
                          </div>

                          <div className={cx("detail-page-selector-template-card-body")}>
                            <Text strong className={cx("detail-page-selector-template-card-name")}>
                              {tpl.manifest.name}
                            </Text>
                            <Text
                              type="secondary"
                              className={cx("detail-page-selector-template-desc")}
                              title={desc}
                            >
                              {desc}
                            </Text>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </section>
              ) : (
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
              )}
            </div>

            {/* ---------- 选择要开始设计的接口（有模板时移至下方整行） ---------- */}
            {templates.length > 0 && (
              <section className={cx("detail-page-selector-target-section", "detail-page-selector-endpoint-row")}>
                <Text className={cx("detail-page-selector-section-title")} strong>
                  <DatabaseOutlined style={{ marginRight: 6 }} />
                  选择要开始设计的接口
                </Text>
                {endpointOptions.length ? (
                  <div className={cx("detail-page-selector-options", "detail-page-selector-endpoint-options")}>
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
            )}
          </Radio.Group>
        ) : (
          <Text type="secondary">项目计划中暂无可设计页面或接口。</Text>
        )}

        {/* ---------- 模板预览图全屏放大弹窗 ---------- */}
        {previewingTemplate && previewingTemplate.manifest.previewImage && (
          <div
            className={cx("detail-page-selector-template-zoom-mask")}
            onClick={() => setPreviewingTemplate(undefined)}
          >
            <div
              className={cx("detail-page-selector-template-zoom-box")}
              onClick={(e) => e.stopPropagation()}
            >
              <img
                src={previewingTemplate.manifest.previewImage}
                alt={previewingTemplate.manifest.name}
                draggable={false}
              />
              <div className={cx("detail-page-selector-template-zoom-caption")}>
                <Text strong style={{ color: "#fff" }}>
                  {previewingTemplate.manifest.name}
                </Text>
                <Text style={{ color: "rgba(255,255,255,0.72)", fontSize: 12 }}>
                  {previewingTemplate.manifest.description}
                </Text>
                <Text style={{ color: "rgba(255,255,255,0.45)", fontSize: 11, marginTop: 4 }}>
                  点击空白处关闭
                </Text>
              </div>
            </div>
          </div>
        )}

        <Button
          className={cx("detail-page-selector-action")}
          disabled={disabled || !selectedTarget || !selectedTargetId}
          icon={<PlayCircleOutlined />}
          loading={disabled}
          onClick={() => {
            const selectedTemplate = templates.find(
              (t) => t.manifest.id === selectedTemplateId,
            );
            const templateContext =
              selectedTargetType === "page" && selectedTemplate
                ? {
                  templateId: selectedTemplate.manifest.id,
                  templateName: selectedTemplate.manifest.name,
                  templateSourcePath: selectedTemplate.sourcePath,
                }
                : undefined;

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
                  : templateContext || undefined,
              );
          }}
          size="large"
          type="primary"
        >
          开始生成「{selectedTarget?.label || "所选对象"}」
        </Button>
      </main>
    </section>
  );
}
