#!/usr/bin/env bash
# Instala os serviços systemd do Paparazzi. Rode UMA vez com sudo:
#     sudo bash systemd/instalar_servicos.sh
set -euo pipefail

REPO="/home/luiz/github/paparazzi-worker"
USUARIO="luiz"

echo "==> Copiando units para /etc/systemd/system/"
cp "$REPO/systemd/paparazzi-watcher.service" \
   "$REPO/systemd/paparazzi-poster.service" \
   "$REPO/systemd/paparazzi-editor.service" \
   "$REPO/systemd/paparazzi-retencao.service" \
   "$REPO/systemd/paparazzi-retencao.timer" /etc/systemd/system/

echo "==> systemctl daemon-reload"
systemctl daemon-reload

echo "==> Habilitando a retenção automática (timer horário)"
systemctl enable --now paparazzi-retencao.timer

echo "==> Watcher/Poster/Editor NÃO iniciam no boot (quem liga/desliga é o painel)"
systemctl disable paparazzi-watcher.service 2>/dev/null || true
systemctl disable paparazzi-poster.service 2>/dev/null || true
systemctl disable paparazzi-editor.service 2>/dev/null || true

echo "==> Instalando autorização do polkit (painel controla os serviços sem sudo)"
# Formato .rules (JavaScript) — polkit >= 0.106
mkdir -p /etc/polkit-1/rules.d
cat > /etc/polkit-1/rules.d/49-paparazzi.rules <<'EOF'
polkit.addRule(function(action, subject) {
  if (action.id == "org.freedesktop.systemd1.manage-units" &&
      subject.user == "luiz") {
    var unit = action.lookup("unit");
    if (unit == "paparazzi-watcher.service" ||
        unit == "paparazzi-poster.service" ||
        unit == "paparazzi-editor.service" ||
        unit == "ollama.service") {
      return polkit.Result.YES;
    }
  }
});
EOF
# Formato .pkla (pklocalauthority) — polkit <= 0.105 (Ubuntu 20.04 e afins), que
# IGNORA os .rules. Não dá para restringir por unit neste formato, então vale para
# manage-units em geral (aceitável em máquina de uso pessoal do luiz).
mkdir -p /etc/polkit-1/localauthority/50-local.d
cat > /etc/polkit-1/localauthority/50-local.d/49-paparazzi.pkla <<'EOF'
[Paparazzi: luiz controla os servicos sem senha]
Identity=unix-user:luiz
Action=org.freedesktop.systemd1.manage-units
ResultAny=yes
ResultInactive=yes
ResultActive=yes
EOF

echo
echo "==> Concluído. Estado atual:"
systemctl list-unit-files 'paparazzi-*'
echo
systemctl list-timers paparazzi-retencao.timer --no-pager || true
echo
echo "Pronto! O painel já pode ligar/desligar o watcher, e a retenção roda de hora em hora."
