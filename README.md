# xdg-desktop-portal-hyprland

An [XDG Desktop Portal](https://github.com/flatpak/xdg-desktop-portal) backend
for Hyprland.

## Installing

First, make sure to install the required dependencies:

```
gbm
hyprland-protocols
hyprlang
hyprutils
hyprwayland-scanner
libdrm
libpipewire-0.3
libei (libeis-1.0)
libspa-0.2
sdbus-cpp
wayland-client
wayland-protocols
xkbcommon
```

Then run the build and install command:

```sh
git clone --recursive https://github.com/hyprwm/xdg-desktop-portal-hyprland
cd xdg-desktop-portal-hyprland/
cmake -DCMAKE_INSTALL_LIBEXECDIR=/usr/lib -DCMAKE_INSTALL_PREFIX=/usr -B build
cmake --build build
sudo cmake --install build
```

## Nix

> [!CAUTION]
> XDPH should not be used from this flake directly!
> 
> Instead, use it from the [Hyprland flake](https://github.com/hyprwm/Hyprland).

There are two reasons for the above:

1. Hyprland depends on XDPH, but XDPH also depends on Hyprland. This results in
   a cyclic dependency, which is a nightmare. To counter this, we use the
   Nixpkgs Hyprland package in this flake, so that it can be later consumed by
   the Hyprland flake while overriding the Hyprland package.
2. Even if you manually do all the overriding, you may still get it wrong and
   lose out on the Cachix cache (which has XDPH as exposed by the Hyprland
   flake).

## Running, FAQs, etc.

See
[the Hyprland wiki](https://wiki.hyprland.org/Hypr-Ecosystem/xdg-desktop-portal-hyprland/)

RemoteDesktop supports keyboard and pointer control, optionally combined with a
ScreenCast session. New grants require the share picker's explicit consent.
EIS keyboard input releases held keys when the keyboard stops emulating, is
removed, or disconnects; pointer stop events do not reset keyboard modifiers.

For the interactive native regression, compile `tests/remote-desktop-eis.c`
against `libei-1.0`, then run the following in a real Hyprland session with a
US keymap, Python GObject/GTK3, and GStreamer's PipeWire source installed:

```sh
cc -Wall -Wextra -Werror tests/remote-desktop-eis.c \
  $(pkg-config --cflags --libs libei-1.0) -o /tmp/remote-desktop-eis
python3 tests/remote-desktop.py --eis-client /tmp/remote-desktop-eis
```

Approve the real screen selection and control dialogs. The test consumes 30
PipeWire frames and observes compositor-delivered D-Bus and EIS input, including
keyboard stop/resume, pointer stop without modifier loss, and held-key disconnect.
Run again with `--deny` and reject either native dialog to check consent refusal.
