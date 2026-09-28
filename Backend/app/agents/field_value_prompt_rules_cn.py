"""字段取值提示词中文对照，仅供查阅，不参与运行时注入。修改英文规则时同步维护本文件。"""

FIELD_VALUE_SEMANTICS = (
    "implementationDescription 是可选的接口级映射说明。按已确认说明在相应任务阶段实现业务校验、执行顺序、分支和返回处理；"
    "未填写或为空不增加要求。说明不授权新增数据源，也不能覆盖 API 契约、结构化映射或来源快照；冲突应报告 contract_mismatch。\n"
    "【字段取值契约】\n"
    "sourceBinding 标识已选定的数据表或外部 Operation；即使 externalApiBindings 为空、所有返回字段均为 "
    "value_mapping，也不能丢弃该来源或跳过已确认的上游调用。同一来源的多字段依赖不授权新增数据源、JOIN 或其他调用。"
    "物理元数据以已确认 sourceSnapshots 为准，禁止仅凭同名字段推断映射。\n"
    "完整读取 databaseQuery.items 条件树、databaseWrites、externalApiBindings 和 fieldMappings。"
    "databaseQuery 决定条件目标和取值，databaseWrites 决定写入列和取值，externalApiBindings 决定外部请求目标和取值。"
    "right.kind=endpoint 按准确的请求 location/path 取值；fixed 保留值及其类型；business 根据 "
    "endpointFields、builtinFields 和 businessDescription 实现业务处理。origin 仅表示编辑类别，不是运行时算法。"
    "同一接口入参可被多个目标复用。\n"
    "business_description 的业务校验或分支逻辑必须实现，但不自动产生物理字段绑定；value_mapping 无需 sourceFields。"
    "source_mapping 在来源结果可用后处理：direct 仅直接赋值；single_field_description / multi_field_description "
    "使用全部已声明 sourceFields、endpointFields、builtinFields 并执行 businessDescription。"
    "保留数组元素的对应关系，除非契约明确要求，不展开合并数组，也不只取首项。\n"
    "缺值不等于 false、0、空查询结果或上游失败；空字符串和 null 是否算缺值由已确认规则及字段约束决定。"
    "missingBehavior=error 按已确认错误契约失败；omit 省略目标或移除该查询条件；default 使用类型匹配的 defaultValue。"
    "规则明确允许某个依赖缺失并指定分支时执行该分支，不能先将所有依赖的空值一律短路为错误；"
    "其余未被规则处理的必需输入缺失再按 missingBehavior 处理。非法输入与缺值分别处理，不擅自用默认值掩盖校验失败。"
    "禁止省略必填外部参数或必填响应字段，禁止把上游超时或错误当成输入缺失。"
    "缺少关键算法、时区、舍入方式或错误语义时报告具体契约缺口，不能猜测补齐。"
)

FIELD_VALUE_PLANNING_RULES = (
    FIELD_VALUE_SEMANTICS,
    "【字段取值任务规划】在适用 Task 的中文编号执行步骤中写明目标 location/path 或列、输入依赖、"
    "处理规则、缺值策略及调用前或调用后阶段，不能仅写‘完成字段映射’。保持 manifest 的固定任务数量，"
    "不新增逐字段 Task、独立映射 Task 或 deliverable 种类。objects 负责共享类型边界；repository 负责参数化访问和 "
    "AND/OR 条件结构；upstream 负责传输 DTO、请求区段和响应结构；service 负责业务取值和响应组装；controller 负责 HTTP 绑定与委托。"
    "service 调用前计算 databaseQuery 的 right、databaseWrites 和 externalApiBindings，向访问层传递类型化值与显式存在状态。"
    "在既有负责阶段及授权路径内成对规划调用签名与必要值对象，不能要求单个执行 Task 越权补齐其他阶段文件。"
    "仅有 sourceBinding 的外部操作也必须保留 upstream 职责。update/delete 必须拒绝所有有效条件均被 omit 的情况。"
    "边界行为写入实现步骤，不额外生成测试、构建或验收任务。",
)

FIELD_VALUE_IMPLEMENTATION_RULES = (
    FIELD_VALUE_SEMANTICS + "\n\n"
    "【字段取值实现顺序】仅实现当前 stage 的职责。service 先校验业务入参、读取可信服务端上下文，"
    "计算调用前的查询值、写入值或外部入参，再调用已规划的 Repository / Client，最后构造应用响应。"
    "current_user_id 来自服务端认证主体，current_time 来自服务端时钟；上下文不可用时按缺值策略处理，"
    "不能信任客户端传入的用户身份。禁止用本次调用尚未返回的响应构造本次请求。保留 false/0，显式区分目标缺席与目标值为 null。\n"
    "repository 消费已计算的类型化值，保留 AND/OR 括号、重复列和重复参数；omit 后删除空分组，"
    "但不能将空组替换为恒真条件。update/delete 没有有效条件时拒绝执行。所有值必须参数化；"
    "contains/starts_with/ends_with 使用带通配符转义的参数化 LIKE；IN 数组非空，BETWEEN 必须为有序的两个值。"
    "upstream 将已计算值绑定到准确的 path/query/header/request_body；omit 表示不发送目标，不能替换成字符串 null 或空串。"
    "Client、Mapper、Controller 不重复执行业务规则。\n"
    "将已确认规则实现为普通类型化 Java 代码，不引入 eval、脚本引擎或新的运行时模型调用。"
    "必需语义缺失时返回 failed、failure_category=contract_mismatch；任务缺少必要类型边界或写入授权时返回 "
    "failed、failure_category=plan_mismatch。两者均携带具体 change_request，禁止自行扩展写入范围。\n\n"
)

# 以下对应两个注入入口中的配套约束，仅用于查阅。
SERVICE_STAGE_REFERENCE = (
        "service Task 统一负责 Endpoint fieldMappings、databaseQuery 取值规则、databaseWrites 和 "
        "externalApiBindings 的业务处理与最终响应组装。仅协调 manifest 已包含的 repository/upstream 分支，"
        "必须将已确认 externalApiBindings 的取值结果传给外部请求目标。层间类型及执行时机遵循字段取值任务规划规则。"
        "不新增独立映射 Task，不向内部 API 暴露上游传输类型，不实现 Controller 路由。"
)

API_BOUNDARY_REFERENCE = (
        "【接口边界】implementation_contract.api_design 是一个内部 Endpoint 已确认的字段映射契约。"
        "Controller 的方法、路径、请求和响应仍以内部 api_contract 为准；字段规则不能修改这些事实。"
        "按 databaseOperation 执行既定操作，仅消费已确认的来源及映射。\n\n"
)
