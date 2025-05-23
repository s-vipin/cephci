"""
Module to verify :
  -  Verify that a user can successfully mirror rbd group containing rbd images between primary and secondary sites
            Option 1: pool1/image1 & pool1/image2 (Images without namespaces)

Test case covered:
CEPH-83610860 - Verify that a user can successfully replicate grouped RBD images between primary and secondary sites

Pre-requisites :
1. Cluster must be up in 8.1 and above and running with capacity to create pool
2. We need atleast one client node with ceph-common package,
   conf and keyring files

Test Case Flow:
Step 1: Deploy Two ceph cluster on version 8.1 or above
Step 2: Create RBD pool ‘pool_1’ on both sites
Step 3: Enable Image mode mirroring on pool_1 on both sites
Step 4: Bootstrap the storage cluster peers (Two-way)
Step 5: Create 2 RBD images in pool_1
Step 6: Create Consistency group
Step 7: Add Images in the consistency group
Step 8: Add data to the images
Step 9: Enable Mirroring for the group
Step 10: Wait for mirroring to complete
Step 11: Check all image is replicated on site-b
Step 12: Validate size of each image should be same on site-a and site-b
Step 13: Check group is replicated on site-b
Step 14: Check whether images are part of correct group on site-b
Step 15: Check pool mirror status, image mirror status and group mirror status on both sites does
         not show 'unknown' or 'error' on both clusters
Step 16: Confirm that the global ids match for the groups and images on both clusters.
Step 17: Validate the integrity of the data on secondary site-b
Step 18: Repeat above on EC pool

"""

import json
from copy import deepcopy

from ceph.rbd.initial_config import initial_mirror_config
from ceph.rbd.mirror_utils import (
    check_mirror_consistency,
    compare_image_size_primary_secondary,
)
from ceph.rbd.utils import getdict, random_string
from ceph.rbd.workflows.cleanup import cleanup
from ceph.rbd.workflows.group_mirror import (
    enable_group_mirroring_and_verify_state,
    group_mirror_status_verify,
    wait_for_idle,
)
from ceph.rbd.workflows.krbd_io_handler import krbd_io_handler
from utility.log import Log

log = Log(__name__)


def test_group_mirroring(
    rbd_primary,
    rbd_secondary,
    client_primary,
    client_secondary,
    primary_cluster,
    secondary_cluster,
    pool_types,
    **kw
):
    """
    Test user can successfully mirror rbd group containing rbd images between primary and secondary sites
    Args:
        rbd_primary: RBD object of primary cluster
        rbd_secondary: RBD objevct of secondary cluster
        client_primary: client node object of primary cluster
        client_secondary: client node object of secondary cluster
        primary_cluster: Primary cluster object
        secondary_cluster: Secondary cluster object
        pool_types: Replication pool or EC pool
        **kw: any other arguments
    """

    for pool_type in pool_types:
        rbd_config = kw.get("config", {}).get(pool_type, {})
        multi_pool_config = deepcopy(getdict(rbd_config))

        # FIO Params Required for ODF workload exclusively in group mirroring
        fio = kw.get("config", {}).get("fio", {})
        io_config = {
            "size": fio["size"],
            "do_not_create_image": True,
            "runtime": fio["runtime"],
            "num_jobs": fio["ODF_CONFIG"]["num_jobs"],
            "iodepth": fio["ODF_CONFIG"]["iodepth"],
            "rwmixread": fio["ODF_CONFIG"]["rwmixread"],
            "direct": fio["ODF_CONFIG"]["direct"],
            "invalidate": fio["ODF_CONFIG"]["invalidate"],
            "config": {
                "file_size": fio["size"],
                "file_path": ["/mnt/mnt_" + random_string(len=5) + "/file"],
                "get_time_taken": True,
                "operations": {
                    "fs": "ext4",
                    "io": True,
                    "mount": True,
                    "map": True,
                },
                "cmd_timeout": 2400,
                "io_type": fio["ODF_CONFIG"]["io_type"],
            },
        }

        for pool, pool_config in multi_pool_config.items():
            group_config = {}
            if "data_pool" in pool_config.keys():
                _ = pool_config.pop("data_pool")
            group_config.update({"pool": pool})

            for image, image_config in pool_config.items():
                if "image" in image:
                    # Add data to the images
                    image_spec = []
                    io_config["rbd_obj"] = rbd_primary
                    io_config["client"] = client_primary
                    image_spec.append(pool + "/" + image)
                    io_config["config"]["image_spec"] = image_spec
                    (io, err) = krbd_io_handler(**io_config)
                    if err:
                        raise Exception(
                            "Map, mount and run IOs failed for "
                            + str(io_config["config"]["image_spec"])
                        )
                    else:
                        log.info(
                            "Map, mount and IOs successful for "
                            + str(io_config["config"]["image_spec"])
                        )

            group_config.update({"group": pool_config.get("group")})

            # Get Group Mirroring Status
            (group_mirror_status, err) = rbd_primary.mirror.group.status(**group_config)
            if err:
                if "mirroring not enabled on the group" in err:
                    mirror_state = "Disabled"
                else:
                    raise Exception("Getting group mirror status failed : " + str(err))
            else:
                mirror_state = "Enabled"
            log.info(
                "Group " + group_config["group"] + " mirroring state is " + mirror_state
            )

            # Enable Group Mirroring and Verify
            if mirror_state == "Disabled":
                enable_group_mirroring_and_verify_state(rbd_primary, **group_config)
            log.info("Successfully Enabled group mirroring")

            # Wait for group mirroring to complete
            wait_for_idle(rbd_primary, **group_config)
            log.info(
                "Successfully completed sync for group mirroring to secondary site"
            )

            # Validate size of each image should be same on site-a and site-b
            (group_image_list, err) = rbd_primary.group.image.list(
                **group_config, format="json"
            )
            if err:
                raise Exception("Getting group image list failed : " + str(err))
            compare_image_size_primary_secondary(
                rbd_primary, rbd_secondary, group_image_list
            )
            log.info(
                "Successfully verified size of rbd images matches across both clusters"
            )

            # Check group is replicated on site-b using group info
            (group_info_status, err) = rbd_secondary.group.info(
                **group_config, format="json"
            )
            if err:
                raise Exception("Getting group info failed : " + str(err))
            if (
                json.loads(group_info_status)["group_name"] != group_config["group"]
                or json.loads(group_info_status)["mirroring"]["state"] != "enabled"
                or json.loads(group_info_status)["mirroring"]["mode"] != "snapshot"
                or json.loads(group_info_status)["mirroring"]["primary"]
            ):
                raise Exception("group info is not as expected on secondary cluster")
            log.info("Successfully verified group is present on secondary cluster")

            # Check whether images are part of correct group on site-b using group image-list
            (group_image_list_primary, err) = rbd_primary.group.image.list(
                **group_config, format="json"
            )
            (group_image_list_secondary, err) = rbd_secondary.group.image.list(
                **group_config, format="json"
            )
            if err:
                raise Exception("Getting group image list failed : " + str(err))
            if json.loads(group_image_list_primary) != json.loads(
                group_image_list_secondary
            ):
                raise Exception(
                    "Group image list does not match for primary and secondary cluster"
                )
            log.info(
                "Successfully verified image list for the group matches across both cluster"
            )

            # Verify group mirroring status on both clusters & Match global id of both cluster
            group_mirror_status_verify(
                primary_cluster,
                secondary_cluster,
                rbd_primary,
                rbd_secondary,
                primary_state="up+stopped",
                secondary_state="up+replaying",
                **group_config
            )
            log.info(
                "Successfully verified group status and global ids match for both clusters"
            )

            # Validate the integrity of the data on secondary site-b
            check_mirror_consistency(
                rbd_primary,
                rbd_secondary,
                client_primary,
                client_secondary,
                group_image_list,
            )
            log.info(
                "Successfully verified md5sum of all images matches across both clusters"
            )


def run(**kw):
    """
    This test verify that a user can successfully mirror rbd group containing rbd images between primary and
    secondary sites
            Option 1: pool1/image1 & pool1/image2 (Images without namespaces) in a
            group is consistent between both clusters
    Args:
        kw: test data
    Returns:
        int: The return value. 0 for success, 1 otherwise

    """
    try:
        pool_types = ["rep_pool_config", "ec_pool_config"]
        log.info("Running Consistency Group Mirroring across two clusters")
        kw.get("config").update({"grouptype": kw.get("config").get("grouptype")})
        mirror_obj = initial_mirror_config(**kw)
        mirror_obj.pop("output", [])
        for val in mirror_obj.values():
            if not val.get("is_secondary", False):
                rbd_primary = val.get("rbd")
                client_primary = val.get("client")
                primary_cluster = val.get("cluster")
            else:
                rbd_secondary = val.get("rbd")
                client_secondary = val.get("client")
                secondary_cluster = val.get("cluster")

        pool_types = list(mirror_obj.values())[0].get("pool_types")
        test_group_mirroring(
            rbd_primary,
            rbd_secondary,
            client_primary,
            client_secondary,
            primary_cluster,
            secondary_cluster,
            pool_types,
            **kw
        )

    except Exception as e:
        log.error(
            "Test: RBD group mirroring (snapshot mode) across two clusters failed: "
            + str(e)
        )
        return 1

    finally:
        cleanup(pool_types=pool_types, multi_cluster_obj=mirror_obj, **kw)

    return 0
