import math
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass

from module.logger import logger


class LinuxAVDConfigurationError(ValueError):
    pass


class LinuxAVDStartError(RuntimeError):
    def __init__(self, phase, elapsed):
        self.phase = phase
        self.elapsed = elapsed
        super().__init__(f'Linux AVD startup timed out in phase {phase} after {elapsed:.1f}s')


@dataclass(frozen=True)
class LinuxAVDSettings:
    enabled: bool
    name: str
    emulator_path: str
    adb_path: str
    serial: str
    console_port: int
    memory_mb: int
    gpu: str
    headless: bool
    start_timeout: float
    stop_timeout: float

    @classmethod
    def from_config(cls, config):
        enabled = str(config.EmulatorInfo_Emulator).strip() == 'AndroidAVD'
        name = cls._text(getattr(config, 'EmulatorInfo_name', ''))
        sdk_root = cls._text(getattr(config, 'LinuxAVD_SDKRoot', ''))
        emulator_path = cls._resolve_executable(
            cls._text(getattr(config, 'EmulatorInfo_path', '')),
            sdk_root,
            'emulator/emulator',
            'emulator',
        )
        adb_path = cls._resolve_executable(
            cls._text(getattr(config, 'LinuxAVD_AdbPath', '')),
            sdk_root,
            'platform-tools/adb',
            'adb',
        )
        serial = cls._text(getattr(config, 'Emulator_Serial', ''))

        # PlatformLinux is also the connection base for physical devices and
        # non-AVD emulators.  Their settings must remain completely untouched.
        if not enabled:
            return cls(
                enabled=False,
                name=name,
                emulator_path=emulator_path,
                adb_path=adb_path,
                serial=serial,
                console_port=0,
                memory_mb=2048,
                gpu='host',
                headless=True,
                start_timeout=300,
                stop_timeout=60,
            )

        match = re.fullmatch(r'emulator-(\d+)', serial)
        if match is None:
            raise LinuxAVDConfigurationError(
                f'Linux AVD serial must look like emulator-5554, got {serial!r}'
            )
        console_port = int(match.group(1))
        if not 5554 <= console_port <= 5682 or console_port % 2:
            raise LinuxAVDConfigurationError(
                f'Linux AVD console port must be even and between 5554 and 5682, got {console_port}'
            )

        try:
            memory_mb = int(getattr(config, 'LinuxAVD_MemoryMB', 2048))
            start_timeout = float(getattr(config, 'LinuxAVD_StartTimeout', 300))
            stop_timeout = float(getattr(config, 'LinuxAVD_StopTimeout', 60))
        except (TypeError, ValueError, OverflowError) as error:
            raise LinuxAVDConfigurationError(f'Linux AVD numeric settings are invalid: {error}') from error
        if not 1536 <= memory_mb <= 8192:
            raise LinuxAVDConfigurationError(
                f'Linux AVD memory must be between 1536 and 8192 MB, got {memory_mb}'
            )
        if not all(math.isfinite(value) and value > 0 for value in (start_timeout, stop_timeout)):
            raise LinuxAVDConfigurationError('Linux AVD timeouts must be finite and greater than zero')

        gpu = cls._text(getattr(config, 'LinuxAVD_GPU', 'host'))
        if not gpu:
            raise LinuxAVDConfigurationError('Linux AVD GPU mode must not be empty')
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9._-]*', name):
            raise LinuxAVDConfigurationError(
                'Linux AVD name must start with a letter, number, or underscore and contain '
                'only letters, numbers, dot, underscore, or hyphen'
            )

        return cls(
            enabled=enabled,
            name=name,
            emulator_path=emulator_path,
            adb_path=adb_path,
            serial=serial,
            console_port=console_port,
            memory_mb=memory_mb,
            gpu=gpu,
            headless=bool(getattr(config, 'LinuxAVD_Headless', True)),
            start_timeout=start_timeout,
            stop_timeout=stop_timeout,
        )

    @staticmethod
    def _text(value):
        if value is None:
            return ''
        return str(value).strip()

    @staticmethod
    def _resolve_executable(explicit, sdk_root, relative, fallback):
        if explicit:
            return os.path.abspath(os.path.expanduser(explicit))
        if sdk_root:
            return os.path.abspath(os.path.join(os.path.expanduser(sdk_root), relative))
        for variable in ('ANDROID_SDK_ROOT', 'ANDROID_HOME'):
            root = os.environ.get(variable)
            if root:
                return os.path.abspath(os.path.join(os.path.expanduser(root), relative))
        return fallback

    def launch_command(self):
        command = [
            self.emulator_path,
            '-avd',
            self.name,
            '-port',
            str(self.console_port),
            '-memory',
            str(self.memory_mb),
            '-gpu',
            self.gpu,
            '-no-metrics',
        ]
        if self.headless:
            command.extend(['-no-window', '-no-audio'])
        return command


class LinuxAVDLifecycle:
    def __init__(
            self,
            config,
            runner=None,
            popen=None,
            process_iter=None,
            monotonic=None,
            sleeper=None,
            poll_interval=1.0,
            logger_instance=None,
    ):
        self.settings = LinuxAVDSettings.from_config(config)
        self._runner = runner or self._run_command
        self._popen = popen or self._start_process
        self._process_iter = process_iter or self._iter_processes
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleeper or time.sleep
        self.poll_interval = float(poll_interval)
        self._logger = logger_instance or logger
        self._started_at = 0.0
        self._last_result = None
        self._launched_process = None
        self._launched_process_group = None

    @staticmethod
    def _run_command(command, timeout):
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    @staticmethod
    def _start_process(command, **kwargs):
        return subprocess.Popen(command, **kwargs)

    @staticmethod
    def _iter_processes():
        import psutil
        return psutil.process_iter(['pid', 'cmdline'])

    @staticmethod
    def _process_cmdline(process):
        try:
            info = getattr(process, 'info', {})
            command = info.get('cmdline') if isinstance(info, dict) else None
            if command is None:
                command = process.cmdline()
            return [str(item) for item in command or []]
        except Exception:
            return []

    def matching_processes(self):
        matches = []
        for process in self._process_iter():
            command = self._process_cmdline(process)
            if not self._is_android_emulator_process(command):
                continue
            avd_match = self._option_matches(command, '-avd', self.settings.name) \
                or f'@{self.settings.name}' in command
            port_match = self._option_matches(command, '-port', str(self.settings.console_port))
            if avd_match and port_match:
                matches.append(process)
        return matches

    def _is_android_emulator_process(self, command):
        if not command:
            return False

        executable = os.path.realpath(command[0])
        configured = os.path.realpath(self.settings.emulator_path)
        executable_name = os.path.basename(executable)
        configured_name = os.path.basename(configured)
        if executable_name == configured_name:
            return not os.path.isabs(self.settings.emulator_path) or executable == configured

        if not executable_name.startswith('qemu-system-'):
            return False
        if not os.path.isabs(self.settings.emulator_path):
            return True
        try:
            return os.path.commonpath([executable, os.path.dirname(configured)]) \
                == os.path.dirname(configured)
        except ValueError:
            return False

    @staticmethod
    def _option_matches(command, option, value):
        for index, item in enumerate(command[:-1]):
            if item == option and command[index + 1] == value:
                return True
        return False

    def _remaining(self, deadline):
        return max(deadline - self._monotonic(), 0.0)

    def _call(self, command, deadline):
        remaining = self._remaining(deadline)
        if remaining <= 0:
            return None
        try:
            result = self._runner(command, timeout=min(5.0, remaining))
        except (OSError, subprocess.SubprocessError) as error:
            self._last_result = error
            return None
        self._last_result = result
        return result

    @staticmethod
    def _stdout(result):
        if result is None:
            return ''
        output = getattr(result, 'stdout', '')
        return output.decode(errors='replace') if isinstance(output, bytes) else str(output)

    def _wait_for(self, phase, predicate, deadline):
        self._logger.info(f'Linux AVD waiting for {phase}')
        while True:
            if predicate():
                elapsed = self._monotonic() - self._started_at
                self._logger.info(f'Linux AVD {phase} ready after {elapsed:.1f}s')
                return
            if self._remaining(deadline) <= 0:
                error = LinuxAVDStartError(phase, self._monotonic() - self._started_at)
                self._logger.error(str(error))
                raise error
            self._sleep(min(self.poll_interval, self._remaining(deadline)))

    def _adb(self, *arguments):
        return [self.settings.adb_path, '-s', self.settings.serial, *arguments]

    def _serial_online(self, deadline):
        result = self._call(self._adb('get-state'), deadline)
        return result is not None \
            and getattr(result, 'returncode', 1) == 0 \
            and self._stdout(result).strip() == 'device'

    def start(self):
        if not self.settings.enabled:
            return True

        self._started_at = self._monotonic()
        deadline = self._started_at + self.settings.start_timeout
        try:
            if not self.matching_processes():
                self._logger.info(
                    f'Linux AVD launching {self.settings.name!r} on {self.settings.serial}: '
                    f'{self.settings.launch_command()}'
                )
                self._launched_process = self._popen(
                    self.settings.launch_command(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                    start_new_session=True,
                )
                self._launched_process_group = getattr(self._launched_process, 'pid', None)
            else:
                self._logger.info(
                    f'Linux AVD {self.settings.name!r} is already running; adopting it'
                )

            self._wait_for('process', lambda: bool(self.matching_processes()), deadline)
            self._wait_for('adb_serial', lambda: self._serial_online(deadline), deadline)
            self._wait_for(
                'boot_completed',
                lambda: self._stdout(
                    self._call(self._adb('shell', 'getprop', 'sys.boot_completed'), deadline)
                ).strip() == '1',
                deadline,
            )
            self._wait_for(
                'adb_shell',
                lambda: self._stdout(
                    self._call(self._adb('shell', 'echo', 'src-avd-ready'), deadline)
                ).strip() == 'src-avd-ready',
                deadline,
            )
            self._wait_for(
                'package_manager',
                lambda: self._stdout(
                    self._call(self._adb('shell', 'pm', 'path', 'android'), deadline)
                ).strip().startswith('package:'),
                deadline,
            )
            elapsed = self._monotonic() - self._started_at
            self._logger.info(f'Linux AVD startup completed after {elapsed:.1f}s')
            return True
        except BaseException as error:
            if not isinstance(error, LinuxAVDStartError):
                self._logger.error(f'Linux AVD startup failed: {error}')
            self._logger.warning('Linux AVD startup failed; attempting cleanup')
            try:
                self.stop()
            except Exception as cleanup_error:
                self._logger.error(f'Linux AVD startup cleanup failed: {cleanup_error}')
            raise

    def stop(self):
        if not self.settings.enabled:
            return True
        self._logger.info(
            f'Linux AVD shutdown requested for {self.settings.name!r} on {self.settings.serial}'
        )
        graceful_deadline = self._monotonic() + self.settings.stop_timeout
        processes = self.matching_processes()
        serial_online = self._serial_online(graceful_deadline)
        if not processes and not serial_online and not self._launched_process_alive():
            self._logger.info('Linux AVD is already stopped')
            self._clear_launched_process()
            return True
        if serial_online:
            self._logger.info('Linux AVD sending adb emu kill')
            self._call(self._adb('emu', 'kill'), graceful_deadline)
        if self._wait_for_shutdown(graceful_deadline):
            self._logger.info('Linux AVD process and ADB serial disappeared')
            self._clear_launched_process()
            return True

        self._logger.warning('Linux AVD graceful shutdown timed out; sending SIGTERM')
        self._signal_matching_processes(signal.SIGTERM, 'terminate')
        force_wait = min(max(self.settings.stop_timeout / 2, self.poll_interval), 10.0)
        terminate_deadline = self._monotonic() + force_wait
        if self._wait_for_shutdown(terminate_deadline):
            self._logger.info('Linux AVD stopped after SIGTERM')
            self._clear_launched_process()
            return True

        self._logger.warning('Linux AVD SIGTERM timed out; sending SIGKILL')
        self._signal_matching_processes(signal.SIGKILL, 'kill')
        kill_deadline = self._monotonic() + force_wait
        stopped = self._wait_for_shutdown(kill_deadline)
        if stopped:
            self._logger.info('Linux AVD stopped after SIGKILL')
            self._clear_launched_process()
        else:
            self._logger.error('Linux AVD shutdown failed: process or ADB serial is still present')
        return stopped

    def _clear_launched_process(self):
        process = self._launched_process
        poll = getattr(process, 'poll', None)
        if callable(poll):
            try:
                poll()
            except OSError:
                pass
        self._launched_process = None
        self._launched_process_group = None

    def _signal_matching_processes(self, signum, method):
        """Signal the launched emulator session, or exact adopted processes."""
        processes = self.matching_processes()
        process_group = self._launched_process_group
        if self._matching_process_uses_group(processes, process_group):
            try:
                os.killpg(process_group, signum)
                return
            except OSError:
                pass

        for process in processes:
            try:
                getattr(process, method)()
            except (OSError, RuntimeError):
                continue

    def _launched_process_alive(self):
        poll = getattr(self._launched_process, 'poll', None)
        if not callable(poll):
            return False
        try:
            return poll() is None
        except OSError:
            return False

    def _matching_process_uses_group(self, processes, process_group):
        if process_group is None or process_group == os.getpid():
            return False
        launched_pid = getattr(self._launched_process, 'pid', None)
        if launched_pid == process_group and self._launched_process_alive():
            try:
                return os.getpgid(launched_pid) == process_group
            except OSError:
                pass
        for process in processes:
            try:
                if os.getpgid(process.pid) == process_group:
                    return True
            except OSError:
                continue
        return False

    def _wait_for_shutdown(self, deadline):
        while self._monotonic() < deadline:
            processes = self.matching_processes()
            serial_online = self._serial_online(deadline)
            if not processes and not serial_online and not self._launched_process_alive():
                return True
            self._sleep(min(self.poll_interval, self._remaining(deadline)))
        return False


from module.device.platform.platform_base import PlatformBase


class PlatformLinux(PlatformBase):
    def __init__(self, config):
        self.linux_avd = LinuxAVDLifecycle(config)
        self.linux_avd_managed = False
        if self.linux_avd.settings.enabled:
            self.linux_avd.start()
            self.linux_avd_managed = True
        try:
            super().__init__(config)
        except BaseException:
            if self.linux_avd_managed:
                try:
                    stopped = self.linux_avd.stop()
                except BaseException as cleanup_error:
                    logger.warning(f'Failed to stop Linux AVD after platform initialization error: {cleanup_error}')
                    stopped = False
                self.linux_avd_managed = not stopped
            raise

    def emulator_start(self):
        if not self.linux_avd.settings.enabled:
            return super().emulator_start()
        started = self.linux_avd.start()
        self.linux_avd_managed = bool(started)
        return started

    def emulator_stop(self):
        if not self.linux_avd.settings.enabled:
            return super().emulator_stop()
        stopped = self.linux_avd.stop()
        if stopped:
            self.linux_avd_managed = False
        return stopped
