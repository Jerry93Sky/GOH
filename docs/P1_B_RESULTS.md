# P1-B V0.1 — реализация и статус

Дата: 2026-11-23. Основа: `codex/p1-a-runtime-validation`. Целевая готовность: `READY_FOR_GAME_TEST`; статус реального движка `GAME_VERIFIED` запрещён до двухклиентского теста.

## Реализовано

- `tools/recovery_harness.py`: независимый пакет участника, проверка ZIP path allowlist, metadata consistency, file size и SHA-256, локальный import без перезаписи, resume после смоделированного падения менеджера. Клиентский архив обязателен для каждого пользователя; серверный допускается только у хоста.
- Mode A simulator: чтение уже существующих авторских marker, проверка возраста 15 минут и формата, player mapping bounds и сравнение mission path без учёта регистра. GUI не вооружает Mode A.
- Mode B: подготовка локального client/server marker после ready-confirmation; требуется целая контрольная точка или импортированные метаданные, соответствующий архив, internal mission path, exact virtual archive base path, participant ID, одинаковые mission/build/mod fingerprint, host role для server marker, точный `player_mapping` в допустимых границах. Неизвестные данные блокируют запись. Существующий marker не перезаписывается.
- `tools/goh_recovery_manager.py`: GUI на русском, Steam library поиск и ручной выбор установки, отображение Windows file version (если доступно), обнаружение известных файлов/каталогов модов, просмотр/проверка/backup checkpoints, импорты/экспорт пакетов, диагностика и sanitized JSON export.
- Собственная Windows x64 упаковка через PyInstaller и Actions. Третьи игровые бинарники/DLL не входят в дистрибутив; собственных game hooks/DLL нет.
- Руководства: установка, первый запуск, TEST-A/TEST-B и troubleshooting.

## Проверка

- Автоматические тесты синтетических файлов: **39 PASS** (последний прогон). Реальный build без установленной игры не проверяется.
- Python compileall: PASS.
- Smoke test Python GUI components: PASS. Smoke test Windows EXE следует после сборки.
- Маркерный формат проверен по release source и симулируется unit tests; это не вызов игрового loader.
- TEST-A/TEST-B в настоящей игре: **NOT RUN**. GOH не обнаружена; это не `GAME_VERIFIED`.
- GitHub Actions artifact появляется после успешного workflow run для ветки. До первого успешного опубликованного run прямой URL артефакта неизвестен.

## Ограничения

Дистрибутив не гарантирует, что внутренний mission path, archive base path или player mapping верны для вашей миссии; их следует получить из согласованного фактического runtime лога. Import переносит только локально выделенные архивы; он не передаёт network data и не подтверждает, что клиентский/серверный набор достаточен. Version info исполняемого файла не всегда является игровым build. Каталоги модов не означают, что мод включён в пользовательском профиле.

Реальные TEST-A и TEST-B требуют двух игровых установок/участников, тестового профиля, официального мода отдельно у каждого участника и protocol evidence. Программа не устанавливает и не конфигурирует его.
