import io
import os
import stat
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from roleroster import confluence  # noqa: E402
from roleroster.confluence import (  # noqa: E402
    ConfluenceAuthError,
    ConfluenceError,
    extract_switch_roles,
    fetch_candidates,
    is_inactive_page,
    list_folders,
    load_env_file,
    parse_ids,
    parse_profiles,
    save_env,
    with_role_arn,
)


def code_macro(body):
    return (
        '<ac:structured-macro ac:name="code" ac:schema-version="1">'
        f"<ac:plain-text-body><![CDATA[{body}]]></ac:plain-text-body>"
        "</ac:structured-macro>"
    )


def expand_macro(title, inner):
    return (
        '<ac:structured-macro ac:name="expand">'
        f'<ac:parameter ac:name="title">{title}</ac:parameter>'
        f"<ac:rich-text-body>{inner}</ac:rich-text-body></ac:structured-macro>"
    )


EXTEND_BLOCK = """[acme-prod]
aws_account_id=111111111111
role_name=dreamsquad-login
region=us-east-1
color=ff0000

[acme-dev]
aws_account_id=222222222222
role_name=dreamsquad-login
region=sa-east-1
color=0000ff
external_id=nao-deve-ser-guardado
"""

NOT_A_PROFILE_BLOCK = "[server]\nhost=exemplo\nport=22\n"


class ExtractSwitchRolesTest(unittest.TestCase):
    def test_expand_title_with_cdata_code_block(self):
        page = "<h1>Cliente</h1>" + expand_macro("Switch Role", code_macro(EXTEND_BLOCK))
        result = extract_switch_roles(page)
        by_name = {p["profile"]: p for p in result.profiles}
        self.assertTrue(result.section_found)
        self.assertEqual(result.code_blocks, 1)
        self.assertEqual(by_name["acme-prod"]["aws_account_id"], "111111111111")
        self.assertEqual(by_name["acme-prod"]["role_name"], "dreamsquad-login")
        self.assertEqual(by_name["acme-dev"]["region"], "sa-east-1")

    def test_role_arn_is_derived_from_account_and_role_name(self):
        page = expand_macro("Switch Role", code_macro(EXTEND_BLOCK))
        prod = extract_switch_roles(page).profiles[0]
        self.assertEqual(prod["role_arn"], "arn:aws:iam::111111111111:role/dreamsquad-login")

    def test_bold_text_marker(self):
        page = "<p><strong>Switch Role</strong></p>" + code_macro(EXTEND_BLOCK)
        self.assertEqual(len(extract_switch_roles(page).profiles), 2)

    def test_heading_marker(self):
        page = "<h2>Switch Role</h2>" + code_macro(EXTEND_BLOCK)
        self.assertEqual(len(extract_switch_roles(page).profiles), 2)

    def test_code_before_the_mention_is_ignored(self):
        page = code_macro(EXTEND_BLOCK) + "<h2>Switch Role</h2>"
        self.assertEqual(extract_switch_roles(page).profiles, [])

    def test_block_without_account_is_ignored_and_counted(self):
        page = "<h2>Switch Role</h2>" + code_macro(NOT_A_PROFILE_BLOCK) + code_macro(EXTEND_BLOCK)
        result = extract_switch_roles(page)
        self.assertEqual(result.ignored_blocks, 1)
        self.assertEqual(result.code_blocks, 1)
        self.assertEqual(len(result.profiles), 2)

    def test_unknown_keys_are_dropped(self):
        page = "<h2>Switch Role</h2>" + code_macro(EXTEND_BLOCK)
        dev = extract_switch_roles(page).profiles[1]
        self.assertNotIn("external_id", dev)

    def test_empty_section_returns_nothing(self):
        page = expand_macro("Switch Role", '<ac:structured-macro ac:name="code"/>')
        result = extract_switch_roles(page)
        self.assertTrue(result.section_found)
        self.assertEqual(result.profiles, [])

    def test_page_without_mention_returns_nothing(self):
        result = extract_switch_roles("<h2>Outra coisa</h2>" + code_macro(EXTEND_BLOCK))
        self.assertFalse(result.section_found)
        self.assertEqual(result.profiles, [])

    def test_spelling_variations_match(self):
        page = "<h2>switch-role (AWS)</h2>" + code_macro(EXTEND_BLOCK)
        self.assertEqual(len(extract_switch_roles(page).profiles), 2)


class ParseProfilesTest(unittest.TestCase):
    def test_profile_prefix_is_optional(self):
        names = [p["profile"] for p in parse_profiles("[profile a]\n[b]\n")]
        self.assertEqual(names, ["a", "b"])

    def test_ignores_keys_before_first_section_and_comments(self):
        profiles = parse_profiles("role_arn = x\n; nota\n[a]\nregion = sa-east-1\n")
        self.assertEqual(profiles, [{"profile": "a", "region": "sa-east-1"}])

    def test_existing_role_arn_is_kept(self):
        profile = {"profile": "a", "role_arn": "arn:aws:iam::1:role/x", "aws_account_id": "2", "role_name": "y"}
        self.assertEqual(with_role_arn(profile)["role_arn"], "arn:aws:iam::1:role/x")


class LoadEnvFileTest(unittest.TestCase):
    def test_reads_export_lines_and_skips_noise(self):
        content = (
            "export TESTPOC_URL=https://x.atlassian.net\n"
            'TESTPOC_EMAIL="a@b.com"\n'
            "python3 fetch_roles.py --parent-id 1\n"
            "export TESTPOC_KEEP=from-file\n"
        )
        os.environ["TESTPOC_KEEP"] = "from-shell"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(content)
            load_env_file(path)
        self.assertEqual(os.environ["TESTPOC_URL"], "https://x.atlassian.net")
        self.assertEqual(os.environ["TESTPOC_EMAIL"], "a@b.com")
        self.assertEqual(os.environ["TESTPOC_KEEP"], "from-shell")

    def test_missing_file_is_ignored(self):
        load_env_file(Path("/nao/existe/.env"))


class InactivePageTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"ROLEROSTER_INACTIVE_PREFIX": "Inativ"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def page(self, title, *ancestors):
        return {"title": title, "ancestors": [{"title": t} for t in ancestors]}

    def test_ancestor_folder_marks_the_page_inactive(self):
        self.assertTrue(is_inactive_page(self.page("Projeto", "Clientes", "Squad X", "Inativos Ares")))

    def test_the_page_itself_can_be_the_inactive_marker(self):
        self.assertTrue(is_inactive_page(self.page("Inativos", "Clientes")))

    def test_spelling_and_case_variations(self):
        for title in ("inativos", "  INATIVO 2024", "Inativas"):
            self.assertTrue(is_inactive_page(self.page("P", title)), title)

    def test_without_a_configured_prefix_nothing_is_inactive(self):
        with mock.patch.dict(os.environ, {"ROLEROSTER_INACTIVE_PREFIX": ""}):
            self.assertFalse(is_inactive_page(self.page("P", "Inativos Ares")))

    def test_another_company_can_choose_its_own_prefix(self):
        with mock.patch.dict(os.environ, {"ROLEROSTER_INACTIVE_PREFIX": "Archived"}):
            self.assertTrue(is_inactive_page(self.page("P", "Clientes", "archived 2023")))
            self.assertFalse(is_inactive_page(self.page("P", "Inativos Ares")))

    def test_regex_characters_in_the_prefix_are_taken_literally(self):
        with mock.patch.dict(os.environ, {"ROLEROSTER_INACTIVE_PREFIX": "[x] "}):
            self.assertTrue(is_inactive_page(self.page("P", "[x] antigo")))
            self.assertFalse(is_inactive_page(self.page("P", "x antigo")))

    def test_only_a_title_that_starts_with_inativ_counts(self):
        self.assertFalse(is_inactive_page(self.page("Projeto", "Clientes", "Squad Ares")))
        self.assertFalse(is_inactive_page(self.page("Projetos nao Inativos", "Clientes")))
        self.assertFalse(is_inactive_page({"title": "Sem ancestrais"}))


class RoleSectionTest(unittest.TestCase):
    """O titulo da secao e configuravel; o padrao aceita as grafias de "Switch Role"."""

    BLOCK = expand_macro("{title}", code_macro(EXTEND_BLOCK))

    def parser_finds(self, title, configured=None):
        env = {"ROLEROSTER_ROLE_SECTION": configured} if configured else {}
        with mock.patch.dict(os.environ, env):
            parser = confluence.extract_switch_roles(self.BLOCK.replace("{title}", title))
        return [p["profile"] for p in parser.profiles]

    def test_the_default_accepts_the_usual_spellings(self):
        for title in ("Switch Role", "switch-role", "SwitchRole", "Switch  Role"):
            self.assertTrue(self.parser_finds(title), title)

    def test_another_title_is_ignored_by_default(self):
        self.assertEqual(self.parser_finds("Acessos AWS"), [])

    def test_a_company_can_name_its_own_section(self):
        self.assertTrue(self.parser_finds("Acessos AWS", configured="Acessos AWS"))
        self.assertEqual(self.parser_finds("Switch Role", configured="Acessos AWS"), [])

    def test_regex_characters_in_the_title_are_taken_literally(self):
        self.assertTrue(self.parser_finds("Roles (AWS)", configured="Roles (AWS)"))


class ParseIdsTest(unittest.TestCase):
    def test_accepts_commas_and_spaces_and_drops_anything_not_numeric(self):
        self.assertEqual(parse_ids("11, 22 33,,x,4a"), ["11", "22", "33"])
        self.assertEqual(parse_ids(""), [])
        self.assertEqual(parse_ids(None), [])


class SaveEnvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / ".env"
        patcher = mock.patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_updates_in_place_appends_new_keys_and_keeps_other_lines(self):
        self.path.write_text("# comentario\n CONFLUENCE_EMAIL=velho@x.co\nOUTRA=1\n")
        save_env({"CONFLUENCE_EMAIL": "novo@x.co", "CONFLUENCE_TOKEN": "a=b=c"}, path=self.path)
        self.assertEqual(
            self.path.read_text().splitlines(),
            ["# comentario", "CONFLUENCE_EMAIL=novo@x.co", "OUTRA=1", "CONFLUENCE_TOKEN=a=b=c"],
        )

    def test_first_run_creates_the_missing_folder_private(self):
        path = Path(self.tmp.name) / "nova" / "pasta" / ".env"
        save_env({"A": "1"}, path=path)
        self.assertEqual(path.read_text(), "A=1\n")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_remove_drops_the_line_and_the_environment_variable(self):
        os.environ["LEGADO"] = "1"
        self.path.write_text("LEGADO=1\nMANTER=2\n")
        save_env({}, remove=("LEGADO",), path=self.path)
        self.assertEqual(self.path.read_text(), "MANTER=2\n")
        self.assertNotIn("LEGADO", os.environ)

    def test_file_is_private_and_environment_is_updated(self):
        save_env({"CONFLUENCE_TOKEN": "t"}, path=self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(os.environ["CONFLUENCE_TOKEN"], "t")
        self.assertEqual(os.listdir(self.tmp.name), [".env"])

    def test_token_with_equals_survives_a_round_trip(self):
        save_env({"CONFLUENCE_TOKEN": "ATATT3x=ABC=="}, path=self.path)
        os.environ.clear()
        confluence.load_env_file(self.path)
        self.assertEqual(os.environ["CONFLUENCE_TOKEN"], "ATATT3x=ABC==")


def listing(page_id, title, *ancestors):
    return {"id": page_id, "title": title, "version": {"number": 1}, "ancestors": [{"title": t} for t in ancestors]}


class FetchCandidatesTest(unittest.TestCase):
    """Duas fases: lista sem corpo, depois baixa o corpo so do que interessa."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"ROLEROSTER_INACTIVE_PREFIX": "Inativ"})
        patcher.start()
        self.addCleanup(patcher.stop)
        body = expand_macro("Switch Role", code_macro(EXTEND_BLOCK))
        self.pages = [
            listing("1", "Cliente Ativo", "Clientes", "Squad A"),
            listing("2", "Cliente Antigo", "Clientes", "Squad A", "Inativos A"),
            listing("3", "Outro Antigo", "Clientes", "Squad B", "Inativos IA"),
        ]
        self.calls = []

        def fake_search(base, auth, cql, expand=""):
            self.calls.append((cql, expand))
            if "ancestor in" in cql:
                return iter(self.pages)
            wanted = cql.split("(")[1].rstrip(")").split(", ")
            return iter({"id": i, "body": {"storage": {"value": body}}} for i in wanted)

        patcher = mock.patch.object(confluence, "search_pages", fake_search)
        patcher.start()
        self.addCleanup(patcher.stop)

    def bodies_requested(self):
        return [cql for cql, expand in self.calls if expand == "body.storage"]

    def test_inactive_pages_are_listed_but_their_bodies_are_never_downloaded(self):
        candidates, stats = fetch_candidates("b", "a", ["11", "22"])
        self.assertEqual({c["page_id"] for c in candidates}, {"1"})
        self.assertEqual(self.bodies_requested(), ["type = page and id in (1)"])
        self.assertEqual((stats["listed"], stats["inactive_pages"], stats["inactive_skipped"], stats["scanned"]), (3, 2, 2, 1))
        self.assertFalse(candidates[0]["inactive"])

    def test_include_inactive_downloads_everything_and_marks_the_inactive_ones(self):
        candidates, stats = fetch_candidates("b", "a", ["11", "22"], include_inactive=True)
        self.assertEqual({c["page_id"] for c in candidates}, {"1", "2", "3"})
        self.assertEqual({c["page_id"] for c in candidates if c["inactive"]}, {"2", "3"})
        self.assertEqual(stats["inactive_skipped"], 0)
        self.assertEqual(stats["scanned"], 3)

    def test_listing_uses_all_selected_squads_and_asks_for_ancestors_without_body(self):
        fetch_candidates("b", "a", ["11", "22"])
        self.assertEqual(self.calls[0], ("type = page and ancestor in (11, 22)", "ancestors,version"))

    def test_bodies_are_downloaded_in_batches(self):
        self.pages = [listing(str(n), f"P{n}", "Squad A") for n in range(1, 121)]
        fetch_candidates("b", "a", ["11"])
        self.assertEqual([len(cql.split(", ")) for cql in self.bodies_requested()], [50, 50, 20])

    def test_no_squads_is_an_error_and_ids_are_sanitized(self):
        with self.assertRaises(ConfluenceError):
            fetch_candidates("b", "a", [])
        fetch_candidates("b", "a", ["11; DROP", "22"])
        self.assertEqual(self.calls[0][0], "type = page and ancestor in (22)")


class ListFoldersTest(unittest.TestCase):
    def test_returns_only_id_and_title_sorted_by_name(self):
        raw = [{"id": "2", "title": "squad b", "x": 1}, {"id": "1", "title": "Squad A"}]
        with mock.patch.object(confluence, "search_pages", return_value=iter(raw)) as search:
            self.assertEqual(
                list_folders("b", "a", "100200"),
                [{"id": "1", "title": "Squad A"}, {"id": "2", "title": "squad b"}],
            )
        self.assertEqual(search.call_args.args[2], "parent = 100200 and type = folder")


class ApiErrorsTest(unittest.TestCase):
    URL = "https://x.atlassian.net/wiki/rest/api/content/search?cql=a"

    def http_error(self, code):
        error = urllib.error.HTTPError("u", code, "m", {}, io.BytesIO(b""))
        self.addCleanup(error.close)
        return error

    def ok(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        return response

    def call(self, *outcomes, url=URL):
        """Cada resultado e uma resposta ou um erro, na ordem das chamadas de rede."""
        with mock.patch.object(confluence.urllib.request, "urlopen", side_effect=list(outcomes)) as opened:
            try:
                return confluence.api_get(url, "auth"), opened.call_count
            except confluence.ConfluenceError as error:
                error.calls = opened.call_count
                raise

    def test_401_is_an_auth_error_so_the_token_can_be_renewed(self):
        with self.assertRaises(ConfluenceAuthError):
            self.call(self.http_error(401))

    def test_403_with_a_dead_token_is_an_auth_error(self):
        # o Confluence Cloud responde 403 (e nao 401) para token expirado ou errado
        with self.assertRaises(ConfluenceAuthError) as raised:
            self.call(self.http_error(403), self.http_error(403))
        self.assertEqual(raised.exception.calls, 2)

    def test_403_with_a_working_token_is_a_permission_error_not_an_auth_error(self):
        with self.assertRaises(ConfluenceError) as raised:
            self.call(self.http_error(403), self.ok())
        self.assertNotIsInstance(raised.exception, ConfluenceAuthError)
        self.assertIn("permissao", str(raised.exception))

    def test_403_on_the_credentials_check_itself_needs_no_second_probe(self):
        with self.assertRaises(ConfluenceAuthError) as raised:
            self.call(self.http_error(403), url="https://x.atlassian.net/wiki/rest/api/user/current")
        self.assertEqual(raised.exception.calls, 1)

    def test_403_when_the_probe_cannot_run_is_not_blamed_on_the_token(self):
        with self.assertRaises(ConfluenceError) as raised:
            self.call(self.http_error(403), urllib.error.URLError("sem rede"))
        self.assertNotIsInstance(raised.exception, ConfluenceAuthError)

    def test_other_failures_are_plain_errors_not_auth_errors(self):
        for error in (self.http_error(500), urllib.error.URLError("sem rede"), TimeoutError()):
            with self.assertRaises(ConfluenceError) as raised:
                self.call(error)
            self.assertNotIsInstance(raised.exception, ConfluenceAuthError)


if __name__ == "__main__":
    unittest.main()
