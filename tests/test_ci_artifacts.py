import json
from pathlib import Path
import tempfile
import unittest

from scripts.ci import artifacts as a


SHA = 'a' * 40
VERSION = '1.9.0'


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binaries, self.digests = self.root/'binaries', self.root/'digests'
        self.binaries.mkdir()
        self.digests.mkdir()
        for system, (filename, _) in a.BINARIES.items():
            (self.binaries/filename).write_bytes(('binary:'+system).encode())
            a.binary_metadata(self.binaries, system, SHA, VERSION, check_host=False)
        for variant in a.PLATFORMS:
            a.write_json(self.digests/f'digest-{variant}.json',
                         a.digest_metadata(variant, 'sha256:'+'b'*64, SHA, VERSION))

    def test_complete_assets_and_recomputed_checksums(self):
        output = self.root/'release'
        info = a.prepare(self.binaries, self.digests, output, SHA, VERSION, 'refs/tags/v1.9.0', 'https://github.com/akaUNik/Amnezia-Web-Panel/actions/runs/1')
        self.assertEqual(len(list(output.iterdir())), 4)
        self.assertEqual(set(info['containers']), {'default','warp'})
        for row in (output/'SHA256SUMS').read_text().splitlines():
            digest, filename = row.split('  ')
            self.assertEqual(a.checksum(output/filename), digest)
        self.assertEqual(a.verify_release(output, SHA, VERSION), info)
        (output/'Amnezia-Web-Panel-Linux').write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            a.verify_release(output, SHA, VERSION)

    def test_missing_and_wrong_binary_identity(self):
        for field, value in (('sha', 'c'*40), ('arch', 'arm64'), ('version','1.8.4'), ('sha256','bad')):
            path = self.binaries/'metadata-Linux.json'
            original = path.read_text()
            data = json.loads(original)
            data[field] = value
            a.write_json(path,data)
            with self.subTest(field=field), self.assertRaises(ValueError):
                a.validate_binaries(self.binaries, SHA, VERSION)
            path.write_text(original)
        (self.binaries/'Amnezia-Web-Panel-Linux').unlink()
        with self.assertRaises(ValueError):
            a.validate_binaries(self.binaries, SHA, VERSION)
        with self.assertRaises(ValueError):
            a.binary_metadata(self.binaries, 'Linux', SHA, VERSION, check_host=False)

    def test_missing_wrong_or_extra_digest(self):
        path = self.digests/'digest-warp.json'
        original = path.read_text()
        for field, value in (('digest','sha256:bad'), ('sha','c'*40), ('platforms',['linux/arm64'])):
            data = json.loads(original)
            data[field] = value
            a.write_json(path,data)
            with self.subTest(field=field), self.assertRaises(ValueError):
                a.prepare(self.binaries,self.digests,self.root/'release',SHA,VERSION,'','')
        path.unlink()
        with self.assertRaises(ValueError):
            a.prepare(self.binaries,self.digests,self.root/'release',SHA,VERSION,'','')
        path.write_text(original)
        (self.digests/'record.dockerbuild').write_bytes(b'unrelated')
        with self.assertRaises(ValueError):
            a.validate_digests(self.digests,SHA,VERSION)

    def test_extra_binary_artifacts_rejected(self):
        (self.binaries/'record.dockerbuild').write_bytes(b'unrelated')
        with self.assertRaises(ValueError):
            a.validate_binaries(self.binaries,SHA,VERSION)

    def test_macos_binary_is_not_part_of_distribution(self):
        self.assertEqual(set(a.BINARIES), {'Linux', 'Windows'})
        (self.binaries/'Amnezia-Web-Panel-macOS').write_bytes(b'excluded')
        with self.assertRaises(ValueError):
            a.validate_binaries(self.binaries,SHA,VERSION)

    def test_runner_architecture_checked(self):
        from unittest.mock import patch
        with patch.object(a.platform,'system',return_value='Linux'), patch.object(a.platform,'machine',return_value='aarch64'):
            with self.assertRaises(ValueError):
                a.binary_metadata(self.binaries,'Linux',SHA,VERSION)
