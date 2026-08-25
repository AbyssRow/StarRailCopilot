from pathlib import Path
import unittest

import yaml


class SyncUpstreamWorkflowContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parents[1] / '.github' / 'workflows' / 'sync-upstream.yml'
        cls.workflow = yaml.load(path.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)

    def test_schedule_checks_upstream_every_four_hours(self):
        """Catches a game update waiting days before the fork checks upstream."""
        schedules = self.workflow['on']['schedule']

        self.assertEqual(schedules, [{'cron': '37 */4 * * *'}])

    def test_failure_and_recovery_jobs_can_manage_one_alert_issue(self):
        """Catches failed syncs becoming silent or recovery leaving a stale alert open."""
        self.assertEqual(self.workflow['permissions'].get('issues'), 'write')
        jobs = self.workflow['jobs']

        failure = jobs.get('notify-failure', {})
        self.assertEqual(failure.get('needs'), 'merge-test-push')
        self.assertIn("needs.merge-test-push.result == 'failure'", failure.get('if', ''))

        recovery = jobs.get('resolve-alert', {})
        self.assertEqual(recovery.get('needs'), 'merge-test-push')
        self.assertIn("needs.merge-test-push.result == 'success'", recovery.get('if', ''))
        recovery_script = recovery.get('steps', [{}])[0].get('run', '')
        self.assertIn('repos/${REPOSITORY}/labels', recovery_script)

    def test_manual_dispatch_can_exercise_the_failure_alert(self):
        """Catches an alert path that cannot be verified without breaking a real merge."""
        dispatch = self.workflow['on']['workflow_dispatch']
        if not isinstance(dispatch, dict):
            dispatch = {}
        simulate = dispatch.get('inputs', {}).get('simulate_failure', {})

        self.assertEqual(simulate.get('type'), 'boolean')
        self.assertEqual(simulate.get('default'), 'false')

    def test_verification_compiles_the_application_entrypoint(self):
        """Catches upstream sync verification omitting src.py."""
        steps = self.workflow['jobs']['merge-test-push']['steps']
        verification = next(
            step for step in steps if step.get('name') == 'Verify Linux AVD patch'
        )

        self.assertIn('python -m compileall -q module tests src.py', verification['run'])


if __name__ == '__main__':
    unittest.main()
