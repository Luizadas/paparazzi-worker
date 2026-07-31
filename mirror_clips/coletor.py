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


def gerar_legenda_ia(texto_transcricao: str, titulo_original: str = "") -> str:
    """
    Gera a legenda do TikTok via LLM local (Ollama) com BASE no conteúdo do
    próprio vídeo (transcrição do Whisper + título original), orientando o LLM
    com técnicas de viralização no TikTok.
    Retorna string vazia se o LLM falhar (o poster cai no template padrão).
    """
    texto = (texto_transcricao or "").strip()
    if not texto:
        return ""

    contexto_titulo = f"\nTÍTULO ORIGINAL DO VÍDEO: {titulo_original}\n" if titulo_original else ""

    # Prompt baseado em boas práticas de legenda viral no TikTok (2026):
    # gancho curto na 1ª frase (o que aparece antes do "ver mais"), CTA para
    # gerar comentários, 1-2 palavras-chave (SEO da busca) e 3-5 hashtags
    # (amplas + de nicho). Legenda curtíssima e escaneável.
    prompt = f"""Você é um especialista em viralização no TikTok no Brasil. Escreva a LEGENDA (descrição) de um post curto.

A transcrição automática abaixo pode estar IMPERFEITA. NÃO copie nem resuma a transcrição — apenas ENTENDA o TEMA e escreva uma legenda NOVA e original.

Estrutura da legenda (siga à risca):
1) GANCHO forte e CURTO na primeira frase (ideal 20-60 caracteres): curiosidade, opinião polêmica, choque ou promessa de valor. É o que aparece antes do "ver mais".
2) (opcional) uma CTA curta pra gerar comentários: "comenta aí", "concorda?", "marca alguém".
3) 3 a 5 HASHTAGS no fim: misture amplas (#fyp #viral #foryou) com 1-2 específicas do tema.

Regras:
- Curtíssima e escaneável: fora as hashtags, no máximo ~150 caracteres.
- Português do Brasil, tom coloquial e natural. No máximo 1-2 emojis.
- Inclua 1-2 palavras-chave do tema (ajuda na busca do TikTok).
- PROIBIDO: copiar/resumir a transcrição, textão, caracteres de outro idioma (chinês etc.), aspas, "Legenda:".
- Responda SÓ com a legenda final (gancho + hashtags), em UMA linha.
{contexto_titulo}
--- TRANSCRIÇÃO (pode estar imperfeita) ---
{texto[:2000]}
"""

    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.8, "num_predict": 120},
    }
    try:
        print("🧠 Gerando legenda com IA (LLM)...")
        resp = requests.post(LLM_API_URL, json=payload, timeout=180)
        resp.raise_for_status()
        saida = resp.json().get("response", "") or ""

        legenda = _pos_processar_legenda(_sanitizar_legenda(saida))
        if legenda:
            print(f"✅ Legenda IA: {legenda}")
        return legenda[:2200]
    except Exception as e:
        print(f"⚠️ Falha ao gerar legenda com IA: {e}. Usarei o template padrão.")
        return ""


def _sanitizar_legenda(saida: str) -> str:
    """Limpa a resposta do LLM: remove blocos de raciocínio, caracteres CJK/estrangeiros
    e prefixos, deixando apenas a legenda em português + hashtags."""
    if not saida:
        return ""
    # Remove blocos <think>...</think> de modelos de raciocínio
    saida = re.sub(r"<think>.*?</think>", "", saida, flags=re.DOTALL)
    # Remove caracteres CJK (chinês/japonês/coreano) e formas de largura total
    saida = re.sub(
        r"[　-〿぀-ヿㇰ-ㇿ㐀-䶿"
        r"一-鿿ꥠ-꥿가-퟿豈-﫿＀-￯]",
        "", saida,
    )
    # Junta em uma linha, remove aspas e prefixos comuns
    linhas = [l.strip().strip('"').strip() for l in saida.splitlines() if l.strip()]
    legenda = " ".join(linhas).strip()
    legenda = re.sub(r"^(legenda|caption)\s*:\s*", "", legenda, flags=re.IGNORECASE)
    # Colapsa espaços múltiplos criados pela remoção de caracteres
    legenda = re.sub(r"\s{2,}", " ", legenda).strip()
    return legenda


def _pos_processar_legenda(legenda: str) -> str:
    """Aplica as boas práticas de legenda viral de forma determinística:
    - separa gancho (texto) das hashtags;
    - encurta o gancho (~150 chars, sem cortar no meio da palavra);
    - garante de 3 a 5 hashtags (amplas + de nicho), sem duplicar.
    """
    if not legenda:
        return ""
    tokens = legenda.split()
    hashtags, texto_toks = [], []
    for t in tokens:
        (hashtags if t.startswith("#") and len(t) > 1 else texto_toks).append(t)
    texto = " ".join(texto_toks).strip(" -–—:")

    # Gancho curto (best practice: primeira linha curtíssima)
    if len(texto) > 150:
        texto = texto[:150].rsplit(" ", 1)[0].rstrip(",;.") + "…"

    # Dedup de hashtags preservando ordem
    vistos, hs = set(), []
    for h in hashtags:
        hl = h.lower()
        if hl not in vistos:
            vistos.add(hl); hs.append(h)
    # Garante hashtags amplas e limita a 5 (3-5 é o recomendado)
    for base in ("#fyp", "#viral", "#foryou"):
        if len(hs) >= 5:
            break
        if base not in vistos:
            vistos.add(base); hs.append(base)
    hs = hs[:5]

    return (f"{texto} {' '.join(hs)}").strip()

# ─────────────────────────────────────────────
#  LEGENDA: detecção da faixa original + geração do ASS (1 linha)
# ─────────────────────────────────────────────

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


def _detectar_meme_topo(caixas_topo, n_frames):
    """
    Detecta uma FRASE DE MEME estática no topo do vídeo (aquele texto de contexto
    que forma a piada). Precisa ser persistente (aparece na maioria dos frames) e
    ter conteúdo de frase (>=8 chars, >=2 palavras) — assim ignora logos/marcas.
    Retorna {'texto', 'x0','x1','y0','y1'} (frações) ou None.
    """
    if len(caixas_topo) < 2:
        return None
    por_frame = {}
    for (x0, x1, y0, y1, yc, idx, t) in caixas_topo:
        por_frame.setdefault(idx, []).append((y0, x0, t))
    if len(por_frame) < max(2, int(0.4 * n_frames)):
        return None   # não é persistente → provavelmente não é meme

    def texto_do_frame(itens):
        return " ".join(z[2] for z in sorted(itens, key=lambda z: (round(z[0], 2), z[1])))

    melhor_idx = max(por_frame, key=lambda i: len(texto_do_frame(por_frame[i])))
    texto = texto_do_frame(por_frame[melhor_idx])
    if len(texto) < 8 or len(texto.split()) < 2:
        return None   # curto demais → provável logo/marca

    xs0 = [b[0] for b in caixas_topo]; xs1 = [b[1] for b in caixas_topo]
    ys0 = [b[2] for b in caixas_topo]; ys1 = [b[3] for b in caixas_topo]
    return {
        "texto": texto,
        "x0": round(max(0.0, min(xs0) - 0.02), 4),
        "x1": round(min(1.0, max(xs1) + 0.02), 4),
        "y0": round(max(0.0, min(ys0) - 0.01), 4),
        "y1": round(min(1.0, max(ys1) + 0.01), 4),
    }


def _detectar_tarja_fullwidth(paths, y0f, y1f):
    """
    Procura, atrás da legenda original, uma TARJA PRETA sólida que atravessa a tela
    de ponta a ponta (lateral a lateral).

    Método robusto a texto vazado nas pontas: uma LINHA pertence à tarja quando
    >=75% da sua largura é quase-preta (o texto branco/colorido ocupa só uma fração
    da linha). Um bloco contíguo dessas linhas, persistente entre frames e sobrepondo
    a banda da legenda, É a tarja. Retorna a altura real (ytopo, ybase) em frações
    0..1 — para o blur cobrir de x=0 a x=W exatamente nessa altura — ou None.
    """
    contagem = None   # por linha: em quantos frames aquela linha é "linha de tarja"
    Href = None
    nframes = 0
    for p in paths:
        try:
            g = np.asarray(Image.open(p).convert("L"))
        except Exception:
            continue
        H, W = g.shape[:2]
        if W < 10:
            continue
        if Href is None:
            Href = H
            contagem = np.zeros(H, dtype=int)
        if H != Href:
            continue
        dark_frac = (g < 50).mean(axis=1)          # fração escura de cada linha
        contagem += (dark_frac >= 0.75).astype(int)
        nframes += 1
    if not nframes or Href is None:
        return None

    bar = contagem >= max(2, int(0.5 * nframes))    # linha de tarja na maioria dos frames
    if not bar.any():
        return None

    # limita à janela ao redor da legenda (evita barras pretas de UI/rodapé alheias)
    ytop_lim = max(0, int((y0f - 0.12) * Href))
    ybot_lim = min(Href, int((y1f + 0.12) * Href))
    idx = np.where(bar)[0]
    idx = idx[(idx >= ytop_lim) & (idx < ybot_lim)]
    if idx.size == 0:
        return None

    # maior bloco contíguo (tolera buracos de até 2 px) que sobreponha a legenda
    grupos = np.split(idx, np.where(np.diff(idx) > 2)[0] + 1)
    ya = y0f * Href; yb = y1f * Href
    melhor, melhor_ov = None, -1
    for gpo in grupos:
        top, base = int(gpo[0]), int(gpo[-1])
        ov = min(base, yb) - max(top, ya)          # sobreposição com a banda da legenda
        if ov > melhor_ov:
            melhor, melhor_ov = (top, base), ov
    if melhor is None or (melhor[1] - melhor[0]) < 4:
        return None
    top, base = melhor
    return (top / Href, (base + 1) / Href)


def detectar_legenda_ocr(video_path, n=16):
    """
    Detecta via OCR a legenda QUEIMADA do vídeo ORIGINAL (antes do espelhamento):
    se existe, ONDE está (caixa exata) e o ESTILO (barra preta vs texto solto).

    Como a detecção roda no vídeo original, o texto está normal (não espelhado),
    então o OCR o localiza bem. A posição vertical não muda com o hflip.

    Retorna dict:
      {'tem_legenda': True, 'estilo': 'barra'|'blur',
       'x0','x1','y0','y1','cy'}  (frações 0..1, caixa já com margem)
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
            out = os.path.join(tmp, f"f{i}.png")
            subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", video_path,
                            "-frames:v", "1", "-vf", "scale=540:-1", out, "-loglevel", "error"],
                           check=False)
            if os.path.exists(out):
                paths.append(out)
        if len(paths) < 3:
            return {"tem_legenda": False}

        reader = _get_ocr_reader()
        H, W = np.asarray(Image.open(paths[0])).shape[:2]

        caixas = []       # legenda (meio/baixo): (x0,x1,y0,y1,yc,idx)
        caixas_topo = []  # frase de meme no topo: (x0,x1,y0,y1,yc,idx,texto)
        for idx, p in enumerate(paths):
            img = np.asarray(Image.open(p).convert("RGB"))
            for box, txt, conf in reader.readtext(img):
                t = txt.strip()
                if conf < 0.35 or len(t) < 2:
                    continue
                xs = [q[0] for q in box]; ys = [q[1] for q in box]
                yc = (min(ys) + max(ys)) / 2 / H
                caixa = (min(xs)/W, max(xs)/W, min(ys)/H, max(ys)/H, yc, idx)
                if 0.30 <= yc <= 0.92:              # legenda (meio/baixo)
                    caixas.append(caixa)
                elif 0.02 <= yc < 0.30 and len(t) >= 3:  # possível meme no topo
                    caixas_topo.append(caixa + (t,))

        meme = _detectar_meme_topo(caixas_topo, len(paths))

        if len(caixas) < 3:
            return {"tem_legenda": False, "meme": meme}

        # Agrupa por y-centro: a banda com MAIS caixas é a legenda (±8%)
        melhor = []
        for c in caixas:
            grupo = [b for b in caixas if abs(b[4] - c[4]) < 0.08]
            if len(grupo) > len(melhor):
                melhor = grupo

        frames_distintos = len(set(b[5] for b in melhor))
        if len(melhor) < 3 or frames_distintos < max(2, int(0.25 * len(paths))):
            return {"tem_legenda": False, "meme": meme}   # sem legenda persistente

        # Caixa-união (percentis no X p/ ignorar outliers; min/max no Y) + margem
        x0 = float(np.percentile([b[0] for b in melhor], 5))
        x1 = float(np.percentile([b[1] for b in melhor], 95))
        y0 = min(b[2] for b in melhor)
        y1 = max(b[3] for b in melhor)
        x0 = max(0.0, x0 - 0.02); x1 = min(1.0, x1 + 0.02)
        y0 = max(0.0, y0 - 0.015); y1 = min(1.0, y1 + 0.015)
        # Se atrás da legenda houver uma TARJA PRETA que atravessa a tela de ponta
        # a ponta, o blur cobre a largura toda; usa a ALTURA REAL da tarja (não só a
        # do texto), pra não vazar as bordas da legenda original.
        tarja = _detectar_tarja_fullwidth(paths, y0, y1)
        faixa_total = tarja is not None
        if tarja:
            y0 = min(y0, tarja[0]); y1 = max(y1, tarja[1])
        cy = (y0 + y1) / 2

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


def gerar_ass_legenda(segments, ass_path, W, H, centro_y_px, max_chars=18, meme=None):
    """
    Gera um arquivo .ASS com legendas de UMA linha (agrupa palavras até max_chars),
    texto branco em negrito com contorno, posicionado no centro vertical detectado
    (\\pos), sobre o blur. Se 'meme' for dado, adiciona um evento ESTÁTICO no topo
    reescrevendo a frase de meme (preservada) sobre o blur do topo.
    """
    cues, cur, ini = [], [], None
    for seg in segments:
        for w in seg.get("words", []):
            tok = (w.get("word") or "").strip()
            if not tok:
                continue
            if ini is None:
                ini = w["start"]
            cand = (" ".join([x[2] for x in cur] + [tok])).strip()
            if len(cand) > max_chars and cur:
                cues.append((ini, cur[-1][1], " ".join(x[2] for x in cur)))
                cur, ini = [(w["start"], w["end"], tok)], w["start"]
            else:
                cur.append((w["start"], w["end"], tok))
    if cur:
        cues.append((ini, cur[-1][1], " ".join(x[2] for x in cur)))

    max_chars = max((len(t) for _, _, t in cues), default=0)
    # Fonte GRANDE, mas limitada pela largura da legenda mais longa para NÃO
    # encostar na borda (mantém a linha dentro de ~86% da largura do vídeo).
    base_fs = int(H * 0.042)
    if max_chars > 0:
        fs_cap = int(0.84 * W / (0.58 * max_chars))
        fs = max(30, min(base_fs, fs_cap))
    else:
        fs = base_fs
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Def,Arial,{fs},&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,"
        "100,100,0,0,1,3,0,5,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    linhas = [header]
    for st, en, txt in cues:
        txt = txt.replace("\n", " ").strip()
        linhas.append(
            f"Dialogue: 0,{_fmt_ass_ts(st)},{_fmt_ass_ts(en)},Def,,0,0,0,,"
            f"{{\\an5\\pos({W // 2},{centro_y_px})}}{txt}"
        )

    # Frase de MEME no topo: evento ESTÁTICO (dura o vídeo todo), reescrito sobre
    # o blur, preservando o texto original (já corrigido por IA).
    if meme and meme.get("texto"):
        linhas_meme = _quebrar_linhas(meme["texto"], 18)
        maior = max((len(l) for l in linhas_meme), default=1)
        mfs = max(28, min(int(H * 0.038), int(0.84 * W / (0.58 * maior))))
        mcx = int((meme["x0"] + meme["x1"]) / 2 * W)
        mcy = int((meme["y0"] + meme["y1"]) / 2 * H)
        texto_meme = "\\N".join(linhas_meme)
        linhas.append(
            f"Dialogue: 0,0:00:00.00,9:59:59.99,Def,,0,0,0,,"
            f"{{\\an5\\pos({mcx},{mcy})\\fs{mfs}}}{texto_meme}"
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

        n_cues, max_chars, fs = gerar_ass_legenda(segmentos, ass_path, W, H, centro_y, meme=meme)
        print(f"📝 {n_cues} legendas de 1 linha geradas (máx {max_chars} chars).")
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

        # Monta as caixas de BLUR: a da legenda (união com a nossa) e a do meme.
        blur_boxes = []
        if leg.get("tem_legenda"):
            ox0 = int(leg["x0"] * W); ox1 = int(leg["x1"] * W)
            oy0 = int(leg["y0"] * H); oy1 = int(leg["y1"] * H)
            char_w = 0.55 * fs                          # caixa da NOSSA legenda
            sub_w = min(int(W * 0.94), int(max_chars * char_w) + int(0.05 * W))
            sub_h = int(fs * 1.7)
            sx = (W - sub_w) // 2
            sy = centro_y - sub_h // 2
            m = int(0.01 * W)
            bx = max(0, min(ox0, sx) - m); bx1 = min(W, max(ox1, sx + sub_w) + m)
            by = max(0, min(oy0, sy) - m); by1 = min(H, max(oy1, sy + sub_h) + m)
            if leg.get("faixa_total"):
                # Tarja preta original atravessa a tela → blur de ponta a ponta,
                # mantendo a altura da faixa (só a largura muda).
                bx = 0; bx1 = W
                print("   ↔️ Tarja original de ponta a ponta → blur na largura total.")
            blur_boxes.append((bx, by, max(1, bx1 - bx), max(1, by1 - by)))
        if meme:
            mx = int(meme["x0"] * W); mx1 = int(meme["x1"] * W)
            my = int(meme["y0"] * H); my1 = int(meme["y1"] * H)
            mm = int(0.015 * W)
            mx = max(0, mx - mm); my = max(0, my - mm)
            blur_boxes.append((mx, my, min(W - mx, mx1 - mx + 2 * mm),
                               min(H - my, my1 - my + 2 * mm)))

        if blur_boxes:
            # Cadeia de blurs localizados (gblur forte) + a legenda/meme por cima (ass)
            partes = ["[0:v]hflip[v0]"]
            prev = "v0"
            for i, (x, y, w, h) in enumerate(blur_boxes):
                sig = max(16, min(w, h) // 5)
                partes.append(f"[{prev}]split=2[{prev}a][{prev}t]")
                partes.append(f"[{prev}t]crop={w}:{h}:{x}:{y},gblur=sigma={sig}[bl{i}]")
                partes.append(f"[{prev}a][bl{i}]overlay={x}:{y}[v{i+1}]")
                prev = f"v{i+1}"
            partes.append(f"[{prev}]ass={ass_basename}")
            ff_args = ["-filter_complex", ";".join(partes)]
            print(f"🎬 Cobertura: {len(blur_boxes)} blur(s) "
                  f"({'legenda' if leg.get('tem_legenda') else ''}"
                  f"{'+meme' if meme else ''}).")
        else:
            # Sem legenda nem meme no original → só espelha; nossa legenda embaixo
            ff_args = ["-vf", f"hflip,ass={ass_basename}"]
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
        titulo = base_name.replace("_", " ")
        try:
            from comum import db_bridge
            db_bridge.registrar_video_processado(
                final_output, deteccao=leg, versao=SISTEMA_VERSAO, titulo=titulo)
        except Exception as e:
            print(f"⚠️  espelho no banco falhou (ignorado): {e}")

        # Enfileira para postagem automática (com legenda gerada por IA)
        if POSTER_DISPONIVEL and os.path.exists(final_output):
            legenda_ia = gerar_legenda_ia(texto_transcricao, titulo_original="")
            adicionar_na_fila(final_output, titulo, caption=legenda_ia)
            try:
                from comum import db_bridge
                db_bridge.registrar_post_pendente(
                    final_output, caption=legenda_ia, versao=SISTEMA_VERSAO)
            except Exception:
                pass

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
