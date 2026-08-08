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

/*
 * ╔══════════════════════════════════════════════════════════╗
 * ║  多分组表单页面模板（骨架）                              ║
 * ║                                                        ║
 * ║  本模板提供分组表单 UI 框架：                            ║
 * ║  · Divider 分隔的多个信息分组                           ║
 * ║  · ProFormList 动态列表项（可增删）                     ║
 * ║  · 表单验证规则                                         ║
 * ║  · 提交/重置按钮                                        ║
 * ║                                                        ║
 * ║  使用时请根据项目计划填充：                              ║
 * ║  ① 表单分组 —— 每个分组由 Divider + 字段组成           ║
 * ║  ② 每个分组的字段列表 —— 字段名、标签、类型、验证      ║
 * ║  ③ 动态列表项（ProFormList）的字段与验证                ║
 * ║  ④ 提交接口 —— 调用 api_contracts 中的创建/更新接口    ║
 * ║  ⑤ 编辑态 —— 如需回填数据，实现 initialValues 加载     ║
 * ╚══════════════════════════════════════════════════════════╝
 */

// ============================================================
// ① TODO: 定义表单字段（按分组编排）
// ============================================================
// 每个字段定义：
//   name     —— 字段名（表单 key）
//   label    —— 字段标签
//   type     —— 'text' | 'date' | 'select'
//   required —— 是否必填
//   span     —— 栅格宽度（默认 12，即半行）
//   options  —— select 时的选项 [{ label, value }]
//   group    —— 所属分组标题
//   placeholder —— 输入提示文字

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
  // TODO: 在此添加字段定义，例如：
  // { name: 'name', label: '名称', type: 'text', required: true, span: 12, group: '基本信息', placeholder: '请输入名称' },
  // { name: 'date', label: '日期', type: 'date', required: true, span: 12, group: '基本信息' },
  // { name: 'type', label: '类型', type: 'select', required: true, span: 12, group: '基本信息', options: [{ label: '类型A', value: 'A' }] },
];

// ============================================================
// ② TODO: 动态列表项字段定义（ProFormList 内每一行的字段）
// ============================================================
interface ListItemFieldDef {
  name: string;
  label: string;
  type: 'text' | 'date' | 'select';
  required: boolean;
  span?: number;
  options?: { label: string; value: string }[];
  placeholder?: string;
}

// 动态列表的名称（表单 key 中 ProFormList 的 name）
const LIST_ITEMS: { listName: string; groupTitle: string; itemLabel: string; addButtonText: string; fields: ListItemFieldDef[] }[] = [
  // TODO: 在此添加动态列表定义，例如：
  // {
  //   listName: 'items',
  //   groupTitle: '明细列表',
  //   itemLabel: '明细',
  //   addButtonText: '＋ 添加明细',
  //   fields: [
  //     { name: 'name', label: '名称', type: 'text', required: true, span: 12 },
  //     { name: 'price', label: '价格', type: 'text', required: true, span: 12 },
  //   ],
  // },
];

// ============================================================
// ③ TODO: 提交接口
// ============================================================
const submitForm = async (values: Record<string, unknown>): Promise<{ success: boolean }> => {
  // TODO: 调用项目计划声明的创建或更新接口
  // 根据是否为编辑态选择 POST 或 PUT
  void values;
  throw new Error('submitForm: 请替换为实际 API 调用');
};

// ============================================================
// 以下为 UI 框架代码，无需修改
// ============================================================

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
    const rules = f.required ? [{ required: true, message: `请输入${f.label}` }] : undefined;

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
              {/* TODO: ④ 如需标题可在此修改 */}
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
