# Serviços systemd — Paparazzi

Três units controlam o pipeline:

| Unit | Papel |
|------|-------|
| `paparazzi-watcher.service` | Roda o watcher (produz vídeos). Liga/desliga pelo painel. |
| `paparazzi-poster.service` | Roda o poster (publica a fila). Liga/desliga pelo painel. |
| `paparazzi-retencao.service` | Apaga arquivos de vídeo com mais de 24h (mantém metadados). |
| `paparazzi-retencao.timer` | Dispara a retenção de hora em hora. |

> O `ollama.service` já existe (instalado à parte) e é ligado/desligado
> automaticamente pelo painel conforme o uso das LLMs.

## Instalação

```bash
sudo cp systemd/paparazzi-*.service systemd/paparazzi-*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# Retenção automática (24h) — habilita o timer:
sudo systemctl enable --now paparazzi-retencao.timer

# O watcher NÃO deve iniciar sozinho no boot: quem liga/desliga é o painel.
sudo systemctl disable paparazzi-watcher.service
```

## Deixar o painel controlar os serviços SEM senha (polkit)

O painel (rodando como usuário `luiz`) precisa dar `start/stop` em
`paparazzi-watcher.service` e `ollama.service`. Crie a regra polkit:

```bash
sudo tee /etc/polkit-1/rules.d/49-paparazzi.rules >/dev/null <<'EOF'
polkit.addRule(function(action, subject) {
  if (action.id == "org.freedesktop.systemd1.manage-units" &&
      subject.user == "luiz") {
    var unit = action.lookup("unit");
    if (unit == "paparazzi-watcher.service" || unit == "ollama.service") {
      return polkit.Result.YES;
    }
  }
});
EOF
```

Assim `systemctl start/stop` desses serviços funciona sem `sudo`.

> **polkit ≤ 0.105 (Ubuntu 20.04 e afins):** essa versão IGNORA os `.rules` (JS).
> O `instalar_servicos.sh` também grava o formato antigo `.pkla` em
> `/etc/polkit-1/localauthority/50-local.d/49-paparazzi.pkla`, que é o que funciona
> nessas versões. Esse formato não restringe por unit (vale para `manage-units` em
> geral) — ok em máquina de uso pessoal.

Alternativa: definir `SYSTEMCTL="sudo systemctl"` no `.env` e liberar no sudoers.

## Verificar

```bash
systemctl status paparazzi-watcher.service
systemctl list-timers paparazzi-retencao.timer
```
