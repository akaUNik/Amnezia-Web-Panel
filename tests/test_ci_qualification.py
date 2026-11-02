"""Public qualification rejects unsafe selectors and always cleans its own fixture."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.ci import qualify


class PublishedQualificationTests(unittest.TestCase):
    def pair(self):
        return {variant: {'sha': 'a' * 40, 'version': '1.9.0', 'digest': 'sha256:' + 'b' * 64,
                          'platforms': sorted(platforms)}
                for variant, platforms in qualify.PLATFORMS.items()}

    def test_only_fixed_repository_tag_and_full_sha_are_accepted(self):
        for tag in ('latest', '1.9.0-rc.1', 'sha-' + 'a' * 40):
            qualify.validate_inputs(tag, 'a' * 40)
        for tag in ('../image', 'someone/image:latest', 'latest; true', '$(id)', '', 'x' * 129):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                qualify.validate_inputs(tag, '')
        for sha in ('main', 'a' * 39, 'A' * 40, 'a' * 40 + '\n'):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                qualify.validate_inputs('latest', sha)

    def test_mixed_missing_and_wrong_platform_images_fail_before_startup(self):
        valid = self.pair()
        qualify.validate_pair(valid, 'a' * 40)
        candidates = [{}, {**valid, 'warp': None}, {'default': valid['default']}]
        for field, value in (('sha', 'c' * 40), ('version', '1.8.4'), ('platforms', ['linux/arm64'])):
            changed = copy.deepcopy(valid)
            changed['warp'][field] = value
            candidates.append(changed)
        for pair in candidates:
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                qualify.validate_pair(pair, 'a' * 40)
        with self.assertRaises(ValueError):
            qualify.validate_pair(valid, 'c' * 40)

    def test_failed_compose_start_cleans_only_the_generated_project(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(qualify.socket, 'socket') as sock, \
             patch.object(qualify, 'run', side_effect=[RuntimeError('fixture startup failure'), '', '']) as run:
            sock.return_value.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 32000)
            with self.assertRaisesRegex(RuntimeError, 'fixture startup failure'):
                qualify.exercise('warp', self.pair(), Path(directory), {})
            start = run.call_args_list[0].args[0]
            cleanup = run.call_args_list[-1].args[0]
            project = start[start.index('--project-name') + 1]
            self.assertRegex(project, r'^panel-qualification-warp-[0-9a-f]{10}$')
            self.assertEqual(cleanup[:-2], start[:-4])
            self.assertEqual(cleanup[-2:], ['down', '--volumes'])
            self.assertNotIn('--remove-orphans', cleanup)
