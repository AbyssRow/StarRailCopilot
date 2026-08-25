# Linux AVD On-Demand Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a configurable, persistent, low-memory Linux Android AVD that SRC starts only for due tasks and reliably stops after the pending-task batch or recoverable process exit.

**Architecture:** A Linux-specific platform controller owns process discovery, argument construction, readiness polling, and shutdown. The scheduler remains the authority on when Device may be initialized and only touches an already-cached Device while idle; Device and process-manager boundaries add cleanup for partial initialization and SIGTERM.

**Tech Stack:** Python 3.10, standard-library `unittest`/`unittest.mock`, adbutils, psutil, Android SDK Emulator/ADB, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-25-linux-avd-lifecycle-design.md`

## Global Constraints

- Do not initialize Device or start an AVD while no task is due.
- Poll every startup/shutdown condition with monotonic deadlines; do not guess readiness using a fixed boot sleep.
- Preserve AVD userdata and never pass an option that wipes or replaces it.
- Default to 2048 MB RAM and `-gpu host`; 1536 MB is tested only after 2048 MB is stable.
- Never kill a process unless its AVD name and configured console port match.
- Keep Windows and non-AVD Linux device behavior unchanged.

---

### Task 1: Testable Linux AVD configuration and launch contract

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_platform_linux.py`
- Create: `module/device/platform/platform_linux.py`
- Modify: `module/device/platform/plat.py`
- Modify: `module/config/argument/argument.yaml`
- Regenerate: `module/config/argument/args.json`
- Regenerate: `module/config/config_generated.py`
- Regenerate: `module/config/i18n/*.json`
- Regenerate: `config/template.json`

**Interfaces:**
- Produces: `LinuxAVDSettings.from_config(config) -> LinuxAVDSettings`
- Produces: `LinuxAVDLifecycle(config, runner=None, process_iter=None, monotonic=None, sleeper=None)`
- Produces: `LinuxAVDLifecycle.start() -> bool` and `.stop() -> bool`
- Produces: `PlatformLinux.emulator_start() -> bool` and `.emulator_stop() -> bool`

- [ ] Write a failing test that constructs literal config values and expects an argument list containing `-avd src-cloud`, `-port 5554`, `-memory 2048`, `-gpu host`, `-no-window`, and no destructive data flags.
- [ ] Run `python -m unittest tests.test_platform_linux.LinuxAVDSettingsTest -v`; confirm failure because `platform_linux` does not exist.
- [ ] Implement settings parsing, path resolution, serial/port/memory/timeout validation, and argument construction with no shell.
- [ ] Re-run the focused test and then `python -m unittest tests.test_platform_linux -v`.
- [ ] Add `AndroidAVD` and LinuxAVD fields to `argument.yaml`, run `python -m module.config.config_updater`, and verify generated artifacts contain the defaults.
- [ ] Modify `plat.py` so Linux imports `PlatformLinux`, Windows remains `PlatformWindows`, and other systems remain `PlatformBase`.
- [ ] Commit with `git commit -m "feat: add configurable Linux AVD platform"`.

### Task 2: Startup readiness state machine

**Files:**
- Modify: `tests/test_platform_linux.py`
- Modify: `module/device/platform/platform_linux.py`

**Interfaces:**
- Consumes: settings and lifecycle interfaces from Task 1.
- Produces: phase-specific `LinuxAVDStartError` with phase and elapsed-time context.

- [ ] Write a failing “initially stopped” test using a fake command boundary that changes literal states in this order: no process, process alive, serial `device`, boot `1`, shell marker, package-manager path.
- [ ] Run the focused test and confirm it fails because `start()` has no state machine.
- [ ] Implement exact process matching, new-session launch, monotonic deadline polling, and the four ordered readiness gates.
- [ ] Re-run the focused test until green.
- [ ] Write and fail an “already running” test; expect no launch and successful readiness verification.
- [ ] Implement idempotent adoption and re-run tests.
- [ ] Write and fail a boot-timeout test; require phase `boot_completed` and a cleanup attempt.
- [ ] Implement phase-aware timeout cleanup and re-run `python -m unittest tests.test_platform_linux -v`.
- [ ] Commit with `git commit -m "feat: poll Linux AVD startup readiness"`.

### Task 3: Bounded, idempotent shutdown

**Files:**
- Modify: `tests/test_platform_linux.py`
- Modify: `module/device/platform/platform_linux.py`

**Interfaces:**
- Consumes: exact AVD process matcher and command runner from Task 2.
- Produces: `.stop()` that returns only after serial and process disappearance or bounded exact-target fallback.

- [ ] Write failing tests for already stopped, graceful `adb emu kill`, serial-only disappearance, process-only disappearance, TERM fallback, and final KILL fallback.
- [ ] Run each focused test and confirm the expected missing behavior.
- [ ] Implement the smallest idempotent shutdown state machine, checking both conditions on every poll.
- [ ] Re-run all platform tests and verify no unrelated process can satisfy or receive a termination action.
- [ ] Commit with `git commit -m "feat: add bounded Linux AVD shutdown"`.

### Task 4: Scheduler lazy Device and consecutive-task lifecycle

**Files:**
- Create: `tests/test_alas_linux_avd.py`
- Modify: `module/alas.py`
- Modify: `module/device/device.py`

**Interfaces:**
- Produces: `AzurLaneAutoScript._get_existing_device()` without evaluating the cached property.
- Produces: idempotent scheduler cleanup used by idle transition and outer `finally`.

- [ ] Write a failing future-task test whose Device factory raises if touched; stop `wait_until()` deterministically and assert Device was never initialized.
- [ ] Run the focused test and confirm the current `self.run('stop')` initializes Device.
- [ ] Implement cached-Device lookup and make the `close_emulator` branch skip all Device calls when absent.
- [ ] Re-run the future-task test.
- [ ] Write a failing consecutive-task test with two pending commands followed by one future command; require one Device identity/start and one shutdown after both pending tasks.
- [ ] Refactor emulator-idle cleanup into one idempotent helper and re-run the test.
- [ ] Write a failing Device-partial-initialization test and add Linux-managed cleanup around the full constructor failure path.
- [ ] Run `python -m unittest tests.test_alas_linux_avd -v` and all platform tests.
- [ ] Commit with `git commit -m "fix: keep Device lazy while scheduler is idle"`.

### Task 5: Recoverable process-exit cleanup

**Files:**
- Modify: `tests/test_alas_linux_avd.py`
- Modify: `module/alas.py`
- Modify: `module/webui/process_manager.py`

**Interfaces:**
- Consumes: scheduler cleanup helper from Task 4.
- Produces: outer-loop cleanup for exceptions/SystemExit and Linux SIGTERM grace before SIGKILL.

- [ ] Write failing tests for task exception and `SystemExit`, requiring exactly one AVD cleanup and no new Device construction.
- [ ] Wrap the scheduler loop in `try/finally`, re-run tests, and preserve original exception propagation.
- [ ] Write a failing ProcessManager behavior test for terminate → bounded join → kill only if still alive.
- [ ] Implement the Linux graceful-stop path without changing Windows behavior.
- [ ] Run both test modules and compile the modified files.
- [ ] Commit with `git commit -m "fix: clean Linux AVD on SRC exit"`.

### Task 6: Fork update automation and operator documentation

**Files:**
- Create: `.github/workflows/sync-upstream.yml`
- Create: `doc/linux-avd.md`
- Modify: `README.md`

**Interfaces:**
- Workflow input: scheduled or manual dispatch on the fork default branch.
- Workflow output: merge and push `upstream/master` into `linux-avd` only after tests/compile pass.

- [ ] Add a manual and weekly workflow using checkout with full history, explicit upstream URL, merge without push, Python 3.10 setup, lifecycle tests, compile checks, and push on success.
- [ ] Add concurrency so only one upstream sync runs at a time.
- [ ] Document installation, all settings, persistent-data guarantees, logs, failure recovery, and the Fork deploy configuration.
- [ ] Validate workflow YAML by parsing it and inspect the generated command behavior rather than grepping source text.
- [ ] Commit with `git commit -m "docs: add Linux AVD deployment and sync workflow"`.

### Task 7: Automated verification and review

**Files:**
- Modify only files required by verified failures or review findings.

**Interfaces:**
- Consumes all prior task outputs.
- Produces a clean test/compile/config-generation result.

- [ ] Run `python -m unittest discover -s tests -v`.
- [ ] Run `python -m compileall module tests`.
- [ ] Run `python -m module.config.config_updater`, then `git diff --exit-code` for generator consistency after restoring intentional documentation/implementation diffs to the index.
- [ ] Inspect `git diff --check`, `git status`, and the full branch diff from `master`.
- [ ] Review each acceptance criterion against a named test or upcoming real-machine check; fix uncovered behavior through a new failing test.

### Task 8: Real low-memory persistent AVD acceptance

**Files:**
- Create ignored local files: `config/deploy.yaml`, `config/src.json` as required by SRC setup.
- Install outside the repository: Python 3.10 environment, Android SDK command-line tools, emulator, platform tools, system image, and KVM permissions.

**Interfaces:**
- AVD name: `src-cloud` unless an installed APK constraint requires a documented alternative.
- Serial: `emulator-5554`.
- Initial memory/GPU: `2048` MB and `host`.

- [ ] Verify `/dev/kvm` access, emulator `-accel-check`, Mesa/EGL/Vulkan visibility for the Radeon RX 580, free disk, and TCP ports 5554/5555.
- [ ] Install a Python 3.10 virtual environment and project dependencies; do not use the system Python 3.14 environment.
- [ ] Install Android SDK command-line tools, platform tools, emulator, and a compatible x86_64 system image.
- [ ] Create `src-cloud` once without force-overwriting any existing AVD, set the local first-run override `Headless=false`, boot a visible persistent emulator, and verify host GPU renderer from emulator logs.
- [ ] Obtain/install the user-authorized cloud-game APK and verify ABI compatibility, package-manager visibility, and manual launch; pause for the user to enter credentials/verification codes and grant required permissions in the emulator window.
- [ ] Run automated cold-start, already-running, consecutive-task, future-task shutdown, timeout, SIGTERM, and memory-release checks.
- [ ] Restart the visible AVD twice and verify package installation, login state, and a sentinel file persist; then set `Headless=true` and repeat automated startup once without a window.
- [ ] If stable at 2048 MB, try 1536 MB once; retain 1536 only if boot, cloud login, streaming, and task execution remain stable without low-memory kills.

### Task 9: Deploy configuration, branch publication, and upstream-ready handoff

**Files:**
- Local ignored config: `config/deploy.yaml`, `config/src.json`.
- Tracked source: only fixes found during real acceptance.

**Interfaces:**
- Repository: `https://github.com/AbyssRow/StarRailCopilot`
- Branch: `linux-avd`
- AutoUpdate: `true`
- KeepLocalChanges: `false`

- [ ] Add `upstream` remote pointing to `https://github.com/LmeSzinc/StarRailCopilot.git` and verify origin/upstream roles.
- [ ] Write local deploy and SRC LinuxAVD settings without committing account data.
- [ ] Re-run all automated verification after real-AVD fixes.
- [ ] Push `linux-avd` to origin and verify the remote branch SHA.
- [ ] Configure the fork default branch/workflow permissions needed for scheduled sync, or report the exact remaining GitHub UI action if credentials do not permit it.
- [ ] Prepare an upstream PR branch/description excluding fork-only workflow and local deployment details.
