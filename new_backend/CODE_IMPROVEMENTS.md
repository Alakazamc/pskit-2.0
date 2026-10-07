# New Backend Code Improvement Recommendations

## 执行摘要

基于对 `new_backend` 代码库的系统性分析（2394个Python文件），以下是主要改进建议的优先级排序：

## 🔴 高优先级改进

### 1. 异常处理的改进

**当前问题：**
- 发现14处过于宽泛的 `except Exception` 捕获
- 部分关键路径缺少具体的异常类型
- 异常信息可能暴露内部实现细节

**改进建议：**

```python
# ❌ 不好的做法
try:
    result = process_data()
except Exception as e:
    return {"error": str(e)}

# ✅ 好的做法
try:
    result = process_data()
except ProtocolError as e:
    # 预期的协议错误
    raise
except ValidationError as e:
    # 输入验证失败
    raise ProtocolError("INPUT_INVALID") from e
except Exception as e:
    # 未预期的错误，记录日志但不暴露细节
    logger.error("Unexpected error in process_data", exc_info=True)
    raise ProtocolError("INTERNAL_ERROR") from e
```

**影响文件：**
- `pskit_compute/service.py:116`
- `app/adapters/live/remote_mcp.py:118, 143`
- `app/services/agent.py:398, 508`

### 2. 错误代码标准化

**当前问题：**
- 错误代码分散在多处，缺少统一的定义
- 某些错误代码是动态生成的，难以追踪
- 缺少错误代码到用户友好消息的映射

**改进建议：**

创建集中的错误代码注册表：

```python
# app/domain/errors.py
from enum import Enum
from typing import Dict

class ErrorCode(str, Enum):
    """Centralized error codes with severity and user messages."""
    
    # Protocol errors
    MCP_REPORT_REQUIRED = "MCP_REPORT_REQUIRED"
    MCP_REPORT_TOO_LARGE = "MCP_REPORT_TOO_LARGE"
    MCP_REMOTE_OUTPUT_INVALID = "MCP_REMOTE_OUTPUT_INVALID"
    MCP_FAILED_USAGE_MISSING = "MCP_FAILED_USAGE_MISSING"
    
    # Compute errors
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    COMPUTE_FAILURE = "COMPUTE_FAILURE"
    
    # Auth errors
    AUTH_REQUIRED = "AUTH_REQUIRED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"

ERROR_MESSAGES: Dict[ErrorCode, str] = {
    ErrorCode.MCP_REPORT_REQUIRED: "远程服务未返回有效的计算报告",
    ErrorCode.MCP_REPORT_TOO_LARGE: "计算结果超出大小限制",
    # ... 其他映射
}

class ErrorSeverity(str, Enum):
    """Error severity levels for logging and monitoring."""
    EXPECTED = "expected"      # 用户输入错误等预期错误
    TRANSIENT = "transient"    # 网络超时等可重试错误
    CRITICAL = "critical"      # 需要立即关注的错误
```

### 3. 日志记录的改进

**当前问题：**
- 日志使用不一致（有的用 `logger`，有的用 `LOG`）
- 缺少结构化日志
- 关键操作（如CORAL任务失败）的日志信息不足

**改进建议：**

```python
# app/services/structured_logging.py
import logging
import json
from typing import Any, Dict
from contextvars import ContextVar

# 使用 ContextVar 跟踪请求上下文
request_context: ContextVar[Dict[str, Any]] = ContextVar('request_context', default={})

class StructuredLogger:
    """Structured logger with request context."""
    
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
    
    def log(self, level: int, message: str, **kwargs):
        """Log with structured data and request context."""
        context = request_context.get()
        data = {
            "message": message,
            "context": context,
            **kwargs
        }
        self.logger.log(level, json.dumps(data, ensure_ascii=False))
    
    def info(self, message: str, **kwargs):
        self.log(logging.INFO, message, **kwargs)
    
    def error(self, message: str, **kwargs):
        self.log(logging.ERROR, message, **kwargs)

# 使用示例
logger = StructuredLogger("pskit.compute")
logger.info(
    "CORAL task failed during PDB download",
    task_id="6VXX",
    error_code="PDB_DOWNLOAD_FAILED",
    pdb_id="1ABC",
    user_id=user_id
)
```

## 🟡 中优先级改进

### 4. 类型注解的完善

**当前问题：**
- 部分函数缺少返回类型注解
- 某些复杂类型使用 `Any` 或 `dict`

**改进建议：**

```python
# ❌ 不好的做法
def process_report(data):
    return parse_data(data)

# ✅ 好的做法
from typing import Dict, List, Optional
from app.contracts.compute import ExecutionReport

def process_report(data: Dict[str, Any]) -> ExecutionReport:
    """Process raw report data into validated execution report.
    
    Args:
        data: Raw report dictionary from MCP service
        
    Returns:
        Validated execution report
        
    Raises:
        ProtocolError: If report format is invalid
    """
    return parse_data(data)
```

### 5. 硬编码值的配置化

**当前问题：**
- 发现15处硬编码的 `asyncio.sleep` 延迟
- 超时值、重试次数等分散在代码中

**改进建议：**

```python
# app/config.py
@dataclass(frozen=True)
class RetryConfig:
    """Retry and timeout configuration."""
    max_retries: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    timeout_seconds: float = 30.0
    
@dataclass(frozen=True)
class ComputeConfig:
    """Compute service configuration."""
    mcp_poll_interval_seconds: float = 0.25
    lease_renewal_interval_ratio: float = 0.33  # Renew at 1/3 of lease time
    status_check_interval_seconds: float = 0.2
    
    coral_retry: RetryConfig = field(default_factory=RetryConfig)
    af3_retry: RetryConfig = field(default_factory=lambda: RetryConfig(
        max_retries=5,
        timeout_seconds=21600  # 6 hours
    ))
```

### 6. 数据库查询优化

**当前问题：**
- 某些查询可能造成N+1问题
- 缺少查询性能监控

**改进建议：**

```python
# app/domain/persistent_conversation/af3.py

# ❌ 不好的做法（N+1查询）
def get_jobs_with_users(self):
    jobs = self.db.execute("SELECT * FROM agent_jobs").fetchall()
    for job in jobs:
        user = self.db.execute(
            "SELECT * FROM users WHERE id=?", (job["user_id"],)
        ).fetchone()
        # ...

# ✅ 好的做法（JOIN查询）
def get_jobs_with_users(self):
    return self.db.execute("""
        SELECT j.*, u.email, u.role
        FROM agent_jobs j
        LEFT JOIN users u ON j.user_id = u.id
        WHERE j.status IN ('queued', 'running')
    """).fetchall()
```

## 🟢 低优先级改进

### 7. 代码重复的消除

**当前问题：**
- 类似的错误处理逻辑重复出现
- 某些验证逻辑在多处重复

**改进建议：**

创建可复用的装饰器和工具函数：

```python
# app/utils/decorators.py
from functools import wraps
import asyncio
from typing import TypeVar, Callable

T = TypeVar('T')

def retry_on_transient_error(
    max_attempts: int = 3,
    backoff_seconds: float = 1.0
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Retry decorator for transient errors."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_error = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except (OSError, TimeoutError) as e:
                    last_error = e
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(backoff_seconds * (2 ** attempt))
            raise last_error
        return wrapper
    return decorator

# 使用示例
@retry_on_transient_error(max_attempts=3)
async def fetch_remote_data(url: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return response.json()
```

### 8. 测试覆盖率提升

**当前问题：**
- 某些关键路径（如CORAL失败处理）缺少测试
- 边界情况测试不足

**改进建议：**

```python
# tests/test_coral_failure_handling.py
import pytest
from app.contracts.compute import Failed, UsageReport
from pskit_compute.service import ProtocolError

@pytest.mark.asyncio
async def test_coral_pdb_download_failure_without_usage():
    """Test that PDB download failures are handled even without GPU usage."""
    # Arrange
    adapter = create_test_adapter()
    grant = create_test_grant()
    
    # Mock CORAL service returning failure without usage
    mock_response = {
        "status": "failed",
        "error": {
            "code": "PDB_DOWNLOAD_FAILED",
            "message": "Failed to download PDB structure 1ABC"
        }
        # Note: no "usage" field
    }
    
    # Act & Assert
    with pytest.raises(ProtocolError) as exc_info:
        await adapter._report(mock_response, grant)
    
    # Before fix: raises MCP_FAILED_USAGE_MISSING
    # After fix: should accept and mark for reconciliation
    assert exc_info.value.args[0] == "MCP_FAILED_USAGE_MISSING"

@pytest.mark.asyncio
async def test_coral_failure_with_partial_usage():
    """Test that failures with partial usage are accepted."""
    # Test implementation...
```

## 📊 代码度量

基于分析的代码库统计：

- 总Python文件数：2,394
- 使用异常处理的文件：76
- 包含异步函数的文件：63
- 类型检查使用频率：8次 isinstance(dict/list)
- 过于宽泛的异常捕获：14处
- 代码检查忽略注释：17处

## 🔧 具体实施步骤

### 阶段1：紧急修复（已完成）
- [x] 修复CORAL失败任务的用量检查问题
- [x] 增加Pi RPC管道大小以支持图片

### 阶段2：错误处理改进（建议1-2周）
1. 创建集中的错误代码枚举
2. 重构关键路径的异常处理
3. 添加结构化日志记录
4. 补充缺失的测试用例

### 阶段3：代码质量提升（建议1个月）
1. 完善类型注解
2. 将硬编码值迁移到配置
3. 优化数据库查询
4. 消除代码重复

### 阶段4：长期优化（持续进行）
1. 提升测试覆盖率
2. 性能监控和优化
3. 文档完善
4. 技术债务追踪

## 📝 建议的代码审查清单

每次PR应检查：

- [ ] 是否使用了具体的异常类型而非 `Exception`
- [ ] 错误信息是否使用了标准错误代码
- [ ] 是否添加了适当的日志记录
- [ ] 新函数是否有完整的类型注解
- [ ] 是否避免了硬编码的魔法数字
- [ ] 是否添加了相应的测试用例
- [ ] 是否更新了相关文档

## 🎯 成功指标

- 减少生产环境中的未捕获异常 > 50%
- 提高关键路径的测试覆盖率至 > 80%
- 减少平均故障恢复时间(MTTR) > 30%
- 提高代码可维护性评分(通过静态分析工具)

---

**生成时间：** 2026-10-07
**分析范围：** new_backend/
**分析工具：** Manual code review + grep/rg analysis
