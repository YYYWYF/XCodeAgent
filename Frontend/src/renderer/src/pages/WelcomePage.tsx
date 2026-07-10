import {
  DesktopOutlined,
  MoonOutlined,
  QuestionCircleOutlined,
  SettingOutlined,
  SunOutlined
} from '@ant-design/icons'
import { Button, Dropdown, message, Tooltip } from 'antd'
import type { MenuProps } from 'antd'
import { useEffect, useState } from 'react'
import {
  CreateApplicationAction,
  OpenWorkspaceAction,
  WelcomeAgentTrack,
  WelcomeHero,
  WelcomeRecentProjects
} from '../components/Welcome'
import type { ApplicationConfig } from '../typings'
import { cx } from '../utils'
import './WelcomePage.less'
import './WelcomePageLight.less'

type Props = {
  onOpenApplication: (application: ApplicationConfig) => void
}

type WelcomeTheme = 'dark' | 'light'
type ThemePreference = WelcomeTheme | 'system'

const THEME_PREFERENCE_KEY = 'xcode-agent-theme-preference'

const themeItems: MenuProps['items'] = [
  { key: 'system', icon: <DesktopOutlined />, label: '跟随系统' },
  { key: 'light', icon: <SunOutlined />, label: '浅色主题' },
  { key: 'dark', icon: <MoonOutlined />, label: '深色主题' }
]

function getSystemTheme(): WelcomeTheme {
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

function getThemePreference(): ThemePreference {
  const storedPreference = window.localStorage.getItem(THEME_PREFERENCE_KEY)
  return storedPreference === 'light' || storedPreference === 'dark' ? storedPreference : 'system'
}

export default function WelcomePage({ onOpenApplication }: Props): JSX.Element {
  const [themePreference, setThemePreference] = useState<ThemePreference>(getThemePreference)
  const [systemTheme, setSystemTheme] = useState<WelcomeTheme>(getSystemTheme)
  const theme = themePreference === 'system' ? systemTheme : themePreference

  useEffect(() => {
    const colorScheme = window.matchMedia('(prefers-color-scheme: light)')
    const syncTheme = (event: MediaQueryListEvent): void => {
      setSystemTheme(event.matches ? 'light' : 'dark')
    }
    colorScheme.addEventListener('change', syncTheme)
    return () => colorScheme.removeEventListener('change', syncTheme)
  }, [])

  const handleThemeChange: MenuProps['onClick'] = ({ key }) => {
    const nextPreference = key as ThemePreference
    setThemePreference(nextPreference)
    window.localStorage.setItem(THEME_PREFERENCE_KEY, nextPreference)
  }

  return (
    <main className={cx('welcome-page')} data-theme={theme}>
      <section className={cx('welcome-shell')}>
        <header className={cx('welcome-topbar')}>
          <div className={cx('welcome-brand')} aria-label="XCodeAgent">
            <span className={cx('welcome-brand-mark')} aria-hidden="true">
              <i />
              <i />
            </span>
            <span>XCodeAgent</span>
          </div>

          <nav className={cx('welcome-utilities')} aria-label="欢迎页工具">
            <Dropdown
              menu={{
                items: themeItems,
                onClick: handleThemeChange,
                selectable: true,
                selectedKeys: [themePreference]
              }}
              overlayClassName={cx('welcome-theme-dropdown', theme)}
              placement="bottomRight"
              trigger={['click']}
            >
              <Button
                aria-label="设置与主题"
                icon={<SettingOutlined />}
                title="切换主题"
                type="text"
              />
            </Dropdown>
            <Tooltip title="帮助功能即将推出">
              <Button
                aria-label="帮助"
                icon={<QuestionCircleOutlined />}
                onClick={() => message.info('帮助功能即将推出')}
                type="text"
              />
            </Tooltip>
          </nav>
        </header>

        <div className={cx('welcome-content')}>
          <section className={cx('welcome-primary')}>
            <WelcomeHero />

            <section className={cx('welcome-actions')} aria-label="开始使用 XCodeAgent">
              <CreateApplicationAction onOpenApplication={onOpenApplication} theme={theme} />
              <OpenWorkspaceAction onOpenApplication={onOpenApplication} theme={theme} />
            </section>

            <WelcomeRecentProjects onOpenApplication={onOpenApplication} />
          </section>

          <WelcomeAgentTrack />
        </div>
      </section>
    </main>
  )
}
