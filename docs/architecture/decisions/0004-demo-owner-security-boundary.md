# 0004 — Security boundary и DemoOwner adapter

## Status

Accepted

## Context

Demo/MVP должен выполнять workspace, document, indexing и query операции без
внешнего IdP и `Authorization` header. При этом domain services не должны
зависеть от JWT claims, OIDC middleware или будущих RBAC/ACL tables, чтобы
production authentication можно было добавить без изменения их контрактов.

## Decision

Application boundary использует immutable `PrincipalContext` и
`AuthorizedWorkspaceContext`, а получение identity и проверку workspace access
задаёт протоколами `IdentityResolver` и `WorkspaceAccessPolicy`. В режиме
`GRAPH_BLIZZ_AUTH__MODE=demo_owner` приложение явно устанавливает
`DemoOwnerIdentityResolver` и `DemoOwnerAccessPolicy`: configured owner не
требует credentials и получает полный фиксированный набор Demo/MVP permissions.

## Consequences

Demo запускается без OIDC, principals, memberships и ACL schema. Domain code
получает server-resolved `workspace_id` и `storage_key` вместе с permissions,
не доверяя namespace из HTTP input. Пока production adapters не реализованы,
выбор другого auth mode завершается явной ошибкой и не откатывается неявно в
`demo_owner`.

## Alternatives considered

- Встроить проверку header в endpoints: создаёт зависимость domain/API к
  конкретному authentication mechanism и усложняет будущую замену на OIDC.
- Создать principals/memberships для demo owner: добавляет преждевременную
  persistence dependency, которая не нужна единственному bootstrap actor.
