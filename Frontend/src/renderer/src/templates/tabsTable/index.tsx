import React, { useRef, useState } from 'react'
import type { FormInstance } from 'antd'
import type { ActionType, ProColumns } from '@ant-design/pro-components'
import { ProForm, ProFormDateRangePicker, ProFormText, ProTable, ModalForm } from '@ant-design/pro-components'
import { Button, Form, Modal, Row, Col, Space, Tabs, Tag, message } from 'antd'
import { ReloadOutlined, DeleteOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'

// ============================================================
// 行数据类型：工单
// ============================================================
interface WorkOrderRow {
  id: string
  orderNo: string
  title: string
  applicant: string
  amount: number
  applyDate: string
  status: 'pending' | 'approved' | 'rejected'
}

type RowType = WorkOrderRow

const STATUS_OPTIONS: { label: string; value: WorkOrderRow['status']; color: string }[] = [
  { label: '待审批', value: 'pending', color: 'default' },
  { label: '已通过', value: 'approved', color: 'success' },
  { label: '已驳回', value: 'rejected', color: 'error' },
]
const statusMap = new Map(STATUS_OPTIONS.map((s) => [s.value, s]))

// ============================================================
// Tab 定义
// ============================================================
interface TabDef {
  key: string
  label: string
  status: WorkOrderRow['status'] | 'all'
  columns: { title: string; dataIndex: keyof WorkOrderRow; width?: number; valueType?: string }[]
  dateFields: string[]
  readonlyFields: string[]
}

const TAB_DEFS: TabDef[] = [
  {
    key: 'pending',
    label: '待审批',
    status: 'pending',
    columns: [
      { title: '编号', dataIndex: 'orderNo', width: 140 },
      { title: '标题', dataIndex: 'title', width: 200 },
      { title: '申请人', dataIndex: 'applicant', width: 100 },
      { title: '金额', dataIndex: 'amount', width: 120, valueType: 'money' },
      { title: '申请日期', dataIndex: 'applyDate', width: 130, valueType: 'date' },
      { title: '状态', dataIndex: 'status', width: 100 },
    ],
    dateFields: ['applyDate'],
    readonlyFields: ['orderNo'],
  },
  {
    key: 'approved',
    label: '已通过',
    status: 'approved',
    columns: [
      { title: '编号', dataIndex: 'orderNo', width: 140 },
      { title: '标题', dataIndex: 'title', width: 200 },
      { title: '申请人', dataIndex: 'applicant', width: 100 },
      { title: '金额', dataIndex: 'amount', width: 120, valueType: 'money' },
      { title: '申请日期', dataIndex: 'applyDate', width: 130, valueType: 'date' },
      { title: '状态', dataIndex: 'status', width: 100 },
    ],
    dateFields: ['applyDate'],
    readonlyFields: ['orderNo'],
  },
  {
    key: 'rejected',
    label: '已驳回',
    status: 'rejected',
    columns: [
      { title: '编号', dataIndex: 'orderNo', width: 140 },
      { title: '标题', dataIndex: 'title', width: 200 },
      { title: '申请人', dataIndex: 'applicant', width: 100 },
      { title: '金额', dataIndex: 'amount', width: 120, valueType: 'money' },
      { title: '申请日期', dataIndex: 'applyDate', width: 130, valueType: 'date' },
      { title: '状态', dataIndex: 'status', width: 100 },
    ],
    dateFields: ['applyDate'],
    readonlyFields: ['orderNo'],
  },
]

// ============================================================
// 内存 mock 数据（全量，按 Tab status 过滤）
// ============================================================
let MOCK_DATA: WorkOrderRow[] = [
  { id: '1', orderNo: 'GD-2026-0001', title: '北京客户拜访差旅申请', applicant: '张伟', amount: 3580.5, applyDate: '2026-07-12', status: 'approved' },
  { id: '2', orderNo: 'GD-2026-0002', title: '部门季度团建费用申请', applicant: '李娜', amount: 8200, applyDate: '2026-07-15', status: 'pending' },
  { id: '3', orderNo: 'GD-2026-0003', title: '研发服务器采购申请', applicant: '王强', amount: 15600.8, applyDate: '2026-07-18', status: 'pending' },
  { id: '4', orderNo: 'GD-2026-0004', title: '市场推广物料制作申请', applicant: '刘洋', amount: 4300, applyDate: '2026-07-20', status: 'rejected' },
  { id: '5', orderNo: 'GD-2026-0005', title: '上海展会参展费用申请', applicant: '陈静', amount: 22800, applyDate: '2026-07-22', status: 'approved' },
  { id: '6', orderNo: 'GD-2026-0006', title: '员工培训课程费申请', applicant: '赵磊', amount: 6800, applyDate: '2026-07-25', status: 'pending' },
  { id: '7', orderNo: 'GD-2026-0007', title: '办公电脑采购申请', applicant: '孙芳', amount: 12400, applyDate: '2026-07-28', status: 'pending' },
  { id: '8', orderNo: 'GD-2026-0008', title: '客户接待餐费申请', applicant: '周杰', amount: 1280, applyDate: '2026-07-30', status: 'approved' },
  { id: '9', orderNo: 'GD-2026-0009', title: '打印机维修费申请', applicant: '吴敏', amount: 860, applyDate: '2026-08-01', status: 'rejected' },
  { id: '10', orderNo: 'GD-2026-0010', title: '广州分公司差旅申请', applicant: '郑华', amount: 5430.5, applyDate: '2026-08-03', status: 'pending' },
  { id: '11', orderNo: 'GD-2026-0011', title: '年度审计咨询费申请', applicant: '林涛', amount: 38000, applyDate: '2026-08-05', status: 'approved' },
  { id: '12', orderNo: 'GD-2026-0012', title: '团建活动场地租赁申请', applicant: '何丽', amount: 4500, applyDate: '2026-08-06', status: 'rejected' },
]

// mock 接口：操作内存数组
const updateTabRecord = (payload: Partial<WorkOrderRow> & { id: string }): { success: boolean } => {
  const idx = MOCK_DATA.findIndex((r) => r.id === payload.id)
  if (idx === -1) return { success: false }
  MOCK_DATA[idx] = { ...MOCK_DATA[idx], ...payload }
  return { success: true }
}

const deleteTabRecord = (id: string): { success: boolean } => {
  const before = MOCK_DATA.length
  MOCK_DATA = MOCK_DATA.filter((r) => r.id !== id)
  return { success: MOCK_DATA.length < before }
}

// ============================================================
// 弹窗与主组件
// ============================================================
type ModalType = 'detail' | 'edit' | null

type DetailModalProps = { open: boolean; record?: WorkOrderRow; tab: TabDef; onClose: () => void }

const DetailModal: React.FC<DetailModalProps> = ({ open, record, tab, onClose }) => {
  return (
    <ModalForm title="查看详情" open={open} onOpenChange={(v) => { if (!v) onClose() }}
      modalProps={{ destroyOnClose: true, width: 640 }}
      submitter={{ resetButtonProps: { style: { display: 'none' } }, submitButtonProps: { style: { display: 'none' } } }}
      initialValues={record ? { ...record } : undefined}
    >
      <Row gutter={[16, 0]}>
        {tab.columns.map((f) => (
          <Col span={12} key={f.dataIndex}>
            <ProFormText name={f.dataIndex} label={f.title} disabled fieldProps={{ style: { width: '100%' } }} />
          </Col>
        ))}
      </Row>
      <div style={{ textAlign: 'right', marginTop: 16 }}><Button onClick={onClose}>关闭</Button></div>
    </ModalForm>
  )
}

type EditModalProps = { open: boolean; record?: WorkOrderRow; tab: TabDef; onClose: () => void; onSaved: () => void }

const EditModal: React.FC<EditModalProps> = ({ open, record, tab, onClose, onSaved }) => {
  return (
    <ModalForm title="修改" open={open} onOpenChange={(v) => { if (!v) onClose() }}
      modalProps={{ destroyOnClose: true, width: 640 }}
      initialValues={record ? { ...record } : undefined}
      submitter={{ searchConfig: { submitText: '确定', resetText: '取消' } }}
      onFinish={async (values) => {
        try {
          const res = await updateTabRecord({ id: record?.id as string, ...values })
          if (!res.success) { message.error('修改失败，记录不存在'); return false }
          message.success('修改成功')
          onSaved()
          return true
        } catch { message.error('修改失败，请稍后重试'); return false }
      }}
    >
      <Row gutter={[16, 0]}>
        {tab.columns.map((f) => (
          <Col span={12} key={f.dataIndex}>
            <ProFormText
              name={f.dataIndex} label={f.title} placeholder="请输入内容"
              disabled={tab.readonlyFields.includes(f.dataIndex)}
              fieldProps={{ style: { width: '100%' } }}
            />
          </Col>
        ))}
      </Row>
    </ModalForm>
  )
}

const TabsTable: React.FC = () => {
  const formRef = useRef<FormInstance>(null)
  const tableRef = useRef<ActionType>(null)
  const [activeKey, setActiveKey] = useState<string>(TAB_DEFS[0]?.key ?? '')
  const [refreshing, setRefreshing] = useState(false)
  const [selection, setSelection] = useState<{ keys: React.Key[]; rows: WorkOrderRow[] }>({ keys: [], rows: [] })
  const [modal, setModal] = useState<{ type: ModalType; record?: WorkOrderRow }>({ type: null })

  const activeTab = TAB_DEFS.find((t) => t.key === activeKey) ?? TAB_DEFS[0]

  const clearSelection = () => setSelection({ keys: [], rows: [] })
  const closeModal = () => setModal({ type: null })

  const handleDelete = (record: WorkOrderRow) => {
    const recordId = record.id
    const displayName = record.title ?? recordId

    Modal.confirm({
      title: '确认删除', content: `确定要删除「${displayName}」的记录吗？删除后不可恢复。`,
      okText: '确认', cancelText: '取消', okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const res = await deleteTabRecord(recordId)
          if (!res.success) { message.error('删除失败，记录不存在'); return }
          message.success('删除成功')
          tableRef.current?.reload()
        } catch { message.error('删除失败，请稍后重试') }
      },
    })
  }

  const handleBatchDelete = () => {
    if (selection.keys.length === 0) return
    Modal.confirm({
      title: '批量删除', content: '确认删除吗？删除后不可恢复。', okText: '确定删除', cancelText: '取消', okButtonProps: { danger: true },
      onOk: async () => {
        try {
          for (const id of selection.keys) await deleteTabRecord(String(id))
          message.success(`成功删除 ${selection.keys.length} 条记录`)
          clearSelection()
          tableRef.current?.reload()
        } catch { message.error('删除失败，请稍后重试') }
      },
    })
  }

  const refresh = async () => {
    setRefreshing(true)
    try {
      formRef.current?.resetFields()
      clearSelection()
      tableRef.current?.reload()
    } finally { setRefreshing(false) }
  }

  const tabsChange = (key: string) => {
    setActiveKey(key)
    clearSelection()
    formRef.current?.resetFields()
    // Tab 切换后等 activeKey 更新再 reload，让 request 读到新 key
    setTimeout(() => tableRef.current?.reloadAndRest?.(), 0)
  }

  const handleQuery = async () => {
    try { await formRef.current?.validateFields() } catch { /* 校验失败仍用当前值查询 */ }
    tableRef.current?.reload()
  }

  const handleReset = () => {
    formRef.current?.resetFields()
    tableRef.current?.reload()
  }

  // 根据当前 Tab 生成 ProTable 列配置
  const columns: ProColumns<RowType>[] = [
    ...activeTab.columns.map((f) => ({
      title: f.title,
      dataIndex: f.dataIndex,
      key: f.dataIndex,
      width: f.width ?? 120,
      valueType: f.valueType as ProColumns<RowType>['valueType'],
      ellipsis: true,
      ...(f.dataIndex === 'status'
        ? {
          render: (_: unknown, record: WorkOrderRow) => {
            const s = statusMap.get(record.status)
            return s ? <Tag color={s.color}>{s.label}</Tag> : record.status
          },
        }
        : {}),
      ...(f.dataIndex === 'amount'
        ? { sorter: (a: WorkOrderRow, b: WorkOrderRow) => a.amount - b.amount, align: 'right' as const }
        : {}),
      ...(f.dataIndex === 'applyDate'
        ? { sorter: (a: WorkOrderRow, b: WorkOrderRow) => dayjs(a.applyDate).valueOf() - dayjs(b.applyDate).valueOf() }
        : {}),
    })),
    {
      title: '操作', key: 'operation', width: 220, fixed: 'right',
      render: (_, record) => (
        <>
          <a key="detail" onClick={() => setModal({ type: 'detail', record })}>查看详情</a>
          <a key="edit" style={{ marginLeft: 8 }} onClick={() => setModal({ type: 'edit', record })}>修改</a>
          <a key="delete" style={{ marginLeft: 8 }} onClick={() => handleDelete(record)}>删除</a>
        </>
      ),
    },
  ]

  return (
    <div style={{ padding: 24, overflow: 'hidden', minWidth: 0 }}>
      <Tabs activeKey={activeKey} onChange={tabsChange}
        items={TAB_DEFS.map((t) => ({ key: t.key, label: t.label }))}
      />

      <ProForm formRef={formRef} layout="horizontal" submitter={false} style={{ marginBottom: 16 }} key={activeKey}>
        <Row gutter={[16, 16]}>
          <Col span={6}>
            <ProFormText name="orderNo" label="编号" placeholder="请输入编号" />
          </Col>
          <Col span={6}>
            <ProFormText name="title" label="标题" placeholder="请输入标题" />
          </Col>
          <Col span={6}>
            <ProFormText name="applicant" label="申请人" placeholder="请输入申请人" />
          </Col>
          <Col span={6}>
            <ProFormDateRangePicker name="applyDate" label="申请日期" />
          </Col>
          <Col flex="none">
            <Form.Item label=" " colon={false}>
              <Space>
                <Button type="primary" onClick={handleQuery}>查询</Button>
                <Button onClick={handleReset}>重置</Button>
              </Space>
            </Form.Item>
          </Col>
        </Row>
      </ProForm>

      <ProTable<RowType>
        actionRef={tableRef}
        columns={columns}
        rowKey="id"
        params={{ activeKey }}
        request={async () => {
          const tab = TAB_DEFS.find((t) => t.key === activeKey)
          const status = tab?.status
          const list = status === 'all' ? MOCK_DATA : MOCK_DATA.filter((r) => r.status === status)
          return {
            data: list,
            success: true,
            total: list.length,
          };
        }}
        search={false}
        scroll={{ x: 'max-content', y: 300 }}
        pagination={{ defaultPageSize: 10, showSizeChanger: true, showQuickJumper: true, pageSizeOptions: ['10', '20', '50', '100'] }}
        rowSelection={{ selectedRowKeys: selection.keys, onChange: (keys, rows) => setSelection({ keys, rows: rows as WorkOrderRow[] }) }}
        tableAlertRender={({ selectedRowKeys: selKeys }) =>
          selKeys.length > 0 ? (<Space><span>已选择 {selKeys.length} 项</span><a onClick={clearSelection}>取消选择</a></Space>) : false
        }
        tableAlertOptionRender={false}
        options={{ setting: true, reload: false, density: false, fullScreen: false }}
        toolBarRender={() => [
          <Button key="batchDelete" icon={<DeleteOutlined />} danger disabled={selection.keys.length === 0} onClick={handleBatchDelete}>批量删除</Button>,
          <Button key="refresh" icon={<ReloadOutlined />} loading={refreshing} onClick={refresh}>刷新</Button>,
        ]}
      />

      <DetailModal open={modal.type === 'detail'} record={modal.record} tab={activeTab} onClose={closeModal} />
      <EditModal open={modal.type === 'edit'} record={modal.record} tab={activeTab} onClose={closeModal}
        onSaved={() => { closeModal(); tableRef.current?.reload() }} />
    </div>
  )
}

export default TabsTable
