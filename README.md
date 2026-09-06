# YouTube Video Tools

Набор локальных инструментов для обслуживания скачанного видеоархива. Проект работает с файловыми именами, метаданными контейнеров и `yt-dlp`; видео не загружаются в репозиторий и не используются в CI.

## Установка

Требуется Python 3.11 или новее. Внешние программы `yt-dlp`, `ffprobe` и `ffmpeg` должны быть доступны в `PATH` либо указаны в конфигурации.

```powershell
python -m pip install -e .
video-tools doctor
```

Для разработки и проверок установите Ruff:

```powershell
python -m pip install ruff
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
```

## Запуск

Старый способ запуска сохранён:

```powershell
python video_tools.py
python video_tools.py doctor
python video_tools.py bookmarks --dry-run
```

После установки доступна console-команда:

```powershell
video-tools doctor
video-tools download
```

В Windows можно также запустить `video_tools.bat`.

## Конфигурация

Скопируйте `config.example.toml` в локальный `config.toml` и при необходимости задайте пути к cookies и внешним программам. Реальный `config.toml` игнорируется Git и никогда не должен публиковаться.

Приоритет настроек: аргументы CLI, переменные окружения, `config.toml`, значения по умолчанию. Например:

```powershell
$env:VIDEO_TOOLS_ROOT = 'D:\Video\Youtube'
$env:YOUTUBE_COOKIES_FILE = 'D:\Secrets\youtube-cookies.txt'
video-tools doctor
```

## Команды

- `doctor` — проверка Python, конфигурации, root, cookies, `yt-dlp`, `ffprobe`, `ffmpeg`, журнала и кэша.
- `download` — загрузка новых видео из «Смотреть позже».
- `bookmarks` — обновление встроенных глав SponsorBlock.
- `subtitles` — загрузка автоматических субтитров.
- `rename` — добавление даты публикации в имя файла.
- `resort` — раскладка файлов по папкам автора и отмена последнего запуска.
- `duplicates` — точные дубли и повторяющиеся YouTube ID.
- `dates`, `resolution`, `inventory`, `report`, `archive-sync` — проверки и отчёты архива.

Команды, изменяющие архив, получают межпроцессный lock. JSON-кэш и состояние сканирования SponsorBlock записываются атомарно. Перед заменой видео результат `ffmpeg` или перекачивания проверяется `ffprobe`.

## Структура

```text
youtube_video_tools/
  cli.py              # CLI и интерактивное меню
  config.py            # конфигурация и приоритеты
  console.py           # уровни вывода
  models.py            # SourceRef и parser ID
  cache.py, journal.py, state.py, locking.py
  services/            # адаптеры внешних программ
  commands/            # реализации команд
video_tools.py         # compatibility launcher
config.example.toml
```

История заметных изменений — в [CHANGELOG.md](CHANGELOG.md). Дальнейшие идеи — в [TODO.md](TODO.md).
