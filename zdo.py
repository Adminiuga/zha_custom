import logging

import zigpy.zdo.types as zdo_t
import zigpy.types as t
import zigpy.device
import zigpy.zdo

LOGGER = logging.getLogger(__name__)


async def leave(app, listener, ieee, cmd, data, service):
    if ieee is None or not data:
        LOGGER.warning("Incorrect parameters for 'zdo.leave' command: %s", service)
        return
    LOGGER.debug(
        "running 'leave' command. Telling 0x%s to remove %s: %s", data, ieee, service
    )
    parent = int(data, base=16)
    parent = app.get_device(nwk=parent)

    res = await parent.zdo.request(zdo_t.ZDOCmd.Mgmt_Leave_req, ieee, 0x02)
    LOGGER.debug("0x%04x: Mgmt_Leave_req: %s", parent.nwk, res)


async def ieee_ping(app, listener, ieee, cmd, data, service):
    if ieee is None:
        LOGGER.warning("Incorrect parameters for 'ieee_ping' command: %s", service)
        return
    dev = app.get_device(ieee=ieee)

    LOGGER.debug("running 'ieee_ping' command to 0x%s", dev.nwk)

    res = await dev.zdo.request(zdo_t.ZDOCmd.IEEE_addr_req, dev.nwk, 0x00, 0x00)
    LOGGER.debug("0x%04x: IEEE_addr_req: %s", dev.nwk, res)


async def join_with_code(app, listener, ieee, cmd, data, service):

    code = b"\xA8\x16\x92\x7F\xB1\x9B\x78\x55\xC1\xD7\x76\x0D\x5C\xAD\x63\x7F\x69\xCC"
    # res = await app.permit_with_key(node, code, 60)
    import bellows.types as bt

    node = t.EUI64.convert("04:cf:8c:df:3c:75:e1:e7")
    link_key = bt.EmberKeyData(b"ZigBeeAlliance09")
    res = await app._ezsp.addTransientLinkKey(node, link_key)
    LOGGER.debug("permit with key: %s", res)
    res = await app.permit(60)


async def update_nwk_id(app, listener, ieee, cmd, data, service):
    """Update NWK id. data contains new NWK id."""
    if data is None:
        LOGGER.error("Need NWK update id in the data")
        return

    nwk_upd_id = t.uint8_t(data)

    await zigpy.device.broadcast(
        app,
        0,
        zdo_t.ZDOCmd.Mgmt_NWK_Update_req,
        0,
        0,
        0x0000,
        0x00,
        0xEE,
        b"\xee"
        + t.Channels.ALL_CHANNELS.serialize()
        + b"\xFF"
        + nwk_upd_id.serialize()
        + b"\x00\x00",
    )

    res = await app._ezsp.getNetworkParameters()
    LOGGER.debug("Network params: %s", res)
