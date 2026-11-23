# GOH Coop Recovery

Независимый экспериментальный проект для исследования восстановления исторических кооперативных миссий Call to Arms – Gates of Hell: Ostfront (Windows x64). Текущая ветка `codex/p1-b-experimental-build` добавляет P1-B офлайн Recovery Manager поверх P0–P1-A.

## Статус

**V0.1 цель: READY_FOR_GAME_TEST. GAME_VERIFIED: НЕТ.** Реальная игра и два клиента на машине сборки недоступны. Windows GUI и пакетный формат проходят синтетические проверки; они не доказывают загрузку сохранений игровым движком. Результаты: [P1-B](docs/P1_B_RESULTS.md), [исследование маркеров P1-B](docs/P1_B_MARKER_COMPATIBILITY.md).

## Что умеет V0.1

- Переносимый русский `GOHRecoveryManager.exe` (Windows x64): поиск Steam installation, ручной выбор игры, показ файловой версии, диагностика Coop Checkpoint/data mod.
- Просмотр и SHA-256-проверка P1-A checkpoint, резервное копирование, экспорт отдельного пакета для каждого участника и импорт только в локальные папки.
- В host package можно включить локальный server snapshot; гостевой пакет содержит только архив соответствующего гостя.
- Режим A читает локальные авторские TEMP markers. Режим B экспериментально формирует marker только с точной mission path, archive base path, локальным архивом, build/mod metadata и player ID mapping.
- Core не содержит игровых hooks, DLL или стороннего бинарного кода. Программа не подключается к сети игры, не меняет `version.dll` и не устанавливает мод автоматически.

Установка и тест: [INSTALL](docs/INSTALL_RU.md), [первый запуск](docs/FIRST_RUN_RU.md), [TEST-A](docs/TEST_A_RU.md), [TEST-B](docs/TEST_B_RU.md), [диагностика](docs/TROUBLESHOOTING_RU.md).

## Marker contract

Исследованы исходники официального release tag Coop Checkpoint 1.0.4 на исходном коммите `e7f13b3c2ab9d764a351afca4005ed034c918cca`. Marker хранит внутренний mission path, виртуальный archive base path и опциональную строку `players base count`; это **не** абсолютный Windows путь к `.sav`. Loader ищет role-specific marker в Windows TEMP, принимает только marker не старше 15 минут, сравнивает mission path без регистра, удаляет marker при принятии и вызывает штатный path builder. Точный mapping необходим для owner remap. См. [дословные источники и независимый контракт](docs/P1_B_MARKER_COMPATIBILITY.md).

Исходник и бинарный release третьей стороны не распространяются. Coop Checkpoint пользователь получает отдельно по [официальной странице автора](https://github.com/nicko996/goh-coop-mods/releases/tag/coop-checkpoint-v1.0.4); проверяйте условия лицензии у правообладателя.

## Сборка и тесты

```powershell
python -m unittest discover -s tools/tests -v
python -m PyInstaller --noconfirm --clean --onefile --windowed --name GOHRecoveryManager --paths tools tools/goh_recovery_manager.py
dist\GOHRecoveryManager.exe --smoke-test
```

Workflow [Windows x64](.github/workflows/windows-v0.1.yml) запускает тесты и сборку и публикует ZIP как GitHub Actions artifact. [Успешный run #2](https://github.com/Jerry93Sky/GOH/actions/runs/45144792103) создал artifact `GOH-Coop-Recovery-V0.1-Experimental-win64`. Workflow не устанавливает игру и не выставляет `GAME_VERIFIED`.

**Блокер:** отсутствуют реальные двухклиентские TEST-A/TEST-B, точный mapping, корректные runtime metadata для выбранной миссии/build, подтверждение межсессионной загрузки и лицензия для повторной публикации кода Coop Checkpoint. Непроверенные values нельзя вводить по догадке.
