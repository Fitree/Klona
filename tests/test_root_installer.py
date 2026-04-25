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
        install.assert_called_once_with(mcp_url=None, mcp_token=None)
        uninstall.assert_not_called()

    def test_opencode_install_passes_mcp_args_to_install(self):
        with mock.patch(
            "klona_agent.opencode.install.install"
        ) as install, mock.patch(
            "klona_agent.opencode.install.uninstall"
        ) as uninstall:
            result = install_agent.main(
                [
                    "--platform",
                    "opencode",
                    "--klona-memory-server-url",
                    "https://memory.example/mcp",
                    "--klona-memory-server-token",
                    "secret-token",
                ]
            )

        self.assertEqual(result, 0)
        install.assert_called_once_with(
            mcp_url="https://memory.example/mcp", mcp_token="secret-token"
        )
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

    def test_opencode_uninstall_with_mcp_args_routes_to_uninstall_only(self):
        with mock.patch(
            "klona_agent.opencode.install.install"
        ) as install, mock.patch(
            "klona_agent.opencode.install.uninstall"
        ) as uninstall:
            result = install_agent.main(
                [
                    "--platform",
                    "opencode",
                    "--uninstall",
                    "--klona-memory-server-url",
                    "https://memory.example/mcp",
                    "--klona-memory-server-token",
                    "secret-token",
                ]
            )

        self.assertEqual(result, 0)
        uninstall.assert_called_once_with()
        install.assert_not_called()

    def test_missing_platform_raises_system_exit(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            install_agent.main([])


if __name__ == "__main__":
    unittest.main()
