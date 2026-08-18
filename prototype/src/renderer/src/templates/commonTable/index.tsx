import React, { useRef, useState } from 'react';
import { ProTable, ProColumns, ProForm, ProFormText, ProFormDatePicker, ProFormDateRangePicker, ModalForm } from '@ant-design/pro-components';
import { Button, Col, Form, FormInstance, Modal, Row, Space, message } from 'antd';
import { ReloadOutlined, DeleteOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';

/*
 * ╔══════════════════════════════════════════════════════════╗
 * ║  通用列表查询页面模板（骨架）                            ║
 * ║                                                        ║
 * ║  本模板提供标准后台列表页面的 UI 框架，包括：           ║
 * ║  · 搜索筛选表单                                         ║
 * ║  · ProTable 数据表格（分页 + 排序 + 行选择）            ║
 * ║  · 查看详情弹窗                                         ║
 * ║  · 编辑弹窗                                             ║
 * ║  · 单条删除确认                                         ║
 * ║  · 批量删除                                             ║
 * ║                                                        ║
 * ║  使用时请根据项目计划中的 api_contracts 填充以下内容：  ║
 * ║  ① 表格列定义 —— 列标题 + 数据字段名                   ║
 * ║  ② API 调用 —— 查询/修改/删除/批量删除接口             ║
 * ║  ③ 行数据类型 —— 根据接口响应 schema 定义              ║
 * ║  ④ 搜索字段 —— 哪些列支持筛选                          ║
 * ║  ⑤ 日期字段标记 —— 哪些列用日期组件渲染                ║
 * ╚══════════════════════════════════════════════════════════╝
 */

// ============================================================
// ① TODO: 根据 api_contracts 响应 schema 定义行数据类型
// ============================================================
type RowType = Record<string, unknown>;

// ============================================================
// ② TODO: 根据业务需求定义表格列
// ============================================================
// 示例格式（请替换为实际字段）：
//   { title: '列标题', dataIndex: '字段名' }
// 字段名应与接口响应中的字段名一致
// 数值列可设置 valueType: 'money'，日期列设置 valueType: 'dateTime'
const FIELD_DEFS: { title: string; dataIndex: string }[] = [
  // TODO: 在此添加列定义，例如：
  // { title: '编号', dataIndex: 'code' },
  // { title: '名称', dataIndex: 'name' },
  // { title: '金额', dataIndex: 'amount' },
  // { title: '日期', dataIndex: 'date' },
  // { title: '状态', dataIndex: 'status' },
];

// ============================================================
// ③ TODO: 标记日期类型字段（用于日期选择器和格式化）
// ============================================================
const DATE_FIELDS: string[] = [
  // TODO: 列出日期字段名，例如 'date', 'createdAt'
];

// ============================================================
// ④ TODO: 根据 api_contracts 实现接口调用函数
// ============================================================
// 以下函数为占位，请替换为实际 API 调用：

/** 查询列表（分页 + 筛选） */
const fetchList = async (_params: Record<string, unknown>): Promise<{ data: RowType[]; success: boolean; total: number }> => {
  // TODO: 调用项目计划声明的列表查询接口
  // 返回格式: { data: RowType[], success: boolean, total: number }
  throw new Error('fetchList: 请替换为实际 API 调用');
};

/** 修改记录 */
const updateRecord = async (_payload: Record<string, unknown>): Promise<{ success: boolean }> => {
  // TODO: 调用项目计划声明的修改接口
  throw new Error('updateRecord: 请替换为实际 API 调用');
};

/** 删除记录 */
const deleteRecord = async (_id: string): Promise<{ success: boolean }> => {
  // TODO: 调用项目计划声明的删除接口
  throw new Error('deleteRecord: 请替换为实际 API 调用');
};

/** 批量删除 */
const batchDeleteRecords = async (_ids: string[]): Promise<{ success: boolean; deleted: number }> => {
  // TODO: 调用项目计划声明的批量删除接口
  throw new Error('batchDeleteRecords: 请替换为实际 API 调用');
};

// ============================================================
// 以下为 UI 框架代码，无需修改
// ============================================================

type ModalType = 'detail' | 'edit' | null;

// ---------- 查看详情弹窗 ----------
type DetailModalProps = { open: boolean; record?: Record<string, unknown>; onClose: () => void };

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
          <Col span={12} key={f.dataIndex}>
            <ProFormText name={f.dataIndex} label={f.title} disabled fieldProps={{ style: { width: '100%' } }} />
          </Col>
        ))}
      </Row>
      <div style={{ textAlign: 'right', marginTop: 16 }}>
        <Button onClick={onClose}>关闭</Button>
      </div>
    </ModalForm>
  );
};

// ---------- 修改弹窗 ----------
type EditModalProps = { open: boolean; record?: Record<string, unknown>; onClose: () => void; onSaved: () => void };

const EditModal: React.FC<EditModalProps> = ({ open, record, onClose, onSaved }) => {
  // TODO: ⑤ 指定不可编辑的字段（如主键、编号）
  const readonlyFields: string[] = [
    // 例如: 'code', 'id'
  ];

  return (
    <ModalForm title="修改记录" open={open}
      onOpenChange={(v) => { if (!v) onClose(); }}
      modalProps={{ destroyOnClose: true, width: 720 }}
      initialValues={record}
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
        {FIELD_DEFS.map((f) => {
          const isReadonly = readonlyFields.includes(f.dataIndex);
          const isDate = DATE_FIELDS.includes(f.dataIndex);
          return (
            <Col span={12} key={f.dataIndex}>
              {isDate ? (
                <ProFormDatePicker name={f.dataIndex} label={f.title} fieldProps={{ style: { width: '100%' } }} disabled={isReadonly} />
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

  const [modal, setModal] = useState<{ type: ModalType; record?: Record<string, unknown> }>({ type: null });
  const [selection, setSelection] = useState<{ keys: React.Key[]; rows: Record<string, unknown>[] }>({ keys: [], rows: [] });
  const [refreshing, setRefreshing] = useState(false);

  const openModal = (type: Exclude<ModalType, null>, record?: Record<string, unknown>) => setModal({ type, record });
  const closeModal = () => setModal({ type: null });
  const clearSelection = () => setSelection({ keys: [], rows: [] });

  const handleDelete = (record: Record<string, unknown>) => {
    // TODO: ⑥ 指定记录的唯一标识字段 + 展示用的名称字段
    const recordId = record.id as string;
    const displayName = record.name ?? recordId;

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

  // ⑦ TODO: 根据 FIELD_DEFS 生成 ProTable 列配置
  const columns: ProColumns<RowType>[] = [
    ...FIELD_DEFS.map((f) => ({
      title: f.title,
      dataIndex: f.dataIndex,
      key: f.dataIndex,
      // TODO: 根据字段类型调整 valueType 和 width
      // 数值: valueType: 'money', width: 100
      // 日期: valueType: 'dateTime', width: 160
      // 文本: valueType: 'text', width: 120
      width: 120,
      ellipsis: true,
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

  // 搜索筛选表单
  const renderFilter = () => (
    <ProForm formRef={formRef} layout="horizontal" submitter={false}
      onFinish={async () => { tableRef.current?.reload(); }}>
      <Row gutter={[16, 16]}>
        {FIELD_DEFS.map((f) => (
          <Col span={6} key={f.dataIndex}>
            {DATE_FIELDS.includes(f.dataIndex) ? (
              <ProFormDateRangePicker name={f.dataIndex} label={f.title} placeholder={['开始日期', '结束日期']} fieldProps={{ style: { width: '100%' } }} />
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
      <ProTable<RowType>
        actionRef={tableRef} columns={columns} rowKey="id"
        style={{ marginTop: 16 }}
        request={async (params) => {
          try {
            const { current, pageSize, ...rest } = params;
            const formValues = formRef.current?.getFieldsValue() || {};

            // ⑧ TODO: 日期范围字段转换为接口参数格式（如 date → dateStart/dateEnd）
            const apiParams: Record<string, unknown> = {};
            Object.entries(formValues).forEach(([key, value]) => {
              if (DATE_FIELDS.includes(key) && Array.isArray(value) && value.length === 2) {
                if (value[0]) apiParams[key + 'Start'] = dayjs(value[0] as any).format('YYYY-MM-DD');
                if (value[1]) apiParams[key + 'End'] = dayjs(value[1] as any).format('YYYY-MM-DD');
              } else {
                apiParams[key] = value;
              }
            });

            const res = await fetchList({
              page: current,
              pageSize,
              ...apiParams,
              ...rest,
            });
            return { data: res.data, success: res.success, total: res.total };
          } catch {
            message.error('请求失败，请稍后重试');
            return { data: [], success: false, total: 0 };
          }
        }}
        pagination={{ defaultPageSize: 10, showSizeChanger: true, showQuickJumper: true, pageSizeOptions: ['10', '20', '50', '100'] }}
        search={false}
        scroll={{ x: 'max-content', y: 'calc(100vh - 380px)' }}
        rowSelection={{
          selectedRowKeys: selection.keys,
          onChange: (keys, rows) => setSelection({ keys, rows: rows as Record<string, unknown>[] }),
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
