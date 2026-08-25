# Linux AVD Fork Maintenance Guide

This repository is the `AbyssRow/StarRailCopilot` fork. Its daily-use branch is
`linux-avd`; `master` mirrors the upstream project. Preserve the Linux AVD patch
when updating from `LmeSzinc/StarRailCopilot:master`.

## Non-negotiable behavior

- SRC may stay running while the AVD is off.
- A future task must not initialize `Device` or start the AVD.
- A due task starts the configured AVD and polls, in order: exact emulator
  process, ADB serial, `sys.boot_completed=1`, ADB shell, package manager.
- All currently due tasks reuse one Device. Before a future task, SRC exits the
  cloud game, sends `adb emu kill`, and waits for both the serial and exact
  process to disappear.
- Startup timeout, partial Device initialization, task exceptions, `SystemExit`,
  SIGTERM, and GUI manual stop must attempt AVD cleanup.
- A task that still fails after SRC retries must stop its scheduler and AVD but
  must not take down the Web UI. An unexpected Uvicorn child exit is restarted
  inside the persistent `gui.py` supervisor; systemd remains the outer backstop.
- Never infer boot completion with a fixed sleep. Keep monotonic deadlines and
  polling.
- Never add `-wipe-data`, a temporary `-data` path, or another option that
  discards persistent AVD userdata.

## Important files

- `module/device/platform/platform_linux.py`: AVD settings, launch, readiness,
  shutdown, and Linux platform integration.
- `module/device/platform/plat.py`: selects `PlatformLinux` on Linux.
- `module/alas.py`: lazy Device handling, due-task batching, idle shutdown, and
  scheduler exit cleanup.
- `module/device/device.py`: cleanup after partial Device initialization.
- `module/webui/process_manager.py`: Linux SIGTERM grace before SIGKILL.
- `gui.py`: persistent Web child-process supervisor and in-process restart.
- `tests/test_platform_linux.py`: AVD settings/start/stop/platform tests.
- `tests/test_alas_linux_avd.py`: scheduler, exception, and GUI-stop tests.
- `tests/test_sync_upstream_workflow.py`: automatic-sync schedule and alert
  contract.
- `tests/test_gui_supervisor.py`: unexpected Web child-exit restart contract.
- `.github/workflows/sync-upstream.yml`: upstream merge, verification, push, and
  GitHub Issue alert workflow.
- `doc/linux-avd.md`: user-facing setup and operating guide.

## This machine

- Repository: `/home/abyssrow/StarRailCopilot`
- Python: `/home/abyssrow/StarRailCopilot/.venv/bin/python` (3.10)
- Android SDK: `/home/abyssrow/Android/Sdk`
- Primary config/AVD: `src` / `src-cloud` / `emulator-5554`
- Secondary config/AVD: `src2` / `src-cloud-2` / `emulator-5556`
- Persistent AVD data:
  `/home/abyssrow/.android/avd/{src-cloud,src-cloud-2}.avd`
- Image: Android 11 / API 30 Google APIs x86_64
- ARM translation: `libndk_translation.so`; the cloud APK is ARM-only.
- Runtime: KVM, 2 vCPU, 2048 MB guest RAM, `-gpu host`, headless.
- Screenshot/control: `ADB` and `MaaTouch` on both configs. A 2026-08-25
  real-AVD test measured ADB at about 378 ms, uiautomator2 at 425 ms, and
  ADB_nc at 579 ms with truncation retries. Scrcpy cannot decode without the
  unavailable compatible PyAV dependency. DroidCast is unreliable on this
  image. Do not change screenshot methods without a fresh real-device test.
- The configs deliberately use separate AVDs because cloud-game login state is
  stored in Android userdata. `src` uses server update `04:00`; every enabled
  task in `src2` uses `04:30` to reduce simultaneous boots on the old CPU.
- Web service: `systemctl --user status starrailcopilot-web`
- Web unit: `/home/abyssrow/.config/systemd/user/starrailcopilot-web.service`
- Local proxy: `clash-verge-service.service` starts at boot and the Mihomo core
  listens on `127.0.0.1:7897`. This does not depend on the desktop system-proxy
  toggle being enabled.
- Codex uses the user-global, untracked `/home/abyssrow/.codex/.env` with HTTP
  and HTTPS proxy variables set to `http://127.0.0.1:7897`. New Codex processes
  load this file; an already-running process does not hot-reload it.
- SRC updates use `GitProxy: http://127.0.0.1:7897` in the ignored
  `config/deploy.yaml`, plus matching repository-local `http.proxy` and
  `https.proxy` values in `.git/config`. Keep both: the periodic update check
  fetches before the updater reapplies `GitProxy`.

Local files `config/deploy.yaml`, `config/src.json`, and `config/src2.json` are
intentionally ignored by Git. Never commit them. They may contain account state
or a Web password: do not read, print, log, copy, or overwrite password values.
At the user's explicit request, the Web UI listens on `0.0.0.0:22367` for the
existing direct remote-access setup and uses the password stored in the ignored
deploy config. Tailscale Serve and Funnel are disabled. TLS is not configured,
so future maintenance should prefer adding HTTPS when a domain/certificate is
available; never print or overwrite the password while doing so.

The repository pins `av==10.0.0`, which is incompatible with this host's current
FFmpeg toolchain. Installed dependencies use ADB screenshots, and local
`InstallDependencies` is disabled so AutoUpdate is not blocked by PyAV. Revisit
this only after testing dependency installation and screenshots end to end.

## Verification

Before committing or pushing lifecycle changes, run:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q module tests src.py
.venv/bin/python -m module.config.config_updater
git diff --check
git status --short
```

The config generator must leave no unexpected tracked diff. For a real-machine
check, start from an already stopped AVD, verify all five readiness gates, take
an ADB screenshot, stop through the SRC cleanup path, and confirm both the
configured serial and its exact QEMU process disappear. Test `src` and `src2`
separately; never copy userdata between them. Do not inspect or log the user's
account screen or credentials.

## Automatic upstream sync and alerts

The Fork default branch is `linux-avd`. The workflow checks upstream every four
hours at minute 37 and can also be dispatched manually. It merges
`upstream/master`, runs the unit tests, compile check, and config generator, and
pushes only after every verification step succeeds.

On failure, the workflow creates one open Issue titled
`[linux-avd] Upstream sync failed`, labels it `linux-avd-sync`, and assigns it to
`AbyssRow`. Repeated failures add comments to that Issue. The first later
successful sync comments with the recovery run and closes the Issue.

To exercise the alert path intentionally:

```bash
gh workflow run sync-upstream.yml \
  --repo AbyssRow/StarRailCopilot \
  --ref linux-avd \
  -f simulate_failure=true
```

After confirming the failure Issue, run the workflow normally and verify that
the recovery job closes it:

```bash
gh workflow run sync-upstream.yml \
  --repo AbyssRow/StarRailCopilot \
  --ref linux-avd \
  -f simulate_failure=false
```

If a sync fails, inspect the linked Actions run before editing anything. A merge
conflict or failed test leaves the remote `linux-avd` branch unchanged. Resolve
the actual upstream incompatibility locally, run the full verification above,
then push normally; never force-push or reset away user work without explicit
approval. A workflow syntax or permission failure may prevent the Issue job
itself from running, so also inspect the Actions page if scheduled runs vanish.

## Branches and upstream contribution

- `origin`: `https://github.com/AbyssRow/StarRailCopilot.git`
- `upstream`: `https://github.com/LmeSzinc/StarRailCopilot.git`
- Daily branch: `linux-avd`
- Clean upstream PR branch: `upstream-linux-avd`
- Upstream PR: `https://github.com/LmeSzinc/StarRailCopilot/pull/1045`

Keep Fork-specific workflow and machine guidance out of the upstream PR branch.
If upstream accepts equivalent functionality, compare it carefully with the
invariants above before removing the Fork patch.
