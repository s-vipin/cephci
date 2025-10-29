"""
Test Module to test functionalities of bluestore data compression.
scenario-1: Validate default compression values
scenario-2: Enable bluestore_write_v2 and validate
scenario-3: Disable bluestore_write_v2 and validate
"""

from ceph.ceph_admin import CephAdmin
from ceph.rados.core_workflows import RadosOrchestrator
from tests.rados.monitor_configurations import MonConfigMethods
from tests.rados.test_bluestore_comp_enhancements_class import (
	BLUESTORE_ALLOC_HINTS,
	COMPRESSION_MODES,
	BluestoreDataCompression,
)
from utility.log import Log
from libcloud.utils.misc import get_secure_random_string
import concurrent.futures

log = Log(__name__)


def run(ceph_cluster, **kw):
	"""
    Test Module to test functionalities of bluestore data compression.
        scenario-1: Validate default compression values
        scenario-2: Enable bluestore_write_v2 and validate
        scenario-3: Disable bluestore_write_v2 and validate
    """
	log.info(run.__doc__)
	config = kw["config"]

	cephadm = CephAdmin(cluster=ceph_cluster, **config)
	rados_obj = RadosOrchestrator(node=cephadm)
	mon_obj = MonConfigMethods(rados_obj=rados_obj)
	client_node = ceph_cluster.get_nodes(role="client")[0]
	scenarios_to_run = config.get(
		"scenarios_to_run",
		[
			"scenario-1",
			"scenario-2",
			"scenario-3",
			"scenario-4",
			"scenario-5",
			"scenario-6",
			"scenario-7",
			"scenario-8",
			"scenario-9",
		],
	)
	compression_config = {
		"rados_obj": rados_obj,
		"mon_obj": mon_obj,
		"cephadm": cephadm,
		"client_node": client_node,
		"ceph_cluster": ceph_cluster,
	}
	bluestore_compression = BluestoreDataCompression(**compression_config)
	try:

		log.info(
			"\n\n ************ Execution begins for bluestore data compression scenarios ************ \n\n"
		)

		if "scenario-4" in scenarios_to_run:
			results = []
			log.info("STARTED: Scenario 4: Validate blob sizes")

			compression_mode = COMPRESSION_MODES.FORCE
			obj_alloc_hint = BLUESTORE_ALLOC_HINTS.COMPRESSIBLE

			pool_id = get_secure_random_string(6)
			pool_name = f"test-{obj_alloc_hint}-{compression_mode}-{pool_id}"

			kwargs = {
				"pool_name": pool_name,
				"compression_mode": compression_mode,
				"alloc_hint": obj_alloc_hint,
				"device_class": "HDD",
				"run_dir": kw["run_config"]["log_dir"],
			}

			log.info("Compression parameters are :")
			log.info(kwargs)
			result = bluestore_compression.test_blob_sizes(**kwargs)
			log.info(f"result={result}")
			results.append(result)

			log.info(results)
			if result[-1] is True:
				raise Exception(f"Actual {result[0]}, Expected {result[1]}, values are not as expected")

			log.info("COMPLETED: Scenario 4: Validate blob sizes")

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

		# delete all rados pools
		rados_obj.rados_pool_cleanup()
		# log cluster health
		rados_obj.log_cluster_health()
		# check for crashes after test executio
		if rados_obj.check_crash_status():
			log.error("Test failed due to crash at the end of test")
			return 1

	log.info("Completed validation of bluestore v2 data compression.")
	return 0
