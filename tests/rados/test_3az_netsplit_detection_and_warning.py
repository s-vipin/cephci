"""
This test module is used to test revert stretch mode
includes:
Scenario 1 -> [3 AZ] Netsplit between 1 mon of DC1 and 1 mon of DC2
Scenario 2 -> [3 AZ] Netsplit between mon1 of DC1 and mon2 of DC2. mon1 of DC1 and mon3 of DC3
Scenario 3 -> [3 AZ] Netsplit between all mon of DC1 and 1 mon of DC2
Scenario 4 -> [3 AZ] Netsplit between mons of same Datacenter
Scenario 5 -> [3 AZ] Netsplit between all mons of DC1 and all mons of DC2
"""

import random
from collections import namedtuple

from ceph.ceph_admin import CephAdmin
from ceph.rados.core_workflows import RadosOrchestrator
from tests.rados.stretch_cluster import wait_for_clean_pg_sets
from tests.rados.test_netsplit_detection_and_warning import (
    check_individual_netsplit_warning,
    check_location_netsplit_warning,
)
from tests.rados.test_stretch_revert_class import (
    flush_ip_table_rules_on_all_hosts,
    simulate_netsplit_between_hosts,
)
from utility.log import Log
from tests.rados.monitor_configurations import MonConfigMethods

log = Log(__name__)

Hosts = namedtuple("Hosts", ["dc_1_hosts", "dc_2_hosts", "tiebreaker_hosts"])


def run(ceph_cluster, **kw):
    """
    This test module is used to test netsplit warnings
    includes:
    """

    log.info(run.__doc__)
    config = kw.get("config")
    cephadm = CephAdmin(cluster=ceph_cluster, **config)
    rados_obj = RadosOrchestrator(node=cephadm)
    pool_name = config.get("pool_name", "test_stretch_io")
    stretch_bucket = config.get("stretch_bucket", "datacenter")
    mon_obj = MonConfigMethods(rados_obj=rados_obj)
    set_debug = config.get("set_debug", False)
    scenarios_to_run = config.get(
        "scenarios_to_run",
        [
            "scenario1",
            "scenario2",
            "scenario3",
            "scenario4",
            "scenario5",
        ],
    )

    try:

        if set_debug:
            log.debug(
                "Setting up debug configs on the cluster for mon, osd & Mgr daemons"
            )
            mon_obj.set_config(section="osd", name="debug_osd", value="20/20")
            mon_obj.set_config(section="mon", name="debug_mon", value="30/30")
            mon_obj.set_config(section="mgr", name="debug_mgr", value="20/20")

        # 3 AZ stretch cluster - Netsplit detection and warning
        osd_tree_cmd = "ceph osd tree"
        buckets = rados_obj.run_ceph_command(osd_tree_cmd)
        dc_buckets = [d for d in buckets["nodes"] if d.get("type") == stretch_bucket]
        dc_names = [name["name"] for name in dc_buckets]
        dc_1_name = dc_names[0]
        dc_2_name = dc_names[1]
        dc_3_name = dc_names[2]

        all_hosts = rados_obj.get_multi_az_stretch_site_hosts(
            num_data_sites=len(dc_names), stretch_bucket=stretch_bucket
        )

        for site in dc_names:
            log.debug(
                f"Hosts present in Datacenter : {site} : {getattr(all_hosts, site)}"
            )

        dc_1_mon_hosts = list()
        for host in getattr(all_hosts, dc_1_name):
            if "mon" in rados_obj.get_host_label(host_name=host):
                dc_1_mon_hosts.append(host)

        dc_2_mon_hosts = list()
        for host in getattr(all_hosts, dc_2_name):
            if "mon" in rados_obj.get_host_label(host_name=host):
                dc_2_mon_hosts.append(host)

        dc_3_mon_hosts = list()
        for host in getattr(all_hosts, dc_3_name):
            if "mon" in rados_obj.get_host_label(host_name=host):
                dc_3_mon_hosts.append(host)

        if not rados_obj.create_n_az_stretch_pool(
            pool_name=pool_name,
            rule_name="3az_rule",
            rule_id=102,
            peer_bucket_barrier=stretch_bucket,
            num_sites=3,
            num_copies_per_site=2,
            total_buckets=3,
            req_peering_buckets=2,
        ):
            log.error(f"Unable to Create/Enable stretch mode on the pool : {pool_name}")
            raise Exception("Unable to enable stretch pool")

        if "scenario1" in scenarios_to_run:
            log.info(
                "Scenario 1 -> [3 AZ] Netsplit between 1 mon of site A and 1 mon of site B"
            )
            group_1_hosts = list()
            group_2_hosts = list()

            random_host = random.choice(dc_1_mon_hosts)
            group_1_hosts.append(random_host)

            random_host = random.choice(dc_2_mon_hosts)
            group_2_hosts.append(random_host)

            log_debug = (
                f"\nSelected mon hosts are"
                f"\nDC1 -> {group_1_hosts}"
                f"\nDC2 -> {group_2_hosts}"
            )
            log.debug(log_debug)

            log_info_msg = f"Step 1: Simulating netsplit between {group_1_hosts} <-> {group_2_hosts}"
            log.info(log_info_msg)
            simulate_netsplit_between_hosts(rados_obj, group_1_hosts, group_2_hosts)

            log_info_msg = f"Step 2: Validating netsplit warning between {group_1_hosts} <-> {group_2_hosts}"
            log.info(log_info_msg)
            check_individual_netsplit_warning(rados_obj, group_1_hosts, group_2_hosts)

            log_info_msg = f"Step 3: Flushing IP tables rules of {group_1_hosts} and {group_2_hosts}"
            log.info(log_info_msg)
            flush_ip_table_rules_on_all_hosts(rados_obj, group_1_hosts)
            flush_ip_table_rules_on_all_hosts(rados_obj, group_2_hosts)

            log.info("Step 4: Wait for clean PGs")
            wait_for_clean_pg_sets(rados_obj=rados_obj)

        if "scenario2" in scenarios_to_run:
            log.info(
                "Scenario 2 -> [3 AZ] Netsplit between mon1 of DC1 and mon2 of DC2. mon1 of DC1 and mon3 of DC3"
            )
            group_1_hosts = list()
            group_2_hosts = list()
            group_3_hosts = list()

            random_host = random.choice(dc_1_mon_hosts)
            group_1_hosts.append(random_host)

            random_host = random.choice(dc_2_mon_hosts)
            group_2_hosts.append(random_host)

            random_host = random.choice(dc_3_mon_hosts)
            group_3_hosts.append(random_host)

            log_debug = (
                f"\nSelected mon hosts are"
                f"\nDC1 -> {group_1_hosts}"
                f"\nDC2 -> {group_2_hosts}"
                f"\nDC3 -> {group_3_hosts}"
            )
            log.debug(log_debug)

            log_info_msg = f"Step 1: Simulating netsplit between {group_1_hosts} <-> {group_2_hosts}"
            log.info(log_info_msg)
            simulate_netsplit_between_hosts(rados_obj, group_1_hosts, group_2_hosts)

            log_info_msg = f"Step 2: Simulating netsplit between {group_1_hosts} <-> {group_3_hosts}"
            log.info(log_info_msg)
            simulate_netsplit_between_hosts(rados_obj, group_1_hosts, group_3_hosts)

            log_info_msg = f"Step 3: Validating netsplit warning between {group_1_hosts} <-> {group_2_hosts}"
            log.info(log_info_msg)
            check_individual_netsplit_warning(
                rados_obj, group_1_hosts, group_2_hosts, expected_warning_count=2
            )

            log_info_msg = f"Step 4: Validating netsplit warning between {group_1_hosts} <-> {group_3_hosts}"
            log.info(log_info_msg)
            check_individual_netsplit_warning(
                rados_obj, group_1_hosts, group_3_hosts, expected_warning_count=2
            )

            log_info_msg = f"Step 3: Flushing IP tables rules of {group_1_hosts} ,{group_2_hosts} and {group_3_hosts}"
            log.info(log_info_msg)
            flush_ip_table_rules_on_all_hosts(rados_obj, group_1_hosts)
            flush_ip_table_rules_on_all_hosts(rados_obj, group_2_hosts)
            flush_ip_table_rules_on_all_hosts(rados_obj, group_3_hosts)

            log.info("Step 4: Wait for clean PGs")
            wait_for_clean_pg_sets(rados_obj=rados_obj)

        if "scenario3" in scenarios_to_run:
            log.info(
                "Scenario 3 -> [ 3 AZ ] Netsplit between all mon of DC1 and 1 mon of DC2"
            )
            group_1_hosts = dc_1_mon_hosts
            group_2_hosts = list()

            random_host = random.choice(dc_2_mon_hosts)
            group_2_hosts.append(random_host)

            log_debug = (
                f"\nSelected mon hosts are"
                f"\nDC1 -> {group_1_hosts}"
                f"\nDC2 -> {group_2_hosts}"
            )
            log.debug(log_debug)

            # Simulating netsplit between MON1 in DC1 and MON2 in DC2
            log_info_msg = f"Step 1: Simulating netsplit between {group_1_hosts} <-> {group_2_hosts}"
            log.info(log_info_msg)
            simulate_netsplit_between_hosts(rados_obj, group_1_hosts, group_2_hosts)

            # Validate the netsplit is detected and warning has been generated
            log_info_msg = f"Step 2: Validating netsplit warning between {group_1_hosts} <-> {group_2_hosts}"
            log.info(log_info_msg)
            check_individual_netsplit_warning(rados_obj, group_1_hosts, group_2_hosts)

            # Flush all the IP tables rules added for simulating netsplit
            log_info_msg = f"Step 3: Flushing IP tables rules of {group_1_hosts} and {group_2_hosts}"
            log.info(log_info_msg)
            flush_ip_table_rules_on_all_hosts(rados_obj, group_1_hosts)
            flush_ip_table_rules_on_all_hosts(rados_obj, group_2_hosts)

            log.info("Step 4: Wait for clean PGs")
            wait_for_clean_pg_sets(rados_obj=rados_obj)

        if "scenario4" in scenarios_to_run:
            log.info("Scenario 4 -> [3 AZ] Netsplit between mons of same Datacenter")
            group_1_hosts = list()
            group_2_hosts = list()

            random_host = random.choice(dc_1_mon_hosts)
            group_1_hosts.append(random_host)

            # selecting hosts which are not in group1
            random_host = random.choice(list(set(dc_1_mon_hosts) - set(group_1_hosts)))
            group_2_hosts.append(random_host)

            log_debug = (
                f"\nSelected mon hosts are"
                f"\nDC1 -> {group_1_hosts}"
                f"\nDC2 -> {group_2_hosts}"
            )
            log.debug(log_debug)

            log_info_msg = (
                f"Step 1: Simulate netsplit between {group_1_hosts} <-> {group_2_hosts}"
            )
            log.info(log_info_msg)
            simulate_netsplit_between_hosts(rados_obj, group_1_hosts, group_2_hosts)

            log_info_msg = f"Step 2: Validate netsplit warning between {group_1_hosts} <-> {group_2_hosts}"
            log.info(log_info_msg)
            check_individual_netsplit_warning(rados_obj, group_1_hosts, group_2_hosts)

            log_info_msg = (
                f"Step 3: Flush IPTable rules of {group_1_hosts} and {group_2_hosts}"
            )
            log.info(log_info_msg)
            flush_ip_table_rules_on_all_hosts(rados_obj, group_1_hosts)
            flush_ip_table_rules_on_all_hosts(rados_obj, group_2_hosts)

            log.info("Step 4: Wait for clean PGs")
            wait_for_clean_pg_sets(rados_obj=rados_obj)

        if "scenario5" in scenarios_to_run:
            log.info(
                "Scenario 5 -> [3 AZ] Netsplit between all mons of DC1 and all mons of DC2"
            )
            group_1_hosts = dc_1_mon_hosts
            group_2_hosts = dc_2_mon_hosts

            log_debug = (
                f"\nSelected mon hosts are"
                f"\nDC1 -> {group_1_hosts}"
                f"\nDC2 -> {group_2_hosts}"
            )
            log.debug(log_debug)

            log_info_msg = (
                f"Step 1: Simulate netsplit between {group_1_hosts} <-> {group_2_hosts}"
            )
            log.info(log_info_msg)
            simulate_netsplit_between_hosts(rados_obj, group_1_hosts, group_2_hosts)

            log_info_msg = (
                f"Step 2: Validate location level netsplit warning between "
                f"{group_1_hosts} <-> {group_2_hosts}"
            )
            log.info(log_info_msg)
            check_location_netsplit_warning(rados_obj, dc_1_name, dc_2_name)

            log_info_msg = (
                f"Step 3: Flush IPTable rules of {group_1_hosts} and {group_2_hosts}"
            )
            log.info(log_info_msg)
            flush_ip_table_rules_on_all_hosts(rados_obj, group_1_hosts)
            flush_ip_table_rules_on_all_hosts(rados_obj, group_2_hosts)

            log_info_msg = "Step 4: Wait for clean PGs"
            log.info(log_info_msg)
            wait_for_clean_pg_sets(rados_obj=rados_obj)

    except Exception as e:
        log.error(f"Failed with exception: {e.__doc__}")
        log.exception(e)
        # log cluster health
        rados_obj.log_cluster_health()
        return 1

    finally:
        log.info(
            "\n \n ************** Execution of finally block begins here *************** \n \n"
        )

        if config.get("delete_pool"):
            rados_obj.delete_pool(pool=pool_name)

        if set_debug:
            log.debug("Removing debug configs on the cluster for mon, osd & Mgr")
            mon_obj.remove_config(section="osd", name="debug_osd")
            mon_obj.remove_config(section="mon", name="debug_mon")
            mon_obj.remove_config(section="mgr", name="debug_mgr")

        # log cluster health
        rados_obj.log_cluster_health()

        # check for crashes after test execution
        if rados_obj.check_crash_status():
            log.error("Test failed due to crash at the end of test")
            return 1

    log.info("All the tests completed on the cluster, Pass!!!")
    return 0
