#!/usr/bin/env python3
"""Balboa WiFi spa → MQTT bridge with Home Assistant discovery.

Publishes spa state following the Homie 4.0 convention and emits HA MQTT
discovery configs so that Home Assistant auto-creates all entities.

Usage:
    python bwa_mqtt_bridge.py mqtt://user:pass@broker:1883 tcp://192.168.1.x
    python bwa_mqtt_bridge.py mqtt://broker tcp://192.168.1.x
    python bwa_mqtt_bridge.py mqtt://broker          # auto-discover spa

Environment variables:
    BWA_MQTT_URI      — MQTT broker URI (overrides positional arg)
    BWA_SPA_URI       — Spa URI (overrides positional arg)
    BWA_DEVICE_ID     — Homie device ID (default: bwa)
    BWA_ROOT_TOPIC    — Homie root topic (default: homie)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import aiomqtt

import bwa.messages  # noqa: F401 — side-effect: registers all message classes
from bwa.client import BWAClient
from bwa.discovery import discover
from bwa import messages as msgs

logger = logging.getLogger("bwa_mqtt_bridge")

VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Minimal Homie 4.0 device implementation
# ---------------------------------------------------------------------------

@dataclass
class HomieProperty:
    prop_id: str
    name: str
    datatype: str           # boolean, integer, float, string, enum, color
    value: Any = None
    settable: bool = False
    retained: bool = True
    unit: str = ""
    format: str = ""        # e.g. "0:100" for numbers, "a,b,c" for enums
    on_set: Optional[Callable[[str], None]] = field(default=None, repr=False)
    # HA-specific
    hass_component: str = ""          # sensor, number, switch, select, binary_sensor, light, button
    hass_extra: dict = field(default_factory=dict)


@dataclass
class HomieNode:
    node_id: str
    name: str
    node_type: str
    properties: dict[str, HomieProperty] = field(default_factory=dict)

    def add(self, prop: HomieProperty) -> HomieProperty:
        self.properties[prop.prop_id] = prop
        return prop


class HomieDevice:
    def __init__(self, device_id: str, name: str, mqtt: aiomqtt.Client,
                 root: str = "homie", hass_device: dict | None = None):
        self.device_id = device_id
        self.name = name
        self.mqtt = mqtt
        self.root = root
        self.nodes: dict[str, HomieNode] = {}
        self.hass_device = hass_device or {}

    @property
    def base(self) -> str:
        return f"{self.root}/{self.device_id}"

    def add_node(self, node: HomieNode) -> HomieNode:
        self.nodes[node.node_id] = node
        return node

    # ------------------------------------------------------------------

    async def publish(self, topic: str, payload: Any, *, retain: bool = True) -> None:
        await self.mqtt.publish(topic, str(payload), retain=retain, qos=1)

    async def publish_structure(self) -> None:
        base = self.base
        await self.publish(f"{base}/$homie", "4.0.0")
        await self.publish(f"{base}/$name", self.name)
        await self.publish(f"{base}/$state", "init")
        await self.publish(f"{base}/$nodes", ",".join(self.nodes))

        for node in self.nodes.values():
            nb = f"{base}/{node.node_id}"
            await self.publish(f"{nb}/$name", node.name)
            await self.publish(f"{nb}/$type", node.node_type)
            await self.publish(f"{nb}/$properties", ",".join(node.properties))

            for prop in node.properties.values():
                pb = f"{nb}/{prop.prop_id}"
                await self.publish(f"{pb}/$name", prop.name)
                await self.publish(f"{pb}/$datatype", prop.datatype)
                await self.publish(f"{pb}/$settable", "true" if prop.settable else "false")
                await self.publish(f"{pb}/$retained", "true" if prop.retained else "false")
                if prop.unit:
                    await self.publish(f"{pb}/$unit", prop.unit)
                if prop.format:
                    await self.publish(f"{pb}/$format", prop.format)
                if prop.value is not None:
                    await self.publish_value(node.node_id, prop.prop_id, prop.value)

        await self.publish(f"{base}/$state", "ready")

    async def publish_value(self, node_id: str, prop_id: str, value: Any) -> None:
        prop = self.nodes[node_id].properties[prop_id]
        prop.value = value
        topic = f"{self.base}/{node_id}/{prop_id}"
        payload = _encode_value(value, prop.datatype)
        await self.publish(topic, payload)

    async def subscribe_settable(self) -> None:
        await self.mqtt.subscribe(f"{self.base}/+/+/set", qos=1)

    async def handle_set(self, topic: str, payload: str) -> None:
        """Route an incoming /set message to the correct property callback."""
        parts = topic.split("/")
        # expected: root / device_id / node_id / prop_id / set
        if len(parts) < 5 or parts[-1] != "set":
            return
        node_id = parts[-3]
        prop_id = parts[-2]
        node = self.nodes.get(node_id)
        if node is None:
            return
        prop = node.properties.get(prop_id)
        if prop is None or prop.on_set is None:
            return
        logger.debug("set %s/%s = %r", node_id, prop_id, payload)
        prop.on_set(payload)

    # ------------------------------------------------------------------
    # Home Assistant discovery helpers
    # ------------------------------------------------------------------

    async def publish_hass_discovery(self) -> None:
        device_info = {
            "identifiers": [self.device_id],
            **self.hass_device,
        }
        for node in self.nodes.values():
            for prop in node.properties.values():
                if not prop.hass_component:
                    continue
                uid = f"{self.device_id}_{node.node_id}_{prop.prop_id}"
                state_topic = f"{self.base}/{node.node_id}/{prop.prop_id}"
                config: dict[str, Any] = {
                    "name": prop.name,
                    "unique_id": uid,
                    "device": device_info,
                }
                config.update(prop.hass_extra)

                comp = prop.hass_component
                if comp in ("sensor", "binary_sensor"):
                    config["state_topic"] = state_topic
                    if comp == "binary_sensor":
                        config["payload_on"] = "true"
                        config["payload_off"] = "false"
                elif comp == "switch":
                    config["state_topic"] = state_topic
                    config["command_topic"] = f"{state_topic}/set"
                    config["payload_on"] = "true"
                    config["payload_off"] = "false"
                    config["state_on"] = "true"
                    config["state_off"] = "false"
                elif comp == "number":
                    config["state_topic"] = state_topic
                    config["command_topic"] = f"{state_topic}/set"
                    if prop.format:
                        lo, hi = prop.format.split(":")
                        config["min"] = float(lo)
                        config["max"] = float(hi)
                elif comp == "select":
                    config["state_topic"] = state_topic
                    config["command_topic"] = f"{state_topic}/set"
                    if prop.format:
                        config["options"] = prop.format.split(",")
                elif comp == "light":
                    config["state_topic"] = state_topic
                    config["command_topic"] = f"{state_topic}/set"
                    config["payload_on"] = "true"
                    config["payload_off"] = "false"
                    config["state_value_template"] = (
                        "{% if value == 'true' %}true{% else %}false{% endif %}"
                    )
                elif comp == "button":
                    config["command_topic"] = f"{state_topic}/set"
                elif comp == "water_heater":
                    pass  # hass_extra provides all fields

                discovery_topic = f"homeassistant/{comp}/{self.device_id}/{uid}/config"
                await self.mqtt.publish(
                    discovery_topic,
                    json.dumps(config),
                    retain=True,
                    qos=1,
                )


def _encode_value(value: Any, datatype: str) -> str:
    if datatype == "boolean":
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class MQTTBridge:
    def __init__(self, spa: BWAClient, mqtt: aiomqtt.Client,
                 device_id: str = "bwa", root_topic: str = "homie"):
        self.spa = spa
        self.mqtt = mqtt
        self.device_id = device_id
        self.root = root_topic
        self.homie: HomieDevice | None = None

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _make_homie(self) -> HomieDevice:
        spa = self.spa
        cfg = spa.configuration   # ControlConfiguration2
        status = spa.status

        hd = HomieDevice(
            device_id=self.device_id,
            name="BWA Link",
            mqtt=self.mqtt,
            root=self.root,
            hass_device={
                "manufacturer": "Balboa Water Group",
                "model": spa.model,
                "sw_version": VERSION,
                "name": "BWA Link",
            },
        )

        # ---- spa node ----
        spa_node = HomieNode("spa", "Hot Tub", spa.model)
        hd.add_node(spa_node)

        scale = status.temperature_scale
        scale_char = "C" if scale == "celsius" else "F"
        temp_unit = f"°{scale_char}"

        if scale == "celsius":
            cur_fmt, tgt_fmt = "0:42", "10:40"
            temp_step = 0.5
        else:
            cur_fmt, tgt_fmt = "32:108", "50:106"
            temp_step = 1.0

        spa_node.add(HomieProperty(
            "current-temperature", "Current Water Temperature", "float",
            value=status.current_temperature,
            unit=temp_unit, format=cur_fmt,
            hass_component="sensor",
            hass_extra={"device_class": "temperature", "unit_of_measurement": temp_unit},
        ))
        spa_node.add(HomieProperty(
            "target-temperature", "Target Water Temperature", "float",
            value=status.target_temperature,
            settable=True, unit=temp_unit, format=tgt_fmt,
            hass_component="number",
            hass_extra={
                "unit_of_measurement": temp_unit,
                "device_class": "temperature",
                "step": temp_step,
                "icon": "mdi:thermometer",
            },
            on_set=lambda v: asyncio.create_task(
                spa.set_target_temperature(float(v))
            ),
        ))

        # Water heater entity for HA
        spa_node.add(HomieProperty(
            "water-heater", "Hot Tub", "string",
            value=None, retained=False,
            hass_component="water_heater",
            hass_extra={
                "current_temperature_topic": f"{hd.base}/spa/current-temperature",
                "temperature_command_topic": f"{hd.base}/spa/target-temperature/set",
                "temperature_state_topic": f"{hd.base}/spa/target-temperature",
                "mode_command_topic": f"{hd.base}/spa/heating/set",
                "mode_state_topic": f"{hd.base}/spa/heating",
                "mode_state_template": (
                    "{% if value == 'true' %}electric{% else %}off{% endif %}"
                ),
                "mode_command_template": (
                    "{% if value == 'electric' %}true{% else %}false{% endif %}"
                ),
                "modes": ["off", "electric"],
                "min_temp": float(tgt_fmt.split(":")[0]),
                "max_temp": float(tgt_fmt.split(":")[1]),
                "precision": temp_step,
                "temperature_unit": scale_char,
                "icon": "mdi:hot-tub",
            },
        ))

        spa_node.add(HomieProperty(
            "heating", "Heating", "boolean",
            value=status.heating,
            hass_component="binary_sensor",
            hass_extra={"device_class": "heat", "icon": "mdi:fire"},
        ))
        spa_node.add(HomieProperty(
            "heating-mode", "Heating Mode", "enum",
            value=status.heating_mode,
            settable=True,
            format="ready,rest,ready_in_rest",
            hass_component="select",
            hass_extra={"icon": "mdi:cog-play"},
            on_set=lambda v: asyncio.create_task(
                spa.set_heating_mode(v) if v != "toggle" else spa.toggle_heating_mode()
            ),
        ))
        spa_node.add(HomieProperty(
            "temperature-range", "Temperature Range", "enum",
            value=status.temperature_range,
            settable=True,
            format="high,low",
            hass_component="select",
            hass_extra={"icon": "mdi:thermometer-lines"},
            on_set=lambda v: asyncio.create_task(
                spa.set_temperature_range(v) if v != "toggle" else spa.toggle_temperature_range()
            ),
        ))
        spa_node.add(HomieProperty(
            "temperature-scale", "Temperature Scale", "enum",
            value=status.temperature_scale,
            settable=True,
            format="fahrenheit,celsius",
            hass_component="select",
            on_set=lambda v: asyncio.create_task(spa.set_temperature_scale(v)),
        ))
        spa_node.add(HomieProperty(
            "hold", "Hold", "boolean",
            value=status.hold,
            settable=True,
            hass_component="switch",
            hass_extra={"icon": "mdi:pause-octagon"},
            on_set=lambda v: asyncio.create_task(
                spa.toggle_hold() if v == "toggle" else spa.set_hold(v == "true")
            ),
        ))
        spa_node.add(HomieProperty(
            "priming", "Priming", "boolean",
            value=status.priming,
            hass_component="binary_sensor",
            hass_extra={"icon": "mdi:fast-forward"},
        ))
        spa_node.add(HomieProperty(
            "notification", "Notification", "enum",
            value=status.notification or "none",
            format="ph,filter,sanitizer,none",
            hass_component="sensor",
        ))
        spa_node.add(HomieProperty(
            "twenty-four-hour-time", "24 Hour Time", "boolean",
            value=status.twenty_four_hour_time,
            settable=True,
            hass_component="switch",
            hass_extra={"icon": "mdi:timer-cog"},
            on_set=lambda v: asyncio.create_task(_set_time_format(spa, v == "true")),
        ))

        # Circulation pump
        if cfg.circulation_pump:
            spa_node.add(HomieProperty(
                "circulation-pump", "Circulation Pump", "boolean",
                value=status.circulation_pump,
                hass_component="binary_sensor",
                hass_extra={"device_class": "running", "icon": "mdi:sync"},
            ))

        # Blower
        if cfg.blower == 1:
            spa_node.add(HomieProperty(
                "blower", "Blower", "boolean",
                value=bool(status.blower),
                settable=True,
                hass_component="switch",
                hass_extra={"icon": "mdi:chart-bubble"},
                on_set=lambda v: asyncio.create_task(
                    spa.toggle_blower() if v == "toggle"
                    else spa.set_blower(v == "true")
                ),
            ))
        elif cfg.blower > 1:
            spa_node.add(HomieProperty(
                "blower", "Blower", "integer",
                value=status.blower,
                settable=True,
                format=f"0:{cfg.blower}",
                hass_component="number",
                hass_extra={"icon": "mdi:chart-bubble"},
                on_set=lambda v: asyncio.create_task(
                    spa.toggle_blower() if v == "toggle"
                    else spa.set_blower(int(v))
                ),
            ))

        # Mister
        if cfg.mister:
            spa_node.add(HomieProperty(
                "mister", "Mister", "boolean",
                value=status.mister,
                settable=True,
                hass_component="switch",
                hass_extra={"icon": "mdi:sprinkler-fire"},
                on_set=lambda v: asyncio.create_task(
                    spa.toggle_mister() if v == "toggle"
                    else spa.set_mister(v == "true")
                ),
            ))

        # Pumps
        single_pump = sum(1 for s in cfg.pumps if s != 0) == 1
        for i, max_speed in enumerate(cfg.pumps):
            if max_speed == 0:
                continue
            pump_name = "Pump" if single_pump else f"Pump {i + 1}"
            pid = f"pump{i + 1}"
            idx = i  # capture for lambda
            if max_speed == 1:
                spa_node.add(HomieProperty(
                    pid, pump_name, "boolean",
                    value=bool(status.pumps[i]),
                    settable=True,
                    hass_component="switch",
                    hass_extra={"icon": "mdi:chart-bubble"},
                    on_set=lambda v, ix=idx: asyncio.create_task(
                        spa.toggle_pump(ix) if v == "toggle"
                        else spa.set_pump(ix, v == "true")
                    ),
                ))
            else:
                spa_node.add(HomieProperty(
                    pid, pump_name, "integer",
                    value=status.pumps[i],
                    settable=True,
                    format=f"0:{max_speed}",
                    hass_component="number",
                    hass_extra={"icon": "mdi:chart-bubble"},
                    on_set=lambda v, ix=idx: asyncio.create_task(
                        spa.toggle_pump(ix) if v == "toggle"
                        else spa.set_pump(ix, int(v))
                    ),
                ))

        # Lights
        single_light = sum(1 for l in cfg.lights if l) == 1
        for i, exists in enumerate(cfg.lights):
            if not exists:
                continue
            light_name = "Lights" if single_light else f"Lights {i + 1}"
            lid = f"light{i + 1}"
            idx = i
            spa_node.add(HomieProperty(
                lid, light_name, "boolean",
                value=status.lights[i],
                settable=True,
                hass_component="light",
                hass_extra={"icon": "mdi:car-parking-lights"},
                on_set=lambda v, ix=idx: asyncio.create_task(
                    spa.toggle_light(ix) if v == "toggle"
                    else spa.set_light(ix, v == "true")
                ),
            ))

        # Aux
        for i, exists in enumerate(cfg.aux):
            if not exists:
                continue
            idx = i
            spa_node.add(HomieProperty(
                f"aux{i + 1}", f"Auxiliary {i + 1}", "boolean",
                value=status.aux[i],
                settable=True,
                hass_component="switch",
                on_set=lambda v, ix=idx: asyncio.create_task(
                    spa.toggle_aux(ix) if v == "toggle"
                    else spa.set_aux(ix, v == "true")
                ),
            ))

        # Command buttons
        spa_node.add(HomieProperty(
            "command", "Send Command", "enum",
            settable=True, retained=False,
            format="normal_operation,clear_notification,soak",
            hass_component="button",
            hass_extra={"payload_press": "normal_operation"},
            on_set=lambda v: asyncio.create_task(spa.toggle_item(v)),
        ))

        # ---- filter cycle nodes ----
        for cycle_num in range(1, 3):
            fc_node = HomieNode(
                f"filter-cycle{cycle_num}",
                f"Filter Cycle {cycle_num}",
                "Filter Cycle",
            )
            hd.add_node(fc_node)

            fc = spa.filter_cycles
            sh = getattr(fc, f"cycle{cycle_num}_start_hour")
            sm = getattr(fc, f"cycle{cycle_num}_start_minute")
            dur = getattr(fc, f"cycle{cycle_num}_duration")
            running = status.filter_cycles[cycle_num - 1]
            cn = cycle_num  # capture

            fc_node.add(HomieProperty(
                "running", "Running", "boolean",
                value=running,
                hass_component="binary_sensor",
                hass_extra={"icon": "mdi:air-filter"},
            ))
            fc_node.add(HomieProperty(
                "start-hour", "Start Hour", "integer",
                value=sh, settable=True,
                unit="hours", format="0:23",
                hass_component="number",
                hass_extra={"icon": "mdi:clock"},
                on_set=lambda v, c=cn: asyncio.create_task(
                    _update_filter_cycle(spa, c, "start_hour", int(v))
                ),
            ))
            fc_node.add(HomieProperty(
                "start-minute", "Start Minute", "integer",
                value=sm, settable=True,
                unit="minutes", format="0:59",
                hass_component="number",
                hass_extra={"icon": "mdi:clock"},
                on_set=lambda v, c=cn: asyncio.create_task(
                    _update_filter_cycle(spa, c, "start_minute", int(v))
                ),
            ))
            fc_node.add(HomieProperty(
                "duration", "Duration", "integer",
                value=dur, settable=True,
                unit="minutes", format="0:1439",
                hass_component="number",
                hass_extra={"icon": "mdi:clock"},
                on_set=lambda v, c=cn: asyncio.create_task(
                    _update_filter_cycle(spa, c, "duration", int(v))
                ),
            ))
            if cycle_num == 2:
                enabled = fc.cycle2_enabled
                fc_node.add(HomieProperty(
                    "enabled", "Enabled", "boolean",
                    value=enabled, settable=True,
                    hass_component="switch",
                    hass_extra={"icon": "mdi:filter-check"},
                    on_set=lambda v: asyncio.create_task(
                        _update_filter_cycle(spa, 2, "enabled", v == "true")
                    ),
                ))

        return hd

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        spa = self.spa

        # Wait for full configuration
        logger.info("Waiting for full spa configuration…")
        while not spa.full_configuration:
            message = await spa.poll()
            if isinstance(message, msgs.Status):
                if not spa.control_configuration:
                    await spa.request_control_info()
                if not spa.configuration:
                    await spa.request_control_info2()
                if not spa.filter_cycles:
                    await spa.request_filter_configuration()

        logger.info("Full configuration received. Model: %s", spa.model)

        self.homie = self._make_homie()
        await self.homie.publish_structure()
        await self.homie.publish_hass_discovery()
        await self.homie.subscribe_settable()

        logger.info("BWA MQTT bridge running (version %s)", VERSION)

        # Concurrently: poll spa and handle incoming MQTT commands
        await asyncio.gather(
            self._spa_loop(),
            self._mqtt_loop(),
        )

    async def _spa_loop(self) -> None:
        """Continuously poll the spa and push updates to MQTT."""
        spa = self.spa
        homie = self.homie
        assert homie is not None

        while True:
            try:
                message = await spa.poll()
            except ConnectionError as exc:
                logger.error("Spa connection lost: %s — reconnecting in 10s", exc)
                await asyncio.sleep(10)
                await spa.connect()
                await spa.request_configuration()
                await spa.request_filter_configuration()
                continue

            if isinstance(message, msgs.Status):
                await self._publish_status(message)

                # Keep spa clock in sync (allow 1 min skew)
                import datetime
                now = datetime.datetime.now()
                spa_min = message.hour * 60 + message.minute
                now_min = now.hour * 60 + now.minute
                diff = min(abs(spa_min - now_min), 1440 - abs(spa_min - now_min))
                if diff > 1:
                    logger.info(
                        "Spa time %02d:%02d, local %02d:%02d — correcting",
                        message.hour, message.minute, now.hour, now.minute,
                    )
                    await spa.set_time(now.hour, now.minute,
                                       twenty_four_hour_time=message.twenty_four_hour_time)

            elif isinstance(message, msgs.FilterCycles):
                await self._publish_filter_cycles(message)

    async def _mqtt_loop(self) -> None:
        """Handle incoming MQTT /set commands."""
        homie = self.homie
        assert homie is not None
        async for message in self.mqtt.messages:
            await homie.handle_set(str(message.topic), message.payload.decode())

    async def _publish_status(self, status: msgs.Status) -> None:
        homie = self.homie
        spa = self.spa
        assert homie is not None
        sn = homie.nodes.get("spa")
        if not sn:
            return

        async def pub(pid: str, val: Any) -> None:
            if pid in sn.properties:
                await homie.publish_value("spa", pid, val)

        await pub("current-temperature", status.current_temperature)
        await pub("target-temperature", status.target_temperature)
        await pub("heating", status.heating)
        await pub("heating-mode", status.heating_mode)
        await pub("temperature-range", status.temperature_range)
        await pub("temperature-scale", status.temperature_scale)
        await pub("hold", status.hold)
        await pub("priming", status.priming)
        await pub("notification", status.notification or "none")
        await pub("twenty-four-hour-time", status.twenty_four_hour_time)

        if spa.configuration:
            cfg = spa.configuration
            if cfg.circulation_pump:
                await pub("circulation-pump", status.circulation_pump)
            if cfg.blower == 1:
                await pub("blower", bool(status.blower))
            elif cfg.blower > 1:
                await pub("blower", status.blower)
            if cfg.mister:
                await pub("mister", status.mister)
            for i, max_speed in enumerate(cfg.pumps):
                if max_speed == 0:
                    continue
                pid = f"pump{i + 1}"
                val = bool(status.pumps[i]) if max_speed == 1 else status.pumps[i]
                await pub(pid, val)
            for i, exists in enumerate(cfg.lights):
                if exists:
                    await pub(f"light{i + 1}", status.lights[i])
            for i, exists in enumerate(cfg.aux):
                if exists:
                    await pub(f"aux{i + 1}", status.aux[i])

        # Filter cycle running state comes from status
        for i in range(2):
            cn = f"filter-cycle{i + 1}"
            if cn in homie.nodes:
                prop = homie.nodes[cn].properties.get("running")
                if prop is not None:
                    await homie.publish_value(cn, "running", status.filter_cycles[i])

    async def _publish_filter_cycles(self, fc: msgs.FilterCycles) -> None:
        homie = self.homie
        assert homie is not None
        for i, cn in enumerate(("filter-cycle1", "filter-cycle2"), start=1):
            node = homie.nodes.get(cn)
            if node is None:
                continue
            sh = getattr(fc, f"cycle{i}_start_hour")
            sm = getattr(fc, f"cycle{i}_start_minute")
            dur = getattr(fc, f"cycle{i}_duration")
            await homie.publish_value(cn, "start-hour", sh)
            await homie.publish_value(cn, "start-minute", sm)
            await homie.publish_value(cn, "duration", dur)
            if i == 2 and "enabled" in node.properties:
                await homie.publish_value(cn, "enabled", fc.cycle2_enabled)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _set_time_format(spa: BWAClient, twenty_four_hour: bool) -> None:
    import datetime
    now = datetime.datetime.now()
    await spa.set_time(now.hour, now.minute, twenty_four_hour_time=twenty_four_hour)


async def _update_filter_cycle(spa: BWAClient, cycle_num: int, attr: str, value) -> None:
    if spa.filter_cycles is None:
        return
    import copy
    new_fc = copy.copy(spa.filter_cycles)
    setattr(new_fc, f"cycle{cycle_num}_{attr}", value)
    await spa.update_filter_cycles(new_fc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(description="BWA MQTT Bridge")
    parser.add_argument("mqtt_uri", nargs="?", help="MQTT broker URI, e.g. mqtt://broker:1883")
    parser.add_argument("spa_uri", nargs="?", help="Spa URI, e.g. tcp://192.168.1.x or /dev/ttyUSB0")
    parser.add_argument("--device-id", default=os.getenv("BWA_DEVICE_ID", "bwa"))
    parser.add_argument("--root-topic", default=os.getenv("BWA_ROOT_TOPIC", "homie"))
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args()

    level = logging.WARNING
    if args.verbose == 1:
        level = logging.INFO
    elif args.verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    mqtt_uri_str = args.mqtt_uri or os.getenv("BWA_MQTT_URI")
    spa_uri_str = args.spa_uri or os.getenv("BWA_SPA_URI")

    if not mqtt_uri_str:
        parser.error("MQTT URI required (positional arg or BWA_MQTT_URI env var)")

    # Parse MQTT URI
    mqtt_parsed = urlparse(mqtt_uri_str)
    mqtt_host = mqtt_parsed.hostname or "localhost"
    mqtt_port = mqtt_parsed.port or 1883
    mqtt_user = mqtt_parsed.username
    mqtt_pass = mqtt_parsed.password

    # Discover spa if not provided
    if not spa_uri_str:
        logger.info("No spa URI given, discovering…")
        spas = await discover(timeout=10)
        if not spas:
            print("ERROR: Could not find any spa on the network.", file=sys.stderr)
            sys.exit(1)
        spa_ip = next(iter(spas))
        spa_uri_str = f"tcp://{spa_ip}"
        logger.info("Discovered spa at %s", spa_ip)

    # Ensure tcp:// scheme for plain IPs
    if not spa_uri_str.startswith(("/", "rfc2217", "telnet")) and "://" not in spa_uri_str:
        spa_uri_str = f"tcp://{spa_uri_str}"

    logger.info("Connecting to spa at %s", spa_uri_str)
    spa = BWAClient(spa_uri_str)
    await spa.connect()
    await spa.request_configuration()
    await spa.request_filter_configuration()

    will = aiomqtt.Will(
        topic=f"{args.root_topic}/{args.device_id}/$state",
        payload="lost",
        retain=True,
        qos=1,
    )

    logger.info("Connecting to MQTT broker at %s:%s", mqtt_host, mqtt_port)
    async with aiomqtt.Client(
        hostname=mqtt_host,
        port=mqtt_port,
        username=mqtt_user,
        password=mqtt_pass,
        will=will,
        keepalive=60,
    ) as mqtt_client:
        bridge = MQTTBridge(
            spa=spa,
            mqtt=mqtt_client,
            device_id=args.device_id,
            root_topic=args.root_topic,
        )
        await bridge.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
