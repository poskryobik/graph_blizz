# Examples

## Graph RAG запрос к workspace

`QueryService` получает runtime только через авторизованный workspace context,
выполняет retrieval/generation и возвращает минимальные sources из `READY`
документов того же workspace.

### Example

```python
from backend.query import QueryService

service = QueryService(document_repository, runtime_registry)
result = await service.query(authorized_workspace, "Что описано в документах?")
```

## Загрузка и чтение документа через Demo API

Multipart upload сохраняет immutable source revision и атомарно создаёт durable
indexing job. Ответ содержит `action=created`, `status=PENDING`, `revision` и `job_id`; физические
ключи формируются сервером и не входят в запрос или ответ.

### Example

```bash
curl -F 'file=@guide.md;type=text/markdown' \
  http://localhost:8000/v1/workspaces/12345678-1234-5678-1234-567812345678/documents

# {"action":"created","id":"...","revision":1,"status":"PENDING","job_id":"...",...}

curl \
  http://localhost:8000/v1/workspaces/12345678-1234-5678-1234-567812345678/documents
```

## Идемпотентный upsert документа

`PUT` со стабильным `document_id` создаёт документ только один раз. Повтор с теми
же bytes возвращает текущую активную ревизию с `action=unchanged` и `job_id=null`.

### Example

```bash
curl -X PUT -F 'file=@guide.md;type=text/markdown' \
  http://localhost:8000/v1/workspaces/12345678-1234-5678-1234-567812345678/documents/aaaaaaaa-1234-5678-1234-567812345678

# {"action":"unchanged","id":"aaaaaaaa-...","revision":1,"status":"READY","job_id":null,...}
```

## Индексация сохранённого документа

`IndexingService` читает original source через storage boundary, выбирает parser
по MIME type или имени файла и вставляет разобранный текст в LightRAG runtime,
полученный только из авторизованного workspace context.

### Example

```python
from backend.indexing import IndexingService

service = IndexingService(repository, source_service, parser_registry, runtimes)
ready_document = await service.index(authorized_workspace, uploaded_document)
```

## Добавление ревизии документа

`DocumentSourceService` сохраняет новые bytes под отдельным номерным object key и
регистрирует следующую ревизию, не меняя `document_id`. Активация выполняется
отдельно после успешной обработки.

### Example

```python
revision = await source_service.add_revision(
    document=current_document,
    content=updated_source,
)
await repository.activate_revision(
    workspace_id=current_document.workspace_id,
    document_id=current_document.id,
    revision=revision.revision,
)
await repository.commit()
```

## Проверка соединения с Neo4j

`Neo4jConnectivity` проверяет доступность настроенного Neo4j через Bolt и
гарантирует закрытие driver при выходе из контекстного менеджера.

### Example

```python
from backend.config import ApplicationSettings
from backend.storage import Neo4jConnectivity

settings = ApplicationSettings()
with Neo4jConnectivity(settings.neo4j) as connectivity:
    connectivity.check()
```
