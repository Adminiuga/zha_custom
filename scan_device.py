import asyncio
import logging
import os
from collections import OrderedDict

from zigpy.exceptions import DeliveryError
from zigpy.util import retryable

from homeassistant.util.json import save_json

LOGGER = logging.getLogger(__name__)


@retryable((DeliveryError, asyncio.TimeoutError), tries=5)
async def read_attr(cluster, attrs):
    return await cluster.read_attributes(attrs, allow_cache=False)


@retryable((DeliveryError, asyncio.TimeoutError), tries=5)
def wrapper(cmd, *args, **kwargs):
    return cmd(*args, **kwargs)


async def scan_results(device):
    result = {"ieee": str(device.ieee), "nwk": "0x{:04x}".format(device.nwk)}

    LOGGER.debug("Scanning device 0x{:04x}".format(device.nwk))

    endpoints = []
    for epid, ep in device.endpoints.items():
        if epid == 0:
            continue
        LOGGER.debug("scanning endpoint #%i", epid)
        result["model"] = ep.model
        result["manufacturer"] = ep.manufacturer
        endpoint = {
            "id": epid,
            "device_type": "0x{:04x}".format(ep.device_type),
            "profile": "0x{:04x}".format(ep.profile_id),
        }
        endpoint.update(await scan_endpoint(ep))
        endpoints.append(endpoint)

    result["endpoints"] = endpoints
    return result


async def scan_endpoint(ep):
    result = {}
    clusters = {}
    for cluster in ep.in_clusters.values():
        LOGGER.debug(
            "Scanning cluster_id 0x{:04x}/'{}' input cluster".format(
                cluster.cluster_id, cluster.ep_attribute
            )
        )
        key = "0x{:04x}".format(cluster.cluster_id)
        clusters[key] = await scan_cluster(cluster, is_server=True)
    result["in_clusters"] = OrderedDict(sorted(clusters.items(), key=lambda k: k[0]))

    clusters = {}
    for cluster in ep.out_clusters.values():
        LOGGER.debug(
            "Scanning cluster_id 0x{:04x}/'{}' output cluster".format(
                cluster.cluster_id, cluster.ep_attribute
            )
        )
        key = "0x{:04x}".format(cluster.cluster_id)
        clusters[key] = await scan_cluster(cluster, is_server=True)
    result["out_clusters"] = OrderedDict(sorted(clusters.items(), key=lambda k: k[0]))
    return result


async def scan_cluster(cluster, is_server=True):
    if is_server:
        cmds_gen = "commands_generated"
        cmds_rec = "commands_received"
    else:
        cmds_rec = "commands_generated"
        cmds_gen = "commands_received"
    return {
        "cluster_id": "0x{:04x}".format(cluster.cluster_id),
        "name": cluster.ep_attribute,
        "attributes": await discover_attributes_extended(cluster),
        cmds_rec: await discover_commands_received(cluster, is_server),
        cmds_gen: await discover_commands_generated(cluster, is_server),
    }


async def discover_attributes_extended(cluster, manufacturer=None):
    from zigpy.zcl import foundation

    LOGGER.debug("Discovering attributes extended")
    result = {}
    attr_id = 0
    done = False

    while not done:
        try:
            done, rsp = await wrapper(
                cluster.discover_attributes_extended,
                attr_id,
                16,
                manufacturer=manufacturer,
            )
            await asyncio.sleep(0.4)
        except DeliveryError as ex:
            LOGGER.error(
                "Failed to discover attributes extended starting %s. Error: {}".format(
                    attr_id, ex
                )
            )
            break
        if isinstance(rsp, foundation.Status):
            LOGGER.error(
                "got %s status for discover_attribute starting %s", rsp, attr_id
            )
            break
        for attr_rec in rsp:
            attr_id = attr_rec.attrid
            attr_name = cluster.attributes.get(
                attr_rec.attrid, (str(attr_rec.attrid), None)
            )[0]
            attr_type = foundation.DATA_TYPES.get(attr_rec.datatype)
            if attr_type:
                attr_type = [attr_type[1].__name__, attr_type[2].__name__]
            else:
                attr_type = "0x{:02x}".format(attr_rec.datatype)
            try:
                access = foundation.AttributeAccessControl(attr_rec.acl).name
            except ValueError:
                access = "undefined"

            result[attr_id] = {
                "attribute_id": "0x{:04x}".format(attr_id),
                "attribute_name": attr_name,
                "value_type": attr_type,
                "access": access,
            }
            attr_id += 1
        await asyncio.sleep(0.2)

    to_read = list(result.keys())
    LOGGER.debug("Reading attrs: %s", to_read)
    chunk, to_read = to_read[:4], to_read[4:]
    while chunk:
        try:
            success, failed = await read_attr(cluster, chunk)
            LOGGER.debug("Reading attr success: %s, failed %s", success, failed)
            for attr_id, value in success.items():
                if isinstance(value, bytes):
                    try:
                        value = value.split(b"\x00")[0].decode().strip()
                    except UnicodeDecodeError:
                        value = value.hex()
                result[attr_id]["attribute_value"] = value
        except DeliveryError as exc:
            LOGGER.error("Couldn't read attr_id %i: %s", attr_id, exc)
        chunk, to_read = to_read[:4], to_read[4:]
        await asyncio.sleep(0.3)

    return OrderedDict(
        [("0x{:04x}".format(a_id), result[a_id]) for a_id in sorted(result)]
    )


async def discover_commands_received(cluster, is_server, manufacturer=None):
    from zigpy.zcl.foundation import Status

    LOGGER.debug("Discovering commands received")
    direction = "received" if is_server else "generated"
    result = {}
    cmd_id = 0
    done = False

    while not done:
        try:
            done, rsp = await wrapper(
                cluster.discover_commands_received,
                cmd_id,
                16,
                manufacturer=manufacturer,
            )
            await asyncio.sleep(0.3)
        except DeliveryError as ex:
            LOGGER.error(
                "Failed to discover commands starting %s. Error: {}".format(cmd_id, ex)
            )
            break
        if isinstance(rsp, Status):
            LOGGER.error(
                "got %s status for discover_attribute starting %s", rsp, cmd_id
            )
            break
        for cmd_id in rsp:
            cmd_data = cluster.server_commands.get(
                cmd_id, (str(cmd_id), "not_in_zcl", None)
            )
            cmd_name, cmd_args, _ = cmd_data
            if not isinstance(cmd_args, str):
                cmd_args = [arg.__name__ for arg in cmd_args]
            key = "0x{:02x}".format(cmd_id)
            result[key] = {
                "command_id": "0x{:02x}".format(cmd_id),
                "command_name": cmd_name,
                "command_arguments": cmd_args,
            }
            cmd_id += 1
        await asyncio.sleep(0.2)
    return OrderedDict(sorted(result.items(), key=lambda k: k[0]))


async def discover_commands_generated(cluster, is_server, manufacturer=None):
    from zigpy.zcl.foundation import Status

    direction = "generated" if is_server else "received"
    result = {}
    cmd_id = 0
    done = False

    while not done:
        try:
            done, rsp = await wrapper(
                cluster.discover_commands_generated,
                cmd_id,
                16,
                manufacturer=manufacturer,
            )
            await asyncio.sleep(0.3)
        except DeliveryError as ex:
            LOGGER.error(
                "Failed to discover commands starting %s. Error: {}".format(cmd_id, ex)
            )
            break
        if isinstance(rsp, Status):
            LOGGER.error(
                "got %s status for discover_attribute starting %s", rsp, cmd_id
            )
            break
        for cmd_id in rsp:
            cmd_data = cluster.client_commands.get(
                cmd_id, (str(cmd_id), "not_in_zcl", None)
            )
            cmd_name, cmd_args, _ = cmd_data
            if not isinstance(cmd_args, str):
                cmd_args = [arg.__name__ for arg in cmd_args]
            key = "0x{:02x}".format(cmd_id)
            result[key] = {
                "command_id": "0x{:02x}".format(cmd_id),
                "command_Name": cmd_name,
                "command_args": cmd_args,
            }
            cmd_id += 1
        await asyncio.sleep(0.2)
    return OrderedDict(sorted(result.items(), key=lambda k: k[0]))


async def scan_device(app, listener, ieee, cmd, data, service):
    if ieee is None:
        LOGGER.error("missine ieee")
        return

    LOGGER.debug("running 'scan_device' command: %s", service)
    device = app.get_device(ieee=ieee)
    scan = await scan_results(device)

    model = scan.get("model")
    manufacturer = scan.get("manufacturer")
    if model is not None and manufacturer is not None:
        ieee_tail = "".join(["%02x" % (o,) for o in ieee[-4:]])
        file_name = "{}_{}_{}_scan_results.txt".format(model, manufacturer, ieee_tail)
    else:
        ieee_tail = "".join(["%02x" % (o,) for o in ieee])
        file_name = "{}_scan_results.txt".format(ieee_tail)

    file_name = os.path.join(listener._hass.config.config_dir, "scans", file_name)
    save_json(file_name, scan)
    LOGGER.debug("Finished writing scan results int '%s'", file_name)


async def get_node_desc(app, listener, ieee, cmd, data, service):
    for device in app.devices.values():
        model = manufacturer = None
        ep = next((e for epid, e in device.endpoints.items() if epid), None)
        if ep is not None:
            model = ep.model
            manufacturer = ep.manufacturer
        LOGGER.info(
            "%s: %s %s is a %s. Mains powered=%s",
            device.ieee,
            manufacturer,
            model,
            device.node_desc.logical_type,
            device.node_desc.is_mains_powered,
        )


async def discover_device_endpoints(app, listener, ieee, cmd, data, service):
    """Discover devices endpoints."""
    if ieee is None:
        LOGGER.error("Missing IEEE address")
        return

    dev = app.get_device(ieee=ieee)
    LOGGER.info("[0x%04x] Discovering endpoints", dev.nwk)
    try:
        epr = await dev.zdo.Active_EP_req(dev.nwk, tries=3, delay=2)
        if epr[0] != 0:
            raise Exception("Endpoint request failed: %s", epr)
    except Exception:
        dev.exception(
            "Failed ZDO request during device initialization", exc_info=True
        )
        return

    LOGGER.info("[0x%04x] Discovered endpoints: %s", dev.nwk, epr[2])
    endpoints = []

    for endpoint_id in epr[2]:
        sdr = await dev.zdo.Simple_Desc_req(
            dev.nwk, endpoint_id, tries=3, delay=2
        )
        LOGGER.info("[0x%04x] Endpoint %s info: %s", dev.nwk, endpoint_id, sdr)
        endpoints.append(sdr[2])

    LOGGER.info("[0x%04x] Discovered endpoints: %s", dev.nwk, epr[2])
    for ep in endpoints:
        LOGGER.info("[0x%04x] Endpoint info: %s", dev.nwk, ep)

