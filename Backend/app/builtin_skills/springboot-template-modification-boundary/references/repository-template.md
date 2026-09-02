# Repository 接口 + 实现 + Converter 模板

本文件给大模型提供后端仓储层的标准代码模板。平台已根据 TechnicalPlan 确定性生成接口和基础实现，Agent 只需在需要时补充自定义方法。

## 仓储接口

定义业务需要的持久化方法签名。方法用领域实体（Entity），不用 PO。

```java
package com.cmbchina.backend.project.domain.repository;

import com.cmbchina.backend.common.page.PageResult;
import com.cmbchina.backend.project.domain.entity.Project;

public interface ProjectRepository {
    PageResult<Project> page(int current, int pageSize);

    Project findByProjectId(String projectId);

    void save(Project project);

    void update(Project project);

    void softDelete(Project project, String actor);
}
```

### 方法命名约定

| 业务操作 | 方法签名 |
| --- | --- |
| 分页查询 | `PageResult<Entity> page(int current, int pageSize)` |
| 按业务 ID 查 | `Entity findBy<Entity>Id(String <entity>Id)` |
| 新增 | `void save(Entity entity)` |
| 更新 | `void update(Entity entity)` |
| 逻辑删除 | `void softDelete(Entity entity, String actor)` |
| 自定义查询 | `List<Entity> findByXxx(...)` |

## 仓储实现

`@Repository` + `@RequiredArgsConstructor`，注入 Mapper 和 Converter，调用 `BaseMapper` 方法实现接口。

```java
package com.cmbchina.backend.project.infrastructure.repository.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.cmbchina.backend.common.page.PageResult;
import com.cmbchina.backend.project.domain.entity.Project;
import com.cmbchina.backend.project.domain.repository.ProjectRepository;
import com.cmbchina.backend.project.infrastructure.mapper.ProjectMapper;
import com.cmbchina.backend.project.infrastructure.po.ProjectPO;
import com.cmbchina.backend.project.infrastructure.repository.converter.ProjectConverter;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;

@Repository
@RequiredArgsConstructor
public class ProjectRepositoryImpl implements ProjectRepository {
    private final ProjectMapper projectMapper;
    private final ProjectConverter converter;

    @Override
    public PageResult<Project> page(int current, int pageSize) {
        Page<ProjectPO> page = new Page<>(current, pageSize);
        LambdaQueryWrapper<ProjectPO> query = new LambdaQueryWrapper<ProjectPO>()
                .orderByDesc(ProjectPO::getId);
        Page<ProjectPO> result = projectMapper.selectPage(page, query);
        return PageResult.of(result.getTotal(), (int) result.getCurrent(), (int) result.getSize(),
                result.getRecords(), converter::toEntity);
    }

    @Override
    public Project findByProjectId(String projectId) {
        ProjectPO po = projectMapper.selectOne(new LambdaQueryWrapper<ProjectPO>()
                .eq(ProjectPO::getProjectId, projectId));
        return converter.toEntity(po);
    }

    @Override
    public void save(Project project) {
        ProjectPO po = converter.toPO(project);
        projectMapper.insert(po);
        project.setId(po.getId());
    }

    @Override
    public void update(Project project) {
        projectMapper.update(null, new LambdaUpdateWrapper<ProjectPO>()
                .eq(ProjectPO::getProjectId, project.getProjectId())
                .set(ProjectPO::getProjectName, project.getProjectName())
                .set(ProjectPO::getDescription, project.getDescription())
                .set(ProjectPO::getBudget, project.getBudget())
                .set(ProjectPO::getStatus, project.getStatus())
                .set(ProjectPO::getUpdatedBy, project.getUpdatedBy()));
    }

    @Override
    public void softDelete(Project project, String actor) {
        projectMapper.update(null, new LambdaUpdateWrapper<ProjectPO>()
                .eq(ProjectPO::getProjectId, project.getProjectId())
                .set(ProjectPO::getIsDeleted, true)
                .set(ProjectPO::getDeletedAt, LocalDateTime.now())
                .set(ProjectPO::getDeletedBy, actor)
                .set(ProjectPO::getUpdatedBy, actor));
    }
}
```

### 实现要点

- `page`：用 `selectPage` + `LambdaQueryWrapper`，通过 `PageResult.of(total, current, size, records, converter::toEntity)` 转换
- `findByXxx`：用 `selectOne` + `LambdaQueryWrapper.eq`
- `save`：`converter.toPO` 后 `insert`，回写自增 id
- `update`：用 `LambdaUpdateWrapper` 按业务 id 定位，`.set` 各字段
- `softDelete`：用 `LambdaUpdateWrapper` 设 `isDeleted=true` + 审计字段

## PO 转换器 Converter

MapStruct `@Mapper(componentModel = "spring")` 接口，Entity ↔ PO 互转。字段名一致时自动映射，不一致用 `@Mapping`。

```java
package com.cmbchina.backend.project.infrastructure.repository.converter;

import com.cmbchina.backend.project.domain.entity.Project;
import com.cmbchina.backend.project.infrastructure.po.ProjectPO;
import org.mapstruct.Mapper;

import java.util.List;

@Mapper(componentModel = "spring")
public interface ProjectConverter {
    Project toEntity(ProjectPO source);

    ProjectPO toPO(Project source);

    List<Project> toEntities(List<ProjectPO> source);
}
```

### MapStruct 说明

- `componentModel = "spring"`：生成 Spring Bean，可 `@Autowired`/`@RequiredArgsConstructor` 注入
- 字段名一致自动映射（Entity 的 `projectName` ↔ PO 的 `projectName`）
- 字段名不一致用 `@Mapping(target = "xxx", source = "yyy")`
- PO 的审计字段（isDeleted/deletedAt/deletedBy）在 Entity 里不存在，MapStruct 自动忽略
- **不要手写转换逻辑**，MapStruct 编译时自动生成实现类
