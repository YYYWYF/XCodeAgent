# Database Foundation Reference

## Prepare the Persistence Object

Inspect the task's PO file and nearby PO conventions. Use the confirmed database
table, columns and key from `implementation_contract.source_snapshots`, the project's
actual package and Lombok style. For example:

```java
@Data
@TableName("confirmed_table")
public class ProductPO {
    @TableId(value = "id", type = IdType.AUTO)
    private Long id;
}
```

Derive the key strategy and every column from the confirmed snapshot and existing
project convention. Do not default to `AUTO` without evidence. Add `@TableField`
only where the database column and Java property differ.

## Prepare the Mapper

Inspect the task's Mapper file and nearby Mapper conventions. The interface extends
`BaseMapper<PO>`, for example:

```java
@Mapper
public interface ProductMapper extends BaseMapper<ProductPO> {
}
```

If the task calls for a Mapper XML placeholder, inspect the project's XML conventions
and include only its mapper namespace, without SQL statements. For example:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper
    PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
    "https://mybatis.org/dtd/mybatis-3-mapper.dtd">

<mapper namespace="com.example.product.infrastructure.mapper.ProductMapper">
</mapper>
```

Use the project's fully qualified Mapper interface name for `namespace`.

## Repository Skeleton

Inspect the task's Repository interface and implementation and reuse existing module
and Converter conventions. A new Repository interface starts without business methods.
The implementation skeleton may contain:

- `@Repository`
- constructor or Lombok injection
- the Mapper dependency
- the Converter dependency

For example:

```java
@Repository
@RequiredArgsConstructor
public class ProductRepositoryImpl implements ProductRepository {

    private final ProductMapper productMapper;
}
```

Use the project's actual package and injection style. Add Mapper or Converter injection
as required by the task. Existing business methods and their Mapper or Converter
dependencies remain in place; Foundation adds no business methods.

## Mapping and Java 8

Use the confirmed table and column types. Common mappings are:

- integer families to `Integer` or `Long`
- text families to `String`
- decimal families to `BigDecimal`
- date/time families to `java.time` types
- boolean families to `Boolean`

Do not use `record`, `var`, `List.of`, text blocks, or `String.isBlank`.
