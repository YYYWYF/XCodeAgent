import { CheckCircleFilled, LockOutlined, PlayCircleOutlined, RocketOutlined } from '@ant-design/icons'
import { Button, Radio, Skeleton, Tag, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import type { DevelopmentPlanningPageOption } from '../../typings'
import { cx } from '../../utils'
import './DetailConfirmationPageSelector.less'

const { Text, Title } = Typography

type Props = {
  disabled: boolean
  loading: boolean
  mode?: 'initial' | 'locked'
  onStart: (pageId: string, pageLabel: string, hasDetailPlan: boolean) => Promise<void>
  pages: DevelopmentPlanningPageOption[]
  selectedPage?: DevelopmentPlanningPageOption
}

/** 在首次进入或选择待设计页面时提供唯一的详细设计入口。 */
export default function DetailConfirmationPageSelector({
  disabled,
  loading,
  mode = 'initial',
  onStart,
  pages,
  selectedPage: lockedPage
}: Props): JSX.Element {
  const [selectedPageId, setSelectedPageId] = useState('')
  const selectedPage = useMemo(
    () => lockedPage || pages.find((page) => page.key === selectedPageId),
    [lockedPage, pages, selectedPageId]
  )

  // 页面清单刷新后保留有效选择，否则默认选择第一个页面。
  useEffect(() => {
    if (!lockedPage && !pages.some((page) => page.key === selectedPageId)) {
      setSelectedPageId(pages[0]?.key || '')
    }
  }, [lockedPage, pages, selectedPageId])

  if (mode === 'locked' && selectedPage) {
    return (
      <section className={cx('detail-page-selector', 'locked-mode')}>
        <div className={cx('detail-page-selector-backdrop')} />
        <main className={cx('detail-page-selector-panel', 'locked-panel')}>
          <span className={cx('detail-page-selector-lock-icon')}><LockOutlined /></span>
          <Text className={cx('detail-page-selector-eyebrow')}>DETAIL DESIGN REQUIRED</Text>
          <Title level={3}>「{selectedPage.label}」尚未进行详细设计</Title>
          <Text className={cx('detail-page-selector-locked-copy')} type="secondary">
            为避免自由对话跳过页面设计，请先生成该页面的布局、状态、交互与验收标准。
          </Text>
          <div className={cx('detail-page-selector-target')}>
            <div>
              <Text strong>{selectedPage.label}</Text>
              <Text code>{selectedPage.path}</Text>
            </div>
            <Text type="secondary">{selectedPage.purpose}</Text>
          </div>
          <Button
            className={cx('detail-page-selector-action')}
            disabled={disabled}
            icon={<PlayCircleOutlined />}
            loading={disabled}
            onClick={() => void onStart(
              selectedPage.pageId,
              selectedPage.label,
              Boolean(selectedPage.hasDetailPlan)
            )}
            size="large"
            type="primary"
          >
            开始详细设计
          </Button>
          <Text className={cx('detail-page-selector-lock-hint')} type="secondary">
            完成生成后将自动解锁当前对话区
          </Text>
        </main>
      </section>
    )
  }

  return (
    <section className={cx('detail-page-selector')}>
      <div className={cx('detail-page-selector-aurora')} />
      <main className={cx('detail-page-selector-panel')}>
        <header className={cx('detail-page-selector-heading')}>
          <span className={cx('detail-page-selector-logo')}><RocketOutlined /></span>
          <Text className={cx('detail-page-selector-eyebrow')}>PAGE DESIGN</Text>
          <Title level={2}>选择要开始设计的页面</Title>
          <Text type="secondary">页面目录来自 RequirementSpec。选择一个页面，开始第一份详细设计。</Text>
        </header>

        {loading ? (
          <Skeleton active paragraph={{ rows: 4 }} title={false} />
        ) : pages.length ? (
          <Radio.Group
            aria-label="选择要开始设计的页面"
            className={cx('detail-page-selector-options')}
            onChange={(event) => setSelectedPageId(String(event.target.value))}
            value={selectedPageId}
          >
            {pages.map((page) => (
              <Radio.Button key={page.pageId} value={page.pageId}>
                <span className={cx('detail-page-selector-name')}>{page.label}</span>
                <span className={cx('detail-page-selector-path')}>{page.path}</span>
                <span className={cx('detail-page-selector-purpose')}>{page.purpose}</span>
                {page.hasDetailPlan ? (
                  <Tag color="green"><CheckCircleFilled /> 已有 plan</Tag>
                ) : page.designed ? (
                  <Tag color="blue"><CheckCircleFilled /> 已设计</Tag>
                ) : (
                  <Tag>待设计</Tag>
                )}
              </Radio.Button>
            ))}
          </Radio.Group>
        ) : (
          <Text type="secondary">RequirementSpec 的 pages 中暂无可设计页面。</Text>
        )}

        <Button
          className={cx('detail-page-selector-action')}
          disabled={disabled || !selectedPage}
          icon={<PlayCircleOutlined />}
          loading={disabled}
          onClick={() =>
            selectedPage && void onStart(
              selectedPage.pageId,
              selectedPage.label,
              Boolean(selectedPage.hasDetailPlan),
            )
          }
          size="large"
          type="primary"
        >
          {selectedPage?.hasDetailPlan ? '查看并确认' : '开始生成'}「{selectedPage?.label || '所选页面'}」
        </Button>
      </main>
    </section>
  )
}
