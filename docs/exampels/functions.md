# Examples

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
