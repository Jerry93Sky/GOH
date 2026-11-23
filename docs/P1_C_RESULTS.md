# P1-C / V0.2 — результат реализации

**Статус: EXPERIMENTAL BUILD PREPARED; READY_FOR_FIELD_TEST NOT YET ESTABLISHED.** Доступ к установленной игре и двум игровым клиентам в среде разработки отсутствует. TEST-A=NOT_RUN, TEST-B=NOT_RUN.

## Реализовано

- Отдельный `ArchivePathResolver`: точный синтаксис и роль, profile ID, наличие ожидаемого `.sav`; отказ `UNVERIFIED_PATH` без fallback-поиска. Подготовка marker блокируется, если локальный путь не разрешён или содержимое не совпадает.
- Read-only `RuntimeMetadataCollector`: provenance-поля; разбор опубликованных строк mission, local checkpoint sequence/quant, mapping, `reload ARMED`; чтение marker; профили только на выбранном уровне; bounded polling по выбранным файлам; локальная стабилизация, отдельная копия и SHA-256 с проверкой источника во время копирования.
- Вкладка наблюдения и явный операторский выбор роли HOST/CLIENT. При изменении точного `.sav` пути наблюдатель ждёт локальную стабилизацию, делает отдельную копию, проверяет hash и пишет provenance JSON. Два ожидаемых `.sav` пути отслеживаются при появлении позже; новые TEMP log-файлы требуют перезапуска watcher.
- Исправлено расположение `binaries/x64/version.dll`; добавлена инвентаризация известных имён Coop Checkpoint, GoH Mod Loader и COA Script Extender. По файлу не делается вывод совместимости или runtime-загрузки.
- Отдельный валидатор группы ZIP сверяет checkpoint, mission, build, mod fingerprint, roster, уникальность participant packages и обязательный server archive HOST. Результат прямо помечает, что это только проверка набора файлов.
- Подготовка marker может брать точные поля из Coop Checkpoint log и checkpoint manifest без JSON ввода; отсутствующие поля запрашиваются отдельно. Marker только в системный TEMP, после ручного подтверждения готовности, с 15-минутным TTL и без перезаписи.
- Диагностика включает форму append-only отчёта TEST-A/B с `PASS`, `FAIL` или `INCONCLUSIVE`. PASS/FAIL требуют ручного подтверждения реальной игры и evidence файла. Отчёт маркируется как операторский и не ставит `RESTORE_VERIFIED`.
- Пакетировщик и Windows workflow для V0.2; автоматический тест-набор содержит синтетические фикстуры.

## Извлечение и неизвестные поля

Автоматически доступны mission path (`scene load`), локальные sequence ID и quant clock, `player_mapping` из опубликованных строк, virtual archive base только из `reload ARMED`, путь/размер/hash локального файла, timestamp наблюдения, marker contents, версия ресурса EXE и SHA-256 найденных файлов мода. Статус `checkpoint written` — локальное завершение вызова, не peer ACK.

Авторский лог не доказывает shared checkpoint UUID, сетевую роль, отображаемый game build, согласованный roster целиком, mod activation одинаковый у игроков или синхронизацию результатов. Эти значения остаются `MISSING`/`UNVERIFIED` или операторскими. Стабильный размер файла их не заменяет.

Неполны относительно целевого workflow: пошаговый экран со всеми восемью отдельными стадиями, единый schema для merge peer observations, обнаружение новых log-файлов без перезапуска наблюдателя. Подтверждение готовности выполняется одной ручной формой «все готовы», а не отдельными peer ACK.

## Проверки

- `python -m unittest discover -s tools/tests -v` — 66 тестов PASS (синтетические тесты только локальной программы).
- Финальный Windows 11 x64 EXE локально собран PyInstaller 6.16.0 / Python 3.14.3; `--smoke-test` завершился кодом 0. Локальный ZIP проверен: 14 записей, EXE 12,904,717 байт, внутренний SHA-256 совпал.
- GitHub Actions Windows x64 / Python 3.12: все шаги прошли SUCCESS — 66 тестов, standalone EXE, smoke-test, проверка ZIP/checksum и upload artifact. Windows 10 отдельно не запускалась.
- Реальный GUI interaction, установленная игра и пара клиентов недоступны и не заявляются как протестированные.

## Блокеры и следующий этап

1. `reload ARMED` появляется только после штатного игрового Reload; без этой строки virtual archive base не извлекается автоматически.
2. Новый процесс/лобби может иметь другой profile ID или локальное содержимое; это требует индивидуальной проверки на каждом peer.
3. Нет подтверждения, что серверный и клиентские архивы переживают закрытие процессов и загружаются в новом lobby как согласованный набор.
4. Восстановление quant clock, миссионных триггеров, owner mapping и host-server state нужно подтвердить TEST-B.

Минимальный P1: провести TEST-A/B на двух изолированных Windows клиентах с опубликованным Coop Checkpoint 1.0.4; добавить подписываемый вручную group manifest с подтверждённым общей точкой ID и provenance; только затем завершить wizard/peer-ready экран по реальным runtime данным. Не добавлять hooks или менять сетевой протокол.

## Артефакты

Локальные файлы: `out/v02-final/GOHRecoveryManager.exe` и `out/v02-final/GOH-Coop-Recovery-V0.2-FieldTest-win64.zip` (игнорируются Git). CI artifact: `GOH-Coop-Recovery-V0.2-FieldTest-win64`, workflow [Windows V0.2 Field-Test Build](https://github.com/Jerry93Sky/GOH/actions/workflows/windows-v0.2.yml). ZIP digest от Actions upload: `sha256:9d00b422e6a71680ade0471b9795e94a560207e8d8378d8a52b415c0efd24578`.
