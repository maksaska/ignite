# Демо: переполнение тома с логами (disk_full_test)

Шпаргалка для показа коллегам двух тестов из `tests/ignitetest/tests/disk_full_test.py`.
Общее про демо-режим и `demo_pause` — в [README.md](README.md), раздел *Demo Mode (Breakpoints)*.

## Что показываем

| Тест | Сценарий |
|---|---|
| `test_node_survives_full_log_volume` | Том с логами забит внешней записью (`dd`-файл). Узел жив, нагрузки идут, логи молчат. Удаляем файл-заполнитель — логи растут снова. |
| `test_node_survives_log_volume_filled_by_stdout` | Сервис на узле льёт `System.out` (по ~10 МБ/с), сам забивает том с логами. Узел снова жив и держит нагрузку, логи молчат, а сервис продолжает писать (в никуда). После `enlarge_volume` тому добавляют место (remount, файлы сохраняются) — `ignite.log` растёт снова; между старыми и новыми строками виден «разрыв», который оставила полная ёмкость. |

Том — tmpfs 32 МБ, смонтированный тестом поверх `/mnt/service/logs` узла. Data-томов это не касается:
они лежат на ФС контейнера, узел живой.

## Запуск

Из `modules/ducktests/tests`. Требуется глобал `disk_full_tests=true` (тесты-репродуцеры по умолчанию
не запускаются) и `demo_pause` для пауз. Узел в прогоне один, `-n 4` хватает.

Терминал 1:

```bash
./docker/run_tests.sh -n 4 --test-runner-timeout 5400000 \
  -gj '{"disk_full_tests": true, "demo_pause": "*", "demo_pause_timeout_sec": 900}' \
  -t ./ignitetest/tests/disk_full_test.py::DiskFullTest.test_node_survives_full_log_volume

./docker/run_tests.sh -n 4 --test-runner-timeout 5400000 \
  -gj '{"disk_full_tests": true, "demo_pause": "*", "demo_pause_timeout_sec": 900}' \
  -t ./ignitetest/tests/disk_full_test.py::DiskFullTest.test_node_survives_log_volume_filled_by_stdout
```

`--test-runner-timeout` поднят: все паузы теста тратят один бюджет ducktape (по умолчанию 30 мин),
и без этого последняя пауза сократится почти до нуля.

Терминал 2 (там же, из `modules/ducktests/tests`):

```bash
python docker/demo_console.py     # [Enter] продолжить, [c] продолжить всё, [a] abort
```

Можно и без консоли: `cat .ducktests-demo/paused.txt`, затем `touch .ducktests-demo/continue-<N>`.

## Паузы

Оба теста: `cluster-up` → `volume-full` → `volume-freed` / `volume-enlarged`.

В баннере каждой паузы, кроме списка сервисов, печатается четыре секции — и почти всё, что нужно
показать, уже на экране:

- `VOLUMES` — `df` по тому логов (`ignitetest-tmpfs`, его монтирует тест) и по тому данных
  (`/mnt/service` на ФС контейнера, `overlay`: там `partitions`, `wal`, `wal_archive`).
- `LAST 5 S` — два замера с разницей в 5 с, по строке на каждое:
  - `ignite.log` и `console.log`: размер, прирост и `growing` / `stopped`, а пока на томе 0 байт —
    `no space` (полный tmpfs ещё дописывает хвост последней страницы файла, до 4 КБ, поэтому прирост
    там бывает не ноль); у `ignite.log` ещё время последней строки и сколько секунд назад она записана;
  - `gap HH:MM:SS -> HH:MM:SS` под `ignite.log`, когда место вернулось: сколько узел не мог писать
    (самая длинная пауза между строками, от 10 с);
  - `load puts`: сколько put легло в Ignite, прирост и ошибки;
  - `flood lines` на паузе `volume-full` флуд-теста: сколько строк написал сервис, прирост.
- `LAST LINES` — по 3 последние строки каждого лога, обрезанные до 100 символов.
- `SHOW` — готовые к вставке команды с **фактическим** hostname узла: `control.sh --baseline`,
  `watch` по размерам логов, хвосты логов, `progress->` клиента, `lines->` флудера.

Имя узла меняется от прогона к прогону (в проверенном прогоне узел был `ducker02`, load-клиент —
`ducker03`) — берите его из баннера. Все команды ниже — те же, что в секции `SHOW`.

### 1. `cluster-up` — кластер поднялся

Показать, где что смонтировано и сколько свободного места (в `VOLUMES` уже видно, но можно и руками):

```bash
docker exec ducker02 findmnt -rn -S ignitetest-tmpfs -o TARGET,SIZE,USED,AVAIL,USE%
# /mnt/service/logs  32M  ...   <- том с логами, tmpfs от теста
docker exec ducker02 df -h /mnt/service/work/db   # data-том на ФС контейнера (overlay), не tmpfs
docker exec ducker02 tail -n 5 /mnt/service/logs/ignite.log   # логи пишутся
```

Статус кластера:

```bash
docker exec ducker02 bash -c "/opt/ignite-dev/bin/control.sh --host ducker02 --baseline"
docker exec ducker02 bash -c "/opt/ignite-dev/bin/control.sh --host ducker02 --state"
```

### 2. `volume-full` — том забит

В `VOLUMES` видно `32M 32M 0 100%`. Показать, что запись логов прекратилась — снять размеры дважды
с интервалом в несколько секунд (или `watch`) и убедиться, что не меняются:

```bash
docker exec ducker02 stat -c '%s %n' /mnt/service/logs/ignite.log /mnt/service/logs/console.log
# ... подождать ~10 с ...
docker exec ducker02 stat -c '%s %n' /mnt/service/logs/ignite.log /mnt/service/logs/console.log
# или непрерывно (Ctrl-C — остановить):
watch -n 2 "docker exec ducker02 stat -c '%s %n' /mnt/service/logs/ignite.log /mnt/service/logs/console.log"
# по отдельности:
watch -n 2 "docker exec ducker02 stat -c '%s' /mnt/service/logs/ignite.log"
watch -n 2 "docker exec ducker02 stat -c '%s' /mnt/service/logs/console.log"
```

`watch -n 2 "..."` — повторять команду внутри каждые 2 секунды и показывать вывод; так на глазах
у аудитории видно, что числа (размеры файлов) перестают меняться.

Последние строки (у flood-сервиса строки по 1 КБ, поэтому `cut`):

```bash
docker exec ducker02 tail -n 5 /mnt/service/logs/ignite.log
docker exec ducker02 tail -n 5 /mnt/service/logs/console.log | cut -c -100
```

Что при этом работает (узел жив, не упал, нагрузка идёт):

```bash
docker exec ducker02 bash -c "/opt/ignite-dev/bin/control.sh --host ducker02 --baseline"
docker exec ducker02 bash -c "/opt/ignite-dev/bin/control.sh --host ducker02 --state"
docker exec ducker03 grep 'progress->' /mnt/service/logs/ignite.log | tail -n 5   # puts и ошибки клиента, раз в секунду
docker exec ducker04 grep 'lines->' /mnt/service/logs/ignite.log | tail -n 5      # флуд-тест: строки сервиса
```

`progress-><puts> <errors><-` — сколько значений клиент положил в Ignite и сколько puts упало.
Пока том с логами полон, puts продолжают идти (данные на другом томе), ошибок нет.

На паузе флуд-теста сервис продолжает лить в `console.log` (в переполненный том, ошибки глотает) —
`stat` покажет, что размер `console.log` стоит, а `lines` в логе flood-приложения продолжает расти
(в баннере — строка `flood lines`).

В `docker exec` пишите `ignite.log`, а не `ignite*.log`: шаблон раскрывает шелл ноутбука, а не
контейнера, и команда ищет файл с буквальным именем `ignite*.log`. Шаблон работает только внутри
`bash -c "..."`.

### 3. `volume-freed` / `volume-enlarged` — место есть, логи растут

В `VOLUMES` снова есть свободное место (`rm` файла-заполнителя или remount до 128M — файлы сохранены).
Снять размеры дважды — теперь они растут:

```bash
docker exec ducker02 stat -c '%s %n' /mnt/service/logs/ignite.log /mnt/service/logs/console.log
# ... ~10 с ...
docker exec ducker02 stat -c '%s %n' /mnt/service/logs/ignite.log /mnt/service/logs/console.log
docker exec ducker02 tail -n 5 /mnt/service/logs/ignite.log    # свежие таймстампы
```

В секции `LAST 5 S` баннера это же видно без ручных команд: оба лога `growing`, у `ignite.log` свежая
последняя строка, а под ней `gap HH:MM:SS -> HH:MM:SS, N s without a line` — сколько узел не мог писать.

Растут оба лога: `ignite.log` (метрики раз в секунду) и `console.log` (тихий вывод Ignite: те же метрики
в коротком виде). Во флуд-тесте сервис к этой паузе остановлен, `console.log` растёт только за счёт них.
В тесте с файлом-заполнителем на паузе `volume-full` хвост `console.log` — обрезанный стек ENOSPC от
log4j2: сообщение об ошибке записи само не поместилось на том.

## Замечания

- Имена пауз можно выбирать: `"demo_pause": "volume-full"` — только вторая пауза.
- В demo-режиме — один тест за раз, без `--max-parallel`: каталог управления один.
- Если менялся Java-код (`StdoutFloodApplication` и т. п.), пересобрать jar до запуска:
  `./mvnw package -pl modules/ducktests -am -Pall-java -DskipTests` (контейнеры берут classpath
  из bind-mount'а репозитория, из `modules/ducktests/target/`).
- Проверено 2026-10-21 из WSL: оба теста c `demo_pause="*"` — PASS, все баннеры отрендерены
  (сессии `2026-10-21--001`, `--002`). Дыра в `ignite.log` без пауз в демо — около 25-30 с; на демо
  она длится, сколько стоит пауза `volume-full`.
