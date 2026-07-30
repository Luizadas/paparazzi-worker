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
import whisper
import numpy as np
from PIL import Image
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

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

    prompt = f"""Você é um especialista em crescimento e viralização no TikTok no Brasil,
com anos escrevendo legendas que geram milhões de views.

Abaixo está a transcrição automática (Whisper) de um vídeo curto. Ela pode estar
IMPERFEITA, com trechos soltos, repetições ou erros. Sua tarefa NÃO é resumir nem
copiar a transcrição: é ENTENDER o tema/assunto geral e escrever uma legenda NOVA,
coerente e chamativa para o post.

Aplique as melhores práticas de viralização no TikTok:
- GANCHO forte no início: curiosidade, choque, polêmica ou identificação imediata.
- CURIOSITY GAP: insinue algo sem entregar tudo, pra prender até o fim.
- Gatilhos emocionais (surpresa, humor, indignação, inspiração) e linguagem coloquial BR.
- CTA sutil quando fizer sentido ("marca alguém", "concorda?", "comenta aí").
- Termine com 4 a 6 HASHTAGS de alto alcance (misture amplas como #fyp #viral #foryou
  com específicas do tema).

Regras de saída (OBRIGATÓRIAS):
- É PROIBIDO copiar ou parafrasear frases soltas da transcrição. Escreva algo próprio,
  fluido e que faça sentido sozinho. Se a transcrição estiver confusa, capte só o TEMA
  geral (ex: treino, futebol, humor, entrevista) e escreva sobre ele.
- Escreva EXCLUSIVAMENTE em PORTUGUÊS DO BRASIL, com frases completas e coerentes.
- NÃO use caracteres chineses, japoneses, coreanos, russos ou de outro idioma.
- Legenda curta e escaneável (idealmente até 150 caracteres antes das hashtags).
- Pode usar 1 ou 2 emojis se combinar.
- Responda APENAS com a legenda final (texto + hashtags) em UMA linha. Sem aspas,
  sem títulos, sem explicações, sem "Legenda:".
{contexto_titulo}
--- TRANSCRIÇÃO (pode estar imperfeita) ---
{texto[:2500]}
"""

    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 200},
    }
    try:
        print("🧠 Gerando legenda com IA (LLM)...")
        resp = requests.post(LLM_API_URL, json=payload, timeout=180)
        resp.raise_for_status()
        saida = resp.json().get("response", "") or ""

        legenda = _sanitizar_legenda(saida)
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

        caixas = []  # (x0,x1,y0,y1,yc,idx_frame) em fração
        for idx, p in enumerate(paths):
            img = np.asarray(Image.open(p).convert("RGB"))
            for box, txt, conf in reader.readtext(img):
                if conf < 0.35 or len(txt.strip()) < 2:
                    continue
                xs = [q[0] for q in box]; ys = [q[1] for q in box]
                yc = (min(ys) + max(ys)) / 2 / H
                if yc < 0.30 or yc > 0.92:          # ignora topo (logo) e base (UI)
                    continue
                caixas.append((min(xs)/W, max(xs)/W, min(ys)/H, max(ys)/H, yc, idx))

        if len(caixas) < 3:
            return {"tem_legenda": False}

        # Agrupa por y-centro: a banda com MAIS caixas é a legenda (±8%)
        melhor = []
        for c in caixas:
            grupo = [b for b in caixas if abs(b[4] - c[4]) < 0.08]
            if len(grupo) > len(melhor):
                melhor = grupo

        frames_distintos = len(set(b[5] for b in melhor))
        if len(melhor) < 3 or frames_distintos < max(2, int(0.25 * len(paths))):
            return {"tem_legenda": False}    # transitório/ruído → sem legenda persistente

        # Caixa-união (percentis no X p/ ignorar outliers; min/max no Y) + margem
        x0 = float(np.percentile([b[0] for b in melhor], 5))
        x1 = float(np.percentile([b[1] for b in melhor], 95))
        y0 = min(b[2] for b in melhor)
        y1 = max(b[3] for b in melhor)
        x0 = max(0.0, x0 - 0.02); x1 = min(1.0, x1 + 0.02)
        y0 = max(0.0, y0 - 0.015); y1 = min(1.0, y1 + 0.015)
        cy = (y0 + y1) / 2

        # Sempre BLUR (sem tarja): só devolvemos a caixa da legenda original.
        return {"tem_legenda": True,
                "x0": round(x0, 4), "x1": round(x1, 4),
                "y0": round(y0, 4), "y1": round(y1, 4),
                "cy": round(cy, 4)}
    except Exception as e:
        print(f"⚠️ Detecção OCR falhou ({e}); legenda irá para baixo, sem cobertura.")
        return {"tem_legenda": False}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fmt_ass_ts(s):
    h = int(s // 3600); s -= h * 3600
    m = int(s // 60); s -= m * 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def gerar_ass_legenda(segments, ass_path, W, H, centro_y_px, max_chars=18):
    """
    Gera um arquivo .ASS com legendas de UMA linha (agrupa palavras até max_chars),
    texto branco em negrito com contorno, posicionado exatamente no centro vertical
    detectado (\\pos), para ficar sobre a tarja preta.
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

def processar_video(video_path):
    print(f"\n⚙️ Iniciando processamento de: {video_path}")
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

    # Passo 2: Gerar Legendas (SRT) usando o CLI do Whisper
    print("Passo 2/3: Gerando legendas com Whisper...")
    try:
        # Chama o whisper localmente pedindo formato SRT
        subprocess.run([
            "whisper", audio_path, "--model", "base", "--output_format", "srt", "--output_dir", base_dir
        ], check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Erro ao rodar whisper: {e}")
        return
    except FileNotFoundError:
        print("❌ Whisper não encontrado no PATH. Certifique-se de que está instalado.")
        return

    # Verificar se o srt foi gerado. O whisper pode salvar como .wav.srt dependendo de como é chamado, ou com o mesmo nome do áudio.
    whisper_srt_output = os.path.join(base_dir, f"{base_name}_audio.srt")
    if os.path.exists(whisper_srt_output):
        os.rename(whisper_srt_output, srt_path)
    elif not os.path.exists(srt_path):
        print("❌ Arquivo de legenda não encontrado após a execução do Whisper.")
        return

    # Extrai o texto do SRT para servir de base à legenda gerada por IA
    texto_transcricao = ""
    try:
        with open(srt_path, encoding="utf-8") as f:
            linhas = [l.strip() for l in f
                      if l.strip() and not l.strip().isdigit() and "-->" not in l]
        texto_transcricao = " ".join(linhas)
    except Exception:
        pass

    # Passo 3: Espelhar o vídeo e queimar as legendas
    print("Passo 3/3: Espelhando o vídeo e adicionando legendas...")
    
    # É preciso formatar o caminho do SRT para o FFmpeg (escapar caminhos absolutos no windows/linux)
    # FFmpeg subtitles filter tem uma sintaxe chata para caminhos absolutos, então vamos rodar no mesmo diretório
    try:
        srt_basename = os.path.basename(srt_path)
        video_basename = os.path.basename(video_path)
        final_basename = os.path.basename(final_output)

        # Filtro: hflip para espelhar, subtitles para legendar. 
        # Configuração básica de fonte para ficar visível (amarelo com borda)
        # Legenda: texto branco, menor, com FAIXA PRETA atrás (BorderStyle=3),
        # centralizada no MEIO do vídeo (Alignment=5) para cobrir a legenda
        # original espelhada. Cores em ASS: &HAABBGGRR (AA=00 => opaco).
        estilo_legenda = (
            "FontName=Arial,FontSize=14,Bold=1,"
            "PrimaryColour=&H00FFFFFF,"      # texto branco
            "OutlineColour=&H00000000,"      # faixa preta (BorderStyle=3)
            "BorderStyle=3,Outline=4,Shadow=0,"
            "Alignment=10"
        )
        # Tarja preta de largura total na altura da legenda original (que fica
        # espelhada pelo hflip), cobrindo-a 100%; a legenda branca fica por cima.
        tarja = "drawbox=x=0:y=ih*0.43:w=iw:h=ih*0.14:color=black:t=fill"
        vf_filter = f"hflip,{tarja},subtitles={srt_basename}:force_style='{estilo_legenda}'"

        subprocess.run([
            "ffmpeg", "-i", video_basename, "-vf", vf_filter, 
            "-c:a", "copy", final_basename, "-y"
        ], cwd=base_dir, check=True, capture_output=True)
        print(f"✅ Processamento concluído! Vídeo final salvo em: {final_output}")

        # Enfileira para postagem automática (com legenda gerada por IA)
        if POSTER_DISPONIVEL and os.path.exists(final_output):
            titulo = base_name.replace("_", " ")
            legenda_ia = gerar_legenda_ia(texto_transcricao, titulo_original="")
            adicionar_na_fila(final_output, titulo, caption=legenda_ia)

    except subprocess.CalledProcessError as e:
        print(f"❌ Erro ao processar FFmpeg final: {e.stderr.decode('utf-8', errors='ignore')}")

    # Limpeza
    for temp_file in [audio_path, srt_path, video_path]:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass


def processar_video_whisper_nativo(video_path):
    print(f"\n⚙️ Iniciando processamento de: {video_path}")
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
    print("Passo 2/3: Gerando legendas com Whisper (Nativo)...")
    ass_path = os.path.join(base_dir, f"{base_name}.ass")
    texto_transcricao = ""
    leg = {"tem_legenda": False}
    try:
        model = whisper.load_model("base")
        result = model.transcribe(audio_path, fp16=False, word_timestamps=True)
        texto_transcricao = (result.get("text") or "").strip()  # base para a legenda IA

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

        n_cues, max_chars, fs = gerar_ass_legenda(result["segments"], ass_path, W, H, centro_y)
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

        if leg.get("tem_legenda"):
            # SEMPRE blur. A caixa do blur = união da legenda ORIGINAL (OCR) com a
            # caixa da NOSSA legenda (centralizada), pra que a nossa fique
            # ESTRITAMENTE em cima do blur. Blur forte.
            ox0 = int(leg["x0"] * W); ox1 = int(leg["x1"] * W)
            oy0 = int(leg["y0"] * H); oy1 = int(leg["y1"] * H)
            # caixa estimada da nossa legenda (centralizada em W/2)
            char_w = 0.55 * fs
            sub_w = min(int(W * 0.94), int(max_chars * char_w) + int(0.05 * W))
            sub_h = int(fs * 1.7)
            sx = (W - sub_w) // 2
            sy = centro_y - sub_h // 2
            # união + margem
            m = int(0.01 * W)
            bx = max(0, min(ox0, sx) - m)
            bx1 = min(W, max(ox1, sx + sub_w) + m)
            by = max(0, min(oy0, sy) - m)
            by1 = min(H, max(oy1, sy + sub_h) + m)
            bw = max(1, bx1 - bx); bh = max(1, by1 - by)
            # blur FORTE via gblur (Gaussian): sigma alto, sem limite de raio
            sigma = max(16, min(bw, bh) // 5)
            fc = (f"[0:v]hflip,split=2[b][t];"
                  f"[t]crop={bw}:{bh}:{bx}:{by},gblur=sigma={sigma}[bl];"
                  f"[b][bl]overlay={bx}:{by}[base];"
                  f"[base]ass={ass_basename}")
            ff_args = ["-filter_complex", fc]
            print(f"🎬 Cobertura: BLUR forte gblur (x={bx},y={by},w={bw},h={bh},sigma={sigma}).")
        else:
            # Sem legenda no original → só espelha; a nossa legenda vai embaixo
            ff_args = ["-vf", f"hflip,ass={ass_basename}"]
            print("🎬 Sem cobertura (legenda embaixo).")

        subprocess.run(
            ["ffmpeg", "-i", video_basename, *ff_args, "-c:a", "copy", final_basename, "-y"],
            cwd=base_dir, check=True, capture_output=True)
        print(f"✅ Processamento concluído! Vídeo final salvo em: {final_output}")

        # Enfileira para postagem automática (com legenda gerada por IA)
        if POSTER_DISPONIVEL and os.path.exists(final_output):
            titulo = base_name.replace("_", " ")
            legenda_ia = gerar_legenda_ia(texto_transcricao, titulo_original="")
            adicionar_na_fila(final_output, titulo, caption=legenda_ia)

    except subprocess.CalledProcessError as e:
        print(f"❌ Erro ao processar FFmpeg final: {e.stderr.decode('utf-8', errors='ignore')}")

    # Limpeza
    for temp_file in [audio_path, ass_path, video_path]:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass


if __name__ == "__main__":
    if len(sys.argv) > 1:
        video_url = sys.argv[1]
        caminho_baixado = baixar_short(video_url)
        if caminho_baixado:
            # Mantendo a chamada original ou a nova
            # processar_video(caminho_baixado) # original via CLI
            processar_video_whisper_nativo(caminho_baixado) # nova via biblioteca nativa
    else:
        print("ERRO: Nenhuma URL de vídeo fornecida.")
        print("Uso: python coletor.py 'URL_DO_SHORT'")
