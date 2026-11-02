"""Publication contracts checked against the workflow actually delivered."""
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)


def evaluate(expression, repository, event, ref, qualify=False):
    expression = expression.removeprefix('${{').removesuffix('}}').strip()
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    expression = re.sub(r'!(?!=)', 'not ', expression)
    return eval(expression, {'__builtins__': {}}, {
        'github': SimpleNamespace(repository=repository, event_name=event, ref=ref),
        'inputs': SimpleNamespace(qualify_published=qualify),
        'startsWith': lambda value, prefix: value.startswith(prefix),
    })


class WorkflowContracts(unittest.TestCase):
    def test_published_compose_requires_own_manual_opt_in(self):
        config = workflow('test.yml')
        job = config['jobs']['published-compose']
        self.assertEqual(config['on']['workflow_dispatch']['inputs']['qualify_published']['default'], 'false')
        self.assertEqual(job['runs-on'], 'ubuntu-24.04')
        self.assertEqual(job['timeout-minutes'], '15')
        self.assertNotIn('permissions', job)
        self.assertEqual(config['permissions'], {'contents': 'read'})
        for repository in ('akaUNik/Amnezia-Web-Panel', 'someone/fork'):
            for event in ('push', 'pull_request', 'workflow_dispatch'):
                for enabled in (True, False):
                    self.assertEqual(bool(evaluate(job['if'], repository, event, 'refs/heads/test', enabled)),
                                     repository == 'akaUNik/Amnezia-Web-Panel' and event == 'workflow_dispatch' and enabled)
                    self.assertEqual(bool(evaluate(config['jobs']['unittest']['if'], repository, event,
                                                   'refs/heads/test', enabled)),
                                     event != 'workflow_dispatch' or not enabled)
        self.assertFalse(any('login-action' in step.get('uses', '') for step in job['steps']))
        self.assertNotIn('GH_TOKEN', str(job))

    def test_binary_matrix_contains_linux_and_windows_only(self):
        build = workflow('build.yml')['jobs']['build']
        entries = build['strategy']['matrix']['include']
        self.assertEqual({row['os'] for row in entries}, {'ubuntu-24.04', 'windows-2022'})
        self.assertEqual({row['system'] for row in entries}, {'Linux', 'Windows'})
        self.assertTrue(all(row['arch'] == 'x86_64' for row in entries))
        self.assertNotIn('macOS', str(build))
        self.assertEqual(build['permissions'],{'contents':'read','actions':'read'})
        self.assertTrue(any('gh run download' in s.get('run','') for s in build['steps']))
        self.assertTrue(any(s.get('name')=='Smoke downloaded binary on native OS' for s in build['steps']))

    def test_shared_verification_and_sha(self):
        build, tests = workflow('build.yml'), workflow('test.yml')
        self.assertEqual(set(tests['on']), {'workflow_call', 'pull_request', 'workflow_dispatch'})
        self.assertEqual(set(build['on']), {'push', 'workflow_dispatch'})
        self.assertEqual(build['jobs']['tests']['uses'], './.github/workflows/test.yml')
        for job in ('build', 'docker'):
            self.assertIn('tests', build['jobs'][job]['needs'])
            self.assertIn('version', build['jobs'][job]['needs'])
        for config in (build, tests):
            self.assertEqual(config['permissions'], {'contents': 'read'})
            for job in config['jobs'].values():
                for step in job.get('steps', []):
                    if step.get('uses', '').startswith('actions/checkout@'):
                        self.assertNotIn('ref', step.get('with', {}))

    def test_publication_event_matrix(self):
        build = workflow('build.yml')
        docker = build['jobs']['docker']
        login = next(s['if'] for s in docker['steps'] if s.get('name') == 'Log in to GHCR')
        push = next(s['with']['push'] for s in docker['steps'] if s.get('name') == 'Build and push')
        release = build['jobs']['release']['if']
        for repository in ('akaUNik/Amnezia-Web-Panel', 'someone/fork'):
            for event in ('push', 'pull_request', 'workflow_dispatch'):
                for ref in ('refs/heads/main', 'refs/heads/develop', 'refs/tags/v1.9.0', 'refs/pull/1/merge'):
                    allowed = repository == 'akaUNik/Amnezia-Web-Panel' and event == 'push'
                    container = allowed and (ref == 'refs/heads/main' or ref.startswith('refs/tags/v'))
                    with self.subTest(repository=repository, event=event, ref=ref):
                        self.assertEqual(bool(evaluate(login, repository, event, ref)), container)
                        self.assertEqual(bool(evaluate(push, repository, event, ref)), container)
                        self.assertEqual(bool(evaluate(release, repository, event, ref)), allowed and ref.startswith('refs/tags/v'))
        self.assertEqual(docker['permissions'], {'contents': 'read', 'packages': 'write'})
        self.assertEqual(build['jobs']['release']['permissions'], {'contents': 'write'})

    def test_actions_are_pinned(self):
        for config in (workflow('build.yml'), workflow('test.yml')):
            for job in config['jobs'].values():
                for step in job.get('steps', []):
                    if 'uses' in step:
                        self.assertRegex(step['uses'], r'^[\w-]+/[\w-]+@[0-9a-f]{40}$')

    def test_container_matrix_stages_unique_outputs(self):
        config = workflow('build.yml')
        docker = config['jobs']['docker']
        entries = docker['strategy']['matrix']['include']
        self.assertEqual({e['variant']:e['platforms'] for e in entries},
                         {'default':'linux/amd64,linux/arm64','warp':'linux/amd64'})
        build = next(s for s in docker['steps'] if s.get('name')=='Build and push')
        self.assertEqual(config['env']['IMAGE_NAME'],'ghcr.io/akaunik/amnezia-panel')
        self.assertEqual(config['env']['DOCKER_BUILD_RECORD_UPLOAD'],'false')
        self.assertIn('staging-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}-', build['with']['tags'])
        self.assertNotIn('latest',build['with']['tags'])
        self.assertIn('org.opencontainers.image.revision=${{ github.sha }}',build['with']['labels'])
        self.assertIn('org.opencontainers.image.source=https://github.com/akaUNik/Amnezia-Web-Panel',build['with']['labels'])

    def test_promotion_waits_for_the_whole_verified_set(self):
        config=workflow('build.yml')
        self.assertEqual(config['concurrency']['cancel-in-progress'],'false')
        self.assertIn('github.repository',config['concurrency']['group'])
        self.assertTrue({'tests','version','build','docker','prepare'} <= set(config['jobs']['promote']['needs']))
        self.assertIn('promote',config['jobs']['release']['needs'])
        downloads=[s['with'] for s in config['jobs']['prepare']['steps'] if s.get('uses','').startswith('actions/download-artifact@')]
        self.assertEqual({s['pattern'] for s in downloads},{'binaries-*','container-*'})
        self.assertNotIn('dockerbuild',str(downloads))
