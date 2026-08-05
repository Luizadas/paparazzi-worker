# coletor.py - Download e Processamento de Shorts (Mirror-Clips)

import sys
import os
import re
import tempfile
import requests
import yt_dlp
from datetime import datetime
import subprocess
import shutil
import numpy as np
from PIL import Image
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# Raiz do repo no path para importar 'comum'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from comum.versao import SISTEMA_VERSAO, registrar_processamento

# Integração com módulo de postagem
try:
    from poster import adicionar_na_fila
    POSTER_DISPONIVEL = True
except ImportError:
    POSTER_DISPONIVEL = False

DOWNLOAD_DIR = "/mnt/paparazzi/mirror_clips"

# LLM local (Ollama) para gerar a legenda do post
LLM_API_URL = os.getenv("LLM_API_URL", "http://localhost:11434/api/generate")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-r1:latest")

# Transcrição (faster-whisper): mais rápido/preciso que o openai-whisper em CPU.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium")   # tiny/base/small/medium/large-v3
_FW_MODEL = None

# Análise da legenda original (blur). Amostramos MUITOS frames em resolução boa
# para medir nos pixels a faixa exata a cobrir; o OCR (caro, ~4s/frame em CPU)
# roda só num subconjunto e em resolução menor — ele só precisa dizer ONDE, mais
# ou menos, está o texto; quem fecha a conta é a medição em pixels.
N_FRAMES_PIXEL = int(os.getenv("BLUR_FRAMES", "40"))    # frames p/ medir pixels
N_FRAMES_OCR = int(os.getenv("BLUR_FRAMES_OCR", "16"))  # frames p/ o OCR
LARGURA_ANALISE = 720   # largura dos frames analisados
LARGURA_OCR = 540       # largura usada no OCR

# Legenda KARAOKÊ (uma palavra por vez), medida quadro a quadro nos vídeos do
# canal original — ver assets/fontes/LEIA-ME.md.
RAIZ_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_FONTES = os.path.join(RAIZ_REPO, "assets", "fontes")   # Anton.ttf (sem instalar)
ALT_LETRA_LEGENDA = 0.0479     # altura da letra / altura do vídeo (medido: 184/3840)
ANTON_ALT_POR_FS = 0.4977      # o libass renderiza a Anton a ~0,50 do fontsize
DESLOC_LEGENDA = 0.01          # deslize vertical total, em fração da altura
COR_LEGENDA = "&H00FFFFFF"          # branco (ASS é &HAABBGGRR)
COR_LEGENDA_DESTAQUE = "&H00800000"  # azul marinho #000080
CICLO_COR_LEGENDA = 4          # 3 palavras brancas + 1 destacada


def _get_whisper():
    """Carrega o modelo faster-whisper uma vez (CPU, int8)."""
    global _FW_MODEL
    if _FW_MODEL is None:
        from faster_whisper import WhisperModel
        print(f"🎙️  Carregando faster-whisper ({WHISPER_MODEL}, CPU/int8)...")
        _FW_MODEL = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _FW_MODEL


def transcrever_audio(audio_path):
    """
    Transcreve o áudio com faster-whisper, forçando PT-BR e com filtros de
    qualidade (VAD remove silêncios/alucinações; sem condicionar no texto
    anterior evita repetições tipo 'na... na...'). Retorna (segmentos, texto),
    onde cada segmento é {'words': [{'start','end','word'}], 'text'}.
    """
    model = _get_whisper()
    segments, _info = model.transcribe(
        audio_path,
        language="pt",
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
        condition_on_previous_text=False,
    )
    segs, textos = [], []
    for seg in segments:  # generator — consumir aqui dispara a transcrição
        palavras = [{"start": w.start, "end": w.end, "word": w.word}
                    for w in (seg.words or [])]
        segs.append({"words": palavras, "text": seg.text})
        textos.append(seg.text)
    return segs, " ".join(textos).strip()


# A geração da legenda do post vive em comum/legenda_ia.py (compartilhada com o
# painel, que permite regerar). _sanitizar_legenda também é reusado aqui pelo
# corrigir_texto_ia (limpeza da saída do LLM na correção de OCR).
from comum.legenda_ia import gerar_legenda_ia, _sanitizar_legenda


# ─────────────────────────────────────────────
#  LEGENDA: detecção da faixa original + geração do ASS (karaokê)
# ─────────────────────────────────────────────

def _duracao_video(video_path):
    """Duração em segundos via ffprobe (0.0 se não der)."""
    try:
        return float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, check=True).stdout.strip())
    except Exception:
        return 0.0


def _dimensoes_video(video_path):
    """Retorna (largura, altura) do vídeo via ffprobe."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", video_path],
            capture_output=True, text=True, check=True).stdout.strip()
        w, h = out.split(",")[:2]
        return int(w), int(h)
    except Exception:
        return 1080, 1920


_OCR_READER = None


def _get_ocr_reader():
    """Inicializa o EasyOCR uma vez (import tardio; baixa modelo no 1º uso)."""
    global _OCR_READER
    if _OCR_READER is None:
        import easyocr
        _OCR_READER = easyocr.Reader(['pt'], gpu=False, verbose=False)
    return _OCR_READER


def corrigir_texto_ia(texto: str) -> str:
    """Corrige SÓ caracteres trocados pelo OCR (0→o, 1→i, rn→m...) numa frase,
    SEM remover/adicionar palavras. Se a IA mexer demais (perder palavras) ou
    falhar, devolve o texto original com uma pré-correção leve."""
    texto = (texto or "").strip()
    if not texto:
        return texto
    # pré-correção leve de erros comuns (dígito isolado no lugar de letra)
    base = re.sub(r"\b0\b", "o", texto)
    base = re.sub(r"\b1\b", "i", base)
    base = (base[:1].upper() + base[1:]) if base else base   # 1ª letra maiúscula

    prompt = (
        "Corrija SOMENTE erros de OCR (caracteres trocados, ex: 0->o, 1->i, rn->m) "
        "nesta frase em português do Brasil. É TERMINANTEMENTE PROIBIDO remover ou "
        "adicionar palavras, mudar a ordem ou a pontuação — devolva a MESMA frase, "
        "com a MESMA quantidade de palavras, só com os caracteres corrigidos. "
        "Sem aspas, sem explicação.\n\nFRASE: " + base
    )
    try:
        resp = requests.post(LLM_API_URL, json={
            "model": LLM_MODEL, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.0, "num_predict": 80},
        }, timeout=60)
        resp.raise_for_status()
        out = _sanitizar_legenda(resp.json().get("response", "") or "").strip()
        # TRAVA: só aceita se NÃO perdeu palavras (senão mantém o OCR pré-corrigido)
        if out and len(out.split()) >= len(base.split()):
            return out
        return base
    except Exception:
        return base


def _palavras_chave(texto):
    """Conjunto de palavras (só letras, >=3 chars) de um texto — usado para medir
    se o OCR leu a MESMA frase em frames diferentes."""
    limpo = re.sub(r"[^a-zà-ú ]", " ", (texto or "").lower())
    return {w for w in limpo.split() if len(w) >= 3}


def _detectar_meme_topo(caixas, n_frames):
    """
    Detecta uma FRASE DE MEME estática no topo do vídeo (aquele texto de contexto
    que forma a piada). Não basta "ter texto no topo": se o topo é VÍDEO AO VIVO,
    o OCR lê lixo diferente em cada frame (placar, camisa, painel...) e isso já
    gerou blur gigante no lugar errado. Então exigimos:
      • persistência (aparece na maioria dos frames);
      • conteúdo de frase (>=8 chars, >=3 palavras, quase tudo letras);
      • ESTABILIDADE: a mesma frase (mesmas palavras) relida em >=50% dos frames;
      • caixa compacta (uma frase de 1-3 linhas, não meia tela).
    A confirmação final (região realmente ESTÁTICA nos pixels) é feita depois em
    detectar_legenda_ocr via _texto_sobreposto_estatico().
    `caixas` = lista de dicts (x0,x1,y0,y1,yc,idx,txt,conf).
    Retorna {'texto', 'x0','x1','y0','y1'} (frações) ou None.
    """
    if len(caixas) < 2:
        return None
    por_frame = {}
    for c in caixas:
        por_frame.setdefault(c["idx"], []).append((c["y0"], c["x0"], c["txt"]))
    if len(por_frame) < max(2, int(0.4 * n_frames)):
        return None   # não é persistente → provavelmente não é meme

    def texto_do_frame(itens):
        return " ".join(z[2] for z in sorted(itens, key=lambda z: (round(z[0], 2), z[1])))

    textos = {i: texto_do_frame(v) for i, v in por_frame.items()}
    melhor_idx = max(textos, key=lambda i: len(textos[i]))
    texto = textos[melhor_idx]
    if len(texto) < 8 or len(texto.split()) < 3:
        return None   # curto demais → provável logo/marca

    # Quase tudo letras: frase de meme não é placar/número ("858, onal "fpa os,").
    letras = sum(c.isalpha() or c.isspace() for c in texto)
    if letras / max(1, len(texto)) < 0.80 or sum(c.isdigit() for c in texto) > 2:
        return None

    # ESTABILIDADE: a mesma frase tem que reaparecer (>=60% das palavras em comum)
    # na maioria dos frames. Vídeo ao vivo no topo falha aqui.
    ref = _palavras_chave(texto)
    if len(ref) < 3:
        return None
    def parecido(outro):
        p = _palavras_chave(outro)
        return bool(p) and len(ref & p) / len(ref | p) >= 0.6
    iguais = sum(1 for t in textos.values() if parecido(t))
    if iguais < max(2, int(0.5 * n_frames)):
        return None

    # Caixa: só os frames que leram a MESMA frase (evita lixo esticando a caixa).
    idx_ok = {i for i, t in textos.items() if parecido(t)}
    grupo = [b for b in caixas if b["idx"] in idx_ok] or caixas
    xs0 = [b["x0"] for b in grupo]; xs1 = [b["x1"] for b in grupo]
    y0 = min(b["y0"] for b in grupo); y1 = max(b["y1"] for b in grupo)
    if (y1 - y0) > 0.16:
        return None   # "frase" espalhada demais → não é um bloco de meme

    return {
        "texto": texto,
        "x0": round(max(0.0, min(xs0) - 0.02), 4),
        "x1": round(min(1.0, max(xs1) + 0.02), 4),
        "y0": round(max(0.0, y0 - 0.01), 4),
        "y1": round(min(1.0, y1 + 0.01), 4),
    }


# ─────────────────────────────────────────────
#  MEDIÇÃO EM PIXELS da faixa que o blur vai cobrir
#
#  O OCR dá a banda APROXIMADA do texto. Antes de aplicar o blur, medimos os
#  pixels de MUITOS frames para saber a faixa REAL — nem sobrando (blur gigante
#  cobrindo cena/legenda) nem faltando (texto vazando pelas beiradas).
# ─────────────────────────────────────────────

def _carregar_cinza(paths):
    """Empilha os frames em tons de cinza como (n, H, W); None se não der."""
    imgs = []
    for p in paths:
        try:
            imgs.append(np.asarray(Image.open(p).convert("L"), dtype=np.float32))
        except Exception:
            continue
    if len(imgs) < 3:
        return None
    forma = imgs[0].shape[:2]
    imgs = [g for g in imgs if g.shape[:2] == forma]
    return np.stack(imgs) if len(imgs) >= 3 else None


def _perfis_linha(A):
    """
    Perfis por LINHA da imagem (mediana entre os frames):
      mean — brilho médio da linha
      std  — variação DENTRO da linha (tarja lisa ≈ 0; cena com conteúdo ≫ 0)
      dark — fração de pixels escuros
      edge — densidade de bordas horizontais = evidência de TEXTO na linha
    Estrutura (mean/std/dark) usa MEDIANA: é o que vale na maior parte do vídeo.
    Texto usa PERCENTIL 70: a legenda muda de frase e some entre cues — se
    exigíssemos a mediana, linhas com texto em "só" 40% dos frames escapariam do
    blur e o texto original vazaria.
    """
    d = np.abs(np.diff(A, axis=2))
    return {
        "mean": np.median(A.mean(axis=2), axis=0),
        "std":  np.median(A.std(axis=2), axis=0),
        "dark": np.median((A < 50).mean(axis=2), axis=0),
        "edge": np.percentile((d > 40).mean(axis=2), 70, axis=0),
    }


def _maior_bloco(mascara, gap, alvo):
    """Maior bloco contíguo de True (tolerando buracos de até `gap`) que mais se
    sobrepõe ao intervalo `alvo` (a0, a1). Retorna (ini, fim) ou None."""
    idx = np.where(mascara)[0]
    if idx.size == 0:
        return None
    grupos = np.split(idx, np.where(np.diff(idx) > gap)[0] + 1)
    a0, a1 = alvo
    melhor, melhor_ov = None, -1e9
    for g in grupos:
        ini, fim = int(g[0]), int(g[-1])
        ov = min(fim, a1) - max(ini, a0)          # sobreposição com o alvo
        if ov > melhor_ov:
            melhor, melhor_ov = (ini, fim), ov
    return melhor


def _medir_faixa(A, y0f, y1f):
    """
    Mede nos PIXELS a faixa que o blur precisa cobrir, partindo da banda
    aproximada do OCR (y0f..y1f, frações do frame).

    Retorna dict ou None (aí o chamador fica com a banda do OCR):
      y0, y1      — topo/base da FAIXA a cobrir (frações do frame)
      cy_texto    — centro do TEXTO em si (é onde a nossa legenda deve ficar; o
                    centro da faixa pode estar deslocado quando ela cresce pela
                    tarja preta em volta)
      x0, x1      — extensão horizontal real do texto (frações)
      faixa_total — True só quando o texto está sobre uma TARJA LISA de ponta a
                    ponta (aí o blur vai de x=0 a x=W); False cobre só o texto.
    """
    n, H, W = A.shape
    prof = _perfis_linha(A)
    lin_ocr = (int(y0f * H), int(y1f * H))
    jan0 = max(0, lin_ocr[0] - int(0.04 * H))
    jan1 = min(H, lin_ocr[1] + int(0.04 * H))
    if jan1 - jan0 < 6:
        return None

    # 1) Linhas de TEXTO, com histerese: um limiar FORTE acha o miolo do texto e
    #    um limiar FRACO estende até onde a evidência morre (topo dos acentos, pé
    #    das descidas, 2ª linha). Só o forte cortaria as bordas e o texto original
    #    vazaria por cima/baixo do blur.
    janela = prof["edge"][jan0:jan1]
    pico = float(janela.max())
    forte = max(0.006, 0.35 * pico)
    fraco = max(0.003, 0.15 * pico)
    gap = max(3, int(0.008 * H))          # tolera o vão entre linhas de texto
    miolo = np.zeros(H, dtype=bool)
    miolo[jan0:jan1] = janela >= forte
    bloco = _maior_bloco(miolo, gap, lin_ocr)
    if bloco is None:
        return None
    ytop, ybase = bloco
    if ybase - ytop < 3:
        return None
    # estende pelo limiar FRACO (mesma tolerância de vão: uma única linha fraca no
    # meio de duas linhas de legenda não pode travar o crescimento)
    debil = np.zeros(H, dtype=bool)
    debil[jan0:jan1] = janela >= fraco
    bloco_fraco = _maior_bloco(debil, gap, (ytop, ybase))
    if bloco_fraco:
        ytop = min(ytop, bloco_fraco[0]); ybase = max(ybase, bloco_fraco[1])

    cy_texto = (ytop + ybase + 1) / 2 / H     # centro do texto, antes da tarja

    # 2) Linhas de TARJA: lisas de ponta a ponta (std baixo) e escuras. A cena do
    #    vídeo, mesmo escura, tem std alto — é isso que antes fazia a "tarja"
    #    engolir metade da tela.
    barra = (prof["std"] < 20) & (prof["dark"] >= 0.7)

    # 3) Cresce a faixa para dentro da tarja, com teto (tarja não é meia tela).
    teto = max(4, int(0.05 * H))
    cima = 0
    while cima < teto and ytop - 1 >= 0 and barra[ytop - 1]:
        ytop -= 1; cima += 1
    baixo = 0
    while baixo < teto and ybase + 1 < H and barra[ybase + 1]:
        ybase += 1; baixo += 1
    faixa_total = (cima + baixo) >= max(3, int(0.004 * H)) and \
                  float(np.median(prof["dark"][ytop:ybase + 1])) >= 0.6
    # Vídeo DIVIDIDO (cena em cima, imagem embaixo, tarja preta no meio): a faixa
    # a cobrir é EXATAMENTE a tarja — ela já vai de ponta a ponta e o texto está
    # todo dentro dela. Passar disso borra a cena/imagem de graça, que era o que
    # aparecia como véu cinza acima e abaixo da tarja.

    # 4) Extensão horizontal real do texto (colunas com borda dentro da faixa).
    #    Percentil alto entre frames: cada frase ocupa colunas diferentes, então a
    #    mediana zeraria tudo fora do centro e o blur ficaria estreito demais.
    sub = A[:, ytop:ybase + 1, :]
    dcol = np.percentile((np.abs(np.diff(sub, axis=2)) > 40).mean(axis=1), 90, axis=0)
    cols = np.where(dcol >= max(0.02, 0.30 * float(dcol.max() or 0)))[0]
    if cols.size >= 2:
        x0 = float(np.percentile(cols, 1)) / W
        x1 = float(np.percentile(cols, 99) + 1) / W
    else:
        x0, x1 = 0.0, 1.0

    # 5) Teto de altura: uma faixa de legenda não passa de ~20% da tela. Se passou,
    #    a medição saiu do controle → recorta em torno do centro do OCR.
    if (ybase - ytop) / H > 0.20:
        centro = (lin_ocr[0] + lin_ocr[1]) // 2
        meia = int(0.10 * H)
        ytop = max(0, centro - meia); ybase = min(H - 1, centro + meia)

    # Margem só faz sentido quando NÃO há tarja (texto solto sobre a cena): aí
    # uma folga ajuda a pegar contorno/sombra do glifo. Com tarja, margem = 0.
    m = 0 if faixa_total else max(1, int(0.005 * H))
    return {
        "y0": round(max(0.0, (ytop - m) / H), 4),
        "y1": round(min(1.0, (ybase + 1 + m) / H), 4),
        "cy_texto": round(cy_texto, 4),
        "x0": round(max(0.0, x0), 4),
        "x1": round(min(1.0, x1), 4),
        "faixa_total": bool(faixa_total),
    }


def _escolher_banda(caixas, A, n_frames):
    """
    Escolhe, entre as faixas horizontais onde o OCR viu texto, qual é a LEGENDA
    QUEIMADA do vídeo — e não logo de camisa/parede/marca d'água.

    Contar caixas não basta e já pôs o blur no lugar errado: um vídeo com imagem
    fixa embaixo tem "CIMED"/"StockCar" em TODOS os frames, e a cena ao vivo rende
    dezenas de leituras de patrocinador. O que separa a legenda do resto:

      • CONFIANÇA do OCR — texto renderizado limpo dá ~0.95-1.0; letreiro de
        camisa/parede dá 0.5-0.8 (é o sinal mais forte, entra ao cubo na nota);
      • MUDA de texto ao longo do vídeo (acompanha a fala);
      • fica CENTRALIZADA na horizontal;
      • NÃO é um pixel congelado (logo em imagem fixa).

    `caixas` = lista de dicts (x0,x1,y0,y1,yc,idx,txt,conf).
    Retorna as caixas da faixa escolhida, ou [] se nenhuma candidata convence.
    """
    if not caixas:
        return []
    melhor, melhor_nota = [], 0.0
    for c in caixas:
        grupo = [b for b in caixas if abs(b["yc"] - c["yc"]) < 0.035]
        frames = len(set(b["idx"] for b in grupo))
        if frames < max(2, int(0.25 * n_frames)):
            continue
        conf = float(np.median([b["conf"] for b in grupo]))
        if conf < 0.50:
            continue        # leitura ruim → provável ruído da cena
        x0 = float(np.percentile([b["x0"] for b in grupo], 5))
        x1 = float(np.percentile([b["x1"] for b in grupo], 95))
        y0 = min(b["y0"] for b in grupo); y1 = max(b["y1"] for b in grupo)
        congelado = A is not None and _regiao_estatica(
            A, {"x0": x0, "x1": x1, "y0": y0, "y1": y1})

        nota = frames * (conf ** 3)
        if _texto_dinamico(grupo):
            nota *= 1.4                       # texto que muda = legenda de fala
        if abs((x0 + x1) / 2 - 0.5) <= 0.15:
            nota *= 1.3                       # centralizada na tela
        if congelado:
            nota *= 0.35                      # pixels parados = logo/marca
        if nota > melhor_nota:
            melhor, melhor_nota = grupo, nota
    # A nota serve para COMPARAR candidatas (é relativa: conf³ varia muito entre
    # vídeos). Se a vencedora merece blur mesmo é decidido depois, em
    # detectar_legenda_ocr, com a faixa já medida nos pixels.
    return melhor


def _texto_dinamico(grupo):
    """True se o texto da faixa MUDA ao longo do vídeo — legenda acompanhando a
    fala, e não um letreiro fixo."""
    frames = len(set(b["idx"] for b in grupo))
    textos = {re.sub(r"\W+", "", b["txt"].lower()) for b in grupo}
    textos.discard("")
    return len(textos) >= max(2, int(0.4 * frames))


def _variacao_temporal(A, box):
    """Variação de cada pixel da região (dict x0,x1,y0,y1 em frações) ao longo dos
    frames. None se a região for degenerada."""
    n, H, W = A.shape
    y0 = max(0, int(box["y0"] * H)); y1 = min(H, int(box["y1"] * H) + 1)
    x0 = max(0, int(box["x0"] * W)); x1 = min(W, int(box["x1"] * W) + 1)
    if y1 - y0 < 4 or x1 - x0 < 4:
        return None
    return A[:, y0:y1, x0:x1].std(axis=0)


def _regiao_estatica(A, box, limite=4.0):
    """
    True se a região está CONGELADA (quase nenhum pixel muda entre os frames) —
    assinatura de logo/marca em imagem fixa. Usa a mediana: exige a região toda
    parada, não só um pedaço.
    """
    var = _variacao_temporal(A, box)
    return var is not None and float(np.median(var)) <= limite


def _texto_sobreposto_estatico(A, box, minimo=0.5):
    """
    True se os TRAÇOS DE LETRA da região estão parados ao longo do vídeo —
    assinatura de texto QUEIMADO por cima da imagem (meme).

    Olhar a região inteira não serve: uma tarja semi-transparente deixa o vídeo
    vazar por trás e a região "muda" mesmo com o texto parado (medido: só 11% dos
    pixels quietos num overlay real). Então comparamos apenas os pixels de BORDA
    do frame-mediano — onde estão os traços das letras. Medido nos testes:
    overlay estático 0.96, vídeo ao vivo 0.00.
    """
    n, H, W = A.shape
    y0 = max(0, int(box["y0"] * H)); y1 = min(H, int(box["y1"] * H) + 1)
    x0 = max(0, int(box["x0"] * W)); x1 = min(W, int(box["x1"] * W) + 1)
    if y1 - y0 < 4 or x1 - x0 < 4:
        return False
    S = A[:, y0:y1, x0:x1]
    var = S.std(axis=0)
    med = np.median(S, axis=0)      # no vídeo ao vivo a mediana sai lavada
    gx = np.abs(np.diff(med, axis=1, prepend=med[:, :1]))
    gy = np.abs(np.diff(med, axis=0, prepend=med[:1, :]))
    traco = (gx > 40) | (gy > 40)
    if traco.sum() < 50:
        return False                # sem traço nítido persistente → não é texto
    return float((var[traco] < 6.0).mean()) >= minimo


def detectar_legenda_ocr(video_path, n=N_FRAMES_PIXEL, n_ocr=N_FRAMES_OCR):
    """
    Detecta a legenda QUEIMADA do vídeo ORIGINAL (antes do espelhamento): se
    existe, ONDE está (faixa exata) e se ela está sobre uma tarja de ponta a ponta.

    Duas etapas:
      1) OCR (em `n_ocr` frames) → banda APROXIMADA do texto + frase de meme no topo;
      2) medição em PIXELS (em `n` frames, resolução maior) → faixa REAL que o blur
         deve cobrir (_medir_faixa) e confirmação de que o meme é estático
         (_texto_sobreposto_estatico). É o que evita blur gigante fora de lugar.

    Como a detecção roda no vídeo original, o texto está normal (não espelhado),
    então o OCR o localiza bem. A posição vertical não muda com o hflip.

    Retorna dict:
      {'tem_legenda': True, 'x0','x1','y0','y1','cy','faixa_total','meme'}
      (frações 0..1, faixa já com margem)
    ou {'tem_legenda': False} quando não há legenda persistente (→ legenda embaixo).
    """
    tmp = tempfile.mkdtemp()
    try:
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path], capture_output=True, text=True).stdout.strip())
        paths = []
        for i in range(n):
            t = dur * (i + 0.5) / n
            out = os.path.join(tmp, f"f{i:03d}.png")
            subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", video_path,
                            "-frames:v", "1", "-vf", f"scale={LARGURA_ANALISE}:-1",
                            out, "-loglevel", "error"],
                           check=False)
            if os.path.exists(out):
                paths.append(out)
        if len(paths) < 3:
            return {"tem_legenda": False}

        # Frames para a MEDIÇÃO em pixels: todos (barato, é só numpy).
        A = _carregar_cinza(paths)
        # Frames para o OCR: um subconjunto espalhado (o OCR é o caro, ~4s/frame).
        passo = max(1, len(paths) // max(1, n_ocr))
        paths_ocr = paths[::passo][:n_ocr]

        reader = _get_ocr_reader()

        caixas = []       # legenda (meio/baixo)   } dicts com x0,x1,y0,y1,yc,
        caixas_topo = []  # frase de meme no topo  }   idx (frame), txt e conf
        for idx, p in enumerate(paths_ocr):
            img = np.asarray(Image.open(p).convert("RGB"))
            # OCR em resolução menor (mais rápido); as caixas viram FRAÇÕES, então
            # a escala usada aqui não afeta as coordenadas.
            if img.shape[1] > LARGURA_OCR:
                alt = int(img.shape[0] * LARGURA_OCR / img.shape[1])
                img = np.asarray(Image.fromarray(img).resize((LARGURA_OCR, alt)))
            hi, wi = img.shape[:2]
            for box, txt, conf in reader.readtext(img):
                t = txt.strip()
                if conf < 0.35 or len(t) < 2:
                    continue
                xs = [q[0] for q in box]; ys = [q[1] for q in box]
                caixa = {"x0": min(xs) / wi, "x1": max(xs) / wi,
                         "y0": min(ys) / hi, "y1": max(ys) / hi,
                         "yc": (min(ys) + max(ys)) / 2 / hi,
                         "idx": idx, "txt": t, "conf": float(conf)}
                if 0.30 <= caixa["yc"] <= 0.92:              # legenda (meio/baixo)
                    caixas.append(caixa)
                elif 0.02 <= caixa["yc"] < 0.30 and len(t) >= 3:  # meme no topo?
                    caixas_topo.append(caixa)

        n_ocr_real = len(paths_ocr)
        meme = _detectar_meme_topo(caixas_topo, n_ocr_real)
        # Confirma nos pixels que o meme é um texto SOBREPOSTO (região estática).
        # Vídeo ao vivo no topo muda a cada frame → o OCR ali era lixo e virava
        # blur gigante fora de lugar.
        if meme and A is not None:
            if not _texto_sobreposto_estatico(A, meme):
                print("   🚫 topo não é meme (nada parado ali) → sem blur no topo.")
                meme = None
            else:
                med_m = _medir_faixa(A, meme["y0"], meme["y1"])
                if med_m:      # ajusta a caixa do meme na medida real do texto
                    meme["y0"], meme["y1"] = med_m["y0"], med_m["y1"]
                    # No X a caixa do OCR já é confiável: a medição só pode
                    # empurrar um pouco (senão a cena em volta esticaria o blur
                    # até as bordas e o texto reescrito sairia do lugar).
                    meme["x0"] = max(0.0, max(min(meme["x0"], med_m["x0"]),
                                              meme["x0"] - 0.04))
                    meme["x1"] = min(1.0, min(max(meme["x1"], med_m["x1"]),
                                              meme["x1"] + 0.04))

        if len(caixas) < 3:
            return {"tem_legenda": False, "meme": meme}

        # Qual faixa é a legenda queimada (e não uma logo da imagem de fundo)
        melhor = _escolher_banda(caixas, A, n_ocr_real)
        if len(melhor) < 3:
            return {"tem_legenda": False, "meme": meme}   # sem legenda persistente

        # Banda APROXIMADA do OCR (percentis no X p/ ignorar outliers) + margem
        x0 = float(np.percentile([b["x0"] for b in melhor], 5))
        x1 = float(np.percentile([b["x1"] for b in melhor], 95))
        y0 = min(b["y0"] for b in melhor)
        y1 = max(b["y1"] for b in melhor)
        x0 = max(0.0, x0 - 0.02); x1 = min(1.0, x1 + 0.02)
        y0 = max(0.0, y0 - 0.015); y1 = min(1.0, y1 + 0.015)

        # Medição em PIXELS: a faixa REAL do texto. Só vira blur de ponta a ponta
        # quando o texto está sobre uma TARJA LISA (std baixo na largura toda) —
        # cena escura NÃO é tarja, e era isso que estourava o blur.
        faixa_total = False
        cy = (y0 + y1) / 2
        med = _medir_faixa(A, y0, y1) if A is not None else None
        if med:
            y0, y1 = med["y0"], med["y1"]
            faixa_total = med["faixa_total"]
            cy = med["cy_texto"]      # nossa legenda vai onde estava o texto
            # X: a medição pode alargar a caixa do OCR, mas só um pouco (o texto
            # pode ter glifos que o OCR cortou; o resto da linha é cena).
            x0 = max(0.0, max(min(x0, med["x0"]), x0 - 0.06))
            x1 = min(1.0, min(max(x1, med["x1"]), x1 + 0.06))

        # Última trava: legenda queimada MUDA de texto, ou está sobre uma tarja,
        # ou é uma camada sobreposta centralizada (título fixo). Fora disso é
        # letreiro da cena (placa, camisa) — não vale borrar nem mandar a nossa
        # legenda para lá.
        camada = A is not None and _texto_sobreposto_estatico(
            A, {"x0": x0, "x1": x1, "y0": y0, "y1": y1})
        centralizada = abs((x0 + x1) / 2 - 0.5) <= 0.15
        if not (_texto_dinamico(melhor) or faixa_total or (camada and centralizada)):
            print("   🚫 texto do meio parece letreiro da cena → sem blur, "
                  "legenda vai para baixo.")
            return {"tem_legenda": False, "meme": meme}

        # Sempre BLUR (sem tarja) na legenda; 'meme' preserva a frase do topo.
        return {"tem_legenda": True,
                "x0": round(x0, 4), "x1": round(x1, 4),
                "y0": round(y0, 4), "y1": round(y1, 4),
                "cy": round(cy, 4), "faixa_total": faixa_total, "meme": meme}
    except Exception as e:
        print(f"⚠️ Detecção OCR falhou ({e}); legenda irá para baixo, sem cobertura.")
        return {"tem_legenda": False}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fmt_ass_ts(s):
    h = int(s // 3600); s -= h * 3600
    m = int(s // 60); s -= m * 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _quebrar_linhas(texto, max_chars):
    """Quebra um texto em linhas de até max_chars caracteres (por palavra)."""
    linhas, atual = [], ""
    for w in texto.split():
        if atual and len(atual) + 1 + len(w) > max_chars:
            linhas.append(atual)
            atual = w
        else:
            atual = (atual + " " + w).strip()
    if atual:
        linhas.append(atual)
    return linhas or [texto]


def gerar_ass_legenda(segments, ass_path, W, H, centro_y_px, duracao=None,
                      meme=None, contorno=False):
    """
    Gera o .ASS da legenda KARAOKÊ — UMA PALAVRA POR VEZ, no estilo do canal
    original (medido quadro a quadro nos vídeos deles):

      • uma palavra por cue, em MAIÚSCULAS, centralizada na faixa detectada;
      • fonte Anton (em assets/fontes, via fontsdir) com ScaleX 110 — a medição
        deu 425 px de largura contra 424 px do original em "TUDO" (4K);
      • cada palavra ENTRA um pouco abaixo e SOBE 1% da altura do vídeo ao longo
        de toda a sua duração (\\move) — no original a distância é fixa e a
        velocidade varia com o tempo da palavra;
      • a palavra fica até a PRÓXIMA começar; em silêncio, a última permanece;
      • a cada 3 palavras brancas, uma AZUL MARINHO.

    `duracao` é usada para o fim da última palavra. `contorno=True` adiciona borda
    preta (necessário quando a legenda cai sobre a cena, sem tarja atrás).
    Se 'meme' for dado, adiciona um evento ESTÁTICO no topo reescrevendo a frase.
    """
    palavras = [(w["start"], w.get("end") or w["start"], (w.get("word") or "").strip())
                for seg in segments for w in seg.get("words", [])
                if (w.get("word") or "").strip()]
    palavras.sort(key=lambda p: p[0])

    # Cada palavra vale até a próxima começar (isso já "estica sobre o silêncio"
    # e mantém a última no ar enquanto ninguém fala).
    fim_video = duracao or (palavras[-1][1] + 2.0 if palavras else 0.0)
    cues = []
    for i, (ini, fim_fala, txt) in enumerate(palavras):
        fim = palavras[i + 1][0] if i + 1 < len(palavras) else max(fim_video, fim_fala)
        if fim <= ini:
            continue
        cues.append((ini, fim, txt.upper()))

    max_chars = max((len(t) for _, _, t in cues), default=0)
    # fontsize p/ dar a altura de letra do original (4,79% de H). O libass
    # renderiza a Anton a ~0,50 do fontsize — medido, não é o mesmo fator do PIL.
    fs = max(24, int(round(ALT_LETRA_LEGENDA * H / ANTON_ALT_POR_FS)))
    # trava de largura: palavra longa não pode encostar na borda. Na Anton com
    # ScaleX 110 cada caractere ocupa ~0,32 do fontsize.
    if max_chars > 0:
        fs = min(fs, max(24, int(0.92 * W / (0.32 * max_chars))))
    dy = max(1, int(round(DESLOC_LEGENDA * H / 2)))     # metade p/ cada lado
    borda = max(2, int(fs * 0.05)) if contorno else 0
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # Anton já é pesada: Bold=0 (senão o libass engrossa artificialmente).
        # ScaleX 110 = a largura medida no original.
        f"Style: Kar,Anton,{fs},{COR_LEGENDA},&H00000000,&H00000000,0,0,0,0,"
        f"110,100,0,0,1,{borda},0,5,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    linhas = [header]
    cx = W // 2
    for i, (st, en, txt) in enumerate(cues):
        txt = txt.replace("\n", " ").strip()
        # 3 brancas + 1 azul marinho, em ciclo
        cor = "" if (i + 1) % CICLO_COR_LEGENDA else f"\\c{COR_LEGENDA_DESTAQUE}"
        linhas.append(
            f"Dialogue: 0,{_fmt_ass_ts(st)},{_fmt_ass_ts(en)},Kar,,0,0,0,,"
            f"{{\\an5{cor}\\move({cx},{centro_y_px + dy},{cx},{centro_y_px - dy})}}{txt}"
        )

    # Frase de MEME no topo: evento ESTÁTICO (dura o vídeo todo), reescrito sobre
    # o blur, preservando o texto original (já corrigido por IA).
    if meme and meme.get("texto"):
        linhas_meme = _quebrar_linhas(meme["texto"], 18)
        maior = max((len(l) for l in linhas_meme), default=1)
        mfs = max(24, min(int(ALT_LETRA_LEGENDA * H / ANTON_ALT_POR_FS * 0.8),
                          int(0.92 * W / (0.32 * maior))))
        # O meme foi localizado no vídeo ORIGINAL e o ASS entra DEPOIS do hflip →
        # espelha o X para o texto cair exatamente sobre o blur do topo.
        mcx = W - int((meme["x0"] + meme["x1"]) / 2 * W)
        mcy = int((meme["y0"] + meme["y1"]) / 2 * H)
        texto_meme = "\\N".join(linhas_meme)
        linhas.append(
            f"Dialogue: 0,0:00:00.00,9:59:59.99,Kar,,0,0,0,,"
            f"{{\\an5\\pos({mcx},{mcy})\\fs{mfs}}}{texto_meme.upper()}"
        )

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))
    return len(cues), max_chars, fs


def baixar_short(url):
    """
    Usa a biblioteca yt-dlp para baixar um vídeo de uma URL.
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    print(f"[{timestamp}] 📥 Iniciando download de: {url}")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    
    # Extraindo apenas ID e Titulo para formar o nome de saída
    out_tmpl = os.path.join(DOWNLOAD_DIR, f'%(id)s_{timestamp}.%(ext)s')
    
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': out_tmpl,
        'noplaylist': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info_dict)
            print(f"✅ Download concluído: {filename}")
            return filename
    except Exception as e:
        print(f"❌ ERRO ao tentar baixar o vídeo. Motivo: {e}")
        return None


def processar_video_whisper_nativo(video_path):
    print(f"\n⚙️ Iniciando processamento de: {video_path} (sistema v{SISTEMA_VERSAO})")
    base_dir = os.path.dirname(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    
    srt_path = os.path.join(base_dir, f"{base_name}.srt")
    audio_path = os.path.join(base_dir, f"{base_name}_audio.wav")
    final_output = os.path.join(base_dir, f"{base_name}_final.mp4")

    # Passo 1: Extrair áudio para o Whisper
    print("Passo 1/3: Extraindo áudio para transcrição...")
    try:
        subprocess.run([
            "ffmpeg", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1", audio_path, "-y"
        ], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Erro ao extrair áudio: {e.stderr.decode('utf-8', errors='ignore')}")
        return

    # Passo 2: Transcrever (timestamps por palavra), detectar a legenda original
    # (OCR) e gerar o ASS de 1 linha na posição certa.
    print(f"Passo 2/3: Transcrevendo com faster-whisper ({WHISPER_MODEL}, PT-BR)...")
    ass_path = os.path.join(base_dir, f"{base_name}.ass")
    texto_transcricao = ""
    leg = {"tem_legenda": False}
    try:
        segmentos, texto_transcricao = transcrever_audio(audio_path)

        W, H = _dimensoes_video(video_path)

        # Detecta (via OCR) a legenda QUEIMADA do vídeo ORIGINAL (texto normal)
        print("🔎 Detectando a legenda original (OCR)...")
        leg = detectar_legenda_ocr(video_path)
        if leg.get("tem_legenda"):
            centro_y = int(leg["cy"] * H)
            print(f"🎯 Legenda original em y~{int(leg['cy']*100)}% → blur nessa faixa.")
        else:
            centro_y = int(0.85 * H)  # sem legenda original → a nossa vai para baixo
            print("🎯 Sem legenda no original → nossa legenda irá para baixo (sem blur).")

        # Frase de MEME no topo (se houver): preserva o texto, corrigindo via IA
        meme = leg.get("meme") if isinstance(leg, dict) else None
        if meme and meme.get("texto"):
            print(f"🧩 Meme no topo: '{meme['texto']}' — corrigindo via IA...")
            meme["texto"] = corrigir_texto_ia(meme["texto"])
            print(f"   → meme reescrito: '{meme['texto']}'")

        # Sem tarja atrás, a legenda cai sobre a cena → precisa de contorno.
        n_cues, max_chars, fs = gerar_ass_legenda(
            segmentos, ass_path, W, H, centro_y, duracao=_duracao_video(video_path),
            meme=meme, contorno=not leg.get("faixa_total"))
        print(f"📝 {n_cues} palavras na legenda karaokê "
              f"(maior: {max_chars} chars, fonte {fs}).")
    except Exception as e:
        print(f"❌ Erro ao rodar whisper internamente: {e}")
        return

    if not os.path.exists(ass_path):
        print("❌ Arquivo de legenda não foi gerado.")
        return

    # Passo 3: Espelhar e cobrir a legenda original conforme o estilo detectado
    print("Passo 3/3: Espelhando o vídeo e adicionando legendas...")
    try:
        ass_basename = os.path.basename(ass_path)
        video_basename = os.path.basename(video_path)
        final_basename = os.path.basename(final_output)

        # Monta as caixas de BLUR: cobrem SÓ o que existe de texto no original
        # (a faixa medida em pixels). A NOSSA legenda é desenhada por cima do
        # blur pelo ASS, então não precisa (nem deve) inflar a caixa — inflar era
        # o que gerava aquele retângulo cinza gigante sobre a cena.
        blur_boxes = []
        if leg.get("tem_legenda"):
            bx = int(leg["x0"] * W); bx1 = int(leg["x1"] * W)
            by = int(leg["y0"] * H); by1 = int(leg["y1"] * H)
            if leg.get("faixa_total"):
                # Tarja lisa de ponta a ponta (vídeo dividido) → largura total e
                # altura EXATA da tarja medida: nada de margem, senão o blur
                # invade a cena em cima e a imagem embaixo.
                bx = 0; bx1 = W
                print("   ↔️ Tarja original de ponta a ponta → blur na largura "
                      "total, altura exata da tarja.")
            else:
                # Texto solto sobre a cena: uma margem pequena ajuda a cobrir
                # contorno/sombra das letras.
                m = int(0.008 * W)
                bx = max(0, bx - m); bx1 = min(W, bx1 + m)
                by = max(0, by - int(0.004 * H)); by1 = min(H, by1 + int(0.004 * H))
            print(f"   🩹 Faixa da legenda: y {by/H:.3f}→{by1/H:.3f} "
                  f"({(by1-by)/H*100:.1f}% da altura), x {bx/W:.3f}→{bx1/W:.3f}")
            blur_boxes.append((bx, by, max(1, bx1 - bx), max(1, by1 - by)))
        if meme:
            mm = int(0.012 * W); mv = int(0.004 * H)
            mx = max(0, int(meme["x0"] * W) - mm)
            mx1 = min(W, int(meme["x1"] * W) + mm)
            my = max(0, int(meme["y0"] * H) - mv)
            my1 = min(H, int(meme["y1"] * H) + mv)
            print(f"   🩹 Faixa do meme: y {my/H:.3f}→{my1/H:.3f}, "
                  f"x {mx/W:.3f}→{mx1/W:.3f}")
            blur_boxes.append((mx, my, max(1, mx1 - mx), max(1, my1 - my)))

        if blur_boxes:
            # ATENÇÃO: as caixas foram medidas no vídeo ORIGINAL, mas o blur é
            # aplicado DEPOIS do hflip → o X tem que ser espelhado (x → W-x-w),
            # senão o blur cai no lado errado da tela.
            blur_boxes = [(W - x - w, y, w, h) for (x, y, w, h) in blur_boxes]

            # Cadeia de blurs localizados (gblur) + a legenda/meme por cima (ass)
            partes = ["[0:v]hflip[v0]"]
            prev = "v0"
            for i, (x, y, w, h) in enumerate(blur_boxes):
                # Força proporcional à ALTURA da faixa (é ela que dá a escala do
                # traço das letras). h/4 foi o mínimo que deixou o texto original
                # ilegível nos testes em 1080p e 4K — abaixo disso ainda se lia.
                sig = int(max(12, min(150, h / 4)))
                # O gblur perde força na BORDA do recorte (não tem vizinho pra
                # puxar), e texto encostado na quina ficava legível. Então
                # recortamos uma faixa MAIOR só para o blur ter vizinhança e
                # depois voltamos ao tamanho exato antes do overlay — o que vai
                # para a tela é só a caixa medida, sem vazar para fora dela.
                folga = sig // 2
                ya = max(0, y - folga)
                ha = min(H - ya, h + folga + (y - ya))
                partes.append(f"[{prev}]split=2[{prev}a][{prev}t]")
                partes.append(f"[{prev}t]crop={w}:{ha}:{x}:{ya},gblur=sigma={sig},"
                              f"crop={w}:{h}:0:{y - ya}[bl{i}]")
                partes.append(f"[{prev}a][bl{i}]overlay={x}:{y}[v{i+1}]")
                prev = f"v{i+1}"
            # fontsdir: a Anton vive no repo, não instalada no sistema
            partes.append(f"[{prev}]ass={ass_basename}:fontsdir={DIR_FONTES}")
            ff_args = ["-filter_complex", ";".join(partes)]
            print(f"🎬 Cobertura: {len(blur_boxes)} blur(s) "
                  f"({'legenda' if leg.get('tem_legenda') else ''}"
                  f"{'+meme' if meme else ''}).")
        else:
            # Sem legenda nem meme no original → só espelha; nossa legenda embaixo
            ff_args = ["-vf", f"hflip,ass={ass_basename}:fontsdir={DIR_FONTES}"]
            print("🎬 Sem cobertura (legenda embaixo).")

        subprocess.run(
            ["ffmpeg", "-i", video_basename, *ff_args, "-c:a", "copy", final_basename, "-y"],
            cwd=base_dir, check=True, capture_output=True)
        print(f"✅ Processamento concluído! Vídeo final salvo em: {final_output}")

        # Proveniência: grava nome + versão do sistema + o que foi detectado,
        # para sabermos se este vídeo saiu antes ou depois de cada mudança.
        registrar_processamento(
            video_path, deteccao=leg, saida=final_output,
            extra={"n_cues": n_cues, "fs": fs, "cobertura": len(blur_boxes)})

        # Espelha no banco de controle (Postgres) — best-effort, não bloqueia.
        # NÃO manda título: o título do post no canal ORIGINAL é gravado pelo
        # watcher e é ele que o painel mostra (com link pra fonte). Mandar o nome
        # do arquivo aqui sobrescrevia tudo por "bfAcE48SKDk 20260805 124549".
        try:
            from comum import db_bridge
            db_bridge.registrar_video_processado(
                final_output, deteccao=leg, versao=SISTEMA_VERSAO,
                transcricao=texto_transcricao)
        except Exception as e:
            print(f"⚠️  espelho no banco falhou (ignorado): {e}")

        # Enfileira para postagem no BANCO (fila = model Post 'pendente').
        if POSTER_DISPONIVEL and os.path.exists(final_output):
            legenda_ia = gerar_legenda_ia(texto_transcricao, titulo_original="")
            adicionar_na_fila(final_output, caption=legenda_ia)

    except subprocess.CalledProcessError as e:
        print(f"❌ Erro ao processar FFmpeg final: {e.stderr.decode('utf-8', errors='ignore')}")

    # Limpeza
    for temp_file in [audio_path, ass_path, video_path]:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass


def processar_url(url):
    """Baixa e processa um vídeo. Reaproveita os modelos já carregados em memória
    (faster-whisper / EasyOCR são cacheados no módulo) — chamado EM PROCESSO pelo
    watcher para NÃO recarregar os modelos a cada vídeo.
    Cada vídeo é transcrito de forma independente (condition_on_previous_text=False),
    então NÃO há 'delírio'/contaminação entre vídeos."""
    caminho = baixar_short(url)
    if caminho:
        processar_video_whisper_nativo(caminho)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        processar_url(sys.argv[1])
    else:
        print("ERRO: Nenhuma URL de vídeo fornecida.")
        print("Uso: python coletor.py 'URL_DO_SHORT'")
