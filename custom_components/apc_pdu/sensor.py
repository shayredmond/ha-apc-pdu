"""Sensor platform — PDU load, rPDU2 power/energy metering, and identity."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LOAD_STATE_MAP
from .coordinator import APCPDUCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create load and identity sensors for this PDU."""
    coordinator: APCPDUCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        APCPDUCurrentSensor(coordinator),
        APCPDULoadStateSensor(coordinator),
        APCPDUIdentSensor(coordinator, "PDU Name", "name", "mdi:tag-text"),
        APCPDUIdentSensor(coordinator, "Manufacture Date", "manufacture_date", "mdi:calendar"),
        APCPDUIdentSensor(coordinator, "Number of Outlets", "num_outlets", "mdi:counter"),
    ]

    # Metering sensors are only created for values the PDU answered during the
    # first refresh, so switched-only models (e.g. AP7920) get no dead entities.
    for spec in DEVICE_METER_SPECS:
        if coordinator.device_power.get(spec.key) is not None:
            entities.append(APCPDUDeviceMeterSensor(coordinator, spec))

    for outlet, values in sorted(coordinator.outlet_metering.items()):
        for spec in OUTLET_METER_SPECS:
            if values.get(spec.key) is not None:
                entities.append(APCPDUOutletMeterSensor(coordinator, outlet, spec))

    async_add_entities(entities)


@dataclass(frozen=True, kw_only=True)
class MeterSpec:
    """Static description of one metering sensor."""

    key: str
    name: str
    device_class: SensorDeviceClass | None
    unit: str | None
    state_class: SensorStateClass
    precision: int | None = None
    icon: str | None = None
    enabled_default: bool = True
    diagnostic: bool = False


DEVICE_METER_SPECS: tuple[MeterSpec, ...] = (
    MeterSpec(
        key="power", name="Power",
        device_class=SensorDeviceClass.POWER, unit=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT, precision=0,
    ),
    MeterSpec(
        key="energy", name="Energy",
        device_class=SensorDeviceClass.ENERGY, unit=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING, precision=1,
    ),
    MeterSpec(
        key="apparent_power", name="Apparent Power",
        device_class=SensorDeviceClass.APPARENT_POWER, unit=UnitOfApparentPower.VOLT_AMPERE,
        state_class=SensorStateClass.MEASUREMENT, precision=0,
    ),
    MeterSpec(
        key="power_factor", name="Power Factor",
        device_class=SensorDeviceClass.POWER_FACTOR, unit=None,
        state_class=SensorStateClass.MEASUREMENT, precision=2,
        enabled_default=False, diagnostic=True,
    ),
)

OUTLET_METER_SPECS: tuple[MeterSpec, ...] = (
    MeterSpec(
        key="power", name="Power",
        device_class=SensorDeviceClass.POWER, unit=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT, precision=0,
    ),
    MeterSpec(
        key="energy", name="Energy",
        device_class=SensorDeviceClass.ENERGY, unit=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING, precision=1,
    ),
    MeterSpec(
        key="current", name="Current",
        device_class=SensorDeviceClass.CURRENT, unit=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT, precision=1,
        enabled_default=False,
    ),
)


class _APCPDUBaseSensor(CoordinatorEntity[APCPDUCoordinator], SensorEntity):
    """Shared base for PDU-level sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: APCPDUCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the same device as the outlet switches."""
        return self.coordinator.device_info


class APCPDUCurrentSensor(_APCPDUBaseSensor):
    """Reports the total current draw of the PDU in Amps."""

    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:current-ac"

    def __init__(self, coordinator: APCPDUCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_load_amps"
        self._attr_name = "Current"

    @property
    def available(self) -> bool:
        """Unavailable if the coordinator failed or the PDU doesn't support this OID."""
        return self.coordinator.last_update_success and self.coordinator.load_amps is not None

    @property
    def native_value(self) -> float | None:
        """Return the current draw in Amps."""
        return self.coordinator.load_amps


class APCPDULoadStateSensor(_APCPDUBaseSensor):
    """Reports the PDU load state (Normal, Near Overload, Overload, Low Load)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:gauge"
    _attr_options = list(LOAD_STATE_MAP.values())

    def __init__(self, coordinator: APCPDUCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_load_state"
        self._attr_name = "Load Status"

    @property
    def available(self) -> bool:
        """Unavailable if the coordinator failed or the PDU doesn't support this OID."""
        return self.coordinator.last_update_success and self.coordinator.load_state is not None

    @property
    def native_value(self) -> str | None:
        """Return the human-readable load state string."""
        return LOAD_STATE_MAP.get(self.coordinator.load_state)


class APCPDUIdentSensor(_APCPDUBaseSensor):
    """Exposes a single PDU identity string (name, manufacture date) as a diagnostic sensor."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: APCPDUCoordinator,
        name: str,
        ident_key: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._ident_key = ident_key
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_ident_{ident_key}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        """Unavailable when the identity value is empty."""
        return bool(self.coordinator.device_ident.get(self._ident_key))

    @property
    def native_value(self) -> str | None:
        """Return the identity string."""
        return self.coordinator.device_ident.get(self._ident_key) or None


class _APCPDUMeterSensor(_APCPDUBaseSensor):
    """Shared behaviour for metering sensors built from a MeterSpec."""

    def __init__(self, coordinator: APCPDUCoordinator, spec: MeterSpec) -> None:
        super().__init__(coordinator)
        self._spec = spec
        self._attr_device_class = spec.device_class
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_state_class = spec.state_class
        self._attr_suggested_display_precision = spec.precision
        self._attr_entity_registry_enabled_default = spec.enabled_default
        if spec.icon:
            self._attr_icon = spec.icon
        if spec.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    def _value(self) -> float | None:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        """Unavailable if the poll failed or the PDU returned no value."""
        return self.coordinator.last_update_success and self._value() is not None

    @property
    def native_value(self) -> float | None:
        return self._value()


class APCPDUDeviceMeterSensor(_APCPDUMeterSensor):
    """Whole-PDU power, energy, apparent power or power factor (rPDU2DeviceStatus)."""

    def __init__(self, coordinator: APCPDUCoordinator, spec: MeterSpec) -> None:
        super().__init__(coordinator, spec)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_device_{spec.key}"
        self._attr_name = spec.name

    def _value(self) -> float | None:
        return self.coordinator.device_power.get(self._spec.key)


class APCPDUOutletMeterSensor(_APCPDUMeterSensor):
    """Per-outlet power, energy or current (rPDU2OutletMeteredStatus)."""

    def __init__(
        self, coordinator: APCPDUCoordinator, outlet: int, spec: MeterSpec
    ) -> None:
        super().__init__(coordinator, spec)
        self._outlet = outlet
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_outlet_{outlet}_{spec.key}"
        )
        outlet_name = coordinator.outlet_names.get(outlet, "").strip() or f"Outlet {outlet}"
        self._attr_name = f"{outlet_name} {spec.name}"

    def _value(self) -> float | None:
        return self.coordinator.outlet_metering.get(self._outlet, {}).get(self._spec.key)
