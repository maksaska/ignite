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
Dynamic main data center reassignment: ``control.sh --set-main-dc --new-main-dc <DC_ID>``.

Whatever mode MdcTopologyValidator runs in, a data center outage can leave a segment that is
perfectly healthy on its own and still read-only - it has lost the main DC (an even DC count)
or a majority of the DC set (an odd one). Nothing about the topology will bring the writes
back: the segment is the last DC standing. The reassignment command is the manual override
for exactly that situation - it is told from outside the cluster that the DCs the segment
cannot see are really gone, and that the surviving DC is the main one from now on.

The override is deliberately not durable, which is the second half of what is tested here.
It holds only while the segment stays alone: a server node joining from another DC is proof
that the other DC has someone alive, so the dynamic assignment is dropped and the validator
is back to its configured verdict. A repeated outage therefore lands read-only again until
the command is run again - an operator's decision about one outage is never silently
inherited by the next.

Both layouts are covered, since the outage that leaves a read-only survivor differs:

  - two DCs, validated against ``mainDc=DC1``: the main DC goes down, DC2 survives;
  - three DCs, validated by majority: two DCs go down, DC3 survives seeing 1 of 3.

No network impairments are involved anywhere - the DCs are stopped, not partitioned - so a
rejected write can only ever be the validator's verdict.
"""
from itertools import count
from typing import List, Sequence, Tuple

from ignitetest.services.mdc.mdc_cluster import MdcCluster, cross_dc_network, DCS_2, DCS_3
from ignitetest.utils import cluster, ignite_versions
from ignitetest.utils.ignite_test import IgniteTest
from ignitetest.utils.version import DEV_BRANCH

CACHE_NAME = "mdc-dynamic-main-dc"

SRV_PER_DC = 2

# Keys generated from each DC; the whole pre-outage data set is [0, KEYS_PER_DC * dcs).
KEYS_PER_DC = 100

# Write bursts that are read back afterwards, so that an accepted write is verified to have
# landed rather than just to have not been rejected. One range per burst, all of them above
# the generated data set and below the probe ranges.
BURST_BASE = 100_000
BURST_STRIDE = 10_000
BURST_KEYS = 50

# Probe key ranges of the put admissibility checks, one per check, so that the writes an
# admissible check performs never collide with another check's range.
PROBE_BASE = 1_000_000
PROBE_STRIDE = 1_000_000


def _probe_offset(idx: int) -> int:
    return PROBE_BASE + idx * PROBE_STRIDE


def _burst_range(idx: int) -> Tuple[int, int]:
    start = BURST_BASE + idx * BURST_STRIDE

    return start, start + BURST_KEYS


class MdcDynamicMainDcTest(IgniteTest):
    """
    Tests for the dynamic main data center reassignment command.
    """
    @cluster(num_nodes=6)
    @ignite_versions(str(DEV_BRANCH))
    def test_set_main_dc_restores_writes_in_two_dc(self, ignite_version):
        """
        Two DCs, the main one goes down. The survivor keeps serving reads but rejects every
        write, and the reassignment command makes it writable again.
        """
        self._check_reassignment_restores_writes(ignite_version, DCS_2)

    @cluster(num_nodes=9)
    @ignite_versions(str(DEV_BRANCH))
    def test_set_main_dc_restores_writes_in_three_dc(self, ignite_version):
        """
        Three DCs, two of them go down. The survivor holds a minority of the DC set - one out
        of three - so the majority based validator rejects every write until the reassignment
        command hands it the main DC role.
        """
        self._check_reassignment_restores_writes(ignite_version, DCS_3)

    @cluster(num_nodes=6)
    @ignite_versions(str(DEV_BRANCH))
    def test_dynamic_main_dc_reset_by_rejoin_in_two_dc(self, ignite_version):
        """
        Two DCs: the reassignment survives neither the return of the main DC nor its second
        loss - the survivor is read-only again until the command is repeated.
        """
        self._check_reassignment_reset_by_rejoin(ignite_version, DCS_2)

    @cluster(num_nodes=9)
    @ignite_versions(str(DEV_BRANCH))
    def test_dynamic_main_dc_reset_by_rejoin_in_three_dc(self, ignite_version):
        """
        Three DCs: the reassignment survives neither the return of the two lost DCs nor their
        second loss - the survivor is read-only again until the command is repeated.
        """
        self._check_reassignment_reset_by_rejoin(ignite_version, DCS_3)

    def _check_reassignment_restores_writes(self, ignite_version, dcs: Sequence[str]):
        """
        Scenario 1: everything but the last DC goes down - which takes the main DC itself in
        the two DC layout, and leaves a minority behind in the three DC one. The survivor is
        verified read-only first, so that the writes accepted afterwards can only be the work
        of the reassignment; the command is then run from the survivor naming the survivor,
        the only DC a lone segment can hand write access to.
        """
        mdc, survivor, lost = self._prepare_cluster(ignite_version, dcs)

        probes, bursts = count(), count()

        with cross_dc_network(self.logger, mdc) as net:
            self._start_cluster(mdc)

            self.pause("cluster-up", mdc, net)

            total_keys = self._generate_data(mdc)

            mdc.verify_cache_distribution(CACHE_NAME, copies_per_dc=1)

            # The survivor writes while the cluster is whole, so the rejection below is the
            # outage talking rather than something about this DC.
            self._probe_writes(mdc, survivor, True, probes)

            self._take_dcs_down(mdc, survivor, lost, total_keys, probes)

            self.pause("survivor-read-only", mdc, net)

            mdc.set_main_dc(survivor)

            self.pause("main-dc-reassigned", mdc, net)

            # The point of the scenario: write load is processed again.
            self._probe_writes(mdc, survivor, True, probes)

            self._write_and_read_back(mdc, survivor, "afterReassignment", bursts)

            mdc.verify_servers_log_clean()

            mdc.stop_servers()

    def _check_reassignment_reset_by_rejoin(self, ignite_version, dcs: Sequence[str]):
        """
        Scenario 2: the same outage and reassignment, then the topology is restored, write
        load is run across the whole cluster, and the very same DCs go down again.

        At that point the survivor must be read-only, before the command is run a second
        time: the dynamic assignment was dropped the moment a server from another DC joined,
        because that join is proof the other DC is not gone. A survivor still writing here
        would be writing on an operator's say-so about an outage that has since ended.
        """
        mdc, survivor, lost = self._prepare_cluster(ignite_version, dcs)

        probes, bursts = count(), count()

        with cross_dc_network(self.logger, mdc) as net:
            self._start_cluster(mdc)

            total_keys = self._generate_data(mdc)

            mdc.verify_cache_distribution(CACHE_NAME, copies_per_dc=1)

            self._take_dcs_down(mdc, survivor, lost, total_keys, probes)

            mdc.set_main_dc(survivor)

            self._probe_writes(mdc, survivor, True, probes)

            # Topology restored: the servers of every lost DC join back, which is what drops
            # the dynamic assignment. Writes keep working, now on the configured verdict -
            # the main DC is back (two DCs), the DC set is a majority again (three DCs).
            mdc.start_dcs(*lost)

            mdc.verify_whole_cluster_healthy()

            mdc.verify_cache_distribution(CACHE_NAME, copies_per_dc=1)

            self.pause("topology-restored", mdc, net)

            for dc in mdc.dcs:
                self._probe_writes(mdc, dc, True, probes)

            self._write_and_read_back(mdc, survivor, "wholeCluster", bursts)

            # ...and the same DCs go down again.
            mdc.stop_dcs(*lost)

            mdc.verify_segment_healthy(survivor)

            # The assertion this scenario exists for: no write is accepted before the command
            # is run again, even though writes once were accepted for this very topology.
            self._probe_writes(mdc, survivor, False, probes)

            self.pause("reassignment-reset", mdc, net)

            # Reads never depended on any of this.
            mdc.check_data(survivor, CACHE_NAME, 0, total_keys)

            # A repeated reassignment is accepted and restores the writes just as the first
            # one did, so the reset left nothing behind that blocks the override.
            mdc.set_main_dc(survivor)

            self._probe_writes(mdc, survivor, True, probes)

            self._write_and_read_back(mdc, survivor, "afterSecondReassignment", bursts)

            mdc.verify_servers_log_clean()

            mdc.stop_servers()

    def _prepare_cluster(self, ignite_version, dcs: Sequence[str]) -> Tuple[MdcCluster, str, List[str]]:
        """
        Builds the cluster and works out who survives the outage: the last DC, with every
        other one going down. In an even sized DC set that puts the configured main DC among
        the lost ones, which is what leaves the survivor read-only there.

        :return: The cluster, the surviving DC and the DCs that are to go down.
        """
        mdc = MdcCluster(self, ignite_version, dcs=dcs, srv_per_dc=SRV_PER_DC, runners_per_dc=1)

        survivor = mdc.dcs[-1]

        return mdc, survivor, [dc for dc in mdc.dcs if dc != survivor]

    @staticmethod
    def _start_cluster(mdc: MdcCluster):
        """
        Starts the servers of every DC and points discovery at all of them.

        The latter is needed because the DCs going down include the first started one - the
        DC the shared ip finder was seeded from - so it has to rejoin through the surviving
        DC instead of seeding a second cluster off itself.
        """
        mdc.start_servers()

        mdc.sync_service_discovery()

    def _take_dcs_down(self, mdc: MdcCluster, survivor: str, lost: Sequence[str], total_keys: int, probes):
        """
        Stops every lost DC and verifies the state the reassignment command exists for: a
        segment that is healthy and fully readable - one copy of every partition lives in
        every DC - and still rejects every write.
        """
        mdc.stop_dcs(*lost)

        mdc.verify_segment_healthy(survivor)

        mdc.check_data(survivor, CACHE_NAME, 0, total_keys)

        self._probe_writes(mdc, survivor, False, probes)

    @staticmethod
    def _probe_writes(mdc: MdcCluster, dc: str, admissible: bool, probes):
        """
        Checks whether put load from the given DC is accepted or rejected by the validator,
        on a probe key range of its own - the checks that do write must not overwrite each
        other's keys.
        """
        return mdc.check_put_admissibility(dc, CACHE_NAME, admissible, key_offset=_probe_offset(next(probes)))

    def _write_and_read_back(self, mdc: MdcCluster, dc: str, label: str, bursts):
        """
        Runs a put burst from the given DC and reads every written key back, so that an
        accepted write is verified to have actually landed in the cache.
        """
        key_from, key_to = _burst_range(next(bursts))

        svc = mdc.run_load(dc, "PUT", CACHE_NAME, label, keyFrom=key_from, keyTo=key_to, iterations=BURST_KEYS)

        ops = mdc.result_int(svc, f"{label}OpsCnt")
        errs = mdc.result_int(svc, f"{label}ErrCnt")

        self.logger.info(f"Write burst [dc={dc}, label={label}, keys=[{key_from}, {key_to}), "
                         f"ops={ops}, errs={errs}]")

        assert errs == 0, f"Write burst was expected to be accepted [dc={dc}, label={label}, errs={errs}]"

        assert ops == BURST_KEYS, \
            f"Write burst is incomplete [dc={dc}, label={label}, ops={ops}, expected={BURST_KEYS}]"

        mdc.check_data(dc, CACHE_NAME, key_from, key_to)

    @staticmethod
    def _generate_data(mdc: MdcCluster) -> int:
        """
        Populates the cache from every DC in turn, each with its own key range.

        :return: The size of the whole data set, i.e. the exclusive upper key bound.
        """
        for idx, dc in enumerate(mdc.dcs):
            mdc.generate_data(dc, CACHE_NAME, idx * KEYS_PER_DC, (idx + 1) * KEYS_PER_DC)

        return len(mdc.dcs) * KEYS_PER_DC
