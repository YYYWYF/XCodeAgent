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

const MultiForm: React.FC = () => {
  const [form] = Form.useForm()

  const onFinish = async (_values: Record<string, unknown>) => {
    message.success('提交成功（模拟）')
  }

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
        <Divider style={{ color: '#8c8c8c', borderColor: '#d9d9d9', fontWeight: 500, fontSize: 16 }}>
          执法文书信息
        </Divider>

        <Row gutter={16}>
          <Col span={12}>
            <ProFormText name="authorityName" label="有权机关名称"
              placeholder="请输入内容"
              rules={[{ required: true, message: '请输入有权机关名称' }]}
            />
          </Col>
          <Col span={12}>
            <ProFormText name="authorityType" label="有权机关类型"
              placeholder="请输入内容"
              rules={[{ required: true, message: '请输入有权机关类型' }]}
            />
          </Col>
        </Row>

        <Row gutter={16}>
          <Col span={12}>
            <ProFormText name="docNumber" label="法律文书号"
              placeholder="请输入内容"
              rules={[{ required: true, message: '请输入法律文书号' }]}
            />
          </Col>
          <Col span={12}>
            <ProFormText name="sealContent" label="公章内容" placeholder="请输入内容" />
          </Col>
        </Row>

        <Row gutter={16}>
          <Col span={12}>
            <ProFormText name="noticeName" label="通知书名称" placeholder="请输入内容" />
          </Col>
          <Col span={12}>
            <ProFormText name="enforcementOfficers"
              label={
                <span>执法人员姓名
                </span>
              }
              placeholder="请输入内容"
              rules={[{ required: true, message: '请输入执法人员姓名' }]}
            />
          </Col>
        </Row>

        <Row gutter={16}>
          <Col span={12}>
            <ProFormText name="contactPerson" label="联系人" placeholder="请输入内容" />
          </Col>
          <Col span={12}>
            <ProFormText name="contactNumber1" label="联系号码1"
              placeholder="请输入内容"
              rules={[{ required: true, message: '请输入联系号码1' }]}
            />
          </Col>
        </Row>

        <Row gutter={16}>
          <Col span={12}>
            <ProFormText name="contactNumber2" label="联系号码2" placeholder="请输入内容" />
          </Col>
          <Col span={12} />
        </Row>

        <Divider style={{ color: '#8c8c8c', borderColor: '#d9d9d9', fontWeight: 500, fontSize: 16, marginTop: 8 }}>
          执法文书附件信息（如无附件可忽略）
        </Divider>

        <Row gutter={16}>
          <Col span={12}>
            <ProFormText name="attachmentDocNumber" label="附件法律文书号" placeholder="请输入内容" />
          </Col>
          <Col span={12}>
            <ProFormText name="attachmentSealContent" label="附件公章内容" placeholder="请输入内容" />
          </Col>
        </Row>

        <Divider style={{ color: '#8c8c8c', borderColor: '#d9d9d9', fontWeight: 500, fontSize: 16, marginTop: 8 }}>
          执法人员证件信息
        </Divider>

        <ProFormList
          name="officers"
          initialValue={[{}]}
          min={1}
          copyIconProps={false}
          creatorButtonProps={{
            creatorButtonText: '＋ 添加执法人员',
            type: 'dashed',
            block: true,
            icon: null,
            className: 'add-officer-btn',
            style: { borderColor: '#1677ff', color: '#1677ff', marginTop: 8 },
          }}
          itemRender={({ listDom, action }) => (
            <ProCard bordered style={{ marginBottom: 16 }} extra={action}>{listDom}</ProCard>
          )}
        >
          <Row gutter={16}>
            <Col span={12}>
              <ProFormText name="name" label="执法人员姓名" placeholder="请输入"
                rules={[{ required: true, message: '请输入执法人员姓名' }]}
              />
            </Col>
            <Col span={12}>
              <ProFormText name="issuer" label="发证机关" placeholder="请输入内容" />
            </Col>
          </Row>

          <Row gutter={16}>
            <Col span={12}>
              <ProFormSelect name="certificateType" label="证件类型" placeholder="请选择"
                options={[{ label: '税务检查证', value: '税务检查证' }, { label: '身份证', value: '身份证' }]}
                rules={[{ required: true, message: '请选择证件类型' }]}
              />
            </Col>
            <Col span={12}>
              <ProFormDatePicker name="expiryDate" label="证件到期日" placeholder="请选择"
                rules={[{ required: true, message: '请选择证件到期日' }]}
                fieldProps={{
                  renderExtraFooter: () => (
                    <span style={{ fontSize: 12, color: '#999' }}>日期格式为 yyyy-mm-dd，长期录入 9999-12-31</span>
                  ),
                }}
              />
            </Col>
          </Row>

          <Row gutter={16}>
            <Col span={12}>
              <ProFormText name="certificateNumber" label="证件号码" placeholder="请输入内容"
                rules={[{ required: true, message: '请输入证件号码' }]}
              />
            </Col>
            <Col span={12} />
          </Row>
        </ProFormList>

        <div style={{ textAlign: 'center', marginTop: 24 }}>
          <Space>
            <Button type="primary" onClick={() => form.submit()}>提交</Button>
            <Button onClick={() => form.resetFields()}>重置</Button>
          </Space>
        </div>
      </ProForm>
      <style>{`
        .add-officer-btn:hover {
          border-color: #4096ff !important;
          color: #4096ff !important;
          background: rgba(22,119,255,0.04) !important;
        }
      `}</style>
    </ProCard>
  )
}

export default MultiForm
