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

В баннере каждой паузы, кроме списка сервисов, печатается три секции — и почти всё, что нужно показать,
уже на экране:

- `VOLUMES` — смонтированные тома с заголовком колонок и подписью к каждой строке: tmpfs поверх
  лог-директории (от теста) и корневое ФС контейнера (`overlay` — это docker overlay, там живут
  data-том `partitions`, `wal`, `wal_archive`).
- `LOGS` — размеры `ignite.log` и `console.log` **в два снимка с разницей в 5 с** (`now` / `+5s`)
  и по 3 последние строки каждого. Если том полон — размеры не двигаются и таймстампы в `+5s`
  старые; если место есть — размеры растут и таймстампы свежие. Между старыми и новыми строками
  виден «разрыв» — период, когда узел не мог писать.
- `SHOW` — готовые к вставке команды с **фактическим** hostname узла: статус кластера через
  `control.sh`, `watch` по размерам логов (сразу оба, и по отдельности), хвосты логов, и
  `progress->` из лога load-приложения (сколько puts легло в Ignite и сколько ошибок).

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
docker exec ducker03 grep 'progress->' /mnt/service/logs/ignite*.log | tail -n 5   # puts и ошибки клиента, раз в секунду
```

`progress-><puts> <errors><-` — сколько значений клиент положил в Ignite и сколько puts упало.
Пока том с логами полон, puts продолжают идти (данные на другом томе), ошибок нет.

На паузе второго теста сервис продолжает лить в `console.log` (в переполненный том, ошибки глотает) —
`stat` покажет, что размер `console.log` стоит, а `lines` в логе flood-приложения продолжает расти.

### 3. `volume-freed` / `volume-enlarged` — место есть, логи растут

В `VOLUMES` снова есть свободное место (`rm` файла-заполнителя или remount до 128M — файлы сохранены).
Снять размеры дважды — теперь они растут:

```bash
docker exec ducker02 stat -c '%s %n' /mnt/service/logs/ignite.log /mnt/service/logs/console.log
# ... ~10 с ...
docker exec ducker02 stat -c '%s %n' /mnt/service/logs/ignite.log /mnt/service/logs/console.log
docker exec ducker02 tail -n 5 /mnt/service/logs/ignite.log    # свежие таймстампы
```

В секции `LOGS` баннера это же видно без ручных команд: в `now` — старые строки (таймстамп до
переполнения), в `+5s` — новые (после восстановления), и размеры `A -> B` выросли.

Во втором тесте растёт только `ignite.log` (metrics раз в секунду): flood к этой паузе остановлен,
`console.log` статичен. В хвостах обоих логов виден «разрыв» — старые строки до переполнения и новые
после восстановления; в `console.log` первого теста между ними ENOSPC-стек log4j2.

## Замечания

- Имена пауз можно выбирать: `"demo_pause": "volume-full"` — только вторая пауза.
- В demo-режиме — один тест за раз, без `--max-parallel`: каталог управления один.
- Если менялся Java-код (`StdoutFloodApplication` и т. п.), пересобрать jar до запуска:
  `./mvnw package -pl modules/ducktests -am -Pall-java -DskipTests` (контейнеры берут classpath
  из bind-mount'а репозитория, из `modules/ducktests/target/`).
- Проверено 2026-10-20: `test_node_survives_full_log_volume` c `demo_pause="*"` — PASS, все три баннера
  отрендерены (включая два снимка логов, `control.sh` и `progress->`).
