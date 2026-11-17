# Database Business Reference

## Confirm Database Operations

Locate the current database source in `api_design.sources` and read its database,
table, and column structures.

Database-specific fields:

- `databaseOperation`: the database operation type.
- `databaseQuery`: query conditions, grouping, sorting, and pagination.
- `databaseWrites`: the target source, columns, and values.

For example:

```json
{
  "databaseOperation": "create",
  "databaseWrites": {
    "source": "database_1",
    "values": {
      "name": { "request": "request_body.name" },
      "created_at": { "business": "Current time" }
    }
  }
}
```

`source` refers to an alias in `sources`. The keys of `values` are database column
names, and each value describes its value rule.

Read column types and database constraints from the source's `columns`.

Multiple write targets use an array of the same `source`/`values` objects.
`databaseQuery` retains nested `join`/`items` groups. Each condition contains a
source-qualified `column`, an `operator`, and a `value` when the operator needs one.

## Prepare Domain Objects and Conversions

Inspect the task's Entity, PO, and Converter. Complete the domain fields using
confirmed fields and implement only the conversion directions required by the
current query or write.

Reuse existing project fields, conversion methods, and coding conventions.

```java
@Component
public class ProductPoConverter {

    public ProductPO toPO(Product product) {
        // For writes: map the domain object to the PO.
    }

    public Product toDomain(ProductPO productPO) {
        // For reads: map the PO to the domain object.
    }
}
```

## Implement the Repository

Inspect the task's Repository interface, implementation, Mapper, and Converter.
Declare and implement the required methods, reusing existing methods.

```java
public interface ProductRepository {

    Product save(Product product);

    boolean existsByCode(String code);
}
```

```java
@Repository
@RequiredArgsConstructor
public class ProductRepositoryImpl implements ProductRepository {

    private final ProductMapper productMapper;
    private final ProductPoConverter productPoConverter;

    @Override
    public Product save(Product product) {
        // Convert to a PO, persist it, and convert back to a domain object.
    }

    @Override
    public boolean existsByCode(String code) {
        // Check existence using a parameterized condition.
    }
}
```

Use MyBatis-Plus `BaseMapper` for ordinary single-table operations and
`LambdaQueryWrapper` or `LambdaUpdateWrapper` for conditions.

Preserve confirmed condition grouping, sorting, and pagination. Do not execute an
update or delete without an effective predicate.

Use custom Mapper methods and XML for complex joins, aggregations, or subqueries
when `BaseMapper` cannot express them clearly.

## Prepare Requests, Responses, and Conversions

Inspect the task's request DTOs, response DTOs, and Assembler. Implement conversions
from the API schemas and `fieldMappings`.

```java
@Component
public class ProductAssembler {

    public Product toDomain(CreateProductRequest request) {
        // Map request fields to the domain object.
    }

    public ProductDetailResponse toResponse(Product product) {
        // Compose fields using the response mappings.
    }
}
```

Use the task's actual fields, time formats, and method names.

## Implement the Application Service

Inspect the task's Service and existing module implementations. Coordinate business
rules, resolved values, Repository calls, and response composition.

Resolve query and write values before invoking the Repository.

```java
@Service
@RequiredArgsConstructor
public class ProductApplicationService {

    private final ProductRepository productRepository;
    private final ProductAssembler productAssembler;

    @Transactional
    public ProductDetailResponse createProduct(CreateProductRequest request) {
        // Apply explicitly required business rules.
        // Assemble the domain object, invoke the Repository, and compose the response.
    }
}
```

Access the database through the domain Repository. Follow the actual persistence
boundary and existing project transaction conventions.
