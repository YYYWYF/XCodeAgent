# Spring Boot + MyBatis-Plus Bootstrap

仅当当前任务明确属于 `backend:bootstrap`，且任务修改范围包含对应工程文件时读取。
普通 `backend:endpoint:*` 任务不得执行本文件中的检查或修改。

## 幂等检查范围

先读取现有 `pom.xml`、`application.yml` 和配置类。已有配置保持不动，只补充当前工程运行数据库来源代码所必需且确实缺失的部分。

### Maven 依赖

确认工程已经提供以下能力，优先沿用现有依赖和版本管理：

- `mybatis-plus-boot-starter`
- 对应数据库的 JDBC 驱动（当前默认 MySQL）
- 项目已经采用 Lombok 时使用 `lombok`
- 项目已经采用 MapStruct 时使用 `mapstruct` 与注解处理器

仅在依赖缺失且项目没有等价方案时补充。可采用的默认版本为 MyBatis-Plus
`3.5.2`、MySQL Connector `8.2.0`、Lombok `1.18.30`、MapStruct
`1.5.5.Final`；如果项目已有集中版本管理，必须服从现有版本。

若 `maven-compiler-plugin` 已配置 `annotationProcessorPaths`，并且同时使用 Lombok
与 MapStruct，可加入 `lombok-mapstruct-binding` `0.2.0` 解决处理顺序问题。

### 数据源配置

确认 `application.yml` 已存在可解析的 `spring.datasource.url`、`username` 和
`password`。配置缺失时，使用工作区提供的 `get_mysql_config` 获取当前应用绑定的
连接信息；不要猜测凭据，不要读取后端服务自身的 `.env`，也不要把真实凭据写入日志或任务描述。

典型 JDBC URL 参数包括 UTF-8、时区、SSL 与 public-key retrieval，但应优先沿用
现有项目格式和工具返回的 `jdbc_url`。

### MyBatis-Plus 分页配置

只有当前 endpoint 使用 MyBatis-Plus 分页且工程尚无等价配置时，才补充全局拦截器：

```java
@Configuration
public class MyBatisPlusConfig {

    @Bean
    public MybatisPlusInterceptor mybatisPlusInterceptor() {
        MybatisPlusInterceptor interceptor = new MybatisPlusInterceptor();
        interceptor.addInnerInterceptor(new PaginationInnerInterceptor(DbType.MYSQL));
        return interceptor;
    }
}
```

配置类路径和包名必须沿用工作区现有 `infrastructure/config` 或等价约定；发现已有
分页拦截器时不得创建第二份配置。

## 完成条件

- 只修改明确缺失且属于当前 bootstrap 任务授权范围的工程基础设施。
- 不生成业务 Entity、Repository、Service 或 Controller。
- 不执行数据库建表、迁移、种子数据或其他 schema 操作。
