# TODO

## Stabilization Before Stage 2

- [x] Идентичность файлов и schema_version для безопасного resume/apply.
- [x] Общий путь состояния, включая resort preflight и errors.log.
- [x] Изоляция unit-тестов и проверка normal/relocated layout с отсутствующим/невалидным config.
- [x] Блокировки записи состояния, отчётов и архива.
- [x] Crash-тесты журнала, timeout процесса и cleanup перекачивания.
- [x] Базовый Command API, первая команда: dates.
- [x] Декомпозиция bookmarks на пять модулей.
- [x] Типизированные Settings и диагностика TOML.
- [x] .gitattributes; массовая нормализация не выполнялась.
- [ ] Постепенно переводить остальные команды на Command API без изменения CLI.

## Stage 2

- [ ] Pipeline для последовательного обслуживания выбранных папок.
- [ ] Availability audit для удалённых видео.
- [ ] SQLite-каталог архива.
- [ ] Perceptual duplicate detection и checksum manifest.
- [ ] Профили загрузки и расширенные subtitle policies.
- [ ] GUI.

## Completed in Stage 1

- [x] Модульная архитектура и compatibility launcher.
- [x] Единый parser источников и ID.
- [x] Atomic state writes и archive lock.
- [x] Проверка временного видео перед заменой.
- [x] `doctor` с ffmpeg, cache и проверкой конфигурации.
- [x] CI, packaging, Ruff и example-конфигурация.
- [x] Stage 1.1: cross-platform регрессии, Console, hardened doctor и packaging smoke test.
- [x] Stage 1.2: safety quiet-подтверждений, дополнительные проверки временных файлов и cleanup CLI.
- [x] Stage 1.3: unified Console, корректный quiet и Unicode-safe output.

Stage 1 complete: modular architecture, safe state handling, cross-platform CI,
typed source parsing, unified Console and reliable quiet semantics.
