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
DEMO: a two DC cluster through a network failure and back.

Two data centers form one stretched cluster with ``mainDc=DC1``. The cross-DC link is cut and
the cluster splits into two half-rings, each healthy on its own. The half that still sees the
main DC keeps taking writes; the other half serves every read and refuses every write. The
link comes back, the read-only half is restarted into the surviving ring, and the stretched
cluster is whole again - split-brain never heals on its own, the rejoin is an operator step.

The second scenario is the other decision an operator can make while the link is down: rather
than waiting for DC1, promote the read-only half to be the main DC and carry on writing there.

Breakpoints: `cluster-up`, `data-loaded`, `split-brain`, `main-dc-reassigned`, `rejoined`.
"""
from time import sleep

from ducktape.mark import parametrize

from ignitetest.services.mdc.mdc_cluster import MdcCluster, cross_dc_network, DC_1, DC_2
from ignitetest.utils import cluster, ignite_versions
from ignitetest.utils.ignite_test import IgniteTest
from ignitetest.utils.version import DEV_BRANCH

CACHE_NAME = "mdc-demo-recovery"

KEYS_PER_DC = 100

# Time for discovery to notice the cut link and for both half-rings to complete PME.
SPLIT_SETTLE_SECS = 20

PROBE_MAIN = 1_000_000
PROBE_READ_ONLY = 2_000_000
PROBE_PROMOTED = 3_000_000
PROBE_AFTER_DC_1 = 4_000_000
PROBE_AFTER_DC_2 = 5_000_000


class MdcRecoveryDemo(IgniteTest):
    """
    Demonstrates the life of a stretched two DC cluster around a network failure.
    """
    @cluster(num_nodes=6)
    @ignite_versions(str(DEV_BRANCH))
    @parametrize(cross_dc_latency_ms=100)
    def demo_split_brain_and_rejoin(self, ignite_version, cross_dc_latency_ms):
        """
        Cut the link, watch the cluster split into an active half and a read-only half, heal
        the link and restart the read-only half back into one stretched cluster.
        """
        mdc = MdcCluster(self, ignite_version, srv_per_dc=2, runners_per_dc=1,
                         network_timeout=20_000, tcp_connect_timeout=10_000)

        with cross_dc_network(self.logger, mdc, delay_ms=cross_dc_latency_ms) as net:
            mdc.start_servers()

            self.pause("cluster-up", mdc, net)

            total_keys = self._generate_data(mdc)

            mdc.verify_cache_distribution(CACHE_NAME, copies_per_dc=1)

            self.pause("data-loaded", mdc, net)

            net.enable_network_partition(DC_1, DC_2)

            sleep(SPLIT_SETTLE_SECS)

            # Two independent half-rings, each ACTIVE and complete on its own.
            mdc.verify_split_brain()

            # Every partition has a copy on both sides, so both halves read everything...
            for dc in mdc.dcs:
                mdc.check_data(dc, CACHE_NAME, 0, total_keys)

            # ...while only the half that sees the main DC may write.
            mdc.check_put_admissibility(DC_1, CACHE_NAME, True, key_offset=PROBE_MAIN)
            mdc.check_put_admissibility(DC_2, CACHE_NAME, False, key_offset=PROBE_READ_ONLY)

            self.pause("split-brain", mdc, net)

            net.disable_network_partition(DC_1, DC_2)

            # The rejoin is the operator's step: a split-brained half-ring does not merge back.
            mdc.restart(DC_2)

            mdc.verify_whole_cluster_healthy()

            mdc.check_put_admissibility(DC_1, CACHE_NAME, True, key_offset=PROBE_AFTER_DC_1)
            mdc.check_put_admissibility(DC_2, CACHE_NAME, True, key_offset=PROBE_AFTER_DC_2)

            mdc.verify_cache_distribution(CACHE_NAME, copies_per_dc=1)

            self.pause("rejoined", mdc, net)

            mdc.stop_servers()

    @cluster(num_nodes=6)
    @ignite_versions(str(DEV_BRANCH))
    @parametrize(cross_dc_latency_ms=100)
    def demo_promote_the_read_only_half_ring_to_main(self, ignite_version, cross_dc_latency_ms):
        """
        The same cut link, the other decision: DC1 is treated as lost, so the read-only half
        is promoted with ``control.sh --set-main-dc --new-main-dc DC2``, run inside DC2 and
        naming DC2 - the command is a deliberate double confirmation of where it is run.

        The promoted half starts taking writes immediately. Note what this means: the
        operator asserts that DC1 is gone, and the cluster takes their word for it - if DC1
        is in fact still up and writing on its own, both halves are now writable. That is why
        the promotion is a manual command and not something the cluster decides by itself.
        """
        mdc = MdcCluster(self, ignite_version, srv_per_dc=2, runners_per_dc=1,
                         network_timeout=20_000, tcp_connect_timeout=10_000)

        with cross_dc_network(self.logger, mdc, delay_ms=cross_dc_latency_ms) as net:
            mdc.start_servers()

            total_keys = self._generate_data(mdc)

            net.enable_network_partition(DC_1, DC_2)

            sleep(SPLIT_SETTLE_SECS)

            mdc.verify_split_brain()

            # The reserve half is read-only: it cannot see the main DC.
            mdc.check_data(DC_2, CACHE_NAME, 0, total_keys)
            mdc.check_put_admissibility(DC_2, CACHE_NAME, False, key_offset=PROBE_READ_ONLY)

            self.pause("split-brain", mdc, net)

            mdc.set_main_dc(DC_2)

            # The reserve half is now the main one and takes the write load.
            mdc.check_put_admissibility(DC_2, CACHE_NAME, True, key_offset=PROBE_PROMOTED)

            self.pause("main-dc-reassigned", mdc, net)

            mdc.stop_servers()

    @staticmethod
    def _generate_data(mdc: MdcCluster) -> int:
        """
        Populates the cache from every DC in turn, each with its own key range.

        :return: The size of the whole data set, i.e. the exclusive upper key bound.
        """
        for idx, dc in enumerate(mdc.dcs):
            mdc.generate_data(dc, CACHE_NAME, idx * KEYS_PER_DC, (idx + 1) * KEYS_PER_DC)

        return len(mdc.dcs) * KEYS_PER_DC
