"""
Test Module to perform specific functionalities of ceph-volume.
 - ceph-volume lvm list [OSD_ID | DEVICE_PATH | VOLUME_GROUP/LOGICAL_VOLUME]
 - ceph-volume lvm zap [--destroy] [--osd-id OSD_ID | --osd-fsid OSD_FSID | DEVICE_PATH ]
 - ceph-volume --help
"""

import json
import random

from ceph.ceph_admin import CephAdmin
from ceph.rados import utils
from ceph.rados.cephvolume_workflows import CephVolumeWorkflows
from ceph.rados.core_workflows import RadosOrchestrator
from cli.utilities.operations import wait_for_osd_daemon_state
from tests.rados.rados_test_util import get_device_path
from utility.log import Log

log = Log(__name__)


def run(ceph_cluster, **kw):
    """
    Test to perform +ve workflows for the ceph-volume utility
    Returns:
        1 -> Fail, 0 -> Pass
    """
    log.info(run.__doc__)
    config = kw["config"]

    cephadm = CephAdmin(cluster=ceph_cluster, **config)
    rados_obj = RadosOrchestrator(node=cephadm)
    volumeobject_obj = CephVolumeWorkflows(node=cephadm)

    try:

        if config.get("zap"):

            log.info(
                "fetching active OSDs and selecting random OSD for 'ceph-volume lvm zap' test\n"
            )

            osd_list = rados_obj.get_active_osd_list()
            osd_id = random.choice(osd_list)

            log.info(
                f"Active OSDs in the cluster: {osd_list}\n"
                f"OSD {osd_id} selected at random for zap test\n"
                f"Proceeding to fetch Host, osd_fsid and device path for OSD {osd_id}\n"
            )

            osd_host = rados_obj.fetch_host_node(daemon_type="osd", daemon_id=osd_id)
            osd_fsid = rados_obj.get_osd_uuid(osd_id)
            dev_path = get_device_path(host=osd_host, osd_id=osd_id)

            log.info(
                f"Successfully fetched Host, osd fsid and device path for OSD {osd_id}\n"
                f"Host: {osd_host.hostname}\n"
                f"OSD fsid: {osd_fsid}\n"
                f"device path: {dev_path}\n"
            )

            # Execute ceph-volume --help
            log.info(
                "\n ---------------------------------"
                "\n Running help for Ceph Volume"
                "\n ---------------------------------"
            )
            out = volumeobject_obj.help(osd_host)
            log.info(out)

            # ceph-volume lvm zap [ --destroy | device path | --osd-id osd id | --osd-fsid osd fsid ]
            log.info(
                f"\n -------------------------------------------"
                f"\n Zapping device on host {osd_host.hostname}"
                f"\n -------------------------------------------"
            )

            log.info("Setting OSD service to unmanaged")
            utils.set_osd_devices_unmanaged(
                ceph_cluster=ceph_cluster, osd_id=osd_id, unmanaged=True
            )

            log.info(
                "Successfully set OSD service to unmanaged\n"
                f"proceeding to remove OSD [id:{osd_id}, fsid:{osd_fsid}"
                f"device path:{dev_path}] on host {osd_host.hostname}"
            )

            utils.osd_remove(ceph_cluster, osd_id=osd_id)

            log.info(
                f"Removed OSD {osd_id} on host {osd_host}"
                f"Proceeding to zap volume on host {osd_host}"
            )

            _ = volumeobject_obj.lvm_zap(
                device=dev_path if config.get("zap").get("device_path") else None,
                destroy=config.get("zap").get("destroy"),
                osd_id=osd_id if config.get("zap").get("osd_id") else None,
                osd_fsid=osd_fsid if config.get("zap").get("osd_fsid") else None,
                host=osd_host,
            )

            log.info(
                f"Zapped device {dev_path} using `ceph-volume lvm zap`\n"
                f"Proceeding to check OSD {osd_id} removed from `ceph-volume lvm list`"
            )

            out = volumeobject_obj.lvm_list(host=osd_host, osd_id=osd_id)

            try:
                ceph_volume_lvm_list = json.loads(out)
                log.info(ceph_volume_lvm_list)
            except json.JSONDecodeError as e:
                log.error(f"Error decoding ceph-volume lvm list command {e}")
                raise

            if len(ceph_volume_lvm_list):
                log.error(
                    f"zapping OSD device failed [ device_path: {dev_path} OSD ID: {osd_id} OSD FSID: {osd_fsid} ]\n"
                    f"`ceph-volume lvm list` output contains OSD device {dev_path}\n"
                    f"`ceph-volume lvm list` output: {ceph_volume_lvm_list}"
                )
                raise

            log.info(
                f"OSD {osd_id} removed from `ceph-volume lvm list` on host {osd_host.hostname}\n"
                f"Proceeding to check if data and file systems are removed from OSD {osd_id}"
            )

            device_reject_reasons = rados_obj.get_device_rejected_reasons(
                node_name=osd_host.hostname, device_path=dev_path
            )
            log.info(f"Execute: rejected reason {device_reject_reasons}")

            if len(device_reject_reasons):
                log.error(
                    f"zapping device {dev_path} failed\n"
                    f"`ceph-volume lvm zap` did not wipe data and filesystem {dev_path}\n"
                    f"ceph orch device ls - reject reason {device_reject_reasons}"
                )
                raise

            log.info(
                f"Data and file system successfully wiped from OSD {osd_id} device {dev_path}\n"
                f"Proceeding to check if LVs/VGs/PVs for OSD {osd_id} on host {osd_host.hostname}"
            )

            out, _ = osd_host.exec_command(
                sudo=True, cmd="pvs -o pv_name --reportformat json"
            )
            out = json.loads(out)
            pvs_names = [pv_detail["pv_name"] for pv_detail in out["report"][0]["pv"]]

            if config.get("zap").get("destroy"):
                log.info(
                    "Destroy flag (--destroy) passed during test execution\n"
                    f"Proceeding to check if LVs/VGs/PVs are removed for OSD {osd_id}"
                    f" ( OSD device path {dev_path} ) on host {osd_host.hostname}"
                )

                if dev_path in pvs_names:
                    log.error(
                        f"LVs/VGs/PVs not cleared for OSD {osd_id} {osd_fsid} {dev_path}\n"
                        f"PVs on host {osd_host.hostname} are {pvs_names}"
                    )
                    raise

                log.info(
                    f"LVs/VGs/PVs are cleared for OSD {osd_id} on host {osd_host.hostname}\n"
                )

            else:

                log.info(
                    f"Destroy flag (--destroy) not passed during test execution\n"
                    f"Proceeding to check LVs/VGs/PVs are intact for"
                    f" OSD {osd_id} ( OSD device path {dev_path} ) on host {osd_host.hostname}"
                )
                if dev_path not in pvs_names:
                    log.error(
                        f"lvs/vgs/pvs are cleared with passing --destroy flag for OSD {osd_id} {osd_fsid} {dev_path}\n"
                        f"pvs on host {osd_host.hostname} are {pvs_names}"
                    )
                    raise
                log.info(
                    f"LVs/VGs/PVs are not cleared for OSD {osd_id} on host {osd_host.hostname}\n"
                )

            log.info(
                f"Successfully zapped device {dev_path} with path:{dev_path}"
                f" OSD_ID:{osd_id} OSD_FSID:{osd_fsid} on host:{osd_host.hostname}"
            )

    except Exception as e:
        log.error(f"Failed with exception: {e.__doc__}")
        log.exception(e)
        return 1
    finally:
        log.info(
            "\n \n ************** Execution of finally block begins here *************** \n \n"
        )

        if config.get("zap"):
            log.info("Retriving list of active OSDs")
            cluster_osds = rados_obj.get_active_osd_list()

            log.info("Setting OSD service to managed")
            if len(cluster_osds):
                utils.set_osd_devices_unmanaged(
                    ceph_cluster, cluster_osds[0], unmanaged=False
                )

            log.info("Successfully set osd service to managed")

            if (
                (osd_id in locals() or osd_id in globals())
                and (osd_host in locals() and osd_host in globals())
                and (osd_fsid in locals() and osd_fsid in globals())
            ):
                if (
                    not (config.get("zap").get("destroy"))
                    and osd_id not in cluster_osds
                ):
                    log.info(
                        f"LVs/VGs/PVs of Device {dev_path} not cleared during test exectuion\n"
                        f"Proceeding to zap device {dev_path} with --destory flag to wipe LVs/VGs/PVs"
                    )
                    out = volumeobject_obj.lvm_zap(
                        device=dev_path,
                        destroy=True,
                        osd_id=None,
                        osd_fsid=None,
                        host=osd_host,
                    )
                    log.info(
                        f"Successfully wiped LVs/VGs/PVs from device {dev_path} on host {osd_host.hostname}"
                    )

                log.info(
                    f"Waiting until removed osd {osd_id} on host {osd_host.hostname} is added back"
                )
                wait_for_osd_daemon_state(osd_host, osd_id, "up")

                log.info(
                    f"Successfully added back osd {osd_id} to cluster\n"
                    f"Successfully removed osd {osd_id}, zapped device {dev_path}"
                    f" associated with osd [osd_id: {osd_id}, osd_fsid: {osd_fsid}]"
                    f" and added back the removed OSD"
                )

        # log cluster health
        rados_obj.log_cluster_health()

        # check for crashes after test execution
        if rados_obj.check_crash_status():
            log.error("Test failed due to crash at the end of test")
            return 1

    log.info("Completed verification of Ceph-BlueStore-Tool commands.")
    return 0
