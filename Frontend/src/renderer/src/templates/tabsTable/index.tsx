import React, { useRef, useState } from 'react'
import type { FormInstance } from 'antd'
import type { ActionType, ProColumns } from '@ant-design/pro-components'
import { ProForm, ProFormDateRangePicker, ProFormSelect, ProFormText, ProTable, ModalForm } from '@ant-design/pro-components'
import { Button, Form, Modal, Row, Col, Space, Tabs, message } from 'antd'
import { ReloadOutlined, DeleteOutlined } from '@ant-design/icons'
import type { ManagementItem, InstitutionParam, UserItem } from './types'
import {
  fetchManagementList, fetchInstitutionList, fetchUserList,
  updateManagement, updateInstitution, updateUser,
  deleteManagement, deleteInstitution, deleteUser,
} from './api'

type TabKey = 'management' | 'institution' | 'users'
type ModalType = 'detail' | 'edit' | null

/** 三个实体类型的联合，用于操作列和 modal 共享 */
type DataRecord = ManagementItem | InstitutionParam | UserItem

/** 清理后的查询参数 */
type CleanedParams = Record<string, string>

// ==================== 字段元信息（按 tab 独立，不向上转联合） ====================
type FieldMeta<Row> = {
  title: string
  dataIndex: keyof Row & string
  editable: boolean
  type: 'text' | 'select'
}

const managementFieldDefs: FieldMeta<ManagementItem>[] = [
  { title: '事项编号', dataIndex: 'itemNo', editable: false, type: 'text' },
  { title: '事项名称', dataIndex: 'itemName', editable: true, type: 'text' },
  { title: '所属部门', dataIndex: 'department', editable: true, type: 'text' },
  { title: '负责人', dataIndex: 'owner', editable: true, type: 'text' },
  { title: '状态', dataIndex: 'status', editable: true, type: 'select' },
]

const institutionFieldDefs: FieldMeta<InstitutionParam>[] = [
  { title: '参数编码', dataIndex: 'paramCode', editable: false, type: 'text' },
  { title: '参数名称', dataIndex: 'paramName', editable: true, type: 'text' },
  { title: '参数值', dataIndex: 'paramValue', editable: true, type: 'text' },
  { title: '生效日期', dataIndex: 'effectiveDate', editable: true, type: 'text' },
  { title: '备注', dataIndex: 'remark', editable: true, type: 'text' },
]

const userFieldDefs: FieldMeta<UserItem>[] = [
  { title: '用户名', dataIndex: 'username', editable: false, type: 'text' },
  { title: '姓名', dataIndex: 'realName', editable: true, type: 'text' },
  { title: '角色', dataIndex: 'role', editable: true, type: 'select' },
  { title: '邮箱', dataIndex: 'email', editable: true, type: 'text' },
  { title: '创建时间', dataIndex: 'createdAt', editable: false, type: 'text' },
]

// ==================== 通用详情弹窗（泛型保持 dataIndex 精确） ====================
type DetailModalProps<Row> = {
  open: boolean
  record?: Row
  tab: TabKey
  onClose: () => void
}

function DetailModal<Row extends DataRecord>({ open, record, tab, onClose }: DetailModalProps<Row>) {
  const fields = (tab === 'management' ? managementFieldDefs : tab === 'institution' ? institutionFieldDefs : userFieldDefs) as FieldMeta<Row>[]
  return (
    <ModalForm title="查看详情" open={open} onOpenChange={(v) => { if (!v) onClose() }}
      modalProps={{ destroyOnClose: true, width: 640 }}
      submitter={{ resetButtonProps: { style: { display: 'none' } }, submitButtonProps: { style: { display: 'none' } } }}
      initialValues={record}
    >
      <Row gutter={[16, 0]}>
        {fields.map((f) => (
          <Col span={12} key={f.dataIndex}>
            <ProFormText name={f.dataIndex} label={f.title} disabled fieldProps={{ style: { width: '100%' } }} />
          </Col>
        ))}
      </Row>
      <div style={{ textAlign: 'right', marginTop: 16 }}><Button onClick={onClose}>关闭</Button></div>
    </ModalForm>
  )
}

// ==================== 通用修改弹窗 ====================
type EditModalProps<Row> = {
  open: boolean
  record?: Row
  tab: TabKey
  onClose: () => void
  onSaved: () => void
}

function EditModal<Row extends DataRecord>({ open, record, tab, onClose, onSaved }: EditModalProps<Row>) {
  const fields = (tab === 'management' ? managementFieldDefs : tab === 'institution' ? institutionFieldDefs : userFieldDefs) as FieldMeta<Row>[]
  const updateFn = tab === 'management' ? updateManagement : tab === 'institution' ? updateInstitution : updateUser
  return (
    <ModalForm title="修改" open={open} onOpenChange={(v) => { if (!v) onClose() }}
      modalProps={{ destroyOnClose: true, width: 640 }}
      initialValues={record}
      submitter={{ searchConfig: { submitText: '确定', resetText: '取消' } }}
      onFinish={async (values) => {
        try {
          const res = await updateFn({ id: record?.id ?? '', ...values })
          if (!res.success) { message.error('修改失败，记录不存在'); return false }
          message.success('修改成功')
          onSaved()
          return true
        } catch { message.error('修改失败，请稍后重试'); return false }
      }}
    >
      <Row gutter={[16, 0]}>
        {fields.map((f) => {
          const key = f.dataIndex as string
          if (key === 'status' && tab === 'management') {
            return (
              <Col span={12} key={f.dataIndex}>
                <ProFormSelect name={f.dataIndex} label={f.title} fieldProps={{ style: { width: '100%' } }}
                  options={[{ label: '待处理', value: 'pending' }, { label: '处理中', value: 'in_progress' }, { label: '已完成', value: 'completed' }]} />
              </Col>
            )
          }
          if (key === 'role' && tab === 'users') {
            return (
              <Col span={12} key={f.dataIndex}>
                <ProFormSelect name={f.dataIndex} label={f.title} fieldProps={{ style: { width: '100%' } }}
                  options={[{ label: '管理员', value: '管理员' }, { label: '普通用户', value: '普通用户' }, { label: '审计员', value: '审计员' }]} />
              </Col>
            )
          }
          return (
            <Col span={12} key={f.dataIndex}>
              <ProFormText name={f.dataIndex} label={f.title} placeholder="请输入内容"
                disabled={!f.editable} fieldProps={{ style: { width: '100%' } }} />
            </Col>
          )
        })}
      </Row>
    </ModalForm>
  )
}

// ==================== 列定义 ====================
const managementColumns: ProColumns<ManagementItem>[] = [
  { title: '事项编号', dataIndex: 'itemNo', width: 140 },
  { title: '事项名称', dataIndex: 'itemName', width: 180, ellipsis: true },
  { title: '所属部门', dataIndex: 'department', width: 120 },
  { title: '负责人', dataIndex: 'owner', width: 100 },
  { title: '状态', dataIndex: 'status', width: 100, valueEnum: { pending: { text: '待处理', status: 'Default' }, in_progress: { text: '处理中', status: 'Processing' }, completed: { text: '已完成', status: 'Success' } } },
]

const institutionColumns: ProColumns<InstitutionParam>[] = [
  { title: '参数编码', dataIndex: 'paramCode', width: 140 },
  { title: '参数名称', dataIndex: 'paramName', width: 180 },
  { title: '参数值', dataIndex: 'paramValue', width: 120 },
  { title: '生效日期', dataIndex: 'effectiveDate', width: 130 },
  { title: '备注', dataIndex: 'remark', width: 200, ellipsis: true },
]

const userColumns: ProColumns<UserItem>[] = [
  { title: '用户名', dataIndex: 'username', width: 120 },
  { title: '姓名', dataIndex: 'realName', width: 100 },
  { title: '角色', dataIndex: 'role', width: 100, valueEnum: { 管理员: { text: '管理员', status: 'Error' }, 普通用户: { text: '普通用户', status: 'Default' }, 审计员: { text: '审计员', status: 'Processing' } } },
  { title: '邮箱', dataIndex: 'email', width: 200, ellipsis: true },
  { title: '创建时间', dataIndex: 'createdAt', width: 160 },
]

const actionColumn: ProColumns<DataRecord> = {
  title: '操作', key: 'operation', width: 220, fixed: 'right',
  render: (_, record) => (
    <>
      <a key="detail" onClick={() => modalRef.current?.('detail', record)}>查看详情</a>
      <a key="edit" style={{ marginLeft: 8 }} onClick={() => modalRef.current?.('edit', record)}>修改</a>
      <a key="delete" style={{ marginLeft: 8 }} onClick={() => deleteRef.current?.(record)}>删除</a>
    </>
  ),
}

const columnsMap: Record<TabKey, ProColumns<ManagementItem>[] | ProColumns<InstitutionParam>[] | ProColumns<UserItem>[]> = {
  management: [...managementColumns, actionColumn as ProColumns<ManagementItem>],
  institution: [...institutionColumns, actionColumn as ProColumns<InstitutionParam>],
  users: [...userColumns, actionColumn as ProColumns<UserItem>],
}

// ==================== 请求函数表 ====================
type FetchFn = (params: Record<string, unknown>) => Promise<{ data: DataRecord[]; success: boolean; total: number }>

const requestFn: Record<TabKey, FetchFn> = {
  management: fetchManagementList as unknown as FetchFn,
  institution: fetchInstitutionList as unknown as FetchFn,
  users: fetchUserList as unknown as FetchFn,
}

// ==================== 全局 ref ====================
type ModalOpener = (type: Exclude<ModalType, null>, record?: DataRecord) => void
type DeleteHandler = (record: DataRecord) => void

const modalRef: { current: ModalOpener | null } = { current: null }
const deleteRef: { current: DeleteHandler | null } = { current: null }

/** 清洗查询表单值：去掉空值，dayjs 转为字符串，日期范围拆为 start/end */
const cleanFormValues = (values: Record<string, unknown>): CleanedParams => {
  const cleaned: CleanedParams = {}
  for (const [key, val] of Object.entries(values)) {
    if (val === undefined || val === null || val === '') continue
    if (Array.isArray(val) && val.length === 2 && val[0] && typeof (val[0] as Record<string, unknown>).format === 'function') {
      cleaned[key + 'Start'] = (val[0] as { format: (f: string) => string }).format('YYYY-MM-DD')
      cleaned[key + 'End'] = (val[1] as { format: (f: string) => string }).format('YYYY-MM-DD 23:59:59')
      continue
    }
    if (typeof val === 'object' && val !== null && typeof (val as { format?: unknown }).format === 'function') {
      cleaned[key] = (val as { format: (f: string) => string }).format('YYYY-MM-DD')
    } else {
      cleaned[key] = String(val)
    }
  }
  return cleaned
}

// ==================== 主组件 ====================
const TabsTable: React.FC = () => {
  const formRef = useRef<FormInstance>(null)
  const tableRef = useRef<ActionType>(null)
  const [activeKey, setActiveKey] = useState<TabKey>('management')
  const [refreshing, setRefreshing] = useState(false)
  const [selection, setSelection] = useState<{ keys: React.Key[]; rows: DataRecord[] }>({ keys: [], rows: [] })
  const [modal, setModal] = useState<{ type: ModalType; record?: DataRecord }>({ type: null })

  const clearSelection = () => setSelection({ keys: [], rows: [] })
  const closeModal = () => setModal({ type: null })

  modalRef.current = (type, record) => setModal({ type, record })

  deleteRef.current = (record: DataRecord) => {
    const identity: string =
      'itemName' in record ? (record as ManagementItem).itemName :
        'paramName' in record ? (record as InstitutionParam).paramName :
          'realName' in record ? (record as UserItem).realName :
            (record as { id: string }).id
    Modal.confirm({
      title: '确认删除', content: `确定要删除「${identity}」的记录吗？删除后不可恢复。`,
      okText: '确认', cancelText: '取消', okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const fn = activeKey === 'management' ? deleteManagement : activeKey === 'institution' ? deleteInstitution : deleteUser
          const res = await fn((record as { id: string }).id)
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
          const fn = activeKey === 'management' ? deleteManagement : activeKey === 'institution' ? deleteInstitution : deleteUser
          for (const id of selection.keys) await fn(String(id))
          message.success(`成功删除 ${selection.keys.length} 条记录`)
          clearSelection()
          tableRef.current?.reload()
        } catch { message.error('删除失败，请稍后重试') }
      },
    })
  }

  const refresh = async () => {
    setRefreshing(true)
    try { await tableRef.current?.reloadAndRest?.() } catch { /* ignore */ }
    finally { setRefreshing(false) }
  }

  const tabsChange = (key: string) => {
    setActiveKey(key as TabKey)
    clearSelection()
    formRef.current?.resetFields()
    setTimeout(() => tableRef.current?.reloadAndRest?.(), 0)
  }

  return (
    <div style={{ padding: 24, overflow: 'hidden', minWidth: 0 }}>
      <Tabs activeKey={activeKey} onChange={tabsChange}
        items={[
          { key: 'management', label: '管理事项' },
          { key: 'institution', label: '机构参数' },
          { key: 'users', label: '用户管理' },
        ]}
      />

      <ProForm formRef={formRef} layout="horizontal" submitter={false} style={{ marginBottom: 16 }} key={activeKey}>
        <Row gutter={[16, 16]}>
          {activeKey === 'management' && (
            <>
              <Col span={6}><ProFormText name="itemNo" label="事项编号" placeholder="请输入" /></Col>
              <Col span={6}><ProFormText name="itemName" label="事项名称" placeholder="请输入" /></Col>
              <Col span={6}><ProFormText name="department" label="所属部门" placeholder="请输入" /></Col>
              <Col span={6}><ProFormText name="owner" label="负责人" placeholder="请输入" /></Col>
              <Col span={6}>
                <ProFormSelect name="status" label="状态" placeholder="全部" fieldProps={{ allowClear: true }}
                  options={[{ label: '待处理', value: 'pending' }, { label: '处理中', value: 'in_progress' }, { label: '已完成', value: 'completed' }]} />
              </Col>
              <Col flex="none">
                <Form.Item label=" " colon={false}>
                  <Space>
                    <Button type="primary" onClick={async () => { await formRef.current?.validateFields(); tableRef.current?.reload() }}>查询</Button>
                    <Button onClick={() => { formRef.current?.resetFields(); tableRef.current?.reload() }}>重置</Button>
                  </Space>
                </Form.Item>
              </Col>
            </>
          )}
          {activeKey === 'institution' && (
            <>
              <Col span={6}><ProFormText name="paramCode" label="参数编码" placeholder="请输入" /></Col>
              <Col span={6}><ProFormText name="paramName" label="参数名称" placeholder="请输入" /></Col>
              <Col span={6}><ProFormText name="paramValue" label="参数值" placeholder="请输入" /></Col>
              <Col span={6}><ProFormDateRangePicker name="effectiveDateRange" label="生效日期" /></Col>
              <Col span={6}><ProFormText name="remark" label="备注" placeholder="请输入" /></Col>
              <Col flex="none">
                <Form.Item label=" " colon={false}>
                  <Space>
                    <Button type="primary" onClick={async () => { await formRef.current?.validateFields(); tableRef.current?.reload() }}>查询</Button>
                    <Button onClick={() => { formRef.current?.resetFields(); tableRef.current?.reload() }}>重置</Button>
                  </Space>
                </Form.Item>
              </Col>
            </>
          )}
          {activeKey === 'users' && (
            <>
              <Col span={6}><ProFormText name="username" label="用户名" placeholder="请输入" /></Col>
              <Col span={6}><ProFormText name="realName" label="姓名" placeholder="请输入" /></Col>
              <Col span={6}>
                <ProFormSelect name="role" label="角色" placeholder="全部" fieldProps={{ allowClear: true }}
                  options={[{ label: '管理员', value: '管理员' }, { label: '普通用户', value: '普通用户' }, { label: '审计员', value: '审计员' }]} />
              </Col>
              <Col span={6}><ProFormText name="email" label="邮箱" placeholder="请输入" /></Col>
              <Col span={6}><ProFormDateRangePicker name="createdAtRange" label="创建时间" /></Col>
              <Col flex="none">
                <Form.Item label=" " colon={false}>
                  <Space>
                    <Button type="primary" onClick={async () => { await formRef.current?.validateFields(); tableRef.current?.reload() }}>查询</Button>
                    <Button onClick={() => { formRef.current?.resetFields(); tableRef.current?.reload() }}>重置</Button>
                  </Space>
                </Form.Item>
              </Col>
            </>
          )}
        </Row>
      </ProForm>

      <ProTable<DataRecord>
        actionRef={tableRef}
        columns={columnsMap[activeKey]}
        rowKey="id"
        request={async (params) => {
          try {
            const { current, pageSize } = params
            const formValues = cleanFormValues(formRef.current?.getFieldsValue() ?? {})
            return (await requestFn[activeKey]({ current, pageSize, ...formValues }))
          } catch {
            message.error('请求失败，请稍后重试')
            return { data: [], success: false, total: 0 }
          }
        }}
        search={false}
        scroll={{ x: 'max-content', y: 'calc(100vh - 440px)' }}
        pagination={{ defaultPageSize: 10, showSizeChanger: true, showQuickJumper: true, pageSizeOptions: ['10', '20', '50', '100'] }}
        rowSelection={{ selectedRowKeys: selection.keys, onChange: (keys, rows) => setSelection({ keys, rows }) }}
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

      <DetailModal open={modal.type === 'detail'} record={modal.record} tab={activeKey} onClose={closeModal} />
      <EditModal open={modal.type === 'edit'} record={modal.record} tab={activeKey} onClose={closeModal}
        onSaved={() => { closeModal(); tableRef.current?.reload() }} />
    </div>
  )
}

export default TabsTable
