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
The fixture spans an arbitrary number of data centers, which selects the
:class:`MdcTopologyValidator` mode (see :func:`mdc_topology_params`):

    mdc = MdcCluster(self, ignite_version, dcs=DCS_3, srv_per_dc=2, runners_per_dc=1)

    with cross_dc_network(self.logger, mdc, delay_ms=20) as net:
        net.enable_network_partitions(*isolation_pairs(DC_3, mdc.dcs))
        mdc.verify_segments((DC_1, DC_2), DC_3)

Globals:

    mdc_cache_topology_validator - whether the MDC caches are created with the cache level
    MdcTopologyValidator, default true.
"""
from itertools import combinations
from typing import Dict, List, Optional, Sequence, Tuple, Union

from ignitetest.services.ignite import IgniteService
from ignitetest.services.ignite_app import IgniteApplicationService
from ignitetest.services.network_group.configuration import NetworkGroupStore, CrossNetworkGroupConfiguration
from ignitetest.services.network_group.manager import NetworkGroupManager
from ignitetest.services.utils.control_utility import ControlUtility
from ignitetest.services.utils.ignite_configuration import IgniteConfiguration, TcpCommunicationSpi, \
    DataStorageConfiguration
from ignitetest.services.utils.ignite_configuration.data_storage import DataRegionConfiguration
from ignitetest.services.utils.ignite_configuration.discovery import TcpDiscoverySpi, from_ignite_cluster, \
    from_ignite_services
from ignitetest.services.utils.ssl.client_connector_configuration import ClientConnectorConfiguration
from ignitetest.utils.version import IgniteVersion

DC_1 = "DC1"
DC_2 = "DC2"
DC_3 = "DC3"

# The two DC layouts the MDC topology validator distinguishes: an even DC count is
# validated against a main DC, an odd one by a majority of visible DCs.
DCS_2 = (DC_1, DC_2)
DCS_3 = (DC_1, DC_2, DC_3)

IGNITE_STARTUP_TIMEOUT_SEC = 90

# Global: set to false to create the MDC caches without the cache level topology validator.
CACHE_TOP_VALIDATOR_GLOBAL = "mdc_cache_topology_validator"

DATA_CENTER_ATTR = "IGNITE_DATA_CENTER_ID"
IGNITE_SQL_RETRY_TIMEOUT_ATTR = "IGNITE_SQL_RETRY_TIMEOUT"

IGNITE_SQL_RETRY_TIMEOUT_MS = 1_000

_APP_PKG = "org.apache.ignite.internal.ducktest.tests.mdc."

GENERATOR_APP = _APP_PKG + "MdcDataGeneratorApplication"
DATA_CHECKER_APP = _APP_PKG + "MdcDataCheckerApplication"
LOAD_APP = _APP_PKG + "MdcContinuousLoadApplication"
THIN_LOAD_APP = _APP_PKG + "MdcThinClientLoadApplication"

# Suspicious server log patterns: none of them is expected in any MDC scenario,
# partitioned or not. Matched against the node console capture.
LRT_PATTERN = "long running transactions"
PME_FREEZE_PATTERN = "Failed to wait for partition map exchange"
LOST_PARTITIONS_PATTERN = "Detected lost partitions"
ASSERTION_ERROR_PATTERN = "AssertionError"

# Every log of a server node, including the ones rotated by a restart.
ALL_LOGS_GLOB = "ignite*.log*"

# Needed by everything that reads a node metric over JMX - await_rebalance(), the snapshot
# commands and the MDC safety metrics below among them.
JMX_METRIC_EXPORTER = "org.apache.ignite.spi.metric.jmx.JmxMetricExporterSpi"

# Per-cache metrics holding the cluster's own verdict on the MDC guarantees. Registered on
# every server node that carries a DC id, for every cache.
#
# The two are not the same statement. The affinity one is about CONFIGURATION - whether the
# cache is set up to keep a copy of every partition in every DC at all, which is what the
# MdcAffinityBackupFilter provides. The distribution one is about the CURRENT assignment
# actually doing so, which a correctly configured cache still fails while some DC has no
# nodes to place a copy on.
MDC_SAFE_AFFINITY_METRIC = "IsCacheAffinityConfigurationMdcSafe"
MDC_SAFE_DISTRIBUTION_METRIC = "IsCachePartitionDistributionSafe"

# The record a rebalancing node logs per cache group, naming the node it pulls the partitions
# from: "Starting rebalance routine [<grp>, topVer=..., supplier=<nodeId>, fullPartitions=...".
# The supplier is the only field of interest, and it never contains a comma.
REBALANCE_SUPPLIER_PATTERN = "supplier=[^,]*"

# A segment of a partitioned cluster: one DC or a group of DCs that still see each other.
Segment = Union[str, Sequence[str]]


def dc_jvm_opts(dc: str) -> List[str]:
    """
    :return: JVM options assigning a node to the given data center.
    """
    return [f"-D{DATA_CENTER_ATTR}={dc}", f"-D{IGNITE_SQL_RETRY_TIMEOUT_ATTR}={IGNITE_SQL_RETRY_TIMEOUT_MS}"]


def node_jvm_opts(dc: str, extra_attrs: Optional[Dict[str, str]] = None) -> List[str]:
    """
    Same as :func:`dc_jvm_opts` plus arbitrary extra node attributes.

    Every system property a node is started with is registered as a node attribute, which is
    what makes an attribute readable both by an affinity backup filter and by
    ``control.sh --cache distribution --user-attributes``.

    :param dc: Data center to assign the node to.
    :param extra_attrs: Attribute name -> value, e.g. ``{"CELL": "CELL1"}``.
    """
    return dc_jvm_opts(dc) + [f"-D{name}={value}" for name, value in (extra_attrs or {}).items()]


def mdc_topology_params(dcs: Sequence[str], main_dc: Optional[str] = None) -> dict:
    """
    Compiles the cache parameters that pin ``MdcTopologyValidator`` and
    ``MdcAffinityBackupFilter`` to the given DC set.

    The validator has two modes and the DC count picks one: with an EVEN number of DCs a
    segment stays writable while it sees the main DC (``mainDc``), with an ODD number
    while it sees a majority of the DC set (``datacenters``). Passing both is rejected by
    ``MdcTopologyValidator.checkConfiguration()``, so exactly one is emitted here.

    :param dcs: All data centers the cluster spans.
    :param main_dc: Main DC for the even-count mode, defaults to the first DC. Ignored for
           an odd DC count, where the validator is majority based.
    """
    params = {"dcsNum": len(dcs)}

    if len(dcs) % 2 == 1:
        params["datacenters"] = list(dcs)
    else:
        params["mainDc"] = main_dc if main_dc is not None else dcs[0]

    return params


def min_backups(dcs: Sequence[str]) -> int:
    """
    :return: Smallest backup count that gives every DC exactly one copy of every partition.
             ``MdcAffinityBackupFilter`` requires ``(backups + 1)`` to be divisible by the
             number of DCs, so this is the smallest admissible value at all.
    """
    return len(dcs) - 1


def all_pairs(dcs: Sequence[str]) -> List[Tuple[str, str]]:
    """
    :return: Every unordered DC pair - the full cross-DC mesh.
    """
    return list(combinations(dcs, 2))


def isolation_pairs(dc: str, dcs: Sequence[str]) -> List[Tuple[str, str]]:
    """
    :return: The DC pairs that cut ``dc`` off from every other DC, leaving the rest
             connected. Feed to :meth:`NetworkGroupManager.enable_network_partitions`.
    """
    return [(dc, other) for other in dcs if other != dc]


def _per_dc(value: Union[int, Dict[str, int]], dcs: Sequence[str]) -> Dict[str, int]:
    """
    Normalizes an int-or-dict per-DC count into a dict, e.g. 3 -> {DC1: 3, DC2: 3}.

    A dict naming a DC the cluster does not span is rejected here rather than left to fail
    later: such a DC is skipped by :meth:`MdcCluster.network_registry`, so its nodes would
    run with no impairments and no partition rules while every other call site kept working.
    """
    if not isinstance(value, dict):
        return {dc: value for dc in dcs}

    unknown = sorted(dc for dc in value if dc not in dcs)

    assert not unknown, \
        f"Per-DC counts name data centers the cluster does not span [unknown={unknown}, dcs={list(dcs)}]"

    return dict(value)


def _per_dc_groups(values: Union[Sequence[str], Dict[str, Sequence[str]]],
                   dcs: Sequence[str]) -> Dict[str, Tuple[str, ...]]:
    """
    Normalizes the node group values of :class:`MdcCluster` into a per-DC dict.

    A plain sequence gives every DC the same groups, which is how a group is stretched over
    the data centers; a dict gives each DC its own, which is how a group is confined to one.
    """
    if not isinstance(values, dict):
        return {dc: tuple(values) for dc in dcs}

    unknown = sorted(dc for dc in values if dc not in dcs)

    assert not unknown, \
        f"Node groups name data centers the cluster does not span [unknown={unknown}, dcs={list(dcs)}]"

    missing = sorted(dc for dc in dcs if dc not in values)

    assert not missing, \
        f"Every data center needs its node groups named [missing={missing}]"

    return {dc: tuple(values[dc]) for dc in dcs}


def _as_segment(segment: Segment) -> Tuple[str, ...]:
    """
    Normalizes a single DC name or a collection of DC names into a tuple of DC names.
    """
    return (segment,) if isinstance(segment, str) else tuple(segment)


def _fmt_segment(segment: Tuple[str, ...]) -> str:
    """
    :return: Segment rendered for an assertion message, e.g. "DC1+DC2" - a Python tuple
             reads poorly in the middle of one, and a single DC renders as itself.
    """
    return "+".join(segment)


class MdcCluster:
    """
    Owns the per-DC Ignite services and reusable application services of an MDC test,
    plus the MDC-specific verification helpers.

    :param test: The ducktape test instance.
    :param ignite_version: Ignite version string.
    :param dcs: Data centers the cluster spans, two by default. The count selects the
           topology validator mode - see :func:`mdc_topology_params`.
    :param main_dc: Main DC for an even-sized DC set, defaults to the first DC.
    :param srv_per_dc: Servers per DC, an int or a per-DC dict (asymmetric DCs).
    :param srv_groups: Splits the servers of a DC into equally sized groups carrying an extra
           node attribute, as ``(attribute name, values)``. Needed by the affinity backup
           filters that group nodes by something finer than the DC (see attribute_filter_demo
           and cell_filter_demo). ``srv_per_dc`` must be divisible by the number of a DC's
           groups. The values are either a plain sequence, giving every DC the same groups -
           ``("CELL", ("CELL1", "CELL2"))`` stretches both cells over both DCs - or a per-DC
           dict, giving each DC groups of its own -
           ``("CELL", {DC_1: ("CELL1",), DC_2: ("CELL2",)})`` confines each cell to one DC.
    :param runners_per_dc: Reusable run-to-completion app services per DC (generator,
           checkers, load bursts). An int or a per-DC dict.
    :param loaders_per_dc: Dedicated background load app services per DC. They run
           concurrently with runner apps, hence separate containers.
    :param client_connector: Whether to expose the thin client connector on servers.
    """
    def __init__(self, test, ignite_version: str, dcs: Sequence[str] = DCS_2,
                 main_dc: Optional[str] = None,
                 srv_per_dc: Union[int, Dict[str, int]] = 3,
                 srv_groups: Optional[Tuple[str, Sequence[str]]] = None,
                 runners_per_dc: Union[int, Dict[str, int]] = 1,
                 loaders_per_dc: Union[int, Dict[str, int]] = 0,
                 client_connector: bool = False,
                 persistent: bool = False,
                 jmx_metrics: bool = False,
                 network_timeout: int = 5_000,
                 tcp_connect_timeout: int = 5_000):
        self.test_context = test.test_context
        self.logger = test.logger

        self.dcs = tuple(dcs)

        assert len(self.dcs) >= 2, f"An MDC cluster spans at least two data centers [dcs={self.dcs}]"

        self.main_dc = main_dc if main_dc is not None else self.dcs[0]

        # A single discovery SPI (hence a single ip finder) shared by all DCs' server
        # services is what makes the DCs form ONE cluster: prepare_on_start() memoizes the
        # addresses of the first started DC into the shared ip finder, so every later DC
        # discovers through the first DC's nodes, and restart() re-joins the same way.
        # Restarting the first started DC itself is the one case this breaks - see
        # sync_service_discovery().
        cfg_kwargs = {
            "version": IgniteVersion(ignite_version),
            "discovery_spi": TcpDiscoverySpi(),
            "network_timeout": network_timeout,
            "communication_spi": TcpCommunicationSpi(connect_timeout=tcp_connect_timeout)
        }

        if client_connector:
            cfg_kwargs["client_connector_configuration"] = ClientConnectorConfiguration()

        if persistent:
            cfg_kwargs["data_storage"] = DataStorageConfiguration(
                default=DataRegionConfiguration(persistence_enabled=True))

        if jmx_metrics:
            # A fresh set, never the shared mutable default of IgniteConfiguration.
            cfg_kwargs["metric_exporters"] = {JMX_METRIC_EXPORTER}

        self.persistent = persistent

        self.ignite_config = IgniteConfiguration(**cfg_kwargs)

        self.srv_per_dc = _per_dc(srv_per_dc, self.dcs)

        self.srv_group_attr = srv_groups[0] if srv_groups else None

        # Per DC, because whether a group spans the data centers or sits inside one of them is
        # the whole difference between a cell that survives a DC outage and one that does not.
        self.srv_group_values: Dict[str, Tuple[str, ...]] = \
            _per_dc_groups(srv_groups[1], self.dcs) if srv_groups else {}

        self.srv_group_all: Tuple[str, ...] = tuple(sorted(
            {value for values in self.srv_group_values.values() for value in values}))

        # A DC's servers are one Ignite service per group value, because the group attribute
        # is a system property and a ducktape service hands the same command line to all of
        # its nodes. Without grouping that is exactly one service per DC, as it always was.
        self.server_groups: Dict[str, List[IgniteService]] = {
            dc: self._server_services(dc, num) for dc, num in self.srv_per_dc.items() if num > 0}

        self.runners: Dict[str, List[IgniteApplicationService]] = {
            dc: [self._app_service(dc) for _ in range(num)]
            for dc, num in _per_dc(runners_per_dc, self.dcs).items()}

        self.loaders: Dict[str, List[IgniteApplicationService]] = {
            dc: [self._app_service(dc) for _ in range(num)]
            for dc, num in _per_dc(loaders_per_dc, self.dcs).items()}

        # Extra services (e.g. thin clients) registered into a DC's network group.
        self.extras: Dict[str, List] = {dc: [] for dc in self.dcs}

        # App services that have been started at least once: the first start is clean,
        # subsequent ones preserve work dirs (and logs - hence unique result prefixes).
        self._started_apps = set()

        # Admissibility checks run on reusable services, so each check needs a unique result prefix.
        self._adm_checks = 0

        # Cache parameters applied to every cache this fixture creates, unless a call overrides them.
        self.cache_defaults = {
            "topologyValidator": self.test_context.globals.get(CACHE_TOP_VALIDATOR_GLOBAL, True)
        }

        self.logger.info(f"MDC cache defaults [{self.cache_defaults}]")

    def _server_services(self, dc: str, num_nodes: int) -> List[IgniteService]:
        """
        Builds a DC's server services: one per group value when the cluster is grouped,
        a single one otherwise.
        """
        def service(nodes, extra_attrs=None):
            return IgniteService(self.test_context, self.ignite_config, num_nodes=nodes,
                                 jvm_opts=node_jvm_opts(dc, extra_attrs),
                                 startup_timeout_sec=IGNITE_STARTUP_TIMEOUT_SEC)

        if not self.srv_group_attr:
            return [service(num_nodes)]

        values = self.srv_group_values[dc]

        assert num_nodes % len(values) == 0, \
            f"Servers of a DC must split evenly between its node groups " \
            f"[dc={dc}, servers={num_nodes}, groups={list(values)}]"

        return [service(num_nodes // len(values), {self.srv_group_attr: value}) for value in values]

    @property
    def servers(self) -> Dict[str, IgniteService]:
        """
        :return: The single server service of every DC.

        Only meaningful for an ungrouped cluster. A grouped one has several server services
        per DC and has to say which it means, so this raises rather than silently answering
        about the first group - see :meth:`dc_servers` and :meth:`all_servers`.
        """
        assert not self.srv_group_attr, \
            f"A cluster split into node groups has several server services per DC; use " \
            f"dc_servers()/all_servers() [attribute={self.srv_group_attr}, " \
            f"groups={list(self.srv_group_all)}]"

        return {dc: services[0] for dc, services in self.server_groups.items()}

    def dc_servers(self, dc: str) -> List[IgniteService]:
        """
        :return: All server services of the given DC, one per node group.
        """
        return self.server_groups.get(dc, [])

    def all_servers(self) -> List[IgniteService]:
        """
        :return: Every server service of the cluster, DCs in order.
        """
        return [svc for dc in sorted(self.server_groups) for svc in self.server_groups[dc]]

    def _group_of(self, dc: str, service: IgniteService) -> Optional[str]:
        """
        :return: Node group value the given server service of a DC carries, None if the
                 cluster is not grouped.
        """
        if not self.srv_group_attr:
            return None

        return self.srv_group_values[dc][self.server_groups[dc].index(service)]

    def node_groups(self) -> Dict[str, str]:
        """
        Node id (as the node logs it about itself) -> node group value, for every alive
        server node. The counterpart of :meth:`node_dcs` for the extra grouping attribute,
        and the way a scenario checks how a group is laid out over the data centers.

        :return: Empty dict when the cluster is not split into node groups.
        """
        if not self.srv_group_attr:
            return {}

        return {svc.node_id(node).lower(): self._group_of(dc, svc)
                for dc, services in self.server_groups.items()
                for svc in services for node in svc.alive_nodes}

    def group_dcs(self) -> Dict[str, List[str]]:
        """
        :return: Node group value -> the DCs it has server nodes in. A group present in every
                 DC is "stretched" across them, which is what makes a colocated cell survive
                 the loss of a data center.
        """
        spread: Dict[str, List[str]] = {value: [] for value in self.srv_group_all}

        for dc in self.dcs:
            for svc in self.dc_servers(dc):
                if svc.nodes:
                    spread[self._group_of(dc, svc)].append(dc)

        return spread

    @property
    def min_backups(self) -> int:
        """
        :return: Smallest backup count giving every DC one copy of every partition
                 (2 for a three DC cluster, 1 for a two DC one).
        """
        return min_backups(self.dcs)

    def topology_params(self) -> dict:
        """
        :return: Cache parameters pinning the topology validator and the affinity backup
                 filter to this cluster's DC set.
        """
        return mdc_topology_params(self.dcs, self.main_dc)

    def sync_service_discovery(self):
        """
        Points every server service at a discovery SPI covering all DCs.

        Required before restarting the FIRST started DC: the shared ip finder holds only
        that DC's addresses, so after a full stop its nodes would seed off themselves and
        form a separate cluster instead of rejoining the surviving DCs.
        """
        discovery_spi = from_ignite_services(self.all_servers())

        for service in self.all_servers():
            service.config = service.config._replace(discovery_spi=discovery_spi)

    def _app_service(self, dc: str) -> IgniteApplicationService:
        # Seeding off the DC's first server service is enough: all of them are one cluster.
        client_cfg = self.ignite_config._replace(client_mode=True,
                                                 discovery_spi=from_ignite_cluster(self.dc_servers(dc)[0]))

        return IgniteApplicationService(self.test_context, client_cfg, jvm_opts=dc_jvm_opts(dc))

    def register(self, dc: str, service):
        """
        Registers an extra service (e.g. a thin client app) into a DC's network group,
        so netem impairments and partitions apply to it. Must be called before
        :func:`cross_dc_network` snapshots the registry into a :class:`NetworkGroupManager`.
        """
        self.extras[dc].append(service)

    def network_registry(self) -> Dict[str, List]:
        """
        :return: Network group registry: DC name -> all services belonging to that DC.
        """
        registry = {}

        for dc in self.dcs:
            services = list(self.dc_servers(dc))

            services += self.runners.get(dc, [])
            services += self.loaders.get(dc, [])
            services += self.extras.get(dc, [])

            if services:
                registry[dc] = services

        return registry

    def describe(self) -> List[str]:
        """
        Describes the cluster per data center for a demo breakpoint banner
        (see :meth:`ignitetest.utils.ignite_test.IgniteTest.pause`). The generic banner sees
        a flat list of services, which is where the DC each node belongs to gets lost.

        Structure only - which node is up is what the banner's own service section reports,
        and it pays an SSH probe per node to find out.

        :return: Section lines, the first one being the section title.
        """
        lines = ["DATA CENTERS"]

        # A grouped cluster gets a server row per group: which node sits in which cell or
        # availability zone is precisely what those scenarios are being watched for.
        def server_roles(dc):
            if not self.srv_group_attr:
                return [("server", self.dc_servers(dc))]

            return [(f"server {self._group_of(dc, svc)}", [svc]) for svc in self.dc_servers(dc)]

        for dc in self.dcs:
            roles = [(label, [node.account.hostname for svc in services for node in svc.nodes])
                     for label, services in (server_roles(dc) +
                                             [("runner", self.runners.get(dc, [])),
                                              ("loader", self.loaders.get(dc, [])),
                                              ("extra", self.extras.get(dc, []))])]

            # A DC that holds nothing is not named at all: an empty header reads as a DC whose
            # nodes have gone, which is exactly what a partition demo is being watched for.
            if not any(hosts for _, hosts in roles):
                continue

            lines.append(f"  {dc}")
            lines.extend(f"    {label:<12} {' '.join(hosts)}" for label, hosts in roles if hosts)

        return lines

    def thin_client_addresses(self) -> List[str]:
        """
        :return: Thin client addresses of all server nodes across all DCs.
        """
        port = self.ignite_config.client_connector_configuration.port

        return [f"{node.account.hostname}:{port}" for svc in self.all_servers() for node in svc.nodes]

    def start_servers(self, activate: Optional[bool] = None):
        """
        Starts all server services.

        :param activate: Whether to activate the cluster afterwards. By default a persistent
               cluster is activated (it comes up INACTIVE) and an in-memory one is not (it
               comes up ACTIVE already).
        """
        for svc in self.all_servers():
            svc.start()

        if activate or (activate is None and self.persistent):
            self.control().activate()

    def stop_servers(self):
        """
        Stops all server services.
        """
        for svc in self.all_servers():
            svc.stop()

    def stop_dcs(self, *dcs: str):
        """
        Stops the server services of the given DCs, in the order given - a data center
        outage, as opposed to the network partition :func:`cross_dc_network` produces.
        """
        for dc in dcs:
            for svc in self.dc_servers(dc):
                svc.stop()

    def start_dcs(self, *dcs: str, clean: bool = False, await_rebalance: bool = True):
        """
        Starts the given DCs back, by default preserving their persistence, and waits until
        the cluster has rebalanced onto them.

        Every DC is started before the first wait, so that their joins are not serialized
        behind each other's rebalance.

        Restarting the FIRST started DC needs :meth:`sync_service_discovery` beforehand.
        """
        for dc in dcs:
            for svc in self.dc_servers(dc):
                svc.start(clean=clean)

        if await_rebalance:
            for dc in dcs:
                for svc in self.dc_servers(dc):
                    svc.await_rebalance()

    def restart(self, dc: str, clean: bool = False, await_rebalance: bool = True):
        """
        Restarts a whole DC preserving its persistence (the pattern used to rejoin a
        read-only segment back into the main cluster after a partition heals).
        """
        for svc in self.dc_servers(dc):
            svc.stop()

        for svc in self.dc_servers(dc):
            svc.start(clean=clean)

        if await_rebalance:
            for svc in self.dc_servers(dc):
                svc.await_rebalance()

    def run_app(self, dc: str, java_class: str, params: dict, runner: int = 0) -> IgniteApplicationService:
        """
        Runs a run-to-completion application on one of the DC's reusable runner services
        and returns the service (for ``extract_result``).
        """
        return self.run_service(self.runners[dc][runner], params, java_class=java_class)

    def run_service(self, svc: IgniteApplicationService, params: dict,
                    java_class: str = None) -> IgniteApplicationService:
        """
        Runs any reusable run-to-completion application service (a runner, a registered
        thin client, ...): the first start is clean, subsequent starts preserve work dirs.
        Returns the service (for ``extract_result``).
        """
        if java_class is not None:
            svc.java_class_name = java_class

        svc.params = self._with_cache_params(params)

        svc.start(clean=self._first_start(svc))
        svc.wait()
        svc.stop()

        return svc

    def start_loader(self, dc: str, params: dict, loader: int = 0,
                     java_class: str = LOAD_APP) -> IgniteApplicationService:
        """
        Starts a background load application (runs until stopped). Any exception raised
        by the application surfaces in :meth:`stop_loader`. A load that creates the cache
        (``createCache``) has the MDC cache parameters injected - see
        :meth:`_with_cache_params`.
        """
        svc = self.loaders[dc][loader]

        svc.java_class_name = java_class
        svc.params = self._with_cache_params(params)

        svc.start(clean=self._first_start(svc))

        return svc

    def stop_loader(self, dc: str, loader: int = 0) -> IgniteApplicationService:
        """
        Stops a background load application. The application finishes its loop, records
        results and exits; a failed application fails the test here.
        """
        svc = self.loaders[dc][loader]

        svc.stop()

        return svc

    def _with_cache_params(self, params: dict, creates_cache: bool = False) -> dict:
        """
        Injects everything the MDC cache is configured from - the topology validator mode,
        the DC count the affinity backup filter needs, and :attr:`cache_defaults` - into
        the parameters of an application that creates it, so no call site can configure a
        cache that disagrees with the DC set. Explicit parameters still win.

        The single injection point for all of it: an application that does not create the
        cache is handed none of it, since it would only ever be ignored.

        :param creates_cache: Whether the application always creates the cache. The ones
               that decide at run time say so with a ``createCache`` parameter instead.
        """
        if not (creates_cache or params.get("createCache")):
            return params

        return {**self.topology_params(), **self.cache_defaults, **params}

    def _first_start(self, svc) -> bool:
        first = id(svc) not in self._started_apps

        self._started_apps.add(id(svc))

        return first

    def generate_data(self, dc: str, cache_name: str, from_idx: int, to_idx: int, backups: Optional[int] = None,
                      sql_mode: bool = False, **cache_params) -> IgniteApplicationService:
        """
        Creates the MDC cache (if absent) and populates keys ``[from_idx, to_idx)``.
        Extra cache parameters (``atomicity``, ``writeSync``, ``readFromBackup``,
        ``partitions``, ...) are passed through to the cache configuration builder, on top
        of the MDC cache parameters - see :meth:`_with_cache_params`.

        :param backups: Backup count, by default the smallest one that gives every DC a
               single copy of every partition (see :attr:`min_backups`).
        """
        params = {"cacheName": cache_name,
                  "backups": self.min_backups if backups is None else backups,
                  "from": from_idx, "to": to_idx, "sqlMode": sql_mode,
                  **cache_params}

        # The generator always creates the cache, so it carries no createCache parameter
        # for _with_cache_params() to key off.
        return self.run_app(dc, GENERATOR_APP, self._with_cache_params(params, creates_cache=True))

    def check_data(self, dc: str, cache_name: str, from_idx: int, to_idx: int) -> Optional[IgniteApplicationService]:
        """
        Verifies that every key in ``[from_idx, to_idx)`` is readable and holds the
        expected value, from a client in the given DC.

        :return: The service that ran the check, or None for an empty range.
        """
        if to_idx <= from_idx:
            self.logger.debug(f"Nothing to check [cache={cache_name}, from={from_idx}, to={to_idx}]")
            return None

        params = {"cacheName": cache_name, "from": from_idx, "to": to_idx}

        return self.run_app(dc, DATA_CHECKER_APP, params)

    def check_put_admissibility(self, dc: str, cache_name: str, admissible: bool,
                                key_offset: int = 1_000_000, probes: int = 100) -> IgniteApplicationService:
        """
        Verifies that put load from the given DC is admissible (the segment passes the
        topology validator) or rejected by it (read-only segment). A PUT burst of the load
        application: an admissible check fails fast on the first rejected put, an
        inadmissible check fails if any of the probe puts succeeds.

        Probe keys start at ``key_offset`` (defaults to 1_000_000) so they never intersect
        with the data set verified by ``check_data``.
        """
        self._adm_checks += 1

        return self.run_load(dc, "PUT", cache_name, f"admCheck{self._adm_checks}",
                             keyFrom=key_offset, keyTo=key_offset + probes,
                             iterations=probes, inadmissible=not admissible)

    def run_load(self, dc: str, mode: str, cache_name: str, result_prefix: str,
                 runner: int = 0, **params) -> IgniteApplicationService:
        """
        Runs a load burst (see ``MdcContinuousLoadApplication``) and returns the service.
        ``result_prefix`` must be unique per burst because runner services are reused.
        A burst that creates the cache (``createCache``) has the MDC cache parameters
        injected - see :meth:`_with_cache_params`.
        """
        load_params = {"mode": mode, "cacheName": cache_name, "resultPrefix": result_prefix, **params}

        return self.run_app(dc, LOAD_APP, load_params, runner=runner)

    def control(self, dc: Optional[str] = None) -> ControlUtility:
        """
        :return: Control utility bound to the given DC's servers, the first DC by default.
        """
        return ControlUtility(self.dc_servers(dc if dc is not None else self.dcs[0])[0])

    def set_main_dc(self, dc: str, new_main_dc: Optional[str] = None) -> str:
        """
        Dynamically reassigns the main data center from within the given DC, handing write
        access to ``new_main_dc`` - the DC the command is run on by default, which is the
        only assignment a lone surviving segment can usefully make.

        See :meth:`ControlUtility.set_main_dc` for the lifetime of the assignment.

        :param dc: DC whose servers the control utility is run against.
        :param new_main_dc: DC to become the main one, ``dc`` by default.
        :return: Output of the command.
        """
        return self.control(dc).set_main_dc(dc if new_main_dc is None else new_main_dc)

    def node_dcs(self) -> Dict[str, str]:
        """
        :return: Node id (as the node logs it about itself) -> DC the node belongs to, for
                 every alive server node. Costs an SSH round-trip per node, so the result is
                 worth reusing - it is what turns a node id printed by the cluster (a
                 rebalance supplier, a baseline entry) into the DC it sits in.
        """
        return {svc.node_id(node).lower(): dc
                for dc, services in self.server_groups.items()
                for svc in services for node in svc.alive_nodes}

    def rebalance_suppliers(self, dc: str, node) -> List[str]:
        """
        :param dc: DC the node belongs to.
        :param node: Server node that has rebalanced.
        :return: Ids of the nodes the given node pulled partitions from, read out of its own
                 log. Empty when the node rebalanced nothing.
        """
        owner = next(svc for svc in self.dc_servers(dc) if node in svc.nodes)

        out = owner.exec_command(
            node, f"grep -o '{REBALANCE_SUPPLIER_PATTERN}' {node.log_file} || true")

        return sorted({line.split("=", 1)[1].strip().lower() for line in out.splitlines() if "=" in line})

    def cache_mdc_metrics(self, cache_name: str, dc: Optional[str] = None) -> Dict[str, bool]:
        """
        Reads the cache's MDC safety metrics off a server node over JMX - see
        :data:`MDC_SAFE_AFFINITY_METRIC` and :data:`MDC_SAFE_DISTRIBUTION_METRIC` for what
        each of them claims.

        Requires the cluster to have been built with ``jmx_metrics=True``, since the metrics
        are only exposed by the JMX metric exporter.

        :param cache_name: Cache to read the metrics of.
        :param dc: DC whose node answers, the first one by default. Every server node reports
               the same verdict, so this only matters for a partitioned cluster - where each
               segment answers about the topology IT can see.
        :return: Metric name -> value.
        """
        node = next(node for svc in self.dc_servers(dc if dc is not None else self.dcs[0])
                    for node in svc.alive_nodes)

        mbean = node.cache_mbean(cache_name)

        return {name: mbean.bool_value(name)
                for name in (MDC_SAFE_AFFINITY_METRIC, MDC_SAFE_DISTRIBUTION_METRIC)}

    def verify_cache_mdc_metrics(self, cache_name: str, affinity_safe: Optional[bool] = None,
                                 distribution_safe: Optional[bool] = None, dc: Optional[str] = None):
        """
        Verifies the MDC safety metrics of a cache against what the scenario expects. Both
        expectations are optional: a metric left as None is only reported, which is how a
        scenario reads out a value whose verdict depends on the state of the topology rather
        than on the point being made.

        :return: The metrics that were read.
        """
        metrics = self.cache_mdc_metrics(cache_name, dc)

        self.logger.info(f"MDC safety metrics [cache={cache_name}, dc={dc}, {metrics}]")

        for name, expected in ((MDC_SAFE_AFFINITY_METRIC, affinity_safe),
                               (MDC_SAFE_DISTRIBUTION_METRIC, distribution_safe)):
            if expected is not None:
                assert metrics[name] == expected, \
                    f"{name} should be {expected} [cache={cache_name}, actual={metrics[name]}]"

        return metrics

    def verify_cache_distribution(self, cache_name: str, copies_per_dc: Optional[int] = None,
                                  dc: Optional[str] = None):
        """
        Verifies that every partition of the cache has an OWNING copy in every DC, and
        optionally that each DC holds exactly ``copies_per_dc`` copies.

        :return: The CacheDistribution for further custom assertions.
        """
        distribution = self.control(dc).cache_distribution(cache_names=cache_name, user_attributes=DATA_CENTER_ATTR)

        assert_cross_dc_distribution_by_attribute(distribution, dc_attr=DATA_CENTER_ATTR,
                                                  expected_dcs=self.dcs, copies_per_dc=copies_per_dc)

        return distribution

    def group_distribution(self, cache_name: str, dc: Optional[str] = None):
        """
        :return: CacheDistribution carrying both the DC and the node group of every copy, so
                 that a scenario can assert on either dimension or on the pair.
        """
        return self.control(dc).cache_distribution(
            cache_names=cache_name, user_attributes=[DATA_CENTER_ATTR, self.srv_group_attr])

    def verify_cache_group_distribution(self, cache_name: str, copies_per_group: Optional[int] = None,
                                        dc: Optional[str] = None):
        """
        Verifies that every partition has an OWNING copy in every (DC, node group) pair, and
        optionally exactly ``copies_per_group`` of them - the guarantee
        ``ClusterNodeAttributeAffinityBackupFilter`` gives when it is handed both attributes.

        :return: The CacheDistribution for further custom assertions.
        """
        distribution = self.group_distribution(cache_name, dc)

        expected = [(dc_name, value) for dc_name in self.dcs for value in self.srv_group_values[dc_name]]

        assert_distribution_by_attributes(distribution, attrs=[DATA_CENTER_ATTR, self.srv_group_attr],
                                          expected_values=expected, copies_per_value=copies_per_group)

        return distribution

    def verify_cache_colocation(self, cache_name: str, dc: Optional[str] = None):
        """
        Verifies that every partition keeps all of its OWNING copies inside one node group -
        the cell ``ClusterNodeAttributeColocatedBackupFilter`` builds.

        :return: (CacheDistribution, cell of every partition per cache group).
        """
        distribution = self.group_distribution(cache_name, dc)

        cells = assert_partitions_colocated_by_attribute(distribution, attr=self.srv_group_attr,
                                                         expected_values=self.srv_group_all)

        return distribution, cells

    def verify_split_brain(self):
        """
        Verifies that the network partition split the cluster into as many independent
        segments as there are DCs, i.e. every DC ended up on its own.
        """
        self.verify_segments(*self.dcs)

    def verify_segments(self, *segments: Segment):
        """
        Verifies that the cluster has split into exactly the given independent segments:
        every segment is healthy on its own, no two segments share a baseline node, and
        each segment elected its own coordinator.

        A segment is a DC name or a collection of DC names that still see each other, e.g.
        ``verify_segments((DC_1, DC_2), DC_3)`` for a cluster with DC3 cut off.
        """
        normalized = [_as_segment(segment) for segment in segments]

        # The state each segment is checked healthy against is the same one its baseline and
        # coordinator are read from: a partitioned segment answers control.sh over the very
        # links the test just cut, so it is fetched once per segment and passed around.
        states = {segment: self.verify_segment_healthy(segment) for segment in normalized}

        baselines = {segment: {node.consistent_id for node in states[segment].baseline} for segment in normalized}

        for seg_a, seg_b in combinations(normalized, 2):
            common_nodes = baselines[seg_a] & baselines[seg_b]

            assert not common_nodes, \
                f"Segment baselines should not intersect [common={sorted(common_nodes)}, " \
                f"{_fmt_segment(seg_a)}={sorted(baselines[seg_a])}, " \
                f"{_fmt_segment(seg_b)}={sorted(baselines[seg_b])}]"

        coordinators = {}

        for segment in normalized:
            coordinator = states[segment].coordinator

            assert coordinator, \
                f"Coordinator is not found in the {_fmt_segment(segment)} segment baseline output!"

            assert coordinator.consistent_id in baselines[segment], \
                f"{_fmt_segment(segment)} coordinator should belong to its own segment baseline " \
                f"[coordinator={coordinator.consistent_id}, baseline={sorted(baselines[segment])}]"

            coordinators[_fmt_segment(segment)] = coordinator.consistent_id

        assert len(set(coordinators.values())) == len(normalized), \
            f"Every segment should have elected its own coordinator [coordinators={coordinators}]"

    def verify_segment_healthy(self, segment: Segment):
        """
        Verifies that a segment is fully alive, ACTIVE, and its baseline covers exactly
        the servers of the DCs it consists of - and nothing else.

        :return: The ClusterState the segment was verified against, so that a caller
                 asserting further on it (see :meth:`verify_segments`) needs no second
                 control.sh round-trip into a segment that may be cut off.
        """
        dcs = _as_segment(segment)

        name = _fmt_segment(dcs)

        exp_alive_nodes = sum(self.srv_per_dc[dc] for dc in dcs)
        act_alive_nodes = sum(len(svc.alive_nodes) for dc in dcs for svc in self.dc_servers(dc))

        assert act_alive_nodes == exp_alive_nodes, \
            f"{exp_alive_nodes} nodes should be alive in {name}! [actual={act_alive_nodes}]"

        cluster_state = self.control(dcs[0]).cluster_state()

        assert "ACTIVE" == cluster_state.state, \
            f"{name} segment state should remain ACTIVE [actual={cluster_state.state}]"

        assert len(cluster_state.baseline) == exp_alive_nodes, \
            f"{name} segment baseline is not expected " \
            f"[exp={exp_alive_nodes}, actual_baseline={cluster_state.baseline}]"

        return cluster_state

    def verify_whole_cluster_healthy(self):
        """
        Verifies that all DCs form a single ACTIVE cluster: every server node is alive
        and the baseline seen from the first DC covers all servers of every DC.
        """
        self.verify_segment_healthy(self.dcs)

    def verify_servers_log_clean(self):
        """
        Verifies the negative invariants on all server nodes: no long running transactions
        were detected, no PME hang and no lost partitions were reported.
        """
        for pattern in (LRT_PATTERN, PME_FREEZE_PATTERN, LOST_PARTITIONS_PATTERN, ASSERTION_ERROR_PATTERN):
            for svc in self.all_servers():
                svc.check_event_absent(pattern, log_file=ALL_LOGS_GLOB)

    def verify_no_hanging_txs(self, dc: Optional[str] = None, try_kill_hanging_tx: bool = False):
        """
        Verifies that no active transactions are left on the cluster.
        """
        txs = self.control(dc).tx()

        if isinstance(txs, list) and len(txs) > 0 and try_kill_hanging_tx:
            for tx in txs:
                self.control(dc).tx_kill(xid=tx.xid)

            txs = self.control(dc).tx()

        assert not isinstance(txs, list) or len(txs) == 0, f"No active transactions expected [txs={txs}]"

    @staticmethod
    def result_int(svc: IgniteApplicationService, name: str) -> int:
        """
        :return: Application-recorded integer result.
        """
        return int(svc.extract_result(name))

    @staticmethod
    def result_float(svc: IgniteApplicationService, name: str) -> float:
        """
        :return: Application-recorded float result.
        """
        return float(svc.extract_result(name))

    @staticmethod
    def result_bool(svc: IgniteApplicationService, name: str) -> bool:
        """
        :return: Application-recorded boolean result.
        """
        val = svc.extract_result(name).strip().lower()

        return val == "true"


def cross_dc_network(logger, mdc: MdcCluster, delay_ms: Optional[int] = None,
                     loss: Optional[float] = None) -> NetworkGroupManager:
    """
    Builds a :class:`NetworkGroupManager` (context manager) for the cluster, applying the
    same impairment to every DC pair. With no impairments the manager still owns partition
    enable/disable and the final network cleanup.

    A cluster whose links are not all alike needs no fixture support: build the
    :class:`NetworkGroupStore` and construct the manager directly, the registry is all it
    takes from here - ``NetworkGroupManager(logger, store, mdc.network_registry())``.

    :param delay_ms: One-way cross-DC latency in milliseconds (the effective RTT is twice
           that, since netem delay is applied on egress in both directions).
    :param loss: Cross-DC packet loss fraction in [0.0, 1.0].
    """
    cfg = CrossNetworkGroupConfiguration(delay=f"{delay_ms}ms" if delay_ms is not None else None, loss=loss)

    store = NetworkGroupStore()

    if not cfg.is_empty:
        for dc_a, dc_b in all_pairs(mdc.dcs):
            store.set_config(dc_a, dc_b, cfg)

    return NetworkGroupManager(logger, store, mdc.network_registry())


def assert_cross_dc_distribution_by_attribute(distribution, dc_attr, expected_dcs, owning_only=True,
                                              copies_per_dc=None):
    """
    Asserts that every partition of every cache group has at least one copy in every DC,
    using a node attribute (requested via --user-attributes) as the DC marker.

    :param distribution: CacheDistribution returned by ControlUtility.cache_distribution(),
                         requested with user_attributes=[dc_attr].
    :param dc_attr: Attribute name holding the DC id, e.g. "IGNITE_DATA_CENTER_ID".
    :param expected_dcs: Collection of DC ids that must own a copy of every partition.
    :param owning_only: Count only copies in OWNING state as present.
    :param copies_per_dc: If set, each DC must hold exactly this many copies of every
                          partition - the MdcAffinityBackupFilter guarantee
                          ``(backups + 1) / dcsNum``.
    """
    def dc_of(copy):
        return copy.user_attributes.get(dc_attr)

    _assert_spread(distribution, set(expected_dcs), dc_of, owning_only, copies_per_dc, label="DC",
                   layout_hint=f"DC attribute: {dc_attr}, expected DCs: {sorted(expected_dcs)}")


def assert_distribution_by_attributes(distribution, attrs, expected_values, owning_only=True,
                                      copies_per_value=None):
    """
    The same check one level finer: every partition must have a copy in every group, where a
    group is the TUPLE of values of several node attributes - which is exactly what
    ``ClusterNodeAttributeAffinityBackupFilter`` spreads copies over.

    :param distribution: CacheDistribution requested with user_attributes=attrs.
    :param attrs: Attribute names forming the group key, e.g.
                  ["IGNITE_DATA_CENTER_ID", "AVAILABILITY_ZONE"].
    :param expected_values: Collection of value tuples that must each own a copy of every
                            partition, e.g. [("DC1", "AZ1"), ("DC1", "AZ2"), ...].
    :param owning_only: Count only copies in OWNING state as present.
    :param copies_per_value: If set, each group must hold exactly this many copies.
    """
    expected = {tuple(value) for value in expected_values}

    def group_of(copy):
        return tuple(copy.user_attributes.get(attr) for attr in attrs)

    _assert_spread(distribution, expected, group_of, owning_only, copies_per_value, label="group",
                   layout_hint=f"attributes: {list(attrs)}, expected groups: {sorted(expected)}")


def assert_partitions_colocated_by_attribute(distribution, attr, expected_values=None):
    """
    Asserts the opposite shape of guarantee: every partition keeps ALL of its OWNING copies
    on nodes carrying ONE value of the attribute - the cell that
    ``ClusterNodeAttributeColocatedBackupFilter`` builds.

    Colocation alone says nothing about data centers: whether a cell survives the loss of one
    is decided by how the cell is laid out over them, which is what
    :meth:`MdcCluster.group_dcs` reports.

    :param distribution: CacheDistribution requested with user_attributes=[attr].
    :param attr: Attribute name holding the cell id, e.g. "CELL".
    :param expected_values: If set, every partition's cell must be one of these.
    :return: Cell of every partition, as ``{group name: {partition: cell}}``.
    """
    cells = {}
    violations = []

    for group in distribution.groups.values():
        cells[group.name] = {}

        for part, copies in sorted(group.partitions.items()):
            owners = [c for c in copies if c.state == "OWNING"]

            values = {c.user_attributes.get(attr) for c in owners}

            cells[group.name][part] = next(iter(values)) if len(values) == 1 else None

            problems = []

            if len(values) > 1:
                problems.append(f"copies span cells {sorted(values)}")

            if expected_values is not None and not values <= set(expected_values):
                problems.append(f"unexpected cells {sorted(values - set(expected_values))}")

            if problems:
                copies_dump = ", ".join(
                    f"{c.node_id}({'P' if c.primary else 'B'},{c.state},"
                    f"{attr}={c.user_attributes.get(attr)},{c.node_addresses})"
                    for c in copies)

                violations.append(f"group={group.name}(id={group.group_id}), partition={part}, "
                                  f"{', '.join(problems)}, copies=[{copies_dump}]")

    assert not violations, \
        f"Partition copies are not colocated by {attr}:\n  " + "\n  ".join(violations)

    return cells


def _assert_spread(distribution, expected_groups, group_of, owning_only, copies_per_group, label,
                   layout_hint):
    """
    Asserts that every partition of every cache group has a copy in every expected group,
    where a group is whatever ``group_of(copy)`` returns - a DC id, or a tuple of several
    node attribute values.
    """
    violations = []

    for group in distribution.groups.values():
        for part, copies in sorted(group.partitions.items()):
            counted = [c for c in copies if not owning_only or c.state == "OWNING"]

            per_group = {key: 0 for key in expected_groups}

            for copy in counted:
                key = group_of(copy)

                if key in per_group:
                    per_group[key] += 1

            missing = {key for key, cnt in per_group.items() if cnt == 0}

            unbalanced = {} if copies_per_group is None else \
                {key: cnt for key, cnt in per_group.items() if cnt != copies_per_group}

            if missing or unbalanced:
                copies_dump = ", ".join(
                    f"{c.node_id}({'P' if c.primary else 'B'},{c.state},{label}={group_of(c)},"
                    f"{c.node_addresses})"
                    for c in copies)

                problems = []

                if missing:
                    problems.append(f"missing {label}s={sorted(missing)}")

                if unbalanced:
                    problems.append(f"copies per {label} != {copies_per_group}: {unbalanced}")

                violations.append(f"group={group.name}(id={group.group_id}), partition={part}, "
                                  f"{', '.join(problems)}, copies=[{copies_dump}]")

    assert not violations, \
        f"Partition distribution does not cover every {label}:\n  " + "\n  ".join(violations) + \
        "\n" + layout_hint
