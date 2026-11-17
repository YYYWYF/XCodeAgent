# Controller Resource Authorization Reference

## Add the Authorization Annotation

Use `@RequireAnyResource` on the corresponding Controller method,
referencing the constant names supplied in the current endpoint's `authConstants`.

For a single resource:

```java
@RequireAnyResource(AuthConstants.PRODUCT_CREATE_RESOURCE)
```

For multiple resources:

```java
@RequireAnyResource({
    AuthConstants.PRODUCT_CREATE_RESOURCE,
    AuthConstants.PRODUCT_MANAGE_RESOURCE
})
```

Multiple resources use `ANY_OF` semantics: access is allowed when the caller has
permission for any one of them.

Reuse the method's existing annotation and adjust it to the current endpoint
configuration without adding a duplicate.
Resource constants are prepared by the preceding task; only reference them here
and do not modify `AuthConstants`.
