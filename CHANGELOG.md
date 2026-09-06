# Changelog

## 1.0.0 - Stage 1.3

- Все команды переведены с compatibility `print` на `get_console()` и явные уровни вывода.
- `--quiet` скрывает обычные сообщения, но не подтверждает операции и не скрывает предупреждения перед destructive prompt.
- Unicode-safe writer перенесён в `Console.emit()`; прямые вызовы command-модулей безопасны для legacy stdout.

## 1.0.0 - Stage 1.1

- Исправлен Unicode-вывод при прямом вызове команд в Windows-совместимом окружении.
- Нормализованы пути preflight-проверки пересортировки для Windows и Linux.
- Console применяется из CLI без перехвата и постфильтрации stdout; quiet-режим не скрывает prompts изменяющих команд.
- `doctor` устойчив к неверным типам TOML, валидирует timeout SponsorBlock и диагностирует повреждённый cache.
- CI разделён на матричные unit-тесты и независимую quality/packaging проверку.

## 1.0.0 - Stage 1.2

- `--quiet` больше никогда не добавляет `--yes`: опасные операции требуют обычного подтверждения, если `--yes` не задан явно.
- Дополнены safety-тесты archive lock, Ctrl+C, cleanup окружения, atomic cache и временной замены видео.
- Улучшены Unicode fallback и точный Console output.

## 1.0.0 - 2026-09-06

- Разделён монолитный `video_tools.py` на пакет `youtube_video_tools`.
- Сохранены старый launcher и `video_tools.bat`; добавлена команда `video-tools`.
- Добавлены atomically written state-файлы, archive lock, строгий parser источников и validation временного видео.
- `doctor` проверяет `ffmpeg`, cache и отсутствие конфигурации без аварийного завершения.
- Первый `download` допускает отсутствующий `yt-dlp-archive.txt`.
- Добавлены GitHub Actions, `pyproject.toml` и расширенные regression-тесты.
