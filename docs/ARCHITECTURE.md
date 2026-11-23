# Независимая архитектура

P0–P1-B не содержат игрового DLL/hook. Проект разделяет файловую логику, marker compatibility adapter, графический менеджер и будущий исследовательский game adapter.

## P1-C.1 preflight hardening

Метаданные выбираются только из двух process-specific логов, basename которых описан опубликованным исходником Coop Checkpoint: `goh_coop_checkpoint_call_to_arms.exe.log` и `goh_coop_checkpoint_call_to_arms_server.exe.log`. Идентификация типа процесса не подменяет операторскую session role `HOST`/`CLIENT`. Каждое поле сохраняет путь источника и process role.

Preflight marker требует конкретную manifest-запись владельца/роли, положительный размер, SHA-256, совпадение импортированного архива и точного файла выбранного профиля. Client/server marker — независимые файлы и процессы; marker directory принимается только как родитель соответствующего role log. Если server log не обнаружен/не выбран однозначно, автоматическая подготовка прекращается.

Сопоставление одной общей точки требует операционный JSON protocol с source path/hash и локальными sequence/quant clock каждого participant client process плюс host server process. `checkpoint #N` хранится отдельно на каждом процессе и не интерпретируется как общий ID. Даже валидный protocol получает статус `OPERATOR_PROTOCOL_RECORDED_NOT_SYNCHRONIZATION_PROOF`; сетевой ACK или движковое подтверждение не установлены.

## Компоненты

1. **Core** — `recovery_coordinator.py` и `recovery_harness.py`: immutable checkpoints, SHA-256, file stability, per-peer package import/export, manifest validation и marker simulator. Core не подключается к игре.
2. **Marker Adapter** — собственный форматный reader/writer для Coop Checkpoint 1.0.4, проверяет TTL, role filename, exact paths, архив и mapping. Writer явно экспериментален и создаёт файлы только после user confirmation; не ставит game hooks.
3. **Recovery Manager** — Tk GUI на русском для поиска/диагностики, пакетов, проверки и инструкции. PyInstaller собирает Windows standalone EXE.
4. **Game Adapter (будущий)** — интерфейс зарезервирован для отдельных исследований. В текущем продукте интерфейс отсутствует и DLL не создаётся, поскольку существующий Coop Checkpoint уже предоставляет runtime marker/scene-load code.

## Поток восстановления

```text
quant checkpoint intent
        -> peer-local capture + server capture
        -> ACK + manifest commit
        -> game/session exits or crashes
        -> new lobby validates roster/build/mods
        -> choose experimentally validated archive set
        -> load all required participants
        -> compare state and record outcome
```

Текущий пакет требует отдельный локальный клиентский archive каждого участника и локальный server archive хоста. Это файловая политика безопасности/тестирования, не доказательство движка, что все они понадобятся или будут достаточны.

## Границы

- P1 — проверка форматов/совместимости снимков и проектирование manifest/ACK на внешнем harness.
- P2+ — только после P1 и явного решения команды о live-game adapter.
- Live join в выполняющуюся миссию — отдельный исследовательский трек.
# P1-C V0.2 notes

Read-only `RuntimeMetadataCollector` records every parsed value with status, source file, UTC observation time and extraction method. `ArchivePathResolver` accepts only the source-documented archive stems under the explicitly selected profile ID; it never searches for a similarly named save. Watcher polling is limited to exact selected profile checkpoint paths and currently existing Coop Checkpoint logs. File copies go to an isolated observation store; hashes are compared to the source before publishing the observation. Stable file metadata is not an engine/network acknowledgement.

`HOST`/`CLIENT` is an operator selection. The observer does not infer network role from a process name. Local process checkpoint sequence is not shared checkpoint identity. No observation package automatically becomes a complete cross-peer checkpoint; current peer ZIP export still requires a verified coordinator manifest.
