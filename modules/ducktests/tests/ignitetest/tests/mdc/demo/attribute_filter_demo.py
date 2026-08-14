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
DEMO: splitting the copies of a partition finer than by data center.

MdcAffinityBackupFilter knows exactly one thing about a node - its data center - and spreads
the copies of every partition evenly over the DCs. It says nothing about where inside a DC
those copies land, so with two copies per DC both of them may well sit on the same rack, in
the same availability zone, behind the same power feed.

ClusterNodeAttributeAffinityBackupFilter is the tool for that next level down. It is built
with a list of node attribute names, and rejects a candidate node when some already chosen
copy of the partition matches it on ALL of them. The tuple of attribute values is therefore
the key of a group, and no two copies of a partition may share a group. Handing it both the
data center and an availability zone gives the guarantee on both levels at once.

Two things are worth knowing before using it:

  - a RendezvousAffinityFunction holds exactly ONE affinity backup filter, so this one
    REPLACES MdcAffinityBackupFilter rather than complementing it. The DC dimension survives
    only because the DC attribute is in the list;
  - the attribute the cluster's own MDC safety metric looks for is the internal
    ``org.apache.ignite.datacenter.id``, not the ``IGNITE_DATA_CENTER_ID`` system property
    that sets it. A filter listing only an availability zone leaves the cache reported as
    not MDC safe - correctly, as the second scenario here shows.

When the groups run out the filter DISCARDS the surplus copies rather than doubling up, so a
partition can end up with fewer owners than ``backups + 1`` asks for. That is a feature: it
refuses to cram two copies into one failure domain.

Breakpoints: `data-loaded`, `distribution`.
"""
from ignitetest.services.mdc.mdc_cluster import MdcCluster, cross_dc_network, DATA_CENTER_ATTR, DC_1
from ignitetest.tests.mdc.demo.util import partitions_missing_a_dc, show
from ignitetest.utils import cluster, ignite_versions
from ignitetest.utils.ignite_test import IgniteTest
from ignitetest.utils.version import DEV_BRANCH

CACHE_NAME = "mdc-demo-attribute-filter"

KEYS = 200

# The availability zone every server is tagged with, on top of its data center.
ZONE_ATTR = "AVAILABILITY_ZONE"

ZONES = ("AZ1", "AZ2")

# The internal node attribute holding the DC id. The IGNITE_DATA_CENTER_ID system property
# sets it, and both are readable node attributes - but the metric behind
# IsCacheAffinityConfigurationMdcSafe accepts only this one as proof that the filter is DC
# aware, so this is the name to hand the filter.
DC_NODE_ATTR = "org.apache.ignite.datacenter.id"

# Four copies over four (DC, zone) groups: one per group, hence two per DC in distinct zones.
BACKUPS_ONE_PER_GROUP = 3


class MdcAttributeFilterDemo(IgniteTest):
    """
    Demonstrates ClusterNodeAttributeAffinityBackupFilter as a finer grained replacement of
    the MDC affinity backup filter.
    """
    @cluster(num_nodes=9)
    @ignite_versions(str(DEV_BRANCH))
    def demo_attribute_filter_splits_copies_into_subgroups_inside_a_dc(self, ignite_version):
        """
        Two data centers, two availability zones in each, two servers in every zone. The
        filter is handed both attributes, so the four copies of every partition go to four
        different (DC, zone) pairs - which is two copies per DC that cannot share a zone.
        """
        mdc = MdcCluster(self, ignite_version, srv_per_dc=4, srv_groups=(ZONE_ATTR, ZONES),
                         runners_per_dc={DC_1: 1}, jmx_metrics=True)

        with cross_dc_network(self.logger, mdc):
            mdc.start_servers()

            mdc.generate_data(DC_1, CACHE_NAME, 0, KEYS, backups=BACKUPS_ONE_PER_GROUP,
                              backupFilterKind="ATTRIBUTE", backupFilterAttrs=[DC_NODE_ATTR, ZONE_ATTR])

            self.pause("data-loaded", mdc)

            # The point of the scenario: one copy in every (DC, zone) group...
            mdc.verify_cache_group_distribution(CACHE_NAME, copies_per_group=1)

            # ...which is the same thing as two copies per DC, read on the coarser axis.
            mdc.verify_cache_distribution(CACHE_NAME, copies_per_dc=2)

            self.logger.info(f"Copies spread over subgroups [cache={CACHE_NAME}, "
                             f"attrs={[DC_NODE_ATTR, ZONE_ATTR]}, dcs={list(mdc.dcs)}, zones={list(ZONES)}]")

            # The DC attribute is in the filter's list, so the cluster still calls the cache
            # MDC safe - the finer grouping did not cost the coarser guarantee.
            mdc.verify_cache_mdc_metrics(CACHE_NAME, affinity_safe=True, distribution_safe=True)

            self.pause("distribution", mdc,
                       show(self, "PARTITION DISTRIBUTION", mdc.control().distribution(caches=CACHE_NAME)))

            mdc.stop_servers()

    @cluster(num_nodes=5)
    @ignite_versions(str(DEV_BRANCH))
    def demo_a_subgroup_attribute_alone_does_not_keep_a_copy_in_every_dc(self, ignite_version):
        """
        The same filter listing only the availability zone. Copies do land in different zones,
        exactly as asked - but a zone spans both data centers, so nothing stops both copies of
        a partition from ending up in one DC, and some of them do.

        The cluster says so itself: with no DC attribute in the filter's list the cache is
        reported as not MDC safe. This is the contrast that says why the DC attribute has to
        be in there, and it is not a supported setup.
        """
        mdc = MdcCluster(self, ignite_version, srv_per_dc=2, srv_groups=(ZONE_ATTR, ZONES),
                         runners_per_dc={DC_1: 1}, jmx_metrics=True)

        with cross_dc_network(self.logger, mdc):
            mdc.start_servers()

            mdc.generate_data(DC_1, CACHE_NAME, 0, KEYS, backups=mdc.min_backups,
                              backupFilterKind="ATTRIBUTE", backupFilterAttrs=[ZONE_ATTR])

            # About the CONFIGURATION alone: no DC attribute in the list, so the answer is no.
            mdc.verify_cache_mdc_metrics(CACHE_NAME, affinity_safe=False)

            distribution = mdc.control().cache_distribution(cache_names=CACHE_NAME,
                                                            user_attributes=[DATA_CENTER_ATTR, ZONE_ATTR])

            missing = partitions_missing_a_dc(distribution, mdc.dcs)

            self.logger.info(f"Partitions without a copy in every DC [count={len(missing)}, "
                             f"examples={missing[:5]}]")

            assert missing, \
                "Grouping by availability zone alone was expected to leave some partition " \
                "without a copy in one of the DCs. Either the DC attribute is still in the " \
                "filter's list, or this cluster is too small to show the difference."

            self.pause("distribution", mdc,
                       show(self, "PARTITION DISTRIBUTION", mdc.control().distribution(caches=CACHE_NAME)))

            mdc.stop_servers()
