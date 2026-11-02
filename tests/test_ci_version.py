"""Version validation must not execute application initialization."""
from pathlib import Path
import tempfile
import unittest

from scripts.ci.version import parse_version, read_version, validate_tag


class ReleaseVersionTests(unittest.TestCase):
    def test_static_read_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'app.py'
            source.write_text('raise RuntimeError("must not run")\nCURRENT_VERSION = "v1.9.0"\n')
            self.assertEqual(read_version(source), '1.9.0')
        for version in ('1.9.0', '1.10.0-rc.1'):
            for prefix in ('', 'v'):
                validate_tag(version, prefix + version)
                validate_tag('v' + version, prefix + version)
        with self.assertRaises(ValueError):
            validate_tag('v1.8.4', 'v1.9.0')
        with self.assertRaisesRegex(ValueError, 'must not contain build metadata'):
            validate_tag('v1.9.0+build.7', 'v1.9.0+build.7')

    def test_invalid_versions(self):
        for value in ('vnext', '1.2', '01.2.3', '1.2.3-01', '1.2.3-',
                      '1.2.3+', '1.2.3\n', '1.2.3-rc..1', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_version(value)

    def test_semantic_order(self):
        ordered = ['1.9.0-alpha', '1.9.0-alpha.1', '1.9.0-alpha.beta',
                   '1.9.0-beta', '1.9.0-beta.2', '1.9.0-beta.11',
                   '1.9.0-rc.1', '1.9.0', '1.10.0']
        for a, b in zip(ordered, ordered[1:]):
            self.assertLess(parse_version(a), parse_version(b))
        self.assertEqual(parse_version('v1.9.0+one'), parse_version('1.9.0+two'))

    def test_missing_or_dynamic_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'app.py'
            for text in ('VERSION="1.2.3"', 'CURRENT_VERSION=get_version()',
                         'CURRENT_VERSION="1.2.3"\nCURRENT_VERSION="1.2.4"'):
                source.write_text(text)
                with self.assertRaises(ValueError):
                    read_version(source)
