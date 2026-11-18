"""Public releases include runtimes but reject private and vendor files."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/Test-ReleasePackage.ps1'
POWERSHELL = shutil.which('powershell.exe')


@unittest.skipUnless(os.name == 'nt' and POWERSHELL, 'Windows packaging check')
class ReleasePackagingTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'Release packages need an enforced completeness check')
        scratch = ROOT / 'work' / 'packaging-tests'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.package = Path(self.temporary.name)
        # These are placeholders for a layout check, not runnable binaries.
        files = (
            'app/Lindrop.exe', 'app/Lindrop.dll', 'app/Lindrop.deps.json',
            'app/Lindrop.runtimeconfig.json', 'app/coreclr.dll', 'app/hostfxr.dll',
            'app/hostpolicy.dll', 'app/System.Private.CoreLib.dll',
            'app/PresentationFramework.dll', 'app/lindrop-transfer.exe',
            'app/transport.py', 'app/backend/receiver.py',
            'app/backend/independent_server.py', 'app/backend/bks_identity.py', 'app/backend/official_server.py',
            'app/python/python.exe', 'app/python/python312.dll',
            'app/python/LICENSE.txt', 'app/python/vcruntime140.dll',
            'app/python/vcruntime140_1.dll', 'Install-Lindrop.cmd',
            'Install-Lindrop.ps1', 'Uninstall-Lindrop.cmd', 'Uninstall-Lindrop.ps1',
            'Set-ReleaseIntegrity.ps1', 'ReleaseFileIntegrity.cs', 'README.md',
            'LICENSE', 'BUILD_INFO.md', 'LICENSES/Tandem-MIT.txt',
        )
        for relative in files:
            file = self.package / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'layout fixture')

    def validate(self):
        return subprocess.run(
            [POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
             'Bypass', '-File', str(SCRIPT), '-PackagePath', str(self.package)],
            capture_output=True, timeout=15,
        )

    def test_complete_package_is_accepted(self):
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bundled_identity_is_rejected(self):
        identity = self.package / 'app/Identity/pc.bks'
        identity.parent.mkdir()
        identity.write_bytes(b'not a real identity')
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Identity', result.stdout + result.stderr)

    def test_missing_vc_runtime_is_rejected(self):
        (self.package / 'app/python/vcruntime140_1.dll').unlink()
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'vcruntime140_1.dll', result.stdout + result.stderr)

    def test_key_outside_identity_directory_is_rejected(self):
        (self.package / 'app/backend/example.pfx').write_bytes(b'not a real key')
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'example.pfx', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
