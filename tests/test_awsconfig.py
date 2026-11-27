import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from roleroster import settings  # noqa: E402
from roleroster.awsconfig import (  # noqa: E402
    ACCOUNTS_HEADER,
    BEGIN,
    END,
    MANUAL_HEADER,
    MANUAL_NOTE,
    apply_block,
    change_counts,
    default_config_path,
    outside_block_names,
    render_block,
    select_profiles,
    source_profile,
    write_atomic,
)
from roleroster.confluence import ConfluenceError  # noqa: E402

BASE_CONFIG = """# cabecalho
[sso-session s]
sso_region = us-east-1

[profile DREAMSQUAD]
region = us-east-1

# ------------------ ACCOUNTS ------------------
"""


def candidate(name, account="111111111111", title="Acme", region="us-east-1", role="dreamsquad-login"):
    return {
        "profile": name,
        "page_title": title,
        "role_arn": f"arn:aws:iam::{account}:role/{role}",
        "region": region,
    }


class SelectProfilesTest(unittest.TestCase):
    def test_duplicates_with_same_arn_count_once(self):
        result = select_profiles([candidate("a", title="X"), candidate("a", title="Y")], set())
        self.assertEqual([p["profile"] for p in result.profiles], ["a"])
        self.assertEqual(result.duplicates, 1)

    def test_same_name_with_different_arn_is_dropped_entirely(self):
        result = select_profiles(
            [candidate("a", account="111111111111"), candidate("a", account="222222222222"), candidate("b")],
            set(),
        )
        self.assertEqual(result.conflicts, ["a"])
        self.assertEqual([p["profile"] for p in result.profiles], ["b"])

    def test_reserved_and_invalid_names_are_skipped(self):
        result = select_profiles(
            [candidate("DREAMSQUAD"), candidate("com/barra"), candidate("ok")],
            {"DREAMSQUAD"},
        )
        self.assertEqual(result.reserved, ["DREAMSQUAD"])
        self.assertEqual(result.invalid, ["com/barra"])
        self.assertEqual([p["profile"] for p in result.profiles], ["ok"])

    def test_spaces_become_hyphens(self):
        result = select_profiles([candidate("Drive Match"), candidate("  Project   Data ")], set())
        self.assertEqual([p["profile"] for p in result.profiles], ["Drive-Match", "Project-Data"])
        self.assertEqual(result.invalid, [])

    def test_a_name_with_spaces_collides_with_its_hyphenated_twin(self):
        result = select_profiles(
            [candidate("Drive Match", account="111111111111"), candidate("Drive-Match", account="222222222222")],
            set(),
        )
        self.assertEqual(result.conflicts, ["Drive-Match"])
        self.assertEqual(result.profiles, [])

    def test_a_manual_profile_blocks_the_hyphenated_name(self):
        result = select_profiles([candidate("Drive Match")], {"Drive-Match"})
        self.assertEqual(result.reserved, ["Drive-Match"])


class RenderBlockTest(unittest.TestCase):
    def test_block_layout_groups_by_page_title(self):
        result = select_profiles(
            [candidate("acme-prod", title="Acme"), candidate("acme-dev", account="222222222222", title="Acme")],
            set(),
        )
        block = render_block(result.profiles, "DREAMSQUAD")
        self.assertTrue(block.startswith(BEGIN))
        self.assertTrue(block.endswith(END + "\n"))
        self.assertEqual(block.count("# --- Acme"), 1)
        self.assertIn(
            "[profile acme-prod]\nsource_profile = DREAMSQUAD\n"
            "role_arn = arn:aws:iam::111111111111:role/dreamsquad-login\nregion = us-east-1\n",
            block,
        )
        self.assertNotIn("color", block)

    def test_region_is_omitted_when_missing(self):
        block = render_block(select_profiles([candidate("a", region="")], set()).profiles, "DREAMSQUAD")
        self.assertNotIn("region", block)


class ApplyBlockTest(unittest.TestCase):
    def setUp(self):
        self.block = render_block(select_profiles([candidate("a")], set()).profiles, "DREAMSQUAD")

    def test_appends_after_existing_content_and_keeps_it(self):
        new = apply_block(BASE_CONFIG, self.block)
        self.assertTrue(new.startswith(BASE_CONFIG.rstrip("\n")))
        self.assertIn(self.block, new)
        self.assertTrue(new.rstrip("\n").endswith(MANUAL_NOTE))

    def test_is_idempotent(self):
        once = apply_block(BASE_CONFIG, self.block)
        self.assertEqual(apply_block(once, self.block), once)

    def test_replaces_only_the_managed_block(self):
        once = apply_block(BASE_CONFIG, self.block) + "\n[profile manual]\nregion = us-east-1\n"
        other = render_block(select_profiles([candidate("b", account="222222222222")], set()).profiles, "DREAMSQUAD")
        twice = apply_block(once, other)
        self.assertIn("[profile b]", twice)
        self.assertNotIn("[profile a]", twice)
        self.assertIn("[profile manual]", twice)
        self.assertTrue(twice.startswith(BASE_CONFIG.rstrip("\n")))

    def test_corrupted_markers_abort(self):
        with self.assertRaises(ConfluenceError):
            apply_block(BASE_CONFIG + END + "\n", self.block)

    def test_outside_names_ignore_managed_block(self):
        text = apply_block(BASE_CONFIG, self.block)
        self.assertEqual(outside_block_names(text), {"DREAMSQUAD", "sso-session s"})

    def test_change_counts(self):
        first = apply_block(BASE_CONFIG, render_block(
            select_profiles([candidate("a"), candidate("b", account="222222222222")], set()).profiles, "DREAMSQUAD"))
        second = apply_block(first, render_block(
            select_profiles([candidate("a", region="sa-east-1"), candidate("c", account="333333333333")], set()).profiles,
            "DREAMSQUAD"))
        self.assertEqual(change_counts(BASE_CONFIG, first), (2, 0, 0))
        self.assertEqual(change_counts(first, second), (1, 1, 1))


class WriteAtomicTest(unittest.TestCase):
    def test_keeps_permissions_and_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config"
            path.write_text("old")
            path.chmod(0o640)
            write_atomic(path, "new")
            self.assertEqual(path.read_text(), "new")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            self.assertEqual(os.listdir(tmp), ["config"])


class InactiveProfilesTest(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            {**candidate("antigo", account="222222222222", title="Cliente B"), "inactive": True},
            {**candidate("ativo", title="Cliente A"), "inactive": False},
            {**candidate("antigo2", account="333333333333", title="Cliente B"), "inactive": True},
        ]

    def test_active_profiles_come_before_inactive_ones(self):
        result = select_profiles(self.candidates, set())
        self.assertEqual([p["profile"] for p in result.profiles], ["ativo", "antigo", "antigo2"])

    def test_block_marks_inactive_groups(self):
        block = render_block(select_profiles(self.candidates, set()).profiles, "DREAMSQUAD")
        self.assertIn("# --- Cliente A\n", block)
        self.assertIn("# --- Cliente B [inativo]\n", block)
        self.assertEqual(block.count("[inativo]"), 1)

    def test_a_profile_that_is_also_active_is_not_marked(self):
        same = [
            {**candidate("x", title="Velho"), "inactive": True},
            {**candidate("x", title="Novo"), "inactive": False},
        ]
        result = select_profiles(same, set())
        self.assertEqual(result.duplicates, 1)
        self.assertEqual([(p["page_title"], p["inactive"]) for p in result.profiles], [("Novo", False)])

    def test_inactives_appear_and_disappear_from_the_config(self):
        active = [c for c in self.candidates if not c["inactive"]]
        only_active = apply_block(BASE_CONFIG, render_block(select_profiles(active, set()).profiles, "DREAMSQUAD"))
        with_all = apply_block(only_active, render_block(select_profiles(self.candidates, set()).profiles, "DREAMSQUAD"))
        self.assertEqual(change_counts(only_active, with_all), (2, 0, 0))
        self.assertEqual(change_counts(with_all, only_active), (0, 2, 0))


class ManualSectionTest(unittest.TestCase):
    def setUp(self):
        self.block = render_block(select_profiles([candidate("a")], set()).profiles, "DREAMSQUAD")
        self.other = render_block(select_profiles([candidate("b", account="222222222222")], set()).profiles, "DREAMSQUAD")

    def test_empty_config_gets_accounts_then_block_then_manual_in_that_order(self):
        text = apply_block("", self.block)
        positions = [text.index(x) for x in (ACCOUNTS_HEADER, BEGIN, END, MANUAL_HEADER)]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(text.count(ACCOUNTS_HEADER), 1)
        self.assertEqual(text.count(MANUAL_HEADER), 1)

    def test_existing_accounts_header_is_reused_not_duplicated(self):
        self.assertEqual(apply_block(BASE_CONFIG, self.block).count(ACCOUNTS_HEADER), 1)

    def test_is_idempotent_including_the_manual_header(self):
        once = apply_block(BASE_CONFIG, self.block)
        self.assertEqual(apply_block(once, self.block), once)
        self.assertEqual(once.count(MANUAL_HEADER), 1)

    def test_everything_under_manual_survives_every_block_update(self):
        text = apply_block(BASE_CONFIG, self.block)
        text += "\n[profile meu-perfil]\nrole_arn = arn:aws:iam::999999999999:role/meu\nregion = sa-east-1\n"
        for block in (self.other, self.block, self.other):
            text = apply_block(text, block)
            self.assertIn("[profile meu-perfil]\nrole_arn = arn:aws:iam::999999999999:role/meu", text)
            self.assertEqual(text.count(MANUAL_HEADER), 1)
        manual_part = text[text.index(MANUAL_HEADER):]
        self.assertIn("[profile meu-perfil]", manual_part)
        self.assertNotIn("[profile a]", manual_part)

    def test_manual_profile_wins_over_a_confluence_profile_with_the_same_name(self):
        text = apply_block(BASE_CONFIG, self.block) + "\n[profile a]\nregion = sa-east-1\n"
        selection = select_profiles([candidate("a"), candidate("b", account="222222222222")], outside_block_names(text))
        self.assertEqual(selection.reserved, ["a"])
        self.assertEqual([p["profile"] for p in selection.profiles], ["b"])

    def test_profiles_that_already_followed_the_block_end_up_under_the_new_manual_header(self):
        legacy = apply_block(BASE_CONFIG, self.block).replace(f"\n{MANUAL_HEADER}\n{MANUAL_NOTE}\n", "")
        legacy += "\n[profile antigo]\nregion = us-east-1\n"
        text = apply_block(legacy, self.block)
        self.assertLess(text.index(MANUAL_HEADER), text.index("[profile antigo]"))
        self.assertLess(text.index(END), text.index(MANUAL_HEADER))

    def test_block_goes_right_under_accounts_even_when_manual_already_follows_it(self):
        text = f"[profile base]\nregion = us-east-1\n\n{ACCOUNTS_HEADER}\n\n{MANUAL_HEADER}\n[profile meu]\nregion = sa-east-1\n"
        result = apply_block(text, self.block)
        self.assertLess(result.index(ACCOUNTS_HEADER), result.index(BEGIN))
        self.assertLess(result.index(END), result.index(MANUAL_HEADER))
        self.assertLess(result.index(MANUAL_HEADER), result.index("[profile meu]"))
        self.assertEqual(result.count(MANUAL_HEADER), 1)
        self.assertEqual(apply_block(result, self.block), result)

    def test_an_existing_manual_header_placed_by_the_user_is_respected(self):
        mine = BASE_CONFIG + "\n" + MANUAL_HEADER + "\n[profile meu]\nregion = us-east-1\n"
        text = apply_block(mine, self.block)
        self.assertEqual(text.count(MANUAL_HEADER), 1)
        self.assertIn("[profile meu]", text)


class SourceProfileTest(unittest.TestCase):
    def test_generic_default_preset_and_user_choice_in_that_order_of_strength(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(source_profile(), "roleroster-base")
            with mock.patch.object(settings.preset, "load_installed", return_value={"base_profile": "ACME"}):
                self.assertEqual(source_profile(), "ACME")
                with mock.patch.dict(os.environ, {"ROLEROSTER_SOURCE_PROFILE": "MEU-BASE"}):
                    self.assertEqual(source_profile(), "MEU-BASE")


class AlternativeConfigPathTest(unittest.TestCase):
    def test_aws_config_file_overrides_the_default(self):
        with mock.patch.dict(os.environ, {"AWS_CONFIG_FILE": "~/testes/config_x"}):
            self.assertEqual(default_config_path(), Path.home() / "testes" / "config_x")
        env = {k: v for k, v in os.environ.items() if k != "AWS_CONFIG_FILE"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(default_config_path(), Path.home() / ".aws" / "config")

    def test_a_missing_config_is_created_with_its_folders_and_without_a_backup(self):
        from roleroster import awsconfig as sync
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outra" / "pasta" / "config_teste"
            cache = Path(tmp) / "cache"
            plan = sync.SyncPlan("", apply_block("", render_block([], "DREAMSQUAD")), sync.Selection(), [], {})
            with mock.patch.object(sync, "CACHE_DIR", cache):
                sync.write_config(path, plan)
            self.assertIn(ACCOUNTS_HEADER, path.read_text())
            self.assertIn(MANUAL_HEADER, path.read_text())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            self.assertFalse(cache.exists())

    def test_plan_sync_treats_a_missing_file_as_empty(self):
        from roleroster import awsconfig as sync
        with mock.patch.object(sync, "require_config", return_value=("b", "a")), \
             mock.patch.object(sync, "fetch_candidates", return_value=([candidate("a")], {})):
            plan = sync.plan_sync(Path("/nao/existe/config"), "DREAMSQUAD", ["1"])
        self.assertTrue(plan.has_changes)
        self.assertIn("[profile a]", plan.new_text)
        self.assertEqual(plan.old_text, "")


if __name__ == "__main__":
    unittest.main()
