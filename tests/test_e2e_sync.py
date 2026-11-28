"""O sync inteiro, com o codigo de verdade e um Confluence de mentira (tests/fake_confluence.py)."""
import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_confluence import FakeConfluence, profile_block, section  # noqa: E402

from roleroster import awsconfig, confluence, onboarding, preset, profiles  # noqa: E402

BASE = "[profile roleroster-base]\nregion = us-east-1\n\n# ------------------ ACCOUNTS ------------------\n"


def pages():
    return [
        {"id": "101", "title": "Cliente Alfa", "squad": "11", "ancestors": ["Clientes", "Squad A"],
         "body": section("Switch Role", profile_block("alfa-prod", "111111111111") + profile_block("alfa-dev", "111111111112"))},
        {"id": "102", "title": "Cliente Beta", "squad": "11", "ancestors": ["Clientes", "Squad A"],
         "body": section("Switch Role", profile_block("beta-prod", "222222222221"))},
        {"id": "103", "title": "Cliente Gama", "squad": "22", "ancestors": ["Clientes", "Squad B"],
         "body": section("Switch Role", profile_block("gama prod", "333333333331"))},
        {"id": "104", "title": "Cliente Velho", "squad": "11", "ancestors": ["Clientes", "Squad A", "Inativos 2023"],
         "body": section("Switch Role", profile_block("velho-prod", "444444444441"))},
        {"id": "105", "title": "Sem perfis", "squad": "11", "ancestors": ["Clientes", "Squad A"], "body": "<p>nada aqui</p>"},
    ]


class QuietConsole:
    def interactive(self):
        return False


class SyncCase(unittest.TestCase):
    """Config temporario, Confluence de mentira e o ambiente isolado para os testes do sync."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = Path(tmp.name) / "config"
        self.config.write_text(BASE)
        self.warnings = []
        self.fake = FakeConfluence(folders={"9": [("11", "Squad A"), ("22", "Squad B")]}, pages=pages())
        self.fake.__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        env = {
            "AWS_CONFIG_FILE": str(self.config), "AWS_SHARED_CREDENTIALS_FILE": str(Path(tmp.name) / "credentials"),
            "CONFLUENCE_BASE_URL": self.fake.url, "CONFLUENCE_EMAIL": "dev@acme.com", "CONFLUENCE_TOKEN": "tok",
            "CONFLUENCE_SQUAD_IDS": "11,22", "ROLEROSTER_SOURCE_PROFILE": "roleroster-base", "ROLEROSTER_BROWSER": "none",
            "ROLEROSTER_INACTIVE_PREFIX": "Inativ", "ROLEROSTER_HOME": str(Path(tmp.name) / "home"),
        }
        for patch in (mock.patch.dict(os.environ, env, clear=True), mock.patch.object(profiles, "warn", self.warnings.append)):
            patch.start()
            self.addCleanup(patch.stop)

    def sync(self, inactives=False):
        self.warnings.clear()
        args = argparse.Namespace(aws_config=None, inactives=inactives)
        return profiles.cmd_sync(args, QuietConsole())

class SyncEndToEndTest(SyncCase):
    def test_the_active_profiles_land_in_the_config_and_the_rest_is_left_out(self):
        self.assertEqual(self.sync(), 0)
        text = self.config.read_text()
        for wanted in ("[profile alfa-prod]", "[profile alfa-dev]", "[profile beta-prod]"):
            self.assertIn(wanted, text)
        self.assertNotIn("velho-prod", text)  # pasta de inativos
        self.assertIn("[profile gama-prod]", text)  # o espaco do nome virou hifen
        self.assertIn("role_arn = arn:aws:iam::111111111111:role/acme-login", text)
        self.assertIn("source_profile = roleroster-base", text)
        self.assertTrue(text.startswith(BASE.split("# ----")[0]))  # o que o usuario escreveu continua no lugar

    def test_the_summary_reports_counts_and_hidden_inactives(self):
        self.sync()
        summary = self.warnings[0]
        self.assertIn("4 perfis · 2 squads", summary)
        self.assertIn("+4 -0 ~0", summary)
        self.assertIn("1 inativos fora (-i)", summary)

    def test_a_second_sync_changes_nothing(self):
        self.sync()
        before = self.config.read_text()
        self.sync()
        self.assertEqual(self.config.read_text(), before)
        self.assertNotIn("+", self.warnings[0])  # sem mudanca, a linha nao traz contadores

    def test_including_the_inactives_marks_them(self):
        self.sync(inactives=True)
        text = self.config.read_text()
        self.assertIn("[profile velho-prod]", text)
        self.assertIn("[inativo]", text)

    def test_a_removed_page_removes_its_profiles_on_the_next_sync(self):
        self.sync()
        self.fake.pages = [p for p in self.fake.pages if p["id"] != "102"]
        self.sync()
        self.assertNotIn("beta-prod", self.config.read_text())
        self.assertIn("-1", self.warnings[0])

    def test_the_manual_section_is_never_touched(self):
        self.config.write_text(BASE + "\n" + awsconfig.MANUAL_HEADER + "\n[profile meu]\nregion = sa-east-1\n")
        self.sync()
        self.assertIn("[profile meu]\nregion = sa-east-1", self.config.read_text())

    def test_pagination_is_followed_until_the_last_page(self):
        self.fake.page_size = 1
        self.sync()
        text = self.config.read_text()
        self.assertTrue(all(name in text for name in ("alfa-prod", "alfa-dev", "beta-prod", "gama-prod")))
        listings = [r for r in self.fake.requests if "ancestor" in r]
        self.assertGreater(len(listings), 1)  # varias requisicoes para uma lista so

    def test_a_wrong_token_is_reported_and_the_config_is_left_alone(self):
        os.environ["CONFLUENCE_TOKEN"] = "errado"
        before = self.config.read_text()
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.config.read_text(), before)
        self.assertIn("Sync ignorado", self.warnings[0])
        self.assertIn("token", self.warnings[0])

    def test_a_server_that_is_down_is_reported_and_the_config_is_left_alone(self):
        self.fake.__exit__(None, None, None)
        before = self.config.read_text()
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.config.read_text(), before)
        self.assertIn("sem conexao", self.warnings[0])
        self.fake.__enter__()  # o addCleanup encerra de novo

    def test_a_company_with_other_conventions_works_through_settings(self):
        self.fake.pages = [{"id": "201", "title": "Acme X", "squad": "11", "ancestors": ["Archived 2020"],
                            "body": section("Acessos AWS", profile_block("x-prod", "555555555551"))},
                           {"id": "202", "title": "Acme Y", "squad": "11", "ancestors": ["Ativos"],
                            "body": section("Acessos AWS", profile_block("y-prod", "555555555552"))}]
        os.environ.update({"ROLEROSTER_ROLE_SECTION": "Acessos AWS", "ROLEROSTER_INACTIVE_PREFIX": "Archived"})
        self.sync()
        text = self.config.read_text()
        self.assertIn("[profile y-prod]", text)
        self.assertNotIn("x-prod", text)

    def test_with_the_default_section_title_another_title_finds_nothing(self):
        self.fake.pages = [{"id": "201", "title": "Acme", "squad": "11", "ancestors": [],
                            "body": section("Acessos AWS", profile_block("x-prod", "555555555551"))}]
        self.sync()
        self.assertIn("0 perfis", self.warnings[0])

    def test_conflicting_names_are_dropped_instead_of_guessing_the_account(self):
        self.fake.pages = [
            {"id": "301", "title": "A", "squad": "11", "ancestors": [], "body": section("Switch Role", profile_block("igual", "666666666661"))},
            {"id": "302", "title": "B", "squad": "11", "ancestors": [], "body": section("Switch Role", profile_block("igual", "666666666662"))},
        ]
        self.sync()
        self.assertNotIn("[profile igual]", self.config.read_text())
        self.assertTrue(any("igual" in line and "ARNs diferentes" in line for line in self.warnings))

    def test_listing_the_squad_folders(self):
        base, auth = confluence.require_config()
        self.assertEqual(confluence.list_folders(base, auth, "9"), [{"id": "11", "title": "Squad A"}, {"id": "22", "title": "Squad B"}])

    def test_credentials_are_checked_with_the_lightweight_endpoint(self):
        confluence.check_credentials(self.fake.url, "dev@acme.com", "tok")
        with self.assertRaises(confluence.ConfluenceAuthError):
            confluence.check_credentials(self.fake.url, "dev@acme.com", "errado")
        self.assertTrue(all(r.endswith("/user/current") for r in self.fake.requests))

    def test_onboarding_helpers_see_the_same_server(self):
        base, _ = confluence.require_config()
        self.assertEqual(base, self.fake.url)
        self.assertEqual(onboarding.normalize_base_url("acme"), "https://acme.atlassian.net")


if __name__ == "__main__":
    unittest.main()


PRESET_PROFILES = """
[[aws.profiles]]
name = "Acme---Sandbox"
account_id = "555555555555"
role = "AdministratorAccess"

[[aws.profiles]]
name = "Acme---Production"
account_id = "666666666666"
role = "ViewOnlyAccess"
region = "sa-east-1"
"""


class PresetProfilesTest(SyncCase):
    def install(self, text=PRESET_PROFILES):
        preset.install_text(text)

    def test_the_preset_profiles_land_in_their_own_block_above_the_confluence_one(self):
        self.install()
        self.sync()
        text = self.config.read_text()
        self.assertEqual(text.count("[profile Acme---Sandbox]"), 1)
        self.assertIn("sso_account_id = 555555555555\nsso_role_name = AdministratorAccess", text)
        self.assertIn("sso_role_name = ViewOnlyAccess\nregion = sa-east-1", text)
        positions = [text.index(x) for x in (awsconfig.ACCOUNTS_HEADER, awsconfig.PRESET_BEGIN, awsconfig.PRESET_END,
                                               awsconfig.BEGIN, awsconfig.END)]
        self.assertEqual(positions, sorted(positions))

    def test_a_second_sync_changes_nothing(self):
        self.install()
        self.sync()
        before = self.config.read_text()
        self.sync()
        self.assertEqual(self.config.read_text(), before)

    def test_changing_the_preset_changes_the_profiles_and_removing_it_removes_the_block(self):
        self.install()
        self.sync()
        self.install(PRESET_PROFILES.replace("ViewOnlyAccess", "ReadOnly"))
        self.sync()
        self.assertIn("sso_role_name = ReadOnly", self.config.read_text())
        self.install("")
        self.sync()
        text = self.config.read_text()
        self.assertNotIn("Acme---Sandbox", text)
        self.assertNotIn(awsconfig.PRESET_BEGIN, text)
        self.assertIn("[profile alfa-prod]", text)

    def test_a_manual_profile_with_the_same_name_wins(self):
        self.config.write_text(BASE + "\n" + awsconfig.MANUAL_HEADER + "\n[profile Acme---Sandbox]\nregion = eu-west-1\n")
        self.install()
        self.sync()
        text = self.config.read_text()
        self.assertEqual(text.count("[profile Acme---Sandbox]"), 1)
        self.assertIn("[profile Acme---Sandbox]\nregion = eu-west-1", text)
        self.assertIn("[profile Acme---Production]", text)

    def test_a_confluence_profile_with_the_same_name_yields_to_the_preset(self):
        self.install(PRESET_PROFILES.replace("Acme---Sandbox", "alfa-prod"))
        self.sync()
        text = self.config.read_text()
        self.assertEqual(text.count("[profile alfa-prod]"), 1)
        self.assertIn("sso_account_id = 555555555555", text)
        self.assertNotIn("111111111111:role/acme-login\nregion = us-east-1\n\n[profile alfa-dev]\nrole_arn = arn:aws:iam::111111111111:role/acme-login\nregion = us-east-1\n\n[profile alfa-prod]", text)

    def test_the_session_and_region_follow_the_settings(self):
        self.install()
        self.sync()
        text = self.config.read_text()
        self.assertIn("sso_session = roleroster\nsso_region = us-east-1\nsso_account_id = 555555555555", text)
        self.assertIn("sso_role_name = AdministratorAccess\nregion = us-east-1\noutput = json", text)

    def test_the_manual_section_below_is_untouched(self):
        self.config.write_text(BASE + "\n" + awsconfig.MANUAL_HEADER + "\n[profile meu]\nregion = sa-east-1\n")
        self.install()
        self.sync()
        self.assertIn("[profile meu]\nregion = sa-east-1", self.config.read_text())
