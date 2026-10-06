from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chaos_agent.remote.transport import parse_host_options


class HostTransportTests(unittest.TestCase):
    def test_default_and_loopback_remain_local(self):
        config = parse_host_options(())
        self.assertEqual((config.bind, config.port, config.scheme), ("127.0.0.1", 8787, "http"))
        self.assertEqual(parse_host_options(("--bind", "::1")).scheme, "http")

    def test_plaintext_remote_requires_explicit_private_interface_debug(self):
        for arguments in (("--lan",), ("--lan", "--insecure-lan-debug"),
                          ("--bind", "192.168.1.10"),
                          ("--bind", "8.8.8.8", "--insecure-lan-debug"),
                          ("--bind", "0.0.0.0", "--insecure-lan-debug")):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                parse_host_options(arguments)
        config = parse_host_options(("--bind", "192.168.1.10", "--insecure-lan-debug"))
        self.assertTrue(config.insecure_lan_debug)

    def test_tls_requires_both_real_files_and_readable_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            certificate, key = root / "test-cert.pem", root / "test-key.pem"
            certificate.write_text("INVALID TEST CERTIFICATE", encoding="ascii")
            key.write_text("INVALID TEST KEY", encoding="ascii")
            for arguments in (("--tls-cert", str(certificate)),
                              ("--tls-cert", str(certificate), "--tls-key", str(key)),
                              ("--tls-cert", str(root / "missing"), "--tls-key", str(key))):
                with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                    parse_host_options(arguments)
            # Parsing test only: actual trust/handshake has a separate integration fixture.
            with patch("chaos_agent.remote.transport.ssl.SSLContext") as context:
                config = parse_host_options(("--lan", "--tls-cert", str(certificate), "--tls-key", str(key)))
                context.return_value.load_cert_chain.assert_called_once_with(str(certificate), str(key))
            self.assertEqual((config.bind, config.scheme), ("0.0.0.0", "https"))

    def test_invalid_or_repeated_options_fail_before_runtime(self):
        for arguments in (("--port", "0"), ("--port", "abc"), ("--port", "65536"),
                          ("--bind",), ("--bind", "example.com"),
                          ("--port", "8787", "--port", "8788"),
                          ("--lan", "--bind", "127.0.0.1")):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                parse_host_options(arguments)
