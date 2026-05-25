#!/usr/bin/env python3
"""
Raspberry Pi GPIO plugin for OpenPLC Runtime V4 desktop.

Maps Raspberry Pi physical GPIO pins to IEC 61131-3 PLC addresses
so they can be read and driven from any PLC program running in the runtime.

GPIO mapping
------------
Outputs (%QX -> GPIO):
    %QX0.0 -> GPIO21
    %QX0.1 -> GPIO20
    %QX0.2 -> GPIO16
    %QX0.3 -> GPIO12

Inputs (GPIO -> %IX):
    GPIO26 -> %IX0.0
    GPIO19 -> %IX0.1
    GPIO13 -> %IX0.2
    GPIO5  -> %IX0.3
    GPIO22 -> %IX0.4
    GPIO27 -> %IX0.5
    GPIO17 -> %IX0.6
    GPIO4  -> %IX0.7

All input pins have external pull-down resistors wired in hardware.
No internal pull resistor is configured (pigpio PUD_OFF).
Output pins are initialised LOW on startup.

The plugin connects to pigpiod (the pigpio daemon) which must be
running before the PLC program starts. If pigpiod is not available
at startup, the polling thread retries the connection every second.
"""

import os
import sys
import threading

# Allow imports from the shared utilities folder next to this plugin
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared import PluginLogger, SafeBufferAccess, safe_extract_runtime_args_from_capsule

# How often the plugin reads inputs and drives outputs (10 ms = 100 Hz)
POLL_INTERVAL = 0.010

# Input map: (gpio_pin, buffer_index, bit_index)  ->  %IX address
INPUT_MAP = [
    (26, 0, 0),  # %IX0.0
    (19, 0, 1),  # %IX0.1
    (13, 0, 2),  # %IX0.2
    ( 5, 0, 3),  # %IX0.3
    (22, 0, 4),  # %IX0.4
    (27, 0, 5),  # %IX0.5
    (17, 0, 6),  # %IX0.6
    ( 4, 0, 7),  # %IX0.7
]

# Output map: (buffer_index, bit_index, gpio_pin)  ->  %QX address
OUTPUT_MAP = [
    (0, 0, 21),  # %QX0.0
    (0, 1, 20),  # %QX0.1
    (0, 2, 16),  # %QX0.2
    (0, 3, 12),  # %QX0.3
]

# Module-level state (set during init and start_loop)
_runtime_args = None
_safe_buffer: SafeBufferAccess = None
_logger: PluginLogger = None
_pi = None          # pigpio connection handle
_stop_event = threading.Event()
_thread = None


def _connect_pigpio():
    """Try to connect to the pigpiod daemon. Returns a handle or None."""
    try:
        import pigpio
        pi = pigpio.pi()
        if not pi.connected:
            _logger.error("pigpiod not reachable — is the daemon running? (sudo pigpiod)")
            return None
        return pi
    except Exception as exc:
        _logger.error(f"pigpio connection error: {exc}")
        return None


def _setup_pins(pi):
    """Configure each GPIO pin as input or output with the correct settings."""
    import pigpio

    for gpio, _, _ in INPUT_MAP:
        pi.set_mode(gpio, pigpio.INPUT)
        # No internal pull resistor — hardware pull-down resistors are wired externally
        pi.set_pull_up_down(gpio, pigpio.PUD_OFF)

    for _, _, gpio in OUTPUT_MAP:
        pi.set_mode(gpio, pigpio.OUTPUT)
        pi.write(gpio, 0)   # Start with all outputs LOW


def _poll_loop():
    """
    Background thread that runs every POLL_INTERVAL seconds.
    - Reads physical input pins and writes their state to %IX PLC addresses.
    - Reads %QX PLC addresses and drives the corresponding output pins.
    """
    global _pi

    while not _stop_event.is_set():

        # If we lost connection to pigpiod, try to reconnect before continuing
        if _pi is None or not _pi.connected:
            _pi = _connect_pigpio()
            if _pi is None:
                _stop_event.wait(1.0)   # Wait 1 s before retrying
                continue
            _setup_pins(_pi)

        try:
            # --- Read inputs: physical GPIO state -> PLC %IX buffer ---
            for gpio, buf_idx, bit_idx in INPUT_MAP:
                pin_val = bool(_pi.read(gpio))
                _, err = _safe_buffer.write_bool_input(buf_idx, bit_idx, pin_val)
                if err != "Success":
                    _logger.error(f"write_bool_input GPIO{gpio}: {err}")

            # --- Write outputs: PLC %QX buffer -> physical GPIO state ---
            # Acquire the PLC mutex so the buffer is not modified mid-read
            _safe_buffer.acquire_mutex()
            try:
                for buf_idx, bit_idx, gpio in OUTPUT_MAP:
                    val, err = _safe_buffer.read_bool_output(buf_idx, bit_idx, thread_safe=False)
                    if err == "Success":
                        _pi.write(gpio, 1 if val else 0)
                    else:
                        _logger.error(f"read_bool_output %QX{buf_idx}.{bit_idx}: {err}")
            finally:
                _safe_buffer.release_mutex()

        except Exception as exc:
            _logger.error(f"GPIO poll error: {exc}")
            # Reset the connection so the next iteration reconnects cleanly
            try:
                _pi.stop()
            except Exception:
                pass
            _pi = None

        _stop_event.wait(POLL_INTERVAL)


# ── V4 plugin contract ────────────────────────────────────────────────────────
# The runtime calls these four functions in order:
#   init()       -> called once when the runtime loads the plugin
#   start_loop() -> called when a PLC program starts running
#   stop_loop()  -> called when the PLC program is stopped
#   cleanup()    -> called when the runtime shuts down

def init(runtime_args_capsule):
    """Extract runtime arguments and initialise the buffer access handle."""
    global _runtime_args, _safe_buffer, _logger

    # Create a temporary logger before we have runtime_args
    _logger = PluginLogger("RPI_GPIO", None)
    _logger.info("rpi_gpio plugin initialising...")

    try:
        runtime_args, err = safe_extract_runtime_args_from_capsule(runtime_args_capsule)
        if runtime_args is None:
            _logger.error(f"Failed to extract runtime args: {err}")
            return False

        # Upgrade the logger now that we have the full runtime context
        _logger = PluginLogger("RPI_GPIO", runtime_args)
        _runtime_args = runtime_args

        # SafeBufferAccess is the thread-safe bridge to the PLC memory image
        _safe_buffer = SafeBufferAccess(runtime_args)
        if not _safe_buffer.is_valid:
            _logger.error(f"SafeBufferAccess invalid: {_safe_buffer.error_msg}")
            return False

        _logger.info("rpi_gpio plugin initialised")
        return True

    except Exception as exc:
        _logger.error(f"Initialisation error: {exc}")
        import traceback
        traceback.print_exc()
        return False


def start_loop():
    """Connect to pigpiod, configure GPIO pins, and launch the polling thread."""
    global _pi, _thread

    if _runtime_args is None:
        _logger.error("Plugin not initialised — cannot start loop")
        return False

    # Try to connect to pigpiod immediately; if unavailable the thread will retry
    _pi = _connect_pigpio()
    if _pi is not None:
        _setup_pins(_pi)
        _logger.info("pigpiod connected and GPIO pins configured")
    else:
        _logger.warn("pigpiod unavailable at start — poll thread will retry every second")

    _stop_event.clear()
    _thread = threading.Thread(target=_poll_loop, daemon=True, name="rpi_gpio_poll")
    _thread.start()
    _logger.info(f"GPIO polling thread started (interval={int(POLL_INTERVAL*1000)} ms)")
    return 0


def stop_loop():
    """Signal the polling thread to stop and wait for it to finish."""
    global _thread

    _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=2.0)
        _thread = None
    _logger.info("GPIO polling thread stopped")
    return True


def cleanup():
    """Set all output pins LOW, disconnect from pigpiod, and release resources."""
    global _pi, _runtime_args, _safe_buffer

    if _pi is not None and _pi.connected:
        # Drive all outputs LOW before the runtime exits
        for _, _, gpio in OUTPUT_MAP:
            try:
                _pi.write(gpio, 0)
            except Exception:
                pass
        try:
            _pi.stop()
        except Exception:
            pass
        _pi = None

    _runtime_args = None
    _safe_buffer = None
    _logger.info("rpi_gpio plugin cleaned up")
    return True
