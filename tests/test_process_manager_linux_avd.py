import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from module.webui.process_manager import ProcessManager


class ProcessManagerExceptionBoundaryTest(unittest.TestCase):
    def test_scheduler_uses_clean_spawn_context(self):
        """Catches forking a scheduler from the multithreaded Web child."""
        manager = object.__new__(ProcessManager)
        manager.config_name = 'test'
        manager._renderable_queue = object()
        manager._process = None
        manager.start_log_queue_handler = lambda: None

        process = SimpleNamespace(start=lambda: None)
        context = SimpleNamespace(Process=lambda **kwargs: process)
        with patch('module.webui.process_manager.get_context', return_value=context) as get_context, \
                patch('module.webui.process_manager.get_config_mod', return_value='alas'):
            manager.start(func=None)

        get_context.assert_called_once_with('spawn')
        self.assertIs(manager._process, process)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux process-group API test')
    def test_linux_scheduler_isolates_its_process_group(self):
        with patch('module.webui.process_manager.IS_LINUX', True), \
                patch('module.webui.process_manager.os.setsid') as setsid:
            ProcessManager._isolate_linux_process_group()

        setsid.assert_called_once_with()

    def test_non_linux_scheduler_does_not_use_posix_session_api(self):
        with patch('module.webui.process_manager.IS_LINUX', False), \
                patch('module.webui.process_manager.os', SimpleNamespace()) as os_api:
            ProcessManager._isolate_linux_process_group()

        self.assertFalse(hasattr(os_api, 'setsid'))

    def test_stop_grace_does_not_swallow_control_flow_exceptions(self):
        """Catches ordinary config fallback swallowing process control flow."""
        manager = object.__new__(ProcessManager)
        manager.config_name = 'test'

        for error in (SystemExit(2), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), patch(
                'module.webui.process_manager.load_config',
                side_effect=error,
            ):
                with self.assertRaises(type(error)):
                    manager._linux_avd_stop_grace()

    def test_stop_refuses_a_web_process_handle(self):
        """Catches a bad worker handle shutting down Uvicorn and all schedulers."""
        manager = object.__new__(ProcessManager)
        manager.config_name = 'test'
        manager._process_locks = {}
        manager.renderables = []
        manager.thd_log_queue_handler = None
        manager._process = SimpleNamespace(
            pid=os.getpid(),
            _parent_pid=os.getpid(),
            is_alive=lambda: True,
        )

        manager.stop()

        self.assertIn('stop skipped', manager.renderables[-1])

    def test_stop_refuses_a_worker_owned_by_another_parent(self):
        """Catches a stale worker handle from a previous Web child."""
        manager = object.__new__(ProcessManager)
        manager.config_name = 'test'
        manager._process_locks = {}
        manager.renderables = []
        manager.thd_log_queue_handler = None
        manager._process = SimpleNamespace(
            pid=os.getpid() + 1,
            _parent_pid=os.getpid() + 1,
            is_alive=lambda: True,
        )

        manager.stop()

        self.assertIn('stop skipped', manager.renderables[-1])


if __name__ == '__main__':
    unittest.main()
