import unittest

import gui


class WebProcessSupervisorTest(unittest.TestCase):
    def test_unexpected_server_exit_starts_replacement(self):
        """Catches a clean Uvicorn child exit taking down the persistent Web service."""
        supervisor = getattr(gui, 'supervise_web_process', None)
        self.assertIsNotNone(supervisor, 'gui.py must expose the Web process supervisor')

        started = []
        joined = []
        events_created = 0

        class Event:
            def __init__(self, stop_supervisor):
                self.stop_supervisor = stop_supervisor

            def wait(self, timeout):
                if self.stop_supervisor:
                    raise KeyboardInterrupt
                return False

        class Process:
            def __init__(self, number):
                self.number = number

            def start(self):
                started.append(self.number)

            def is_alive(self):
                return False

            def join(self):
                joined.append(self.number)

        def event_factory():
            nonlocal events_created
            events_created += 1
            return Event(stop_supervisor=events_created == 2)

        def process_factory(event):
            return Process(events_created)

        supervisor(
            process_factory=process_factory,
            event_factory=event_factory,
            wait_interval=0,
        )

        self.assertEqual(started, [1, 2])
        self.assertEqual(joined, [1, 2])


if __name__ == '__main__':
    unittest.main()
