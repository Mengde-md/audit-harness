# Audit Harness 审计工作台

Python 3.11+ / FastAPI 后端及中文网页，支持完整项目审计、变更审计、接口验收、步骤跟踪与报告/制品查看。

## 本地运行

在 Python 3.11 的独立虚拟环境中安装依赖：

    python -m pip install -c requirements-linux.lock -e '.[dev]'

将 `.env.example` 复制为本地 `.env`。需要真实模型时，在本地填写 `LLM_API_KEY` 和对应的 `LLM_BASE_URL`、`LLM_MODEL`。配置站点访问时设置 `APP_ACCESS_CODE`，需要跨重启保留登录会话时设置独立的 `APP_SESSION_SECRET`。

    python -m uvicorn app.main:app --host 127.0.0.1 --port 8080

浏览器打开 `http://127.0.0.1:8080`。设置访问码后，服务端校验访问码并签发带过期时间的 HttpOnly Cookie。

## 离线测试

安装开发依赖后，使用 `python -m pytest -q` 运行完整测试套件。测试使用模拟模型和沙箱调用，以及临时数据库。开发机无需启动 Docker、提供模型密钥或准备数据库服务即可运行这套测试。GitHub Actions 使用同一份依赖锁定文件和 pytest 命令。

只验证模型接口或日志截断时，可运行 `python -m pytest -q tests/test_openai_compatible_provider.py tests/test_process.py`。

## 审计执行条件

完整审计需要可访问的 Docker 守护进程及 `audit-harness/python-basic:3.10`、`:3.11`、`:3.12` 沙箱镜像。`scripts/build-python-sandbox-images.sh` 构建基础镜像。生产审计使用镜像预装的依赖，额外依赖由部署者加入镜像。本地开发可以构建派生依赖镜像。项目检查和验收使用隔离网络，并设置 CPU、内存、进程数及容器权限限制。

生产环境设 `APP_ENV=production`，必须配置 `APP_ACCESS_CODE`。网页支持 GitHub、GitLab、Gitee 的公开 HTTPS 仓库；禁用服务器本地路径、凭据 URL、重定向、链接文件和越界配置读取。创建远程审计时须填写服务启动命令与端口，变更审计须填写十六进制提交 SHA 及 unified diff 补丁。Python 是当前支持的被审语言。

`GET /api/site-info` 在登录后返回实际沙箱状态。沙箱不可用时，页面明确提示且 API 返回 503，不创建不可执行的审计任务。真实模型通过 OpenAI 兼容接口进行文档解析与补丁审查；被审项目的 mock/disabled 参数控制验收中的模型请求。

## 部署与配置隔离

用服务进程的环境变量或私有环境文件提供模型密钥、访问码、会话签名密钥和数据库连接。示例配置中的凭据留空，默认关闭请求、响应和模型正文日志。本地环境文件、数据库、审计制品、缓存、日志和专用运维脚本由忽略规则排除。

通用发布脚本 `deploy/package-release.py <版本>` 可将预先下载的 Linux 依赖与源码打包，启动入口为 `deploy/start.sh`。部署时根据目标环境配置服务账号、环境变量、反向代理和 Docker 沙箱。
