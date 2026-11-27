import argparse
import io
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock  # noqa: E402

from roleroster import (
    confluence,  # noqa: E402
    onboarding,  # noqa: E402
    profiles,  # noqa: E402
)
from roleroster.confluence import ConfluenceAuthError, ConfluenceError  # noqa: E402
from roleroster.profiles import (  # noqa: E402
    cmd_prune,
    detail,
    exact_match,
    format_lines,
    prune_exported,
    read_entries,
)

CONFIG = """# ------------------ DREAMSQUAD ------------------
[profile DREAMSQUAD]
sso_account_id = 999999999999
sso_role_name = AssumeRole
region = us-east-1

# >>> confluence-sync (gerado pelo script, nao edite) >>>
# Fonte: paginas do Confluence, secao Switch Role.

# --- Acme Corp

[profile acme-prod]
source_profile = DREAMSQUAD
role_arn = arn:aws:iam::111111111111:role/dreamsquad-login
region = sa-east-1

[profile acme-dev]
source_profile = DREAMSQUAD
role_arn = arn:aws:iam::222222222222:role/admin/ops

# --- Projeto Antigo Com Um Titulo Muito Longo Mesmo [inativo]

[profile antigo-prod]
source_profile = DREAMSQUAD
role_arn = arn:aws:iam::444444444444:role/dreamsquad-login
# <<< confluence-sync <<<

[sso-session dreamsquad-login]
sso_region = us-east-1

[profile acme-prod-exported]
region = us-east-1
"""

CREDENTIALS = """[manual]
aws_access_key_id = A

[acme-prod-exported]
aws_access_key_id = B

[acme-dev-exported]
aws_access_key_id = C

[acme-prod-exported-exported]
aws_access_key_id = D
"""


class ReadEntriesTest(unittest.TestCase):
    def setUp(self):
        self.entries = {e["name"]: e for e in read_entries(CONFIG)}

    def test_excludes_exported_and_sso_session(self):
        self.assertEqual(set(self.entries), {"DREAMSQUAD", "acme-prod", "acme-dev", "antigo-prod"})

    def test_title_comes_from_nearest_heading_above(self):
        self.assertEqual(self.entries["DREAMSQUAD"]["title"], "DREAMSQUAD")
        self.assertEqual(self.entries["acme-prod"]["title"], "Acme Corp")

    def test_detail_for_assumed_role_and_for_sso(self):
        self.assertEqual(detail(self.entries["acme-prod"]), "111111111111  dreamsquad-login  sa-east-1")
        self.assertEqual(detail(self.entries["acme-dev"]), "222222222222  ops")
        self.assertEqual(detail(self.entries["DREAMSQUAD"]), "999999999999  AssumeRole  us-east-1")

    def test_lines_start_with_the_profile_name_and_are_searchable_by_title_and_account(self):
        lines = format_lines(read_entries(CONFIG))
        self.assertEqual(len(lines), 4)
        prod = next(line for line in lines if line.startswith("acme-prod"))
        self.assertIn("Acme Corp", prod)
        self.assertIn("111111111111", prod)


class InactiveMarkTest(unittest.TestCase):
    def setUp(self):
        self.entries = {e["name"]: e for e in read_entries(CONFIG)}

    def test_marker_leaves_the_title_and_flags_only_the_inactive_group(self):
        self.assertTrue(self.entries["antigo-prod"]["inactive"])
        self.assertEqual(self.entries["antigo-prod"]["title"], "Projeto Antigo Com Um Titulo Muito Longo Mesmo")
        for name in ("acme-prod", "acme-dev", "DREAMSQUAD"):
            self.assertFalse(self.entries[name]["inactive"], name)

    def test_inactive_mark_is_red_in_its_own_column_even_with_a_long_title(self):
        lines = format_lines(read_entries(CONFIG), color=True)
        old = next(line for line in lines if line.startswith("antigo-prod"))
        self.assertIn(f"{profiles.RED}[inativo]{profiles.RESET}", old)
        for line in lines:
            if not line.startswith("antigo-prod"):
                self.assertNotIn(profiles.RED, line)
                self.assertNotIn("[inativo]", line)

    def test_without_color_the_mark_is_plain_text(self):
        lines = format_lines(read_entries(CONFIG), color=False)
        self.assertTrue(any("[inativo]" in line for line in lines))
        self.assertFalse(any("\033" in line for line in lines))

    def test_columns_stay_aligned_and_the_name_is_still_the_first_token(self):
        lines = format_lines(read_entries(CONFIG), color=False)
        positions = {line.index("111111111111") if "111111111111" in line else None for line in lines} - {None}
        title_columns = {line.index("Acme Corp") for line in lines if "Acme Corp" in line}
        self.assertEqual(len(title_columns), 1)
        self.assertEqual(len(positions), 1)
        self.assertEqual([line.split(None, 1)[0] for line in lines][-1], "antigo-prod")

    def test_no_marker_column_when_nothing_is_inactive(self):
        active = [e for e in read_entries(CONFIG) if not e["inactive"]]
        self.assertFalse(any("[inativo]" in line for line in format_lines(active, color=True)))
        self.assertEqual(len(format_lines(active)[0].split("  ")[0]), len("DREAMSQUAD"))


class ExactMatchTest(unittest.TestCase):
    def test_only_a_single_exact_name_skips_the_picker(self):
        entries = read_entries(CONFIG)
        self.assertEqual(exact_match(["acme-prod"], entries), "acme-prod")
        self.assertIsNone(exact_match(["acme"], entries))
        self.assertIsNone(exact_match(["acme-prod", "x"], entries))
        self.assertIsNone(exact_match([], entries))
        self.assertIsNone(exact_match(["acme-prod-exported"], entries))


@unittest.skipUnless(shutil.which("fzf"), "fzf nao instalado")
class FzfFilterTest(unittest.TestCase):
    def test_filter_matches_by_title_account_and_role(self):
        lines = format_lines(read_entries(CONFIG))
        for query, expected in (("acme corp", 2), ("111111111111", 1), ("'ops", 1), ("zzzz", 0)):
            result = profiles.run_fzf(lines, query, list_only=True)
            self.assertEqual(len(result.stdout.splitlines()), expected, query)


class PruneExportedTest(unittest.TestCase):
    def test_keeps_manual_and_the_chosen_exported_profile_only(self):
        result = prune_exported(CREDENTIALS, "acme-prod-exported")
        self.assertIn("[manual]", result)
        self.assertIn("[acme-prod-exported]", result)
        self.assertNotIn("[acme-dev-exported]", result)
        self.assertNotIn("acme-prod-exported-exported", result)
        self.assertNotIn("aws_access_key_id = C", result)

    def test_empty_keep_removes_every_exported_profile(self):
        result = prune_exported(CREDENTIALS, "")
        self.assertEqual(result.strip(), "[manual]\naws_access_key_id = A")

    def test_file_is_rewritten_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credentials"
            path.write_text(CREDENTIALS)
            path.chmod(0o644)
            args = type("Args", (), {"credentials": str(path), "keep": "acme-dev-exported"})
            self.assertEqual(cmd_prune(args), 0)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertIn("[acme-dev-exported]", path.read_text())
            self.assertNotIn("[acme-prod-exported]", path.read_text())
            self.assertEqual(sorted(os.listdir(tmp)), ["credentials"])

    def test_missing_file_is_ignored(self):
        args = type("Args", (), {"credentials": "/nao/existe/credentials", "keep": ""})
        self.assertEqual(cmd_prune(args), 0)


class FakeConsole:
    def __init__(self, interactive=True):
        self._interactive = interactive

    def interactive(self):
        return self._interactive


class FakePlan:
    has_changes = True
    added, removed, changed = 1, 0, 0

    def __init__(self, inactive_skipped=0):
        self.selection = mock.Mock(profiles=["a", "b"], invalid=[], reserved=[], conflicts=[])
        self.stats = {"inactive_skipped": inactive_skipped}


class SyncWiringTest(unittest.TestCase):
    """cmd_sync junta onboarding, renovacao do token e gravacao do config."""

    def setUp(self):
        self.warnings = []
        self.args = argparse.Namespace(aws_config="/tmp/config", inactives=False)
        patches = [
            mock.patch.object(profiles, "warn", self.warnings.append),
            mock.patch.object(onboarding, "ensure_ready"),
            mock.patch.object(onboarding, "renew_token"),
            mock.patch.object(profiles.sync, "plan_sync"),
            mock.patch.object(profiles.sync, "write_config"),
            mock.patch.dict(os.environ, {"CONFLUENCE_SQUAD_IDS": "11,22"}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_happy_path_writes_the_config_and_reports_squads_and_hidden_inactives(self):
        profiles.sync.plan_sync.return_value = FakePlan(inactive_skipped=40)
        self.assertEqual(profiles.cmd_sync(self.args, FakeConsole()), 0)
        profiles.sync.write_config.assert_called_once()
        self.assertEqual(profiles.sync.plan_sync.call_args.args[2], ["11", "22"])
        self.assertIn("2 perfis · 2 squads", self.warnings[0])
        self.assertIn("40 inativos fora (-i)", self.warnings[0])

    def test_the_summary_shows_the_change_counts_only_when_something_changed(self):
        changed = FakePlan()
        changed.added, changed.removed, changed.changed = 3, 1, 2
        profiles.sync.plan_sync.return_value = changed
        profiles.cmd_sync(self.args, FakeConsole())
        self.assertIn("+3 -1 ~2", self.warnings[0])
        unchanged = FakePlan()
        unchanged.has_changes = False
        profiles.sync.plan_sync.return_value = unchanged
        profiles.cmd_sync(self.args, FakeConsole())
        self.assertNotIn("+", self.warnings[1])

    def test_inactives_flag_reaches_the_sync(self):
        profiles.sync.plan_sync.return_value = FakePlan()
        profiles.cmd_sync(argparse.Namespace(aws_config="/tmp/config", inactives=True), FakeConsole())
        self.assertIs(profiles.sync.plan_sync.call_args.args[3], True)

    def test_unchanged_config_is_not_rewritten(self):
        plan = FakePlan()
        plan.has_changes = False
        profiles.sync.plan_sync.return_value = plan
        profiles.cmd_sync(self.args, FakeConsole())
        profiles.sync.write_config.assert_not_called()

    def test_expired_token_in_a_terminal_is_renewed_and_the_sync_retried(self):
        profiles.sync.plan_sync.side_effect = [ConfluenceAuthError("expirou"), FakePlan()]
        self.assertEqual(profiles.cmd_sync(self.args, FakeConsole(interactive=True)), 0)
        onboarding.renew_token.assert_called_once()
        self.assertEqual(profiles.sync.plan_sync.call_count, 2)
        profiles.sync.write_config.assert_called_once()

    def test_expired_token_without_a_terminal_only_warns(self):
        profiles.sync.plan_sync.side_effect = ConfluenceAuthError("expirou")
        self.assertEqual(profiles.cmd_sync(self.args, FakeConsole(interactive=False)), 0)
        onboarding.renew_token.assert_not_called()
        profiles.sync.write_config.assert_not_called()
        self.assertIn("Sync ignorado", self.warnings[0])

    def test_a_second_auth_failure_after_renewal_is_not_retried_forever(self):
        profiles.sync.plan_sync.side_effect = ConfluenceAuthError("expirou")
        self.assertEqual(profiles.cmd_sync(self.args, FakeConsole()), 0)
        self.assertEqual(profiles.sync.plan_sync.call_count, 2)
        self.assertIn("Sync ignorado", self.warnings[0])

    def test_onboarding_failure_only_warns_and_keeps_the_current_config(self):
        onboarding.ensure_ready.side_effect = ConfluenceError("faltando no .env")
        self.assertEqual(profiles.cmd_sync(self.args, FakeConsole()), 0)
        profiles.sync.plan_sync.assert_not_called()
        self.assertIn("faltando no .env", self.warnings[0])


class SquadsCommandTest(unittest.TestCase):
    def setUp(self):
        self.warnings = []
        self.args = argparse.Namespace(aws_config="/tmp/config")
        patches = [
            mock.patch.object(profiles, "warn", self.warnings.append),
            mock.patch.object(onboarding, "ensure_ready"),
            mock.patch.object(onboarding, "choose_squads"),
            mock.patch.object(profiles, "cmd_sync", return_value=0),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_needs_a_terminal(self):
        self.assertEqual(profiles.cmd_squads(self.args, FakeConsole(interactive=False)), 1)
        onboarding.choose_squads.assert_not_called()

    def test_choosing_saves_then_runs_the_sync_without_inactives(self):
        self.assertEqual(profiles.cmd_squads(self.args, FakeConsole()), 0)
        self.assertIs(onboarding.ensure_ready.call_args.kwargs["need_squads"], False)
        onboarding.choose_squads.assert_called_once()
        sync_args = profiles.cmd_sync.call_args.args[0]
        self.assertEqual((sync_args.aws_config, sync_args.inactives), ("/tmp/config", False))

    def test_cancelled_selection_skips_the_sync(self):
        onboarding.choose_squads.side_effect = ConfluenceError("nenhum squad selecionado")
        self.assertEqual(profiles.cmd_squads(self.args, FakeConsole()), 1)
        profiles.cmd_sync.assert_not_called()
        self.assertIn("Squads não alterados", self.warnings[0])


class NoticesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_path = Path(self.tmp.name) / ".env"
        self.warnings = []
        for patch in (mock.patch.object(confluence, "ENV_PATH", self.env_path),
                      mock.patch.object(profiles, "warn", self.warnings.append),
                      mock.patch.dict(os.environ, {}, clear=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def run_notices(self, state):
        return profiles.cmd_notices(argparse.Namespace(state=state))

    def test_off_and_on_are_saved_in_the_env_file(self):
        self.assertEqual(self.run_notices("off"), 0)
        self.assertIn("ROLEROSTER_RENEW_NOTICES=0", self.env_path.read_text())
        self.assertIn("desligados", self.warnings[-1])
        self.run_notices("on")
        self.assertIn("ROLEROSTER_RENEW_NOTICES=1", self.env_path.read_text())
        self.assertIn("ligados", self.warnings[-1])

    def test_turning_off_says_what_still_shows(self):
        self.run_notices("off")
        self.assertIn("falhas", self.warnings[-1])

    def test_without_an_argument_it_reports_the_current_state_and_changes_nothing(self):
        self.run_notices(None)
        self.assertIn("ligados", self.warnings[-1])
        self.assertFalse(self.env_path.exists())
        os.environ["ROLEROSTER_RENEW_NOTICES"] = "0"
        self.run_notices(None)
        self.assertIn("desligados", self.warnings[-1])

    def test_the_parser_accepts_only_on_and_off(self):
        parser = profiles.build_parser()
        self.assertEqual(parser.parse_args(["notices", "off"]).state, "off")
        self.assertIsNone(parser.parse_args(["notices"]).state)
        with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()):
            parser.parse_args(["notices", "talvez"])


class ExpirationTest(unittest.TestCase):
    def test_parses_what_granted_exports_and_common_variants(self):
        base = profiles.parse_expiration("2026-11-26T05:31:01-03:00")
        self.assertEqual(profiles.parse_expiration("2026-11-26T08:31:01Z"), base)
        self.assertEqual(profiles.parse_expiration("2026-11-26T08:31:01.123456789Z"), base)

    def test_garbage_is_none(self):
        for text in ("", None, "amanha", "2026-13-45T00:00:00Z"):
            self.assertIsNone(profiles.parse_expiration(text), text)


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state" / "session"
        self.warnings = []
        for patch in (mock.patch.object(profiles, "SESSION_FILE", self.path),
                      mock.patch.object(profiles, "warn", self.warnings.append)):
            patch.start()
            self.addCleanup(patch.stop)
        self.now = 1_800_000_000

    def args(self, expiration="", renewed=False, no_renew=False, profile="g4"):
        return argparse.Namespace(profile=profile, expiration=expiration, renewed=renewed, no_renew=no_renew)

    def iso(self, seconds_from_now):
        import datetime
        return datetime.datetime.fromtimestamp(self.now + seconds_from_now, datetime.UTC).isoformat()

    def save(self, *a, **kw):
        with mock.patch.object(profiles.time, "time", return_value=self.now):
            return profiles.cmd_session_save(self.args(*a, **kw))

    def test_new_session_opens_a_12_hour_renewal_window_and_is_private(self):
        self.save(self.iso(3600))
        session = profiles.read_session(self.path)
        self.assertEqual((session["profile"], session["renew"]), ("g4", "1"))
        self.assertEqual(int(session["expires"]), self.now + 3600)
        self.assertEqual(int(session["renew_until"]), self.now + 12 * 3600)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_no_renew_flag_turns_renewal_off(self):
        self.save(self.iso(3600), no_renew=True)
        self.assertEqual(profiles.read_session(self.path)["renew"], "0")
        self.assertIn("renovação automática desligada", self.warnings[-1])

    def test_renewal_keeps_the_original_window_and_the_choice(self):
        self.save(self.iso(3600))
        original = profiles.read_session(self.path)["renew_until"]
        self.now += 3000
        self.save(self.iso(3600), renewed=True)
        session = profiles.read_session(self.path)
        self.assertEqual(session["renew_until"], original)  # a janela nao se estende sozinha
        self.assertEqual(int(session["expires"]), self.now + 3600)
        self.save(self.iso(3600), no_renew=True)
        self.save(self.iso(3600), renewed=True)
        self.assertEqual(profiles.read_session(self.path)["renew"], "0")  # quem desligou continua desligado

    def test_a_renewal_reports_only_while_the_notices_are_on(self):
        self.save(self.iso(3600))
        self.warnings.clear()
        self.save(self.iso(3600), renewed=True)
        self.assertEqual(len(self.warnings), 1)
        self.warnings.clear()
        with mock.patch.dict(os.environ, {"ROLEROSTER_RENEW_NOTICES": "0"}):
            self.save(self.iso(3600), renewed=True)
        self.assertEqual(self.warnings, [])
        self.assertEqual(int(profiles.read_session(self.path)["expires"]), self.now + 3600)  # mas grava do mesmo jeito

    def test_an_explicit_sso_always_reports_even_with_the_notices_off(self):
        with mock.patch.dict(os.environ, {"ROLEROSTER_RENEW_NOTICES": "0"}):
            self.save(self.iso(3600))
        self.assertEqual(len(self.warnings), 1)

    def test_missing_expiration_falls_back_to_one_hour(self):
        self.save("")
        self.assertEqual(int(profiles.read_session(self.path)["expires"]), self.now + profiles.DEFAULT_LIFETIME)

    def test_describe_covers_the_three_situations(self):
        base = {"profile": "g4", "expires": str(self.now + 1800), "renew_until": str(self.now + 7200), "renew": "1"}
        on = profiles.describe_session(base, self.now)
        self.assertIn("g4", on)
        self.assertIn("30 min", on)
        self.assertIn("renova sozinha até", on)
        self.assertIn("desligada", profiles.describe_session({**base, "renew": "0"}, self.now))
        self.assertIn("terminou", profiles.describe_session({**base, "renew_until": str(self.now - 1)}, self.now))

    def test_humanize(self):
        self.assertEqual(profiles.humanize(59 * 60), "59 min")
        self.assertEqual(profiles.humanize(3600), "1 h 00 min")
        self.assertEqual(profiles.humanize(12 * 3600 + 5 * 60), "12 h 05 min")
        self.assertEqual(profiles.humanize(-30), "0 min")

    def test_status_without_a_session(self):
        self.assertEqual(profiles.cmd_status(argparse.Namespace()), 1)
        self.save(self.iso(3600))
        self.assertEqual(profiles.cmd_status(argparse.Namespace()), 0)


class SetupCommandTest(unittest.TestCase):
    def test_needs_a_terminal(self):
        with mock.patch.object(profiles, "warn"):
            self.assertEqual(profiles.cmd_setup(argparse.Namespace(), FakeConsole(interactive=False)), 1)


class CredentialsPathTest(unittest.TestCase):
    def test_aws_shared_credentials_file_overrides_the_default(self):
        with mock.patch.dict(os.environ, {"AWS_SHARED_CREDENTIALS_FILE": "~/testes/cred_x"}):
            self.assertEqual(profiles.default_credentials_path(), Path.home() / "testes" / "cred_x")
        env = {k: v for k, v in os.environ.items() if k != "AWS_SHARED_CREDENTIALS_FILE"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(profiles.default_credentials_path(), Path.home() / ".aws" / "credentials")

    def test_paths_are_resolved_after_the_onboarding_so_new_choices_take_effect(self):
        args = profiles.build_parser().parse_args(["prune-exported"])
        self.assertEqual((args.aws_config, args.credentials), (None, None))
        with mock.patch.dict(os.environ, {"AWS_CONFIG_FILE": "/x/config_a", "AWS_SHARED_CREDENTIALS_FILE": "/x/cred_a"}):
            self.assertEqual(profiles.config_path(args), Path("/x/config_a"))
            self.assertEqual(profiles.credentials_path(args), Path("/x/cred_a"))

    def test_explicit_flags_win_over_the_environment(self):
        args = profiles.build_parser().parse_args(["--aws-config", "/y/cfg", "prune-exported", "--credentials", "/y/cred"])
        with mock.patch.dict(os.environ, {"AWS_CONFIG_FILE": "/x/config_a", "AWS_SHARED_CREDENTIALS_FILE": "/x/cred_a"}):
            self.assertEqual((profiles.config_path(args), profiles.credentials_path(args)), (Path("/y/cfg"), Path("/y/cred")))


if __name__ == "__main__":
    unittest.main()
