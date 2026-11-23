# Установка GOH Coop Recovery V0.2.1 Experimental

## Запуск программы

1. Скачайте `GOH-Coop-Recovery-V0.2.1-FieldTest-win64.zip` из секции **Artifacts** последнего успешного [Windows Actions run для `main`](https://github.com/Jerry93Sky/GOH/actions/workflows/windows-v0.2.1.yml?query=branch%3Amain).
2. Распакуйте ZIP в отдельный пользовательский каталог, например `C:\GOH-Recovery-Test`.
3. Проверьте SHA-256 `GOHRecoveryManager.exe` по `checksums.sha256`.
4. Запустите `GOHRecoveryManager.exe` обычным способом. Для файловых операций игра не требуется.

## Работа с игрой

Для реальных кооперативных испытаний нужна тестовая установка *Call to Arms – Gates of Hell: Ostfront*, отдельная резервная копия профиля и одинаковые game build/mods у участников. Совместимый Coop Checkpoint пользователь получает и устанавливает отдельно по [странице релиза автора](https://github.com/nicko996/goh-coop-mods/releases/tag/coop-checkpoint-v1.0.4).

Не копируйте менеджер в каталог игры. Программа не устанавливает DLL, не заменяет `version.dll`, не изменяет игровые ресурсы, профили, сетевую аутентификацию или защитные проверки. Сборка экспериментальная: TEST-A/B ещё не выполнены.

Подробная последовательность: [первый запуск](P1_C1_FIRST_RUN_RU.md). Локальные данные по умолчанию сохраняются в `%LOCALAPPDATA%\GOH Coop Recovery`; проверяйте их перед ручным удалением.
