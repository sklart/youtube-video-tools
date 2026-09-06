# video_tools

Набор утилит для обслуживания архива видео в папке `E:\Video\Youtube`.

Главная точка входа: [video_tools.py](/E:/Video/Youtube/video_tools.py)
Удобный запуск из `cmd.exe`: [video_tools.bat](/E:/Video/Youtube/video_tools.bat)

## Что умеет

`video_tools.py` объединяет основные операции по архиву:

1. `doctor` — проверка окружения и настроек.
2. `download` — скачивание новых видео YouTube.
3. `bookmarks` — обновление встроенных глав SponsorBlock в контейнере видео.
4. `subtitles` — загрузка субтитров к локальным видео.
5. `rename` — переименование файлов по ID и дате.
6. `resort` — раскладка файлов по папкам автора.
7. `duplicates` — поиск дублей по ID, размеру и SHA-256.
8. `dates` — проверка дат в именах файлов.
9. `resolution` — проверка разрешения видео через `ffprobe`.
10. `inventory` — экспорт CSV-инвентаризации.
11. `archive-sync` — синхронизация `yt-dlp-archive.txt` с локальными файлами.
12. `report` — отчёт о проблемах архива без чтения видеопотоков.

## Важные принципы

- Инструмент работает по именам файлов, путям и файловым метаданным там, где это возможно.
- Для обычного анализа архив не перечитывается как медиаконтент.
- Подпапки с видео считаются рабочим архивом; служебные файлы лежат в корне.
- После выполнения пункта интерактивного меню программа возвращается в меню, а не закрывается.

## Быстрый старт

Запуск меню:

```bat
video_tools.bat
```

Прямой запуск команды:

```bat
python video_tools.py doctor
python video_tools.py download
python video_tools.py duplicates
```

## Интерактивное меню

При запуске без аргументов открывается меню:

1. Проверить окружение (`doctor`)
2. Скачать избранное YouTube (`download`)
3. Скачать субтитры (`subtitles`)
4. Предпросмотр переименования (`rename --dry-run`)
5. Выполнить переименование (`rename --apply`)
6. Предпросмотр сортировки (`resort --dry-run`)
7. Выполнить сортировку (`resort --apply`)
8. Отменить последнюю сортировку (`resort --undo-last`)
9. Найти дубли (`duplicates`)
10. Проверить даты в именах (`dates`)
11. Проверить разрешение видео (`resolution`)
12. Создать CSV-инвентаризацию (`inventory`)
13. Проверить архив загрузок (`archive-sync --dry-run`)
14. Синхронизировать архив загрузок (`archive-sync --apply`)
15. Отчёт о проблемах архива (`report`)
16. Обновить закладки SponsorBlock (`bookmarks --apply`)
0. Выход

## Настройка

Основная конфигурация хранится в [config.toml](/E:/Video/Youtube/config.toml).

Текущие секции:

- `[paths]`
  - `cookies` — путь к cookies YouTube
  - `yt_dlp` — команда или путь к `yt-dlp`
  - `ffprobe` — команда или путь к `ffprobe`
  - `ffmpeg` — команда или путь к `ffmpeg` для обновления встроенных глав
- `[subtitles]`
  - `folders` — папки, для которых разрешена загрузка субтитров
  - `languages` — языки субтитров
  - `pause_seconds` — пауза между запросами
- `[sorting]`
  - `pause_seconds` — пауза при сетевых запросах
  - `max_retries` — число повторов
  - `allow_unknown` — разрешать ли папку `Unknown`
- `[download]`
  - `sync_archive_before_download` — синхронизировать ли архив перед скачиванием

## Общие опции CLI

Для большинства команд доступны:

```text
--root PATH
--config PATH
--verbose
--quiet
```

## Переменные окружения

Машинозависимые пути и команды можно переопределять без правки `config.toml`:

```text
VIDEO_TOOLS_ROOT
VIDEO_TOOLS_CONFIG
YOUTUBE_COOKIES_FILE
VIDEO_TOOLS_YT_DLP
VIDEO_TOOLS_FFPROBE
VIDEO_TOOLS_FFMPEG
```

Приоритет такой:

1. явный аргумент командной строки;
2. переменная окружения;
3. `config.toml`;
4. встроенное значение по умолчанию.

## Фильтрация по папкам

Для команд `subtitles`, `rename`, `duplicates`, `dates`, `resolution`, `inventory`, `report` поддерживаются:

```text
--folder NAME
--folder-file PATH
--all-folders
```

Это удобно, когда нужно обработать только часть архива.

## Обновление закладок SponsorBlock

Команда `bookmarks` сравнивает текущие встроенные главы YouTube-видео с актуальными данными SponsorBlock и, если есть различия, переписывает контейнер без перекодирования.

Примеры:

```bat
python video_tools.py bookmarks --dry-run
python video_tools.py bookmarks --apply
python video_tools.py --folder "Deep Look" bookmarks --apply
```

Что важно:

- обновляются только YouTube-видео, у которых ID найден в имени файла;
- для сравнения читаются встроенные главы через `ffprobe`;
- для записи используется `ffmpeg -c copy`, без перекодирования видеопотока;
- рекламные вставки в уже скачанном файле не вырезаются заново, обновляются только главы.

## Служебные файлы

- `yt-dlp-archive.txt` — список уже скачанных YouTube ID
- `.video-tools/journal-*.jsonl` — журнал операций
- `.video-tools/yt-dlp-cache.json` — кэш метаданных YouTube/Rutube
- `inventory.csv` и похожие CSV-выгрузки — результаты отчётов

## Что уже улучшено

- Несколько старых `.py` и `.bat` сценариев объединены в один основной файл.
- Добавлено интерактивное меню с краткой справкой.
- Убраны ANSI-артефакты в подсказках меню.
- Батник переведён на UTF-8-режим для Windows-консоли.
- Перед скачиванием можно автоматически чистить `yt-dlp-archive.txt` от ID удалённых файлов.
- Поиск дублей теперь умеет находить и точные дубликаты по хэшу.
- Кэш метаданных уменьшает повторные сетевые запросы.

## Ограничения

- `archive-sync` работает по локальным именам и найденным ID; если ID в имени нет, запись может остаться в архиве.
- Для `resolution` нужен рабочий `ffprobe`.
- Для `bookmarks` нужны рабочие `yt-dlp`, `ffprobe` и `ffmpeg`.
- Для `download`, `subtitles`, `rename`, `resort`, `duplicates` могут потребоваться сеть, cookies и `yt-dlp`.

## Полезный порядок работы

Обычно достаточно такого цикла:

1. `doctor`
2. `download`
3. `subtitles`
4. `rename --apply`
5. `resort --apply`
6. `duplicates`
7. `report`

## Дальше

План улучшений ведётся в [TODO.md](/E:/Video/Youtube/TODO.md), а история заметных изменений — в [CHANGELOG.md](/E:/Video/Youtube/CHANGELOG.md).
