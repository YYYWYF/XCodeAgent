import { PlayCircleOutlined, RocketOutlined } from '@ant-design/icons'
import { Button, Radio, Skeleton, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import type { DevelopmentPlanningPageOption } from '../../typings'
import { cx } from '../../utils'
import './DetailConfirmationPageSelector.less'

const { Text, Title } = Typography

type Props = {
  disabled: boolean
  loading: boolean
  onStart: (pageId: string, pageLabel: string) => Promise<void>
  pages: DevelopmentPlanningPageOption[]
}

/** 通过全屏蒙层展示 ProjectPlan 页面概览，并让用户选择细节设计起点。 */
export default function DetailConfirmationPageSelector({
  disabled,
  loading,
  onStart,
  pages
}: Props): JSX.Element {
  const [selectedPageId, setSelectedPageId] = useState('')
  const selectedPage = useMemo(
    () => pages.find((page) => page.key === selectedPageId),
    [pages, selectedPageId]
  )

  // 页面清单刷新后保留有效选择，否则默认选择第一个页面。
  useEffect(() => {
    if (!pages.some((page) => page.key === selectedPageId)) {
      setSelectedPageId(pages[0]?.key || '')
    }
  }, [pages, selectedPageId])

  return (
    <section className={cx('detail-page-selector')}>
      <div className={cx('detail-page-selector-aurora')} />
      <main className={cx('detail-page-selector-panel')}>
        <header className={cx('detail-page-selector-heading')}>
          <span className={cx('detail-page-selector-logo')}><RocketOutlined /></span>
          <Text className={cx('detail-page-selector-eyebrow')}>PAGE DESIGN</Text>
          <Title level={2}>选择要开始设计的页面</Title>
          <Text type="secondary">页面来自 ProjectPlan 的 frontend_pages。选择一个具体页面后开始生成。</Text>
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
              <Radio.Button key={page.key} value={page.key}>
                <span className={cx('detail-page-selector-name')}>{page.label}</span>
                <span className={cx('detail-page-selector-path')}>{page.path}</span>
                <span className={cx('detail-page-selector-purpose')}>{page.purpose}</span>
              </Radio.Button>
            ))}
          </Radio.Group>
        ) : (
          <Text type="secondary">ProjectPlan 的 frontend_pages 中暂无可设计页面。</Text>
        )}

        <Button
          className={cx('detail-page-selector-action')}
          disabled={disabled || !selectedPage}
          icon={<PlayCircleOutlined />}
          loading={disabled}
          onClick={() => selectedPage && void onStart(selectedPage.key, selectedPage.label)}
          size="large"
          type="primary"
        >
          开始生成「{selectedPage?.label || '所选页面'}」
        </Button>
      </main>
    </section>
  )
}
