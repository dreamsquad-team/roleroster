# Changelog

Segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Não lançado]

## [0.1.0] - 2026-11-27

### Adicionado
- Comando `role` (e `roleroster`): `sso`, `console`, `assume`, `sync`, `squads`, `setup`, `status`, `browser`, `notices`, `token`, `doctor`, `preset` e `init`. O que não é do roleroster vai para o Granted.
- Sincronização dos perfis de Switch Role das páginas do Confluence para o `~/.aws/config` (bloco gerado, seção manual preservada).
- Onboarding em passos, com perguntas só do que não dá para adivinhar.
- Preset de empresa (TOML) para dispensar perguntas que a empresa já sabe: `role preset` e `role init --preset`.
- Navegador do console e do login SSO (Chrome, Brave, Firefox, Edge) e três modos de abrir as contas: perfil fixo, visitante (janela isolada) e um perfil por conta.
- Renovação automática das credenciais, com `role notices on|off` para silenciar a linha de andamento.
- Token do Confluence no cofre do sistema (Keychain, Credential Manager, Secret Service), com `.env` como alternativa.
- `role doctor` com `--fix` e `--online`.
- Suporte a zsh e bash (3.2 ou superior) em macOS e Linux.
