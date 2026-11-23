# GOH Coop Recovery

Независимое исследование восстановления исторических кооперативных миссий Call to Arms – Gates of Hell: Ostfront (Windows x64). Цель ближайшего этапа — оценить восстановление с контрольной точки через новое лобби. Подключение к уже продолжающейся миссии вынесено в отдельное исследование.

## Состояние P1-A

Ветка `codex/p1-a-runtime-validation` развивает Recovery Coordinator: история TEST-A–F неизменяема, источники сверяются до/после копирования, планы истекают/устаревают и отдельно показывают физически доступный и подтверждённый игрой checkpoint. Игра и два клиента не обнаружены; игровое восстановление не проверено.

Исследовательская база и границы выводов: [результаты P1-A](docs/P1_A_RESULTS.md), [runtime compatibility](docs/P1_RUNTIME_COMPATIBILITY.md), [протокол TEST-B](docs/P1_SESSION_RESTORE_PROTOTYPE.md), [ручное руководство](docs/P1_MANUAL_TEST_GUIDE_RU.md), [анализ границы сессии](docs/P0_5_SESSION_BOUNDARY_ANALYSIS.md), [матрица TEST-A–F](docs/TEST_MATRIX.md), [риски и лицензии](docs/RISKS_AND_LICENSES.md).

## Стенд

`tools/compare_snapshot_trees.ps1` формирует read-only сравнение двух скопированных каталогов сохранений: относительные пути, размеры, SHA-256 и добавленные/удалённые/изменённые файлы. Он не расшифровывает формат игры и не доказывает равенство игрового состояния. См. инструкции в `docs/TEST_MATRIX.md`.

`tools/find_goh_installation.ps1` обнаруживает Steam libraries, manifests, game executable, профили сохранений и временные GOH logs. `tools/recovery_coordinator.py` копирует peer/server `.sav` в новый immutable package, сверяет стабильность источников, пишет версионируемый manifest и планирует TEST-A–F. Время checkpoint и групповые метки вводит оператор; coordinator не извлекает их из архивов. Для запуска требуется Python 3.10+.

```powershell
.\tools\compare_snapshot_trees.ps1 `
  -Before 'E:\GOH-evidence\A\before' `
  -After  'E:\GOH-evidence\A\after' `
  -OutFile 'E:\GOH-evidence\A\comparison.csv'
```

Работайте с копиями файлов сохранения. Не устанавливайте сторонние DLL в рабочую папку игры.

Автоматические тесты: `python -m unittest discover -s tools/tests -v`. Они используют синтетические файлы и проверяют только файловые свойства. Полевые тесты остаются `NOT_RUN`; см. [итоговый отчёт P1-A](docs/P1_A_RESULTS.md).

`src/` содержит только заметки. Экспериментальные игровые hooks и DLL не реализованы.
