#!/usr/bin/env bash
# parar_sistema.sh — para TODO o sistema Paparazzi de forma GRACIOSA.
#
# Roda como o usuário luiz (SEM sudo — os serviços são liberados via polkit/pkla).
#
# Regras de desligamento respeitadas:
#   - watcher/editor/poster recebem SIGTERM e só encerram ENTRE itens: o vídeo que
#     está sendo EDITADO termina (whisper/OCR, pode levar minutos) e o post que
#     está sendo PUBLICADO no Selenium termina. A fila pendente NÃO é drenada
#     (fica para a próxima) — o script apenas espera o item ATUAL concluir.
#   - as LLMs (Ollama) são descarregadas da memória ao final (libera RAM).
#   - o painel Django (runserver) é encerrado por último.
#
# Uso:  bash parar_sistema.sh
set -uo pipefail

# Ordem: watcher (para de produzir) → editor (fecha o vídeo atual) → poster (fecha o post atual)
SERVICOS=(paparazzi-watcher.service paparazzi-editor.service paparazzi-poster.service)
TIMEOUT=1800   # espera máx. (s) por serviço para terminar o item em andamento

parar_gracioso() {
  local unit="$1"
  if ! systemctl is-active --quiet "$unit"; then
    echo "• $unit: já parado."
    return 0
  fi
  echo "• $unit: pedindo parada graciosa (termina o item atual)…"
  # --no-block evita o timeout do DBus numa parada longa; esperamos via is-active.
  systemctl stop --no-block "$unit" 2>/dev/null || true
  local i=0
  while systemctl is-active --quiet "$unit"; do
    sleep 2; i=$((i+2))
    (( i % 20 == 0 )) && echo "   … ainda finalizando o item atual ($i s)"
    if (( i >= TIMEOUT )); then
      echo "   ⚠️ timeout ($TIMEOUT s) esperando $unit — seguindo mesmo assim."
      break
    fi
  done
  systemctl is-active --quiet "$unit" || echo "   ✓ $unit parado."
}

echo "==> Parando o pipeline graciosamente…"
for u in "${SERVICOS[@]}"; do parar_gracioso "$u"; done

echo "==> Descarregando as LLMs da memória (libera RAM)…"
carregados=$(ollama ps 2>/dev/null | awk 'NR>1 && NF {print $1}')
if [ -n "$carregados" ]; then
  for m in $carregados; do ollama stop "$m" 2>/dev/null && echo "• modelo $m descarregado."; done
else
  echo "• nenhum modelo carregado."
fi

echo "==> Encerrando o painel (Django runserver)…"
if pkill -f "manage.py runserver" 2>/dev/null; then
  echo "• painel encerrado."
else
  echo "• painel não estava rodando."
fi

echo
echo "✅ Sistema parado graciosamente."
echo "   (O timer de retenção é manutenção e segue ativo. Para pará-lo também:"
echo "    systemctl stop paparazzi-retencao.timer)"
