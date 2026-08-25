# 架构红线规则参考

本文档包含8条高优先级规则的详细检测逻辑、修复示例和误报排除规则。

> ⚠️ **风险提示**: 全部自动处理，仅在报告中标记风险操作供人工复核

---

## 规则快速参考

| 规则ID | 名称 | 修复方式 | 事务安全 |
|--------|------|----------|----------|
| CKR1104 | Kafka降级 | catch + log + throw | ✅ |
| CKR2002 | 事务中发Kafka | 事务与消息分离（需人工确认） | ✅ |
| CKR6000 | HttpClient超时 | 添加超时配置 | ✅ |
| CKR6002 | HttpURLConnection超时 | 添加超时配置 | ✅ |
| CKR6004 | OkHttp超时 | 添加超时配置 | ✅ |
| CKR4003 | Redis降级 | catch + log + throw | ✅ |
| CKR5000 | CallerRunsPolicy | 改为AbortPolicy | ✅ |

---

## 详细检测与修复规则

### CKR1104 - Kafka降级处理

**规则**: Kafka发送操作应有降级处理

**检测关键字**: `kafkaTemplate.send`, `producer.send`

**检测逻辑**:
1. 搜索: `kafkaTemplate.send`, `producer.send`
2. 排除已处理: 查找同一方法内是否有 `try` 包裹 send 调用
3. 排除: `@KafkaListener` 消费端
4. **方法边界确认**: 必须用 Read 工具读取完整方法体，确认 send 调用确实未被 try-catch 包裹

**检测时的有效捕获类型**（满足任一即视为有降级）:
- `Exception` - 覆盖所有异常
- `RuntimeException` - 覆盖大部分Kafka异常
- `ExecutionException`, `InterruptedException`, `KafkaException` - 特定异常

**修复时应捕获的异常类型**（精确处理）:
- `ExecutionException` - 发送失败包装异常，需解包抛出根因
- `InterruptedException` - 线程中断，需恢复中断状态
- `KafkaException` - Kafka相关异常

**修复代码**:

> **⚠️ 重要**：修复前必须确认：(1) 类有 `@Slf4j` 或 Logger 字段；(2) 已有所需 import 语句

```java
// 如果原代码是同步发送（带 .get()）：
try {
    kafkaTemplate.send("topic", msg).get();
} catch (ExecutionException e) {
    Throwable cause = e.getCause();
    log.warn("Kafka发送失败: {}", cause != null ? cause.getMessage() : e.getMessage());
    if (cause instanceof RuntimeException) {
        throw (RuntimeException) cause;
    } else if (cause != null) {
        throw new RuntimeException("Kafka发送失败", cause);
    }
    throw e;
} catch (InterruptedException e) {
    log.warn("Kafka发送中断: {}", e.getMessage());
    Thread.currentThread().interrupt();
    throw new RuntimeException("Kafka发送被中断", e);
} catch (KafkaException e) {
    log.warn("Kafka发送异常: {}", e.getMessage());
    throw new RuntimeException("Kafka发送失败", e);
}

注意：必须确保在相应的 try 语句主体中能抛出catch的异常错误 

// 如果原代码是异步发送（不带 .get()），不要添加 .get()，保持原有异步行为：
try {
    kafkaTemplate.send("topic", msg);
} catch (KafkaException e) {
    log.warn("Kafka发送异常: {}", e.getMessage());
    throw new RuntimeException("Kafka发送失败", e);
}
```

> **注意**：不要擅自将异步发送改为同步发送（添加 `.get()`），这会改变业务行为，可能导致性能问题

**需要添加的 import**（按需）:
```java
import org.apache.kafka.common.KafkaException;
import java.util.concurrent.ExecutionException;
```

---

### CKR2002 - 事务中发Kafka

**规则**: 数据库事务中不应发送Kafka消息

**⚠️ 风险等级：极高 — 此规则涉及架构重构，不可全自动修复**

> 所有修复方式都会创建新类或改变事务边界，可能影响业务一致性。
> 应在报告中列出问题和建议修复方式，由用户确认后再执行修复。

**检测逻辑**:
1. 搜索带 `@Transactional` 注解的方法
2. 在**同一方法内**搜索 kafka send 调用（`kafkaTemplate.send`、`producer.send`）
3. 排除: `@Transactional(propagation = NOT_SUPPORTED)`
4. 排除: `@Transactional(propagation = REQUIRES_NEW)`

**注意**: 必须是同一方法内，跨方法不检测。必须用 Read 工具读取完整方法体确认。

---

**推荐修复方式**（任选其一）：

#### 方式一：使用 @Async + @Transactional 分离

```java
@Service
public class OrderService {

    @Autowired
    private OrderAsyncService asyncService;

    @Transactional
    public void createOrder(Order order) {
        orderMapper.insert(order);
        // 异步发送，不阻塞事务
        asyncService.sendOrderNotification(order);
    }
}

@Service
public class OrderAsyncService {

    @Autowired
    private KafkaTemplate<String, Object> kafkaTemplate;

    @Async
    public void sendOrderNotification(Order order) {
        kafkaTemplate.send("order-topic", order);
    }
}
```

#### 方式二：编程式事务控制

```java
@Service
public class OrderService {

    @Autowired
    private TransactionTemplate transactionTemplate;

    @Autowired
    private KafkaTemplate<String, Object> kafkaTemplate;

    public void createOrder(Order order) {
        // 事务内执行数据库操作
        transactionTemplate.executeWithoutResult(status -> {
            orderMapper.insert(order);
        });
        // 事务外发送Kafka
        kafkaTemplate.send("order-topic", order);
    }
}
```

---

### CKR6000 - HttpClient超时

**规则**: HttpClient 必须配置超时

**检测关键字**: `HttpClient.newHttpClient()`

**检测逻辑**:
1. 搜索 `HttpClient.newHttpClient()` - 无参数调用
2. 搜索 `HttpClient.newBuilder().build()` - Builder未配置超时
3. 排除: 使用 `@Autowired` 注入的 HttpClient Bean，Bean 已有配置超时

**修复代码**:
```java
HttpClient client = HttpClient.newBuilder()
    .connectTimeout(Duration.ofSeconds(30))
    .build();
```

---

### CKR6002 - HttpURLConnection超时

**规则**: HttpURLConnection 必须配置超时

**检测关键字**: `openConnection()`

**检测逻辑**:
1. 搜索方法内的 `openConnection()` 调用
2. 确认返回值类型为 `HttpURLConnection`（排除非HTTP的URL连接如 `JarURLConnection`）
3. 检查同一方法内是否有 `setConnectTimeout` 和 `setReadTimeout`（两个都需要设置）
4. 排除: 使用工具类方法（已统一设置超时）

**修复代码**:
```java
// 两个超时都必须设置
conn.setConnectTimeout(30000);  // 连接超时 30秒
conn.setReadTimeout(30000);     // 读取超时 30秒
```

> **注意**：`setConnectTimeout` 和 `setReadTimeout` 必须同时设置，只设一个仍不完整

---

### CKR6004 - OkHttp超时

**规则**: OkHttp 必须配置超时

**检测关键字**: `new OkHttpClient()`, `OkHttpClient.Builder`

**检测逻辑**:
1. 搜索 `new OkHttpClient()` - 无参数
2. 搜索 `new OkHttpClient.Builder().build()` - 未配置超时
3. 排除: 使用 `@Autowired` 注入的 OkHttpClient Bean，Bean 已有配置超时

**修复代码**:
```java
OkHttpClient client = new OkHttpClient.Builder()
    .connectTimeout(30, TimeUnit.SECONDS)
    .readTimeout(30, TimeUnit.SECONDS)
    .writeTimeout(30, TimeUnit.SECONDS)
    .build();
```

---

### CKR4003 - Redis降级处理

**规则**: Redis 操作应有降级处理

**检测关键字**: `redisTemplate.opsFor`, `redisTemplate.execute`, `redisTemplate.delete`, `redisTemplate.expire`, `redisTemplate.hasKey`, `stringRedisTemplate.opsFor`, `stringRedisTemplate.execute`

**检测逻辑**:
1. 搜索: `redisTemplate.opsForValue`, `redisTemplate.opsForHash`, `redisTemplate.opsForList`, `redisTemplate.opsForSet`, `redisTemplate.opsForZSet`, `redisTemplate.execute`, `redisTemplate.delete`, `redisTemplate.expire`, `redisTemplate.hasKey`
2. 同时搜索 `stringRedisTemplate` 的同类操作
3. 排除: 同一方法内有 try-catch 包裹（需确认 catch 的异常类型是否覆盖 Redis 异常）
4. 排除: `@Cacheable` 注解（Spring Cache 框架已有异常处理）
5. 排除: `@Retryable` 注解

**检测时的有效捕获类型**（满足任一即视为有降级）:
- `Exception` - 覆盖所有异常
- `RuntimeException` - 覆盖所有Redis异常
- `DataAccessException` - 覆盖Spring Data Redis所有异常
- `RedisSystemException`, `RedisConnectionFailureException` - 特定异常

**修复时应捕获的异常类型**（精确处理）:
- `RedisSystemException` - Redis系统异常
- `RedisConnectionFailureException` - Redis连接失败异常
- `DataAccessException` - Spring数据访问异常（通用父类）

**修复代码**:

> **⚠️ 重要**：修复前必须确认：(1) 类有 `@Slf4j` 或 Logger 字段；(2) 已有所需 import 语句 

```java
try {
    redisTemplate.opsForValue().get(key);
} catch (RedisConnectionFailureException e) {
    log.warn("Redis连接失败: {}", e.getMessage());
    throw new RuntimeException("Redis连接失败", e);
} catch (RedisSystemException e) {
    log.warn("Redis访问失败: {}", e.getMessage());
    throw e;
} catch (DataAccessException e) {
    log.warn("Redis访问异常: {}", e.getMessage());
    throw e;
}
```
注意：必须确保在相应的 try 语句主体中能抛出catch的异常错误 

**需要添加的 import**（按需）:
```java
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.data.redis.RedisSystemException;
import org.springframework.dao.DataAccessException;
```

---

### CKR5000 - CallerRunsPolicy

**规则**: 线程池拒绝策略禁止使用 CallerRunsPolicy

**检测关键字**: `new CallerRunsPolicy()`

**修复代码**:
```java
// 修复前
new ThreadPoolExecutor.CallerRunsPolicy()

// 修复后
new ThreadPoolExecutor.AbortPolicy()
// 或
new ThreadPoolExecutor.DiscardPolicy()
```

**注意**: 无例外场景，必须修改

---



## 误报排除规则总结

| 规则 | 排除条件 |
|------|----------|
| CKR1104 | try-catch已包裹、@KafkaListener、AOP处理、非Kafka的send调用（如邮件、HTTP） |
| CKR2002 | 非@Transactional方法、NOT_SUPPORTED/REQUIRES_NEW、跨方法调用 |
| CKR6000 | @Autowired注入的Bean、Builder中已配置connectTimeout |
| CKR6002 | 工具类方法、setTimeout已调用、非HTTP的URL连接 |
| CKR6004 | @Autowired注入的Bean、Builder中已配置超时 |
| CKR4003 | try-catch已包裹、@Cacheable、@Retryable |
| CKR5000 | 无例外 |

---

## 修复安全检查清单

每次修复前后，必须按此清单逐项确认：

| 检查项 | 说明 |
|--------|------|
| **Logger 存在** | 类有 `@Slf4j` 注解或手动声明 `private static final Logger log = LoggerFactory.getLogger(...)` |
| **Import 完整** | 新增异常类型的 import 已添加，且无重复 |
| **方法边界正确** | try-catch 包裹范围在同一方法内，未跨方法 |
| **异常重新抛出** | catch 块中重新 throw 异常，保证事务回滚 |
| **不改变业务行为** | 不添加 `.get()`（异步→同步）、不改变返回值、不增删参数 |
| **变量名不变** | 修复后的变量名、方法签名与原代码一致 |
| **缩进风格一致** | 新增代码的缩进风格与原文件一致（tab/space） |
| 添加 catch 的异常会 | 必须确保在相应的 try 语句主体中能抛出异常错误 |