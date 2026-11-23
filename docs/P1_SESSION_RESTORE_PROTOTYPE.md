# TEST-B: минимальный протокол межсессионного эксперимента

## Цель

Проверить, достаточно ли уже имеющихся per-client/server archive files и TEMP reload marker, чтобы после полного завершения старой сетевой сессии открыть новый lobby и снова загрузить ту же миссию. Протокол не меняет сетевой протокол и не реализует новый loader.

## Что переносится за границу сессии по исходникам

1. Архив каждого игрового клиента: `Documents\My Games\gates of hell\profiles\<id>\save\coop_ckpt.sav`.
2. Архив локального host server: соответствующий `coop_ckpt_srv.sav`.
3. На каждой машине локальный `%TEMP%\coop_checkpoint_reload_<role>.txt`: mission path, archive path, опциональные `players <base> <count>`. TTL — 15 минут.
4. Build, data mod, мод-порядок, target mission и player roster должны быть зафиксированы тестовой записью. Игра не сериализует эти значения в coordinator manifest автоматически.

Память процесса, которая используется при штатном Reload: текущий `g_mission_path`, `g_ckpt_archive`, `g_players_base/count/mission`, scheduler globals, hooks и состояние UI auto-restart. Исходники не доказывают, что всё это обязано сохраниться: часть значений читается из marker/archive при новом scene load, а UI автоматический restart после закрытия процесса теряется. Ручной новый lobby проверяет, достаточно ли оставшейся persistent части.

## Как marker запускает восстановление

При игровом Reload команда `achievement` доходит до server/clients; каждый процесс вооружает собственный marker. При обычном последующем start сцены hook проверяет marker и сопоставляет mission path (case-insensitive). Совпавший marker передаёт сохранённый archive path штатному load-from-save path builder. Миссия должна стартовать с тем же внутренним path; совпадение только отображаемого названия не гарантирует match.

Код на загрузке берёт clock восстановленной сцены, вычитает 20 ms для client, округляет вниз до секунды и записывает одинаковую базу; server scheduler получает это значение в `+0x40`. Игроки получают повторный owner remap, если marker сохранил стартовый base/count; затем mission data script делает `autoassign`. Всё это основано на signatures/offsets и должно проверяться логами при конкретном build.

## Пошаговый TEST-B без сетевых hooks

1. Сначала успешно пройти TEST-A той же миссией/build/двумя клиентами. Записать quant/game time, состав и owner групп, objective и trigger.
2. Создать checkpoint и убедиться по источникам/логам, что появились оба client archive и host server archive. Зафиксировать SHA-256 и размеры координатором.
3. Нажать штатную кнопку Reload. Дождаться логов о принятом reload request на host client, guest client и local server; это вооружает marker-файлы.
4. Когда host оказался в lobby, до старта миссии штатно закрыть игровые процессы обоих клиентов (включая host client; local server завершится вместе с ним). На каждой машине сохранить копию marker-файла и логи. Не ждать дольше 15 минут.
5. Перезапустить игру на обоих ПК под теми же Windows-пользователями, чтобы их `%TEMP%` и profile save paths остались доступны. Создать новое coop lobby тем же host, roster, порядком игроков, mission/build/mods. Не менять участника/слот в TEST-B.
6. Host вручную запускает ту же миссию. Scene loader должен прочитать локальные markers и каждому процессу подать локальный archive path. Никакие архивы по сети не пересылаются координатором.
7. Если любой marker отсутствует/истёк или любой архив недоступен, остановить тест до start либо пометить FAIL/INCONCLUSIVE; не заставлять loader использовать архив другого peer.
8. Наблюдать минимум 10 минут и выполнить заранее выбранный trigger. Сравнить roster, owner mapping, подразделения/характеристики, objective, trigger, quant/game clock и сетевые ошибки.

## Передача ошибок и ветвления

- Если TEST-A не проходит — TEST-B не запускать; сначала сохранить logs/crash evidence и зафиксировать symptom.
- Если TEST-A проходит, но после restart процессы не используют markers, это проверяет межпроцессный marker path. Записать наличие/TTL marker, точный mission path и loader log.
- Если loader читает markers, но один архив отсутствует — остановиться; безопасный fallback не подтверждён.
- Для separate test изменения player order использовать TEST-C, не смешивать с TEST-B.

## Роль coordinator

Координатор создаёт immutable file package, фиксирует operator checkpoint time/roster/build/mod fingerprint и строит план с TTL 15 минут. Он не записывает marker, не меняет архив, не запускает игру и не передаёт данные между peers. JSON plan — чеклист для человека, не загрузочная команда.

## Вопрос о дополнительных hooks

Для первой попытки отдельный hook не требуется: опубликованный мод уже имеет scene-load hook и штатный Reload marker flow. Если TEST-B не может быть создан повторно через штатную кнопку или marker после полного relaunch не читается, потребуется минимальное исследование способа вручную вооружить per-process load path. Реализовывать такой hook до проверки code/signature/ABI на точном build и разрешённого стенда нельзя.
