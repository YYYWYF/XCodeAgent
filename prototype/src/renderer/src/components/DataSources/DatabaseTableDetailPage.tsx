import { Button, message, Modal, Tag } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import { cx } from '../../utils'
import { DATABASE_MODE_LABEL, importedTables, useDataSources, type ImportedDatabaseTable } from './catalog'
import FieldList from './FieldList'

type Props = {
  /** 维护目标：数据来源清单中的一张已添加表。 */
  target: { kind: 'table'; sourceId: string; name: string }
  onBack: () => void
}

/**
 * 数据表维护页：衔接在数据来源抽屉右侧的详情层。表结构由系统发现、只读呈现
 * （所属连接 + 字段清单），维护动作仅「移除」。数据直接读写共享目录。
 */
export default function DatabaseTableDetailPage({ target, onBack }: Props): JSX.Element {
  const [sources, saveSources] = useDataSources()
  const item: ImportedDatabaseTable | undefined = importedTables(sources).find(
    (entry) => entry.sourceId === target.sourceId && entry.table.name === target.name
  )

  /** 移除前二次确认：有绑定引用时绑定面板会提示重新绑定。 */
  const remove = (): void => {
    if (!item) return
    Modal.confirm({
      centered: true,
      cancelText: '取消',
      content: `移除后「${item.table.name}」不再出现在数据来源清单；如已有应用API绑定该表，将提示重新绑定。`,
      okButtonProps: { danger: true },
      okText: '移除',
      onOk: () => {
        saveSources(
          sources.map((source) =>
            source.type === 'database' && source.id === item.sourceId
              ? {
                  ...source,
                  tables: source.tables.map((table) =>
                    table.name === item.table.name ? { ...table, imported: false } : table
                  )
                }
              : source
          )
        )
        message.success('已从数据来源清单移除')
        onBack()
      },
      title: `移除数据表「${item.table.name}」？`
    })
  }

  if (!item) {
    // 兜底：连接或表刚被移除时给出空态，避免白屏。
    return (
      <div className={cx('ds-detail-page')}>
        <div className={cx('ds-detail-form')}>
          <p className={cx('ds-field-empty')}>该数据表已不在来源清单中。</p>
        </div>
        <footer className={cx('ds-detail-footer')}>
          <span className={cx('ds-detail-footer-spacer')} />
          <Button onClick={onBack}>返回</Button>
        </footer>
      </div>
    )
  }

  return (
    <div className={cx('ds-detail-page')}>
      <div className={cx('ds-detail-form')}>
        <div className={cx('ds-detail-meta')}>
          <span className={cx('ds-detail-source')}>
            <strong>{item.sourceName}</strong>
            <Tag>{DATABASE_MODE_LABEL[item.sourceMode]}</Tag>
          </span>
          <span className={cx('ds-detail-sub')}>表结构由系统发现，此处只读；绑定映射按字段粒度引用。</span>
        </div>
        <div className={cx('ds-param-head')}>字段（{item.table.columns.length}）</div>
        <FieldList emptyText="该表暂无字段信息" fields={item.table.columns} />
      </div>
      <footer className={cx('ds-detail-footer')}>
        <Button danger icon={<DeleteOutlined />} onClick={remove}>
          移除
        </Button>
        <span className={cx('ds-detail-footer-spacer')} />
        <Button onClick={onBack}>返回</Button>
      </footer>
    </div>
  )
}
