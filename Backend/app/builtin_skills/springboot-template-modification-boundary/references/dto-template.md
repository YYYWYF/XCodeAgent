# DTO + Assembler 模板

本文件给大模型提供后端应用层 DTO 和 Assembler 的标准代码模板。平台已根据 TechnicalPlan 确定性生成 DTO 字段，Agent 只需在需要时补充校验注解和 Assembler 转换逻辑。

## 响应 DTO

返回给前端的响应数据载体。用 `@Data` + `@NoArgsConstructor` + `@AllArgsConstructor`。

```java
package com.cmbchina.backend.project.application.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.time.LocalDateTime;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class ProjectDTO {
    private String projectId;
    private String projectName;
    private String description;
    private BigDecimal budget;
    private String status;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
```

## 请求 DTO（新增/更新）

接收前端请求的数据载体。用 `@Data` + `javax.validation` 注解。

```java
package com.cmbchina.backend.project.application.dto;

import lombok.Data;

import javax.validation.constraints.NotBlank;
import javax.validation.constraints.Size;
import java.math.BigDecimal;

@Data
public class ProjectUpsertDTO {
    @NotBlank
    @Size(max = 64)
    private String projectName;

    @Size(max = 256)
    private String description;

    private BigDecimal budget;
}
```

### 校验注解（Agent 按契约补充）

| 约束 | 注解 |
| --- | --- |
| 非空字符串 | `@NotBlank` |
| 非空对象 | `@NotNull` |
| 长度限制 | `@Size(max = N)` |
| 数值范围 | `@Min` / `@Max` |
| 正则 | `@Pattern(regexp = "...")` |

> Controller 方法参数加 `@Valid` 触发校验，校验失败由 `BaseExceptionHandler` 统一处理。

## 状态变更 DTO

```java
package com.cmbchina.backend.project.application.dto;

import lombok.Data;

import javax.validation.constraints.NotNull;

@Data
public class ProjectStatusDTO {
    @NotNull
    private Boolean active;
}
```

## Assembler

`@Component`，DTO ↔ Entity 互转，含业务组装逻辑。

```java
package com.cmbchina.backend.project.application.assembler;

import com.cmbchina.backend.project.application.dto.ProjectDTO;
import com.cmbchina.backend.project.application.dto.ProjectUpsertDTO;
import com.cmbchina.backend.project.domain.entity.Project;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;

@Component
public class ProjectAssembler {
    public ProjectDTO toDTO(Project project) {
        if (project == null) {
            return null;
        }
        return new ProjectDTO(
                project.getProjectId(),
                project.getProjectName(),
                project.getDescription(),
                project.getBudget(),
                project.getStatus(),
                project.getCreatedAt(),
                project.getUpdatedAt()
        );
    }

    public Project toCreatedProject(ProjectUpsertDTO request, String projectId, String actor) {
        Project project = new Project();
        project.setProjectId(projectId);
        project.setProjectName(request.getProjectName().trim());
        project.setDescription(trimToNull(request.getDescription()));
        project.setBudget(request.getBudget());
        project.setStatus("active");
        project.setCreatedBy(actor);
        return project;
    }

    private String trimToNull(String value) {
        return value == null || value.trim().isEmpty() ? null : value.trim();
    }
}
```

### Assembler 要点

- `toDTO`：Entity → DTO，只取前端需要的字段，null 安全
- `toCreatedProject`：请求 DTO → 新建 Entity，设默认值（status/createdBy）
- `trimToNull`：工具方法，空字符串转 null
- **不要用 MapStruct 做 DTO ↔ Entity 转换**——Assembler 是手写的，因为涉及业务默认值和字段筛选，PO ↔ Entity 才用 MapStruct（Converter）
