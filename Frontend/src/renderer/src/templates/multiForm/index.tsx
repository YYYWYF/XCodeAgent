import React from 'react'
import {
  ProForm,
  ProFormText,
  ProFormDatePicker,
  ProFormSelect,
  ProFormList,
  ProCard,
} from '@ant-design/pro-components'
import { Button, Space, Form, message, Row, Col, Divider } from 'antd'

interface FieldDef {
  name: string;
  label: string;
  type: 'text' | 'date' | 'select';
  required: boolean;
  span?: number;
  options?: { label: string; value: string }[];
  group?: string;
  placeholder?: string;
}

const FIELD_DEFS: FieldDef[] = [
  // 基本信息
  { name: 'caseNumber', label: '案件编号', type: 'text', required: true, span: 12, group: '基本信息', placeholder: '请输入案件编号' },
  { name: 'registerDate', label: '登记日期', type: 'date', required: true, span: 12, group: '基本信息' },
  { name: 'handler', label: '经办人', type: 'text', required: true, span: 12, group: '基本信息', placeholder: '请输入经办人姓名' },
  {
    name: 'caseSource', label: '案件来源', type: 'select', required: true, span: 12, group: '基本信息', placeholder: '请选择案件来源',
    options: [
      { label: '巡查发现', value: '巡查发现' },
      { label: '群众举报', value: '群众举报' },
      { label: '上级交办', value: '上级交办' },
      { label: '部门移送', value: '部门移送' },
    ],
  },
  // 案件信息
  { name: 'caseTitle', label: '案件名称', type: 'text', required: true, span: 12, group: '案件信息', placeholder: '请输入案件名称' },
  {
    name: 'caseType', label: '案件类型', type: 'select', required: true, span: 12, group: '案件信息', placeholder: '请选择案件类型',
    options: [
      { label: '治安案件', value: '治安案件' },
      { label: '刑事案件', value: '刑事案件' },
      { label: '行政处罚', value: '行政处罚' },
      { label: '民事纠纷', value: '民事纠纷' },
    ],
  },
  { name: 'occurDate', label: '案发时间', type: 'date', required: true, span: 12, group: '案件信息' },
  { name: 'caseLocation', label: '案发地点', type: 'text', required: true, span: 12, group: '案件信息', placeholder: '请输入案发地点' },
  // 处理意见
  {
    name: 'opinionType', label: '处理方式', type: 'select', required: true, span: 12, group: '处理意见', placeholder: '请选择处理方式',
    options: [
      { label: '立案查处', value: '立案查处' },
      { label: '责令整改', value: '责令整改' },
      { label: '移送司法', value: '移送司法' },
      { label: '警告教育', value: '警告教育' },
    ],
  },
  { name: 'deadline', label: '办理期限', type: 'date', required: true, span: 12, group: '处理意见' },
  { name: 'opinionDesc', label: '处理意见说明', type: 'text', required: false, span: 12, group: '处理意见', placeholder: '请输入处理意见说明' },
];

interface ListItemFieldDef {
  name: string;
  label: string;
  type: 'text' | 'date' | 'select';
  required: boolean;
  span?: number;
  options?: { label: string; value: string }[];
  placeholder?: string;
}

const LIST_ITEMS: { listName: string; groupTitle: string; itemLabel: string; addButtonText: string; fields: ListItemFieldDef[] }[] = [
  {
    listName: 'involvedItems',
    groupTitle: '涉案物品明细',
    itemLabel: '物品',
    addButtonText: '＋ 添加物品',
    fields: [
      { name: 'itemName', label: '物品名称', type: 'text', required: true, span: 12, placeholder: '请输入物品名称' },
      { name: 'quantity', label: '数量', type: 'text', required: true, span: 12, placeholder: '请输入数量' },
      {
        name: 'unit', label: '计量单位', type: 'select', required: true, span: 12, placeholder: '请选择单位',
        options: [
          { label: '个', value: '个' },
          { label: '件', value: '件' },
          { label: '台', value: '台' },
          { label: '套', value: '套' },
          { label: '千克', value: '千克' },
        ],
      },
    ],
  },
];

const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

const submitForm = async (_values: Record<string, unknown>): Promise<{ success: boolean }> => {
  await delay(800);
  return { success: true };
};

const MultiForm: React.FC = () => {
  const [form] = Form.useForm();

  const onFinish = async (values: Record<string, unknown>) => {
    try {
      const res = await submitForm(values);
      if (!res.success) { message.error('提交失败'); return; }
      message.success('提交成功');
    } catch {
      message.error('提交失败，请稍后重试');
    }
  };

  // 按 group 分组字段
  const groups = new Map<string, FieldDef[]>();
  const ungrouped: FieldDef[] = [];
  for (const f of FIELD_DEFS) {
    if (f.group) {
      const list = groups.get(f.group) ?? [];
      list.push(f);
      groups.set(f.group, list);
    } else {
      ungrouped.push(f);
    }
  }

  const renderField = (f: FieldDef | ListItemFieldDef) => {
    const rules = f.required
      ? [{ required: true, message: f.type === 'text' ? `请输入${f.label}` : `请选择${f.label}` }]
      : undefined;

    if (f.type === 'date') {
      return (
        <ProFormDatePicker
          key={f.name}
          name={f.name}
          label={f.label}
          placeholder={f.placeholder ?? '请选择'}
          rules={rules}
          fieldProps={{ style: { width: '100%' } }}
        />
      );
    }
    if (f.type === 'select') {
      return (
        <ProFormSelect
          key={f.name}
          name={f.name}
          label={f.label}
          placeholder={f.placeholder ?? '请选择'}
          rules={rules}
          fieldProps={{ style: { width: '100%' } }}
          options={f.options ?? []}
        />
      );
    }
    return (
      <ProFormText
        key={f.name}
        name={f.name}
        label={f.label}
        placeholder={f.placeholder ?? '请输入内容'}
        rules={rules}
      />
    );
  };

  // 将字段列表按行分组（每行两个 Col span=12）
  const renderFieldRows = (fields: (FieldDef | ListItemFieldDef)[]) => {
    const rows: (FieldDef | ListItemFieldDef)[][] = [];
    for (let i = 0; i < fields.length; i += 2) {
      rows.push(fields.slice(i, i + 2));
    }
    return rows.map((row, ri) => (
      <Row gutter={16} key={ri}>
        {row.map((f) => (
          <Col span={f.span ?? 12} key={f.name}>
            {renderField(f)}
          </Col>
        ))}
        {row.length < 2 && <Col span={12} />}
      </Row>
    ));
  };

  return (
    <ProCard style={{ background: '#fff' }} bodyStyle={{ padding: 24, paddingBottom: 32 }}>
      <ProForm
        form={form}
        onFinish={onFinish}
        submitter={false}
        layout="horizontal"
        labelCol={{ flex: '108px', style: { paddingRight: 8 } }}
        wrapperCol={{ flex: 'auto', style: { maxWidth: '100%' } }}
        style={{ maxWidth: '100%' }}
      >
        {/* 无分组字段 */}
        {ungrouped.length > 0 && (
          <>
            <Divider style={{ color: '#8c8c8c', borderColor: '#d9d9d9', fontWeight: 500, fontSize: 16 }}>
              其他信息
            </Divider>
            {renderFieldRows(ungrouped)}
          </>
        )}

        {/* 分组字段 */}
        {Array.from(groups.entries()).map(([title, fields]) => (
          <React.Fragment key={title}>
            <Divider style={{ color: '#8c8c8c', borderColor: '#d9d9d9', fontWeight: 500, fontSize: 16 }}>
              {title}
            </Divider>
            {renderFieldRows(fields)}
          </React.Fragment>
        ))}

        {/* 动态列表 */}
        {LIST_ITEMS.map((list) => (
          <React.Fragment key={list.listName}>
            <Divider style={{ color: '#8c8c8c', borderColor: '#d9d9d9', fontWeight: 500, fontSize: 16, marginTop: 8 }}>
              {list.groupTitle}
            </Divider>
            <ProFormList
              name={list.listName}
              initialValue={[{}]}
              min={1}
              copyIconProps={false}
              creatorButtonProps={{
                creatorButtonText: list.addButtonText,
                type: 'dashed',
                block: true,
                icon: null,
                className: 'add-dynamic-item-btn',
                style: { borderColor: '#1677ff', color: '#1677ff', marginTop: 8 },
              }}
              itemRender={({ listDom, action }) => (
                <ProCard bordered style={{ marginBottom: 16 }} extra={action}>{listDom}</ProCard>
              )}
            >
              {renderFieldRows(list.fields)}
            </ProFormList>
          </React.Fragment>
        ))}

        <div style={{ textAlign: 'center', marginTop: 24 }}>
          <Space>
            <Button type="primary" onClick={() => form.submit()}>提交</Button>
            <Button onClick={() => form.resetFields()}>重置</Button>
          </Space>
        </div>
      </ProForm>
      <style>{`
        .add-dynamic-item-btn:hover {
          border-color: #4096ff !important;
          color: #4096ff !important;
          background: rgba(22,119,255,0.04) !important;
        }
      `}</style>
    </ProCard>
  );
};

export default MultiForm;
