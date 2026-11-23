import pathlib
import unittest


class DockerfileDependencyTests(unittest.TestCase):
    def test_panel_image_contains_acme_runtime_dependencies(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        for image in ('Dockerfile', 'Dockerfile.warp'):
            with self.subTest(image=image):
                dockerfile = (root / image).read_text(encoding='utf-8')
                self.assertRegex(dockerfile, r'(?s)apt-get install.*?\bcurl\b')
                self.assertIn('PANEL_DUCKDNS_STATE_DIR=/app/data/duckdns', dockerfile)


if __name__ == '__main__':
    unittest.main()
