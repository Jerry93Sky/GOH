# Независимая архитектура

P0–P1-B не содержат игрового DLL/hook. Проект разделяет файловую логику, marker compatibility adapter, графический менеджер и будущий исследовательский game adapter.

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
