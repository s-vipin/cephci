"""
Test Module to perform specific functionalities of ceph-volume.
 - ceph-volume lvm list [OSD_ID | DEVICE_PATH | VOLUME_GROUP/LOGICAL_VOLUME]
 - ceph-volume lvm zap [--destroy] [--osd-id OSD_ID | --osd-fsid OSD_FSID | DEVICE_PATH]
 - ceph-volume --help
 - ceph-volume lvm --help
 - ceph-volume lvm prepare --bluestore [--block.db --block.wal] --data path
 - ceph-volume lvm activate [--bluestore OSD_ID OSD_FSID] [--all]
 - ceph-volume lvm deactivate OSD_ID
 - ceph-volume lvm create --bluestore --data VOLUME_GROUP/LOGICAL_VOLUME
 - ceph-volume lvm batch --bluestore PATH_TO_DEVICE [PATH_TO_DEVICE]
 - ceph-volume lvm migrate --osd-id OSD_ID --osd-fsid OSD_UUID
    --from [ data | db | wal ] --target VOLUME_GROUP_NAME/LOGICAL_VOLUME_NAME
 - ceph-volume lvm new-db --osd-id OSD_ID --osd-fsid OSD_FSID --target VOLUME_GROUP_NAME/LOGICAL_VOLUME_NAME
 - ceph-volume lvm new-wal --osd-id OSD_ID --osd-fsid OSD_FSID --target VOLUME_GROUP_NAME/LOGICAL_VOLUME_NAME
"""

from ceph.ceph_admin import CephAdmin
from ceph.rados.core_workflows import RadosOrchestrator
from utility.log import Log

log = Log(__name__)


class CephVolumeWorkflows:
    """
    Contains various functions to verify ceph-volume commands
    """

    def __init__(self, node: CephAdmin):
        """
        initializes the env to run ceph-volume commands
        Args:
            node: CephAdmin object
        """
        self.rados_obj = RadosOrchestrator(node=node)
        self.cluster = node.cluster
        self.client = node.cluster.get_nodes(role="client")[0]

    def run_cv_command(
        self,
        cmd: str,
        host,
        timeout: int = 300,
    ) -> str:
        """
        Runs ceph-volume commands within cephadm shell
        Args:
            cmd: command that needs to be run
            host: (ceph.utils.CephVMNode): CephVMNode
            timeout: Maximum time allowed for execution.
        Returns:
            output of respective ceph-volume command in string format
        """
        _cmd = f"cephadm {cmd}"
        try:
            out, err = host.exec_command(sudo=True, cmd=_cmd, timeout=timeout)
        except Exception as er:
            log.error(f"Exception hit while command execution. {er}")
            raise
        return str(out)

    def help(self, host):
        """Module to run help command with ceph-volume to display usage
         Args:
            host: (ceph.utils.CephVMNode): CephVMNode
        Returns:
            Output of ceph-volume usage
        """
        _cmd = "ceph-volume --help"
        return self.run_cv_command(cmd=_cmd, host=host)

    def lvm_help(self, host):
        """Module to run help command with ceph-volume lvm to display usage
         Args:
            host: (ceph.utils.CephVMNode): CephVMNode
        Returns:
            Output of ceph-volume lvm usage
        """
        _cmd = "ceph-volume --help"
        return self.run_cv_command(cmd=_cmd, host=host)

    def lvm_list(self, host, osd_id=None, device=None):
        """Module to list logical volumes and devices
        associated with a Ceph cluster

        Args:
            [optional] osd_id: ID of the OSD to list
            [optional] device: path of the OSD device to list
            host: (ceph.utils.CephVMNode): CephVMNode

        Returns:
            Returns the output of ceph-volume lvm list

        Usage:
            usage without any arguements:
                cephvolume_obj.lvm_list(host=osd_host)
            usage with osd_id:
                cephvolume_obj.lvm_list(osd_id="6",host=osd_host)
            usage with device:
                cephvolume_obj.lvm_list(device="/dev/vdb", host=osd_host)
        """
        # Extracting the ceush map from the cluster
        _cmd = "ceph-volume lvm list"
        if osd_id:
            _cmd = f"{_cmd} {osd_id}"
        elif device:
            _cmd = f"{_cmd} {device}"

        _cmd = f"{_cmd} --format json"
        return self.run_cv_command(cmd=_cmd, host=host)

    def lvm_zap(self, host, destroy=None, osd_id=None, osd_fsid=None, device=None):
        """Module to remove all data and file systems from a logical volume or partition.
        Optionally, you can use the --destroy flag for complete removal of a logical volume,
        partition, or the physical device.

        Args:
            [optional] destroy: If destroy is true --destory flag will be added to the command for
            complete removal of logical volume, volume group and physical volume
            [optional] osd_id: ID of the OSD to zap
            [optional] osd_fsid: FSID of the OSD to zap
            [optional] device: Device path of the OSD to zap

        Returns:
            Returns the output of ceph-volume lvm zap

        Usage:
            usage with device path:
                cephvolume_obj.lvm_zap(device="/dev/vdb", destory=False, host=osd_host)
            usage with osd-id:
                cephvolume_obj.lvm_zap(osd_id="6", destroy=True, host=osd_host)
            usage with osd-fsid:
                cephvolume_obj.lvm_zap(osd_fsid="5cf86425-bd5b-4526-9961-a7d2286ab973", destroy=True, host=osd_host)
        """
        _cmd = "ceph-volume lvm zap"
        if device:
            _cmd = f"{_cmd} {device}"

        if osd_id:
            _cmd = f"{_cmd} --osd-id {osd_id}"

        if osd_fsid:
            _cmd = f"{_cmd} --osd-fsid {osd_fsid}"

        if destroy:
            _cmd = f"{_cmd} --destroy"

        log.info(f"Proceeding to zap using command: {_cmd}")
        return self.run_cv_command(cmd=_cmd, host=host)

    def lvm_prepare(self, device: str, host):
        """Module to prepare volumes, partitions or physical devices.
        The prepare subcommand prepares an OSD back-end object store
        and consumes logical volumes (LV) for both the OSD data and journal.
        Args:
            destory
        Returns:
            Returns the output of ceph-volume lvm prepare
        """
        _cmd = f"ceph-volume lvm prepare --bluestore --data {device}"
        return self.run_cv_command(cmd=_cmd, host=host)

    def lvm_activate(self, osd_id: int, osd_fsid: str, all: bool, host):
        """Module to activate OSD. The activation process for a Ceph OSD enables
        a systemd unit at boot time, which allows the correct OSD identifier and
        its UUID to be enabled and mounted.
        Args:
            osd_id: ID of OSD to activate
            osd_fsid: FSID of OSD to activate
            all: activate all OSD that are prepared for activation by passing --all flag
        Returns:
            Returns the output of ceph-volume lvm activate
        """
        _cmd = "ceph-volume lvm activate"
        if all:
            _cmd = f"{_cmd} --all"
        else:
            _cmd = f"{_cmd} --bluestore {osd_id} {osd_fsid}"
        return self.run_cv_command(cmd=_cmd, host=host)

    def lvm_deactivate(self, osd_id: int, host):
        """Module to remove the volume groups and the logical volume
        Args:
            None
        Returns:
            Returns the output of ceph-volume lvm deactivate
        """
        _cmd = f"ceph-volume lvm deactivate {osd_id}"
        return self.run_cv_command(cmd=_cmd, host=host)

    def lvm_batch(self, devices: list, host):
        """Module to automate the creation of multiple OSDs
        Args:
            devices: list of devices for OSD creation
        Returns:
            Returns the output of ceph-volume lvm batch
        """
        _cmd = "ceph-volume lvm batch --bluestore"
        for device in devices:
            _cmd = f"{_cmd} {device}"
        return self.run_cv_command(cmd=_cmd, host=host)

    def lvm_migrate(
        self, osd_id: int, osd_fsid: str, host, from_flag_list: list, target: str
    ):
        """Module to migrate the BlueStore file system (BlueFS) data (RocksDB data)
        from the source volume to the target volume.
        Args:
            from: arguement for --from flag. list can contain "data" "db" "wal"
            osd_id: ID of the source OSD for data migration
            osd_fsid: FSID of the source OSD for data migration
            target: target device/logical volume for data migration
        Returns:
            Returns the output of ceph-volume lvm migrate
        """
        _cmd = f"ceph-volume lvm migrate --osd-id {osd_id} --osd-fsid {osd_fsid} --from"
        for device in from_flag_list:
            _cmd = f"{_cmd} {device}"
        return self.run_cv_command(cmd=_cmd, host=host)

    def lvm_create(self, path: str, host):
        """Module to call the prepare subcommand, and then calls the activate subcommand.
        Args:
            path: path can be device path (/dev/vdb) or logical volume (volume_group/logical_volume)
        Returns:
            Returns the output of ceph-volume lvm create
        """
        _cmd = f"ceph-volume lvm create --bluestore --data {path}"
        return self.run_cv_command(cmd=_cmd, host=host)

    def lvm_new_db(self, path: str, host):
        """Module to attache the given logical volume to the given OSD as a DB volume
        Args:
            path: path can be device path (/dev/vdb) or logical volume (volume_group/logical_volume)
        Returns:
            Returns the output of ceph-volume lvm new-db
        """
        _cmd = f"ceph-volume lvm create --bluestore --data {path}"
        return self.run_cv_command(cmd=_cmd, host=host)

    def lvm_new_wal(self, path: str, host):
        """Module to attache the given logical volume to the given OSD as a WAL volume
        Args:
            path: path can be device path (/dev/vdb) or logical volume (volume_group/logical_volume)
        Returns:
            Returns the output of ceph-volume lvm new-wal
        """
        _cmd = f"ceph-volume lvm create --bluestore --data {path}"
        return self.run_cv_command(cmd=_cmd, host=host)
