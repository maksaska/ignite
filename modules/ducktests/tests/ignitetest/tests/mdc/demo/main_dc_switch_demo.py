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
DEMO: the three DC scheme - majority by default, a promoted DC when there is no majority.

With three data centers no DC is marked as the main one. A segment may write while it sees a
majority of the DCs, so losing any single DC changes nothing: the two that still see each
other carry on, and the one that got cut off goes read-only on the assumption that the larger
part of the cluster is still serving.

That assumption is all the cluster has, and when it fails - every DC isolated, or two DCs
actually down - no segment holds a majority and the whole cluster stops taking writes. Then
an operator decides: ``control.sh --set-main-dc --new-main-dc <DC>``, run in the DC to be
promoted and naming that same DC, makes it the main one and writes resume there. While the
mark is set the DC count and the majority arithmetic are not consulted at all.

The mark is dropped as soon as a server node from any other DC appears in the topology -
proof that the other DC is back, and therefore that the operator's premise no longer holds.
The cluster returns to majority based validation on its own.

Breakpoints: `minority-read-only`, `no-majority`, `dc-promoted`, `network-restored`.
"""
from time import sleep

from ducktape.mark import parametrize

from ignitetest.services.mdc.mdc_cluster import MdcCluster, cross_dc_network, all_pairs, isolation_pairs, \
    DCS_3, DC_1, DC_2, DC_3
from ignitetest.tests.mdc.demo.util import show
from ignitetest.utils import cluster, ignite_versions
from ignitetest.utils.ignite_test import IgniteTest
from ignitetest.utils.version import DEV_BRANCH

CACHE_NAME = "mdc-demo-main-dc-switch"

KEYS = 150

# Time for discovery to notice the cut links and for every segment to complete PME.
SPLIT_SETTLE_SECS = 20

PROBE_MAJORITY = 1_000_000
PROBE_MINORITY = 2_000_000
PROBE_NO_MAJORITY = 3_000_000
PROBE_PROMOTED = 4_000_000
PROBE_RESTORED = 5_000_000


class MdcMainDcSwitchDemo(IgniteTest):
    """
    Demonstrates majority based validation and the manual promotion that overrides it.
    """
    @cluster(num_nodes=8)
    @ignite_versions(str(DEV_BRANCH))
    @parametrize(cross_dc_latency_ms=100)
    def demo_minority_dc_goes_read_only_by_default(self, ignite_version, cross_dc_latency_ms):
        """
        DC3 is cut off from DC1 and DC2. The two that still see each other are a majority and
        keep writing; DC3 sees one DC out of three and goes read-only - while still serving
        every read, since it owns a copy of every partition. No main DC mark is involved.
        """
        mdc = MdcCluster(self, ignite_version, dcs=DCS_3, srv_per_dc=2, runners_per_dc={DC_1: 1, DC_3: 1},
                         network_timeout=20_000, tcp_connect_timeout=10_000)

        cut = isolation_pairs(DC_3, mdc.dcs)

        with cross_dc_network(self.logger, mdc, delay_ms=cross_dc_latency_ms) as net:
            mdc.start_servers()

            mdc.generate_data(DC_1, CACHE_NAME, 0, KEYS)

            mdc.verify_cache_distribution(CACHE_NAME, copies_per_dc=1)

            # Both links go in one round-trip: cutting them one after the other would show the
            # cluster an intermediate topology it would legitimately react to.
            net.enable_network_partitions(*cut)

            sleep(SPLIT_SETTLE_SECS)

            mdc.verify_segments((DC_1, DC_2), DC_3)

            mdc.check_put_admissibility(DC_1, CACHE_NAME, True, key_offset=PROBE_MAJORITY)

            mdc.check_data(DC_3, CACHE_NAME, 0, KEYS)
            mdc.check_put_admissibility(DC_3, CACHE_NAME, False, key_offset=PROBE_MINORITY)

            self.pause("minority-read-only", mdc, net)

            mdc.stop_servers()

    @cluster(num_nodes=7)
    @ignite_versions(str(DEV_BRANCH))
    @parametrize(cross_dc_latency_ms=100)
    def demo_promote_a_dc_when_no_majority_is_left(self, ignite_version, cross_dc_latency_ms):
        """
        Every cross-DC link drops, so all three DCs end up alone and none of them holds a
        majority - the whole cluster is read-only. DC1 is promoted by hand and writes resume
        there. Once the links are back and the other DCs rejoin, the mark is dropped and the
        cluster is back to majority based validation, with writes working everywhere.
        """
        mdc = MdcCluster(self, ignite_version, dcs=DCS_3, srv_per_dc=2, runners_per_dc={DC_1: 1},
                         network_timeout=20_000, tcp_connect_timeout=10_000)

        mesh = all_pairs(mdc.dcs)

        with cross_dc_network(self.logger, mdc, delay_ms=cross_dc_latency_ms) as net:
            mdc.start_servers()

            mdc.generate_data(DC_1, CACHE_NAME, 0, KEYS)

            net.enable_network_partitions(*mesh)

            sleep(SPLIT_SETTLE_SECS)

            # Three single-DC segments, not one of them a majority.
            mdc.verify_split_brain()

            mdc.check_data(DC_1, CACHE_NAME, 0, KEYS)
            mdc.check_put_admissibility(DC_1, CACHE_NAME, False, key_offset=PROBE_NO_MAJORITY)

            self.pause("no-majority", mdc, net)

            mdc.set_main_dc(DC_1)

            mdc.check_put_admissibility(DC_1, CACHE_NAME, True, key_offset=PROBE_PROMOTED)

            self.pause("dc-promoted", mdc,
                       show(self, "MDC TOPOLOGY", mdc.control(DC_1).data_center_topology()))

            net.disable_network_partitions(*mesh)

            # DC1 keeps the ring it was promoted in; the other segments rejoin it by restart,
            # and the first of their server nodes to appear drops the main DC mark.
            for dc in (DC_2, DC_3):
                mdc.restart(dc)

            mdc.verify_whole_cluster_healthy()

            # Writes work again on the default verdict: all three DCs are visible.
            mdc.check_put_admissibility(DC_1, CACHE_NAME, True, key_offset=PROBE_RESTORED)

            self.pause("network-restored", mdc, net,
                       show(self, "MDC TOPOLOGY", mdc.control(DC_1).data_center_topology()))

            mdc.stop_servers()
