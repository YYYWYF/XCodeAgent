import { useEffect, useState } from 'react'
import { Checkbox, Modal, Typography } from 'antd'
import { cx } from '../../utils'
import { type DatabaseDataSource, type DataSource } from './catalog'

const { Text } = Typography

type Props = {
  open: boolean
  /** 目标数据库连接 id：添加动作只作用于当前 Tab 的连接。 */
  sourceId: string
  sources: DataSource[]
  onClose: () => void
  /** 添加完成：回传更新后的目录（所选表标记为已添加）。 */
  onImport: (sources: DataSource[]) => void
}

/**
 * 「添加数据表」弹窗：作用于当前数据库 Tab 的连接，勾选连接下的表添加进清单。
 * 添加是数据表进入清单的唯一入口，未添加的表不会出现在清单与绑定候选中；
 * 新建数据库连接由 Tab 栏的「＋」承担，与本弹窗无关。
 */
export default function DatabaseImportModal({
  open,
  sourceId,
  sources,
  onClose,
  onImport
}: Props): JSX.Element {
  const source = sources.find(
    (item): item is DatabaseDataSource => item.type === 'database' && item.id === sourceId
  )
  const [selectedTables, setSelectedTables] = useState<string[]>([])

  // 打开时清空勾选，每次添加都是全新选择。
  useEffect(() => {
    if (open) setSelectedTables([])
  }, [open])

  /** 确定添加：把勾选的表标记为已添加并回传目录。 */
  const confirmImport = (): void => {
    if (!source || !selectedTables.length) return
    onImport(
      sources.map((item) =>
        item.type === 'database' && item.id === source.id
          ? {
              ...item,
              tables: item.tables.map((table) =>
                selectedTables.includes(table.name) ? { ...table, imported: true } : table
              )
            }
          : item
      )
    )
  }

  return (
    <Modal
      cancelText="取消"
      className={cx('ds-editor-modal', 'ds-import-modal')}
      destroyOnClose
      okButtonProps={{ disabled: selectedTables.length === 0 }}
      okText={`添加 ${selectedTables.length} 张表`}
      onCancel={onClose}
      onOk={confirmImport}
      open={open}
      title={`添加数据表 · ${source?.name || ''}`}
      width={480}
    >
      <div className={cx('ds-import-step')}>
        <Text type="secondary">勾选「{source?.name}」中要添加的数据表</Text>
        <div className={cx('ds-import-tables')}>
          {(source?.tables || []).map((table) => {
            const alreadyImported = Boolean(table.imported)
            return (
              <label className={cx('ds-import-table', alreadyImported && 'imported')} key={table.name}>
                <Checkbox
                  checked={alreadyImported || selectedTables.includes(table.name)}
                  disabled={alreadyImported}
                  onChange={(event) =>
                    setSelectedTables((current) =>
                      event.target.checked
                        ? [...current, table.name]
                        : current.filter((name) => name !== table.name)
                    )
                  }
                />
                <span className={cx('ds-import-table-main')}>
                  <strong>{table.name}</strong>
                  <small>
                    {table.comment} · {table.columns.length} 字段
                  </small>
                </span>
                {alreadyImported ? <em>已添加</em> : null}
              </label>
            )
          })}
          {source && !source.tables.length ? (
            <Text type="secondary">
              {source.mode === 'builtin'
                ? '该模拟库暂无数据表。'
                : '该连接暂未发现数据表，请先完成连接检测。'}
            </Text>
          ) : null}
        </div>
      </div>
    </Modal>
  )
}
