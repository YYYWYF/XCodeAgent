# -*- coding: utf-8 -*-
"""一次性脚本：修复 tables 数组的行尾括号与被清空的已添加表字段，用后即删。"""
import io

path = 'src/renderer/src/components/DataSources/catalog.ts'
raw = io.open(path, encoding='utf-8').read()

# 1) 行尾三层闭合 → 两层：所有 discoveredTable 行的列数组本身是 [[...]]，行尾应为 ]]）。
#    损坏形态为列数组后多了一个 ]，即 ...]]])  →  ...]])
assert raw.count(']]]),') >= 40, 'unexpected tail count: %d' % raw.count(']]]),')
raw = raw.replace(']]]),', ']]),')

# 2) 重建被清空的已添加表字段（按段区分两个库的同名 recheck 表）
recheck_cols = "[['id', '回检编号'], ['project_name', '关联项目'], ['status', '处理状态'], ['created_by', '申请人工号'], ['created_at', '提交时间']]"
audit_cols = "[['id', '轨迹编号'], ['recheck_id', '回检编号'], ['result', '审核结论'], ['reviewer_id', '审核人工号'], ['created_at', '审核时间']]"
user_cols = "[['id', '员工工号'], ['name', '姓名'], ['department', '所属部门']]"
comment_cols = "[['id', '备注编号'], ['recheck_id', '回检编号'], ['content', '备注内容'], ['created_by', '备注人工号'], ['created_at', '备注时间']]"

start_proj = raw.index("id: 'project-db'")
seg_re = raw[raw.index("id: 'wuhan-recheck-db'"):start_proj]
seg_pj = raw[start_proj:]

broken_re = "discoveredTable('recheck', '需求回检记录', [['', ''], ['', ''], ['', ''], ['', ''], ['', '']], true),"
broken_audit = "discoveredTable('recheck_audit', '审核轨迹', [['', ''], ['', ''], ['', ''], ['', ''], ['', '']], true),"
broken_user = "discoveredTable('user', '员工', [['', ''], ['', ''], ['', '']], true),"
broken_comment = "discoveredTable('recheck_comment', '回检备注', [['', ''], ['', ''], ['', ''], ['', ''], ['', '']], true),"

fixed_re = "discoveredTable('recheck', '需求回检记录', %s, true)," % recheck_cols
fixed_audit = "discoveredTable('recheck_audit', '审核轨迹', %s, true)," % audit_cols
fixed_user = "discoveredTable('user', '员工', %s, true)," % user_cols
fixed_comment = "discoveredTable('recheck_comment', '回检备注', %s, true)," % comment_cols

assert seg_re.count(broken_re) == 1, 'recheck broken'
assert seg_re.count(broken_audit) == 1, 'audit broken'
assert seg_re.count(broken_user) == 1, 'user broken'
assert seg_pj.count(broken_comment) == 1, 'comment broken'

seg_re = seg_re.replace(broken_re, fixed_re).replace(broken_audit, fixed_audit).replace(broken_user, fixed_user)
seg_pj = seg_pj.replace(broken_comment, fixed_comment)

raw = raw[:raw.index("id: 'wuhan-recheck-db'")] + seg_re + raw[start_proj:]
io.open(path, 'w', encoding='utf-8', newline='\n').write(raw)
print('ok')
