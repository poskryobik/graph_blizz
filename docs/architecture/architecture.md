# Архитектура Graph RAG Platform — greenfield Demo/MVP-first

## 1. Назначение системы

Система представляет собой multi-workspace Graph RAG-платформу для загрузки текстовых документов и исходного кода, построения knowledge graph и vector index, а также выполнения запросов по накопленным знаниям.

Основной Graph RAG engine — **LightRAG Core**. Для graph storage используется **Neo4j**, для vector storage — **Qdrant**, для application metadata и workflow state — **PostgreSQL**, для оригинальных файлов — **S3-compatible object storage (MinIO)**. Embedding-модель обслуживается отдельным **vLLM** на NVIDIA GPU. Генеративная модель подключается через настраиваемый OpenAI-compatible API.

Архитектура проектируется **с полного нуля**. Никакая часть OIDC, RBAC, ACL, service accounts, member management или production security не считается уже реализованной или обязательной для запуска Demo/MVP.

Главная цель первого этапа — максимально короткий работающий сценарий:

```text
start stack
    -> create workspace
    -> upload document
    -> index with LightRAG
    -> query workspace
    -> receive answer + source metadata
```

До завершения MVP система работает от одного доверенного владельца через явный `demo_owner` security adapter. Отсутствие полноценной пользовательской авторизации не должно отключать или ограничивать workspace, document, indexing и query functionality.

---

## 2. Приоритеты и границы

Приоритеты расположены в следующем порядке:

1. получить воспроизводимый end-to-end Graph RAG Demo;
2. превратить Demo в устойчивый single-owner MVP;
3. только после этого добавить полноценную multi-user security model;
4. затем добавить production hardening, observability и deployment capabilities.

На Demo/MVP сознательно **не являются prerequisite**:

- внешний Identity Provider;
- JWT/OIDC validation;
- таблицы пользователей и service accounts;
- workspace membership;
- RBAC/ACL enforcement для разных пользователей;
- member management API;
- authorization isolation между пользователями;
- distributed tracing;
- Kubernetes;
- MCP;
- production-grade disaster recovery.

При этом архитектурная граница security вводится с самого начала, чтобы позднее заменить owner-заглушку на OIDC/RBAC без переписывания Graph RAG domain logic.

---

## 3. Этапы разработки

### 3.1. Demo

Demo считается готовым, когда на чистом репозитории и чистых persistent volumes можно:

1. запустить `./scripts/init.sh`;
2. дождаться readiness основных сервисов;
3. создать workspace;
4. загрузить `.txt`, `.md` или текстовый source-code файл;
5. сохранить оригинальный файл в MinIO;
6. проиндексировать документ через LightRAG;
7. выполнить query в том же workspace;
8. получить ответ и `document_id`/filename источника;
9. диагностировать ошибку по `request_id` в structured logs.

Допустимые упрощения Demo:

- один логический владелец;
- нет JWT/OIDC;
- нет member API;
- indexing может выполняться inline внутри API request;
- нет durable job queue;
- нет document revisions;
- код может парситься как обычный текст;
- только минимальная provenance information.

### 3.2. MVP

MVP превращает Demo в устойчивый single-owner Graph RAG:

- immutable document revisions;
- durable jobs;
- отдельный `rag-worker`;
- idempotent upsert;
- update/delete lifecycle;
- retry/restart recovery;
- AST-aware parsing;
- rich provenance;
- reindex;
- multi-workspace data isolation;
- persistence/restart E2E.

Security всё ещё может работать в `demo_owner` mode.

### 3.3. Post-MVP

После функционального MVP реализуются:

- persisted principals;
- OIDC authentication;
- workspace membership;
- RBAC/ACL;
- service accounts;
- member management;
- authorization isolation E2E;
- расширенный audit;
- network/secrets hardening;
- OpenTelemetry;
- Kubernetes-readiness;
- MCP facade;
- disaster-recovery automation.

---

## 4. Security boundary с первого дня

Graph RAG domain services не должны зависеть напрямую от JWT claims, OIDC middleware или ACL tables.

Вводятся application-level контракты:

```python
class IdentityResolver(Protocol):
    async def resolve(self, request) -> PrincipalContext:
        ...


class WorkspaceAccessPolicy(Protocol):
    async def authorize(
        self,
        principal: PrincipalContext,
        workspace: Workspace,
        permission: Permission,
    ) -> AuthorizedWorkspaceContext:
        ...
```

`WorkspaceService`, `DocumentService`, `IndexingService`, `QueryService` и LightRAG runtime boundary используют только нормализованный `AuthorizedWorkspaceContext`.

### Demo/MVP implementation

```text
DemoOwnerIdentityResolver
DemoOwnerAccessPolicy
```

### Post-MVP implementation

```text
OIDCIdentityResolver
RBACWorkspaceAccessPolicy
```

Таким образом, authentication/authorization является заменяемым adapter layer, а не prerequisite Graph RAG functionality.

---

## 5. Demo owner mode

Для Demo/MVP используется:

```text
GRAPH_BLIZZ_AUTH__MODE=demo_owner
```

В этом режиме:

- `Authorization` header не требуется;
- внешний IdP не нужен;
- JWT не валидируется;
- таблицы principals/memberships не нужны для принятия решения;
- каждый HTTP request получает один bootstrap owner identity из application configuration;
- owner получает все permissions, необходимые для Demo/MVP operations.

Пример контекста:

```python
AuthorizedWorkspaceContext(
    principal_id=DEMO_OWNER_ID,
    principal_type=PrincipalType.USER,
    workspace_id=workspace.id,
    storage_key=workspace.storage_key,
    permissions=ALL_OWNER_PERMISSIONS,
)
```

`DEMO_OWNER_ID` — технический идентификатор runtime actor. Он не означает наличие полноценной user database.

Для безопасности конфигурации `demo_owner` должен считаться development/demo mode. Production deployment после внедрения OIDC должен запускаться с production security adapter и не иметь неявного fallback в `demo_owner`.

---

## 6. Demo architecture

```text
                         ┌────────────────────┐
                         │      Clients       │
                         │  curl / UI / CLI   │
                         └─────────┬──────────┘
                                   │ HTTP
                                   ▼
┌─────────────────────────────────────────────────────────────┐
│                         rag-api                             │
│                                                             │
│  DemoOwnerIdentityResolver                                  │
│  DemoOwnerAccessPolicy                                      │
│  WorkspaceService                                           │
│  DocumentService                                            │
│  IndexingService                                            │
│  QueryService                                               │
│  LightRAGRuntimeRegistry                                    │
│  structured logging                                         │
└───────┬───────────┬────────────┬────────────┬───────────────┘
        │           │            │            │
        ▼           ▼            ▼            ▼
   PostgreSQL     MinIO        Qdrant       Neo4j
        │
        └─────────────────────┐
                              ▼
                         LightRAG Core
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
             vLLM embeddings      External LLM
```

На Demo отдельный `rag-worker` отсутствует.

---

## 7. MVP architecture

После подтверждения Demo indexing переносится в durable worker без переписывания domain use case:

```text
Client
  │
  ▼
rag-api
  │
  ├── PostgreSQL metadata/jobs
  ├── MinIO source
  │
  ▼
job = PENDING

rag-worker
  │
  ├── claim job
  ├── heartbeat / retry
  ▼
IndexingService
  │
  ▼
LightRAG Core
  ├── PostgreSQL LightRAG storage
  ├── Qdrant
  ├── Neo4j
  ├── vLLM embeddings
  └── External LLM
```

Ключевой инвариант: переход `inline -> worker` меняет orchestration, но не core indexing logic.

---

## 8. `rag-api`

`rag-api` — единственная пользовательская HTTP boundary Graph RAG.

Предпочтительная реализация — FastAPI.

### Demo responsibilities

- health/readiness;
- DemoOwner identity/access adapter;
- workspace CRUD;
- document upload/read;
- durable upload job creation с ответом `PENDING` и `job_id`;
- Graph RAG query;
- stable application errors;
- request correlation и structured logging.

### MVP additions

- job status;
- update/delete;
- reindex;
- richer source metadata.

### Post-MVP additions

- OIDC authentication;
- real RBAC/ACL;
- member management;
- security audit coverage.

---

## 9. Workspace

Workspace — основной scope изоляции knowledge base с первого дня.

Минимальная модель:

```text
workspaces
----------
id          UUID
name        string
slug        string
storage_key string
status      ACTIVE | ARCHIVED
created_at
updated_at
```

`storage_key`:

- генерируется сервером;
- уникален;
- не зависит от `name`/`slug`;
- не меняется при rename;
- используется как physical LightRAG namespace;
- не принимается из пользовательского request body.

На Demo/MVP все workspace принадлежат одному логическому владельцу приложения. Персональные membership записи появятся только Post-MVP.

---

## 10. PostgreSQL

PostgreSQL хранит application metadata и позднее durable workflow state.

### До F027

```text
app.workspaces
app.documents
```

### С F028

```text
app.document_revisions
app.jobs
app.workspace_settings
```

### Post-MVP security

```text
app.principals
app.workspace_members
app.audit_events
```

При необходимости multi-tenant model также добавляется Post-MVP отдельной migration, а не блокирует Demo.

LightRAG PostgreSQL-backed storage логически отделяется от application schema:

```text
app.*
lightrag.*
```

Application migrations не должны управлять внутренней схемой LightRAG.

---

## 11. Object storage

Оригинальный source обязательно сохраняется в MinIO/S3 **до** индексирования.

Demo object key:

```text
workspace/{workspace_id}/document/{document_id}/source
```

После введения revisions в MVP:

```text
workspace/{workspace_id}/document/{document_id}/revision/{revision}/source
```

Source object является основанием для retry/reindex/recovery.

---

## 12. Document model — до F027

Минимальная таблица:

```text
documents
---------
id
workspace_id
source_key
filename
source_type
object_uri
content_hash
status
created_at
updated_at
```

Статусы:

```text
UPLOADED
INDEXING
READY
FAILED
```

Для Demo достаточно первой загрузки logical document. Update/delete/revisions не входят в critical path.

---

## 13. Document model — с F027

После Demo добавляются:

```text
documents
    id
    workspace_id
    source_key
    active_revision
    status


document_revisions
    document_id
    revision
    content_hash
    object_uri
    parser_version
    chunk_schema_version
    index_schema_version
    created_at
```

`document_id` остаётся стабильным между версиями.

Одинаковый SHA-256 при upsert означает idempotent NOOP.

Новые source-объекты получают ключ
`workspace/{workspace_id}/document/{document_id}/revision/{revision}/source`.
Старые Demo-строки мигрируют в ревизию 1 с сохранением существующего URI; SQL
migration не перемещает объекты в MinIO.

---

## 14. Parsing

### Demo

```text
.txt  -> PlainTextParser
.md   -> MarkdownParser
.py   -> PlainTextParser
.js   -> PlainTextParser
.ts   -> PlainTextParser
.tsx  -> PlainTextParser
.java -> PlainTextParser
.go   -> PlainTextParser
```

Цель — доказать end-to-end retrieval, а не сразу максимизировать качество code chunking.

### MVP

Добавляется Tree-sitter и semantic chunking по:

```text
module
class
function
method
```

Metadata:

```text
path
language
symbol
symbol_type
parent_symbol
start_line
end_line
```

---

## 15. Embedding service

Embedding model работает отдельным GPU service:

```text
vllm-embeddings
```

Начальная модель:

```text
ai-sage/Giga-Embeddings-instruct-480M-0826
```

Endpoint:

```http
POST /v1/embeddings
```

`rag-api` и `rag-worker` используют единый embedding client и не загружают собственные копии модели.

GPU integration tests могут находиться в отдельном verification profile; обычные unit/contract tests используют stub.

---

## 16. External LLM

Generation/extraction выполняется через OpenAI-compatible API.

Минимальная Demo configuration:

```text
base_url
model
api_key / secret_ref
timeout
```

Один endpoint/model может использоваться одновременно для extraction и generation. Полноценные LLM profiles можно добавить после подтверждения основного Graph RAG flow.

---

## 17. Qdrant

Qdrant — единственный vector storage.

Vector writes выполняются через LightRAG integration, а не через public application API.

Workspace filter/namespace формируется сервером на основании `AuthorizedWorkspaceContext` и не может быть подменён пользовательским query parameter.

---

## 18. Neo4j

Neo4j — graph storage LightRAG.

Прямой пользовательский Cypher не является частью API.

В Demo локальный diagnostic port допустим для разработчика, но application boundary остаётся `rag-api`.

Production network hardening выполняется Post-MVP.

---

## 19. LightRAG runtime registry

Каждый application process использует `LightRAGRuntimeRegistry`:

```text
storage_key A -> LightRAG(...)
storage_key B -> LightRAG(...)
storage_key C -> LightRAG(...)
```

Registry:

- создаёт runtime только по server-resolved workspace;
- использует `storage_key` как namespace;
- поддерживает lazy initialization;
- корректно закрывает runtime;
- не принимает физический workspace namespace напрямую от клиента.

---

## 20. Current upload indexing flow

```text
POST /v1/workspaces/{id}/documents
    │
    ▼
DemoOwnerContext
    │
    ▼
validate workspace/file
    │
    ▼
store original in MinIO under immutable object key
    │
    ▼
atomically persist document revision + durable PENDING job
    │
    ▼
return document status = PENDING + job_id

rag-worker
    │
    ▼
claim job for the immutable revision
    │
    ▼
parse revision source
    │
    ▼
IndexingService.index(...)
    │
    ▼
LightRAG insert
    ├── embeddings -> vLLM
    ├── vectors    -> Qdrant
    ├── graph      -> Neo4j
    └── KV/status  -> PostgreSQL
    │
    ▼
document = READY
```

HTTP request не выполняет parsing или indexing. Если indexing в `rag-worker`
завершается ошибкой, document и job переходят в `FAILED`, а immutable original
source остаётся в MinIO.

---

## 21. MVP asynchronous workflow

После Demo API больше не выполняет длительный indexing inline:

```text
upload
  -> store immutable revision
  -> create durable job
  -> return PENDING + job_id
```

Worker:

```text
claim job
  -> heartbeat
  -> IndexingService
  -> SUCCEEDED / RETRY / FAILED
```

Для MVP достаточно PostgreSQL queue с `FOR UPDATE SKIP LOCKED`; Kafka/RabbitMQ не требуются.
Job ссылается на конкретную immutable revision. Активными считаются `PENDING`,
`RUNNING` и `RETRY`; PostgreSQL допускает только одну такую mutation job на logical
document. `SUCCEEDED`, `FAILED` и `CANCELLED` терминальны и друг с другом не конфликтуют.
Ошибка сохраняется только как allow-listed классификационный код; произвольные
сообщения, traceback и credentials в jobs не записываются.
Архитектурное решение: [ADR 0015](decisions/0015-durable-indexing-jobs.md).

---

## 22. Query pipeline

```text
POST /v1/workspaces/{id}/query
    │
    ▼
IdentityResolver
    │
    ▼
WorkspaceAccessPolicy
    │
    ▼
AuthorizedWorkspaceContext
    │
    ▼
LightRAGRuntimeRegistry
    │
    ▼
LightRAG query
    ├── Qdrant
    ├── Neo4j
    ├── PostgreSQL LightRAG storage
    └── External LLM
    │
    ▼
answer + sources
```

В Demo/MVP первые два шага реализованы owner adapter'ами и не требуют внешних security services.

---

## 23. Query response

Минимальный Demo contract:

```json
{
  "answer": "...",
  "workspace_id": "...",
  "sources": [
    {
      "document_id": "...",
      "filename": "README.md"
    }
  ],
  "request_id": "..."
}
```

MVP расширяет source metadata:

```text
revision
path
language
symbol
start_line
end_line
```

---

## 24. Public API — Demo

```text
GET    /health/live
GET    /health/ready

POST   /v1/workspaces
GET    /v1/workspaces
GET    /v1/workspaces/{id}

POST   /v1/workspaces/{id}/documents
PUT    /v1/workspaces/{id}/documents/{document_id}
GET    /v1/workspaces/{id}/documents
GET    /v1/workspaces/{id}/documents/{document_id}

POST   /v1/workspaces/{id}/query
```

На Demo нет `/members`, login endpoints и user administration.

---

## 25. Public API — MVP additions

```text
DELETE /v1/workspaces/{id}/documents/{document_id}
GET    /v1/jobs/{job_id}
POST   /v1/workspaces/{id}/maintenance/reindex
```

---

## 26. Error model

Публичный API не возвращает internal exceptions LightRAG/Qdrant/Neo4j.

Минимальные codes:

```text
WORKSPACE_NOT_FOUND
DOCUMENT_NOT_FOUND
DOCUMENT_CONFLICT
UNSUPPORTED_FILE_TYPE
FILE_TOO_LARGE
INDEXING_FAILED
QUERY_FAILED
LLM_UNAVAILABLE
EMBEDDING_UNAVAILABLE
INTERNAL_ERROR
```

Каждый error response содержит `request_id`.

После внедрения production security добавляются стабильные authentication/authorization errors.

---

## 27. Operational logging

Structured JSON logging входит в Demo.

Минимальные fields:

```text
timestamp
level
service
request_id
workspace_id
document_id
operation
duration_ms
result
```

Не логируются:

```text
Authorization header
JWT
API keys
LLM credentials
полный document content
raw embeddings
полные prompts/responses
```

Граница реализуется allow-list схемой, а не поиском известных секретных
паттернов. В JSON проходят только обязательные operational fields,
типизированные `OperationalEvent` и явно разрешённые scalar/metric extras.
Произвольные message, mapping keys/values и exception text отбрасываются целиком.
Это сохраняет метрики вроде `response_time_ms`, `content_type` и числовых рядов
`durations_ms`, не превращая произвольный payload в канал логирования.

Наличие или отсутствие OIDC не должно менять logging boundary.

---

## 28. Consistency model

PostgreSQL, MinIO, Neo4j и Qdrant не объединяются в distributed ACID transaction.

### Demo

Гарантируется минимум:

```text
observable state
source persisted before indexing
stable failures
manual retry possible
```

### MVP

Добавляются:

```text
idempotency
retryability
durable jobs
restart recovery
repairability
```

PostgreSQL становится координатором workflow.

---

## 29. Source of truth

Для Demo/MVP source of truth:

```text
PostgreSQL application metadata
+
MinIO original sources
```

Qdrant и Neo4j — derived indexes.

На раннем Demo автоматический disaster-recovery rebuild не обязателен, но архитектура не должна делать Qdrant/Neo4j единственным экземпляром исходных данных.

---

## 30. Docker Compose

Demo stack:

```text
rag-api
postgres
minio
qdrant
neo4j
vllm-embeddings
```

External LLM может находиться вне Compose.

MVP добавляет:

```text
rag-worker
```

Persistent volumes:

```text
postgres_data
minio_data
qdrant_data
neo4j_data
hf_cache
```

GPU назначается только `vllm-embeddings`.

Первый Compose-инкремент запускает `rag-api` и PostgreSQL в общей internal-сети.
PostgreSQL имеет healthcheck и named volume `postgres_data`; `rag-api` стартует
после состояния `healthy`. Его `/health/ready` подтверждает аутентификацию и
выполнение `SELECT 1`, тогда как `/health/live` не зависит от PostgreSQL.
Application schema создаётся только последующими versioned migrations. Детали
зафиксированы в [ADR 0002](decisions/0002-postgresql-compose-readiness.md).

---

## 31. Initialization

`./scripts/init.sh` является частью Demo exit criteria.

Скрипт:

1. проверяет prerequisites;
2. создаёт/проверяет `.env` requirements;
3. запускает Compose;
4. ждёт PostgreSQL;
5. применяет application migrations;
6. ждёт MinIO/Qdrant/Neo4j/vLLM readiness;
7. проверяет `rag-api /health/ready`;
8. не удаляет persistent data при повторном запуске.

В `demo_owner` mode скрипт **не требует** OIDC issuer, audience, JWKS или user bootstrap.

---

## 32. Production security — только Post-MVP

После завершения functional MVP вводятся следующие сущности:

```text
principals
workspace_members
```

Базовые roles:

```text
reader
writer
admin
owner
```

Permissions:

```text
workspace.read
workspace.manage
workspace.delete
query.execute
document.read
document.create
document.update
document.delete
members.read
members.manage
index.rebuild
index.repair
```

OIDC token определяет authenticated principal, а persisted membership определяет permissions.

Production adapter должен создавать тот же `AuthorizedWorkspaceContext`, который до этого создавал `DemoOwnerAccessPolicy`.

Именно это обеспечивает отсутствие влияния auth implementation на Graph RAG domain logic.

---

## 33. Migration `demo_owner -> OIDC/RBAC`

Переход выполняется без изменения document/query contracts:

```text
before:
request
 -> DemoOwnerIdentityResolver
 -> DemoOwnerAccessPolicy
 -> AuthorizedWorkspaceContext
 -> domain service


after:
request
 -> OIDCIdentityResolver
 -> RBACWorkspaceAccessPolicy
 -> AuthorizedWorkspaceContext
 -> domain service
```

Меняется только security adapter и добавляются security tables/endpoints.

Workspace `storage_key`, document ids, MinIO objects и LightRAG indexes не пересоздаются только из-за включения OIDC.

---

## 34. Security invariants

Даже в Demo фиксируются инварианты, полезные для будущего production режима:

1. `rag-api` — единственная public Graph RAG API boundary.
2. Client не выбирает physical `storage_key`.
3. Workspace context формируется сервером.
4. Neo4j/Qdrant/PostgreSQL/MinIO/vLLM не являются пользовательскими data API.
5. Original source сохраняется до indexing.
6. Application не модифицирует semantic LightRAG graph/vector entities вручную в обход LightRAG lifecycle.
7. Secrets и raw confidential content не попадают в logs.
8. `demo_owner` не должен быть неявным fallback production security mode.

Post-MVP добавляются user authorization invariants.

---

## 35. Итоговый стек

```text
API
    Python
    FastAPI

Graph RAG
    LightRAG Core

Application metadata / jobs
    PostgreSQL

Source storage
    MinIO / S3

Vector storage
    Qdrant

Graph storage
    Neo4j

Embeddings
    vLLM
    ai-sage/Giga-Embeddings-instruct-480M-0826

Generation
    OpenAI-compatible API

Demo/MVP security
    DemoOwnerIdentityResolver
    DemoOwnerAccessPolicy

Post-MVP security
    OIDC / OAuth2
    application RBAC/ACL

Code parsing
    Demo: plain text / Markdown
    MVP: Tree-sitter

Async processing
    Demo/MVP: rag-worker + PostgreSQL jobs

Logging
    structured JSON
```

---

## 36. Главная граница ответственности

```text
                    APPLICATION BOUNDARY

                         rag-api
                            │
           ┌────────────────┼────────────────┐
           │                │                │
           ▼                ▼                ▼
   Security Adapter   Domain Services   PostgreSQL
                           │
                           ├── MinIO
                           ├── LightRAG Core
                           │     ├── Qdrant
                           │     ├── Neo4j
                           │     ├── PostgreSQL storage
                           │     ├── vLLM
                           │     └── External LLM
                           │
                           └── MVP: rag-worker
```

Security adapter определяет **кто и что может делать**.

Domain services определяют **как работает Graph RAG functionality**.

На Demo/MVP security adapter является owner-only заглушкой. После MVP он заменяется полноценным OIDC/RBAC adapter без изменения основной функциональности.

---

## 37. Критерии завершения этапов

### Demo exit criteria

```text
clean repo
 -> ./scripts/init.sh
 -> create workspace
 -> upload document
 -> READY
 -> query
 -> answer + source
```

Без OIDC, JWT и member setup.

### MVP exit criteria

```text
Demo flow
+
async worker
+
revisions/update/delete
+
retry/restart recovery
+
AST-aware source processing
+
reindex
+
multi-workspace data isolation
```

### Post-MVP security exit criteria

```text
OIDC principal
 -> workspace membership
 -> RBAC permission
 -> same AuthorizedWorkspaceContext
 -> same domain services
```

Такой порядок разработки минимизирует time-to-demo и одновременно сохраняет чистую границу для последующего production security.

## Architecture decisions

- [0009 — Единый клиент embeddings](decisions/0009-shared-embedding-client.md)
