# Ручное руководство P1-A: TEST-A и TEST-B

## Статус оборудования

На машине Codex GoH не обнаружен. В текущем проекте нет игровых DLL. Это руководство рассчитано на владельца двух игровых ПК; оно не меняет локальную установку автоматически.

## Перед началом

1. На обоих ПК запустить `tools/find_goh_installation.ps1`. Если Steam library не нашлась, указать папку вручную:

   ```powershell
   .\tools\find_goh_installation.ps1 -GamePath 'D:\SteamLibrary\steamapps\common\Call to Arms - Gates of Hell'
   ```

2. Записать точный `call_to_arms.exe` FileVersion и SHA-256 из JSON отчёта; оба ПК должны совпадать. Автор release 1.0.4 сообщает тест на GoH 1.069.0.
3. Подготовить отдельную тестовую установку и отдельные копии профилей. До установки сохранить исходный `version.dll`, mod list и папку `Documents\My Games\gates of hell\profiles`.
4. Скачать `goh-coop-checkpoint-v1.0.4.zip` только со страницы [официального GitHub release](https://github.com/nicko996/goh-coop-mods/releases/tag/coop-checkpoint-v1.0.4). На обоих ПК использовать одну сборку. Проект не распространяет этот файл.
5. Если установлен GOHSE, оставить его `binaries\x64\version.dll` нетронутым: v1.0.4 включает bridge. Если уже стоит иной proxy/mod loader, не заменять его без проверки в тестовой копии. При любом сомнении отложить тест.
6. В Options → Mods включить `coop_checkpoint` у обоих участников. Сверить версию DLL, data mod, порядок mod list и mission. Сохранить loader/mod logs из `%TEMP%`.

## TEST-A: same-session baseline

1. Начать одну короткую историческую coop-миссию с одним host и одним guest.
2. Выбрать отряд каждого игрока; записать owner/игрока, число бойцов, характеристики, позицию и потери. Записать ресурсы, objective и trigger, который ещё не сработал.
3. Нажать Save и дождаться завершения сохранения. Не продолжать тест, если отсутствует любой `coop_ckpt.sav` или host server `coop_ckpt_srv.sav`.
4. Изменить игровое состояние: передвинуть группы, получить потери или выполнить часть objective. Записать изменения.
5. Нажать Reload. Зафиксировать выход в lobby, запуск миссии, появление обоих игроков и состояние мира.
6. Сравнить время симуляции, группу каждого игрока, ownership, характеристики, objective/trigger и ошибки/desync. Наблюдать ещё 10 минут.

PASS TEST-A требует: обе стороны восстановились; состояние совпало с точкой Save; управление принадлежит ожидаемым игрокам; objective/trigger ведут себя корректно; 10 минут не выявили рассинхронизацию. Скриншоты карты сами по себе недостаточны.

## TEST-B: новое lobby после закрытия процессов

Выполнять только после PASS TEST-A.

1. Создать новую полную контрольную точку и сделать файловую копию client/server архивов. Запустить coordinator `capture`; время checkpoint указать из записанной отметки.
2. Нажать штатный Reload и дождаться marker request в логах всех процессов.
3. Когда host вернулся в lobby, оба игрока закрывают весь процесс игры. Записать копии каждого `%TEMP%\coop_checkpoint_reload_<client|server>.txt`; убедиться, что markers есть и им менее 15 минут.
4. На тех же Windows users перезапустить игру. Создать новое lobby тем же host и игроками, тем же порядком, mission/build/mod list. Host запускает ту же миссию.
5. Если хотя бы один participant marker отсутствует/истёк, profile path изменился или архив недоступен, не считать попытку валидным восстановлением; зафиксировать FAIL/INCONCLUSIVE.
6. Сравнить те же groups, unit characteristics, ownership, resources/objectives/trigger и quant/game clock. Наблюдать не менее 10 минут.

PASS TEST-B — только при завершении старой session, новом lobby, входе обоих клиентов, восстановлении всех контрольных характеристик и owners, правильном trigger и отсутствии desync за период наблюдения. Два игрока на карте без этих проверок — не успех.

## Сохранение evidence

- Собрать `goh_coop_checkpoint_*`, loader logs и server logs на обоих ПК из `%TEMP%`.
- Сохранить копии `.sav`, marker files до старта новой миссии, снимки экрана/видео и таблицу наблюдений до Save/после Load.
- Запустить `compare_snapshot_trees.ps1` только на копиях архивов. Для coordinator используйте уникальные checkpoint ID и явное checkpoint time.
- После полного протокола создавать отдельный JSON на каждый TEST и каждый `test_run_id`; failed run не затирать. `RESTORE_VERIFIED`/`RESTORE_FAILED` в coordinator — ручная аттестация по существующим evidence files.
- Просмотреть логи и скрыть Windows usernames, profile IDs, Steam IDs и локальные пути перед передачей. Не присылать полный профиль.

Форма завершения: TEST ID; test_run_id; game build + exe SHA-256; mod/data-mod версии; roster/host/order; mission path; checkpoint ID/time; файлы и hashes; краткая таблица состояния; ошибки/desync; ссылки или имена evidence files; итог PASS/FAIL/INCONCLUSIVE.
