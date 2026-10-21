# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Fills one volume of a persistent server node under load and checks whether the node survives.

The tests reproduce what Ignite does today and guard no behavior, so they run only with
--globals '{"disk_full_tests": true}'.
"""
import os
import time

from ducktape.mark import matrix
from ducktape.utils.util import wait_until

from ignitetest.services.ignite import IgniteService
from ignitetest.services.ignite_app import IgniteApplicationService
from ignitetest.services.utils.control_utility import ControlUtility
from ignitetest.services.utils.ignite_configuration import IgniteConfiguration, IgniteThinClientConfiguration, \
    DataStorageConfiguration
from ignitetest.services.utils.ignite_configuration.data_storage import DataRegionConfiguration
from ignitetest.services.utils.ignite_configuration.discovery import from_ignite_cluster
from ignitetest.services.utils.ignite_spec import IgniteNodeSpecFileOutput
from ignitetest.services.utils.jvm_utils import merge_jvm_settings
from ignitetest.services.utils.ssl.client_connector_configuration import ClientConnectorConfiguration
from ignitetest.utils import cluster, ignite_versions, reproducer
from ignitetest.utils.ignite_test import IgniteTest
from ignitetest.utils.version import DEV_BRANCH, IgniteVersion

MB = 1024 * 1024

# A full tmpfs still takes writes into the rest of a file's last page.
PAGE = 4096

FILLER = "filler"

# Failure type and failed writer the node logs when the volume fills.
FAILURES = {
    "wal_archive": ("SYSTEM_WORKER_TERMINATION", "Failed to archive WAL segment"),
    "partitions": ("CRITICAL_ERROR", "StorageException"),
    "work": ("CRITICAL_ERROR", "BinaryMetadataFileStore")
}


class DiskFullTest(IgniteTest):
    """
    Each test puts a size-limited tmpfs over a directory of the node, fills it and records what the node does today.
    """
    LOAD_APP = "org.apache.ignite.internal.ducktest.tests.disk_full.DiskFullLoadApplication"

    FLOOD_APP = "org.apache.ignite.internal.ducktest.tests.disk_full.StdoutFloodApplication"

    @cluster(num_nodes=2)
    @reproducer("disk_full_tests")
    @ignite_versions(str(DEV_BRANCH))
    def test_node_survives_full_log_volume(self, ignite_version):
        """
        The log volume holds the log4j2 log, console.log and gc.log.
        """
        ignite = self.__ignite(ignite_version)
        ignite.mount_tmpfs(ignite.log_dir, 32)

        ignite.start()
        ControlUtility(ignite).activate()

        load = self.__start_load_client(ignite)

        self.pause("cluster-up", LogVolumes(ignite, load))

        fill_volume(ignite, ignite.log_dir)

        self.pause("volume-full", LogVolumes(ignite, load))

        check_serves_load_for(ignite, load, seconds=15)
        assert ControlUtility(ignite).cluster_state().state == "ACTIVE"

        for log in log_files(ignite):
            check_write_failed(ignite, log)

        free_volume(ignite, ignite.log_dir)

        self.pause("volume-freed", LogVolumes(ignite, load))

        for log in log_files(ignite):
            check_grows(ignite, log)

        load.stop()

    @cluster(num_nodes=2)
    @reproducer("disk_full_tests")
    @ignite_versions(str(DEV_BRANCH))
    def test_node_restarts_on_full_log_volume(self, ignite_version):
        """
        The node restarts while its log volume is full.
        """
        ignite = self.__ignite(ignite_version)
        ignite.mount_tmpfs(ignite.log_dir, 32)

        ignite.start()
        ControlUtility(ignite).activate()

        load = self.__start_load_client(ignite)

        self.pause("cluster-up", LogVolumes(ignite, load))

        fill_volume(ignite, ignite.log_dir)

        self.pause("volume-full", LogVolumes(ignite, load))

        ignite.stop()

        # Waits for a failed put, so the count of puts read next is final until the node is back.
        _, errors = progress(load)
        wait_until(lambda: progress(load)[1] > errors, timeout_sec=30, err_msg="The load still reaches the node")

        puts, _ = progress(load)

        # start() awaits a line in the node's new log, which the full volume refuses.
        ignite.start_async(clean=False)

        wait_until(lambda: progress(load)[0] > puts, timeout_sec=60, err_msg="No puts after the restart")

        self.pause("restarted", LogVolumes(ignite, load))

        check_serves_load_for(ignite, load, seconds=15)
        assert ControlUtility(ignite).cluster_state().state == "ACTIVE"
        assert file_size(ignite, ignite.nodes[0].log_file) == 0, "The restarted node wrote its log"

        free_volume(ignite, ignite.log_dir)

        self.pause("volume-freed", LogVolumes(ignite, load))

        for log in log_files(ignite):
            check_grows(ignite, log)

        load.stop()

    @cluster(num_nodes=3)
    @reproducer("disk_full_tests")
    @ignite_versions(str(DEV_BRANCH))
    def test_node_survives_log_volume_filled_by_stdout(self, ignite_version):
        """
        A service on the node writes to System.out until its output fills the log volume.
        """
        ignite = self.__ignite(ignite_version)
        ignite.mount_tmpfs(ignite.log_dir, 32)

        ignite.start()
        ControlUtility(ignite).activate()

        load = self.__start_load_client(ignite)

        self.pause("cluster-up", LogVolumes(ignite, load))

        flood = self.__start_flood_service(ignite)

        wait_until(lambda: free_space(ignite, ignite.log_dir) == 0, timeout_sec=60,
                   err_msg="System.out did not fill the log volume")

        self.pause("volume-full", LogVolumes(ignite, load, flood))

        lines = printed_lines(flood)

        check_serves_load_for(ignite, load, seconds=15)
        assert ControlUtility(ignite).cluster_state().state == "ACTIVE"
        assert printed_lines(flood) > lines, "The service stopped writing to System.out"

        for log in log_files(ignite):
            check_write_failed(ignite, log)

        flood.stop()

        enlarge_volume(ignite, ignite.log_dir, 128)

        self.pause("volume-enlarged", LogVolumes(ignite, load))

        for log in log_files(ignite):
            check_grows(ignite, log)

        load.stop()

    @cluster(num_nodes=2)
    @reproducer("disk_full_tests")
    @ignite_versions(str(DEV_BRANCH))
    def test_jvm_exits_on_full_jfr_volume(self, ignite_version):
        """
        JFR ends the JVM once it can't write its recording.
        """
        ignite = self.__ignite(ignite_version)
        repository = os.path.join(ignite.persistent_root, "jfr_repository")
        jfr_opts = ["-XX:StartFlightRecording", f"-XX:FlightRecorderOptions=repository={repository}"]
        ignite.spec.jvm_opts = merge_jvm_settings(ignite.spec.jvm_opts, jfr_opts)
        ignite.mount_tmpfs(repository, 32)

        ignite.start()
        ControlUtility(ignite).activate()

        load = self.__start_load_client(ignite)

        fill_volume(ignite, repository)

        ignite.await_event("An irrecoverable error in Jfr", 60, from_the_beginning=True, log_file="console.log")
        check_stops(ignite)

        load.stop()

    @cluster(num_nodes=2)
    @reproducer("disk_full_tests")
    @ignite_versions(str(DEV_BRANCH))
    def test_node_stops_on_full_volume_shared_by_logs_and_data(self, ignite_version):
        """
        One volume holds the logs and all the data, as in Ignite's default layout.
        """
        ignite = self.__ignite(ignite_version)
        ignite.mount_tmpfs(ignite.persistent_root, 256)

        ignite.start()
        ControlUtility(ignite).activate()

        load = self.__start_load_client(ignite)

        fill_volume(ignite, ignite.persistent_root)

        check_stops(ignite)

        load.stop()

    @cluster(num_nodes=2)
    @reproducer("disk_full_tests")
    @ignite_versions(str(DEV_BRANCH))
    def test_node_survives_full_wal_volume(self, ignite_version):
        """
        WAL segments are preallocated, so puts go on into a full WAL volume.
        """
        ignite = self.__ignite(ignite_version)
        wal = os.path.join(ignite.persistent_root, "wal")
        ignite.mount_tmpfs(wal, 64)

        ignite.start()
        ControlUtility(ignite).activate()

        load = self.__start_load_client(ignite)

        segments = ignite.exec_command(ignite.nodes[0], f"ls {wal}/*/*.wal 2>/dev/null | wc -l")
        assert int(segments) > 0, "No WAL segments on the volume"

        fill_volume(ignite, wal)

        check_serves_load_for(ignite, load, seconds=15)

        load.stop()

    @cluster(num_nodes=2)
    @reproducer("disk_full_tests")
    @ignite_versions(str(DEV_BRANCH))
    @matrix(volume=["wal_archive", "partitions", "work"])
    def test_node_stops_on_full_volume(self, ignite_version, volume):
        """
        The node stops once it can't write the WAL archive, the partitions or the binary metadata (in work).
        """
        ignite = self.__ignite(ignite_version)
        path = os.path.join(ignite.persistent_root, volume)
        ignite.mount_tmpfs(path, 64)

        ignite.start()
        ControlUtility(ignite).activate()

        load = self.__start_load_client(ignite)

        fill_volume(ignite, path)

        check_stops(ignite)
        check_failure(ignite, *FAILURES[volume])

        load.stop()

    @cluster(num_nodes=2)
    @reproducer("disk_full_tests")
    @ignite_versions(str(DEV_BRANCH))
    def test_node_stops_when_cdc_fills_wal_archive_volume(self, ignite_version):
        """
        CDC with no consumer and no cdcWalDirectoryMaxSize keeps every archived segment.
        """
        ignite = self.__ignite(ignite_version)
        archive = os.path.join(ignite.persistent_root, "wal_archive")
        data_storage = ignite.config.data_storage

        # CDC hard-links archived segments, so its directory must be on the archive's volume.
        ignite.config = ignite.config._replace(data_storage=data_storage._replace(
            cdc_wal_path=os.path.join(archive, "cdc"), default=data_storage.default._replace(cdc_enabled=True)))
        ignite.mount_tmpfs(archive, 256)

        ignite.start()
        ControlUtility(ignite).activate()

        load = self.__start_load_client(ignite)

        check_stops(ignite, timeout_sec=120)
        check_failure(ignite, *FAILURES["wal_archive"])

        load.stop()

    def __ignite(self, ignite_version):
        """
        :return: Persistent server node that writes its stdout and stderr into console.log itself, with the
                 partitions, the WAL and its archive in directories of their own.
        """
        ignite = IgniteService(self.test_context, IgniteConfiguration(
            version=IgniteVersion(ignite_version),
            client_connector_configuration=ClientConnectorConfiguration(),
            metrics_log_frequency=1000,
            data_storage=DataStorageConfiguration(
                checkpoint_frequency=1000,
                wal_segment_size=4 * MB,
                max_wal_archive_size=32 * MB,
                default=DataRegionConfiguration(persistence_enabled=True, max_size=256 * MB))), num_nodes=1)

        ignite.spec = ignite.spec.rebuild_as(IgniteNodeSpecFileOutput)

        root = ignite.persistent_root

        ignite.config = ignite.config._replace(data_storage=ignite.config.data_storage._replace(
            storage_path=os.path.join(root, "partitions"),
            wal_path=os.path.join(root, "wal"),
            wal_archive_path=os.path.join(root, "wal_archive")))

        return ignite

    def __start_load_client(self, ignite):
        """
        Starts a load that records its puts and errors once a second.
        :return: Load application service.
        """
        address = f"{ignite.nodes[0].account.hostname}:{ignite.config.client_connector_configuration.port}"

        load = IgniteApplicationService(
            self.test_context,
            IgniteThinClientConfiguration(addresses=[address], version=ignite.config.version),
            java_class_name=self.LOAD_APP,
            params={"cacheName": "disk-full"})

        load.start()

        return load

    def __start_flood_service(self, ignite):
        """
        Starts the application that deploys the System.out flood service on the server.
        :return: Flood application service.
        """
        flood = IgniteApplicationService(
            self.test_context,
            IgniteConfiguration(version=ignite.config.version, client_mode=True,
                                discovery_spi=from_ignite_cluster(ignite)),
            java_class_name=self.FLOOD_APP)

        flood.start()

        return flood


def log_files(ignite):
    """
    :return: The node's log4j2 log, console.log and gc.log, all on the log volume.
    """
    return [ignite.nodes[0].log_file] + [os.path.join(ignite.log_dir, log) for log in ("console.log", "gc.log")]


def fill_volume(ignite, path):
    """
    Writes a file into the path until its volume refuses the write.
    """
    out = ignite.exec_command(ignite.nodes[0], f"dd if=/dev/zero of={os.path.join(path, FILLER)} bs=1M 2>&1")

    assert "No space left on device" in out, f"{path} did not fill up: {out}"


def free_volume(ignite, path):
    """
    Removes the file fill_volume wrote.
    """
    ignite.nodes[0].account.ssh(f"rm -f {os.path.join(path, FILLER)}", allow_fail=False)


def enlarge_volume(ignite, path, size_mb):
    """
    Grows the tmpfs mounted over the path, keeping its files and what they hold, so writes there go on.
    """
    ignite.nodes[0].account.ssh(f"sudo mount -o remount,size={size_mb}m {path}", allow_fail=False)


def free_space(ignite, path):
    """
    :return: Bytes free on the volume of the path.
    """
    return int(ignite.exec_command(ignite.nodes[0], f"df --output=avail -B1 {path} | tail -1"))


class LogVolumes:
    """
    Describes, for a demo breakpoint banner (see :meth:`ignitetest.utils.ignite_test.IgniteTest.pause`), the node's
    volumes, whether its logs and its clients moved over a few seconds, and the last lines of the logs.
    """

    # How long the two snapshots are apart: the node logs its metrics once a second.
    GAP_SEC = 5

    # A pause in ignite.log at least this long is a gap the full volume left.
    SILENCE_SEC = 10

    # The flood service's lines are 1 KB of 'x': the banner shows the head of each line.
    LINE_WIDTH = 100

    # Prints the last timestamp of ignite.log and the logs a restart rotated out of it, then the longest pause between
    # two lines: seconds, from, to. A line the full volume cut inside its timestamp is skipped.
    TIMELINE = ("awk '/^\\[20/ && substr($0, 21, 1) == \",\" { t = substr($0, 13, 8); split(t, h, \":\"); "
                "s = h[1] * 3600 + h[2] * 60 + h[3]; "
                "if (n++ && s - p > gap) { gap = s - p; from = last; to = t } p = s; last = t } "
                "END { print last, gap + 0, from, to }'")

    def __init__(self, ignite, load, flood=None):
        self.ignite = ignite
        self.load = load
        self.flood = flood

    def describe(self):
        """
        :return: Section lines, the first one being the section title.
        """
        node = self.ignite.nodes[0]
        logs = [log_file(self.ignite), os.path.join(self.ignite.log_dir, "console.log")]

        before = self.__snapshot(node, logs)

        time.sleep(self.GAP_SEC)

        after = self.__snapshot(node, logs)

        last, gap = self.__timeline(node, logs[0])

        # A full tmpfs still takes the rest of a file's last page, so a log can grow a little on it.
        full = free_space(self.ignite, self.ignite.log_dir) == 0

        lines = ["VOLUMES", *self.__volumes(node), f"LAST {self.GAP_SEC} S"]

        for i, log in enumerate(logs):
            grown = after["sizes"][i] - before["sizes"][i]
            state = "no space" if full else "growing" if grown else "stopped"

            lines.append(f"  {os.path.basename(log):<12}{size_text(after['sizes'][i]):>10}  "
                         f"{'+' + size_text(grown):<10}{state:<10}{last if i == 0 else ''}".rstrip())

            if i == 0 and gap:
                lines.append(f"{'':<46}{gap}")

        lines.extend(self.__clients(before, after))

        for log in logs:
            lines.append(f"LAST LINES {os.path.basename(log)}")
            lines.extend(f"  {line}" for line in self.ignite.exec_command(
                node, f"tail -n 3 {log} | cut -c -{self.LINE_WIDTH}").splitlines())

        lines.append("SHOW")
        lines.extend(self.__show(node, logs))

        return lines

    def __snapshot(self, node, logs):
        """
        :return: The sizes of the logs and what the clients recorded last.
        """
        return {
            "sizes": [int(size) for size in self.ignite.exec_command(node, f"stat -c %s {' '.join(logs)}").split()],
            "progress": last_record(self.load, "progress"),
            "lines": last_record(self.flood, "lines") if self.flood else None
        }

    def __volumes(self, node):
        """
        :return: A df row for the log volume and one for the volume of the node's data.
        """
        paths = [self.ignite.log_dir, self.ignite.persistent_root]

        rows = self.ignite.exec_command(
            node, f"df -h --output=source,size,used,avail,pcent {' '.join(paths)} | tail -2").splitlines()

        lines = [volume_row("", "", ["fs", "size", "used", "avail", "use%"])]

        lines.extend(volume_row(name, path, row.split()) for name, path, row in zip(["logs", "data"], paths, rows))

        return lines

    def __timeline(self, node, log):
        """
        :return: When ignite.log got its last line, and the gap a full volume left in it, if any.
        """
        now, timeline = self.ignite.exec_command(node, f"date +%T; {self.TIMELINE} $(ls -tr {log}*)").splitlines()
        last, silence, start, end = (timeline.split() + [""] * 4)[:4]

        if not last:
            return "", ""

        gap = f"gap {start} -> {end}, {duration(int(silence))} without a line" \
            if int(silence) >= self.SILENCE_SEC else ""

        return f"last line {last}, {duration(seconds(now) - seconds(last))} ago", gap

    def __clients(self, before, after):
        """
        :return: The puts and failed puts of the load, and the lines of the flood service when it runs.
        """
        if not after["progress"]:
            return [f"  {'load':<12}no progress recorded yet"]

        puts, errors = after["progress"]
        puts_before = before["progress"][0] if before["progress"] else 0

        lines = [f"  {'load puts':<12}{puts:>10,}  {'+' + format(puts - puts_before, ','):<10}errors {errors}"]

        if after["lines"]:
            printed = after["lines"][0]
            printed_before = before["lines"][0] if before["lines"] else 0

            lines.append(f"  {'flood lines':<12}{printed:>10,}  +{printed - printed_before:,}")

        return lines

    def __show(self, node, logs):
        """
        :return: Ready-to-paste commands for the audience, each with a one-line label of what it shows.
        """
        host = node.account.hostname
        load_node = self.load.nodes[0]
        control_cmd = f"{self.ignite.script('control.sh')} --host {node.account.externally_routable_ip} --baseline"

        lines = [
            "  # cluster state and baseline:",
            f"  docker exec {host} bash -c \"{control_cmd}\"",
            "  # log sizes every 2 s (Ctrl-C stops it):",
            f"  watch -n 2 \"docker exec {host} stat -c '%s %n' {' '.join(logs)}\"",
            "  # the log files (a restart starts ignite.log anew, next to ignite.log.1):",
            f"  docker exec {host} ls -l {self.ignite.log_dir}",
            "  # last lines of each log:",
            f"  docker exec {host} tail -n 5 {logs[0]}",
            f"  docker exec {host} tail -n 5 {logs[1]} | cut -c -{self.LINE_WIDTH}",
            "  # the load's puts and errors, once a second:",
            f"  docker exec {load_node.account.hostname} grep 'progress->' {log_file(self.load)} | tail -n 5",
        ]

        if self.flood:
            flood_node = self.flood.nodes[0]

            lines.append("  # the lines the flood service wrote, once a second:")
            lines.append(f"  docker exec {flood_node.account.hostname} grep 'lines->' {log_file(self.flood)} "
                         f"| tail -n 5")

        return lines


def log_file(service):
    """
    :return: The service's log4j2 log. node.log_file is a glob, which a command pasted on the host would not expand.
    """
    return os.path.join(service.log_dir, "ignite.log")


def volume_row(name, path, fields):
    """
    :return: A VOLUMES line: the volume's name and path, then df's source, size, used, avail and use%.
    """
    source, *numbers = fields

    return f"  {name:<6}{path:<20}{source:<18}" + "".join(f"{number:>7}" for number in numbers)


def last_record(app, name):
    """
    :return: The numbers the application recorded last under the name, None before its first record.
    """
    records = app.extract_results(name)

    return [int(value) for value in records[-1].split()] if records else None


def size_text(size):
    """
    :return: The size in B, KB or MB.
    """
    if size < 1024:
        return f"{size} B"

    if size < MB:
        return f"{size / 1024:.1f} KB"

    return f"{size / MB:.1f} MB"


def seconds(clock):
    """
    :return: Seconds since midnight of an HH:MM:SS time.
    """
    hours, minutes, secs = clock.split(":")

    return int(hours) * 3600 + int(minutes) * 60 + int(secs)


def duration(secs):
    """
    :return: The seconds as "N s" or "N min N s", a day wrapped around midnight.
    """
    secs %= 24 * 3600

    return f"{secs} s" if secs < 60 else f"{secs // 60} min {secs % 60} s"


def printed_lines(flood):
    """
    :return: Lines the service wrote to System.out, as the flood application recorded last.
    """
    return int(flood.extract_results("lines")[-1])


def file_size(ignite, path):
    """
    :return: Bytes written into the file on the node.
    """
    return int(ignite.exec_command(ignite.nodes[0], f"stat -c %s {path}"))


def check_write_failed(ignite, path):
    """
    Checks that the file ends on a page boundary: a write the full tmpfs refused filled its last page.
    """
    assert file_size(ignite, path) % PAGE == 0, f"No write into {path} hit the full volume"


def check_grows(ignite, path):
    """
    Checks that the file grows within 15 s.
    """
    size = file_size(ignite, path)

    wait_until(lambda: file_size(ignite, path) > size, timeout_sec=15, err_msg=f"{path} did not grow")


def check_serves_load_for(ignite, load, seconds):
    """
    Waits the given seconds and checks that the node is alive and the puts went on without errors.
    """
    puts, errors = progress(load)

    time.sleep(seconds)

    assert ignite.alive(ignite.nodes[0]), "The node stopped"

    puts_after, errors_after = progress(load)

    assert puts_after > puts, f"No puts in {seconds} s"
    assert errors_after == errors, f"{errors_after - errors} puts failed in {seconds} s"


def check_stops(ignite, timeout_sec=60):
    """
    Checks that the node's process ends.
    """
    wait_until(lambda: not ignite.alive(ignite.nodes[0]), timeout_sec=timeout_sec,
               err_msg=f"The node is still up {timeout_sec} s after its volume filled up")


def check_failure(ignite, failure_type, cause):
    """
    Checks that the node logged a FailureContext of the type, caused by the writer and "No space left on device".
    """
    node = ignite.nodes[0]

    failure = ignite.exec_command(node, f"grep -h -m1 -F -A60 'FailureContext [type={failure_type}' "
                                        f"{node.log_file} || true")

    assert failure, f"No {failure_type} failure in the node's log"
    assert cause in failure, f"The {failure_type} failure is not caused by '{cause}': {failure[:2000]}"
    assert "No space left on device" in failure, f"The {failure_type} failure is not ENOSPC: {failure[:2000]}"


def progress(load):
    """
    :return: Puts and failed puts the load application recorded last.
    """
    puts, errors = load.extract_results("progress")[-1].split()

    return int(puts), int(errors)
