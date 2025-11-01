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
Module contains client custom exception handling test
"""
from ignitetest.services.ignite import IgniteService
from ignitetest.services.ignite_app import IgniteApplicationService
from ignitetest.services.utils.ignite_configuration import IgniteConfiguration
from ignitetest.services.utils.ignite_configuration.discovery import from_ignite_cluster
from ignitetest.tests.rebalance.util import NUM_NODES
from ignitetest.tests.thin_client_query_test import return_spec_without_ducktests
from ignitetest.utils import cluster, ignite_versions
from ignitetest.utils.ignite_test import IgniteTest
from ignitetest.utils.version import DEV_BRANCH, IgniteVersion


class CustomComputeExceptionHandlingTest(IgniteTest):
    """
    Tests custom compute runnable exception handling by thick client.
    """
    @cluster(num_nodes=NUM_NODES)
    @ignite_versions(str(DEV_BRANCH))
    def test_compute_runnable(self, ignite_version):
        node_config = IgniteConfiguration(
            version=IgniteVersion(ignite_version),
            metric_exporters={"org.apache.ignite.spi.metric.jmx.JmxMetricExporterSpi"},
            peer_class_loading_enabled=True
        )

        nodes_count = self.test_context.expected_num_nodes - 1

        ignites = IgniteService(
            self.test_context,
            config=node_config,
            num_nodes=nodes_count)

        ignites.spec = return_spec_without_ducktests(service=ignites, base_spec=ignites.spec.__class__)

        ignites.start()

        self.check_topology(ignites, nodes_count)

        app = IgniteApplicationService(
            self.test_context,
            config=ignites.config._replace(client_mode=True, discovery_spi=from_ignite_cluster(ignites)),
            java_class_name="org.apache.ignite.internal.ducktest.tests.DataGenerationApplication")

        app.start_async()

        app.await_stopped()

        self.check_topology(ignites, nodes_count + 2)