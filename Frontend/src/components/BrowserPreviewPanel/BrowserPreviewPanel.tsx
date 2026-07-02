import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  DesktopOutlined,
  ExpandOutlined,
  MobileOutlined,
  ReloadOutlined,
  TabletOutlined,
} from '@ant-design/icons';
import { Button, Input, Segmented, Select, Tooltip, Typography } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import type { ApplicationConfig } from '../../typings';
import {
  cx,
  getInitialPreviewUrl,
  normalizePreviewUrl,
  openExternalPreviewUrl,
  storePreviewUrl,
} from '../../utils';
import './BrowserPreviewPanel.less';

const { Text } = Typography;

type PreviewViewport = 'desktop' | 'tablet' | 'mobile';

type Props = {
  application: ApplicationConfig;
};

export default function BrowserPreviewPanel({ application }: Props) {
  const [history, setHistory] = useState(() => [getInitialPreviewUrl(application.id)]);
  const [historyIndex, setHistoryIndex] = useState(0);
  const [draftUrl, setDraftUrl] = useState(history[0]);
  const [selectedPage, setSelectedPage] = useState(application.defaultPage || application.pages[0]);
  const [viewport, setViewport] = useState<PreviewViewport>('desktop');
  const [refreshKey, setRefreshKey] = useState(0);
  const [openError, setOpenError] = useState('');
  const previewUrl = history[historyIndex];

  const pageOptions = useMemo(
    () => application.pages.map((page) => ({ label: page, value: page })),
    [application.pages],
  );

  useEffect(() => {
    setDraftUrl(previewUrl);
    setOpenError('');
    storePreviewUrl(application.id, previewUrl);
  }, [application.id, previewUrl]);

  const navigateTo = (rawUrl: string) => {
    const nextUrl = normalizePreviewUrl(rawUrl);
    if (!nextUrl || nextUrl === previewUrl) {
      setDraftUrl(previewUrl);
      return;
    }

    setHistory((currentHistory) => [...currentHistory.slice(0, historyIndex + 1), nextUrl]);
    setHistoryIndex((currentIndex) => currentIndex + 1);
  };

  const openInBrowser = async () => {
    const targetUrl = normalizePreviewUrl(draftUrl) || previewUrl;
    if (!targetUrl) return;

    setOpenError('');

    try {
      await openExternalPreviewUrl(targetUrl);
    } catch (error) {
      setOpenError(error instanceof Error ? error.message : '无法打开浏览器');
    }
  };

  return (
    <section className={cx('browser-preview-panel')}>
      <header className={cx('browser-preview-toolbar')}>
        <div className={cx('browser-window-controls')} aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <div className={cx('browser-navigation')}>
          <Tooltip title="后退">
            <Button
              aria-label="后退"
              disabled={historyIndex === 0}
              icon={<ArrowLeftOutlined />}
              onClick={() => setHistoryIndex((currentIndex) => Math.max(0, currentIndex - 1))}
              type="text"
            />
          </Tooltip>
          <Tooltip title="前进">
            <Button
              aria-label="前进"
              disabled={historyIndex >= history.length - 1}
              icon={<ArrowRightOutlined />}
              onClick={() =>
                setHistoryIndex((currentIndex) => Math.min(history.length - 1, currentIndex + 1))
              }
              type="text"
            />
          </Tooltip>
          <Tooltip title="刷新">
            <Button
              aria-label="刷新"
              icon={<ReloadOutlined />}
              onClick={() => setRefreshKey((key) => key + 1)}
              type="text"
            />
          </Tooltip>
        </div>
        <Input.Search
          aria-label="预览地址"
          className={cx('browser-address-input')}
          enterButton="访问"
          onChange={(event) => setDraftUrl(event.target.value)}
          onSearch={navigateTo}
          value={draftUrl}
        />
        <Select
          aria-label="页面"
          className={cx('browser-page-select')}
          options={pageOptions}
          value={selectedPage}
          onChange={setSelectedPage}
        />
        <Segmented
          aria-label="视口"
          className={cx('browser-viewport-switcher')}
          options={[
            { label: <DesktopOutlined />, value: 'desktop' },
            { label: <TabletOutlined />, value: 'tablet' },
            { label: <MobileOutlined />, value: 'mobile' },
          ]}
          value={viewport}
          onChange={(value) => setViewport(value as PreviewViewport)}
        />
        <Tooltip title="在系统浏览器打开">
          <Button
            aria-label="在系统浏览器打开"
            icon={<ExpandOutlined />}
            onClick={openInBrowser}
            type="primary"
          />
        </Tooltip>
      </header>

      <div className={cx('browser-preview-stage')}>
        <div className={cx('browser-preview-viewport', viewport)}>
          <iframe
            key={`${previewUrl}-${refreshKey}`}
            className={cx('browser-preview-frame')}
            src={previewUrl}
            title={`${application.name} 网页预览`}
            sandbox="allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts"
          />
        </div>
      </div>

      {openError && (
        <Text className={cx('browser-preview-error')} type="danger">
          {openError}
        </Text>
      )}
    </section>
  );
}
