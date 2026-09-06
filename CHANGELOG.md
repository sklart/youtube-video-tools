# Changelog

## 1.0.0 - 2026-09-06

- Разделён монолитный `video_tools.py` на пакет `youtube_video_tools`.
- Сохранены старый launcher и `video_tools.bat`; добавлена команда `video-tools`.
- Добавлены atomically written state-файлы, archive lock, строгий parser источников и validation временного видео.
- `doctor` проверяет `ffmpeg`, cache и отсутствие конфигурации без аварийного завершения.
- Первый `download` допускает отсутствующий `yt-dlp-archive.txt`.
- Добавлены GitHub Actions, `pyproject.toml` и расширенные regression-тесты.
