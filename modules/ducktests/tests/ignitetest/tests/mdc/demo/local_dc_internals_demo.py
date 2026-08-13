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
DEMO: the cluster's own bulk data movement stays inside the data center.

It is not only user requests that avoid the cross-DC link. The internal operations that move
whole partitions around pick a source in the local DC whenever one is available:

  - rebalancing a node that lost its data;
  - restoring a full snapshot.

"Whenever available" is the whole condition, and it is worth being precise about. With one
copy of every partition per DC - the usual layout - a node coming back empty is the ONLY copy
its DC has, so its data can only come from another DC. A local source exists only when the DC
holds more than one copy, which is why the rebalance scenario runs with two copies per DC
(``backups=3`` over two DCs).

Breakpoints: `data-loaded`, `rebalanced`, `snapshot-restored`.
"""
from ignitetest.services.mdc.mdc_cluster import MdcCluster, cross_dc_network, DC_1
from ignitetest.tests.mdc.demo.util import show
from ignitetest.utils import cluster, ignite_versions
from ignitetest.utils.ignite_test import IgniteTest
from ignitetest.utils.version import DEV_BRANCH

CACHE_NAME = "mdc-demo-internals"

KEYS = 500

# Two DCs, four copies of every partition: two per DC. That second local copy is what a
# rebalancing node in this DC can be supplied from.
BACKUPS_TWO_COPIES_PER_DC = 3

SNAPSHOT_NAME = "mdcDemoSnapshot"


class MdcLocalDcInternalsDemo(IgniteTest):
    """
    Demonstrates DC locality of rebalancing and of a full snapshot restore.
    """
    @cluster(num_nodes=5)
    @ignite_versions(str(DEV_BRANCH))
    def demo_rebalance_pulls_from_the_local_dc(self, ignite_version):
        """
        One server node is wiped and restarted empty. It has to be filled from somewhere, and
        every partition it needs exists both in its own DC and in the other one - so the
        choice is real. Every partition it pulls comes from its own DC.
        """
        mdc = MdcCluster(self, ignite_version, srv_per_dc=2, runners_per_dc={DC_1: 1}, jmx_metrics=True)

        with cross_dc_network(self.logger, mdc):
            mdc.start_servers()

            mdc.generate_data(DC_1, CACHE_NAME, 0, KEYS, backups=BACKUPS_TWO_COPIES_PER_DC)

            mdc.verify_cache_distribution(CACHE_NAME, copies_per_dc=2)

            # Which node sits in which DC, read while every node is still up. The wiped node
            # comes back with a new id, but the suppliers keep theirs.
            node_dcs = mdc.node_dcs()

            self.pause("data-loaded", mdc)

            servers = mdc.servers[DC_1]
            victim = servers.nodes[0]

            servers.stop_node(victim)
            servers.clean_node(victim)
            servers.start_node(victim)

            servers.await_started(nodes=[victim])
            servers.await_rebalance()

            suppliers = mdc.rebalance_suppliers(DC_1, victim)

            supplier_dcs = {supplier: node_dcs.get(supplier, "unknown") for supplier in suppliers}

            self.logger.info(f"Rebalance suppliers of the wiped node [dc={DC_1}, "
                             f"node={victim.account.hostname}, suppliers={supplier_dcs}]")

            assert suppliers, \
                f"The wiped node reported no rebalance supplier at all [node={victim.account.hostname}]"

            assert set(supplier_dcs.values()) == {DC_1}, \
                f"The wiped node was supplied from outside its own DC [expected={DC_1}, " \
                f"suppliers={supplier_dcs}]"

            self.pause("rebalanced", mdc)

            mdc.stop_servers()

    @cluster(num_nodes=5)
    @ignite_versions(str(DEV_BRANCH))
    def demo_snapshot_restore_in_mdc(self, ignite_version):
        """
        A full snapshot of a cross-DC cache is taken, the cache is destroyed, and the snapshot
        is restored into the same stretched cluster. The restore reads each partition from the
        local DC where it has a copy there, so the restore traffic mostly stays inside a DC.

        The cluster is persistent here - a full snapshot needs it - which also means it comes
        up INACTIVE and is activated by :meth:`MdcCluster.start_servers`.
        """
        mdc = MdcCluster(self, ignite_version, srv_per_dc=2, runners_per_dc={DC_1: 1},
                         persistent=True, jmx_metrics=True)

        with cross_dc_network(self.logger, mdc):
            mdc.start_servers()

            mdc.generate_data(DC_1, CACHE_NAME, 0, KEYS)

            mdc.verify_cache_distribution(CACHE_NAME, copies_per_dc=1)

            control = mdc.control()

            control.snapshot_create(SNAPSHOT_NAME)

            self.pause("data-loaded", mdc)

            # The cache has to be gone before a restore will take it.
            control.destroy_caches(CACHE_NAME)

            control.snapshot_restore(SNAPSHOT_NAME)

            # Everything is back, and back in the cross-DC layout it was snapshotted in.
            mdc.check_data(DC_1, CACHE_NAME, 0, KEYS)

            mdc.verify_cache_distribution(CACHE_NAME, copies_per_dc=1)

            self.pause("snapshot-restored", mdc,
                       show(self, "PARTITION DISTRIBUTION", control.distribution(caches=CACHE_NAME)))

            mdc.stop_servers()
