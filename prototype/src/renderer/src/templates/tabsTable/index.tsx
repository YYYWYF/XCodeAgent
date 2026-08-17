import React, { useRef, useState } from 'react'
import type { FormInstance } from 'antd'
import type { ActionType, ProColumns } from '@ant-design/pro-components'
import { ProForm, ProFormDateRangePicker, ProFormText, ProTable, ModalForm } from '@ant-design/pro-components'
import { Button, Form, Modal, Row, Col, Space, Tabs, message } from 'antd'
import { ReloadOutlined, DeleteOutlined } from '@ant-design/icons'

/*
 * ╔══════════════════════════════════════════════════════════╗
 * ║  多标签页表格页面模板（骨架）                            ║
 * ║                                                        ║
 * ║  本模板提供带 Tabs 切换的多实体表格 UI 框架。          ║
 * ║  每个 Tab 下包含独立的搜索筛选 + ProTable + CRUD。      ║
 * ║                                                        ║
 * ║  使用时请根据项目计划填充：                              ║
 * ║  ① Tab 列表 —— 标签页名称与数量                        ║
 * ║  ② 每个 Tab 的表头列定义 + 搜索字段                     ║
 * ║  ③ API 调用 —— 增删改查接口                            ║
 * ║  ④ 行数据类型 —— 根据接口响应 schema 定义              ║
 * ║  ⑤ 不可编辑字段标记（主键、编号等）                    ║
 * ║  ⑥ 行标识字段（ID 字段名 + 展示名）                    ║
 * ╚══════════════════════════════════════════════════════════╝
 */

// ============================================================
// ① TODO: 定义 Tab 列表及其列配置
// ============================================================
// 每个 Tab 包含：
//   key         —— Tab 标识
//   label       —— Tab 显示名称
//   columns     —— 表头列定义 [{ title: '列标题', dataIndex: '字段名' }]
//   searchFields —— 搜索字段（与 columns 中的 dataIndex 共用，日期字段需额外标注）
//   dateFields  —— 日期类型字段名列表（用于日期选择器和格式化）
//   readonlyFields —— 详情/编辑弹窗中不可编辑的字段
type RowType = Record<string, unknown>;

interface TabDef {
  key: string;
  label: string;
  columns: { title: string; dataIndex: string }[];
  dateFields: string[];
  readonlyFields: string[];
}

const TAB_DEFS: TabDef[] = [
  // TODO: 在此添加标签页定义，例如：
  // {
  //   key: 'management',
  //   label: '管理事项',
  //   columns: [
  //     { title: '编号', dataIndex: 'itemNo' },
  //     { title: '名称', dataIndex: 'itemName' },
  //     { title: '部门', dataIndex: 'department' },
  //     { title: '负责人', dataIndex: 'owner' },
  //     { title: '状态', dataIndex: 'status' },
  //   ],
  //   dateFields: [],
  //   readonlyFields: ['itemNo'],
  // },
];

// ============================================================
// ② TODO: 根据 api_contracts 实现每个 Tab 的接口调用
// ============================================================
// key → API 函数映射。每个 Tab 需要实现：
//   fetch(key, params) → { data, success, total }
//   update(key, payload) → { success }
//   delete(key, id) → { success }

const fetchTabList = async (tabKey: string, _params: Record<string, unknown>): Promise<{ data: RowType[]; success: boolean; total: number }> => {
  // TODO: 根据 tabKey 调用对应的列表查询接口
  throw new Error(`fetchTabList(${tabKey}): 请替换为实际 API 调用`);
};

const updateTabRecord = async (tabKey: string, _payload: Record<string, unknown>): Promise<{ success: boolean }> => {
  // TODO: 根据 tabKey 调用对应的修改接口
  throw new Error(`updateTabRecord(${tabKey}): 请替换为实际 API 调用`);
};

const deleteTabRecord = async (tabKey: string, _id: string): Promise<{ success: boolean }> => {
  // TODO: 根据 tabKey 调用对应的删除接口
  throw new Error(`deleteTabRecord(${tabKey}): 请替换为实际 API 调用`);
};

// ============================================================
// 以下为 UI 框架代码，无需修改
// ============================================================

type ModalType = 'detail' | 'edit' | null;

/** 清洗查询表单值：去掉空值，日期范围拆为 start/end */
const cleanFormValues = (values: Record<string, unknown>, dateFields: string[]): Record<string, string> => {
  const cleaned: Record<string, string> = {};
  for (const [key, val] of Object.entries(values)) {
    if (val === undefined || val === null || val === '') continue;
    if (Array.isArray(val) && val.length === 2 && dateFields.includes(key)) {
      if (val[0] && typeof val[0] === 'object' && val[0] !== null && typeof (val[0] as Record<string, unknown>).format === 'function') {
        cleaned[key + 'Start'] = (val[0] as { format: (f: string) => string }).format('YYYY-MM-DD');
      }
      if (val[1] && typeof val[1] === 'object' && val[1] !== null && typeof (val[1] as Record<string, unknown>).format === 'function') {
        cleaned[key + 'End'] = (val[1] as { format: (f: string) => string }).format('YYYY-MM-DD 23:59:59');
      }
      continue;
    }
    cleaned[key] = String(val);
  }
  return cleaned;
};

// ---------- 查看详情弹窗 ----------
type DetailModalProps = { open: boolean; record?: Record<string, unknown>; tab: TabDef; onClose: () => void };

const DetailModal: React.FC<DetailModalProps> = ({ open, record, tab, onClose }) => {
  return (
    <ModalForm title="查看详情" open={open} onOpenChange={(v) => { if (!v) onClose() }}
      modalProps={{ destroyOnClose: true, width: 640 }}
      submitter={{ resetButtonProps: { style: { display: 'none' } }, submitButtonProps: { style: { display: 'none' } } }}
      initialValues={record}
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
  );
};

// ---------- 修改弹窗 ----------
type EditModalProps = { open: boolean; record?: Record<string, unknown>; tab: TabDef; onClose: () => void; onSaved: () => void };

const EditModal: React.FC<EditModalProps> = ({ open, record, tab, onClose, onSaved }) => {
  return (
    <ModalForm title="修改" open={open} onOpenChange={(v) => { if (!v) onClose() }}
      modalProps={{ destroyOnClose: true, width: 640 }}
      initialValues={record}
      submitter={{ searchConfig: { submitText: '确定', resetText: '取消' } }}
      onFinish={async (values) => {
        try {
          const res = await updateTabRecord(tab.key, { id: record?.id as string, ...values });
          if (!res.success) { message.error('修改失败，记录不存在'); return false; }
          message.success('修改成功');
          onSaved();
          return true;
        } catch { message.error('修改失败，请稍后重试'); return false; }
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
  );
};

// ==================== 主组件 ====================
const TabsTable: React.FC = () => {
  const formRef = useRef<FormInstance>(null);
  const tableRef = useRef<ActionType>(null);
  const [activeKey, setActiveKey] = useState<string>(TAB_DEFS[0]?.key ?? '');
  const [refreshing, setRefreshing] = useState(false);
  const [selection, setSelection] = useState<{ keys: React.Key[]; rows: Record<string, unknown>[] }>({ keys: [], rows: [] });
  const [modal, setModal] = useState<{ type: ModalType; record?: Record<string, unknown> }>({ type: null });

  const activeTab = TAB_DEFS.find((t) => t.key === activeKey) ?? TAB_DEFS[0];

  const clearSelection = () => setSelection({ keys: [], rows: [] });
  const closeModal = () => setModal({ type: null });

  const handleDelete = (record: Record<string, unknown>) => {
    // ⑥ TODO: 指定每条记录的展示名称来源
    const recordId = record.id as string;
    const displayName = record.name ?? recordId;

    Modal.confirm({
      title: '确认删除', content: `确定要删除「${displayName}」的记录吗？删除后不可恢复。`,
      okText: '确认', cancelText: '取消', okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const res = await deleteTabRecord(activeKey, recordId);
          if (!res.success) { message.error('删除失败，记录不存在'); return; }
          message.success('删除成功');
          tableRef.current?.reload();
        } catch { message.error('删除失败，请稍后重试'); }
      },
    });
  };

  const handleBatchDelete = () => {
    if (selection.keys.length === 0) return;
    Modal.confirm({
      title: '批量删除', content: '确认删除吗？删除后不可恢复。', okText: '确定删除', cancelText: '取消', okButtonProps: { danger: true },
      onOk: async () => {
        try {
          for (const id of selection.keys) await deleteTabRecord(activeKey, String(id));
          message.success(`成功删除 ${selection.keys.length} 条记录`);
          clearSelection();
          tableRef.current?.reload();
        } catch { message.error('删除失败，请稍后重试'); }
      },
    });
  };

  const refresh = async () => {
    setRefreshing(true);
    try { await tableRef.current?.reloadAndRest?.(); } catch { /* ignore */ }
    finally { setRefreshing(false); }
  };

  const tabsChange = (key: string) => {
    setActiveKey(key);
    clearSelection();
    formRef.current?.resetFields();
    setTimeout(() => tableRef.current?.reloadAndRest?.(), 0);
  };

  // 根据当前 Tab 生成 ProTable 列配置
  const columns: ProColumns<RowType>[] = [
    ...activeTab.columns.map((f) => ({
      title: f.title, dataIndex: f.dataIndex, key: f.dataIndex,
      width: activeTab.dateFields.includes(f.dataIndex) ? 160 : 120,
      ellipsis: true,
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
  ];

  return (
    <div style={{ padding: 24, overflow: 'hidden', minWidth: 0 }}>
      <Tabs activeKey={activeKey} onChange={tabsChange}
        items={TAB_DEFS.map((t) => ({ key: t.key, label: t.label }))}
      />

      <ProForm formRef={formRef} layout="horizontal" submitter={false} style={{ marginBottom: 16 }} key={activeKey}>
        <Row gutter={[16, 16]}>
          {activeTab.columns.map((f) => (
            <Col span={6} key={f.dataIndex}>
              {activeTab.dateFields.includes(f.dataIndex) ? (
                <ProFormDateRangePicker name={f.dataIndex} label={f.title} />
              ) : (
                <ProFormText name={f.dataIndex} label={f.title} placeholder="请输入" />
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

      <ProTable<RowType>
        actionRef={tableRef}
        columns={columns}
        rowKey="id"
        request={async (params) => {
          try {
            const { current, pageSize } = params;
            const formValues = cleanFormValues(formRef.current?.getFieldsValue() ?? {}, activeTab.dateFields);
            return fetchTabList(activeKey, { current, pageSize, ...formValues });
          } catch {
            message.error('请求失败，请稍后重试');
            return { data: [], success: false, total: 0 };
          }
        }}
        search={false}
        scroll={{ x: 'max-content', y: 'calc(100vh - 440px)' }}
        pagination={{ defaultPageSize: 10, showSizeChanger: true, showQuickJumper: true, pageSizeOptions: ['10', '20', '50', '100'] }}
        rowSelection={{ selectedRowKeys: selection.keys, onChange: (keys, rows) => setSelection({ keys, rows: rows as Record<string, unknown>[] }) }}
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
        onSaved={() => { closeModal(); tableRef.current?.reload(); }} />
    </div>
  );
};

export default TabsTable;
