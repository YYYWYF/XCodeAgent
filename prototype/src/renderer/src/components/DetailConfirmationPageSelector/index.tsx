import {
  FileTextOutlined,
  PlayCircleOutlined,
  ZoomInOutlined,
} from "@ant-design/icons";
import { Button, Tag, Typography } from "antd";
import { useMemo, useState } from "react";
import type {
  DevelopmentPlanningPageOption,
} from "../../typings";
import { cx } from "../../utils";
import { getAvailableTemplates } from "../../service/templateService";
import "./DetailConfirmationPageSelector.less";

const { Text } = Typography;

type DetailTargetType = "page" | "endpoint";

type Props = {
  disabled: boolean;
  onStart?: (
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
  selectedEndpoint?: {
    apiContractId: string;
    endpointId: string;
    hasDetailPlan?: boolean;
    label: string;
    path?: string;
    purpose?: string;
  };
  selectedPage?: DevelopmentPlanningPageOption;
};

/** 未设计目标（页面/接口）的流内挡板卡：目标信息 + 页面模板选择 + 开始详细设计。
 * 由对话区承载，不再整列覆盖弹框。 */
export default function DetailConfirmationPageSelector({
  disabled,
  onStart,
  selectedEndpoint: progressEndpoint,
  selectedPage: progressPage,
}: Props): JSX.Element {
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | undefined>();
  const [hoveredTemplateId, setHoveredTemplateId] = useState<string | undefined>();
  const templates = useMemo(() => getAvailableTemplates(), []);

  const progressTarget = progressEndpoint || progressPage;
  const progressTargetType: DetailTargetType | undefined = progressEndpoint
    ? "endpoint"
    : progressPage
      ? "page"
      : undefined;
  if (!progressTarget || !progressTargetType) {
    return <div className={cx("detail-page-selector-inline")} />;
  }

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

  /** 组装当前选中模板的上下文，供 onStart 携带给后端学习模板源码。 */
  const buildTemplateContext = (): {
    templateId: string;
    templateName: string;
    templateSourcePath: string;
  } | undefined => {
    const selectedTemplate = templates.find(
      (t) => t.manifest.id === selectedTemplateId,
    );
    return selectedTemplate
      ? {
          templateId: selectedTemplate.manifest.id,
          templateName: selectedTemplate.manifest.name,
          templateSourcePath: selectedTemplate.sourcePath,
        }
      : undefined;
  };

  // 仅页面目标支持选择页面模板，让后端 LLM 学习模板源码后再生成。
  const lockedTemplateVisible = progressTargetType === "page" && templates.length > 0;

  const renderTemplateCards = (): JSX.Element => (
    <div className={cx("detail-page-selector-template-cards")}>
      {templates.map((tpl) => {
        const isSelected = selectedTemplateId === tpl.manifest.id;
        const desc = tpl.manifest.description || "";
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
                <div className={cx("detail-page-selector-template-thumb-overlay")}>
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
  );

  return (
    <div className={cx("detail-page-selector-inline")}>
      {/* 卡头：标题 + 状态，对齐「工作流执行」节点的 header 结构 */}
      <div className={cx("detail-page-selector-inline-header")}>
        <div className={cx("detail-page-selector-inline-title")}>
          <span className={cx("detail-page-selector-inline-signal")} aria-hidden="true" />
          <Text className={cx("detail-page-selector-inline-name")} strong>
            {progressTargetType === "endpoint" ? "接口详细设计" : "页面详细设计"}
          </Text>
        </div>
        <Tag className={cx("detail-page-selector-inline-status")}>待设计</Tag>
      </div>

      {/* Agent 叙述正文：以对话消息的自然语言呈现，而不是挡板面板标题 */}
      <div className={cx("detail-page-selector-message")}>
        <Text className={cx("detail-page-selector-message-text")}>
          「{progressTarget.label}」尚未进行详细设计。为避免
          {progressTargetType === "endpoint"
            ? "接口实现跳过需求细化"
            : "自由对话跳过页面需求"}
          ，我将先综合应用需求与项目计划，为
          {progressTargetType === "endpoint"
            ? "该接口生成需求文档"
            : "该页面生成页面需求文档"}
          ，确认或补充后即可进入构建。
        </Text>
      </div>

      {/* 目标信息：浅色引用块，对齐澄清卡的上下文展示 */}
      <div className={cx("detail-page-selector-target")}>
        <div>
          <Text strong>{progressTarget.label}</Text>
          <Text code>{lockedTargetPath}</Text>
        </div>
        <Text type="secondary">{lockedTargetPurpose}</Text>
      </div>

      {lockedTemplateVisible && (
        <section className={cx("detail-page-selector-locked-template")}>
          <Text className={cx("detail-page-selector-section-title")} strong>
            <FileTextOutlined style={{ marginRight: 6 }} />
            选择页面模板（可选）
          </Text>
          {renderTemplateCards()}
        </section>
      )}

      <div className={cx("detail-page-selector-inline-actions")}>
        <Button
          className={cx("detail-page-selector-action")}
          disabled={disabled}
          icon={<PlayCircleOutlined />}
          loading={disabled}
          onClick={() =>
            lockedTargetId &&
            onStart?.(
              progressTargetType,
              lockedTargetId,
              progressTarget.label,
              Boolean(progressTarget.hasDetailPlan),
              progressTargetType === "endpoint" && progressEndpoint
                ? {
                    apiContractId: progressEndpoint.apiContractId,
                    endpointId: progressEndpoint.endpointId,
                  }
                : buildTemplateContext(),
            )
          }
          type="primary"
        >
          开始详细设计
        </Button>
      </div>
    </div>
  );
}
