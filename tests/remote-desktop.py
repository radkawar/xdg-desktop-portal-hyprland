#!/usr/bin/env python3
"""Native, interactive RemoteDesktop+ScreenCast regression on a US-keymap seat.

Run in the real Hyprland user session, with python-gobject, GTK3, GStreamer
pipewiresrc and the compiled remote-desktop-eis client. Approve the real screen
selection and remote-control dialogs. No consent bypass or private API is used.
"""
import argparse
import json
import os
import subprocess
import threading
import time
import uuid

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gdk, Gio, GLib, Gst, Gtk

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--eis-client", required=True)
parser.add_argument("--deny", action="store_true", help="expect denial in either native dialog")
args = parser.parse_args()
Gst.init(None)

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
name = "org.freedesktop.portal.Desktop"
path = "/org/freedesktop/portal/desktop"
rd = "org.freedesktop.portal.RemoteDesktop"
sc = "org.freedesktop.portal.ScreenCast"
responses = {}
closed_sessions = set()
condition = threading.Condition()
events = []
result = {"passed": False}


def response(_bus, _sender, object_path, _interface, _signal, parameters):
    with condition:
        responses[object_path] = parameters.unpack()
        condition.notify_all()


subscription = bus.signal_subscribe(name, "org.freedesktop.portal.Request", "Response", None, None, Gio.DBusSignalFlags.NONE, response)


def session_closed(_bus, _sender, object_path, _interface, _signal, _parameters):
    with condition:
        closed_sessions.add(object_path)
        condition.notify_all()


session_subscription = bus.signal_subscribe(name, "org.freedesktop.portal.Session", "Closed", None, None, Gio.DBusSignalFlags.NONE, session_closed)


def call(interface, method, signature, values):
    return bus.call_sync(name, path, interface, method, GLib.Variant(signature, values), None, Gio.DBusCallFlags.NONE, 15000, None)


def request(interface, method, signature, values):
    handle = call(interface, method, signature, values).unpack()[0]
    print("WAIT", method, handle, flush=True)
    with condition:
        if not condition.wait_for(lambda: handle in responses, timeout=120):
            raise RuntimeError(method + " response timed out")
        code, data = responses.pop(handle)
    print("RESPONSE", method, code, flush=True)
    return code, data


def options(**values):
    return {key: GLib.Variant(signature, value) for key, (signature, value) in values.items()}


window = Gtk.Window(title="RemoteDesktop native event witness")
window.fullscreen()
label = Gtk.Label(label="RemoteDesktop native test\nApprove the native screen picker and control consent.")
window.add(label)
window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.KEY_RELEASE_MASK)
window.connect("key-press-event", lambda _w, event: events.append(("press", event.keyval, int(event.state), event.hardware_keycode)) or True)
window.connect("key-release-event", lambda _w, event: events.append(("release", event.keyval, int(event.state), event.hardware_keycode)) or True)
window.connect("button-press-event", lambda _w, event: events.append(("button", int(event.button), 0, 0)) or True)
window.connect("destroy", lambda _w: Gtk.main_quit())
window.show_all()
# Damage-driven compositors need changing real window content to produce frames.
GLib.timeout_add(100, lambda: label.set_text("RemoteDesktop native video and input witness\n" + str(time.monotonic_ns())) or True)


def run():
    session = None
    try:
        token = "native_" + uuid.uuid4().hex
        code, data = request(rd, "CreateSession", "(a{sv})", (options(session_handle_token=("s", token)),))
        if code:
            raise RuntimeError("CreateSession refused")
        session = data["session_handle"]
        code, _ = request(rd, "SelectDevices", "(oa{sv})", (session, options(types=("u", 3), persist_mode=("u", 0))))
        if code:
            raise RuntimeError("SelectDevices refused")
        try:
            call(rd, "NotifyKeyboardKeycode", "(oa{sv}iu)", (session, {}, 30, 1))
        except GLib.Error:
            result["pregrant_input_denied"] = True
        else:
            raise RuntimeError("frontend accepted input before Start")
        code, _ = request(sc, "SelectSources", "(oa{sv})", (session, options(types=("u", 1), multiple=("b", False), cursor_mode=("u", 2))))
        if code:
            if not args.deny:
                raise RuntimeError("SelectSources refused")
            result["denied"] = True
            result["passed"] = True
            return
        code, data = request(rd, "Start", "(osa{sv})", (session, "", {}))
        if args.deny:
            if not code:
                raise RuntimeError("expected consent denial, got a successful Start")
            result["denied"] = True
            result["passed"] = True
            return
        if code or data.get("devices", 0) != 3 or not data.get("streams"):
            raise RuntimeError("Start did not grant combined screen and keyboard/pointer")
        node, properties = data["streams"][0]
        result["stream"] = {"node": node, "properties": properties}
        reply, fds = bus.call_with_unix_fd_list_sync(name, path, sc, "OpenPipeWireRemote", GLib.Variant("(oa{sv})", (session, {})), None, Gio.DBusCallFlags.NONE, 15000, None, None)
        fd = fds.get(reply.unpack()[0])
        try:
            pipeline = Gst.parse_launch("pipewiresrc name=capture num-buffers=30 ! video/x-raw ! fakesink name=witness signal-handoffs=true sync=false")
            source = pipeline.get_by_name("capture")
            source.set_property("fd", fd)
            source.set_property("path", str(node))
            frames = []
            pipeline.get_by_name("witness").connect("handoff", lambda _sink, _buffer, _pad: frames.append(True))
            try:
                pipeline.set_state(Gst.State.PLAYING)
                message = pipeline.get_bus().timed_pop_filtered(30 * Gst.SECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
                if message is None:
                    raise RuntimeError("PipeWire consumer timed out")
                if message.type == Gst.MessageType.ERROR:
                    error, debug = message.parse_error()
                    raise RuntimeError("PipeWire consumer failed: " + str(error) + ": " + str(debug))
            finally:
                pipeline.set_state(Gst.State.NULL)
                result["pipewire_frames"] = len(frames)
        finally:
            os.close(fd)
        if len(frames) != 30:
            raise RuntimeError("PipeWire consumer received " + str(len(frames)) + " buffers, expected 30")
        # The public frontend forbids Notify* once ConnectToEIS is used.
        # Exercise lindm's D-Bus pointer and key path before switching to EIS.
        width, height = properties["size"]
        call(rd, "NotifyPointerMotionAbsolute", "(oa{sv}udd)", (session, {}, node, width / 2.0, height / 2.0))
        call(rd, "NotifyPointerButton", "(oa{sv}iu)", (session, {}, 272, 1))
        call(rd, "NotifyPointerButton", "(oa{sv}iu)", (session, {}, 272, 0))
        call(rd, "NotifyKeyboardKeycode", "(oa{sv}iu)", (session, {}, 32, 1))
        call(rd, "NotifyKeyboardKeycode", "(oa{sv}iu)", (session, {}, 32, 0))
        time.sleep(0.3)
        if not any(kind == "press" and value == ord("d") for kind, value, _state, _code in events) or not any(kind == "button" for kind, _value, _state, _code in events):
            raise RuntimeError("missing compositor-delivered D-Bus pointer or key: " + repr(events))
        result["dbus_pointer_keyboard_delivered"] = True
        reply, fds = bus.call_with_unix_fd_list_sync(name, path, rd, "ConnectToEIS", GLib.Variant("(oa{sv})", (session, {})), None, Gio.DBusCallFlags.NONE, 15000, None, None)
        fd = fds.get(reply.unpack()[0])
        try:
            eis = subprocess.run([args.eis_client, str(fd)], pass_fds=(fd,), capture_output=True, text=True, timeout=20)
        finally:
            os.close(fd)
        if eis.returncode:
            raise RuntimeError("EIS client failed: " + eis.stderr)
        result["eis"] = eis.stdout.strip()
        time.sleep(0.5)
        presses = [key for kind, key, _state, _code in events if kind == "press"]
        if ord("d") not in presses or [key for key in presses if key in (ord("A"), ord("B"), ord("a"))] != [ord("A"), ord("B"), ord("a")]:
            raise RuntimeError("missing D-Bus key, pointer-stop modifier retention, or keyboard-stop release: " + repr(events))
        # Modifier keysyms may change when the active Wayland keyboard changes;
        # GDK's hardware keycode identifies the actual released evdev key.
        for keycode in (42 + 8, 29 + 8):  # KEY_LEFTSHIFT and KEY_LEFTCTRL
            if not any(kind == "release" and code == keycode for kind, _value, _state, code in events):
                raise RuntimeError("missing synthetic held-key release: " + repr(events))
        if sum(kind == "button" for kind, _key, _state, _code in events) < 2:
            raise RuntimeError("no compositor-delivered EIS pointer click")
        result["passed"] = True
    except Exception as error:
        result["error"] = str(error)
    finally:
        if session and result.get("denied"):
            with condition:
                result["session_closed"] = condition.wait_for(lambda: session in closed_sessions, timeout=5)
            if not result["session_closed"]:
                result["error"] = "frontend did not close the refused session"
                result["passed"] = False
        if session and session not in closed_sessions:
            try:
                bus.call_sync(name, session, "org.freedesktop.portal.Session", "Close", None, None, Gio.DBusCallFlags.NONE, 15000, None)
            except GLib.Error as error:
                result["close_error"] = str(error)
                result["passed"] = False
        result["events"] = events
        GLib.idle_add(Gtk.main_quit)


GLib.timeout_add(500, lambda: threading.Thread(target=run, daemon=True).start() or False)
Gtk.main()
bus.signal_unsubscribe(subscription)
bus.signal_unsubscribe(session_subscription)
print(json.dumps(result, sort_keys=True), flush=True)
raise SystemExit(0 if result["passed"] else 1)
