import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Empty, Input, message, Modal } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../utils'
import type { DataSourcesDetailTarget } from '../AiChatPanel/components/AuxiliaryDrawer'
import {
  externalApisOfDomain,
  externalDomains,
  useDataSources,
  type DataSource,
  type ExternalApiDomain
} from './catalog'
import './DataSourcesPage.less'

type Props = {
  /** 打开右侧衔接的详情维护层：接口维护（含新增）。 */
  onOpenDetail: (target: DataSourcesDetailTarget) => void
}

/** 列表条目：与数据源抽屉的表条目同款设计，方法徽标 + 名称 + 说明。 */
function ApiRow({
  method,
  label,
  meta,
  onOpen
}: {
  method: string
  label: string
  meta?: string
  onOpen: () => void
}): ReactElement {
  return (
    <button
      className={cx('conversation-item')}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onOpen()
        }
      }}
      title={label}
      type="button"
    >
      <span className={cx('ds-method')}>{method}</span>
      <span className={cx('conversation-item-title')}>{label}</span>
      {meta ? <span className={cx('conversation-item-meta')}>{meta}</span> : null}
    </button>
  )
}

type DomainDraft = { id: string; name: string; baseUrl: string; description: string }

/** 生成空白域草稿。 */
function createDomainDraft(): DomainDraft {
  return { id: '', name: '', baseUrl: '', description: '' }
}

/** 从已有域初始化编辑草稿。 */
function domainDraftFrom(domain: ExternalApiDomain): DomainDraft {
  return { id: domain.id, name: domain.name, baseUrl: domain.baseUrl, description: domain.description }
}

type DomainModalProps = {
  open: boolean
  /** 编辑目标；为空表示新增域。 */
  editing: ExternalApiDomain | null
  onClose: () => void
  onSave: (domain: ExternalApiDomain) => void
  /** 编辑态提供删除入口（由父级做二次确认与级联处理）。 */
  onDelete?: (domain: ExternalApiDomain) => void
}

/**
 * 域维护弹窗：与数据库连接设置同构——名称 + 域名地址 + 说明，
 * 编辑态底部提供删除入口；域下接口随域级联删除，由父级确认。
 */
function ExternalApiDomainModal({ open, editing, onClose, onSave, onDelete }: DomainModalProps): JSX.Element {
  const [draft, setDraft] = useState<DomainDraft>(createDomainDraft)

  useEffect(() => {
    if (open) setDraft(editing ? domainDraftFrom(editing) : createDomainDraft())
  }, [editing, open])

  /** 校验必填项：域名称与域名地址；错误就地提示，不关闭弹窗。 */
  const validate = (): string => {
    if (!draft.name.trim()) return '请输入域名称。'
    if (!draft.baseUrl.trim()) return '请输入域名地址。'
    return ''
  }

  /** 保存域信息。 */
  const save = (): void => {
    const error = validate()
    if (error) {
      message.error(error)
      return
    }
    onSave({
      type: 'external_domain',
      id: draft.id || `domain-${Date.now()}`,
      name: draft.name.trim(),
      baseUrl: draft.baseUrl.trim(),
      description: draft.description.trim()
    })
  }

  return (
    <Modal
      cancelText="取消"
      className={cx('ds-editor-modal')}
      destroyOnClose
      footer={
        <div className={cx('ds-editor-footer')}>
          {editing && onDelete ? (
            <Button danger icon={<DeleteOutlined />} onClick={() => onDelete(editing)}>
              删除域
            </Button>
          ) : null}
          <span className={cx('ds-editor-footer-spacer')} />
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" onClick={save}>
            保存
          </Button>
        </div>
      }
      onCancel={onClose}
      onOk={save}
      open={open}
      title={editing ? '域设置' : '新增域'}
      width={520}
    >
      <div className={cx('ds-editor-form')}>
        <label>
          <span>
            <em aria-hidden="true" className={cx('ds-editor-required')}>*</em>
            域名称
          </span>
          <Input
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            placeholder="例如：门户平台"
            value={draft.name}
          />
        </label>
        <label>
          <span>
            <em aria-hidden="true" className={cx('ds-editor-required')}>*</em>
            域名地址
          </span>
          <Input
            onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })}
            placeholder="例如：https://portal.example.com"
            value={draft.baseUrl}
          />
        </label>
        <label>
          说明
          <Input
            onChange={(event) => setDraft({ ...draft, description: event.target.value })}
            placeholder="一句话描述该域下的系统或服务"
            value={draft.description}
          />
        </label>
      </div>
    </Modal>
  )
}

/**
 * 外部 API 抽屉列表：一个域一个 Tab（该域下登记的接口），末尾「＋」新增域。
 * 域是同一域名下的后端系统；接口在域内登记，点击进入右侧衔接的接口维护页。
 */
export default function ExternalApisPage({ onOpenDetail }: Props): JSX.Element {
  const [sources, saveSources] = useDataSources()
  /** 当前 Tab：域 id；域被删除后自动回落到剩余第一个。 */
  const [activeTab, setActiveTab] = useState<string>('')
  const [domainModal, setDomainModal] = useState<{ open: boolean; editing: ExternalApiDomain | null }>({
    open: false,
    editing: null
  })

  const domains = externalDomains(sources)
  const activeDomain = domains.find((item) => item.id === activeTab) || domains[0]
  const activeApis = activeDomain ? externalApisOfDomain(sources, activeDomain.id) : []

  /** 保存域：新增后切到该域的 Tab；编辑保留域下已登记接口。 */
  const saveDomain = (domain: ExternalApiDomain): void => {
    const isNew = !domainModal.editing
    saveSources(
      isNew ? [...sources, domain] : sources.map((item): DataSource => (item.id === domain.id ? domain : item))
    )
    setDomainModal({ open: false, editing: null })
    if (isNew) setActiveTab(domain.id)
    message.success(isNew ? '域已创建' : '域设置已更新')
  }

  /** 删除域前二次确认：域与其下已登记接口一并移除，Tab 回到剩余第一个域。 */
  const deleteDomain = (domain: ExternalApiDomain): void => {
    Modal.confirm({
      centered: true,
      cancelText: '取消',
      content: '将同时移除该域与域下已登记的接口；如已有应用API绑定相关接口，将提示重新绑定。',
      okButtonProps: { danger: true },
      okText: '删除',
      onOk: () => {
        saveSources(
          sources.filter(
            (item) =>
              !(
                item.id === domain.id ||
                (item.type === 'external_service' && item.domainId === domain.id)
              )
          )
        )
        setDomainModal({ open: false, editing: null })
        if (activeTab === domain.id) setActiveTab('')
        message.success('域已删除')
      },
      title: `删除域「${domain.name}」？`
    })
  }

  return (
    <section className={cx('data-sources-page')}>
      {/* 活跃 Tab 栏：一个域一个 Tab，末尾「＋」新增域。 */}
      <div className={cx('conversation-tabbar', 'ds-tabbar')}>
        <nav aria-label="外部API域分段" className={cx('conversation-tabs')}>
          {domains.map((domain) => (
            <button
              aria-pressed={activeDomain?.id === domain.id}
              className={cx('conversation-tab', activeDomain?.id === domain.id && 'active')}
              key={domain.id}
              onClick={() => setActiveTab(domain.id)}
              title={domain.name}
              type="button"
            >
              <span className={cx('conversation-tab-label')}>{domain.name}</span>
            </button>
          ))}
          <button
            aria-label="新增域"
            className={cx('conversation-tab', 'ds-tab-add')}
            onClick={() => setDomainModal({ open: true, editing: null })}
            title="新增域"
            type="button"
          >
            <PlusOutlined aria-hidden="true" />
          </button>
        </nav>
      </div>
      {activeDomain ? (
        <>
          <p className={cx('ds-hint')}>
            <span className={cx('ds-hint-text')}>接口按出入参粒度参与应用API适配。</span>
            <span className={cx('ds-hint-actions')}>
              <Button
                onClick={() => setDomainModal({ open: true, editing: activeDomain })}
                size="small"
              >
                域设置
              </Button>
              <Button
                onClick={() => onOpenDetail({ kind: 'api-new', domainId: activeDomain.id })}
                size="small"
                type="primary"
              >
                新增接口
              </Button>
            </span>
          </p>
          <div className={cx('ds-list')}>
            {activeApis.map((source) => (
              <ApiRow
                key={source.id}
                label={source.name}
                method={source.method}
                meta={source.description}
                onOpen={() => onOpenDetail({ kind: 'api', id: source.id })}
              />
            ))}
            {activeApis.length === 0 ? (
              <Empty description="该域下暂无接口" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : null}
          </div>
        </>
      ) : (
        <>
          <p className={cx('ds-hint')}>
            <span className={cx('ds-hint-text')}>外部 API 按域名分组：先新增域，再在域下登记接口。</span>
            <span className={cx('ds-hint-actions')}>
              <Button onClick={() => setDomainModal({ open: true, editing: null })} size="small" type="primary">
                新增域
              </Button>
            </span>
          </p>
          <div className={cx('ds-list')}>
            <Empty description="暂无外部 API 域" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </div>
        </>
      )}
      <ExternalApiDomainModal
        editing={domainModal.editing}
        onClose={() => setDomainModal({ open: false, editing: null })}
        onDelete={deleteDomain}
        onSave={saveDomain}
        open={domainModal.open}
      />
    </section>
  )
}
