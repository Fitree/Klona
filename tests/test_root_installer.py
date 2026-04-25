import contextlib
import io
import unittest
from unittest import mock

import install_agent


class RootInstallerTests(unittest.TestCase):
    def test_opencode_install_routes_to_install_only(self):
        with mock.patch(
            "klona_agent.opencode.install.install"
        ) as install, mock.patch(
            "klona_agent.opencode.install.uninstall"
        ) as uninstall:
            result = install_agent.main(["--platform", "opencode"])

        self.assertEqual(result, 0)
        install.assert_called_once_with()
        uninstall.assert_not_called()

    def test_opencode_uninstall_routes_to_uninstall_only(self):
        with mock.patch(
            "klona_agent.opencode.install.install"
        ) as install, mock.patch(
            "klona_agent.opencode.install.uninstall"
        ) as uninstall:
            result = install_agent.main(["--platform", "opencode", "--uninstall"])

        self.assertEqual(result, 0)
        uninstall.assert_called_once_with()
        install.assert_not_called()

    def test_missing_platform_raises_system_exit(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            install_agent.main([])


if __name__ == "__main__":
    unittest.main()
