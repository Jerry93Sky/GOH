# GOH Coop Recovery

Независимое исследование восстановления исторических кооперативных миссий Call to Arms – Gates of Hell: Ostfront (Windows x64). Цель ближайшего этапа — оценить восстановление с контрольной точки через новое лобби. Подключение к уже продолжающейся миссии вынесено в отдельное исследование.

## Состояние P0.5

Ветка `codex/p0-5-recovery-harness` добавляет read-only обнаружение установки, файловый инспектор/координатор и автоматические тесты. Координатор не загружает игровой процесс и не заявляет, что архив можно восстановить. Игра и два клиента в этой среде не обнаружены; межсессионный игровой тест не проводился.

Исследовательская база и границы выводов: [технический анализ](docs/P0_ENGINE_RESEARCH.md), [граница сессии](docs/P0_5_SESSION_BOUNDARY_ANALYSIS.md), [возможность восстановления](docs/P0_RECOVERY_FEASIBILITY.md), [архитектура](docs/ARCHITECTURE.md), [матрица TEST-A–F](docs/TEST_MATRIX.md), [ручное руководство](docs/MANUAL_COOP_TEST_GUIDE_RU.md), [результаты P0.5](docs/P0_5_RESULTS.md), [риски и лицензии](docs/RISKS_AND_LICENSES.md).

## Стенд

`tools/compare_snapshot_trees.ps1` формирует read-only сравнение двух скопированных каталогов сохранений: относительные пути, размеры, SHA-256 и добавленные/удалённые/изменённые файлы. Он не расшифровывает формат игры и не доказывает равенство игрового состояния. См. инструкции в `docs/TEST_MATRIX.md`.

`tools/find_goh_installation.ps1` обнаруживает Steam libraries, manifests, game executable, профили сохранений и временные GOH logs. `tools/recovery_coordinator.py` копирует найденные peer/server `.sav` в новый immutable package, генерирует manifest и план со статусом `RESTORE_TEST_PENDING`; время checkpoint и consistency metadata вводит оператор, координатор не может извлечь их из бинарного архива. Для запуска требуется Python 3.10+.

```powershell
.\tools\compare_snapshot_trees.ps1 `
  -Before 'E:\GOH-evidence\A\before' `
  -After  'E:\GOH-evidence\A\after' `
  -OutFile 'E:\GOH-evidence\A\comparison.csv'
```

Работайте с копиями файлов сохранения. Не устанавливайте сторонние DLL в рабочую папку игры.

Автоматические тесты: `python -m unittest discover -s tools/tests -v`. Они используют синтетические файлы и проверяют только файловые свойства. Полевые тесты остаются `NOT_RUN`; см. [итоговый отчёт](docs/P0_5_RESULTS.md).

`src/` содержит только заметки. Экспериментальные игровые hooks и DLL не реализованы.
