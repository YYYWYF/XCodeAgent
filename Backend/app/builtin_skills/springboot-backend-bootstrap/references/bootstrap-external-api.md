# External API Bootstrap Reference

## Check for Missing OpenFeign Maven Dependencies

Check `pom.xml` and its existing dependency management for:

- `org.springframework.cloud:spring-cloud-dependencies` BOM, Spring Cloud version
  `2021.0.3` for the Java 8 and Spring Boot `2.7.2` template baseline.
- `org.springframework.cloud:spring-cloud-starter-openfeign`, with its version
  managed by the Spring Cloud BOM.

Reuse existing compatible entries. If the BOM is missing, add the following property
and dependency management entry:

```xml
<properties>
    <spring-cloud.version>2021.0.3</spring-cloud.version>
</properties>

<dependencyManagement>
    <dependencies>
        <dependency>
            <groupId>org.springframework.cloud</groupId>
            <artifactId>spring-cloud-dependencies</artifactId>
            <version>${spring-cloud.version}</version>
            <type>pom</type>
            <scope>import</scope>
        </dependency>
    </dependencies>
</dependencyManagement>
```

If the starter is missing, add it without a direct version:

```xml
<dependency>
    <groupId>org.springframework.cloud</groupId>
    <artifactId>spring-cloud-starter-openfeign</artifactId>
</dependency>
```

Merge missing entries into the existing `pom.xml` structure without replacing
compatible dependency management or duplicating elements.

## Check Feign Client Discovery

Check whether the Spring Boot application entrypoint or existing integration
configuration has the `@EnableFeignClients` annotation
(`org.springframework.cloud.openfeign.EnableFeignClients`). If not, add the annotation
to the existing Spring Boot application class.
