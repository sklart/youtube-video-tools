# TODO

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
