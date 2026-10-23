package org.libsdl.app;

import java.util.LinkedHashMap;

/** Modifier metadata only. Never stores, rewrites or logs committed text. */
public final class WesnothKeyboardState {
    // Android KeyEvent constants, kept here so state transitions can run on a host JVM.
    static final int SHIFT = 0x000000c1, ALT = 0x00000032;
    static final int CTRL = 0x00007000, META = 0x00070000, CAPS = 0x00100000;
    static final int ALL = SHIFT | ALT | CTRL | META | CAPS;
    static final int[] KEYS = {59, 60, 57, 58, 113, 114, 117, 118};
    static final int[] SIDES = {0x40, 0x80, 0x10, 0x20, 0x2000, 0x4000, 0x20000, 0x40000};
    private static final int[] GROUPS = {SHIFT, ALT, CTRL, META};
    private static final int[] GENERIC = {1, 2, 0x1000, 0x10000};
    private static final int MAX_DEVICES = 8;

    private static final class Device {
        int suppressed;
        int held;
        Device(int suppressed) { this.suppressed = suppressed; }
    }
    private final LinkedHashMap<Integer, Device> devices = new LinkedHashMap<>();
    private int newDeviceSuppression;

    /** Explicit recovery affects future key events; it never changes an editor's value. */
    public void reset() {
        newDeviceSuppression = ALL;
        for (Device device : devices.values()) { device.suppressed = ALL; device.held = 0; }
    }
    public void focusLost() {
        for (Device device : devices.values()) device.held = 0;
    }
    public int held() {
        int result = 0;
        for (Device device : devices.values()) result |= device.held;
        return result;
    }
    int deviceCount() { return devices.size(); }

    public int event(int deviceId, int key, int action, int repeat, int meta) {
        if (action != 0 && action != 1) return meta;
        Device device = devices.get(deviceId);
        if (device == null) {
            if (devices.size() == MAX_DEVICES) {
                Integer oldest = devices.keySet().iterator().next();
                devices.remove(oldest);
            }
            device = new Device(newDeviceSuppression);
            devices.put(deviceId, device);
        }
        // A real release or subsequent press ends the explicit reset's suppression.
        for (int group : GROUPS) if ((meta & group) == 0) device.suppressed &= ~group;
        if ((meta & CAPS) == 0) device.suppressed &= ~CAPS;
        for (int i = 0; i < KEYS.length; ++i) {
            if (key != KEYS[i]) continue;
            int group = GROUPS[i / 2], generic = GENERIC[i / 2];
            if (action == 0) {
                device.suppressed &= ~group;
                meta |= SIDES[i] | generic;
            } else {
                // Some emulator key-up events still contain the released side.
                meta &= ~SIDES[i];
                if ((meta & (group & ~generic)) == 0) {
                    meta &= ~generic;
                    // An observed release is authoritative until a new press or released metadata.
                    device.suppressed |= group;
                }
            }
        }
        if (key == 115 && action == 0 && repeat == 0) device.suppressed &= ~CAPS;
        meta &= ~device.suppressed;
        int held = 0;
        for (int i = 0; i < GROUPS.length; ++i) {
            int left = SIDES[2 * i], right = SIDES[2 * i + 1];
            if ((meta & left) != 0) held |= 1 << (2 * i);
            if ((meta & right) != 0) held |= 1 << (2 * i + 1);
            if ((meta & GENERIC[i]) != 0 && (meta & (left | right)) == 0) held |= 1 << (2 * i);
        }
        device.held = held;
        return meta;
    }
}
