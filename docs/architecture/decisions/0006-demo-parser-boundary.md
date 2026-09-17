# 0006 — Базовая граница парсеров для Demo

## Status

Accepted

## Context

Indexing должен получать единый типизированный результат для обычного текста,
Markdown и исходного кода, не связываясь с конкретной реализацией парсера. На
этапе Demo нужны детерминированные chunks и metadata, но AST-разбор и
Tree-sitter относятся к следующему этапу MVP.

## Decision

Вводятся протокол `DocumentParser`, immutable-модели `ParsedDocument` и
`ParsedChunk`, а также детерминированный registry с выбором по MIME type и
расширению файла. Plain text и исходники поддерживаемых языков обрабатываются
`PlainTextParser`, Markdown — отдельным `MarkdownParser` с разбиением по
ATX-заголовкам.

## Consequences

Indexing сможет работать с единым контрактом и расширять набор парсеров без
изменения вызывающего кода. Demo сохраняет исходное содержимое документа и не
добавляет Tree-sitter; semantic chunking исходного кода остаётся задачей MVP.
Базовый Markdown parser не интерпретирует полный CommonMark AST и рассматривает
заголовки внутри fenced code blocks как обычный текст.

## Alternatives considered

- Выбирать parser непосредственно в indexing service: это связывает orchestration
  с форматами документов и усложняет последующее расширение.
- Добавить Tree-sitter сразу: это расширяет зависимости и scope Demo без пользы
  для требуемого end-to-end сценария.
