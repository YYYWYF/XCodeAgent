import {
  CheckOutlined,
  PlayCircleOutlined,
} from "@ant-design/icons";
import { Button, Typography } from "antd";
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
  /** 是否嵌入流程节点；嵌入时由节点标题承载卡片标题。 */
  embedded?: boolean;
  /** 仅在当前工作流正在启动时显示按钮加载态；历史卡片禁用但不显示转圈。 */
  loading?: boolean;
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
  embedded = false,
  loading = false,
  onStart,
  selectedEndpoint: progressEndpoint,
  selectedPage: progressPage,
}: Props): JSX.Element {
  const [focusedTemplateIndex, setFocusedTemplateIndex] = useState(0);
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
  /** 组装当前选中模板的上下文，供 onStart 携带给后端学习模板源码。 */
  const buildTemplateContext = (): {
    templateId: string;
    templateName: string;
    templateSourcePath: string;
  } | undefined => {
    const selectedTemplate = templates[focusedTemplateIndex];
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

  const focusedTemplate = templates[focusedTemplateIndex] || templates[0];

  /** 切换当前模板；当前轮播页同时就是用户的模板选择。 */
  const focusTemplate = (index: number): void => {
    if (!templates[index]) return;
    setFocusedTemplateIndex(index);
  };

  const renderTemplateShowcase = (): JSX.Element | null => {
    if (!focusedTemplate) return null;
    return (
      <div className={cx("detail-page-selector-template-showcase")}>
        <div className={cx("detail-page-selector-template-preview-frame")}>
          <TemplatePreview
            confirmed={disabled}
            selected
            template={focusedTemplate}
          />
        </div>
        <div className={cx("detail-page-selector-template-showcase-footer")}>
          <div className={cx("detail-page-selector-template-showcase-copy")}>
            <Text strong>{focusedTemplate.manifest.name}</Text>
            <Text type="secondary" title={focusedTemplate.manifest.description}>
              {disabled ? "已确认模板 ·" : "已选模板 ·"}
              {focusedTemplate.manifest.description}
            </Text>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div
      className={cx(
        "detail-page-selector-inline",
        disabled && "disabled",
        embedded && "embedded",
      )}
    >
      {/* 卡片只承载模板选择，不再混入页面详细设计说明。 */}
      {!embedded && (
        <div className={cx("detail-page-selector-inline-header")}>
          <div className={cx("detail-page-selector-inline-title")}>
            <span className={cx("detail-page-selector-inline-signal")} aria-hidden="true" />
            <Text className={cx("detail-page-selector-inline-name")} strong>
              {progressTargetType === "endpoint" ? "接口详细设计" : "选择页面模板"}
            </Text>
          </div>
          {lockedTemplateVisible && (
            <Text className={cx("detail-page-selector-template-stepper")} type="secondary">
              第 {focusedTemplateIndex + 1} / {templates.length} 个
            </Text>
          )}
        </div>
      )}

      {lockedTemplateVisible && (
        <section className={cx("detail-page-selector-locked-template")}>
          {renderTemplateShowcase()}
        </section>
      )}

      <div className={cx("detail-page-selector-inline-actions")}>
        {lockedTemplateVisible && (
          <Button
            disabled={disabled || focusedTemplateIndex === 0}
            onClick={() => focusTemplate(focusedTemplateIndex - 1)}
          >
            上一个
          </Button>
        )}
        {lockedTemplateVisible && (
          <Button
            disabled={disabled || focusedTemplateIndex === templates.length - 1}
            onClick={() => focusTemplate(focusedTemplateIndex + 1)}
          >
            下一个
          </Button>
        )}
        <Button
          className={cx("detail-page-selector-action")}
          disabled={disabled}
          icon={<PlayCircleOutlined />}
          loading={loading}
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

type TemplateOption = ReturnType<typeof getAvailableTemplates>[number];

/** 展示模板真实预览；外部预览地址不可用时使用本地结构示意，保证模板始终可辨认。 */
function TemplatePreview({
  confirmed = false,
  selected,
  template
}: {
  confirmed?: boolean;
  selected: boolean;
  template: TemplateOption;
}): JSX.Element {
  const [imageFailed, setImageFailed] = useState(false);
  const previewImage = template.manifest.previewImage;
  if (previewImage && !imageFailed) {
    return (
      <div
        className={cx(
          "detail-page-selector-template-preview",
          selected && "selected",
          confirmed && "confirmed"
        )}
      >
        <img
          alt={`${template.manifest.name}预览`}
          className={cx("detail-page-selector-template-preview-image")}
          draggable={false}
          onError={() => setImageFailed(true)}
          src={previewImage}
        />
        {selected && (
          <span
            className={cx(
              "detail-page-selector-template-preview-selected",
              confirmed && "confirmed"
            )}
          >
            <CheckOutlined /> {confirmed ? "已确认" : "已选"}
          </span>
        )}
      </div>
    );
  }

  const isForm = template.manifest.id === "multiForm";
  const isTabs = template.manifest.id === "tabsTable";
  return (
    <div
      className={cx(
        "detail-page-selector-template-preview",
        "fallback",
        selected && "selected",
        confirmed && "confirmed"
      )}
    >
      <div className={cx("template-preview-topbar")}>
        <span className={cx("template-preview-brand")} />
        <span className={cx("template-preview-topbar-line", "short")} />
        <span className={cx("template-preview-topbar-line")} />
      </div>
      {isForm ? (
        <div className={cx("template-preview-form")}>
          <div className={cx("template-preview-heading", "wide")} />
          <div className={cx("template-preview-form-grid")}>
            {Array.from({ length: 6 }).map((_, index) => (
              <span className={cx("template-preview-input")} key={index} />
            ))}
          </div>
          <span className={cx("template-preview-submit")} />
        </div>
      ) : (
        <div className={cx("template-preview-table")}>
          <div className={cx("template-preview-heading")} />
          {isTabs && (
            <div className={cx("template-preview-tabs")}>
              <span className={cx("active")} />
              <span />
              <span />
            </div>
          )}
          <div className={cx("template-preview-filters")}>
            <span />
            <span />
            <b />
          </div>
          <div className={cx("template-preview-table-head")} />
          {Array.from({ length: 4 }).map((_, index) => (
            <div className={cx("template-preview-table-row")} key={index}>
              <span />
              <span />
              <span />
              <span />
            </div>
          ))}
        </div>
      )}
      {selected && (
        <span
          className={cx(
            "detail-page-selector-template-preview-selected",
            confirmed && "confirmed"
          )}
        >
          <CheckOutlined /> {confirmed ? "已确认" : "已选"}
        </span>
      )}
    </div>
  );
}
