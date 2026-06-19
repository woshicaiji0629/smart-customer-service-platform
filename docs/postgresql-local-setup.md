# PostgreSQL 本地开发指南（macOS）

本项目的知识检索库依赖 PostgreSQL 和 pgvector。本文说明如何在 macOS 上完成安装、初始化和日常操作。

## 1. 安装 PostgreSQL 和 pgvector

使用 Homebrew 安装：

```bash
brew install postgresql@17 pgvector
```

确认安装结果：

```bash
/usr/local/opt/postgresql@17/bin/psql --version
```

Intel Mac 的 Homebrew 默认安装在 `/usr/local`；Apple Silicon Mac 通常安装在 `/opt/homebrew`。如果上述命令不存在，可以通过以下命令查看实际路径：

```bash
brew --prefix postgresql@17
```

## 2. 启动和停止 PostgreSQL

启动并注册为登录后自动运行的服务：

```bash
brew services start postgresql@17
```

查看状态：

```bash
brew services list
```

重启或停止服务：

```bash
brew services restart postgresql@17
brew services stop postgresql@17
```

Intel Mac 的数据库文件通常位于 `/usr/local/var/postgresql@17`。不要直接修改该目录中的文件。数据库运行后可查询准确位置：

```bash
psql postgres -c "SHOW data_directory;"
```

## 3. 创建项目用户和数据库

Homebrew 初始化的 PostgreSQL 通常允许当前 macOS 用户通过本地 Socket 以管理员身份登录：

```bash
psql postgres
```

进入 `psql` 后创建应用专用用户和数据库。不要把真实密码提交到仓库：

```sql
CREATE ROLE customer_service
WITH LOGIN PASSWORD '替换为本地开发密码';

CREATE DATABASE smart_customer_service
OWNER customer_service;
```

如果数据库已经存在，只需修改其所有者：

```sql
ALTER DATABASE smart_customer_service OWNER TO customer_service;
```

## 4. 启用 pgvector

管理员进入项目数据库：

```sql
\c smart_customer_service
CREATE EXTENSION IF NOT EXISTS vector;
```

检查扩展：

```sql
\dx
```

输出中包含 `vector` 即表示 pgvector 已启用。扩展只需为每个数据库启用一次。

## 5. 登录项目数据库

使用地址、端口、用户、数据库和密码登录：

```bash
psql -h localhost -p 5432 -U customer_service -d smart_customer_service -W
```

常用 `psql` 命令：

```text
\l                         查看数据库
\c smart_customer_service  切换数据库
\du                        查看用户和角色
\dx                        查看扩展
\dt                        查看当前数据库的表
\d knowledge_chunks        查看表结构
\q                         退出
```

## 6. 配置项目连接

进入后端目录并设置环境变量：

```bash
cd backend
export DATABASE_URL='postgresql+asyncpg://customer_service:本地开发密码@localhost:5432/smart_customer_service'
export DASHSCOPE_API_KEY='百炼 API Key'
```

如果密码包含 `@`、`:`、`/`、`#` 等保留字符，需要先对密码进行 URL 编码。不要将数据库密码或 API Key 写入仓库。

## 7. 初始化数据库结构

业务表由项目代码管理，不需要手工编写建表 SQL。设置 `DATABASE_URL` 后执行：

```bash
cd backend
uv run python -m script.init_database
```

初始化脚本会：

1. 执行 `CREATE EXTENSION IF NOT EXISTS vector`。
2. 创建 `knowledge_documents` 表。
3. 创建 `knowledge_chunks` 表。
4. 创建 `conversations` 表。
5. 创建 `messages` 表。

然后构建并写入知识库索引：

先同步依赖，再用少量文章验证：

```bash
cd backend
uv sync --dev
uv run python -m script.build_knowledge_index --limit 3
```

建表完成后可以登录数据库检查：

```sql
\dt
\d knowledge_documents
\d knowledge_chunks
\d conversations
\d messages
```

确认少量数据构建正常后，再执行全量构建：

```bash
uv run python -m script.build_knowledge_index
```

## 8. 常见问题

### `role "customer_service" does not exist`

说明 PostgreSQL 服务正常，但应用用户尚未创建。按照“创建项目用户和数据库”一节，以本地管理员身份创建该角色。

### `extension "vector" is not available`

确认已安装 pgvector：

```bash
brew install pgvector
```

如果 PostgreSQL 是在安装 pgvector 后才安装的，可以重装 pgvector，使扩展文件关联到当前 PostgreSQL：

```bash
brew reinstall pgvector
```

### `permission denied to create extension "vector"`

应用用户权限不足。使用本地 PostgreSQL 管理员进入 `smart_customer_service` 数据库，执行：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### 无法连接 `localhost:5432`

先检查并启动服务：

```bash
brew services list
brew services start postgresql@17
```

### 备份本地数据库

使用 `pg_dump` 备份，不要直接复制运行中的数据目录：

```bash
pg_dump -h localhost -U customer_service -d smart_customer_service -F c -f smart_customer_service.dump
```
