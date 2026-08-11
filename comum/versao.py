# versao.py — versão do sistema + rastreio de processamento (provenance)
#
# Objetivo: sempre que um vídeo é processado, gravamos o NOME do vídeo e em QUAL
# VERSÃO do sistema ele rodou. Assim conseguimos saber se um vídeo foi gerado
# ANTES ou DEPOIS de uma mudança na lógica (ex.: a correção da tarja full-width)
# e reprocessar só o que precisa. Registro local em JSONL (append-only); depois
# esses dados migram para o Postgres de controle.

import os
import json
import re
from datetime import datetime

# Bump a cada mudança relevante na LÓGICA de processamento (não em bug trivial).
SISTEMA_VERSAO = "1.7.1"

# Histórico curto — o que mudou em cada versão da lógica de processamento.
CHANGELOG = {
    "1.0.0": "Mirror base: blur localizado por OCR, meme no topo, whisper medium.",
    "1.1.0": "Legenda 1 linha, fonte adaptativa, blur sempre (sem tarja preta).",
    "1.2.0": "Detecta tarja preta full-width e faz blur de ponta a ponta (altura real).",
    "1.3.0": ("Blur medido em pixels (40 frames/720p): faixa exata do texto, tarja "
              "só se for lisa, X espelhado após o hflip, meme só se for estático."),
    "1.3.1": ("Vídeo dividido: blur cobre EXATAMENTE a tarja preta (sem margem) e "
              "a folga do gblur é recortada antes do overlay — não invade a cena."),
    "1.4.0": ("Legenda karaokê: UMA palavra por vez em maiúsculas, fonte Anton "
              "(assets/fontes, via fontsdir) com ScaleX 110, subindo 1% da altura "
              "durante a palavra, 3 brancas + 1 azul marinho."),
    "1.5.0": ("Capa: 1º frame do vídeo ORIGINAL (sem espelho/blur) emendado por 1s "
              "no início, com áudio atrasado igual — é a miniatura do TikTok."),
    "1.6.0": ("Vídeo dividido: TARJA PRETA (com fade nas bordas) no lugar do blur, "
              "e o azul da legenda vira marinho #1A3A8F."),
    "1.6.1": ("A tarja cobre a FAIXA PRETA INTEIRA (título fixo + karaokê), não só "
              "a linha que o OCR achou — o título ficava à mostra."),
    "1.7.0": ("Vídeo SEM tarja: o blur passa a ser a banda LIDA pelo OCR (+0,8%), "
              "não o crescimento por bordas, que engolia a janela toda (16,3% de "
              "tela para uma legenda de 5,0%). Legenda sobe 0,0622×fontsize: o "
              "\\an5 centraliza a caixa da linha, não a tinta."),
    "1.7.1": ("Vídeo com TARJA: a nossa legenda vai no CENTRO da tarja (e não "
              "onde estava o texto deles, que podia ficar colado na borda) e a "
              "faixa tem teto de 17% da altura — acima disso vale a banda do OCR."),
}

# Onde gravamos o log de proveniência (append-only).
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PROCESSAMENTOS = os.path.join(_RAIZ, "data", "processamentos.jsonl")


def extrair_video_id(nome_arquivo):
    """Extrai o ID do YouTube do nome do arquivo, removendo o sufixo _AAAAMMDD_HHMMSS
    (o ID pode conter '_' e '-', ex.: 'uBrSJIMM-_g')."""
    base = os.path.splitext(os.path.basename(nome_arquivo))[0]
    return re.sub(r"_\d{8}_\d{6}.*$", "", base)


def registrar_processamento(nome_arquivo, deteccao=None, saida=None, extra=None):
    """Grava uma linha de proveniência do processamento. Best-effort: nunca
    interrompe o pipeline se falhar.

    nome_arquivo: caminho/nome do vídeo processado.
    deteccao:     dict com o resultado do OCR (tem_legenda, faixa_total, cy, meme...).
    saida:        caminho do vídeo final gerado.
    extra:        dict com campos adicionais (n_cues, fs, etc.).
    """
    try:
        base = os.path.splitext(os.path.basename(nome_arquivo))[0]
        deteccao = deteccao or {}
        meme = deteccao.get("meme") if isinstance(deteccao, dict) else None
        registro = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "versao": SISTEMA_VERSAO,
            "video_id": extrair_video_id(nome_arquivo),
            "nome": base,
            "tem_legenda": bool(deteccao.get("tem_legenda")),
            "faixa_total": bool(deteccao.get("faixa_total")),
            "cy": deteccao.get("cy"),
            # Faixa coberta pelo blur (frações y0,y1,x0,x1) — é o que precisamos
            # olhar quando um vídeo sai com blur no lugar/tamanho errado.
            "faixa": [deteccao.get(k) for k in ("y0", "y1", "x0", "x1")]
                     if deteccao.get("tem_legenda") else None,
            "meme": (meme or {}).get("texto") if isinstance(meme, dict) else None,
            "saida": saida,
        }
        if extra:
            registro.update(extra)
        os.makedirs(os.path.dirname(LOG_PROCESSAMENTOS), exist_ok=True)
        with open(LOG_PROCESSAMENTOS, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
        return registro
    except Exception as e:
        print(f"⚠️  Falha ao registrar proveniência (ignorado): {e}")
        return None
