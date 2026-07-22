import {
  CloudUploadOutlined,
  ImportOutlined,
  PlusOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Alert, Button, Empty, Modal, Spin, Tag, Tooltip, Typography, message } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { isAuthenticationFailure } from '../../service/authentication'
import {
  deleteUserSkill,
  requestUserSkills,
  setUserSkillEnabled
} from '../../service/userSkills'
import type { UserSkill, UserSkillCatalog } from '../../typings'
import { cx } from '../../utils'
import SkillEditorDrawer from './SkillEditorDrawer'
import SkillZipImportModal from './SkillZipImportModal'
import SkillCatalogCard from './SkillCatalogCard'
import SkillCatalogToolbar from './SkillCatalogToolbar'
import {
  DEFAULT_SKILL_CATEGORY,
  filterCatalogSkills,
  type SkillCategory
} from './skillCatalog'
import './SkillDelete.less'
import './SkillsPage.less'

const { Text, Title } = Typography

type Props = {
  onSkillDisabled?: (skillName: string) => void
  theme: 'light' | 'dark'
}

type PendingAction = {
  label: string
  icon: ReactNode
}

const pendingActions: PendingAction[] = [
  { label: '导入 Hub', icon: <ImportOutlined /> },
  { label: '批量操作', icon: <ToolOutlined /> }
]

/** 渲染支持来源分类、启停、刷新和用户技能维护的技能页面。 */
export default function SkillsPage({
  onSkillDisabled,
  theme
}: Props): ReactElement {
  const [catalog, setCatalog] = useState<UserSkillCatalog>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [category, setCategory] = useState<SkillCategory>(DEFAULT_SKILL_CATEGORY)
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)
  const [importingZip, setImportingZip] = useState(false)
  const [deletingSkillPath, setDeletingSkillPath] = useState('')
  const [togglingSkillPaths, setTogglingSkillPaths] = useState<Set<string>>(new Set())
  const [selectedSkill, setSelectedSkill] = useState<UserSkill>()
  const mountedRef = useRef(true)
  const catalogRef = useRef<UserSkillCatalog>()
  const loadSequenceRef = useRef(0)

  const loadSkills = useCallback(async (manualRefresh = false): Promise<void> => {
    /** 重新扫描两类技能目录，并忽略晚于当前请求返回的旧响应。 */
    const sequence = loadSequenceRef.current + 1
    loadSequenceRef.current = sequence
    if (manualRefresh && catalogRef.current) setRefreshing(true)
    else setLoading(true)
    setError('')
    try {
      const result = await requestUserSkills()
      if (!mountedRef.current || sequence !== loadSequenceRef.current) return
      catalogRef.current = result
      setCatalog(result)
      setSelectedSkill((current) => {
        if (!current) return current
        const refreshed = result.skills.find((skill) => skill.relativePath === current.relativePath)
        if (refreshed) return refreshed
        if (manualRefresh) message.warning(`技能 ${current.name} 已不存在，编辑器已关闭。`)
        return undefined
      })
      if (manualRefresh) message.success('技能列表已刷新')
    } catch (caughtError) {
      if (!mountedRef.current || sequence !== loadSequenceRef.current) return
      const errorMessage = isAuthenticationFailure(caughtError)
        ? '请重新登录后重试。'
        : caughtError instanceof Error
          ? caughtError.message
          : '技能列表读取失败。'
      if (catalogRef.current) message.error(errorMessage)
      else setError(errorMessage)
    } finally {
      if (mountedRef.current && sequence === loadSequenceRef.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    void loadSkills(false)
    return () => {
      mountedRef.current = false
    }
  }, [loadSkills])

  const filteredSkills = useMemo(
    () => filterCatalogSkills(catalog, category, query),
    [catalog, category, query]
  )
  const categorySkills = category === 'user' ? catalog?.skills || [] : catalog?.builtinSkills || []
  const categoryRoot = category === 'user' ? catalog?.root : catalog?.builtinRoot

  /** 切换技能来源时关闭用户编辑器，避免只读分类保留编辑状态。 */
  const handleCategoryChange = (nextCategory: SkillCategory): void => {
    setCategory(nextCategory)
    setSelectedSkill(undefined)
  }

  /** 持久化用户技能开关，并只在服务端确认后更新卡片和草稿标签。 */
  const handleToggleSkill = async (skill: UserSkill, enabled: boolean): Promise<void> => {
    setTogglingSkillPaths((current) => new Set(current).add(skill.relativePath))
    try {
      const updated = await setUserSkillEnabled({ relativePath: skill.relativePath, enabled })
      if (!mountedRef.current) return
      setCatalog((current) => {
        if (!current) return current
        const next = {
          ...current,
          skills: current.skills.map((item) =>
            item.relativePath === updated.relativePath ? updated : item
          )
        }
        catalogRef.current = next
        return next
      })
      setSelectedSkill((current) =>
        current?.relativePath === updated.relativePath ? updated : current
      )
      if (!updated.enabled) onSkillDisabled?.(updated.name)
      message.success(`技能 ${updated.name} 已${updated.enabled ? '开启' : '关闭'}`)
    } catch (caughtError) {
      if (isAuthenticationFailure(caughtError)) return
      message.error(caughtError instanceof Error ? caughtError.message : '技能状态更新失败。')
    } finally {
      if (mountedRef.current) {
        setTogglingSkillPaths((current) => {
          const next = new Set(current)
          next.delete(skill.relativePath)
          return next
        })
      }
    }
  }

  /** 弹出不可恢复删除确认，并在完成后重新扫描技能目录。 */
  const confirmDeleteSkill = (skill: UserSkill): void => {
    Modal.confirm({
      cancelText: '取消',
      centered: true,
      className: cx('skill-delete-confirm', `theme-${theme}`),
      content: `删除后将同时移除目录 ${skill.directoryName} 及其中的所有辅助资源，且无法恢复。`,
      okButtonProps: { danger: true },
      okText: '删除',
      onOk: async () => {
        setDeletingSkillPath(skill.relativePath)
        try {
          await deleteUserSkill(skill.relativePath)
          if (selectedSkill?.relativePath === skill.relativePath) {
            setSelectedSkill(undefined)
          }
          message.success(`技能 ${skill.name} 已删除`)
          await loadSkills(false)
        } catch (caughtError) {
          if (isAuthenticationFailure(caughtError)) return
          message.error(caughtError instanceof Error ? caughtError.message : '技能删除失败。')
        } finally {
          if (mountedRef.current) setDeletingSkillPath('')
        }
      },
      title: `确认删除技能 ${skill.name}？`
    })
  }

  return (
    <section className={cx('skills-page')} aria-label="技能">
      <header className={cx('skills-header')}>
        <div className={cx('skills-title')}>
          <span className={cx('skills-title-icon')} aria-hidden="true">
            <ThunderboltOutlined />
          </span>
          <div>
            <div className={cx('skills-title-line')}>
              <Title level={4}>技能</Title>
              <Tag>{categorySkills.length} 个{category === 'user' ? '用户' : '内置'}</Tag>
            </div>
            <Text>{categoryRoot || (category === 'user' ? '~/.xcodeagent_dev/skills' : '/.xcodeagent/builtin-skills')}</Text>
          </div>
        </div>
        <div className={cx('skills-actions')}>
          <Button
            icon={<ReloadOutlined />}
            loading={refreshing}
            onClick={() => void loadSkills(true)}
          >
            刷新
          </Button>
          {category === 'user' && (
            <>
              {pendingActions.map((action) => (
                <Tooltip key={action.label} title="即将开放">
                  <span>
                    <Button disabled icon={action.icon}>
                      {action.label}
                    </Button>
                  </span>
                </Tooltip>
              ))}
              <Button
                className={cx('skills-zip-upload-button')}
                icon={<CloudUploadOutlined />}
                onClick={() => setImportingZip(true)}
                type="primary"
              >
                ZIP 上传
              </Button>
              <Button
                className={cx('skills-create-button')}
                icon={<PlusOutlined />}
                onClick={() => {
                  setSelectedSkill(undefined)
                  setCreating(true)
                }}
                type="primary"
              >
                创建技能
              </Button>
            </>
          )}
        </div>
      </header>

      <SkillCatalogToolbar
        category={category}
        countLabel={query ? `${filteredSkills.length} 个匹配` : `${categorySkills.length} 个技能`}
        onCategoryChange={handleCategoryChange}
        onQueryChange={setQuery}
        query={query}
      />

      {category === 'user' && catalog && catalog.skippedCount > 0 && (
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
          <Alert
            action={<Button onClick={() => void loadSkills(false)}>重试</Button>}
            message="无法读取技能列表"
            description={error}
            showIcon
            type="error"
          />
        ) : filteredSkills.length === 0 ? (
          <Empty
            description={
              query
                ? '没有匹配的技能'
                : category === 'user'
                  ? '用户技能目录中暂无可用技能'
                  : '暂无可展示的内置技能'
            }
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : (
          <div className={cx('skills-grid')}>
            {filteredSkills.map((skill) => (
              <SkillCatalogCard
                active={
                  category === 'user' && selectedSkill?.relativePath === skill.relativePath
                }
                category={category}
                deleting={deletingSkillPath === skill.relativePath}
                key={`${category}:${skill.relativePath}`}
                onDelete={confirmDeleteSkill}
                onOpen={setSelectedSkill}
                onToggle={(target, enabled) => void handleToggleSkill(target, enabled)}
                skill={skill}
                toggling={togglingSkillPaths.has(skill.relativePath)}
              />
            ))}
          </div>
        )}
      </div>
      <SkillEditorDrawer
        mode="create"
        onClose={() => setCreating(false)}
        onSaved={async () => {
          setCreating(false)
          await loadSkills(false)
        }}
        open={creating}
        theme={theme}
      />
      <SkillZipImportModal
        existingSkillNames={(catalog?.skills || []).map((skill) => skill.name)}
        onClose={() => setImportingZip(false)}
        onImported={loadSkills}
        open={importingZip}
        theme={theme}
      />
      <SkillEditorDrawer
        mode="edit"
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
