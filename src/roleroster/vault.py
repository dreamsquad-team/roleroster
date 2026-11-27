"""Token do Confluence no cofre do sistema, em vez de um arquivo de texto.

  macOS    Keychain
  Windows  Credential Manager
  Linux    Secret Service (GNOME Keyring, KWallet); sem ele, o token fica no .env (permissao 600)

Tudo aqui falha em silencio e devolve "indisponivel": quem chama cai no .env. Nenhum erro do
cofre pode impedir o uso do roleroster.
"""
SERVICE = "roleroster"
ACCOUNT = "confluence-token"

BACKEND_NAMES = {
    "keyring.backends.macOS": "Keychain do macOS",
    "keyring.backends.Windows": "Credential Manager do Windows",
    "keyring.backends.SecretService": "Secret Service",
    "keyring.backends.kwallet": "KWallet",
}


def is_usable(backend):
    """Os backends `fail` e `null` do keyring existem para dizer "sem cofre": nao servem."""
    module = type(backend).__module__
    return not (module.endswith(".fail") or module.endswith(".null"))


def _keyring():
    """O modulo keyring, ou None se nao houver um cofre de verdade neste sistema."""
    try:
        import keyring
    except ImportError:
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:
        return None
    return keyring if is_usable(backend) else None


def available():
    return _keyring() is not None


def name():
    """Nome legivel do cofre em uso (para mensagens)."""
    keyring = _keyring()
    if keyring is None:
        return "arquivo .env"
    module = type(keyring.get_keyring()).__module__
    return next((label for prefix, label in BACKEND_NAMES.items() if module.startswith(prefix)), "cofre do sistema")


def get_token():
    keyring = _keyring()
    if keyring is None:
        return ""
    try:
        return keyring.get_password(SERVICE, ACCOUNT) or ""
    except Exception:
        return ""


def set_token(token):
    """Guarda o token e confere lendo de volta. False se o cofre nao esta disponivel ou falhou."""
    keyring = _keyring()
    if keyring is None:
        return False
    try:
        keyring.set_password(SERVICE, ACCOUNT, token)
        return keyring.get_password(SERVICE, ACCOUNT) == token
    except Exception:
        return False


def delete_token():
    keyring = _keyring()
    if keyring is None:
        return False
    try:
        keyring.delete_password(SERVICE, ACCOUNT)
        return True
    except Exception:
        return False
