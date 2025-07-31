"""
This test module is used to test revert stretch mode
includes:
1. Netsplit in stretch mode within same crush bucket
2. Netsplit in stretch mode across crush bucket
3. Netsplit in detection and warning in stretch mode across crush bucket between more than 1 MON
4. Netsplit between DC1 mons and tiebreaker
"""

import time
from collections import namedtuple
import random

from ceph.ceph_admin import CephAdmin
from ceph.rados.core_workflows import RadosOrchestrator
from ceph.rados.pool_workflows import PoolFunctions
from ceph.rados.serviceability_workflows import ServiceabilityMethods
from ceph.utils import find_vm_node_by_hostname
from tests.rados.stretch_cluster import wait_for_clean_pg_sets
from tests.rados.test_stretch_revert_class import RevertStretchModeFunctionalities, StretchMode
from tests.rados.test_stretch_site_down import stretch_enabled_checks
from utility.log import Log
from utility.retry import retry
from utility.utils import generate_unique_id

log = Log(__name__)

Hosts = namedtuple("Hosts", ["dc_1_hosts", "dc_2_hosts", "tiebreaker_hosts"])


def run(ceph_cluster, **kw):
    """
        This test module is used to test netsplit warnings
        includes:
        1. Netsplit in stretch mode within same crush bucket
        2. Netsplit in stretch mode across crush bucket
        3. Netsplit in stretch mode across crush bucket between more than 1 MON
    """

    log.info(run.__doc__)
    config = kw.get("config")
    cephadm = CephAdmin(cluster=ceph_cluster, **config)
    pool_obj = PoolFunctions(node=cephadm)
    service_obj = ServiceabilityMethods(cluster=ceph_cluster, **config)
    rhbuild = config.get("rhbuild")
    rados_obj = RadosOrchestrator(node=cephadm)
    pool_name = config.get("pool_name", "test_stretch_io")
    stretch_bucket = config.get("stretch_bucket", "datacenter")
    tiebreaker_mon_site_name = config.get("tiebreaker_mon_site_name", "tiebreaker")
    add_network_delay = config.get("add_network_delay", False)
    client_node = ceph_cluster.get_nodes(role="client")[0]
    scenarios_to_run = config.get(
        "scenarios_to_run",
        [
            "scenario1",
            "scenario2",
            "scenario3",
            "scenario4",
        ],
    )
    config = {
        "rados_obj": rados_obj,
        "pool_obj": pool_obj,
        "tiebreaker_mon_site_name": tiebreaker_mon_site_name,
        "stretch_bucket": stretch_bucket,
        "client_node": client_node,
    }
    try:

        stretch_mode = StretchMode(**config)
        dc_1_hosts = stretch_mode.site_1_hosts
        dc_2_hosts = stretch_mode.site_2_hosts
        tiebreaker_hosts = stretch_mode.tiebreaker_hosts

        if "scenario1" in scenarios_to_run:
            log.info("Scenario 1 -> Netsplit in stretch mode within same crush bucket")
            group_1_hosts = list()
            group_2_hosts = list()

            if not stretch_enabled_checks(rados_obj):
                log.error(
                    "The cluster has not cleared the pre-checks to run stretch tests. Exiting..."
                )
                raise Exception("Test pre-execution checks failed")

            random_host = random.choice(dc_1_hosts)
            group_1_hosts.append(random_host)

            # selecting hosts which are not in group1
            random_host = random.choice(list(set(dc_1_hosts) - set(group_1_hosts)))
            group_2_hosts.append(random_host)

            # generate netsplit, check warnings and remove the netsplits
            log_info_msg = (f"Group 1 hosts -> {group_1_hosts}"
                            f"Group 2 hosts -> {group_2_hosts}")
            log.info(log_info_msg)
            stretch_mode.simulate_netsplit_between_hosts(group_1_hosts, group_2_hosts)
            check_individual_netsplit_warning(rados_obj, group_1_hosts, group_2_hosts)
            stretch_mode.flush_ip_table_rules_on_all_hosts()
            wait_for_clean_pg_sets(rados_obj=rados_obj)

        if "scenario2" in scenarios_to_run:
            log.info("Scenario 2 -> Netsplit in stretch mode across crush bucket")
            group_1_hosts = list()
            group_2_hosts = list()

            if not stretch_enabled_checks(rados_obj):
                log.error(
                    "The cluster has not cleared the pre-checks to run stretch tests. Exiting..."
                )
                raise Exception("Test pre-execution checks failed")

            random_host = random.choice(dc_1_hosts)
            group_1_hosts.append(random_host)

            # selecting hosts which are not in group1
            random_host = random.choice(dc_2_hosts)
            group_2_hosts.append(random_host)

            # generate netsplit, check warnings and remove the netsplits
            log_info_msg = (f"Group 1 hosts -> {group_1_hosts}"
                            f"Group 2 hosts -> {group_2_hosts}")
            log.info(log_info_msg)
            stretch_mode.simulate_netsplit_between_hosts(group_1_hosts, group_2_hosts)
            check_individual_netsplit_warning(rados_obj, group_1_hosts, group_2_hosts)
            stretch_mode.flush_ip_table_rules_on_all_hosts()
            wait_for_clean_pg_sets(rados_obj=rados_obj)

        if "scenario3" in scenarios_to_run:
            log.info("Scenario 3 -> Netsplit in stretch mode across crush bucket between more than 1 MON")
            group_1_hosts = list()
            group_2_hosts = list()

            if not stretch_enabled_checks(rados_obj):
                log.error(
                    "The cluster has not cleared the pre-checks to run stretch tests. Exiting..."
                )
                raise Exception("Test pre-execution checks failed")

            random_host = random.choice(dc_1_hosts)
            group_1_hosts.append(random_host)

            # selecting more than 1 host from DC2
            for _ in range(2):
                random_host = random.choice(list(set(dc_2_hosts)-set(group_2_hosts)))
                group_2_hosts.append(random_host)

            # generate netsplit, check warnings and remove the netsplits
            log_info_msg = (f"Group 1 hosts -> {group_1_hosts}"
                            f"Group 2 hosts -> {group_2_hosts}")
            log.info(log_info_msg)
            stretch_mode.simulate_netsplit_between_hosts(group_1_hosts, group_2_hosts)
            check_individual_netsplit_warning(rados_obj, group_1_hosts, group_2_hosts)
            stretch_mode.flush_ip_table_rules_on_all_hosts()
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

        # log cluster health
        rados_obj.log_cluster_health()

        # check for crashes after test execution
        if rados_obj.check_crash_status():
            log.error("Test failed due to crash at the end of test")
            return 1

    log.info("All the tests completed on the cluster, Pass!!!")
    return 0

@retry((Exception), backoff=1, tries=3, delay=20)
def check_individual_netsplit_warning(rados_obj: RadosOrchestrator, group_1:list, group_2:list):
    # [root @ depressa010 ~]  # ceph health detail -fjson-pretty
    #
    # {
    #     "status": "HEALTH_WARN",
    #     "checks": {
    #         "MON_NETSPLIT": {
    #             "severity": "HEALTH_WARN",
    #             "summary": {
    #                 "message": "2 network partitions detected",
    #                 "count": 2
    #             },
    #             "detail": [
    #                 {
    #                     "message": "Netsplit detected between mon.depressa003 and mon.depressa006"
    #                 },
    #                 {
    #                     "message": "Netsplit detected between mon.depressa003 and mon.depressa007"
    #                 }
    #             ],
    #             "muted": false
    #         },
    #         "SLOW_OPS": {
    #             "severity": "HEALTH_WARN",
    #             "summary": {
    #                 "message": "54 slow ops, oldest one blocked for 271 sec, mon.depressa002 has slow ops",
    #                 "count": 1
    #             },
    #             "detail": [],
    #             "muted": false
    #         }
    #     },
    #     "mutes": []
    # }
    out = rados_obj.run_ceph_command(cmd="ceph health detail", print_output=True)
    checks = out["checks"]
    if "MON_NETSPLIT" not in checks:
        raise Exception("MON_NETSPLIT warning is not generated")

    count = checks["MON_NETSPLIT"]["summary"]["count"]
    total_partitions = len(group_1) + len(group_2)

    if count != total_partitions:
        err_msg = (f"Expected partitions -> {total_partitions}\n"
                   f"Current partitions as per ceph health detail -> {count}")
        log.error(err_msg)
        raise Exception(err_msg)

    detail = checks["MON_NETSPLIT"]["detail"]
    for group_1_mon in group_1:
        for group_2_mon in group_2:
            for message_info in detail:
                if group_1_mon in message_info["message"] and group_2_mon in  message_info["message"]:
                    info_msg = (f"Netsplit warning generated for {group_1_mon} and {group_2_mon}\n"
                                f"Message -> {message_info['message']}")
                    log.info(info_msg)
                    break
            else:
                err_msg = (f"Netsplit warning not generated for {group_1_mon} and {group_2_mon}\n"
                           f"Ceph health detail -> \n {out}")
                log.error(err_msg)
                raise Exception(err_msg)
    log_info = (f"Netsplit detected and warning generated between below set of mons\n"
                f"Group 1 mons -> {group_1}\n"
                f"Group 2 mons -> {group_2}")
    log.info(log_info)



