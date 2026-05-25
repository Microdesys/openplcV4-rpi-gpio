# OpenPLC Runtime V4 — Raspberry Pi GPIO Plugin

A Python plugin that adds native GPIO support to [OpenPLC Runtime V4 desktop](https://github.com/openplc/openplc-runtime) on Raspberry Pi. Once installed, digital input and output pins are available as standard IEC 61131-3 PLC addresses (`%IX`, `%QX`) and can be controlled directly from any PLC program compiled in the runtime.

---
## GPIO mapping

| GPIO | PLC Address | Direction | Notes |
|------|-------------|-----------|-------|
| 21 | `%QX0.0` | Output | Digital output |
| 20 | `%QX0.1` | Output | Digital output |
| 16 | `%QX0.2` | Output | Digital output |
| 12 | `%QX0.3` | Output | Digital output |
| 26 | `%IX0.0` | Input | External pull-down resistor |
| 19 | `%IX0.1` | Input | External pull-down resistor |
| 13 | `%IX0.2` | Input | External pull-down resistor |
| 5  | `%IX0.3` | Input | External pull-down resistor |
| 22 | `%IX0.4` | Input | External pull-down resistor |
| 27 | `%IX0.5` | Input | External pull-down resistor |
| 17 | `%IX0.6` | Input | External pull-down resistor |
| 4  | `%IX0.7` | Input | External pull-down resistor |

> **Note:** All input pins are wired with external pull-down resistors. No internal pull resistor is configured in software.

---

## Requirements

- Raspberry Pi (tested on Raspberry Pi Zero 2W)
- [OpenPLC Runtime V4 desktop](https://github.com/openplc/openplc-runtime) installed and running
- Internet connection on the Raspberry Pi (to download the plugin during installation)
- `pigpio` package and `pigpiod` daemon (installed automatically by the installer)

---

## Installation

Open a terminal on your Raspberry Pi and run:

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/openplcV4-rpi-gpio/main/install.sh | sudo bash
```

The installer will automatically:

1. Detect your OpenPLC V4 installation directory
2. Download the plugin files
3. Create a dedicated Python virtual environment and install `pigpio`
4. Enable and start the `pigpiod` daemon (also enabled at boot)
5. Register the plugin in `plugins.conf` with `enabled=1`
6. Restart the `openplc-runtime` service

If the installation is successful you will see a confirmation message with the active GPIO mapping.

---

## How it works

The plugin follows the OpenPLC V4 plugin contract and implements four functions called by the runtime:

- **`init()`** — extracts the runtime arguments and sets up access to the PLC memory buffer
- **`start_loop()`** — connects to `pigpiod`, configures the GPIO pins, and launches a background polling thread
- **`stop_loop()`** — signals the polling thread to stop
- **`cleanup()`** — drives all outputs LOW and disconnects from `pigpiod`

The polling thread runs every **10 ms** (100 Hz):
- Reads the physical state of each input pin and writes it to the corresponding `%IX` address in the PLC memory image
- Reads each `%QX` address from the PLC memory image and drives the corresponding output pin accordingly

GPIO control uses the [pigpio](http://abyz.me.uk/rpi/pigpio/) library via the `pigpiod` daemon, which provides precise and safe hardware-level GPIO access.

---

## Usage example

Once the plugin is installed and a PLC program is running, you can use the GPIO addresses in any IEC 61131-3 language. A simple Structured Text example:

```Ladder
GPIO26 sets GPIO21 and resets GPIO19.

<img width="866" height="576" alt="image" src="https://github.com/user-attachments/assets/ac28535e-e4d2-4a3a-90b9-0d05fc0c1fab" />

```

Compile and upload the program from the OpenPLC Editor pointing to your Raspberry Pi's IP address. As soon as the runtime enters RUNNING state the GPIO pins will respond.

---

## Troubleshooting

**Service does not start after installation:**
```bash
sudo journalctl -u openplc-runtime.service -n 60
```

**Plugin does not appear in the logs:**
Check that the `rpi_gpio` entry in `plugins.conf` has `enabled=1`:
```bash
grep rpi_gpio /home/raspberrypi/openplc-runtime/plugins.conf
```

**pigpiod is not running:**
```bash
sudo systemctl start pigpiod
sudo systemctl enable pigpiod
```

---

## License

Apache License 2.0
