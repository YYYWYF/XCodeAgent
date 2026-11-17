# Database Bootstrap Reference

## Check for Missing Database Maven Dependencies

Check these coordinates in `pom.xml` and its existing dependency management:

- `com.baomidou:mybatis-plus-boot-starter`, version `3.5.2`
- `com.mysql:mysql-connector-j`, version `8.2.0`

An existing compatible dependency satisfies the check: keep its coordinates,
version, and scope unchanged. Add a missing dependency using the version listed above.

## Check Database Configuration

Inspect `src/main/resources/application.yml` for the MySQL connection settings.
Add only missing keys using these template values:

```yaml
spring:
  datasource:
    driver-class-name: com.mysql.cj.jdbc.Driver
    url: jdbc:mysql://${datasource.ds1.url}:${datasource.ds1.port}/${datasource.ds1.schema}?useUnicode=true&characterEncoding=UTF-8&useSSL=false&serverTimezone=Asia/Shanghai&connectTimeout=10000&socketTimeout=600000&allowPublicKeyRetrieval=true
    username: ${datasource.ds1.username}
    password: ${datasource.ds1.password}
```

Inspect `src/main/resources/application-dev.yml` for the connection parameters used
above. Each field passes as soon as it contains a non-empty literal value; keep that
value even when it differs from the mock example. Actual connection values are
injected through environment variables at startup, so do not verify these literals
against a real database or look up real connection details.

For a missing, empty, or `${...}`-based field, use the corresponding mock literal
below. Use plain literal values, including when replacing placeholders that have
defaults; do not write environment-variable placeholders into these fields:

```yaml
datasource:
  ds1:
    url: 127.0.0.1
    port: 3306
    schema: mock_database
    username: mock_user
    password: mock_password
```

Connection pool tuning and other MyBatis-Plus settings are outside this check;
preserve the current configuration.

If project documentation requires a different data source prefix or driver from
the actual configuration and this reference, report `contract_mismatch` with the
conflicting file paths and settings. Do not infer which template baseline is stale.

## Check MyBatis Pagination Configuration

Check `src/main/java/<base-package-path>/common/config/MybatisPlusConfiguration.java`
for a `MybatisPlusInterceptor` bean with a `PaginationInnerInterceptor` for MySQL.
Confirm the bean is registered in the application component scan, reading the
application entrypoint if needed. Once this bean includes MySQL pagination and is
within the scan scope, the pagination check is complete.

If the file does not exist or does not contain the bean, search `src/main/java/` for
`MybatisPlusInterceptor` and inspect the matching configuration and scan scope.

If an existing bean lacks pagination, add the pagination interceptor to that bean.
If no bean exists, add one in a configuration class, following this example:

```java
import com.baomidou.mybatisplus.annotation.DbType;
import com.baomidou.mybatisplus.extension.plugins.MybatisPlusInterceptor;
import com.baomidou.mybatisplus.extension.plugins.inner.PaginationInnerInterceptor;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class MybatisPlusConfiguration {
    @Bean
    public MybatisPlusInterceptor mybatisPlusInterceptor() {
        MybatisPlusInterceptor interceptor = new MybatisPlusInterceptor();
        interceptor.addInnerInterceptor(new PaginationInnerInterceptor(DbType.MYSQL));
        return interceptor;
    }
}
```
