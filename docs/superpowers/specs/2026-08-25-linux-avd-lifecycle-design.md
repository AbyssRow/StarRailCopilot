# Linux AVD On-Demand Lifecycle Design

## Goal

Run SRC continuously on Linux while keeping a configured Android Virtual Device (AVD) powered off whenever no scheduled task is due. A due task starts and verifies the AVD before the existing Device, cloud-login, queue, and task code runs. When the due-task batch is exhausted, SRC exits the cloud game, shuts down the AVD, and waits for both ADB and emulator processes to disappear.

## Scope

- Linux Android Emulator only. Windows behavior and physical/network Android devices remain unchanged.
- One SRC configuration owns one explicitly named AVD and one explicit `emulator-NNNN` serial.
- Concurrent SRC configurations must not target the same AVD/serial.
- AVD installation data and application data must persist across shutdown and startup.
- SIGKILL, host power loss, and kernel failure cannot run in-process cleanup; all other normal and exceptional exits should attempt cleanup.

## Configuration

Linux AVD lifecycle is enabled only when `EmulatorInfo.Emulator` is `AndroidAVD`. Existing settings are reused:

- `EmulatorInfo.name`: AVD name.
- `EmulatorInfo.path`: Android Emulator executable; when empty it is resolved from `LinuxAVD.SDKRoot`, Android SDK environment variables, then `PATH`.
- `Emulator.Serial`: fixed serial such as `emulator-5554`; the even console port is passed with `-port`.

New `LinuxAVD` settings:

- `SDKRoot`: optional Android SDK root.
- `AdbPath`: optional adb executable override.
- `MemoryMB`: default `2048`; valid Android Emulator range is 1536-8192.
- `GPU`: default `host` for the machine's Radeon RX 580. Software rendering is only an explicitly logged fallback for diagnosis.
- `Headless`: default `true` for unattended operation. Initial machine setup overrides it to `false` so the user can install the APK, log in, and grant permissions in a visible emulator window; it is switched back only after persistence is verified.
- `StartTimeout`: default `300` seconds.
- `StopTimeout`: default `60` seconds.

No launch path may add `-wipe-data`, a temporary `-data` image, `-no-snapshot-save`, or another option that discards changes. The AVD's persistent `userdata-qemu.img` remains authoritative.

## Components

### Linux lifecycle controller

`module/device/platform/platform_linux.py` contains a focused controller whose external dependencies are injected or isolated at command/process boundaries so unit tests do not need an installed AVD.

It validates configuration, builds argument arrays without a shell, identifies only the configured AVD and port, starts the emulator in a new process session, polls readiness, and performs bounded shutdown. `PlatformLinux` delegates the platform `emulator_start()` and `emulator_stop()` interface to this controller.

### Startup state machine

The controller uses one monotonic deadline and reports the current phase:

1. Validate emulator, adb, AVD name, serial, port, memory, GPU, and timeouts.
2. Detect an already-running exact AVD/port and adopt it without starting a duplicate.
3. Launch `emulator -avd NAME -port PORT -memory MEMORY -gpu GPU`, adding `-no-window` and `-no-audio` in headless mode.
4. Poll until the exact emulator process exists and remains alive.
5. Poll `adb devices` until the exact serial reports `device`.
6. Poll `adb shell getprop sys.boot_completed` until it returns `1`.
7. Poll both `adb shell echo src-avd-ready` and `adb shell pm path android`.
8. Only then continue Device initialization.

Polling sleeps throttle checks but never represent boot-completion guesses. Each external command has its own short timeout inside the overall monotonic deadline.

### Shutdown state machine

1. If Device exists, call the existing game stop path so cloud gaming actively exits.
2. Release screenshot/control resources.
3. Send `adb -s SERIAL emu kill` when the serial is present.
4. Poll until both the exact serial and matching process are absent.
5. If the deadline expires, send TERM only to exact matching processes/process group, poll again, and use KILL as the last bounded fallback.
6. Clear Device/lifecycle state so the next due task creates a fresh connection.

Shutdown is idempotent. An already-stopped AVD is success.

## Scheduler integration

`get_next_task()` must never evaluate the `device` cached property while a task is in the future. The `close_emulator` branch checks `self.__dict__` for an already-created Device:

- No Device: release ordinary resources and wait.
- Existing Device: exit the app/cloud session, release Device resources, stop the emulator, and delete the Device cache before waiting.

Pending tasks retain the same Device and AVD. Only transition from the pending batch to a future task triggers shutdown. Existing Restart scheduling remains responsible for cloud login and queue entry after the next boot.

The scheduler loop has an outer `try/finally`. Device initialization failure also cleans a Linux AVD that was already launched. On Linux, manual GUI stop uses SIGTERM with a cleanup grace period before SIGKILL fallback.

## Logging and errors

Logs include resolved non-secret configuration, launch arguments, lifecycle phase changes, elapsed time, last ADB result, matching PIDs, graceful shutdown result, and fallback actions. Startup validation and timeout failures raise a clear SRC error only after cleanup has been attempted.

## Testing

Unit tests cover:

- initially stopped and already-running AVDs;
- exact launch arguments and phase ordering;
- boot timeout and cleanup;
- idempotent stop and dual serial/process disappearance;
- no due task without Device construction;
- one startup across multiple pending tasks;
- future-task transition shutdown;
- exception/SystemExit cleanup;
- non-AndroidAVD and non-Linux compatibility.

Real-machine acceptance additionally verifies host GPU rendering, low-memory stability, cloud APK ABI compatibility, cloud login/queue behavior, shutdown memory release, and login/app-data persistence across at least two stop/start cycles. Credentials and verification codes are entered manually by the user in the visible first-run emulator and are never read or stored by the automation.

## Fork and update strategy

Development and deployment use `AbyssRow/StarRailCopilot:linux-avd`. SRC deploy configuration points at the full fork URL and branch while retaining `AutoUpdate=true` and `KeepLocalChanges=false`.

A fork-only scheduled/manual workflow fetches `LmeSzinc/StarRailCopilot:master`, merges it in CI, runs lifecycle tests and compile checks, and pushes only on success. Conflicts or test failures leave `linux-avd` unchanged. The fork default branch must contain the scheduled workflow. An upstream PR is prepared from a clean branch without fork-only deployment automation.
