import { useEffect, useState } from 'react'
import { Button, Input, InputNumber, message, Modal, Radio, Typography } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import { cx } from '../../utils'
import { DATABASE_MODE_LABEL, type DatabaseDataSource } from './catalog'

const { Text } = Typography

type Draft = {
  id: string
  name: string
  mode: DatabaseDataSource['mode']
  domain: string
  port?: number
  schema: string
  userName: string
  dbid: string
  password: string
}

/** 生成数据库连接草稿：端口默认 3306。 */
function createDraft(): Draft {
  return {
    id: '',
    name: '',
    mode: 'direct',
    domain: '',
    port: 3306,
    schema: '',
    userName: '',
    dbid: '',
    password: ''
  }
}

/** 从已有连接初始化编辑草稿；密码密文不回填输入框，只按已配置状态处理。 */
function draftFromSource(source: DatabaseDataSource): Draft {
  return {
    id: source.id,
    name: source.name,
    mode: source.mode,
    domain: source.domain,
    port: source.port,
    schema: source.schema,
    userName: source.userName,
    dbid: source.dbid,
    password: ''
  }
}

type Props = {
  open: boolean
  /** 编辑目标；为空表示新建连接。 */
  editing: DatabaseDataSource | null
  onClose: () => void
  onSave: (source: DatabaseDataSource) => void
  /** 编辑态提供删除入口（由父级做二次确认与级联处理）。 */
  onDelete?: (source: DatabaseDataSource) => void
  /** 演示环境的连接检测：按当前登记信息自检并提示结果。 */
  onValidate?: () => void
}

/**
 * 数据库连接设置：连接模式（模拟数据库/DBID/本地直连）+ 连接明细。
 * 按数据库常识区分可改性——连接模式决定参数结构、创建后锁定；
 * 地址/端口/Schema/账号/密码属于运维常态修改项，随时可改，改后重新检测连接即可；
 * 表结构由系统发现，不在此维护。模拟数据库是开发环境的模拟存储，无连接参数、无需检测。
 */
export default function DatabaseConnectionModal({
  open,
  editing,
  onClose,
  onSave,
  onDelete,
  onValidate
}: Props): JSX.Element {
  const [draft, setDraft] = useState<Draft>(createDraft)

  useEffect(() => {
    if (open) setDraft(editing ? draftFromSource(editing) : createDraft())
  }, [editing, open])

  /** 校验必填项；错误就地提示，不关闭弹窗。名称必填；本地直连要求完整连接参数，DBID 要求实例标识。 */
  const validate = (): string => {
    if (!draft.name.trim()) return '请输入数据源名称。'
    if (draft.mode === 'direct') {
      if (!draft.domain.trim()) return '请输入数据库地址。'
      if (!draft.port) return '请输入端口。'
      if (!draft.schema.trim()) return '请输入 Schema。'
      if (!draft.userName.trim()) return '请输入用户名。'
      const savedPassword = editing ? editing.hasPassword : false
      if (!savedPassword && !draft.password.trim()) return '本地直连必须填写密码。'
    }
    if (draft.mode === 'dbid' && !draft.dbid.trim()) return '请输入 DBID。'
    return ''
  }

  /** 保存连接信息，保留连接下已发现与已添加的表结构。 */
  const save = (): void => {
    const error = validate()
    if (error) {
      message.error(error)
      return
    }
    const previous = editing
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
  }

  return (
    <Modal
      cancelText="取消"
      className={cx('ds-editor-modal')}
      destroyOnClose
      footer={
        <div className={cx('ds-editor-footer')}>
          {editing ? (
            <>
              {/* 模拟数据库是本地模拟存储，不存在真实连接，无需检测；仅外部连接提供检测 */}
              {onValidate && editing.mode !== 'builtin' ? (
                <Button onClick={onValidate}>检测连接</Button>
              ) : null}
              {onDelete ? (
                <Button danger icon={<DeleteOutlined />} onClick={() => onDelete(editing)}>
                  删除连接
                </Button>
              ) : null}
            </>
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
      title={editing ? '连接设置' : '新建数据库连接'}
      width={520}
    >
      <div className={cx('ds-editor-form')}>
        <label>
          <span>
            <em aria-hidden="true" className={cx('ds-editor-required')}>*</em>
            名称
          </span>
          <Input
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            placeholder="例如：回检业务库"
            value={draft.name}
          />
        </label>
        {/* 连接模式创建后锁定：模式决定了下方参数结构，切换会让已填好的连接参数失效 */}
        <label>
          连接模式
          <Radio.Group
            disabled={Boolean(editing)}
            onChange={(event) => setDraft({ ...draft, mode: event.target.value })}
            options={(Object.keys(DATABASE_MODE_LABEL) as Array<DatabaseDataSource['mode']>).map(
              (mode) => ({ label: DATABASE_MODE_LABEL[mode], value: mode })
            )}
            value={draft.mode}
          />
        </label>
        {editing ? (
          draft.mode === 'builtin' ? (
            <Text type="secondary">
              模拟数据库仅用于开发环境跑通应用，无需任何连接配置；应用最终生成的数据库脚本将部署到行内
              MySQL 运行。
            </Text>
          ) : (
            <Text type="secondary">
              连接模式创建后不可更改；地址、端口、账号与密码可随时修改，改后请重新检测连接。
            </Text>
          )
        ) : draft.mode === 'builtin' ? (
          <Text type="secondary">
            模拟数据库仅用于开发环境跑通应用，无需任何连接配置；应用最终生成的数据库脚本将部署到行内
            MySQL 运行。
          </Text>
        ) : null}
        {draft.mode === 'builtin' ? null : (
          <>
            {/* 直连专属的连接参数在 DBID 模式仍展示备用，但只有直连时才是必填（红星随之显隐）。 */}
            <div className={cx('ds-editor-grid')}>
              <label>
                <span>
                  {draft.mode === 'direct' ? (
                    <em aria-hidden="true" className={cx('ds-editor-required')}>*</em>
                  ) : null}
                  数据库地址
                </span>
                <Input
                  onChange={(event) => setDraft({ ...draft, domain: event.target.value })}
                  placeholder="例如：127.0.0.1"
                  value={draft.domain}
                />
              </label>
              <label>
                <span>
                  {draft.mode === 'direct' ? (
                    <em aria-hidden="true" className={cx('ds-editor-required')}>*</em>
                  ) : null}
                  端口
                </span>
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
                <span>
                  {draft.mode === 'direct' ? (
                    <em aria-hidden="true" className={cx('ds-editor-required')}>*</em>
                  ) : null}
                  Schema
                </span>
                <Input
                  onChange={(event) => setDraft({ ...draft, schema: event.target.value })}
                  placeholder="请输入数据库 Schema"
                  value={draft.schema}
                />
              </label>
              <label>
                <span>
                  {draft.mode === 'direct' ? (
                    <em aria-hidden="true" className={cx('ds-editor-required')}>*</em>
                  ) : null}
                  用户名
                </span>
                <Input
                  onChange={(event) => setDraft({ ...draft, userName: event.target.value })}
                  placeholder="请输入数据库用户名"
                  value={draft.userName}
                />
              </label>
            </div>
            {draft.mode === 'dbid' ? (
              <label>
                <span>
                  <em aria-hidden="true" className={cx('ds-editor-required')}>*</em>
                  DBID
                </span>
                <Input
                  onChange={(event) => setDraft({ ...draft, dbid: event.target.value })}
                  placeholder="请输入 DBID"
                  value={draft.dbid}
                />
              </label>
            ) : null}
            {draft.mode === 'direct' ? (
              <label>
                <span>
                  <em aria-hidden="true" className={cx('ds-editor-required')}>*</em>
                  密码
                </span>
                {editing?.hasPassword ? '（留空保持不变）' : ''}
                <Input.Password
                  autoComplete="new-password"
                  onChange={(event) => setDraft({ ...draft, password: event.target.value })}
                  value={draft.password}
                />
              </label>
            ) : null}
            {draft.mode === 'dbid' ? (
              <Text type="secondary">DBID 当前只保存配置并执行静态校验，暂不发起连接检测。</Text>
            ) : null}
          </>
        )}
      </div>
    </Modal>
  )
}
