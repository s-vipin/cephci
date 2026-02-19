import random
import string
import time
from dataclasses import dataclass
from typing import List

from ceph.ceph import Ceph, CephNode, CommandFailed
from ceph.ceph_admin import CephAdmin
from ceph.rados.core_workflows import RadosOrchestrator
from ceph.rados.crushtool_workflows import CrushToolWorkflows
from ceph.rados.osd_ok_to_upgrade_utils import (
    OsdOkToUpgradeCommand,
    OsdOkToUpgradeCommandOutput,
    execute_negative_scenario,
)
from ceph.rados.utils import get_cluster_timestamp
from utility.log import Log

log = Log(__name__)


@dataclass
class OkToUpgradeTestConfig:
    """Holds cluster and test context for ok-to-upgrade scenarios."""

    rados_obj: RadosOrchestrator
    ceph_cluster: Ceph
    ceph_nodes: CephNode
    crushtool: CrushToolWorkflows
    cephadm: CephAdmin
    client_node: CephNode
    number_of_racks: int = 4
    number_of_chassis_per_rack: int = 2


class InvalidScenarioException(Exception):
    """Raised when config.scenarios contains an invalid or unsupported scenario name."""

    pass


def run(ceph_cluster, **kw):
    """
    Test ceph osd ok-to-upgrade command.
    negative scenarios:
    - max value < 0
    - crush bucket type must be rack/chassis/host/osd, not root
    - non-existent crush bucket
    - invalid Ceph version format
    - missing required ceph_version parameter
    - crush bucket with no OSDs (has no children)
    Polarion: CEPH-83632279
    """
    log.info(run.__doc__)
    config = kw["config"]
    cephadm = CephAdmin(cluster=ceph_cluster, **config)
    rados_obj = RadosOrchestrator(node=cephadm)
    client_node = ceph_cluster.get_nodes(role="client")[0]
    start_time = get_cluster_timestamp(rados_obj.node)
    ceph_nodes = kw.get("ceph_nodes")
    scenarios = config.get("scenarios", [])
    log.debug(f"Test workflow started. Start time: {start_time}")

    crushtool = CrushToolWorkflows(node=cephadm)
    number_of_racks = 4
    number_of_chassis_per_rack = 2

    try:
        if len(scenarios) == 0:
            log.error("config.scenarios cannot be empty")
            return 1

        config = OkToUpgradeTestConfig(
            rados_obj=rados_obj,
            ceph_cluster=ceph_cluster,
            ceph_nodes=ceph_nodes,
            crushtool=crushtool,
            cephadm=cephadm,
            client_node=client_node,
            number_of_racks=number_of_racks,
            number_of_chassis_per_rack=number_of_chassis_per_rack,
        )

        ok_to_upgrade_object: OkToUpgradeScenarios = OkToUpgradeScenarios(config)

        if "rack_failure_domain_test" in scenarios:
            ok_to_upgrade_object.setup_phase(failure_domain="rack")
            Scenario1(config).execute()
            Scenario2(config).execute()
            # Bug to be raised
            # Scenario3(config).execute()
            ok_to_upgrade_object.cleanup()

        elif "negative" in scenarios:
            log.info("Running negative scenarios for ceph osd ok-to-upgrade")

            # max value < 0
            log.info("Negative scenario: max value must be non-negative")
            in_osds = rados_obj.get_osd_list(status="in")
            osd_id = in_osds[0]
            execute_negative_scenario(
                command=OsdOkToUpgradeCommand(
                    f"osd.{osd_id}", "20.3.0-3803-g63ca1ffb5a21", rados_obj=rados_obj
                ).add_max(-100),
                expected_err_substring="'max' must be non-negative",
            )
            log.debug("Passed: max=-100 rejected as expected")

            # crush bucket of type root
            log.info(
                "Negative scenario: crush bucket type must be rack/chassis/host/osd, not root"
            )
            execute_negative_scenario(
                command=OsdOkToUpgradeCommand(
                    "default", "20.3.0-3803-g63ca1ffb5a21", rados_obj=rados_obj
                ),
                expected_err_substring="valid types are: 'rack', 'chassis', 'host' and 'osd'",
            )
            log.debug("Passed: root bucket 'default' rejected as expected")

            # bucket does not exist
            log.info("Negative scenario: non-existent crush bucket")
            execute_negative_scenario(
                command=OsdOkToUpgradeCommand(
                    "test", "20.3.0-3803-g63ca1ffb5a21", rados_obj=rados_obj
                ),
                expected_err_substring="does not exist",
            )
            log.debug("Passed: non-existent bucket rejected as expected")

            # garbage value for ceph version
            log.info("Negative scenario: invalid Ceph version format")
            execute_negative_scenario(
                command=OsdOkToUpgradeCommand("test", "garbage", rados_obj=rados_obj),
                expected_err_substring="Invalid Ceph version (short) format",
            )
            log.debug("Passed: garbage version rejected as expected")

            # empty version string
            log.info("Negative scenario: missing required ceph_version parameter")
            execute_negative_scenario(
                command=OsdOkToUpgradeCommand("test", "", rados_obj=rados_obj),
                expected_err_substring="missing required parameter ceph_version",
            )
            log.debug("Passed: empty version rejected as expected")

            # no osd in crush bucket
            log.info("Negative scenario: crush bucket with no OSDs (has no children)")
            rados_obj.run_ceph_command(cmd="ceph osd crush add-bucket rack11 rack")
            execute_negative_scenario(
                command=OsdOkToUpgradeCommand(
                    "rack11", "20.3.0-3803-g63ca1ffb5a21", rados_obj=rados_obj
                ),
                expected_err_substring="has no children",
            )
            rados_obj.run_ceph_command(cmd="ceph osd crush rm rack11")
            log.debug("Passed: empty bucket rejected and rack11 cleaned up")

            log.info("All negative scenarios completed successfully")
        else:
            raise InvalidScenarioException(
                f"config.scenarios must be one of ['rack_failure_domain_test', 'negative']"
                f" Scenarios passed - {scenarios}"
            )

    except Exception as e:
        log.error(f"Execution failed with exception: {e.__doc__}")
        log.exception(e)
        rados_obj.log_cluster_health()
        return 1
    finally:
        log.info(
            "\n \n ************** Execution of finally block begins here *************** \n \n"
        )
        if ok_to_upgrade_object in globals() or ok_to_upgrade_object in locals():
            ok_to_upgrade_object.cleanup()
        rados_obj.log_cluster_health()
        test_end_time = get_cluster_timestamp(rados_obj.node)
        log.debug(
            f"Test workflow completed. Start time: {start_time}, End time: {test_end_time}"
        )
        if rados_obj.check_crash_status(start_time=start_time, end_time=test_end_time):
            log.error("Test failed due to crash at the end of test")
            return 1
        log.info("Test workflow finished; no crashes detected")
    return 0


class OkToUpgradeScenarios:
    """Manages crush hierarchy, pools, and scenarios for testing ceph osd ok-to-upgrade."""

    def __init__(self, config: OkToUpgradeTestConfig):
        self.config: OkToUpgradeTestConfig = config
        self.pool_name: str = (
            "scenario_"
            + self.__class__.__name__
            + "".join(random.choices(string.ascii_letters, k=6))
        )
        self.osd_hosts: List[CephNode] = self.config.ceph_cluster.get_nodes(role="osd")
        self.id_map = {}
        self.children_map = {}

    def __move_crush_items(
        self, items: list, to_crush_bucket_name, to_crush_bucket_type
    ):
        """Move each crush item into the given bucket (e.g. rack or chassis)."""
        for item in items:
            cmd = f"ceph osd crush move {item} {to_crush_bucket_type}={to_crush_bucket_name}"
            self.config.rados_obj.run_ceph_command(
                cmd=cmd, client_exec=True, print_output=True
            )

    def __setup_crush_buckets(
        self,
        source_type: str,
        source_name: str,
        destination_type: str,
        destination_name: str,
    ) -> None:
        """Create a crush bucket and place it under the given destination bucket."""
        self.config.rados_obj.run_ceph_command(
            cmd=f"ceph osd crush add-bucket {source_name} {source_type}"
        )
        self.__move_crush_items([source_name], destination_name, destination_type)

    def __create_crush_hierarchy(self):
        """Build rack/chassis/host crush tree and assign OSDs to chassis."""
        try:
            for index in range(0, self.config.number_of_racks):
                rack = f"rack{index}"
                self.__setup_crush_buckets("rack", rack, "root", "default")

            for index in range(
                0, self.config.number_of_chassis_per_rack * self.config.number_of_racks
            ):
                chassis = f"chassis{index}"
                rack = f"rack{index %self.config.number_of_racks}"
                self.__setup_crush_buckets("chassis", chassis, "rack", rack)

            self.osd_hostnames = list()
            for index, host in enumerate(self.osd_hosts):
                self.osd_hostnames.append(host.hostname)
                chassis = f"chassis{index % (self.config.number_of_chassis_per_rack * self.config.number_of_racks)}"
                self.__move_crush_items([host.hostname], chassis, "chassis")

        except CommandFailed as e:
            log.error("Crush hierachy creation failed " + str(e))
            return False

        return True

    def setup_phase(self, failure_domain="rack"):
        """Create crush rule, pool, write data, and wait for PGs to be clean."""
        self.crush_rule = CrushRule(
            crush_rule_name=f"{failure_domain}_failure_domain_"
            + "".join(random.choices(string.ascii_letters, k=6)),
            failure_domain=failure_domain,
            crushtool=self.config.crushtool,
        )
        self.crush_rule.create()
        self.config.rados_obj.create_pool(
            self.pool_name,
            pg_num=128,
            pg_num_min=128,
            crush_rule=self.crush_rule.crush_rule_name,
        ), "Failed to create pool"
        # self.config.rados_obj.bench_write(self.pool_name), "Failed to fill the"
        self.config.rados_obj.wait_for_clean_pg_sets()

    def cleanup(self):
        """Restore crush layout (hosts under default), remove racks/chassis, rule and pool."""
        __osd_hostnames = list()
        for index, host in enumerate(self.osd_hosts):
            __osd_hostnames.append(host.hostname)

        self.__move_crush_items(__osd_hostnames, "root", "default")

        for index in range(self.config.number_of_racks):
            rack = f"rack{index}"
            self.config.rados_obj.run_ceph_command(cmd=f"ceph osd crush rm {rack}")

        for index in range(self.config.number_of_chassis_per_rack):
            chassis = f"chassis{index}"
            self.config.rados_obj.run_ceph_command(cmd=f"ceph osd crush rm {chassis}")

        self.crush_rule.remove()
        self.config.rados_obj.delete_pool(self.pool_name)


def log_scenario(func):
    """Decorator that logs scenario start/end and elapsed time."""

    def wrapper(self):
        __start_time = time.time()
        log.info(
            f" ================= Starting scenario {self.__class__.__name__} - {func.__name__} ==================== "
        )
        func(self)
        __end_time = time.time()
        __total_time = __end_time - __start_time
        log.info(
            f" ===== End of scenario {self.__class__.__name__} - {func.__name__} - total time {__total_time}s ==== "
        )

    return wrapper


class Scenario1(OkToUpgradeScenarios):
    """Single OSD: run ok-to-upgrade on one OSD and assert expected output."""

    @log_scenario
    def execute(self):
        all_osds_in_host = self.config.rados_obj.collect_osd_daemon_ids(
            self.osd_hosts[0]
        )
        osd_id = all_osds_in_host[0]

        actual = OsdOkToUpgradeCommand(
            f"osd.{osd_id}",
            "20.3.0-3803-g63ca1ffb5a21",
            rados_obj=self.config.rados_obj,
        ).execute()
        expected = OsdOkToUpgradeCommandOutput(
            ok_to_upgrade=True,
            all_osds_upgraded=False,
            osds_in_crush_bucket=[osd_id],
            osds_ok_to_upgrade=[osd_id],
            osds_upgraded=[],
            bad_no_version=[],
        )
        assert expected == actual


class Scenario2(OkToUpgradeScenarios):
    """Host-level: run ok-to-upgrade on a host and assert all its OSDs are ok-to-upgrade."""

    @log_scenario
    def execute(self):
        all_osds_in_host = self.config.rados_obj.collect_osd_daemon_ids(
            self.osd_hosts[0]
        )
        actual = OsdOkToUpgradeCommand(
            self.osd_hosts[0].hostname,
            "20.3.0-3803-g63ca1ffb5a21",
            rados_obj=self.config.rados_obj,
        ).execute()
        expected = OsdOkToUpgradeCommandOutput(
            ok_to_upgrade=True,
            all_osds_upgraded=False,
            osds_in_crush_bucket=all_osds_in_host,
            osds_ok_to_upgrade=all_osds_in_host,
            osds_upgraded=[],
            bad_no_version=[],
        )
        assert expected == actual


class Scenario3(OkToUpgradeScenarios):
    """Rack-level: run ok-to-upgrade on a rack and assert all OSDs in that rack."""

    @log_scenario
    def execute(self):
        all_osds_of_rack: List[int] = self.config.rados_obj.collect_osd_daemon_ids(
            "rack1"
        )
        actual = OsdOkToUpgradeCommand(
            "rack1", "20.3.0-3803-g63ca1ffb5a21", rados_obj=self.config.rados_obj
        ).execute()
        expected = OsdOkToUpgradeCommandOutput(
            ok_to_upgrade=True,
            all_osds_upgraded=False,
            osds_in_crush_bucket=all_osds_of_rack,
            osds_ok_to_upgrade=all_osds_of_rack,
            osds_upgraded=[],
            bad_no_version=[],
        )
        assert expected == actual


@dataclass
class CrushRule:
    """Represents a CRUSH rule (e.g. chooseleaf by rack/chassis) for test pools."""

    crush_rule_name: str
    failure_domain: str
    crushtool: CrushToolWorkflows
    source_bin: str = "/etc/ceph"
    target_bin: str = "/etc/ceph"
    type: str = "replicated"
    backup_original_crush_map_txt: str = None

    def create(self) -> None:
        """Add this rule to the crush map (backup of original map is kept for remove())."""
        device_rules = f"""
         id {str(random.randint(10, 100))}
         type {self.type}
         step take default
         step chooseleaf firstn 0 type {self.failure_domain}
         step emit"""

        res, original_crush_map_bin = self.crushtool.generate_crush_map_bin(
            self.source_bin
        )
        if res is False:
            raise Exception("failed to generate crushmap bin")

        res, self.backup_original_crush_map_txt = (
            self.crushtool.decompile_crush_map_txt(
                source_loc=original_crush_map_bin, target_loc=self.target_bin
            )
        )
        if res is False:
            raise Exception("failed to extract crushmap text from bin")

        if (
            self.crushtool.add_crush_rule(
                rule_name=self.crush_rule_name, rules=device_rules
            )
            is False
        ):
            raise Exception("Failed to add crush rule")

        log.info("Crush rule added successfully..." + device_rules)

    def remove(self):
        """Restore crush map to state before create() by applying the backup."""
        if self.backup_original_crush_map_txt:
            raise ValueError(
                "CrushRule.create() needs to be executed inorder to update crush map"
            )
        res, bin = self.crushtool.compile_crush_map_txt(
            source_loc=self.backup_original_crush_map_txt
        )
        assert self.crushtool.set_crush_map_bin(bin)
