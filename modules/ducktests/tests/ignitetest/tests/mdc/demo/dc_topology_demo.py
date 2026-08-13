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
DEMO: data center aware topology, and the commands that show it.

The discovery ring is ordered by data center id first, so the nodes of one DC sit next to
each other on the ring and it crosses a DC boundary as few times as it possibly can - once
per DC. That is what ``control.sh --data-center print_topology`` reports, together with how
the nodes and the thin client connections are spread over the data centers.

The DC id of a node is a first class node attribute: it is published as
``IGNITE_DATA_CENTER_ID`` and shown in the NODES system view, so it can be read without any
MDC specific tooling at all.

Breakpoints: `dc-topology`, `thin-clients-connected`.
"""
from ignitetest.services.ignite_app import IgniteApplicationService
from ignitetest.services.mdc.mdc_cluster import MdcCluster, cross_dc_network, dc_jvm_opts, \
    DATA_CENTER_ATTR, DCS_2, DCS_3, DC_1, THIN_LOAD_APP
from ignitetest.services.utils.ignite_configuration import IgniteThinClientConfiguration
from ignitetest.tests.mdc.demo.util import show
from ignitetest.utils import cluster, ignite_versions
from ignitetest.utils.ignite_test import IgniteTest
from ignitetest.utils.version import DEV_BRANCH

CACHE_NAME = "mdc-demo-topology"

KEYS = 100

# The thin clients only need to be connected while the topology is printed, so they read in a
# slow loop until the demo stops them.
THIN_READ_PAUSE_MS = 200

NODES_VIEW = "NODES"


class MdcDcTopologyDemo(IgniteTest):
    """
    Demonstrates DC aware ring formation and the commands that visualize it.
    """
    @cluster(num_nodes=6)
    @ignite_versions(str(DEV_BRANCH))
    def demo_dc_aware_ring_and_node_attributes(self, ignite_version):
        """
        Three data centers, two servers each, nothing else - the topology alone. The MDC
        topology output names every DC and shows how the ring visits them, and the NODES
        system view carries the DC id of every node as a plain attribute.
        """
        mdc = MdcCluster(self, ignite_version, dcs=DCS_3, srv_per_dc=2, runners_per_dc=0)

        with cross_dc_network(self.logger, mdc):
            mdc.start_servers()

            topology = mdc.control().data_center_topology()

            nodes_view = mdc.control().system_view(NODES_VIEW)

            for dc in mdc.dcs:
                assert dc in topology, f"The MDC topology output does not mention {dc}:\n{topology}"

                assert dc in nodes_view, f"The {NODES_VIEW} system view does not carry {dc}:\n{nodes_view}"

            self.logger.info(f"MDC topology reports every data center [dcs={list(mdc.dcs)}, "
                             f"attribute={DATA_CENTER_ATTR}]")

            self.pause("dc-topology", mdc,
                       show(self, "MDC TOPOLOGY", topology),
                       show(self, f"{NODES_VIEW} SYSTEM VIEW", nodes_view))

            mdc.stop_servers()

    @cluster(num_nodes=7)
    @ignite_versions(str(DEV_BRANCH))
    def demo_thin_client_connections_are_reported_per_dc(self, ignite_version):
        """
        One thin client pinned to each data center, both holding open connections while the
        topology is printed. The MDC topology output groups the connections by the DC of the
        client that opened them - and counts physical connections, not client instances.
        """
        mdc = MdcCluster(self, ignite_version, dcs=DCS_2, srv_per_dc=2, runners_per_dc={DC_1: 1},
                         client_connector=True)

        clients = {dc: self._thin_client(mdc, dc) for dc in mdc.dcs}

        for dc, client in clients.items():
            mdc.register(dc, client)

        with cross_dc_network(self.logger, mdc):
            mdc.start_servers()

            mdc.generate_data(DC_1, CACHE_NAME, 0, KEYS)

            # Background reads: the clients have to be connected when the topology is printed,
            # so they are started here and stopped only after the breakpoint.
            for dc, client in clients.items():
                client.params = {"mode": "GET", "cacheName": CACHE_NAME, "keyFrom": 0, "keyTo": KEYS,
                                 "iterations": 0, "opPauseMs": THIN_READ_PAUSE_MS,
                                 "resultPrefix": f"thinGet{dc}"}

                client.start(clean=True)

            topology = mdc.control().data_center_topology()

            for dc in mdc.dcs:
                assert dc in topology, f"The MDC topology output does not mention {dc}:\n{topology}"

            self.pause("thin-clients-connected", mdc, show(self, "MDC TOPOLOGY", topology))

            for client in clients.values():
                client.stop()

            mdc.stop_servers()

    def _thin_client(self, mdc: MdcCluster, dc: str) -> IgniteApplicationService:
        """
        Builds a thin client pinned to the given DC. It is handed the addresses of the servers
        of every DC, so that which DC it talks to is decided by routing and not by its address
        list.
        """
        return IgniteApplicationService(
            self.test_context,
            IgniteThinClientConfiguration(addresses=mdc.thin_client_addresses(),
                                          version=mdc.ignite_config.version),
            java_class_name=THIN_LOAD_APP,
            num_nodes=1,
            jvm_opts=dc_jvm_opts(dc))
