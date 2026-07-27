import os
import requests
import random
import re
import edge_tts
import gtts
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, AudioFileClip, ImageClip, ColorClip, concatenate_videoclips, CompositeAudioClip, vfx, afx
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np

# Pillow 10.0.0以降でのANTIALIASエラー対策
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.LANCZOS

def normalize_text_for_speech(text, language="ja"):
    """
    ナレーション用にテキストを最適化する。
    - アルファベットの誤読防止
    - 文末の句点補完および重複句読点の整理
    """
    if language == "ja":
        # 1. アルファベットの誤読防止
        text = text.replace("VS", "バーサス").replace("vs", "バーサス")
        text = text.replace("AI", "エーアイ")
        
        # 2. 文末に句点がない場合に補完（最後の間を空けるため）
        if not text.endswith(("。", "！", "？", ".", "!", "?")):
            text += "。"
            
        # 重複する句読点のクレンジング
        text = re.sub(r'、+', '、', text)
        text = re.sub(r'。+', '。', text)
        text = text.replace("、。", "。").replace("。、", "。")
        
    else:
        text = text.replace("VS", "versus").replace("vs", "versus")
    return text

def create_boxed_text_image(text, size=(1080, 1920), fontsize=60):
    """
    中央に2-3行の読みやすい字幕画像を生成。日本語・英語の両対応。
    """
    img = Image.new('RGBA', size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    if os.name == 'nt':
        font_path = "C:\\Windows\\Fonts\\meiryo.ttc"
    else:
        # Linux (Ubuntu) 環境向けのフォント候補（CJKおよび英字標準）
        font_candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        ]
        font_path = next((p for p in font_candidates if os.path.exists(p)), None)
    
    font = ImageFont.truetype(font_path, fontsize) if font_path and os.path.exists(font_path) else ImageFont.load_default()

    # 最大3行程度に収める
    max_width = 850
    
    # 日本語/中国語などの全角文字が含まれるか判定
    is_cjk = any(ord(char) > 0x2000 for char in text)
    
    if is_cjk:
        # 日本語などの文字単位での分割
        words = list(text.strip())
        join_char = ""
    else:
        # 英語などの単語単位での分割
        words = text.strip().split()
        join_char = " "
        
    lines = []
    current_line = ""
    
    for word in words:
        test_line = (current_line + join_char + word).strip() if current_line else word
        if draw.textbbox((0, 0), test_line, font=font)[2] > max_width and current_line:
            lines.append(current_line)
            current_line = word
        else:
            current_line = test_line
    if current_line:
        lines.append(current_line)
    
    # 描画位置の計算
    line_spacing = 30
    total_text_height = sum([draw.textbbox((0, 0), l, font=font)[3] - draw.textbbox((0, 0), l, font=font)[1] for l in lines]) + line_spacing * (len(lines) - 1)
    
    box_width = 950
    box_height = total_text_height + 120
    box_x = (size[0] - box_width) // 2
    box_y = (size[1] - box_height) // 2
    
    overlay = Image.new('RGBA', size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle([box_x, box_y, box_x + box_width, box_y + box_height], radius=40, fill=(0, 0, 0, 160))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)
    
    current_y = box_y + 60
    for line in lines:
        w = draw.textbbox((0, 0), line, font=font)[2]
        x = (size[0] - w) // 2
        draw.text((x, current_y), line, font=font, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0,0,0))
        current_y += draw.textbbox((0, 0), line, font=font)[3] - draw.textbbox((0, 0), line, font=font)[1] + line_spacing
        
    return img

async def generate_speech(text, output_path, voice="ja-JP-AoiNeural", rate="+8%"):
    """
    音声合成を行い、ファイルが正しく生成されたかチェックする。
    1. VOICEVOX (primary local, fallback if unavailable)
    2. Edge TTS (secondary)
    3. gTTS (tertiary fallback)
    4. No audio (final fallback, returns None)
    """
    lang = "ja" if "ja-JP" in voice else "en"
    clean_text = normalize_text_for_speech(text, language=lang)
    
    # 1. VOICEVOX (Local engine)
    if lang == "ja" and os.environ.get("GITHUB_ACTIONS") != "true":
        try:
            print(f"[TTS] Attempting VOICEVOX for text: '{clean_text[:20]}...'")
            speaker_id = 2
            query_res = requests.post(
                f"http://localhost:50021/audio_query",
                params={"text": clean_text, "speaker": speaker_id},
                timeout=3.0
            )
            if query_res.status_code == 200:
                query_data = query_res.json()
                query_data["speedScale"] = 1.1
                query_data["pitchScale"] = -0.03
                query_data["intonationScale"] = 1.1
                synth_res = requests.post(
                    f"http://localhost:50021/synthesis",
                    params={"speaker": speaker_id},
                    json=query_data,
                    timeout=10.0
                )
                if synth_res.status_code == 200:
                    with open(output_path, "wb") as f:
                        f.write(synth_res.content)
                    if os.path.exists(output_path) and os.path.getsize(output_path) >= 100:
                        print("[TTS] VOICEVOX succeeded.")
                        return output_path
        except Exception as e:
            print(f"[TTS_WARN] VOICEVOX failed/unavailable: {e}")

    # 2. Edge TTS
    try:
        print(f"[TTS] Attempting Edge TTS for text: '{clean_text[:20]}...'")
        communicate = edge_tts.Communicate(clean_text, voice, rate=rate)
        await communicate.save(output_path)
        if os.path.exists(output_path) and os.path.getsize(output_path) >= 100:
            print("[TTS] Edge TTS succeeded.")
            return output_path
    except Exception as e:
        print(f"[TTS_WARN] Edge TTS failed: {e}")
        
    # 2. gTTS
    try:
        print(f"[TTS] Attempting gTTS fallback...")
        tts = gtts.gTTS(text=clean_text, lang=lang)
        tts.save(output_path)
        if os.path.exists(output_path) and os.path.getsize(output_path) >= 100:
            print("[TTS] gTTS fallback succeeded.")
            return output_path
    except Exception as e:
        print(f"[TTS_WARN] gTTS fallback failed: {e}")

    # 3. No audio (Final fallback)
    print("[TTS_FAIL] Both Edge TTS and gTTS failed. Continuing in NO-AUDIO mode.")
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except:
            pass
    return None

async def fetch_best_visual(query, api_key, target_animal="dog", forbidden_animals=["cat"], work_dir="."):
    """
    対象動物と禁止キーワードを厳格に指定してPexelsから動画を検索する。
    """
    headers = {"Authorization": api_key}
    
    # 除外クエリの作成
    exclude = " ".join([f"-{a}" for a in forbidden_animals])
    
    # 検索クエリの構築
    base_queries = [
        f"{target_animal} {query}",
        target_animal,
        f"cute {target_animal}"
    ]

    queries = [f"{q} {exclude}".strip() for q in base_queries]
    print(f"[DEBUG] Pexels Strict Queries: {queries}")
    
    for q in queries:
        try:
            page = random.randint(1, 3)
            v_url = f"https://api.pexels.com/videos/search?query={q}&per_page=15&orientation=portrait&page={page}"
            res = requests.get(v_url, headers=headers)
            res.raise_for_status()
            v_data = res.json()
            if v_data.get('videos'):
                videos = v_data['videos']
                valid_videos = [v for v in videos if v.get('duration', 0) >= 12]
                target_video = random.choice(valid_videos) if valid_videos else random.choice(videos)
                valid_files = [f for f in target_video['video_files'] if f.get('width', 0) >= 720]
                best_file = valid_files[0] if valid_files else target_video['video_files'][0]
                path = os.path.join(work_dir, "temp_bg.mp4")
                with open(path, 'wb') as f: f.write(requests.get(best_file['link']).content)
                return path, "video"
        except Exception as e:
            print(f"[WARN] Pexels Search Error for '{q}': {e}")
            continue
    return None, None

async def assemble_video_professional(script, asset_path, asset_type, bgm_path, output_filename, voice="ja-JP-AoiNeural", topic="", work_dir="."):
    raw_sections = [s.strip() for s in re.split(r'(?<=[。！!？\?\n])', script) if s.strip()]
    if len(raw_sections) > 3:
        n = len(raw_sections)
        sections = [" ".join(raw_sections[:n//2]), " ".join(raw_sections[n//2:])]
    else:
        sections = raw_sections

    temp_dir = os.path.join(work_dir, "temp_audio")
    os.makedirs(temp_dir, exist_ok=True)
    
    audio_clips = []
    section_durations = []
    curr = 0
    any_audio_success = False
    
    for i, txt in enumerate(sections):
        a_path = os.path.join(temp_dir, f"s_{i}.mp3")
        success = await generate_speech(txt, a_path, voice=voice)
        if success and os.path.exists(a_path) and os.path.getsize(a_path) >= 100:
            try:
                clip = AudioFileClip(a_path)
                audio_clips.append(clip.set_start(curr))
                section_durations.append(clip.duration)
                curr += clip.duration
                any_audio_success = True
            except Exception as e:
                print(f"[AUDIO_WARN] Failed to load generated audio clip: {e}")
                section_durations.append(5.0)
                curr += 5.0
        else:
            # Default duration for no-audio fallback per section
            section_durations.append(5.0)
            curr += 5.0
    
    duration = min(curr if curr > 0 else 15.0, 15.0)
    final_audio_content = CompositeAudioClip(audio_clips) if (any_audio_success and audio_clips) else None
    
    if asset_type == "video" and asset_path:
        clip = VideoFileClip(asset_path).without_audio()
        
        # 1. 1080x1920 (9:16) に完全に一致するアスペクト比で中央切り抜き（クロップ）
        target_ratio = 1080 / 1920
        clip_ratio = clip.w / clip.h
        
        if clip_ratio > target_ratio:
            new_w = int(clip.h * target_ratio)
            bg_cropped = clip.crop(x_center=clip.w/2, y_center=clip.h/2, width=new_w, height=clip.h)
        else:
            new_h = int(clip.w / target_ratio)
            bg_cropped = clip.crop(x_center=clip.w/2, y_center=clip.h/2, width=clip.w, height=new_h)
            
        bg = bg_cropped.resize(newsize=(1080, 1920)).fx(vfx.mirror_x)
        bg = bg.fx(vfx.loop, duration=duration) if bg.duration < duration else bg.subclip(0, duration)
    else:
        bg = ColorClip(size=(1080, 1920), color=(30, 30, 30)).set_duration(duration)

    subs = []
    t_curr = 0
    for i, txt in enumerate(sections):
        dur = section_durations[i]
        if t_curr + dur > duration:
            dur = duration - t_curr
        if dur <= 0: break
        
        img = create_boxed_text_image(txt)
        img_p = os.path.join(temp_dir, f"t_{i}.png")
        img.save(img_p)
        subs.append(ImageClip(img_p).set_start(t_curr).set_duration(dur))
        t_curr += dur

    final_audio = final_audio_content
    if bgm_path and os.path.exists(bgm_path):
        try:
            bgm = AudioFileClip(bgm_path).volumex(0.15).fx(afx.audio_loop, duration=duration)
            if final_audio:
                final_audio = CompositeAudioClip([final_audio_content.volumex(1.0), bgm])
            else:
                final_audio = bgm
        except Exception as e:
            print(f"BGM loading failed: {e}")

    try:
        if final_audio:
            video = CompositeVideoClip([bg] + subs).set_audio(final_audio).set_duration(duration)
            video.write_videofile(output_filename, fps=30, codec="libx264", audio_codec="aac", audio_fps=44100, audio_bitrate="192k", ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "faststart"])
        else:
            video = CompositeVideoClip([bg] + subs).set_duration(duration)
            video.write_videofile(output_filename, fps=30, codec="libx264", audio=False, ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "faststart"])
        
        video.close()
        if asset_type == "video":
            bg.close()
        for s in subs:
            s.close()
        if final_audio:
            final_audio.close()
        for a in audio_clips:
            a.close()
            
        return output_filename, True
    except Exception as e:
        print(f"Video assembly failed: {e}")
        return None, False
