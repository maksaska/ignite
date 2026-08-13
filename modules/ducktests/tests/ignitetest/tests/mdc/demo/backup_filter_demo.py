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
DEMO: the affinity backup filter and the guarantee it is responsible for.

MdcAffinityBackupFilter is what makes "every data center owns a copy of every partition"
true. The two scenarios here are the same cluster and the same data, once with the filter
and once without it, so that the guarantee can be seen holding and then breaking.

Both read the partition layout twice over: from the outside with
``control.sh --cache distribution``, which is where the copies of every partition and the DC
of every owner can be counted, and from the cluster's own point of view through the two
per-cache MDC safety metrics - one saying the cache is CONFIGURED for the guarantee, the
other that the current distribution delivers it.

The filter's contract and its limits:

  - ``(backups + 1)`` must be divisible by the number of DCs, otherwise the copies cannot be
    spread evenly and the filter refuses the configuration outright at cache start;
  - the quotient is how many copies each DC gets: ``backups = dcsNum - 1`` is the smallest
    admissible value and gives exactly one copy per DC;
  - every server node must carry a DC id, or the filter rejects it as a candidate.

Breakpoints: `data-loaded`, `distribution`.
"""
from ignitetest.services.mdc.mdc_cluster import MdcCluster, cross_dc_network, DATA_CENTER_ATTR, DCS_2, DCS_3, DC_1
from ignitetest.tests.mdc.demo.util import partitions_missing_a_dc, show
from ignitetest.utils import cluster, ignite_versions
from ignitetest.utils.ignite_test import IgniteTest
from ignitetest.utils.version import DEV_BRANCH

CACHE_NAME = "mdc-demo-backup-filter"

KEYS = 200


class MdcBackupFilterDemo(IgniteTest):
    """
    Demonstrates the cross-DC partition placement the MDC affinity backup filter produces.
    """
    @cluster(num_nodes=7)
    @ignite_versions(str(DEV_BRANCH))
    def demo_backup_filter_puts_a_copy_in_every_dc(self, ignite_version):
        """
        Three data centers, ``backups=2`` - the smallest value that admits one copy per DC.
        Every partition ends up with exactly one OWNING copy in each DC, which is what lets
        any single DC serve every read on its own.
        """
        mdc = MdcCluster(self, ignite_version, dcs=DCS_3, srv_per_dc=2, runners_per_dc={DC_1: 1},
                         jmx_metrics=True)

        with cross_dc_network(self.logger, mdc):
            mdc.start_servers()

            # backups defaults to the smallest value giving one copy per DC: len(dcs) - 1.
            mdc.generate_data(DC_1, CACHE_NAME, 0, KEYS)

            self.pause("data-loaded", mdc)

            distribution = mdc.verify_cache_distribution(CACHE_NAME, copies_per_dc=1)

            self.logger.info(f"Cross-DC distribution verified [cache={CACHE_NAME}, "
                             f"backups={mdc.min_backups}, dcs={list(mdc.dcs)}, "
                             f"groups={sorted(distribution.groups)}]")

            # The cluster says the same about itself: configured for the guarantee, and
            # currently delivering it.
            mdc.verify_cache_mdc_metrics(CACHE_NAME, affinity_safe=True, distribution_safe=True)

            self.pause("distribution", mdc,
                       show(self, "PARTITION DISTRIBUTION", mdc.control().distribution(caches=CACHE_NAME)))

            mdc.stop_servers()

    @cluster(num_nodes=5)
    @ignite_versions(str(DEV_BRANCH))
    def demo_without_the_backup_filter_the_guarantee_is_lost(self, ignite_version):
        """
        The same cache in two DCs with the very same backup count, only with the MDC affinity
        backup filter left off. Plain rendezvous affinity places the copies without looking at
        the DC id, so a share of the partitions keeps both copies in one DC - those partitions
        become unreadable the moment that DC goes away.

        This is the contrast that says what the filter is for; it is not a supported setup.
        """
        mdc = MdcCluster(self, ignite_version, dcs=DCS_2, srv_per_dc=2, runners_per_dc={DC_1: 1},
                         jmx_metrics=True)

        with cross_dc_network(self.logger, mdc):
            mdc.start_servers()

            mdc.generate_data(DC_1, CACHE_NAME, 0, KEYS, backupFilter=False)

            # The cache reports itself as not configured for the guarantee - that metric is
            # about the affinity configuration alone, so it is false the moment the filter is
            # gone. The distribution one is read out too, but not asserted on: it depends on
            # where plain affinity happened to put the copies.
            mdc.verify_cache_mdc_metrics(CACHE_NAME, affinity_safe=False)

            distribution = mdc.control().cache_distribution(cache_names=CACHE_NAME,
                                                            user_attributes=DATA_CENTER_ATTR)

            missing = partitions_missing_a_dc(distribution, mdc.dcs)

            self.logger.info(f"Partitions without a copy in every DC [count={len(missing)}, "
                             f"examples={missing[:5]}]")

            assert missing, \
                "Without the MDC backup filter some partition was expected to miss a DC. " \
                "Either the filter is still in effect, or this cluster is too small for " \
                "plain affinity to show the difference."

            self.pause("distribution", mdc,
                       show(self, "PARTITION DISTRIBUTION", mdc.control().distribution(caches=CACHE_NAME)))

            mdc.stop_servers()
