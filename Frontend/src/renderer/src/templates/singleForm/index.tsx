import React, { useState, useRef } from 'react'
import { Row, Col, Button, Space, Alert, message } from 'antd'
import {
  ProForm,
  ProFormSelect,
  ProFormDateRangePicker,
  ProFormTextArea,
  ProFormRate,
  ProCard,
} from '@ant-design/pro-components'
import type { FormInstance } from 'antd/es/form'

// ===================== 常量 =====================
const TEXT_MAX_LENGTH = 500

// ===================== 类型定义 =====================
interface EmployeeItem {
  label: string
  value: string
  id: string
  name: string
  empNo: string
}

// Mock 员工数据源：一份完整的"员工池"，初始时展示给下拉；搜索时按关键词
// 在 label / name / empNo 三个字段里过滤。后续接真实接口时，把这份常量删掉、
// 把 handleEmployeeSearch 改成调接口 + setEmployeeOptions(响应) 即可。
const MOCK_EMPLOYEES: EmployeeItem[] = [
  { label: '张三(10001)', value: '10001', id: '10001', name: '张三', empNo: '10001' },
  { label: '李四(10002)', value: '10002', id: '10002', name: '李四', empNo: '10002' },
  { label: '王五(10003)', value: '10003', id: '10003', name: '王五', empNo: '10003' },
  { label: '赵六(10004)', value: '10004', id: '10004', name: '赵六', empNo: '10004' },
  { label: '钱七(10005)', value: '10005', id: '10005', name: '钱七', empNo: '10005' },
]

interface SelfEvaluationFormValues {
  employeeKeyword: string
  rotation_range: [string, string]
  task_goal: string
  mentor_teaching_content: string
  achievement: string
  suggestions_for_training: string
  other_reflections: string
  /** 自评完成度（1-5 星）。语义对齐"自我评价"，让用户对本双周完成情况打分。 */
  completion_rating?: number
  practical_activities?: string
  self_study_content?: string
  shortcomings?: string
}

const SingleForm: React.FC = () => {
  // ===================== 自我评价表单 =====================
  const selfForm = useRef<FormInstance<SelfEvaluationFormValues>>()
  const [selectedEmployee, setSelectedEmployee] = useState<EmployeeItem | null>(null)
  // 初始就用 mock 员工池，让下拉聚焦时立即有内容；搜索时按关键词在池里本地过滤。
  const [employeeOptions, setEmployeeOptions] = useState<EmployeeItem[]>(MOCK_EMPLOYEES)
  const [employeeSearching, setEmployeeSearching] = useState(false)
  const [selfSubmitting, setSelfSubmitting] = useState(false)
  const [selfSuccessMessage, setSelfSuccessMessage] = useState<string | null>(null)

  // ===================== 员工搜索 =====================
  const handleEmployeeSearch = async (keyword: string) => {
    setEmployeeSearching(true)
    // 👉 替换成真实接口调用；当前用本地过滤模拟异步搜索的 loading 体验。
    await new Promise((r) => setTimeout(r, 300))
    const trimmed = keyword.trim().toLowerCase()
    // 空关键词恢复完整列表（用户清空了搜索框）。
    if (!trimmed) {
      setEmployeeOptions(MOCK_EMPLOYEES)
      setEmployeeSearching(false)
      return
    }
    // 在 label（"张三(10001)"）、name（"张三"）、empNo（"10001"）三个字段任一命中即可。
    setEmployeeOptions(
      MOCK_EMPLOYEES.filter((item) =>
        item.label.toLowerCase().includes(trimmed) ||
        item.name.toLowerCase().includes(trimmed) ||
        item.empNo.includes(trimmed),
      ),
    )
    setEmployeeSearching(false)
  }

  const handleEmployeeChange = (value: string, option?: EmployeeItem | EmployeeItem[]) => {
    if (Array.isArray(option)) return
    setSelectedEmployee(option ?? null)
  }

  // ===================== 提交 & 重置 =====================
  const handleSelfSubmit = async (values: SelfEvaluationFormValues) => {
    setSelfSubmitting(true)
    try {
      // 👉 在这里调用后端接口
      console.log('自我评价表单值：', values)
      await new Promise((r) => setTimeout(r, 1000))
      setSelfSuccessMessage('自我评价提交成功！')
      // 重置表单
      selfForm.current?.resetFields()
      setSelectedEmployee(null)
      setEmployeeOptions([])
    } catch (e) {
      message.error('提交失败，请重试')
    } finally {
      setSelfSubmitting(false)
    }
  }

  const handleResetSelf = () => {
    selfForm.current?.resetFields()
    setSelectedEmployee(null)
    setEmployeeOptions([])
    setSelfSuccessMessage(null)
  }

  return (
    <div style={{ padding: 24 }}>
      <Row gutter={16}>
        <Col xs={24} lg={12}>
          <ProForm<SelfEvaluationFormValues>
            formRef={selfForm}
            layout="vertical"
            submitter={false}
            onFinish={handleSelfSubmit}
          >
            <ProCard title="双周自我评价" bordered headerBordered style={{ marginBottom: 16 }}>
              <ProFormSelect
                name="employeeKeyword"
                label="新员工姓名工号"
                placeholder="请输入新员工姓名或工号进行模糊搜索"
                showSearch
                allowClear
                rules={[
                  { required: true, message: '请选择新员工' },
                  {
                    validator: async (_rule, value: string | undefined) => {
                      if (value && !selectedEmployee) {
                        throw new Error('请从搜索结果中选择一位新员工')
                      }
                    },
                  },
                ]}
                fieldProps={{
                  filterOption: false,
                  loading: employeeSearching,
                  listHeight: 320,
                  notFoundContent: employeeSearching ? '搜索中…' : '未找到匹配的新员工',
                  onSearch: handleEmployeeSearch,
                  onChange: handleEmployeeChange,
                  options: employeeOptions,
                }}
                extra="输入姓名或工号后按「姓名/工号」展示最多 10 条结果，可滚动查看更多"
              />

              <ProFormDateRangePicker
                name="rotation_range"
                label="轮岗时间区间"
                placeholder={['开始日期', '结束日期']}
                rules={[{ required: true, message: '请选择轮岗时间区间' }]}
                fieldProps={{ format: 'YYYY-MM-DD', style: { width: '100%' } }}
              />

              <ProFormTextArea
                name="task_goal"
                label="任务目标"
                placeholder="请输入本双周的任务目标"
                rules={[{ required: true, whitespace: true, message: '请输入任务目标' }]}
                fieldProps={{
                  autoSize: { minRows: 3, maxRows: 8 },
                  maxLength: TEXT_MAX_LENGTH,
                  showCount: true,
                }}
              />

              <ProFormTextArea
                name="mentor_teaching_content"
                label="导师教学内容"
                placeholder="请输入导师在本双周教授的内容"
                rules={[{ required: true, whitespace: true, message: '请输入导师教学内容' }]}
                fieldProps={{
                  autoSize: { minRows: 3, maxRows: 8 },
                  maxLength: TEXT_MAX_LENGTH,
                  showCount: true,
                }}
              />

              <ProFormTextArea
                name="achievement"
                label="成效（已掌握）"
                placeholder="请输入本双周已掌握的成效"
                rules={[{ required: true, whitespace: true, message: '请输入成效（已掌握）' }]}
                fieldProps={{
                  autoSize: { minRows: 3, maxRows: 8 },
                  maxLength: TEXT_MAX_LENGTH,
                  showCount: true,
                }}
              />

              <ProFormTextArea
                name="suggestions_for_training"
                label="对新员工轮岗培养的意见建议"
                placeholder="请输入对轮岗培养的意见建议"
                rules={[{ required: true, whitespace: true, message: '请输入对新员工轮岗培养的意见建议' }]}
                fieldProps={{
                  autoSize: { minRows: 3, maxRows: 8 },
                  maxLength: TEXT_MAX_LENGTH,
                  showCount: true,
                }}
              />

              <ProFormTextArea
                name="other_reflections"
                label="其他心得体会"
                placeholder="请输入其他心得体会"
                rules={[{ required: true, whitespace: true, message: '请输入其他心得体会' }]}
                fieldProps={{
                  autoSize: { minRows: 3, maxRows: 8 },
                  maxLength: TEXT_MAX_LENGTH,
                  showCount: true,
                }}
              />

              <ProFormRate
                name="completion_rating"
                label="自评完成度"
                // 星级评分（Rate）：1-5 星，半星可选。语义对齐"自我评价"场景，
                // 让用户对本双周完成情况直观打分，比多行文本更轻量。
                fieldProps={{
                  allowHalf: true,
                }}
                extra="选择 1-5 星（支持半星），表示你对本双周工作完成度的自评"
              />

              {selfSuccessMessage ? (
                <Alert
                  type="success"
                  showIcon
                  closable
                  onClose={() => setSelfSuccessMessage(null)}
                  style={{ marginBottom: 16 }}
                  message={selfSuccessMessage}
                />
              ) : null}

              <div style={{ textAlign: 'right' }}>
                <Space>
                  <Button onClick={handleResetSelf}>重置</Button>
                  <Button type="primary" loading={selfSubmitting} onClick={() => selfForm.current?.submit()}>
                    提交自我评价
                  </Button>
                </Space>
              </div>
            </ProCard>
          </ProForm>
        </Col>
      </Row>
    </div>
  )
}

export default SingleForm
