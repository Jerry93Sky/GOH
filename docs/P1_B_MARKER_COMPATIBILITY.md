# Coop Checkpoint 1.0.4 — marker contract

Исследован неизменяемый release tag `coop-checkpoint-v1.0.4`, peeled commit `e7f13b3c2ab9d764a351afca4005ed034c918cca`. Проверяемые исходники: [`coop_checkpoint.c`](https://github.com/nicko996/goh-coop-mods/blob/e7f13b3c2ab9d764a351afca4005ed034c918cca/mods/coop-checkpoint/coop_checkpoint.c) и [`mission_patch.h`](https://github.com/nicko996/goh-coop-mods/blob/e7f13b3c2ab9d764a351afca4005ed034c918cca/mods/coop-checkpoint/mission_patch.h). Путь исходника закреплён по commit SHA, а не по движущемуся branch.

## Наблюдаемое поведение кода

### `arm_reload()`

- Отказывается писать marker, если process memory не содержит checkpoint archive или current mission path.
- Marker path берётся из `GetTempPathW()` и дополняется именем `coop_checkpoint_reload_<role>.txt`; `mod_start()` использует `client` для game client и `server` для локального сервера.
- Записывает `g_mission_path`, затем `g_ckpt_archive`; если mapping был обнаружен для той же mission, добавляет `players <base> <count>`.
- Runtime `remember_checkpoint_path()` удаляет суффикс `.sav` из save path и добавляет `/`, потому что loader затем добавляет имена `map.scn`, `mission.scn`, `game.scn`. Поэтому второй marker line — виртуальный archive base, а не Windows path к `.sav` и не имя файла с суффиксом.

### `read_marker()`

- Проверяет файл в `%TEMP%`. Если время после его `ftLastWriteTime` превышает 15 минут, удаляет файл и возвращает отказ.
- Читает первые две строки mission/archive. Третья строка принимается как mapping только если `0 ≤ base < 0x12` и `count > 0`; иначе reset remap state (`base=-1`, `count=0`). Некорректный/пустой обязательный line отвергается.

### `on_scene_load()`

- Обходит marker path при загрузке из сохранения/editor (`paths != NULL`). При обычном старте сцены читает marker и сравнивает требуемый и текущий mission path case-insensitive.
- Если нет marker или mission не совпала, делает обычную загрузку сцены и marker не используется.
- При совпадении удаляет marker, вызывает штатный load-from-save path builder для archive base и загружает archive. Значит одноразовый marker может быть потерян до того, как полный игровой load завершился; это требует отдельной диагностики при live тесте.
- Если mapping отсутствует, source log сообщает, что owners не remap'ятся. В нашем MODE B mapping обязателен; идентификаторы не генерируются автоматически.

## Независимая реализация

`recovery_harness.parse_marker()` моделирует только формат/TTL/граничные значения. `prepare_marker()` — наш собственный writer, который получает точные runtime значения от оператора и проверяет локальную `.sav` копию отдельно от виртуального archive base. Он не копирует third-party implementation и не меняет DLL. Writer предназначен только для изолированного эксперимента: создание marker само по себе не подтверждает, что любой user-provided archive base корректно разрешается процессом/профилем.

В архиве release исходников проверялся текст на commit выше; отдельная лицензия на репозитории ранее не обнаружена. Поэтому third-party source/binary не входят в проект или сборку. Публичная страница релиза — ссылка пользователю; правовой статус зависит от правообладателя.
