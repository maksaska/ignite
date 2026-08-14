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
DEMO: Cache API reads are served inside the local data center - and SQL ones are not.

Both data centers own a copy of every partition, so either one can answer any read on its
own. A Cache API read picks the copy in its own DC, and the cross-DC link is never on the
path. A SQL query does not: it is mapped onto the PRIMARY copy of every partition it needs,
wherever that primary lives.

The demo makes both visible with latency: the cross-DC link is given a large netem delay, so
a read that crossed it cannot help but show. Reading from BOTH DCs in turn is what makes the
measurement mean something - a client that simply fixed on one DC would look just as fast
from there and hopeless from the other side.

What the configuration has to provide for local Cache API reads, and its limits:

  - the affinity backup filter must place a copy in the reader's DC (see backup_filter_demo);
  - ``readFromBackup`` must stay on - a read pinned to the primary copy goes wherever the
    primary lives, which is one DC for every partition;
  - the write synchronization mode must not be PRIMARY_SYNC, otherwise a local copy is not
    guaranteed to be up to date and cannot be read from;
  - it is READS that stay local. A write has to reach the copies in every DC, so it pays the
    cross-DC latency by design;
  - it is the CACHE API. The default (H2) query engine maps a query onto primaries only -
    ``ReducePartitionMapper.stableDataNodesMap`` takes ``partNodes.get(0)`` for every
    partition - so ``readFromBackup`` never enters the picture and the DC of a node is not
    consulted at all. DC aware query mapping exists in the Calcite engine, which has its own
    coverage for it (``MultiDcQueryMappingTest``).

Breakpoints: `reads-measured`.
"""
from ducktape.mark import parametrize

from ignitetest.services.mdc.mdc_cluster import MdcCluster, cross_dc_network, DC_1
from ignitetest.utils import cluster, ignite_versions
from ignitetest.utils.ignite_test import IgniteTest
from ignitetest.utils.version import DEV_BRANCH

CACHE_NAME = "mdc-demo-local-reads"

KEYS = 100

READS = 200


class MdcLocalDcAccessDemo(IgniteTest):
    """
    Demonstrates which read load is served by the reader's own data center, and which is not.
    """
    @cluster(num_nodes=6)
    @ignite_versions(str(DEV_BRANCH))
    @parametrize(cross_dc_latency_ms=100)
    def demo_cache_api_reads_stay_in_the_local_dc(self, ignite_version, cross_dc_latency_ms):
        """
        Cache API: a GET burst from each DC in turn, both averaging well below the one-way
        cross-DC delay - which a read leaving its DC could not do.
        """
        mdc = MdcCluster(self, ignite_version, srv_per_dc=2, runners_per_dc=1)

        with cross_dc_network(self.logger, mdc, delay_ms=cross_dc_latency_ms) as net:
            latencies = self._measure_read_latencies(mdc, "GET", sql_mode=False,
                                                     cross_dc_latency_ms=cross_dc_latency_ms)

            for dc, avg_ms in latencies.items():
                assert avg_ms < cross_dc_latency_ms, \
                    f"Cache API reads from {dc} did not stay in the local DC [avgOpMs={avg_ms}, " \
                    f"crossDcDelayMs={cross_dc_latency_ms}]"

            self.pause("reads-measured", mdc, net)

            mdc.stop_servers()

    @cluster(num_nodes=6)
    @ignite_versions(str(DEV_BRANCH))
    @parametrize(cross_dc_latency_ms=100)
    def demo_sql_api_reads_cross_the_dc_boundary(self, ignite_version, cross_dc_latency_ms):
        """
        SQL API: the same cache with a query entity on top, read with SELECT instead of GET -
        and the very same local copy is not used. The default query engine maps every
        partition of a query onto its PRIMARY copy, so a SELECT is served by whichever DC
        holds the primary, and the reader's own copy is ignored.

        The arithmetic of the expected latency, and why the bound below is what it is: about
        half of the primaries live in the other DC, and each of those lookups pays a full
        round trip (twice the one-way delay), so the average lands near the one-way delay
        itself - an order of magnitude above the Cache API figure of the previous scenario.
        Half of that is a floor the measurement cannot fall through while queries are leaving
        the data center.
        """
        mdc = MdcCluster(self, ignite_version, srv_per_dc=2, runners_per_dc=1)

        with cross_dc_network(self.logger, mdc, delay_ms=cross_dc_latency_ms) as net:
            latencies = self._measure_read_latencies(mdc, "SQL_SELECT", sql_mode=True,
                                                     cross_dc_latency_ms=cross_dc_latency_ms)

            floor_ms = cross_dc_latency_ms / 2

            for dc, avg_ms in latencies.items():
                assert avg_ms >= floor_ms, \
                    f"SQL reads from {dc} look local, which the query mapping cannot deliver " \
                    f"[avgOpMs={avg_ms}, expectedAtLeastMs={floor_ms}, " \
                    f"crossDcDelayMs={cross_dc_latency_ms}]"

            self.pause("reads-measured", mdc, net)

            mdc.stop_servers()

    def _measure_read_latencies(self, mdc: MdcCluster, mode: str, sql_mode: bool,
                                cross_dc_latency_ms: int) -> dict:
        """
        Starts the cluster, loads the cache from one DC and then reads it from every DC in
        turn, so that neither DC can look fast merely because the reader happens to sit in it.

        :return: Average operation latency in milliseconds, per DC.
        """
        mdc.start_servers()

        mdc.generate_data(DC_1, CACHE_NAME, 0, KEYS, sql_mode=sql_mode)

        latencies = {}

        for dc in mdc.dcs:
            prefix = f"localRead{dc}"

            svc = mdc.run_load(dc, mode, CACHE_NAME, prefix, keyFrom=0, keyTo=KEYS, iterations=READS)

            latencies[dc] = mdc.result_float(svc, f"{prefix}AvgOpMs")

            self.logger.info(f"Read latency [dc={dc}, mode={mode}, avgOpMs={latencies[dc]}, "
                             f"crossDcDelayMs={cross_dc_latency_ms}, "
                             f"errs={mdc.result_int(svc, f'{prefix}ErrCnt')}]")

        return latencies
