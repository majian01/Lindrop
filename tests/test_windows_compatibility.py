from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


class WindowsCompatibilityPackagingTests(unittest.TestCase):
    def test_installer_accepts_windows_10_21h2_but_not_older_builds(self):
        text = (ROOT / 'scripts/Lindrop.iss').read_text(encoding='utf-8-sig')
        version = tuple(map(int, re.search(r'^MinVersion=(\d+\.\d+\.\d+)', text, re.M).group(1).split('.')))
        self.assertEqual(version, (10, 0, 19044))
        self.assertLessEqual(version, (10, 0, 19045))
        self.assertLessEqual(version, (10, 0, 22000))

    def test_manifest_declares_windows_10_and_11_compatibility(self):
        manifest = ET.parse(ROOT / 'desktop/app.manifest')
        ids = {entry.attrib['Id'].lower() for entry in manifest.findall('.//{urn:schemas-microsoft-com:compatibility.v1}supportedOS')}
        self.assertIn('{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}', ids)

    def test_private_use_icons_have_a_windows_10_font_fallback(self):
        tree = ET.parse(ROOT / 'desktop/MainWindow.xaml')
        fonts = [node.attrib['FontFamily'] for node in tree.iter() if 'Segoe Fluent Icons' in node.attrib.get('FontFamily', '')]
        self.assertTrue(fonts)
        self.assertTrue(all('Segoe MDL2 Assets' in font for font in fonts))


if __name__ == '__main__':
    unittest.main()
