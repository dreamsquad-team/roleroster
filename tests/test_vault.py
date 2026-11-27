import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeKeyring  # noqa: E402

from roleroster import confluence, vault  # noqa: E402


class VaultTest(unittest.TestCase):
    def use(self, fake):
        patch = mock.patch.object(vault, "_keyring", lambda: fake)
        patch.start()
        self.addCleanup(patch.stop)
        return fake

    def test_a_token_goes_in_and_comes_back(self):
        fake = self.use(FakeKeyring())
        self.assertTrue(vault.set_token("abc"))
        self.assertEqual(vault.get_token(), "abc")
        self.assertEqual(fake.store, {(vault.SERVICE, vault.ACCOUNT): "abc"})

    def test_without_a_vault_everything_reports_unavailable_without_raising(self):
        self.use(None)
        self.assertFalse(vault.available())
        self.assertFalse(vault.set_token("abc"))
        self.assertEqual(vault.get_token(), "")
        self.assertFalse(vault.delete_token())
        self.assertEqual(vault.name(), "arquivo .env")

    def test_a_write_that_does_not_read_back_is_a_failure(self):
        self.use(FakeKeyring(corrupt=True))
        self.assertFalse(vault.set_token("abc"))

    def test_vault_errors_never_escape(self):
        self.use(FakeKeyring(fail_set=True, fail_get=True))
        self.assertFalse(vault.set_token("abc"))
        self.assertEqual(vault.get_token(), "")

    def test_delete(self):
        self.use(FakeKeyring())
        vault.set_token("abc")
        self.assertTrue(vault.delete_token())
        self.assertEqual(vault.get_token(), "")
        self.assertFalse(vault.delete_token())

    def test_the_vault_name_is_readable_per_system(self):
        for module, label in (("keyring.backends.macOS", "Keychain do macOS"),
                              ("keyring.backends.Windows", "Credential Manager do Windows"),
                              ("keyring.backends.SecretService", "Secret Service"),
                              ("keyring.backends.outro", "cofre do sistema")):
            self.use(FakeKeyring(module=module))
            self.assertEqual(vault.name(), label)

    def test_the_fail_and_null_backends_mean_no_vault(self):
        for module, usable in (("keyring.backends.fail", False), ("keyring.backends.null", False),
                               ("keyring.backends.macOS", True), ("keyring.backends.SecretService", True)):
            self.assertEqual(vault.is_usable(type("K", (), {"__module__": module})()), usable, module)


class GetTokenOrderTest(unittest.TestCase):
    def test_the_environment_wins_over_the_vault(self):
        with mock.patch.dict(os.environ, {"CONFLUENCE_TOKEN": "do-env"}), \
                mock.patch.object(vault, "_keyring", lambda: FakeKeyring()):
            vault.set_token("do-cofre")
            self.assertEqual(confluence.get_token(), "do-env")

    def test_without_the_environment_the_vault_answers(self):
        fake = FakeKeyring()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(vault, "_keyring", lambda: fake):
            vault.set_token("do-cofre")
            self.assertEqual(confluence.get_token(), "do-cofre")

    def test_nothing_anywhere_is_an_empty_string(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(confluence.get_token(), "")


if __name__ == "__main__":
    unittest.main()
