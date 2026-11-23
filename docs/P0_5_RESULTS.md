# P0.5 — результаты

Дата: 2026-11-23. Ветка: `codex/p0-5-recovery-harness`.

## Реализовано

- `.gitignore` для игровых профилей/save, исполняемых файлов игры, дампов, логов, бинарных модулей без разрешения, сборок и временных файлов.
- `tools/find_goh_installation.ps1`: read-only поиск по Steam registry roots, Steam libraryfolders VDF (включая дополнительные libraries), app manifests, пользовательскому ручному пути, ожидаемым Documents save и `%TEMP%` GOH logs.
- `tools/recovery_coordinator.py`: schema v1, discovery/copy `.sav` по участникам и host server, SHA-256/size, roster/build/mission/mod consistency, `INCOMPLETE`/`FILE_VERIFIED`, атомарная публикация через staging directory, запрет перезаписи checkpoint, выбор последнего полного набора и immutable recovery plan со статусом `RESTORE_TEST_PENDING`.
- Результат `RESTORE_VERIFIED`/`RESTORE_FAILED` записывается отдельным файлом только с JSON-протоколом реального теста, существующими evidence files и явным подтверждением оператора. Это не машинная проверка факта игрового восстановления.
- [Session boundary analysis](P0_5_SESSION_BOUNDARY_ANALYSIS.md), [manual guide](MANUAL_COOP_TEST_GUIDE_RU.md) и развёрнутая [TEST-A–F matrix](TEST_MATRIX.md).

## Автоматические тесты

Запуск: `python -m unittest discover -s tools/tests -v`.

Результат: **12/12 PASS**. Проверены обнаружение synthetic install из дополнительной Steam library через VDF и appmanifest; полный набор и pending plan; отсутствие серверного архива; игнорирование несвязанного профиля; прерывание копирования и публикации manifest с последующим безопасным retry; изменение файла после захвата; повторный checkpoint ID без перезаписи; идемпотентный повтор plan и конфликт входов; выбор последнего checkpoint по операторскому времени; roster mismatch; gate статуса реального теста.

Тесты используют только синтетические файлы во временном каталоге. Они не являются тестами формата save, игры, сети или восстановления.

PowerShell-скрипты прошли parser syntax check. Запуск поиска в текущей среде не обнаружил игру, вывел пример ручного `-GamePath` и завершился кодом `1` (ожидаемый результат без установки). Отдельно ручной `-GamePath` протестирован на synthetic directory с fake exe files; это проверяет обработку аргумента, не наличие игры.

## Статусы полевых тестов

Game install: **NOT_FOUND** в перечисленных Steam registry/library путях и стандартном/OneDrive Documents-поиске. Второй компьютер/клиент недоступен.

| Сценарий | Статус | Причина |
|---|---|---|
| TEST-A Same Session Reload | NOT_RUN | Нет GoH и разрешённой тестовой сборки checkpoint |
| TEST-B New Lobby, Same Players | NOT_RUN | Нет двух клиентов |
| TEST-C New Lobby, Changed Player Order | NOT_RUN | Нет двух клиентов |
| TEST-D Client Crash | NOT_RUN | Нет игровой среды |
| TEST-E Missing Peer Snapshot | FILE_HARNESS_ONLY | Синтетический coordinator case прошёл; реальный lobby не испытывался |
| TEST-F Mission Script Continuity | NOT_RUN | Нет игры/миссии |

## Межсессионная переносимость

Статика указывает, что существующий Coop Checkpoint вооружает локальные TEMP markers при Reload, затем обрабатывает их на новом scene load той же миссии; archive path строится через штатный load-from-save path builder. Автоматический маршрут сам выходит в lobby и запускает миссию из живого host client. Marker имеет TTL 15 минут и может включать player ID range только если он был сохранён из памяти процесса.

Это не доказывает восстановление после завершения всех процессов, падения отдельного peer или создания нового lobby пользователем. Координатор консервативно требует client snapshots всех ожидаемых игроков и server snapshot; достаточность server-only архива остаётся **UNKNOWN**.

## Ручные действия

См. [полевое руководство](MANUAL_COOP_TEST_GUIDE_RU.md). Нужны две одинаковые Windows x64 установки/учётные записи, разрешённый тестовый механизм сохранения, одна историческая coop-миссия, резервные копии профилей, диагностические логи и протокол TEST-A–F. Отдельно требуется проверить сценарии с порядком игроков, crash одного client и потерянным peer archive.

## Ограничения и следующий этап

- Игра, multiplayer runtime и proprietary archive schema не были доступны.
- SHA-256 не удостоверяет корректность формата, loadability, семантическую идентичность мира или синхронность quants. Время checkpoint и mission/build/mod metadata вводятся оператором и не извлекаются из архива; coordinator проверяет согласованность этих меток и файлового состава, но не доказывает, что peer archives действительно сделаны на одном quant.
- Перенос/сохранение auth, lobby state и игровых сетевых идентификаторов не исследован.
- Лицензии внешних repos из P0 остались неподтверждёнными, поэтому игровых модулей в проект не добавлено.

**Переход к ограниченной части P1 допустим:** испытать файловый manifest/coordinator на реальных скопированных архивах и провести ручные TEST-A–F на изолированной установке после получения разрешённой checkpoint-сборки. Разработку game loader, hooks, автоматической загрузки и live reconnect начинать нельзя до результатов этих экспериментов.
