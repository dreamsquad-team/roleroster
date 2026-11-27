"""Dublês compartilhados pelos testes."""


class FakeKeyring:
    """No lugar do modulo `keyring`: um cofre em memoria. `module` simula o backend (o nome vira o rotulo)."""

    def __init__(self, module="keyring.backends.macOS", fail_set=False, corrupt=False, fail_get=False):
        self.store = {}
        self.fail_set, self.corrupt, self.fail_get = fail_set, corrupt, fail_get
        self._backend = type("Keyring", (), {"__module__": module})()

    def get_keyring(self):
        return self._backend

    def get_password(self, service, account):
        if self.fail_get:
            raise RuntimeError("cofre trancado")
        return self.store.get((service, account))

    def set_password(self, service, account, value):
        if self.fail_set:
            raise RuntimeError("sem permissao")
        self.store[(service, account)] = "outro" if self.corrupt else value

    def delete_password(self, service, account):
        if (service, account) not in self.store:
            raise RuntimeError("nao existe")
        del self.store[(service, account)]
