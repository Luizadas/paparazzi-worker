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


def detectar_faixa_legenda(video_path, n=18):
    """
    Detecta a faixa vertical onde está a legenda queimada do vídeo ORIGINAL
    (meio ou embaixo), analisando os frames. A legenda muda de texto ao longo
    do tempo (alta variância temporal) enquanto o cenário/equipamentos são
    estáticos — por isso usamos energia_de_borda * variância_temporal.
    Retorna (centro_frac, altura_frac). Fallback (0.5, 0.11) se falhar.
    """
    tmp = tempfile.mkdtemp()
    try:
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path], capture_output=True, text=True).stdout.strip())
        frames = []
        for i in range(n):
            t = dur * (i + 0.5) / n
            out = os.path.join(tmp, f"f{i}.png")
            subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", video_path,
                            "-frames:v", "1", "-vf", "scale=240:-1", out, "-loglevel", "error"],
                           check=False)
            if os.path.exists(out):
                frames.append(np.asarray(Image.open(out).convert("L"), dtype=np.float32))
        if len(frames) < 3:
            return 0.5, 0.11
        fr = np.array(frames)                                  # [n, H, W]
        H = fr.shape[1]
        edge = np.abs(np.diff(fr, axis=2)).sum(axis=2)         # bordas verticais por linha
        score = edge.mean(axis=0) * fr.std(axis=0).mean(axis=1)  # borda * variância temporal
        k = max(3, H // 60)
        score = np.convolve(score, np.ones(k) / k, mode="same")
        y0, y1 = int(0.28 * H), int(0.93 * H)                 # ignora topo (logo) e base (UI)
        reg = score.copy(); reg[:y0] = 0; reg[y1:] = 0
        pico = int(np.argmax(reg))
        lim = 0.45 * reg[pico]
        a = pico
        while a > y0 and reg[a] > lim:
            a -= 1
        b = pico
        while b < y1 and reg[b] > lim:
            b += 1
        centro = (a + b) / 2.0 / H
        altura = min(max((b - a) / H, 0.10), 0.22)
        return round(centro, 3), round(altura, 3)
    except Exception as e:
        print(f"⚠️ Detecção da legenda falhou ({e}); usando o meio como padrão.")
        return 0.5, 0.11
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fmt_ass_ts(s):
    h = int(s // 3600); s -= h * 3600
    m = int(s // 60); s -= m * 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def gerar_ass_legenda(segments, ass_path, W, H, centro_y_px, max_chars=26):
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

    fs = int(H * 0.028)
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
    return len(cues)


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

    # Passo 2: Transcrever (com timestamps por palavra) e gerar o ASS de 1 linha
    print("Passo 2/3: Gerando legendas com Whisper (Nativo)...")
    ass_path = os.path.join(base_dir, f"{base_name}.ass")
    texto_transcricao = ""
    try:
        model = whisper.load_model("base")
        result = model.transcribe(audio_path, fp16=False, word_timestamps=True)
        texto_transcricao = (result.get("text") or "").strip()  # base para a legenda IA

        # Detecta ONDE está a legenda do vídeo original (meio ou embaixo)
        W, H = _dimensoes_video(video_path)
        centro_frac, altura_frac = detectar_faixa_legenda(video_path)
        centro_y = int(centro_frac * H)
        tarja_h = max(int(altura_frac * H), int(H * 0.09))
        print(f"🎯 Legenda original detectada em ~{int(centro_frac*100)}% da altura "
              f"(tarja de {tarja_h}px).")

        n_cues = gerar_ass_legenda(result["segments"], ass_path, W, H, centro_y)
        print(f"📝 {n_cues} legendas de 1 linha geradas.")
    except Exception as e:
        print(f"❌ Erro ao rodar whisper internamente: {e}")
        return

    if not os.path.exists(ass_path):
        print("❌ Arquivo de legenda não foi gerado.")
        return

    # Passo 3: Espelhar o vídeo, cobrir a legenda original e queimar a nova
    print("Passo 3/3: Espelhando o vídeo e adicionando legendas...")
    try:
        ass_basename = os.path.basename(ass_path)
        video_basename = os.path.basename(video_path)
        final_basename = os.path.basename(final_output)

        # hflip espelha o vídeo; a TARJA PRETA de largura total cobre 100% a
        # legenda original (agora espelhada) exatamente na altura detectada; e o
        # ASS desenha a legenda branca de 1 linha centralizada sobre a tarja.
        y_tarja = max(0, centro_y - tarja_h // 2)
        tarja = f"drawbox=x=0:y={y_tarja}:w=iw:h={tarja_h}:color=black:t=fill"
        vf_filter = f"hflip,{tarja},ass={ass_basename}"

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
