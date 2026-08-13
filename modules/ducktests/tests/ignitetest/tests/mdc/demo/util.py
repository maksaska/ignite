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
Helpers shared by the MDC demonstration scenarios.
"""
from typing import List, Sequence

from ignitetest.services.mdc.mdc_cluster import DATA_CENTER_ATTR


class CommandOutput:
    """
    The output of a control.sh command, rendered as a demo breakpoint banner section - so
    that what an operator would be reading is on screen while the scenario is held.

    See :meth:`ignitetest.utils.ignite_test.IgniteTest.pause`.
    """
    def __init__(self, title: str, output: str):
        self.title = title
        self.output = output

    def describe(self) -> List[str]:
        """
        :return: Section lines, the first one being the section title.
        """
        return [self.title] + [f"  {line}" for line in self.output.splitlines() if line.strip()]


def show(test, title: str, output: str) -> CommandOutput:
    """
    Logs the output of a control.sh command and wraps it for a demo breakpoint banner, so the
    same text lands both in the test log and on the banner of the breakpoint it is passed to.

    :param test: The test, for its logger.
    :param title: Section title, e.g. "MDC TOPOLOGY".
    :param output: Raw command output.
    """
    test.logger.info(f"{title}\n{output}")

    return CommandOutput(title, output)


def partitions_missing_a_dc(distribution, dcs: Sequence[str]) -> List[str]:
    """
    Finds the partitions that break the "one copy of every partition in every DC" guarantee.
    The inverse of what
    :func:`ignitetest.services.mdc.mdc_cluster.assert_cross_dc_distribution_by_attribute`
    enforces, for a demo that has to show the guarantee being LOST.

    :param distribution: CacheDistribution requested with user_attributes=[DATA_CENTER_ATTR].
    :param dcs: DCs every partition is expected to have an OWNING copy in.
    :return: One entry per offending partition, naming the DCs it has no copy in.
    """
    missing = []

    for group in distribution.groups.values():
        for part, copies in sorted(group.partitions.items()):
            owning_dcs = {copy.user_attributes.get(DATA_CENTER_ATTR)
                          for copy in copies if copy.state == "OWNING"}

            absent = sorted(set(dcs) - owning_dcs)

            if absent:
                missing.append(f"{group.name}#{part} has no copy in {absent}")

    return missing
