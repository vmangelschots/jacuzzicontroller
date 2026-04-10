# Jacuzzi Controller — Python Port

## Project Goal

Convert the Ruby `balboa_worldwide_app` gem to Python. The Ruby library is unstable (crashes), and the owner prefers Python. The end goal is a Python MQTT bridge that integrates a Balboa Water Group WiFi spa controller with **Home Assistant** via the Homie MQTT convention.

The reference Ruby implementation lives in `./balboa_worldwide_app/`. Read it to understand behavior, but write all new code in Python.

## Architecture

Replicate the Ruby gem's structure in Python:

```
bwa/
  __init__.py
  client.py          # BWAClient class — connection + high-level API
  crc.py             # CRC-8 checksum (initial value 0x02, final XOR 0x02)
  discovery.py       # UDP broadcast discovery on port 30303
  message.py         # Base Message class + parse/serialize logic
  messages/
    status.py                       # Status update (0xff 0xaf 0x13) — sent every ~1s
    configuration.py                # Config response (0x0a 0xbf 0x94)
    configuration_request.py        # Config request (0x0a 0xbf 0x04)
    control_configuration.py        # Control config response (0x0a 0xbf 0x24)
    control_configuration_request.py
    filter_cycles.py                # Filter cycles response (0x0a 0xbf 0x23)
    toggle_item.py                  # Toggle command (0x0a 0xbf 0x11)
    set_target_temperature.py       # Set temp (0x0a 0xbf 0x20)
    set_temperature_scale.py        # Set scale (0x0a 0xbf 0x27)
    set_time.py                     # Set time (0x0a 0xbf 0x21)
    ready.py                        # RS-485 ready signal (0x10 0xbf 0x06)
    nothing_to_send.py
    new_client_clear_to_send.py
    error.py
bwa_mqtt_bridge.py   # Main entry point — MQTT bridge to Home Assistant
```

## Protocol

Reference: `./balboa_worldwide_app/doc/protocol.md`

**Transport:**
- WiFi: TCP on port 4257 — spa sends status updates immediately on connect (~1/s)
- RS-485: 115200,8,N,1 — must wait for `Ready` message before sending
- RFC2217: serial-over-telnet (e.g. via `ser2net`)

**Message frame:**
```
0x7e  LENGTH  SRC  TYPE(2 bytes)  PAYLOAD...  CHECKSUM  0x7e
```
- Start/end byte: `0x7e` (`~`)
- Length = total bytes between the two `0x7e` bytes (i.e. `len(payload) + 5`)
- CRC-8 covers everything between the start byte and the checksum byte

**CRC-8:** initial value `0x02`, final XOR `0x02`. See `./balboa_worldwide_app/lib/bwa/crc.rb`.

**Key message types (hex):**
| Direction | Type bytes | Description |
|-----------|-----------|-------------|
| In  | `ff af 13` | Status update (every ~1s) |
| In  | `0a bf 94` | Configuration response |
| In  | `0a bf 24` | Control configuration response |
| In  | `0a bf 23` | Filter cycles response |
| In  | `10 bf 06` | Ready (RS-485 only) |
| Out | `0a bf 04` | Configuration request |
| Out | `0a bf 11` | Toggle item |
| Out | `0a bf 20` | Set target temperature |
| Out | `0a bf 21` | Set time |
| Out | `0a bf 22` | Settings request (filter/info/panel) |
| Out | `0a bf 27` | Set temperature scale |

**Toggle item IDs:**
- Pump 1-3: `0x04`, `0x05`, `0x06`
- Blower: `0x0C`
- Light 1: `0x11`
- Hold mode: `0x3C`
- Heating mode: `0x51`
- Temperature range: `0x50`

## Connection Modes

```python
# WiFi (most common)
client = BWAClient("tcp://192.168.1.x")

# RS-485 direct
client = BWAClient("/dev/ttyUSB0")

# RFC2217 (serial over network)
client = BWAClient("rfc2217://hostname:2217/")
```

For RS-485 and RFC2217, outgoing messages must be queued and only sent immediately after receiving a `Ready` message.

## Python Implementation Notes

- Use `asyncio` for I/O — the Ruby gem uses blocking I/O with threads but async is the right Python approach
- Use `dataclasses` or plain classes for messages; avoid heavy frameworks
- Bitmask parsing from `status.rb` is the most complex part — translate carefully
- Temperature: stored as integer; divide by 2 if Celsius; `0xff` means unknown/not yet read
- Use `paho-mqtt` or `aiomqtt` for MQTT
- Python equivalent of Ruby's `Homie` gem: implement the [Homie 4.0 convention](https://homieiot.github.io/specification/) manually, or use an existing Python Homie library

## Discovery

UDP broadcast to port 30303. Filter responses by MAC prefix `00:15:27` (Balboa Instruments). Response is two CRLF-terminated lines: hostname then MAC address.

## MQTT / Home Assistant Integration

The bridge publishes topics following the Homie convention under `homie/bwa/`. Home Assistant auto-discovers devices from these topics.

Key HA device entities to expose:
- `current-temperature` (sensor, `device_class: temperature`)
- `target-temperature` (number, settable)
- `heating` (binary sensor)
- `heating-mode` (select: ready/rest/ready_in_rest)
- `temperature-range` (select: high/low)
- `temperature-scale` (select: fahrenheit/celsius)
- `pump1`..`pump6` (number 0–2 or boolean for single-speed)
- `light1`..`light2` (light/switch)
- `circulation-pump` (binary sensor)
- `blower` (switch or number)
- `hold` (switch)
- `filter-cycle1/2` start-hour, start-minute, duration (number)
- `filter-cycle2` enabled (switch)

The bridge also exposes a `water_heater` entity to HA using the HASS discovery format.

## Reference Files

Read these Ruby files when implementing each Python module:
- Protocol/framing: `balboa_worldwide_app/lib/bwa/message.rb`
- CRC: `balboa_worldwide_app/lib/bwa/crc.rb`
- Client API: `balboa_worldwide_app/lib/bwa/client.rb`
- Status parsing: `balboa_worldwide_app/lib/bwa/messages/status.rb`
- MQTT bridge: `balboa_worldwide_app/exe/bwa_mqtt_bridge`
- Discovery: `balboa_worldwide_app/lib/bwa/discovery.rb`
