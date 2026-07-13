# 页面模板详解

## 3.1 模板一：查询表格 + 批量导入导出

### 适用场景

- ☑ 字段较多，需自定义横排筛选表单
- ☑ 需要 Excel 批量导入（下载模板 → 上传 → 解析 → 入库）
- ☑ 需要按勾选行 + 自选列导出 Excel
- ☑ 行内需"查看详情"，无需表格内联编辑
- ☑ 后端数据量大，需服务端分页

### 核心结构

```
页面入口
├── 自定义筛选表单 (ProForm + Row/Col + 手动查询/重置)
├── ProTable (search={false})
│   ├── columns (FIELD_DEFS 驱动 + 操作列)
│   ├── request (合并 formValues + 分页参数)
│   ├── toolBarRender (导入/导出/刷新)
│   └── rowSelection (keys + rows 合并为一个 selection state)
└── 弹窗（统一 modal state 管理）
    ├── ImportModal (上传 + 解析)
    ├── ExportModal (自选列)
    └── DetailModal (只读表单)
```

### 设计要点

**1. FIELD_DEFS 元信息驱动**

```ts
const FIELD_DEFS: { title: string; dataIndex: string }[] = [
  { title: '报销单号', dataIndex: 'reimbursementNo' },
  { title: '科目名称', dataIndex: 'subjectName' },
  // ...
];
```

**2. 弹窗子组件内聚状态** — 导入弹窗的 `uploadFile`/`parsedRows`、导出弹窗的 `exportColumns` 等状态封装在各自子组件内。父组件只用 `open`/`onClose` 控制显隐。

**3. 合并 selection state**

```ts
// ✅ 推荐：合并为一个对象
const [selection, setSelection] = useState<{ keys: React.Key[]; rows: ReimbursementItem[] }>({
  keys: [], rows: []
});
```

**4. 只读详情表单**

```tsx
<ModalForm
  submitter={{
    resetButtonProps: { style: { display: 'none' } },
    submitButtonProps: { style: { display: 'none' } },
  }}
  initialValues={record}
>
  {/* 所有表单项 disabled */}
</ModalForm>
```

**5. 导入导出用 xlsx**

```ts
import * as XLSX from 'xlsx';
```

常用 API：`aoa_to_sheet`、`book_new`/`book_append_sheet`、`writeFile`、`read`、`sheet_to_json`。

**6. 导入来源纪律** — Pro 系列从 `@ant-design/pro-components`，基础组件从 `antd`。绝对禁止混在同一个 import 里。

### 完整示例代码

```tsx
// pages/Normal_table/index.tsx
import React, { useRef, useState } from 'react';
import {
    ProTable, ProColumns, ProForm, ProFormText, ProFormDatePicker, ModalForm,
} from '@ant-design/pro-components';
import { Button, Col, FormInstance, Row, Space, Upload, Checkbox, message } from 'antd';
import { ReloadOutlined, ExportOutlined, ImportOutlined, InboxOutlined, DownloadOutlined } from '@ant-design/icons';
import type { UploadFile } from 'antd/lib/upload/interface';
import * as XLSX from 'xlsx';
import { fetchReimbursementList, importReimbursement } from '@/apis/NormalTable';
import type { ReimbursementItem, ReimbursementQuery } from '@/typings/NormalTable';

// ---------- 表头元信息 ----------
const FIELD_DEFS: { title: string; dataIndex: keyof ReimbursementItem }[] = [
    { title: '报销单号', dataIndex: 'reimbursementNo' },
    { title: '科目代码', dataIndex: 'subjectCode' },
    { title: '科目名称', dataIndex: 'subjectName' },
    { title: '销方名称or商户名称', dataIndex: 'merchantName' },
    { title: '金额', dataIndex: 'amount' },
    { title: '日期', dataIndex: 'date' },
    { title: '报销单申请人', dataIndex: 'applicant' },
    { title: '交易记录持卡人', dataIndex: 'cardHolder' },
    { title: '规则明细', dataIndex: 'ruleDetail' },
    { title: '详细描述', dataIndex: 'detailDesc' },
    { title: '消费内容', dataIndex: 'consumeContent' },
    { title: '事项类型', dataIndex: 'eventType' },
    { title: '申请时间', dataIndex: 'applyTime' },
    { title: '供应商名称', dataIndex: 'supplierName' },
    { title: '员工编号', dataIndex: 'employeeId' },
];

const DATE_FIELDS: string[] = ['date', 'applyTime'];

type ModalType = 'import' | 'export' | 'detail' | null;

// ==================== 批量导入弹窗 ====================
type ImportModalProps = { open: boolean; onClose: () => void; onImported: () => void; };

const ImportModal: React.FC<ImportModalProps> = ({ open, onClose, onImported }) => {
    const [uploadFile, setUploadFile] = useState<UploadFile | null>(null);
    const [parsedRows, setParsedRows] = useState<Record<string, any>[]>([]);

    const resetUpload = () => { setUploadFile(null); setParsedRows([]); };

    const handleDownloadTemplate = () => {
        const header = FIELD_DEFS.map((f) => f.title);
        const ws = XLSX.utils.aoa_to_sheet([header]);
        ws['!cols'] = header.map((t) => ({ wch: Math.max(t.length * 2 + 4, 14) }));
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, '报销数据导入模板');
        XLSX.writeFile(wb, '报销数据导入模板.xlsx');
    };

    const handleParseExcel = (file: File) => {
        const reader = new FileReader();
        reader.onload = (e) => {
            try {
                const data = new Uint8Array(e.target?.result as ArrayBuffer);
                const wb = XLSX.read(data, { type: 'array' });
                const ws = wb.Sheets[wb.SheetNames[0]];
                const rows: any[][] = XLSX.utils.sheet_to_json(ws, { header: 1 });
                if (rows.length < 2) { message.error('文件中没有数据行'); resetUpload(); return; }
                const headerRow = rows[0].map((h) => String(h).trim());
                const dataIndexByTitle = new Map(FIELD_DEFS.map((f) => [f.title, f.dataIndex]));
                const parsed: Record<string, any>[] = [];
                for (let i = 1; i < rows.length; i++) {
                    const row = rows[i];
                    if (!row || row.every((c) => c === null || c === undefined || c === '')) continue;
                    const obj: Record<string, any> = {};
                    headerRow.forEach((title, idx) => {
                        const di = dataIndexByTitle.get(title);
                        if (di && row[idx] !== undefined) obj[di] = row[idx];
                    });
                    parsed.push(obj);
                }
                setParsedRows(parsed);
                setUploadFile({ uid: String(Date.now()), name: file.name, status: 'done', size: file.size, type: file.type } as UploadFile);
                message.success(`解析成功,共 ${parsed.length} 条数据`);
            } catch (err) { message.error('文件解析失败'); resetUpload(); }
        };
        reader.onerror = () => { message.error('文件读取失败'); resetUpload(); };
        reader.readAsArrayBuffer(file);
        return false;
    };

    const handleFinish = async () => {
        if (parsedRows.length === 0) { message.warning('请先上传 Excel 文件'); return false; }
        const res = await importReimbursement(parsedRows);
        message.success(`导入成功,新增 ${res.imported} 条数据`);
        resetUpload(); onImported(); return true;
    };

    return (
        <ModalForm title="批量导入" visible={open}
            onVisibleChange={(v) => { if (!v) { resetUpload(); onClose(); } }}
            modalProps={{ destroyOnClose: true, width: 520 }}
            submitter={{ searchConfig: { submitText: '确定', resetText: '取消' }, submitButtonProps: { disabled: parsedRows.length === 0 } }}
            onFinish={handleFinish}
        >
            <Button type="link" icon={<DownloadOutlined />} onClick={handleDownloadTemplate} style={{ padding: 0 }}>
                下载导入模板
            </Button>
            <div style={{ marginTop: 12 }}>
                <Upload.Dragger accept=".xlsx,.xls" maxCount={1} fileList={uploadFile ? [uploadFile] : []}
                    beforeUpload={handleParseExcel} onRemove={resetUpload}>
                    <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                    <p className="ant-upload-text">点击或拖拽 Excel 文件到此区域上传</p>
                    <p className="ant-upload-hint">支持 .xlsx / .xls 格式,表头需与模板一致</p>
                </Upload.Dragger>
            </div>
        </ModalForm>
    );
};

// ==================== 批量导出弹窗 ====================
type ExportModalProps = { open: boolean; rows: ReimbursementItem[]; onClose: () => void; };

const ExportModal: React.FC<ExportModalProps> = ({ open, rows, onClose }) => {
    const [exportColumns, setExportColumns] = useState<string[]>([]);

    const handleFinish = async () => {
        if (exportColumns.length === 0) { message.warning('请至少选择一个导出列'); return false; }
        if (rows.length === 0) { message.warning('没有可导出的数据'); return false; }
        const aoa: any[][] = [exportColumns];
        rows.forEach((row) => {
            aoa.push(exportColumns.map((title) => {
                const def = FIELD_DEFS.find((f) => f.title === title);
                return def ? (row as any)[def.dataIndex] ?? '' : '';
            }));
        });
        const ws = XLSX.utils.aoa_to_sheet(aoa);
        ws['!cols'] = exportColumns.map((t) => ({ wch: Math.max(t.length * 2 + 4, 14) }));
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, '报销数据');
        XLSX.writeFile(wb, `报销数据导出_${rows.length}条.xlsx`);
        message.success(`已导出 ${rows.length} 条数据`);
        setExportColumns([]); return true;
    };

    return (
        <ModalForm title="批量导出" visible={open}
            onVisibleChange={(v) => { if (!v) { setExportColumns([]); onClose(); } }}
            modalProps={{ destroyOnClose: true, width: 520 }}
            submitter={{ searchConfig: { submitText: '确定', resetText: '取消' }, submitButtonProps: { disabled: exportColumns.length === 0 } }}
            onFinish={handleFinish}
        >
            <p style={{ color: '#999' }}>将导出已勾选的 <strong>{rows.length}</strong> 条数据,请选择需要导出的列:</p>
            <Checkbox.Group value={exportColumns} onChange={(checked) => setExportColumns(checked as string[])} style={{ width: '100%' }}>
                <Row>
                    {FIELD_DEFS.map((f) => (
                        <Col span={12} key={f.dataIndex as string} style={{ marginBottom: 8 }}>
                            <Checkbox value={f.title}>{f.title}</Checkbox>
                        </Col>
                    ))}
                </Row>
            </Checkbox.Group>
        </ModalForm>
    );
};

// ==================== 查看详情弹窗 ====================
type DetailModalProps = { open: boolean; record?: ReimbursementItem; onClose: () => void; };

const DetailModal: React.FC<DetailModalProps> = ({ open, record, onClose }) => {
    return (
        <ModalForm title="查看详情" visible={open}
            onVisibleChange={(v) => { if (!v) onClose(); }}
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

// ==================== 主组件 ====================
const ReimbursementList: React.FC = () => {
    const formRef = useRef<FormInstance>(null);
    const tableRef = useRef<any>(null);

    const [selection, setSelection] = useState<{ keys: React.Key[]; rows: ReimbursementItem[] }>({ keys: [], rows: [] });
    const [modal, setModal] = useState<{ type: ModalType; record?: ReimbursementItem }>({ type: null });
    const openModal = (type: Exclude<ModalType, null>, record?: ReimbursementItem) => setModal({ type, record });
    const closeModal = () => setModal({ type: null });
    const clearSelection = () => setSelection({ keys: [], rows: [] });

    const columns: ProColumns<ReimbursementItem>[] = [
        ...FIELD_DEFS.map((f) => ({
            title: f.title, dataIndex: f.dataIndex, key: f.dataIndex,
            width: f.dataIndex === 'amount' ? 100 : f.dataIndex === 'date' || f.dataIndex === 'applyTime' ? 160 : 120,
            valueType: (f.dataIndex === 'amount' ? 'money' : f.dataIndex === 'date' || f.dataIndex === 'applyTime' ? 'dateTime' : 'text') as any,
            ellipsis: ['merchantName', 'ruleDetail', 'detailDesc'].includes(f.dataIndex as string),
        })),
        { title: '操作', key: 'operation', width: 100, fixed: 'right',
            render: (_, record) => (
                <Button type="link" size="small" onClick={() => openModal('detail', record)}>查看详情</Button>
            ),
        },
    ];

    const renderFilter = () => (
        <ProForm formRef={formRef} layout="horizontal" submitter={false}
            onFinish={async () => { tableRef.current?.reload(); }}>
            <Row gutter={[16, 16]}>
                <Col span={6}><ProFormText name="reimbursementNo" label="报销单号" placeholder="请输入内容" /></Col>
                <Col span={6}><ProFormText name="subjectCode" label="科目代码" placeholder="请输入内容" /></Col>
                <Col span={6}><ProFormText name="subjectName" label="列支科目名称" placeholder="请输入内容" /></Col>
                <Col span={6}><ProFormText name="merchantName" label="销方名称or商户名称" placeholder="请输入内容" /></Col>
                <Col span={6}><ProFormText name="amount" label="金额" placeholder="请输入内容" /></Col>
                <Col span={6}><ProFormDatePicker name="date" label="日期" placeholder="请选择" fieldProps={{ style: { width: '100%' } }} /></Col>
                <Col span={6}><ProFormText name="applicant" label="报销单申请人" placeholder="请输入内容" /></Col>
                <Col span={6}><ProFormText name="cardHolder" label="交易记录持卡人" placeholder="请输入内容" /></Col>
                <Col span={6}><ProFormText name="ruleDetail" label="规则明细" placeholder="请输入内容" /></Col>
                <Col span={6}><ProFormText name="detailDesc" label="详细描述" placeholder="请输入内容" /></Col>
                <Col span={6}><ProFormText name="consumeContent" label="消费内容" placeholder="请输入内容" /></Col>
                <Col span={6}><ProFormText name="eventType" label="事项类型" placeholder="请输入内容" /></Col>
                <Col span={6}><ProFormDatePicker name="applyTime" label="申请时间" placeholder="请选择" fieldProps={{ style: { width: '100%' } }} /></Col>
                <Col span={6}><ProFormText name="supplierName" label="供应商名称" placeholder="请输入内容" /></Col>
                <Col span={6}><ProFormText name="employeeId" label="员工编号" placeholder="请输入内容" /></Col>
            </Row>
            <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
                <Button type="primary" onClick={async () => { await formRef.current?.validateFields(); tableRef.current?.reload(); }}>查询</Button>
                <Button onClick={() => { formRef.current?.resetFields(); tableRef.current?.reload(); }}>重置</Button>
            </div>
        </ProForm>
    );

    const toolBarRender = () => [
        <Button key="import" icon={<ImportOutlined />} onClick={() => openModal('import')}>批量导入</Button>,
        <Button key="export" icon={<ExportOutlined />} disabled={selection.keys.length === 0} onClick={() => openModal('export')}>批量导出</Button>,
        <Button key="refresh" icon={<ReloadOutlined />} onClick={() => tableRef.current?.reload()}>刷新</Button>,
    ];

    return (
        <div style={{ padding: 24 }}>
            {renderFilter()}
            <ProTable<ReimbursementItem>
                actionRef={tableRef} columns={columns} rowKey="id"
                request={async (params) => {
                    const { current, pageSize, ...rest } = params;
                    const formValues = formRef.current?.getFieldsValue() || {};
                    const res = await fetchReimbursementList({ page: current, pageSize, ...formValues, ...rest } as ReimbursementQuery);
                    return { data: res.data, success: res.success, total: res.total };
                }}
                pagination={{ pageSize: 10 }} search={false}
                rowSelection={{
                    selectedRowKeys: selection.keys,
                    onChange: (keys, rows) => setSelection({ keys, rows: rows as ReimbursementItem[] }),
                }}
                tableAlertRender={({ selectedRowKeys: selKeys }) =>
                    selKeys.length > 0 ? (<Space><span>已选择 {selKeys.length} 项</span><a onClick={clearSelection}>取消选择</a></Space>) : false
                }
                options={{ setting: true, reload: false, density: false, fullScreen: false }}
                toolBarRender={toolBarRender}
            />
            <ImportModal open={modal.type === 'import'} onClose={closeModal}
                onImported={() => { clearSelection(); closeModal(); tableRef.current?.reload(); }} />
            <ExportModal open={modal.type === 'export'} rows={selection.rows} onClose={closeModal} />
            <DetailModal open={modal.type === 'detail'} record={modal.record} onClose={closeModal} />
        </div>
    );
};

export default ReimbursementList;
```

---

## 3.2 模板二：多标签页查询表格

### 适用场景

- ☑ 同一业务域下有多个并列视图需切换
- ☑ 每个视图有独立的筛选条件、列定义和操作按钮
- ☑ 某些视图需要批量选择 + 批量删除
- ☑ 需要统一管理多个弹窗

### 核心结构

```
页面入口
├── 顶部 Banner
└── ProCard tabs
    ├── Tab 1: 管理事项
    │   ├── ProForm 筛选表单
    │   └── ProTable + rowSelection
    ├── Tab 2: 机构参数
    │   ├── ProForm 筛选表单
    │   └── ProTable
└── 弹窗（统一 modal state 管理）
    ├── ModalForm (新增)
    ├── ModalForm (编辑，key + destroyOnClose + initialValues)
    ├── ModalForm (删除确认)
    ├── ModalForm (批量删除确认)
    └── ModalForm (机构参数修改)
```

### 按标签页动态切换

**1. 筛选表单** — `renderFilterForm()` 根据 `activeTab` 返回不同表单项：
```tsx
const renderFilterForm = () => {
  const formItems =
    activeTab === 'institutionParams'
      ? [<ProFormText key="institutionNo" .../>, <ProFormText key="approver" .../>]
      : [<ProFormText key="managementItem" .../>, <ProFormSelect .../>, <ProFormSelect .../>];
  return <Row gutter={[16, 0]}>{/* map formItems */}<Col>{/* 查询/重置按钮 */}</Col></Row>;
};
```

**2. 工具栏** — `getToolBarRender()` 根据 `activeTab` 返回不同按钮。

**3. API 调用** — `request` 中传入 `tabType` 区分数据来源：
```tsx
request={async (params) => {
  const res = await fetchList({ ...params, tabType: activeTab, ...formValues });
}}
```

### Tab 切换钩子

```tsx
const handleTabChange = (key: string) => {
  setActiveTab(key as TabKey);
  setMgmtSelectedRowKeys([]);        // 清空选中行
  formRef.current?.resetFields();    // 重置筛选表单
  tableRef.current?.reload();        // 刷新表格
};
```

### 完整示例代码：多标签页管理平台

核心代码结构（完整示例参见原始文档）：

```tsx
type ModalState = { type: 'add' | 'edit' | 'delete' | 'batchDelete' | 'instEdit' | null; record?: ManagementItem | InstitutionParam; };

const ManagementPlatform: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabKey>('managementItems');
  const [modal, setModal] = useState<ModalState>({ type: null });

  const managementItemsColumns: ProColumns<ManagementItem>[] = [...];
  const institutionParamsColumns: ProColumns<InstitutionParam>[] = [...];

  const renderFilterForm = () => { /* 按 activeTab 动态返回表单项 */ };
  const getToolBarRender = () => { /* 按 activeTab 动态返回按钮 */ };

  return (
    <div className={cx('management-platform')}>
      <div className={cx('management-platform-banner')} />
      <ProCard tabs={{
        activeKey: activeTab, onChange: handleTabChange, type: 'card',
        items: [
          { key: 'managementItems', label: '管理事项',
            children: (
              <>
                <ProForm formRef={formRef} layout="horizontal" submitter={false}
                  style={{ marginBottom: 16 }}>{renderFilterForm()}</ProForm>
                <ProTable<ManagementItem>
                  actionRef={tableRef} columns={managementItemsColumns} rowKey="keyValue"
                  request={async (params) => { /* ... tabType: 'managementItems' */ }}
                  search={false} rowSelection={{...}}
                  options={{ setting: true, reload: false, density: false, fullScreen: false }}
                  toolBarRender={getToolBarRender}
                />
              </>
            ),
          },
          { key: 'institutionParams', label: '机构参数',
            children: (
              <>
                <ProForm formRef={formRef} layout="horizontal" submitter={false}
                  style={{ marginBottom: 16 }}>{renderFilterForm()}</ProForm>
                <ProTable<InstitutionParam>
                  actionRef={tableRef} columns={institutionParamsColumns} rowKey="id"
                  request={async (params) => { /* ... tabType: 'institutionParams' */ }}
                  search={false}
                  options={{ setting: true, reload: false, density: false, fullScreen: false }}
                  toolBarRender={getToolBarRender}
                />
              </>
            ),
          },
        ],
      }} style={{ marginTop: 16 }} />
      {/* 弹窗：新增 / 编辑 / 删除 / 批量删除 / 机构参数修改 */}
    </div>
  );
};
```
