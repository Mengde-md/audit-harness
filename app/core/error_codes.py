from enum import Enum


class ErrorCode(Enum):
    def __new__(cls, code: int, message: str, http_status: int):
        obj = object.__new__(cls)
        obj._value_ = code
        obj.code = code
        obj.message = message
        obj.http_status = http_status
        return obj

    SUCCESS = (0, "成功", 200)

    # Interface / HTTP / request: 20000-29999
    PARAM_ERROR = (20001, "请求参数错误", 400)
    REQUEST_VALIDATION_ERROR = (20002, "请求参数校验失败", 422)
    UNAUTHORIZED = (20003, "未授权，请登录", 401)
    FORBIDDEN = (20004, "权限不足", 403)
    RESOURCE_NOT_FOUND = (20005, "资源不存在", 404)
    METHOD_NOT_ALLOWED = (20006, "请求方法不支持", 405)
    REQUEST_TOO_LARGE = (20007, "请求内容过大", 413)
    API_RATE_LIMIT = (20008, "接口调用频率超限", 429)
    API_REQUEST_ERROR = (20009, "接口请求失败", 502)
    API_TIMEOUT = (20010, "接口请求超时", 504)
    UNSUPPORTED_OPERATION = (20011, "不支持的操作", 400)

    # Database / storage: 30000-39999
    DB_ERROR = (30000, "数据库操作失败", 500)
    DB_CONNECTION_ERROR = (30001, "数据库连接失败", 503)
    DB_QUERY_ERROR = (30002, "数据库查询失败", 500)
    DB_WRITE_ERROR = (30003, "数据库写入失败", 500)
    DATA_DUPLICATE = (30004, "数据已存在", 409)
    DATA_NOT_FOUND = (30005, "数据不存在", 404)
    DB_TRANSACTION_ERROR = (30006, "数据库事务执行失败", 500)
    STORAGE_ERROR = (30007, "文件或存储操作失败", 500)

    # LLM: 40000-49999
    LLM_ERROR = (40000, "大模型调用失败", 502)
    LLM_TIMEOUT = (40001, "大模型响应超时", 504)
    LLM_NO_RESPONSE = (40002, "大模型未返回有效内容", 502)
    LLM_CONTENT_VIOLATION = (40003, "内容违规，大模型拒绝生成", 400)
    LLM_TOKEN_LIMIT = (40004, "上下文长度超限", 400)
    LLM_RATE_LIMIT = (40005, "大模型调用频率超限", 429)
    LLM_MODEL_ERROR = (40006, "模型不存在或未部署", 502)
    LLM_AUTH_ERROR = (40007, "大模型服务认证失败", 502)
    LLM_INVALID_RESPONSE = (40008, "大模型返回内容格式无效", 502)
    LLM_CONFIG_ERROR = (40009, "大模型配置不完整", 500)

    # System / service: 50000-59999
    SERVER_ERROR = (50000, "服务器异常", 500)
    SYSTEM_BUSY = (50001, "系统繁忙，请稍后再试", 503)
    CONFIG_ERROR = (50002, "服务配置错误", 500)
    WORKSPACE_ERROR = (50003, "工作区准备失败", 500)
    SANDBOX_ERROR = (50004, "沙箱执行失败", 500)
    DOCKER_UNAVAILABLE = (50005, "Docker 服务不可用", 503)
    SANDBOX_IMAGE_NOT_FOUND = (50006, "沙箱镜像不存在", 500)
    COMMAND_TIMEOUT = (50007, "检查命令执行超时", 504)
    PATCH_APPLY_ERROR = (50008, "代码补丁应用失败", 400)
    FILE_IO_ERROR = (50009, "文件操作失败", 500)
    BACKGROUND_TASK_ERROR = (50010, "后台任务执行失败", 500)
    API_DOCUMENT_ERROR = (50011, "API 文档解析失败", 400)
    API_DOCUMENT_INSUFFICIENT = (50012, "API 文档信息不足", 400)
    TEST_SUITE_ERROR = (50013, "验收测试集生成失败", 500)
    SERVICE_STARTUP_TIMEOUT = (50014, "被审计服务启动超时", 504)
    DEPENDENCY_DETECTION_ERROR = (50015, "项目依赖识别失败", 400)
    DEPENDENCY_IMAGE_BUILD_ERROR = (50016, "依赖镜像构建失败", 500)
