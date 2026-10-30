"""Metering sensors: SNMP scaling and entity creation for metered vs switched-only PDUs."""
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.apc_pdu.const import DOMAIN
from custom_components.apc_pdu.snmp import APCPDUClient

OUTLETS = 24


async def test_scaling():
    """Raw values from the AP8659 snmpwalk scale to the expected SI units."""
    client = APCPDUClient.__new__(APCPDUClient)
    client._get_integer_oids = AsyncMock(return_value=[27, 70525, 33, 80])
    assert await client.get_device_power_metrics() == {
        "power": 270, "energy": 7052.5, "apparent_power": 330, "power_factor": 0.8,
    }
    # Outlets 1 and 2 metered, outlet 3 unsupported.
    client._get_integer_oids = AsyncMock(
        return_value=[5, 101, 11553, 0, 0, 0, None, None, None]
    )
    assert await client.get_outlet_metering(3) == {
        1: {"current": 0.5, "power": 101, "energy": 1155.3},
        2: {"current": 0.0, "power": 0, "energy": 0.0},
    }


def _mock_client(metered: bool) -> MagicMock:
    c = MagicMock()
    c.get_num_outlets = AsyncMock(return_value=OUTLETS)
    c.get_outlet_names = AsyncMock(return_value={1: "Outlet 1 - PBS"})
    c.get_device_ident = AsyncMock(return_value={})
    c.get_all_outlet_states = AsyncMock(return_value={o: True for o in range(1, OUTLETS + 1)})
    c.get_load_metrics = AsyncMock(return_value=(1.4, 2))
    if metered:
        c.get_device_power_metrics = AsyncMock(return_value={
            "power": 270, "energy": 7052.5, "apparent_power": 330, "power_factor": 0.8,
        })
        c.get_outlet_metering = AsyncMock(return_value={
            o: {"current": 0.0, "power": 0, "energy": 0.0} for o in range(1, OUTLETS + 1)
        })
    else:
        c.get_device_power_metrics = AsyncMock(return_value=dict.fromkeys(
            ("power", "energy", "apparent_power", "power_factor")))
        c.get_outlet_metering = AsyncMock(return_value={})
    return c


async def _setup(hass, metered: bool) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_NAME: "PDU", CONF_HOST: "192.0.2.1", CONF_PORT: 161,
        "snmp_username": "u", "auth_protocol": "none", "priv_protocol": "none",
    })
    entry.add_to_hass(hass)
    with patch("custom_components.apc_pdu.APCPDUClient", return_value=_mock_client(metered)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _sensor_uids(hass, entry) -> dict[str, er.RegistryEntry]:
    reg = er.async_get(hass)
    return {
        e.unique_id.removeprefix(f"{entry.entry_id}_"): e
        for e in er.async_entries_for_config_entry(reg, entry.entry_id)
        if e.domain == "sensor"
    }


async def test_metered_pdu(hass):
    entry = await _setup(hass, metered=True)
    uids = _sensor_uids(hass, entry)

    for key in ("power", "energy", "apparent_power", "power_factor"):
        assert f"device_{key}" in uids
    assert uids["device_power_factor"].disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert sum(k.startswith("outlet_") for k in uids) == OUTLETS * 3
    assert uids["outlet_23_current"].disabled_by is er.RegistryEntryDisabler.INTEGRATION
    # Pre-existing sensors keep their unique IDs.
    assert {"load_amps", "load_state"} <= uids.keys()

    power = hass.states.get(uids["device_power"].entity_id)
    assert power.state == "270" and power.attributes["unit_of_measurement"] == "W"
    energy = hass.states.get(uids["device_energy"].entity_id)
    assert energy.state == "7052.5"
    assert energy.attributes["state_class"] == "total_increasing"
    assert hass.states.get(uids["outlet_1_power"].entity_id).name == "PDU Outlet 1 - PBS Power"
    assert len(hass.states.async_entity_ids("switch")) == OUTLETS


async def test_switched_only_pdu(hass):
    entry = await _setup(hass, metered=False)
    uids = _sensor_uids(hass, entry)
    assert not any(k.startswith(("device_", "outlet_")) for k in uids)
    assert {"load_amps", "load_state"} <= uids.keys()
    assert len(hass.states.async_entity_ids("switch")) == OUTLETS
