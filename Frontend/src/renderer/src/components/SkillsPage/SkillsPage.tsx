import {
  AppstoreOutlined,
  CloudUploadOutlined,
  ImportOutlined,
  MoonOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  SunOutlined,
  ThunderboltOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Alert, Button, Empty, Input, Spin, Tag, Tooltip, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { requestUserSkills } from '../../service/userSkills'
import type { UserSkill, UserSkillCatalog } from '../../typings'
import { cx } from '../../utils'
import SkillEditorDrawer from './SkillEditorDrawer'
import './SkillsPage.less'

const { Paragraph, Text, Title } = Typography

type Props = {
  onThemeChange: (theme: 'light' | 'dark') => void
  theme: 'light' | 'dark'
}

type PendingAction = {
  label: string
  icon: ReactNode
  primary?: boolean
}

const pendingActions: PendingAction[] = [
  { label: '刷新', icon: <ReloadOutlined /> },
  { label: 'ZIP 上传', icon: <CloudUploadOutlined /> },
  { label: '导入 Hub', icon: <ImportOutlined /> },
  { label: '批量操作', icon: <ToolOutlined /> },
  { label: '创建技能', icon: <PlusOutlined />, primary: true }
]

function formatUpdatedAt(value: string): string {
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) return '未知时间'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(timestamp)
}

export default function SkillsPage({ onThemeChange, theme }: Props): ReactElement {
  const [catalog, setCatalog] = useState<UserSkillCatalog>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [selectedSkill, setSelectedSkill] = useState<UserSkill>()
  const mountedRef = useRef(true)

  const loadSkills = useCallback(async (): Promise<void> => {
    setLoading(true)
    setError('')
    try {
      const result = await requestUserSkills()
      if (mountedRef.current) setCatalog(result)
    } catch (caughtError) {
      if (mountedRef.current) {
        setError(caughtError instanceof Error ? caughtError.message : '技能列表读取失败。')
      }
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    void loadSkills()
    return () => {
      mountedRef.current = false
    }
  }, [loadSkills])

  const filteredSkills = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase()
    if (!normalizedQuery) return catalog?.skills || []
    return (catalog?.skills || []).filter((skill) =>
      `${skill.name}\n${skill.description}\n${skill.directoryName}`
        .toLocaleLowerCase()
        .includes(normalizedQuery)
    )
  }, [catalog, query])

  return (
    <section className={cx('skills-page')} aria-label="用户技能">
      <header className={cx('skills-header')}>
        <div className={cx('skills-title')}>
          <span className={cx('skills-title-icon')} aria-hidden="true">
            <ThunderboltOutlined />
          </span>
          <div>
            <div className={cx('skills-title-line')}>
              <Title level={4}>技能</Title>
              <Tag>{catalog?.skills.length || 0} 个可用</Tag>
            </div>
            <Text>{catalog?.root || '~/.xcodeagent_dev/skills'}</Text>
          </div>
        </div>
        <div className={cx('skills-actions')}>
          {pendingActions.map((action) => (
            <Tooltip key={action.label} title="即将开放">
              <span>
                <Button disabled icon={action.icon} type={action.primary ? 'primary' : 'default'}>
                  {action.label}
                </Button>
              </span>
            </Tooltip>
          ))}
          <Button
            aria-label={`切换为${theme === 'dark' ? '浅色' : '深色'}主题`}
            icon={theme === 'dark' ? <MoonOutlined /> : <SunOutlined />}
            onClick={() => onThemeChange(theme === 'dark' ? 'light' : 'dark')}
            title={`切换为${theme === 'dark' ? '浅色' : '深色'}主题`}
            type="text"
          />
        </div>
      </header>

      <div className={cx('skills-toolbar')}>
        <Input
          allowClear
          aria-label="搜索技能"
          onChange={(event) => setQuery(event.target.value)}
          placeholder="按名称、描述或目录筛选"
          prefix={<SearchOutlined />}
          value={query}
        />
        <Text type="secondary">
          {query ? `${filteredSkills.length} 个匹配` : `${catalog?.skills.length || 0} 个技能`}
        </Text>
      </div>

      {catalog && catalog.skippedCount > 0 && (
        <Alert
          className={cx('skills-warning')}
          message={`已跳过 ${catalog.skippedCount} 个无效技能`}
          description={catalog.issues
            .map((issue) => `${issue.relativePath}：${issue.message}`)
            .join('；')}
          showIcon
          type="warning"
        />
      )}

      <div className={cx('skills-content')} aria-live="polite">
        {loading ? (
          <div className={cx('skills-state')}>
            <Spin />
            <Text type="secondary">正在读取用户技能...</Text>
          </div>
        ) : error ? (
          <Alert message="无法读取技能列表" description={error} showIcon type="error" />
        ) : filteredSkills.length === 0 ? (
          <Empty
            description={query ? '没有匹配的技能' : '用户技能目录中暂无可用技能'}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : (
          <div className={cx('skills-grid')}>
            {filteredSkills.map((skill) => (
              <article
                aria-expanded={selectedSkill?.relativePath === skill.relativePath}
                className={cx(
                  'skill-card',
                  selectedSkill?.relativePath === skill.relativePath && 'active'
                )}
                key={skill.relativePath}
                onClick={() => setSelectedSkill(skill)}
                onKeyDown={(event) => {
                  if (event.key !== 'Enter' && event.key !== ' ') return
                  event.preventDefault()
                  setSelectedSkill(skill)
                }}
                role="button"
                tabIndex={0}
                title={`编辑技能 ${skill.name}`}
              >
                <div className={cx('skill-card-heading')}>
                  <span className={cx('skill-card-icon')} aria-hidden="true">
                    <AppstoreOutlined />
                  </span>
                  <div className={cx('skill-card-name')}>
                    <Title level={5}>{skill.name}</Title>
                    <Tag>用户技能</Tag>
                  </div>
                </div>
                <Paragraph className={cx('skill-card-description')}>{skill.description}</Paragraph>
                <dl className={cx('skill-card-meta')}>
                  <div>
                    <dt>目录</dt>
                    <dd title={skill.relativePath}>{skill.directoryName}</dd>
                  </div>
                  <div>
                    <dt>更新时间</dt>
                    <dd>{formatUpdatedAt(skill.updatedAt)}</dd>
                  </div>
                  <div>
                    <dt>版本</dt>
                    <dd>{skill.version || '未标注'}</dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
        )}
      </div>
      <SkillEditorDrawer
        onClose={() => setSelectedSkill(undefined)}
        onSaved={async () => {
          setSelectedSkill(undefined)
          await loadSkills()
        }}
        skill={selectedSkill}
        theme={theme}
      />
    </section>
  )
}
