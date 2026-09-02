# Entity + PO + Mapper + MapperXML 模板

本文件给大模型提供后端业务实体持久化层的标准代码模板。平台已根据 TechnicalPlan 确定性生成这些文件，Agent 只需在需要时补充自定义方法/SQL。

## 领域实体 Entity

纯 POJO，用 Lombok `@Data`，只含业务字段，不含持久化注解。字段名 camelCase，类型从 entity_design 的 field_type 映射。

```java
package com.cmbchina.backend.project.domain.entity;

import lombok.Data;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 项目领域实体。
 */
@Data
public class Project {
    private Integer id;
    private String projectId;
    private String projectName;
    private String description;
    private BigDecimal budget;
    private String status;
    private LocalDateTime createdAt;
    private String createdBy;
    private LocalDateTime updatedAt;
    private String updatedBy;
}
```

### 字段类型映射规则

| TechnicalPlan field_type | Java 类型 |
| --- | --- |
| `string` / `text` | `String` |
| `integer` / `int` | `Integer` |
| `long` / `bigint` | `Long` |
| `decimal` / `double` / `float` | `BigDecimal` |
| `boolean` / `bool` | `Boolean` |
| `date` / `datetime` / `timestamp` | `LocalDateTime` |
| `json` | `String`（序列化后存储） |

### 命名转换规则

- 数据库表 `project` → 类名 `Project`；表 `project_member` → 类名 `ProjectMember`
- 数据库列 `project_name` → 字段名 `projectName`
- 数据库列 `is_deleted` → 字段名 `isDeleted`

## 持久化对象 PO

用 MyBatis-Plus 注解绑定数据库表。含审计字段和逻辑删除字段。

```java
package com.cmbchina.backend.project.infrastructure.po;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableLogic;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 数据库 project 表持久化对象。
 */
@Data
@TableName("project")
public class ProjectPO {
    @TableId(value = "id", type = IdType.AUTO)
    private Integer id;
    private String projectId;
    private String projectName;
    private String description;
    private BigDecimal budget;
    private String status;
    private LocalDateTime createdAt;
    private String createdBy;
    private LocalDateTime updatedAt;
    private String updatedBy;
    @TableField("is_deleted")
    @TableLogic(value = "0", delval = "1")
    private Boolean isDeleted;
    private LocalDateTime deletedAt;
    private String deletedBy;
}
```

### 注解说明

- `@TableName("表名")`：绑定数据库表，表名用下划线 snake_case
- `@TableId(value = "id", type = IdType.AUTO)`：主键，`IdType.AUTO` 为自增
- `@TableField("列名")`：字段名与列名不一致时用（如 `isDeleted` → `is_deleted`）
- `@TableLogic(value = "0", delval = "1")`：逻辑删除，`0` 为未删除，`1` 为已删除

### 审计字段（每个 PO 都要有）

```java
private LocalDateTime createdAt;
private String createdBy;
private LocalDateTime updatedAt;
private String updatedBy;
@TableField("is_deleted")
@TableLogic(value = "0", delval = "1")
private Boolean isDeleted;
private LocalDateTime deletedAt;
private String deletedBy;
```

## Mapper 接口

`extends BaseMapper<XxxPO>` + `@Mapper`。基础 CRUD（insert/deleteById/updateById/selectById/selectList/selectPage）由 MyBatis-Plus 自动提供，不需要写任何方法。

```java
package com.cmbchina.backend.project.infrastructure.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.cmbchina.backend.project.infrastructure.po.ProjectPO;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface ProjectMapper extends BaseMapper<ProjectPO> {
    // 基础 CRUD 由 BaseMapper 提供，需要自定义 SQL 时在此加方法
}
```

### 需要自定义 SQL 时

```java
@Mapper
public interface ProjectMapper extends BaseMapper<ProjectPO> {
    List<ProjectPO> findByStatusAndBudget(@Param("status") String status, @Param("minBudget") BigDecimal minBudget);
}
```

## Mapper XML

只含 namespace 声明。需要自定义 SQL 时在此写 `<select>`/`<insert>` 等。

```xml
<?xml version="1.0" encoding="UTF-8" ?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN" "https://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.cmbchina.backend.project.infrastructure.mapper.ProjectMapper">
    <!-- 基础 CRUD 由 MyBatis-Plus 自动实现，需要自定义 SQL 时在此添加 -->
</mapper>
```

### 需要自定义 SQL 时

```xml
<mapper namespace="com.cmbchina.backend.project.infrastructure.mapper.ProjectMapper">
    <select id="findByStatusAndBudget" resultType="com.cmbchina.backend.project.infrastructure.po.ProjectPO">
        SELECT * FROM project
        WHERE is_deleted = 0
          AND status = #{status}
          AND budget >= #{minBudget}
        ORDER BY created_at DESC
    </select>
</mapper>
```

### XML 文件放置位置

```
src/main/resources/mapper/<module>/<Entity>Mapper.xml
```

`<module>` 取业务模块名的小写（如 `project`、`order`）。`application.yml` 已配置 `mapper-locations: classpath*:mapper/**/*.xml`，所有 XML 会被自动扫描。
