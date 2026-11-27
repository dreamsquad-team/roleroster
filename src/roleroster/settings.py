"""Valores de configuracao, do mais forte para o mais fraco:

  1. o que o usuario respondeu (variavel de ambiente / .env)
  2. o preset da empresa (preset.py)
  3. o padrao generico da lib (so o que nao e de nenhuma empresa)

O que nao tem como adivinhar (URL do portal SSO, conta e role do perfil base, pasta raiz do Confluence)
nao tem padrao: devolve "" e o onboarding pergunta, a menos que o preset traga.
"""
import os

from . import preset

ENV_VARS = {
    "sso_start_url": "ROLEROSTER_SSO_URL",
    "sso_session": "ROLEROSTER_SSO_SESSION",
    "sso_region": "ROLEROSTER_SSO_REGION",
    "region": "ROLEROSTER_REGION",
    "base_profile": "ROLEROSTER_SOURCE_PROFILE",
    "base_account_id": "ROLEROSTER_BASE_ACCOUNT_ID",
    "base_role": "ROLEROSTER_BASE_ROLE",
    "extra_role": "ROLEROSTER_EXTRA_ROLE",
    "confluence_url": "CONFLUENCE_BASE_URL",
    "root_id": "CONFLUENCE_ROOT_ID",
    "role_section": "ROLEROSTER_ROLE_SECTION",
    "inactive_prefix": "ROLEROSTER_INACTIVE_PREFIX",
}

GENERIC = {
    "base_profile": "roleroster-base",
    "sso_session": "roleroster",
    "sso_region": "us-east-1",
    "region": "us-east-1",
    "extra_role": "AdministratorAccess",
    "role_section": "Switch Role",
}


def get(key):
    """Valor de `key`, ou "" se ninguem definiu e nao ha padrao generico."""
    return os.environ.get(ENV_VARS[key]) or preset.load_installed().get(key) or GENERIC.get(key, "")


def from_user_or_preset(key):
    """Como `get`, mas sem o padrao generico: so o que alguem definiu de proposito."""
    return os.environ.get(ENV_VARS[key]) or preset.load_installed().get(key) or ""
