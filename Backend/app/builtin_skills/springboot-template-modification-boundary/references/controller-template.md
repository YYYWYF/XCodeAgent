# Controller 骨架模板

本文件提供 REST Controller 的参考模板。Agent 按已确认 Endpoint 的 HTTP Method、Path、请求/响应 Schema，在当前任务授权路径内创建或最小调整 Controller，业务逻辑留在 ApplicationService。

## Controller 骨架

`@RestController` + `@RequiredArgsConstructor` + `@RequestMapping`，注入 ApplicationService，返回 `ResponseEntity<T>`。

```java
package com.cmbchina.backend.project.adapter.web;

import com.cmbchina.backend.common.page.PageParam;
import com.cmbchina.backend.common.page.PageResult;
import com.cmbchina.backend.common.response.ResponseEntity;
import com.cmbchina.backend.project.application.dto.ProjectDTO;
import com.cmbchina.backend.project.application.dto.ProjectStatusDTO;
import com.cmbchina.backend.project.application.dto.ProjectUpsertDTO;
import com.cmbchina.backend.project.application.service.ProjectApplicationService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import javax.validation.Valid;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/projects")
public class ProjectController {

    private final ProjectApplicationService projectService;

    @GetMapping
    public ResponseEntity<PageResult<ProjectDTO>> list(@ModelAttribute PageParam query) {
        return ResponseEntity.success(projectService.listProjects(query));
    }

    @GetMapping("/{projectId}")
    public ResponseEntity<ProjectDTO> get(@PathVariable String projectId) {
        return ResponseEntity.success(projectService.getProject(projectId));
    }

    @PostMapping
    public ResponseEntity<ProjectDTO> create(@Valid @RequestBody ProjectUpsertDTO request) {
        return ResponseEntity.success(projectService.createProject(request));
    }

    @PutMapping("/{projectId}")
    public ResponseEntity<ProjectDTO> update(@PathVariable String projectId,
                                              @Valid @RequestBody ProjectUpsertDTO request) {
        return ResponseEntity.success(projectService.updateProject(projectId, request));
    }

    @PutMapping("/{projectId}/status")
    public ResponseEntity<ProjectDTO> setStatus(@PathVariable String projectId,
                                                @Valid @RequestBody ProjectStatusDTO request) {
        return ResponseEntity.success(projectService.setProjectStatus(projectId, request));
    }

    @DeleteMapping("/{projectId}")
    public ResponseEntity<Void> delete(@PathVariable String projectId) {
        projectService.deleteProject(projectId);
        return ResponseEntity.success();
    }
}
```

## 端点映射规则

从 API Contract 的 operations 推导 REST 端点：

| API Contract operation | HTTP 方法 | 路径 | 方法名 |
| --- | --- | --- | --- |
| `list` / `query` | `GET` | `/api/<module>s` | `list` |
| `get` / `detail` | `GET` | `/api/<module>s/{id}` | `get` |
| `create` / `add` | `POST` | `/api/<module>s` | `create` |
| `update` / `edit` | `PUT` | `/api/<module>s/{id}` | `update` |
| `delete` / `remove` | `DELETE` | `/api/<module>s/{id}` | `delete` |
| `set_status` | `PUT` | `/api/<module>s/{id}/status` | `setStatus` |

## 统一响应格式

所有端点返回 `ResponseEntity<T>`：

```java
// 成功带数据
return ResponseEntity.success(data);

// 成功无数据
return ResponseEntity.success();

// 失败（通常在 Service 层抛 BizException，不由 Controller 直接返回 failed）
throw new BizException(ProjectErrorCode.PROJECT_NOT_FOUND);
```

前端 `service.ts` 拦截器统一处理 `returnCode`/`errorMsg`，Controller 只管业务逻辑。

## 权限控制（authorization effective 时）

authorization effective 模板的 Controller 用 `@RequireAnyResource` 注解控制权限：

```java
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/projects")
@RequireAnyResource(AuthConstants.PAGE_PROJECT_MANAGEMENT)
public class ProjectController {
    // ...
}
```

> `AuthConstants.PAGE_PROJECT_MANAGEMENT` 是平台权限基础设施定义的资源常量。仅当 TemplateState.effective 包含 authorization 且任务提供相应资源时，业务 Controller 才加此注解；资源 key 由平台授权投影从确认的 TechnicalPlan 派生。

## 当前任务需实现或复用的部分

先读取真实 Controller，保留不属于当前任务的端点和逻辑；不存在时仅在授权路径创建。根据确认契约实现或复用：

- 类注解（`@RestController`/`@RequiredArgsConstructor`/`@RequestMapping`）与 Service 注入。
- 当前端点方法声明（HTTP 方法、路径、参数、返回类型）及 Service 调用。
- 需要的 `@Valid`、`@ModelAttribute`、`@PathVariable`、`@RequestBody` 注解。
- 权限注解 `@RequireAnyResource`（authorization effective 且任务要求时）。
- Controller 层的参数预处理（如 trim）。

**不要**在 Controller 里写业务逻辑——业务逻辑在 ApplicationService 里。Controller 只做参数接收和调用转发。
