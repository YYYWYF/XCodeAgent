import { useEffect, useState } from 'react'
import { Input, InputNumber, message, Modal, Radio, Typography } from 'antd'
import { cx } from '../../utils'
import { type DataSource } from './catalog'

const { Text } = Typography

type Draft = {
  id: string
  type: 'database' | 'external_service'
  name: string
  mode: 'builtin' | 'dbid' | 'direct'
  domain: string
  port?: number
  schema: string
  userName: string
  dbid: string
  password: string
  baseUrl: string
}

/** 生成数据库草稿：端口默认 3306，与原工程一致。 */
function createDatabaseDraft(): Draft {
  return {
    id: '',
    type: 'database',
    name: '',
    mode: 'direct',
    domain: '',
    port: 3306,
    schema: '',
    userName: '',
    dbid: '',
    password: '',
    baseUrl: ''
  }
}

/** 生成外部 API 草稿。 */
function createApiDraft(): Draft {
  return {
    id: '',
    type: 'external_service',
    name: '',
    mode: 'direct',
    domain: '',
    port: undefined,
    schema: '',
    userName: '',
    dbid: '',
    password: '',
    baseUrl: ''
  }
}

/** 从已有连接初始化编辑草稿；密码密文不回填输入框，只按已配置状态处理。 */
function draftFromSource(source: DataSource): Draft {
  const base: Draft = {
    id: source.id,
    type: source.type,
    name: source.name,
    mode: 'direct',
    domain: '',
    port: undefined,
    schema: '',
    userName: '',
    dbid: '',
    password: '',
    baseUrl: ''
  }
  if (source.type === 'database') {
    return {
      ...base,
      mode: source.mode,
      domain: source.domain,
      port: source.port,
      schema: source.schema,
      userName: source.userName,
      dbid: source.dbid
    }
  }
  return { ...base, baseUrl: source.baseUrl }
}

type Props = {
  open: boolean
  editing: DataSource | null
  creatingType: Draft['type'] | null
  onClose: () => void
  onSave: (source: DataSource) => void
}

/** 数据库连接复刻原工程字段：连接模式（内置数据库/DBID/本地直连）+ 连接明细，编辑已有连接时锁定连接模式；外部 API 只登记站点 URL。 */
export default function DataSourceEditorModal({
  open,
  editing,
  creatingType,
  onClose,
  onSave
}: Props): JSX.Element {
  const [draft, setDraft] = useState<Draft>(createDatabaseDraft)

  useEffect(() => {
    if (open)
      setDraft(
        editing
          ? draftFromSource(editing)
          : creatingType === 'external_service'
            ? createApiDraft()
            : createDatabaseDraft()
      )
  }, [creatingType, editing, open])

  /** 校验必填与模式专属字段；错误就地提示，不关闭弹窗。 */
  const validate = (): string => {
    if (draft.type === 'database') {
      if (!draft.name.trim()) return '请输入数据源名称。'
      const savedPassword = editing?.type === 'database' ? editing.hasPassword : false
      if (draft.mode === 'direct' && !savedPassword && !draft.password.trim())
        return '本地直连必须填写密码。'
      return ''
    }
    if (!draft.name.trim()) return '请输入域名配置名称。'
    if (!draft.baseUrl.trim()) return '请输入 Base URL 或域名。'
    return ''
  }

  /** 保存连接信息，同时保留系统已发现的内部结构元数据。 */
  const save = (): void => {
    const error = validate()
    if (error) {
      message.error(error)
      return
    }
    if (draft.type === 'database') {
      const previous = editing?.type === 'database' ? editing : undefined
      onSave({
        type: 'database',
        id: draft.id || `db-${Date.now()}`,
        name: draft.name.trim(),
        mode: draft.mode,
        domain: draft.mode === 'builtin' ? '' : draft.domain.trim(),
        port: draft.mode === 'builtin' ? undefined : draft.port,
        schema: draft.mode === 'builtin' ? '' : draft.schema.trim(),
        userName: draft.mode === 'builtin' ? '' : draft.userName.trim(),
        dbid: draft.mode === 'dbid' ? draft.dbid.trim() : '',
        hasPassword: draft.password.trim() ? true : previous?.hasPassword || false,
        tables: previous?.tables || []
      })
      return
    }
    const previous = editing?.type === 'external_service' ? editing : undefined
    onSave({
      type: 'external_service',
      id: draft.id || `api-${Date.now()}`,
      name: draft.name.trim(),
      baseUrl: draft.baseUrl.trim(),
      directories: previous?.directories || []
    })
  }

  return (
    <Modal
      cancelText="取消"
      className={cx('ds-editor-modal')}
      destroyOnClose
      okText="保存"
      onCancel={onClose}
      onOk={save}
      open={open}
      title={
        editing ? `编辑${draft.type === 'database' ? '数据库' : '外部 API 域名'}` : '新增数据源'
      }
      width={520}
    >
      <div className={cx('ds-editor-form')}>
        <label>
          名称
          <Input
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            placeholder={draft.type === 'database' ? '例如：业务数据库' : '例如：用户中心 API'}
            value={draft.name}
          />
        </label>
        {draft.type === 'database' ? (
          <>
            <label>
              连接模式
              {/* 连接模式创建后锁定：模式决定了下方参数结构，切换会让已填好的连接参数失效 */}
              <Radio.Group
                disabled={Boolean(editing)}
                onChange={(event) => setDraft({ ...draft, mode: event.target.value })}
                options={[
                  { label: '内置数据库', value: 'builtin' },
                  { label: 'DBID', value: 'dbid' },
                  { label: '本地直连', value: 'direct' }
                ]}
                value={draft.mode}
              />
            </label>
            {editing ? (
              <Text type="secondary">连接模式在创建后不可更改。</Text>
            ) : draft.mode === 'builtin' ? (
              <Text type="secondary">内置数据库不需要填写外部连接信息。</Text>
            ) : null}
            {draft.mode === 'builtin' ? null : (
              <>
                <div className={cx('ds-editor-grid')}>
                  <label>
                    数据库地址
                    <Input
                      onChange={(event) => setDraft({ ...draft, domain: event.target.value })}
                      placeholder="例如：127.0.0.1"
                      value={draft.domain}
                    />
                  </label>
                  <label>
                    端口
                    <InputNumber
                      className={cx('ds-editor-port')}
                      max={65535}
                      min={1}
                      onChange={(value) =>
                        setDraft({ ...draft, port: value ? Number(value) : undefined })
                      }
                      placeholder="例如：3306"
                      precision={0}
                      value={draft.port}
                    />
                  </label>
                  <label>
                    Schema
                    <Input
                      onChange={(event) => setDraft({ ...draft, schema: event.target.value })}
                      placeholder="请输入数据库 Schema"
                      value={draft.schema}
                    />
                  </label>
                  <label>
                    用户名
                    <Input
                      onChange={(event) => setDraft({ ...draft, userName: event.target.value })}
                      placeholder="请输入数据库用户名"
                      value={draft.userName}
                    />
                  </label>
                </div>
                {draft.mode === 'dbid' ? (
                  <label>
                    DBID
                    <Input
                      onChange={(event) => setDraft({ ...draft, dbid: event.target.value })}
                      placeholder="请输入 DBID"
                      value={draft.dbid}
                    />
                  </label>
                ) : null}
                {draft.mode === 'direct' ? (
                  <label>
                    密码
                    {editing?.type === 'database' && editing.hasPassword ? '（留空保持不变）' : ''}
                    <Input.Password
                      autoComplete="new-password"
                      onChange={(event) => setDraft({ ...draft, password: event.target.value })}
                      value={draft.password}
                    />
                  </label>
                ) : null}
                {draft.mode === 'dbid' ? (
                  <Text type="secondary">
                    DBID 当前只保存配置并执行静态校验，暂不发起连接检测。
                  </Text>
                ) : null}
              </>
            )}
          </>
        ) : (
          <label>
            域名 / Base URL
            <Input
              onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })}
              placeholder="api.example.com 或 https://api.example.com"
              value={draft.baseUrl}
            />
          </label>
        )}
      </div>
    </Modal>
  )
}
