# Examples

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
