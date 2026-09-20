# 0019 — Tree-sitter foundation для парсеров исходного кода

## Status

Accepted

## Context

Базовый registry обрабатывал Python, JavaScript, TypeScript/TSX, Java и Go как
обычный текст. Для следующего этапа semantic chunking нужна проверяемая AST-база,
при этом текущие контракты `ParsedDocument`, приоритет MIME type над расширением
и indexing boundary не должны измениться. Ошибочный синтаксис не должен аварийно
останавливать обработку документа, а бинарный MIME type нельзя принимать только
из-за текстового расширения файла.

## Decision

Для фиксированного набора языков используется `tree-sitter-language-pack` и
единый `TreeSitterParser`. Валидный исходник получает `syntax_status=parsed`;
дерево с syntax errors сохраняется целиком одним текстовым chunk с
`syntax_status=syntax_error_fallback` и `fallback_parser=plain_text`. Registry
явно отклоняет известные бинарные MIME types до fallback по расширению.

## Consequences

F037 сможет строить semantic chunks поверх проверенного набора Tree-sitter
грамматик, не меняя существующую границу парсеров. На текущем этапе AST не входит
в публичную модель результата, поэтому исходник по-прежнему сохраняется одним
chunk. Некорректный синтаксис индексируется детерминированно как текст, а не
теряется; это снижает качество структуры, но сохраняет доступность содержимого.

## Alternatives considered

- Отдельные Python-пакеты для каждой грамматики: увеличивают число зависимостей и
  риск несовместимых Tree-sitter ABI.
- Отклонять документ при первой syntax error: лишает retrieval частично
  корректного исходника и не выполняет требование deterministic fallback.
