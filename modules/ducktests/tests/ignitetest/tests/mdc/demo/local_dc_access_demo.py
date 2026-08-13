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
DEMO: reads are served inside the local data center - Cache API and SQL API alike.

Both data centers own a copy of every partition, so either one can answer any read on its
own. A client picks the copy in its own DC, and the cross-DC link is never on the path.

The demo makes that visible with latency: the cross-DC link is given a large netem delay, so
a read that crossed it would cost at least that much. Reading from BOTH DCs in turn is what
makes the measurement mean something - a client that simply fixed on one DC would look just
as fast from there and hopeless from the other side.

What the configuration has to provide for local reads, and its limits:

  - the affinity backup filter must place a copy in the reader's DC (see backup_filter_demo);
  - ``readFromBackup`` must stay on - a read pinned to the primary copy goes wherever the
    primary lives, which is one DC for every partition;
  - the write synchronization mode must not be PRIMARY_SYNC, otherwise a local copy is not
    guaranteed to be up to date and cannot be read from;
  - it is READS that stay local. A write has to reach the copies in every DC, so it pays the
    cross-DC latency by design.

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
    Demonstrates that read load is served by the reader's own data center.
    """
    @cluster(num_nodes=6)
    @ignite_versions(str(DEV_BRANCH))
    @parametrize(cross_dc_latency_ms=100)
    def demo_cache_api_reads_stay_in_the_local_dc(self, ignite_version, cross_dc_latency_ms):
        """
        Cache API: a GET burst from each DC in turn, both averaging well below the one-way
        cross-DC delay.
        """
        self._check_reads_stay_local(ignite_version, cross_dc_latency_ms, "GET", sql_mode=False)

    @cluster(num_nodes=6)
    @ignite_versions(str(DEV_BRANCH))
    @parametrize(cross_dc_latency_ms=100)
    def demo_sql_api_reads_stay_in_the_local_dc(self, ignite_version, cross_dc_latency_ms):
        """
        SQL API: the same cache with a query entity on top, read with SELECT instead of GET.
        The route the query takes to the data is the same one, so the latency is too.
        """
        self._check_reads_stay_local(ignite_version, cross_dc_latency_ms, "SQL_SELECT", sql_mode=True)

    def _check_reads_stay_local(self, ignite_version, cross_dc_latency_ms, mode: str, sql_mode: bool):
        """
        Loads the cache from one DC and then reads it from every DC, asserting that the
        average read latency stays below the one-way cross-DC delay - which a read leaving
        the DC could not do.
        """
        mdc = MdcCluster(self, ignite_version, srv_per_dc=2, runners_per_dc=1)

        with cross_dc_network(self.logger, mdc, delay_ms=cross_dc_latency_ms) as net:
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

            for dc, avg_ms in latencies.items():
                assert avg_ms < cross_dc_latency_ms, \
                    f"Reads from {dc} did not stay in the local DC [mode={mode}, avgOpMs={avg_ms}, " \
                    f"crossDcDelayMs={cross_dc_latency_ms}]"

            self.pause("reads-measured", mdc, net)

            mdc.stop_servers()
