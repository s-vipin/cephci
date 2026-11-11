import time
from typing import List

from looseversion import LooseVersion

from ceph.ceph_admin import CephAdmin
from ceph.rados.core_workflows import RadosOrchestrator
from ceph.rados.osd_ok_to_upgrade_utils import (
    OsdOkToUpgradeCommand,
    OsdOkToUpgradeCommandOutput,
)
from ceph.rados.serviceability_workflows import ServiceabilityMethods
from tests.rados.monitor_configurations import MonConfigMethods
from tests.rados.test_osd_ok_to_upgrade_holistic_tests import (
    _wait_osds_single_version,
    get_daemon_container_images,
    validate_upgraded_osds,
)
from utility.log import Log

log = Log(__name__)

POLL_INTERVAL = 30
UPGRADE_TIMEOUT = 3600


def _lightweight_downgrade_osds(
    rados_obj: RadosOrchestrator,
    mon_config_method: MonConfigMethods,
    old_image: str,
    new_image: str,
    osd_service_name: str,
    downgrade_timeout: int,
    service_obj: ServiceabilityMethods,
):
    """
    Downgrade any OSDs currently on new_image back to old_image using
    ceph config set + ceph orch daemon redeploy (lightweight — no OSD remove/re-add).
    Re-issues redeploy commands periodically so cephadm doesn't get stuck.
    """
    # Find OSDs currently on new_image; also collect any that are in error state
    out = rados_obj.run_ceph_command(
        cmd="ceph orch ps --daemon-type osd --refresh", client_exec=True
    )
    osds_on_new_image = []
    error_osds = []
    for d in out:
        oid = d.get("daemon_id")
        img = d.get("container_image_name", "")
        status = d.get("status_desc", "")
        if oid is None:
            continue
        if img.strip() == new_image.strip():
            osds_on_new_image.append(int(oid))
        if status in ("error", "failed", "stopped"):
            error_osds.append(int(oid))

    # Restart any failed/errored OSD daemons — cephadm cannot reconcile a host
    # if it has daemons in error state, which blocks all redeploy tasks.
    if error_osds:
        log.info("Restarting error/failed OSDs before downgrade: %s", error_osds)
        for osd_id in error_osds:
            osd = f"osd.{osd_id}"
            try:
                rados_obj.client.exec_command(
                    cmd=f"ceph orch daemon restart {osd}", pretty_print=True
                )
            except Exception as e:
                log.warning("Failed to restart %s (non-fatal): %s", osd, e)
        time.sleep(20)  # give cephadm a moment to process the restarts

    if not osds_on_new_image:
        log.info("No OSDs on new_image %s; skip downgrade", new_image)
        _wait_osds_single_version(rados_obj, downgrade_timeout, old_image, service_obj)
        return

    log.info(
        "Downgrading OSDs %s from %s to %s", osds_on_new_image, new_image, old_image
    )

    # Remove per-OSD container_image config overrides
    cfg_dump = rados_obj.run_ceph_command(cmd="ceph config dump")
    for entry in cfg_dump:
        if entry.get("name") == "container_image" and "osd." in entry.get(
            "section", ""
        ):
            if entry["section"] != "osd":
                log.info(f"Removing per-OSD container_image config: {entry['section']}")
                mon_config_method.remove_config(
                    section=entry["section"],
                    name="container_image",
                    verify_rm=False,
                )

    # Set osd-level image back to old_image
    mon_config_method.set_config(
        section="osd", name="container_image", value=old_image, no_delay=True
    )

    # Ensure service is managed so cephadm executes the redeploys
    rados_obj.set_managed_flag(service_type="osd", service_name=osd_service_name)

    # Retry loop: re-issue redeploy for any OSD still on new_image.
    # cephadm sometimes marks a redeploy as "Scheduled" but doesn't execute it;
    # re-issuing the command (and periodically bouncing the cephadm mgr module)
    # pokes the reconcile loop into action.
    REDEPLOY_RETRY_INTERVAL = 120  # re-issue redeploys every 2 minutes
    MGR_BOUNCE_AFTER = 3  # bounce cephadm mgr module after this many retries
    deadline = time.time() + downgrade_timeout
    remaining = list(osds_on_new_image)
    retry_count = 0

    while remaining and time.time() < deadline:
        # After several failed retries, bounce the cephadm mgr module to clear
        # any stuck scheduler state and force re-reconciliation.
        if retry_count > 0 and retry_count % MGR_BOUNCE_AFTER == 0:
            log.info(
                "Retried %d times; bouncing cephadm mgr module to force reconciliation",
                retry_count,
            )
            try:
                rados_obj.client.exec_command(
                    cmd="ceph mgr module disable cephadm", pretty_print=True
                )
                time.sleep(5)
                rados_obj.client.exec_command(
                    cmd="ceph mgr module enable cephadm", pretty_print=True
                )
                time.sleep(15)  # let mgr module come back up
            except Exception as e:
                log.warning("mgr module bounce failed (non-fatal): %s", e)

        # Issue (or re-issue) redeploy for all OSDs still needing downgrade
        for osd_id in remaining:
            osd = f"osd.{osd_id}"
            log.info(f"Issuing: ceph orch daemon redeploy {osd}")
            rados_obj.client.exec_command(
                cmd=f"ceph orch daemon redeploy {osd}", pretty_print=True
            )
        retry_count += 1

        # Wait up to REDEPLOY_RETRY_INTERVAL, polling every POLL_INTERVAL
        wait_deadline = min(time.time() + REDEPLOY_RETRY_INTERVAL, deadline)
        while time.time() < wait_deadline:
            time.sleep(POLL_INTERVAL)
            ps_out = rados_obj.run_ceph_command(
                cmd="ceph orch ps --daemon-type osd --refresh", client_exec=True
            )
            id_to_image = {
                int(d["daemon_id"]): d.get("container_image_name", "")
                for d in ps_out
                if d.get("daemon_id") is not None
            }
            remaining = [
                oid
                for oid in osds_on_new_image
                if id_to_image.get(oid, "").strip() == new_image.strip()
            ]
            log.info(
                "Downgrade progress: %d/%d OSDs still on new_image: %s",
                len(remaining),
                len(osds_on_new_image),
                remaining,
            )
            if not remaining:
                break

    if remaining:
        raise TimeoutError(
            f"OSDs {remaining} still on new_image {new_image} after {downgrade_timeout}s"
        )

    # After container image is switched, also redeploy any OTHER OSD on the same hosts
    # that may have old_image container but still running new ceph binary (stale daemon).
    # This covers the case where a previous aborted run left a daemon running the new
    # binary even after its container label was switched back to old_image.
    try:
        ps_final = rados_obj.run_ceph_command(
            cmd="ceph orch ps --daemon-type osd --refresh", client_exec=True
        )
        # Find which hosts had OSDs just downgraded
        downgraded_hosts = set()
        id_to_host = {}
        for d in ps_final:
            oid = d.get("daemon_id")
            if oid is not None:
                id_to_host[int(oid)] = d.get("hostname", "")
        for oid in osds_on_new_image:
            if oid in id_to_host:
                downgraded_hosts.add(id_to_host[oid])

        # Any OSD on those same hosts that is still on old_image but hasn't been
        # explicitly redeployed may have a stale running binary — redeploy them all.
        sibling_osds = [
            int(d["daemon_id"])
            for d in ps_final
            if d.get("daemon_id") is not None
            and id_to_host.get(int(d["daemon_id"])) in downgraded_hosts
            and int(d["daemon_id"]) not in osds_on_new_image
            and d.get("container_image_name", "").strip() == old_image.strip()
        ]
        if sibling_osds:
            log.info(
                "Redeploying %d sibling OSDs on same hosts (may have stale running binary): %s",
                len(sibling_osds),
                sibling_osds,
            )
            rados_obj.set_managed_flag(
                service_type="osd", service_name=osd_service_name
            )
            for osd_id in sibling_osds:
                osd = f"osd.{osd_id}"
                try:
                    rados_obj.client.exec_command(
                        cmd=f"ceph orch daemon redeploy {osd}", pretty_print=True
                    )
                except Exception as e:
                    log.warning("redeploy %s failed (non-fatal): %s", osd, e)
    except Exception as e:
        log.warning("Sibling OSD redeploy check failed (non-fatal): %s", e)

    log.info(
        "All OSDs successfully downgraded to old_image. Waiting for single-version convergence."
    )
    _wait_osds_single_version(rados_obj, downgrade_timeout, old_image, service_obj)


def run(ceph_cluster, **kw):
    config = kw["config"]
    log.info(f"config: {config}")
    cephadm = CephAdmin(cluster=ceph_cluster, **config)
    rados_obj = RadosOrchestrator(node=cephadm)
    scenarios = config.get("scenarios", [])
    downgrade_timeout = config.get("downgrade_timeout", 3600)
    service_obj = ServiceabilityMethods(cluster=ceph_cluster, **config)
    mon_config_method = MonConfigMethods(rados_obj=rados_obj)
    osd_service_name = config.get("osd_service_name", "osd.osds")

    # ceph version check
    if LooseVersion(str(config.get("release"))) < LooseVersion("9.1"):
        log.error(
            "This test module is a 9.1 feature. Skipping test in ceph versions less than 9.1."
        )
        return 0

    # new_image: mgr/mon are already upgraded to this image
    new_image = get_daemon_container_images(rados_obj, daemon="mgr")[0]
    # old_image: all OSDs should be on the pre-upgrade image.
    # Find it dynamically: the image(s) OSDs are running that is NOT new_image.
    # If all OSDs are already on new_image (e.g. after an aborted run), fall back to the
    # osd-section container_image from ceph config dump — that is the configured baseline.
    osd_images = get_daemon_container_images(rados_obj, daemon="osd")
    if not osd_images:
        log.error("No OSD images found via ceph orch ps")
        return 1
    unique_osd_images = list(set(osd_images))
    baseline_images = [img for img in unique_osd_images if img != new_image]
    if baseline_images:
        old_image = baseline_images[0]
    else:
        # All OSDs currently on new_image — look for old_image in config dump
        cfg_dump = rados_obj.run_ceph_command(cmd="ceph config dump")
        old_image = next(
            (
                e.get("value", "").strip()
                for e in cfg_dump
                if e.get("section") == "osd" and e.get("name") == "container_image"
            ),
            None,
        )
        if not old_image or old_image == new_image:
            log.error(
                "Cannot determine old_image: all OSDs are on new_image (%s) and "
                "ceph config osd container_image is also new_image or unset. "
                "Cluster is fully upgraded — cannot run partial upgrade test.",
                new_image,
            )
            return 1
        log.info(
            "All OSDs on new_image; found old_image from config dump: %s. "
            "Will reset OSDs to old_image before running scenarios.",
            old_image,
        )
        # Reset OSDs back to old_image using lightweight redeploy
        _lightweight_downgrade_osds(
            rados_obj,
            mon_config_method,
            old_image,
            new_image,
            osd_service_name,
            downgrade_timeout,
            service_obj,
        )
    log.info(f"old_image (OSD baseline, detected dynamically): {old_image}")
    log.info(f"new_image (upgrade target): {new_image}")

    if not scenarios:
        log.error("config.scenarios cannot be empty")
        return 1

    try:
        # get_osd_hosts() routes through node1-installer which can have transient SSH drops.
        # Retry a few times before giving up.
        osd_hostnames = None
        for _attempt in range(5):
            try:
                osd_hostnames = rados_obj.get_osd_hosts()
                break
            except Exception as _e:
                log.warning(
                    "get_osd_hosts attempt %d failed: %s — retrying in 20s",
                    _attempt + 1,
                    _e,
                )
                time.sleep(20)
        if not osd_hostnames:
            raise RuntimeError("get_osd_hosts() failed after 5 attempts")

        # Pick host1 as the first host whose OSDs are ALL healthy (running, not error/down).
        # A host with a broken OSD (persistent error/down) will cause ceph osd ok-to-upgrade
        # to count that OSD as "upgraded" based on its last known version — skew results.
        ps_now = rados_obj.run_ceph_command(
            cmd="ceph orch ps --daemon-type osd --refresh", client_exec=True
        )
        host_to_broken = {}
        for d in ps_now:
            h = d.get("hostname", "")
            status = d.get("status_desc", "")
            if status in ("error", "failed", "stopped", "unknown") or not d.get(
                "started"
            ):
                host_to_broken[h] = host_to_broken.get(h, 0) + 1
        host1 = None
        for h in osd_hostnames:
            if host_to_broken.get(h, 0) == 0:
                host1 = h
                break
        if host1 is None:
            # Fall back to first host if all have some issue
            log.warning(
                "All OSD hosts have some broken OSDs; falling back to first host"
            )
            host1 = osd_hostnames[0]
        log.info(
            "Selected host1=%s for scenarios (broken OSD count: %d)",
            host1,
            host_to_broken.get(host1, 0),
        )

        for scenario in scenarios:
            log.info("==============================================================")
            log.info("Scenario: %s", scenario)
            log.info("==============================================================")

            # Reset cluster: downgrade any upgraded OSDs back to old_image
            _lightweight_downgrade_osds(
                rados_obj,
                mon_config_method,
                old_image,
                new_image,
                osd_service_name,
                downgrade_timeout,
                service_obj,
            )

            rados_obj.client.exec_command(
                cmd="ceph orch ps | grep osd", pretty_print=True
            )

            if scenario == "host_few_osds_upgraded":
                host1_osds = rados_obj.collect_osd_daemon_ids(host1)
                osds_to_upgrade = host1_osds[:3]
                crush_bucket = host1
                osds_in_crush_bucket = host1_osds
            elif scenario == "chassis_few_osds_upgraded":
                chassis1_osds = rados_obj.collect_osd_daemon_ids("chassis1")
                host1_osds = rados_obj.collect_osd_daemon_ids(host1)
                osds_to_upgrade = host1_osds[:3]
                crush_bucket = "chassis1"
                osds_in_crush_bucket = chassis1_osds
            elif scenario == "chassis_host_osds_upgraded":
                chassis1_osds = rados_obj.collect_osd_daemon_ids("chassis1")
                host1_osds = rados_obj.collect_osd_daemon_ids(host1)
                osds_to_upgrade = host1_osds
                crush_bucket = "chassis1"
                osds_in_crush_bucket = chassis1_osds
            elif scenario == "rack_few_osds_upgraded":
                rack1_osds = rados_obj.collect_osd_daemon_ids("rack1")
                host1_osds = rados_obj.collect_osd_daemon_ids(host1)
                osds_to_upgrade = host1_osds[:3]
                crush_bucket = "rack1"
                osds_in_crush_bucket = rack1_osds
            elif scenario == "rack_host_osds_upgraded":
                rack1_osds = rados_obj.collect_osd_daemon_ids("rack1")
                host1_osds = rados_obj.collect_osd_daemon_ids(host1)
                osds_to_upgrade = host1_osds
                crush_bucket = "rack1"
                osds_in_crush_bucket = rack1_osds
            elif scenario == "rack_chassis_osds_upgraded":
                rack1_osds = rados_obj.collect_osd_daemon_ids("rack1")
                chassis1_osds = rados_obj.collect_osd_daemon_ids("chassis1")
                host1_osds = rados_obj.collect_osd_daemon_ids(host1)
                osds_to_upgrade = chassis1_osds
                crush_bucket = "rack1"
                osds_in_crush_bucket = rack1_osds
            else:
                log.error(f"Invalid scenario: {scenario}")
                return 1

            # Ensure service is managed so cephadm will actually execute the redeploy
            rados_obj.set_managed_flag(
                service_type="osd", service_name=osd_service_name
            )

            # Set per-OSD container_image override to new_image (with retry on transient failures)
            for osd_id in osds_to_upgrade:
                osd = f"osd.{osd_id}"
                for _cfg_attempt in range(5):
                    try:
                        result = mon_config_method.set_config(
                            section=osd, name="container_image", value=new_image
                        )
                        assert result, f"set_config failed for {osd}"
                        break
                    except Exception as _ce:
                        if _cfg_attempt < 4:
                            log.warning(
                                "set_config attempt %d for %s failed: %s — retrying in 15s",
                                _cfg_attempt + 1,
                                osd,
                                _ce,
                            )
                            time.sleep(15)
                        else:
                            raise

            # Retry-redeploy loop: re-issue redeploy for any OSD still on old_image
            REDEPLOY_RETRY_INTERVAL = 120
            upg_deadline = time.time() + UPGRADE_TIMEOUT
            upg_remaining = list(osds_to_upgrade)
            while upg_remaining and time.time() < upg_deadline:
                for osd_id in upg_remaining:
                    osd = f"osd.{osd_id}"
                    log.info(f"Issuing: ceph orch daemon redeploy {osd}")
                    rados_obj.client.exec_command(
                        cmd=f"ceph orch daemon redeploy {osd}", pretty_print=True
                    )
                wait_until = min(time.time() + REDEPLOY_RETRY_INTERVAL, upg_deadline)
                while time.time() < wait_until:
                    time.sleep(POLL_INTERVAL)
                    ps_out = rados_obj.run_ceph_command(
                        cmd="ceph orch ps --daemon-type osd --refresh", client_exec=True
                    )
                    id_to_image = {
                        int(d["daemon_id"]): d.get("container_image_name", "")
                        for d in ps_out
                        if d.get("daemon_id") is not None
                    }
                    upg_remaining = [
                        oid
                        for oid in osds_to_upgrade
                        if id_to_image.get(oid, "").strip() != new_image.strip()
                    ]
                    log.info(
                        "Upgrade progress: %d/%d OSDs not yet on new_image: %s",
                        len(upg_remaining),
                        len(osds_to_upgrade),
                        upg_remaining,
                    )
                    if not upg_remaining:
                        break
            if upg_remaining:
                raise TimeoutError(
                    f"OSDs {upg_remaining} did not upgrade to {new_image} within {UPGRADE_TIMEOUT}s"
                )

            validate_upgraded_osds(rados_obj, osds_to_upgrade, new_image)

            # Get ceph_version_short from an upgraded OSD's metadata (e.g. "20.2.2-349")
            osd_metadata = rados_obj.run_ceph_command(
                cmd=f"ceph osd metadata {osds_to_upgrade[0]}"
            )
            ceph_version = osd_metadata.get("ceph_version_short")
            log.info(
                f"ceph_version_short from OSD {osds_to_upgrade[0]}: {ceph_version}"
            )

            # When every OSD in the crush bucket has been upgraded, the command returns
            # ok_to_upgrade=False / all_osds_upgraded=True and writes to stderr.
            # We must pass ignore_errors=True so execute() doesn't raise CommandFailed.
            all_upgraded = sorted(osds_to_upgrade) == sorted(osds_in_crush_bucket)

            actual = OsdOkToUpgradeCommand(
                crush_bucket=crush_bucket,
                ceph_version=ceph_version,
                rados_obj=rados_obj,
            ).execute(ignore_errors=all_upgraded)

            expected = OsdOkToUpgradeCommandOutput(
                ok_to_upgrade=not all_upgraded,
                all_osds_upgraded=all_upgraded,
                osds_in_crush_bucket=osds_in_crush_bucket,
                osds_ok_to_upgrade=(
                    []
                    if all_upgraded
                    else [
                        osd
                        for osd in osds_in_crush_bucket
                        if osd not in osds_to_upgrade
                    ]
                ),
                osds_upgraded=osds_to_upgrade,
                bad_no_version=[],
            )

            assert expected == actual

            log.info("==============================================================")
            log.info("Scenario %s passed", scenario)
            log.info("==============================================================")

    except Exception as e:
        log.error("Execution failed: %s", e)
        log.exception(e)
        rados_obj.log_cluster_health()
        return 1
    finally:
        log.info(
            "===========FINALLY BLOCK: Downgrading OSDs to old image==============="
        )
        _lightweight_downgrade_osds(
            rados_obj,
            mon_config_method,
            old_image,
            new_image,
            osd_service_name,
            downgrade_timeout,
            service_obj,
        )
    return 0
