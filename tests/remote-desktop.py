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
from gi.repository import Gdk, Gio, GLib, Gtk

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--eis-client", required=True)
parser.add_argument("--deny", action="store_true", help="expect denial in either native dialog")
args = parser.parse_args()

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
name = "org.freedesktop.portal.Desktop"
path = "/org/freedesktop/portal/desktop"
rd = "org.freedesktop.portal.RemoteDesktop"
sc = "org.freedesktop.portal.ScreenCast"
responses = {}
condition = threading.Condition()
events = []
result = {"passed": False}


def response(_bus, _sender, object_path, _interface, _signal, parameters):
    with condition:
        responses[object_path] = parameters.unpack()
        condition.notify_all()


subscription = bus.signal_subscribe(name, "org.freedesktop.portal.Request", "Response", None, None, Gio.DBusSignalFlags.NONE, response)


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
window.add(Gtk.Label(label="RemoteDesktop native test\nApprove the native screen picker and control consent.\nThe test will click here and type through the portal."))
window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.KEY_RELEASE_MASK)
window.connect("key-press-event", lambda _w, event: events.append(("press", event.keyval, int(event.state))) or True)
window.connect("key-release-event", lambda _w, event: events.append(("release", event.keyval, int(event.state))) or True)
window.connect("button-press-event", lambda _w, event: events.append(("button", int(event.button), 0)) or True)
window.connect("destroy", lambda _w: Gtk.main_quit())
window.show_all()


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
            video = subprocess.run(["gst-launch-1.0", "-q", "pipewiresrc", "fd=" + str(fd), "path=" + str(node), "num-buffers=30", "!", "fakesink"], pass_fds=(fd,), capture_output=True, text=True, timeout=30)
        finally:
            os.close(fd)
        if video.returncode:
            raise RuntimeError("PipeWire consumer failed: " + video.stderr)
        result["pipewire_frames"] = 30
        reply, fds = bus.call_with_unix_fd_list_sync(name, path, rd, "ConnectToEIS", GLib.Variant("(oa{sv})", (session, {})), None, Gio.DBusCallFlags.NONE, 15000, None, None)
        fd = fds.get(reply.unpack()[0])
        try:
            eis = subprocess.run([args.eis_client, str(fd)], pass_fds=(fd,), capture_output=True, text=True, timeout=20)
        finally:
            os.close(fd)
        if eis.returncode:
            raise RuntimeError("EIS client failed: " + eis.stderr)
        result["eis"] = eis.stdout.strip()
        # The EIS click focused the witness; now exercise lindm's D-Bus key path.
        call(rd, "NotifyKeyboardKeycode", "(oa{sv}iu)", (session, {}, 32, 1))
        call(rd, "NotifyKeyboardKeycode", "(oa{sv}iu)", (session, {}, 32, 0))
        time.sleep(0.5)
        presses = [key for kind, key, _state in events if kind == "press"]
        if ord("d") not in presses or [key for key in presses if key in (ord("A"), ord("B"), ord("a"))] != [ord("A"), ord("B"), ord("a")]:
            raise RuntimeError("missing D-Bus key, pointer-stop modifier retention, or keyboard-stop release: " + repr(events))
        for key in (65505, 65507):  # XKB Shift_L and Control_L
            if not any(kind == "release" and value == key for kind, value, _state in events):
                raise RuntimeError("missing synthetic held-key release: " + repr(events))
        if not any(kind == "button" for kind, _key, _state in events):
            raise RuntimeError("no compositor-delivered EIS pointer click")
        result["passed"] = True
    except Exception as error:
        result["error"] = str(error)
    finally:
        if session:
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
print(json.dumps(result, sort_keys=True), flush=True)
raise SystemExit(0 if result["passed"] else 1)
