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
DEMO: cells, and why an MDC cluster has to stretch them across its data centers.

ClusterNodeAttributeColocatedBackupFilter does the opposite of every other filter here: it
keeps all the copies of a partition TOGETHER, on nodes sharing one value of a node attribute.
That value is the cell. Every partition lives entirely inside one cell, and different
partitions live in different cells.

The point of it is the arithmetic of a mass failure. Without colocation, losing any
``backups + 1`` nodes at once most likely loses data, because those nodes are owners of some
common partition. With colocation, data is lost only if the nodes that went are all from one
cell - so a cell is the unit whose loss you plan for, and losing pieces of several cells
costs nothing.

That is also the trap in a multi data center cluster. The filter compares one attribute and
knows nothing about data centers: if a cell is built inside a single DC, then every partition
of that cell has ALL of its copies in that DC, and the DC's outage takes them with it. The
cell has to be stretched - to contain nodes from every DC - and then colocation and the
cross-DC guarantee hold at the same time. These two scenarios are that layout and its
opposite, on the same cluster size and the same filter.

Note the cluster's own verdict on this, which is worth showing on both scenarios:
``IsCacheAffinityConfigurationMdcSafe`` accepts a colocated cache unconditionally - the
CONFIGURATION really can be safe - so a cell confined to one DC is caught only by
``IsCachePartitionDistributionSafe``, which looks at where the copies actually went.

Breakpoints: `data-loaded`, `cells`, `distribution`, `dc-lost`.
"""
from ignitetest.services.mdc.mdc_cluster import MdcCluster, cross_dc_network, DC_1, DC_2
from ignitetest.tests.mdc.demo.util import partitions_missing_a_dc, partitions_without_owners, show
from ignitetest.utils import cluster, ignite_versions
from ignitetest.utils.ignite_test import IgniteTest
from ignitetest.utils.version import DEV_BRANCH

CACHE_NAME = "mdc-demo-cells"

KEYS = 200

# Affinity partitions, as configured on the cache: needed to tell a partition that lost every
# owner from one that was never printed.
PARTITIONS = 512

CELL_ATTR = "CELL"

CELLS = ("CELL1", "CELL2")


class MdcCellFilterDemo(IgniteTest):
    """
    Demonstrates ClusterNodeAttributeColocatedBackupFilter, and the difference between a cell
    stretched over the data centers and one confined to a single data center.
    """
    @cluster(num_nodes=5)
    @ignite_versions(str(DEV_BRANCH))
    def demo_cells_stretched_across_dcs_keep_a_copy_in_every_dc(self, ignite_version):
        """
        Two cells, each with a node in every data center. Both copies of a partition stay
        inside one cell, and because that cell straddles the DCs, one of them is in DC1 and
        the other in DC2 - colocation and the cross-DC guarantee at the same time.

        Then DC2 is switched off entirely, and every partition is still readable from DC1:
        no cell lost more than one of its two nodes.
        """
        mdc = MdcCluster(self, ignite_version, srv_per_dc=2, srv_groups=(CELL_ATTR, CELLS),
                         runners_per_dc={DC_1: 1}, jmx_metrics=True)

        with cross_dc_network(self.logger, mdc):
            mdc.start_servers()

            mdc.generate_data(DC_1, CACHE_NAME, 0, KEYS, backups=mdc.min_backups,
                              partitions=PARTITIONS, backupFilterKind="COLOCATED",
                              colocationAttr=CELL_ATTR)

            self.pause("data-loaded", mdc)

            # Every cell has a node in every DC - this is what "stretched" means, and it is a
            # property of the deployment, not of the filter.
            spread = mdc.group_dcs()

            self.logger.info(f"Cell layout over the data centers [{spread}]")

            for cell, dcs in spread.items():
                assert set(dcs) == set(mdc.dcs), \
                    f"Cell {cell} is not stretched across every DC [present in={sorted(dcs)}, " \
                    f"dcs={list(mdc.dcs)}]"

            # All copies of a partition in one cell...
            _, cells = mdc.verify_cache_colocation(CACHE_NAME)

            # ...and, because the cells are stretched, still one copy in every DC.
            mdc.verify_cache_distribution(CACHE_NAME, copies_per_dc=1)

            self.logger.info(f"Colocated and cross-DC at once [cache={CACHE_NAME}, "
                             f"cells={sorted(set(next(iter(cells.values())).values()))}]")

            self.pause("cells", mdc,
                       show(self, "PARTITION DISTRIBUTION", mdc.control().distribution(caches=CACHE_NAME)))

            # The whole point, exercised: a data center goes away and every cell survives it.
            mdc.stop_dcs(DC_2)

            mdc.check_data(DC_1, CACHE_NAME, 0, KEYS)

            self.pause("dc-lost", mdc)

            mdc.stop_servers()

    @cluster(num_nodes=5)
    @ignite_versions(str(DEV_BRANCH))
    def demo_a_cell_confined_to_one_dc_loses_its_partitions(self, ignite_version):
        """
        The same filter and the same cluster size, with the cells drawn along the data center
        boundary instead of across it: CELL1 is DC1, CELL2 is DC2. Colocation is honoured to
        the letter - and now it means every partition has both of its copies in one DC.

        The configuration metric still reports the cache as MDC safe, because a colocated
        cache can be; only the distribution metric sees what actually happened. Switching DC2
        off then takes every partition of CELL2 with it.
        """
        mdc = MdcCluster(self, ignite_version, srv_per_dc=2,
                         srv_groups=(CELL_ATTR, {DC_1: (CELLS[0],), DC_2: (CELLS[1],)}),
                         runners_per_dc={DC_1: 1}, jmx_metrics=True)

        with cross_dc_network(self.logger, mdc):
            mdc.start_servers()

            mdc.generate_data(DC_1, CACHE_NAME, 0, KEYS, backups=mdc.min_backups,
                              partitions=PARTITIONS, backupFilterKind="COLOCATED",
                              colocationAttr=CELL_ATTR)

            spread = mdc.group_dcs()

            self.logger.info(f"Cell layout over the data centers [{spread}]")

            # Colocation itself is not broken - it is exactly what puts both copies in one DC.
            distribution, _ = mdc.verify_cache_colocation(CACHE_NAME)

            missing = partitions_missing_a_dc(distribution, mdc.dcs)

            self.logger.info(f"Partitions without a copy in every DC [count={len(missing)}, "
                             f"examples={missing[:5]}]")

            assert missing, \
                "A cell confined to one DC was expected to leave partitions without a copy " \
                "in the other DC. Check that the cells are really drawn along the DC boundary."

            # The two metrics disagree, and both are right about their own question.
            mdc.verify_cache_mdc_metrics(CACHE_NAME, affinity_safe=True, distribution_safe=False)

            self.pause("distribution", mdc,
                       show(self, "PARTITION DISTRIBUTION", mdc.control().distribution(caches=CACHE_NAME)))

            mdc.stop_dcs(DC_2)

            lost = partitions_without_owners(mdc.group_distribution(CACHE_NAME, dc=DC_1), PARTITIONS)

            self.logger.info(f"Partitions with no owner left after the DC2 outage "
                             f"[count={len(lost)}, of={PARTITIONS}, examples={lost[:10]}]")

            assert lost, \
                "Every partition of the cell that lived in DC2 was expected to lose its last " \
                "owner when DC2 went away."

            self.pause("dc-lost", mdc,
                       show(self, "PARTITION DISTRIBUTION", mdc.control(DC_1).distribution(caches=CACHE_NAME)))

            mdc.stop_servers()
