import React, { useRef, useState } from 'react';
import { ProTable, ProColumns, ProForm, ProFormText, ProFormDatePicker, ProFormDateRangePicker, ModalForm } from '@ant-design/pro-components';
import { Button, Col, Form, FormInstance, Modal, Row, Space, message } from 'antd';
import { ReloadOutlined, DeleteOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { fetchReimbursementList, updateReimbursement, deleteReimbursement, batchDeleteReimbursement } from './api';
import type { ReimbursementItem, ReimbursementQuery } from './types';

// ---------- 表头元信息 ----------
const FIELD_DEFS: { title: string; dataIndex: keyof ReimbursementItem }[] = [
  { title: '报销单号', dataIndex: 'reimbursementNo' },
  { title: '科目名称', dataIndex: 'subjectName' },
  { title: '金额', dataIndex: 'amount' },
  { title: '日期', dataIndex: 'date' },
  { title: '报销单申请人', dataIndex: 'applicant' },
  { title: '事项类型', dataIndex: 'eventType' },
];

const DATE_FIELDS: string[] = ['date', 'applyTime'];

type ModalType = 'detail' | 'edit' | null;

// ==================== 查看详情弹窗 ====================
type DetailModalProps = { open: boolean; record?: ReimbursementItem; onClose: () => void; };

const DetailModal: React.FC<DetailModalProps> = ({ open, record, onClose }) => {
  return (
    <ModalForm title="查看详情" open={open}
      onOpenChange={(v) => { if (!v) onClose(); }}
      modalProps={{ destroyOnClose: true, width: 720 }}
      submitter={{ resetButtonProps: { style: { display: 'none' } }, submitButtonProps: { style: { display: 'none' } } }}
      initialValues={record}
    >
      <Row gutter={[16, 0]}>
        {FIELD_DEFS.map((f) => (
          <Col span={12} key={f.dataIndex as string}>
            {DATE_FIELDS.includes(f.dataIndex as string) ? (
              <ProFormText name={f.dataIndex} label={f.title} disabled fieldProps={{ style: { width: '100%' } }} />
            ) : (
              <ProFormText name={f.dataIndex} label={f.title} disabled />
            )}
          </Col>
        ))}
      </Row>
      <div style={{ textAlign: 'right', marginTop: 16 }}>
        <Button onClick={onClose}>关闭</Button>
      </div>
    </ModalForm>
  );
};

// ==================== 修改弹窗 ====================
type EditModalProps = { open: boolean; record?: ReimbursementItem; onClose: () => void; onSaved: () => void; };

const EditModal: React.FC<EditModalProps> = ({ open, record, onClose, onSaved }) => {
  return (
    <ModalForm title="修改报销记录" open={open}
      onOpenChange={(v) => { if (!v) onClose(); }}
      modalProps={{ destroyOnClose: true, width: 720 }}
      initialValues={record}
      submitter={{ searchConfig: { submitText: '确定', resetText: '取消' } }}
      onFinish={async (values) => {
        try {
          const res = await updateReimbursement({ id: record?.id as string, ...values });
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
        {FIELD_DEFS.map((f) => {
          const isReadonly = f.dataIndex === 'reimbursementNo';
          return (
            <Col span={12} key={f.dataIndex as string}>
              {DATE_FIELDS.includes(f.dataIndex as string) ? (
                <ProFormDatePicker name={f.dataIndex} label={f.title} fieldProps={{ style: { width: '100%' } }} />
              ) : (
                <ProFormText name={f.dataIndex} label={f.title} placeholder="请输入内容" disabled={isReadonly} />
              )}
            </Col>
          );
        })}
      </Row>
    </ModalForm>
  );
};

// ==================== 主组件 ====================
const DefaultPage: React.FC = () => {
  const formRef = useRef<FormInstance>(null);
  const tableRef = useRef<any>(null);

  const [modal, setModal] = useState<{ type: ModalType; record?: ReimbursementItem }>({ type: null });
  const [selection, setSelection] = useState<{ keys: React.Key[]; rows: ReimbursementItem[] }>({ keys: [], rows: [] });
  const [refreshing, setRefreshing] = useState(false);

  const openModal = (type: Exclude<ModalType, null>, record?: ReimbursementItem) => setModal({ type, record });
  const closeModal = () => setModal({ type: null });
  const clearSelection = () => setSelection({ keys: [], rows: [] });

  const handleDelete = (record: ReimbursementItem) => {
    Modal.confirm({
      title: '确认删除',
      content: `确定要删除报销单号「${record.reimbursementNo}」的记录吗？`,
      okText: '确认',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const res = await deleteReimbursement(record.id);
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
          const res = await batchDeleteReimbursement(selection.keys as string[]);
          message.success(`成功删除 ${res.deleted} 条记录`);
          clearSelection();
          tableRef.current?.reload();
        } catch {
          message.error('删除失败，请稍后重试');
        }
      },
    });
  };

  const columns: ProColumns<ReimbursementItem>[] = [
    ...FIELD_DEFS.map((f) => ({
      title: f.title, dataIndex: f.dataIndex, key: f.dataIndex,
      width: f.dataIndex === 'amount' ? 100 : f.dataIndex === 'date' || f.dataIndex === 'applyTime' ? 160 : 120,
      valueType: (f.dataIndex === 'amount' ? 'money' : f.dataIndex === 'date' || f.dataIndex === 'applyTime' ? 'dateTime' : 'text') as any,
      ellipsis: ['merchantName', 'ruleDetail', 'detailDesc'].includes(f.dataIndex as string),
    })),
    {
      title: '操作', key: 'operation', width: 220, fixed: 'right',
      render: (_, record) => (
        <>
          <Button type="link" size="small" onClick={() => openModal('detail', record)}>查看详情</Button>
          <Button type="link" size="small" onClick={() => openModal('edit', record)}>修改</Button>
          <Button type="link" size="small" danger onClick={() => handleDelete(record)}>删除</Button>
        </>
      ),
    },
  ];

  const renderFilter = () => (
    <ProForm formRef={formRef} layout="horizontal" submitter={false}
      onFinish={async () => { tableRef.current?.reload(); }}>
      <Row gutter={[16, 16]}>
        {FIELD_DEFS.map((f) => (
          <Col span={6} key={f.dataIndex as string}>
            {f.dataIndex === 'date' ? (
              <ProFormDateRangePicker name={f.dataIndex} label={f.title} placeholder={['开始日期', '结束日期']} fieldProps={{ style: { width: '100%' } }} />
            ) : DATE_FIELDS.includes(f.dataIndex as string) ? (
              <ProFormDatePicker name={f.dataIndex} label={f.title} placeholder="请选择" fieldProps={{ style: { width: '100%' } }} />
            ) : (
              <ProFormText name={f.dataIndex} label={f.title} placeholder="请输入内容" />
            )}
          </Col>
        ))}
        <Col flex="none">
          <Form.Item label=" " colon={false}>
            <Space>
              <Button type="primary" onClick={async () => { await formRef.current?.validateFields(); tableRef.current?.reload(); }}>查询</Button>
              <Button onClick={() => { formRef.current?.resetFields(); tableRef.current?.reload(); }}>重置</Button>
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
    <Button key="refresh" icon={<ReloadOutlined />} loading={refreshing} onClick={async () => { setRefreshing(true); try { await tableRef.current?.reloadAndRest?.(); } finally { setRefreshing(false); } }}>刷新</Button>,
  ];

  return (
    <div style={{ padding: 24, overflow: 'hidden', minWidth: 0 }}>
      {renderFilter()}
      <ProTable<ReimbursementItem>
        actionRef={tableRef} columns={columns} rowKey="id"
        style={{ marginTop: 16 }}
        request={async (params) => {
          try {
            const { current, pageSize, ...rest } = params;
            const formValues = formRef.current?.getFieldsValue() || {};

            // 日期范围转换为 dateStart / dateEnd
            const apiParams: Record<string, unknown> = {};
            Object.entries(formValues).forEach(([key, value]) => {
              if (key === 'date' && Array.isArray(value) && value.length === 2) {
                if (value[0]) apiParams.dateStart = dayjs(value[0] as any).format('YYYY-MM-DD');
                if (value[1]) apiParams.dateEnd = dayjs(value[1] as any).format('YYYY-MM-DD');
              } else {
                apiParams[key] = value;
              }
            });

            const res = await fetchReimbursementList({
              page: current, pageSize,
              ...apiParams,
              ...rest,
            } as ReimbursementQuery);
            return { data: res.data, success: res.success, total: res.total };
          } catch {
            message.error('请求失败，请稍后重试');
            return { data: [], success: false, total: 0 };
          }
        }}
        pagination={{ defaultPageSize: 10, showSizeChanger: true, showQuickJumper: true, pageSizeOptions: ['10', '20', '50', '100'] }} search={false}
        scroll={{ x: 'max-content', y: 'calc(100vh - 380px)' }}
        rowSelection={{
          selectedRowKeys: selection.keys,
          onChange: (keys, rows) => setSelection({ keys, rows: rows as ReimbursementItem[] }),
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
