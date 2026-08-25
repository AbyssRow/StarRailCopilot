import queue
import unittest
from unittest.mock import Mock, patch

from module.config.utils import filepath_args, read_file
from module.webui.app import (
    AlasGUI,
    filter_linux_queue_empty_options,
    filter_platform_args,
)


class PlatformArgumentFilterTest(unittest.TestCase):
    def test_linux_keeps_android_avd_option_and_settings(self):
        """Catches Linux losing access to its AVD configuration."""
        filtered = filter_platform_args(
            read_file(filepath_args('args')),
            is_linux=True,
        )

        self.assertIn('LinuxAVD', filtered['Alas'])
        self.assertIn(
            'AndroidAVD',
            filtered['Alas']['EmulatorInfo']['Emulator']['option'],
        )

    def test_non_linux_removes_android_avd_option_and_settings(self):
        """Catches Linux-only controls leaking into Windows or macOS."""
        original = read_file(filepath_args('args'))
        filtered = filter_platform_args(original, is_linux=False)

        self.assertNotIn('LinuxAVD', filtered['Alas'])
        self.assertNotIn(
            'AndroidAVD',
            filtered['Alas']['EmulatorInfo']['Emulator']['option'],
        )
        self.assertIn('LinuxAVD', original['Alas'])


class LinuxAVDVisibilityTest(unittest.TestCase):
    def test_linux_non_avd_hides_close_emulator_choice(self):
        options = ['stay_there', 'goto_main', 'close_game', 'close_emulator']

        self.assertNotIn(
            'close_emulator',
            filter_linux_queue_empty_options(options, 'MEmuPlayer', True),
        )
        self.assertIn(
            'close_emulator',
            filter_linux_queue_empty_options(options, 'AndroidAVD', True),
        )

    def test_other_platforms_keep_upstream_queue_options(self):
        options = ['stay_there', 'goto_main', 'close_game', 'close_emulator']

        self.assertEqual(
            filter_linux_queue_empty_options(options, 'MEmuPlayer', False),
            options,
        )

    def test_linux_toggles_group_and_navigator_for_emulator_selection(self):
        """Catches the AVD group remaining visible for another emulator."""
        with patch('module.webui.app.IS_LINUX', True), \
                patch('module.webui.app.run_js') as run_js:
            AlasGUI._set_linux_avd_visibility('auto')
            AlasGUI._set_linux_avd_visibility('AndroidAVD')

        hidden, visible = [call.args[0] for call in run_js.call_args_list]
        self.assertIn('.toggle(false)', hidden)
        self.assertIn('.toggle(true)', visible)
        self.assertIn('group_LinuxAVD', visible)
        self.assertIn('navigator_LinuxAVD', visible)
        self.assertIn('WhenTaskQueueEmpty', hidden)
        self.assertIn('WhenTaskQueueEmpty', visible)

    def test_non_linux_never_runs_linux_visibility_javascript(self):
        """Catches an absent non-Linux group being manipulated by Linux JS."""
        with patch('module.webui.app.IS_LINUX', False), \
                patch('module.webui.app.run_js') as run_js:
            AlasGUI._set_linux_avd_visibility('AndroidAVD')

        run_js.assert_not_called()

    def test_emulator_change_queues_save_and_updates_visibility(self):
        """Catches dynamic visibility becoming stale until a page reload."""
        gui = object.__new__(AlasGUI)
        gui.modified_config_queue = queue.Queue()
        gui._set_linux_avd_visibility = Mock()

        gui._queue_alas_config_change(
            'Alas.EmulatorInfo.Emulator',
            'AndroidAVD',
        )

        self.assertEqual(
            gui.modified_config_queue.get_nowait(),
            {
                'name': 'Alas.EmulatorInfo.Emulator',
                'value': 'AndroidAVD',
            },
        )
        gui._set_linux_avd_visibility.assert_called_once_with('AndroidAVD')


if __name__ == '__main__':
    unittest.main()
