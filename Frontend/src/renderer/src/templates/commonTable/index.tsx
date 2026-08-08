import React, { useRef, useState } from 'react';
import {
  ProTable,
  ProColumns,
  ProForm,
  ProFormText,
  ProFormSelect,
  ProFormDigit,
  ProFormDatePicker,
  ProFormDateRangePicker,
  ModalForm,
  ActionType,
} from '@ant-design/pro-components';
import { Button, Col, Form, FormInstance, Modal, Row, Space, Tag, message } from 'antd';
import { ReloadOutlined, DeleteOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';

// ============================================================
// 行数据类型：报销单
// ============================================================
interface ReimbursementRow {
  id: string;
  code: string;
  title: string;
  applicant: string;
  amount: number;
  applyDate: string;
  status: 'pending' | 'approved' | 'rejected' | 'processing';
}

type RowType = ReimbursementRow;

// 报销单状态映射
const STATUS_OPTIONS: { label: string; value: ReimbursementRow['status']; color: string }[] = [
  { label: '待提交', value: 'pending', color: 'default' },
  { label: '审核中', value: 'processing', color: 'processing' },
  { label: '已通过', value: 'approved', color: 'success' },
  { label: '已驳回', value: 'rejected', color: 'error' },
];

const statusMap = new Map(STATUS_OPTIONS.map((s) => [s.value, s]));

// ============================================================
// 内存 mock 数据
// ============================================================
let MOCK_DATA: ReimbursementRow[] = [
  { id: '1', code: 'BX-2026-0001', title: '北京客户拜访差旅费', applicant: '张伟', amount: 3580.5, applyDate: '2026-07-12', status: 'approved' },
  { id: '2', code: 'BX-2026-0002', title: '部门季度团建费用', applicant: '李娜', amount: 8200, applyDate: '2026-07-15', status: 'processing' },
  { id: '3', code: 'BX-2026-0003', title: '研发服务器采购', applicant: '王强', amount: 15600.8, applyDate: '2026-07-18', status: 'pending' },
  { id: '4', code: 'BX-2026-0004', title: '市场推广物料制作', applicant: '刘洋', amount: 4300, applyDate: '2026-07-20', status: 'rejected' },
  { id: '5', code: 'BX-2026-0005', title: '上海展会参展费用', applicant: '陈静', amount: 22800, applyDate: '2026-07-22', status: 'approved' },
  { id: '6', code: 'BX-2026-0006', title: '员工培训课程费', applicant: '赵磊', amount: 6800, applyDate: '2026-07-25', status: 'processing' },
  { id: '7', code: 'BX-2026-0007', title: '办公电脑采购', applicant: '孙芳', amount: 12400, applyDate: '2026-07-28', status: 'pending' },
  { id: '8', code: 'BX-2026-0008', title: '客户接待餐费', applicant: '周杰', amount: 1280, applyDate: '2026-07-30', status: 'approved' },
  { id: '9', code: 'BX-2026-0009', title: '打印机维修费', applicant: '吴敏', amount: 860, applyDate: '2026-08-01', status: 'rejected' },
  { id: '10', code: 'BX-2026-0010', title: '广州分公司差旅', applicant: '郑华', amount: 5430.5, applyDate: '2026-08-03', status: 'processing' },
  { id: '11', code: 'BX-2026-0011', title: '年度审计咨询费', applicant: '林涛', amount: 38000, applyDate: '2026-08-05', status: 'pending' },
  { id: '12', code: 'BX-2026-0012', title: '团建活动场地租赁', applicant: '何丽', amount: 4500, applyDate: '2026-08-06', status: 'approved' },
  { id: '13', code: 'BX-2026-0013', title: '软件订阅续费', applicant: '高峰', amount: 9800, applyDate: '2026-08-07', status: 'processing' },
];

// ============================================================
// mock 接口：操作内存数组
// ============================================================
const updateRecord = (payload: Partial<ReimbursementRow> & { id: string }): { success: boolean } => {
  const idx = MOCK_DATA.findIndex((r) => r.id === payload.id);
  if (idx === -1) return { success: false };
  MOCK_DATA[idx] = { ...MOCK_DATA[idx], ...payload };
  return { success: true };
};

const deleteRecord = (id: string): { success: boolean } => {
  const before = MOCK_DATA.length;
  MOCK_DATA = MOCK_DATA.filter((r) => r.id !== id);
  return { success: MOCK_DATA.length < before };
};

const batchDeleteRecords = (ids: string[]): { success: boolean; deleted: number } => {
  const idSet = new Set(ids);
  const before = MOCK_DATA.length;
  MOCK_DATA = MOCK_DATA.filter((r) => !idSet.has(r.id));
  return { success: true, deleted: before - MOCK_DATA.length };
};

// ============================================================
// 弹窗与主组件
// ============================================================
type ModalType = 'detail' | 'edit' | null;

// ---------- 查看详情弹窗 ----------
type DetailModalProps = { open: boolean; record?: ReimbursementRow; onClose: () => void };

const DetailModal: React.FC<DetailModalProps> = ({ open, record, onClose }) => {
  return (
    <ModalForm title="查看详情" open={open}
      onOpenChange={(v) => { if (!v) onClose(); }}
      modalProps={{ destroyOnClose: true, width: 720 }}
      submitter={{ resetButtonProps: { style: { display: 'none' } }, submitButtonProps: { style: { display: 'none' } } }}
      initialValues={record ? { ...record } : undefined}
    >
      <Row gutter={[16, 0]}>
        <Col span={12}>
          <ProFormText name="code" label="编号" disabled fieldProps={{ style: { width: '100%' } }} />
        </Col>
        <Col span={12}>
          <ProFormText name="title" label="标题" disabled fieldProps={{ style: { width: '100%' } }} />
        </Col>
        <Col span={12}>
          <ProFormText name="applicant" label="申请人" disabled fieldProps={{ style: { width: '100%' } }} />
        </Col>
        <Col span={12}>
          <ProFormDigit name="amount" label="金额" disabled fieldProps={{ style: { width: '100%' } }} />
        </Col>
        <Col span={12}>
          <ProFormDatePicker name="applyDate" label="申请日期" disabled fieldProps={{ style: { width: '100%' } }} />
        </Col>
        <Col span={12}>
          <ProFormSelect name="status" label="状态" disabled options={STATUS_OPTIONS} fieldProps={{ style: { width: '100%' } }} />
        </Col>
      </Row>
      <div style={{ textAlign: 'right', marginTop: 16 }}>
        <Button onClick={onClose}>关闭</Button>
      </div>
    </ModalForm>
  );
};

// ---------- 修改弹窗 ----------
type EditModalProps = { open: boolean; record?: ReimbursementRow; onClose: () => void; onSaved: () => void };

const EditModal: React.FC<EditModalProps> = ({ open, record, onClose, onSaved }) => {
  return (
    <ModalForm title="修改记录" open={open}
      onOpenChange={(v) => { if (!v) onClose(); }}
      modalProps={{ destroyOnClose: true, width: 720 }}
      initialValues={record ? { ...record } : undefined}
      submitter={{ searchConfig: { submitText: '确定', resetText: '取消' } }}
      onFinish={async (values) => {
        try {
          const res = await updateRecord({ id: record?.id as string, ...values });
          if (!res.success) { message.error('修改失败，记录不存在'); return false; }
          message.success('修改成功');
          onSaved();
          return true;
        } catch {
          message.error('修改失败，请稍后重试');
          return false;
        }
      }}
    >
      <Row gutter={[16, 0]}>
        <Col span={12}>
          <ProFormText name="code" label="编号" disabled fieldProps={{ style: { width: '100%' } }} />
        </Col>
        <Col span={12}>
          <ProFormText name="title" label="标题" placeholder="请输入标题" fieldProps={{ style: { width: '100%' } }} />
        </Col>
        <Col span={12}>
          <ProFormText name="applicant" label="申请人" placeholder="请输入申请人" fieldProps={{ style: { width: '100%' } }} />
        </Col>
        <Col span={12}>
          <ProFormDigit name="amount" label="金额" placeholder="请输入金额" min={0} fieldProps={{ style: { width: '100%' } }} />
        </Col>
        <Col span={12}>
          <ProFormDatePicker name="applyDate" label="申请日期" fieldProps={{ style: { width: '100%' } }} />
        </Col>
        <Col span={12}>
          <ProFormSelect name="status" label="状态" options={STATUS_OPTIONS} fieldProps={{ style: { width: '100%' } }} />
        </Col>
      </Row>
    </ModalForm>
  );
};

// ==================== 主组件 ====================
const DefaultPage: React.FC = () => {
  const formRef = useRef<FormInstance>(null);
  const tableRef = useRef<ActionType | undefined>(null);

  const [modal, setModal] = useState<{ type: ModalType; record?: ReimbursementRow }>({ type: null });
  const [selection, setSelection] = useState<{ keys: React.Key[]; rows: ReimbursementRow[] }>({ keys: [], rows: [] });
  const [refreshing, setRefreshing] = useState(false);

  const openModal = (type: Exclude<ModalType, null>, record?: ReimbursementRow) => setModal({ type, record });
  const closeModal = () => setModal({ type: null });
  const clearSelection = () => setSelection({ keys: [], rows: [] });

  const handleQuery = async () => {
    try {
      await formRef.current?.validateFields();
    } catch {
      // 校验失败仍用当前值查询
    }
    tableRef.current?.reload();
  };

  const handleReset = () => {
    formRef.current?.resetFields();
    tableRef.current?.reload();
  };

  const handleDelete = (record: ReimbursementRow) => {
    const recordId = record.id;
    const displayName = record.title ?? recordId;

    Modal.confirm({
      title: '确认删除',
      content: `确定要删除「${displayName}」的记录吗？`,
      okText: '确认',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const res = await deleteRecord(recordId);
          if (!res.success) { message.error('删除失败，记录不存在'); return; }
          message.success('删除成功');
          tableRef.current?.reload();
        } catch {
          message.error('删除失败，请稍后重试');
        }
      },
    });
  };

  const handleBatchDelete = () => {
    if (selection.keys.length === 0) return;
    Modal.confirm({
      title: '批量删除',
      content: '确认删除吗？删除后不可恢复。',
      okText: '确定删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const res = await batchDeleteRecords(selection.keys as string[]);
          message.success(`成功删除 ${res.deleted} 条记录`);
          clearSelection();
          tableRef.current?.reload();
        } catch {
          message.error('删除失败，请稍后重试');
        }
      },
    });
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      formRef.current?.resetFields();
      clearSelection();
      tableRef.current?.reload();
    } finally {
      setRefreshing(false);
    }
  };

  // ProTable 列配置
  const columns: ProColumns<RowType>[] = [
    { title: '编号', dataIndex: 'code', key: 'code', width: 140, ellipsis: true, copyable: true },
    { title: '标题', dataIndex: 'title', key: 'title', width: 200, ellipsis: true },
    { title: '申请人', dataIndex: 'applicant', key: 'applicant', width: 100 },
    {
      title: '金额',
      dataIndex: 'amount',
      key: 'amount',
      width: 120,
      valueType: 'money',
      align: 'right',
      sorter: (a, b) => a.amount - b.amount,
    },
    {
      title: '申请日期',
      dataIndex: 'applyDate',
      key: 'applyDate',
      width: 130,
      valueType: 'date',
      sorter: (a, b) => dayjs(a.applyDate).valueOf() - dayjs(b.applyDate).valueOf(),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (_, record) => {
        const s = statusMap.get(record.status);
        return s ? <Tag color={s.color}>{s.label}</Tag> : record.status;
      },
      filters: STATUS_OPTIONS.map((s) => ({ text: s.label, value: s.value })),
      onFilter: (value, record) => record.status === value,
    },
    {
      title: '操作',
      key: 'operation',
      width: 220,
      fixed: 'right',
      render: (_, record) => (
        <>
          <Button type="link" size="small" onClick={() => openModal('detail', record)}>查看详情</Button>
          <Button type="link" size="small" onClick={() => openModal('edit', record)}>修改</Button>
          <Button type="link" size="small" danger onClick={() => handleDelete(record)}>删除</Button>
        </>
      ),
    },
  ];

  // 搜索筛选表单
  const renderFilter = () => (
    <ProForm formRef={formRef} layout="horizontal" submitter={false}>
      <Row gutter={[16, 16]}>
        <Col span={6}>
          <ProFormText name="code" label="编号" placeholder="请输入编号" />
        </Col>
        <Col span={6}>
          <ProFormText name="title" label="标题" placeholder="请输入标题" />
        </Col>
        <Col span={6}>
          <ProFormText name="applicant" label="申请人" placeholder="请输入申请人" />
        </Col>
        <Col span={6}>
          <ProFormSelect name="status" label="状态" placeholder="请选择状态" options={STATUS_OPTIONS} allowClear />
        </Col>
        <Col span={12}>
          <ProFormDateRangePicker name="applyDate" label="申请日期" placeholder={['开始日期', '结束日期']} fieldProps={{ style: { width: '100%' } }} />
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
  );

  const toolBarRender = () => [
    <Button
      key="batchDelete"
      icon={<DeleteOutlined />}
      danger
      disabled={selection.keys.length === 0}
      onClick={handleBatchDelete}
    >
      批量删除
    </Button>,
    <Button key="refresh" icon={<ReloadOutlined />} loading={refreshing} onClick={handleRefresh}>刷新</Button>,
  ];

  // ProTable 走 request 返回内存 mock 数据。注意：scroll 不能设 y 值
  // （calc(100vh - N) 在 DesignRenderer 的 iframe 里会算出 0/负值，导致表格体
  // 高度为 0、行不可见）。只保留 x 横向滚动。

  return (
    <div style={{ padding: 24, overflow: 'hidden', minWidth: 0 }}>
      {renderFilter()}
      <ProTable<RowType>
        actionRef={tableRef}
        columns={columns}
        rowKey="id"
        request={async () => ({
          data: MOCK_DATA,
          success: true,
          total: MOCK_DATA.length,
        })}
        style={{ marginTop: 16 }}
        search={false}
        scroll={{ x: 'max-content', y: 300 }}
        pagination={{
          defaultPageSize: 10,
          showSizeChanger: true,
          showQuickJumper: true,
          pageSizeOptions: ['10', '20', '50', '100'],
        }}
        rowSelection={{
          selectedRowKeys: selection.keys,
          onChange: (keys, rows) => setSelection({ keys, rows: rows as ReimbursementRow[] }),
        }}
        tableAlertRender={({ selectedRowKeys: selKeys }) =>
          selKeys.length > 0 ? (
            <Space>
              <span>已选择 {selKeys.length} 项</span>
              <a onClick={clearSelection}>取消选择</a>
            </Space>
          ) : false
        }
        tableAlertOptionRender={false}
        options={{ setting: true, reload: false, density: false, fullScreen: false }}
        toolBarRender={toolBarRender}
      />
      <DetailModal open={modal.type === 'detail'} record={modal.record} onClose={closeModal} />
      <EditModal open={modal.type === 'edit'} record={modal.record} onClose={closeModal}
        onSaved={() => { closeModal(); tableRef.current?.reload(); }} />
    </div>
  );
};

export default DefaultPage;
