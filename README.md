# GOH Coop Recovery

Независимый экспериментальный Windows x64 инструмент для исследования восстановления исторических кооперативных миссий Call to Arms – Gates of Hell: Ostfront из checkpoint после закрытия игровой сессии.

Текущий результат — **P1-C.1 / V0.2.1 Preflight Hardening**: автономный менеджер собирает локальные метаданные с явным provenance, проверяет отдельные архивы участников и хоста, и готовит role-specific marker только после строгой локальной проверки. Это подготовка к игровым экспериментам, не подтверждённый игровой recovery. `TEST-A=NOT_RUN`, `TEST-B=NOT_RUN`.

## Ограничения

- Реальной загрузки нового лобби на двух клиентах в этой среде не проверяли.
- Локальное `checkpoint #N` не является общим идентификатором. Для preflight требуется ручной JSON протокол источников всех клиентов и server process хоста. Он не доказывает сетевую синхронность.
- Client и server marker готовятся отдельно из точных логов соответствующих процессов. Путь каждого marker берётся из папки его role-specific лога, а не из TEMP менеджера.
- Проект не добавляет игровые hooks/DLL, не меняет сетевой протокол, ресурсы игры, аутентификацию или защитные проверки.

## Документы

- [Результаты P1-C.1](docs/P1_C1_RESULTS.md)
- [Первый запуск с другом](docs/P1_C1_FIRST_RUN_RU.md)
- [Наблюдение](docs/OBSERVATION_RU.md)
- [Порядок подготовки recovery](docs/RECOVERY_WIZARD_RU.md)
- [TEST-A](docs/TEST_A_RU.md) и [TEST-B](docs/TEST_B_RU.md)

## Проверки и сборка

```powershell
python -m compileall -q tools
python -m unittest discover -s tools/tests -v
python -m PyInstaller --noconfirm --clean --onefile --windowed --name GOHRecoveryManager --paths tools tools/goh_recovery_manager.py
```

Windows workflow `.github/workflows/windows-v0.2.1.yml` собирает EXE, запускает smoke test, тесты и создаёт `GOH-Coop-Recovery-V0.2.1-FieldTest-win64.zip` как Actions artifact. Сборка не устанавливает файлы в игру и не заменяет `version.dll`.

## Независимость и лицензии

Проект реализован независимо; исходники и бинарники Coop Checkpoint и CoA Script Extender сюда не включены. Использованные опубликованные контракты и проверка условий лицензирования описаны в [P0/P1 документах](docs/RISKS_AND_LICENSES.md). Используйте внешние инструменты только согласно лицензиям их авторов.
