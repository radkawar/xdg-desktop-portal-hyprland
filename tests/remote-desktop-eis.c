#include <libei.h>
#include <linux/input-event-codes.h>
#include <poll.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

/* Run only with an fd granted by the public RemoteDesktop.ConnectToEIS API.
 * The companion Python test observes the actual compositor-delivered events.
 */
static void frame(struct ei* ctx, struct ei_device* device) {
    ei_device_frame(device, ei_now(ctx));
}

int main(int argc, char** argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s EIS_FD\n", argv[0]);
        return 2;
    }
    struct ei* ctx = ei_new_sender(NULL);
    if (!ctx)
        return 1;
    ei_configure_name(ctx, "RemoteDesktop native lifecycle regression");
    if (ei_setup_backend_fd(ctx, atoi(argv[1])) != 0)
        return 1;

    struct ei_device *keyboard = NULL, *pointer = NULL;
    const time_t      deadline = time(NULL) + 15;
    while ((!keyboard || !pointer) && time(NULL) < deadline) {
        struct pollfd fd = {.fd = ei_get_fd(ctx), .events = POLLIN};
        if (poll(&fd, 1, 100) < 0)
            return 1;
        ei_dispatch(ctx);
        struct ei_event* event;
        while ((event = ei_get_event(ctx))) {
            enum ei_event_type type   = ei_event_get_type(event);
            struct ei_device*  device = ei_event_get_device(event);
            if (type == EI_EVENT_SEAT_ADDED)
                ei_seat_bind_capabilities(ei_event_get_seat(event), EI_DEVICE_CAP_KEYBOARD, EI_DEVICE_CAP_POINTER_ABSOLUTE, EI_DEVICE_CAP_BUTTON, NULL);
            if (type == EI_EVENT_DEVICE_RESUMED) {
                if (ei_device_has_capability(device, EI_DEVICE_CAP_KEYBOARD))
                    keyboard = ei_device_ref(device);
                if (ei_device_has_capability(device, EI_DEVICE_CAP_POINTER_ABSOLUTE))
                    pointer = ei_device_ref(device);
            }
            if (type == EI_EVENT_DISCONNECT) {
                fprintf(stderr, "EIS disconnected before devices were ready\n");
                return 1;
            }
            ei_event_unref(event);
        }
    }
    if (!keyboard || !pointer || !ei_device_keyboard_get_keymap(keyboard)) {
        fprintf(stderr, "EIS did not advertise ready pointer and mapped keyboard\n");
        return 1;
    }
    struct ei_region* region = ei_device_get_region(pointer, 0);
    if (!region)
        return 1;
    ei_device_start_emulating(pointer, 1);
    ei_device_pointer_motion_absolute(pointer, ei_region_get_x(region) + ei_region_get_width(region) / 2.0, ei_region_get_y(region) + ei_region_get_height(region) / 2.0);
    ei_device_button_button(pointer, BTN_LEFT, true);
    frame(ctx, pointer);
    ei_device_button_button(pointer, BTN_LEFT, false);
    frame(ctx, pointer);
    usleep(300000);

    ei_device_start_emulating(keyboard, 2);
    ei_device_keyboard_key(keyboard, KEY_LEFTSHIFT, true);
    ei_device_keyboard_key(keyboard, KEY_A, true);
    ei_device_keyboard_key(keyboard, KEY_A, false);
    frame(ctx, keyboard);
    /* A pointer leaving must not release a held keyboard modifier. */
    ei_device_stop_emulating(pointer);
    usleep(300000);
    ei_device_keyboard_key(keyboard, KEY_B, true);
    ei_device_keyboard_key(keyboard, KEY_B, false);
    frame(ctx, keyboard);
    /* No explicit Shift-up: the backend must release it at keyboard stop. */
    ei_device_stop_emulating(keyboard);
    usleep(300000);
    ei_device_start_emulating(keyboard, 3);
    ei_device_keyboard_key(keyboard, KEY_A, true);
    ei_device_keyboard_key(keyboard, KEY_A, false);
    frame(ctx, keyboard);
    ei_device_keyboard_key(keyboard, KEY_LEFTCTRL, true);
    frame(ctx, keyboard);
    usleep(300000);
    /* No explicit Ctrl-up or stop: disconnect must also release held keys. */
    ei_disconnect(ctx);
    ei_device_unref(pointer);
    ei_device_unref(keyboard);
    ei_unref(ctx);
    puts("EIS pointer click, pointer stop, keyboard stop/resume, held-key disconnect sent");
    return 0;
}
