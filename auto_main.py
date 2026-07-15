import os
import sys
import asyncio
import random
import json
import datetime
import re
import time
import traceback
import difflib
import google.generativeai as genai
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# Windowsでの文字化け・エンコードエラー対策
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

from main import get_authenticated_service, check_youtube_channel, upload_to_youtube, SCOPES_TASKS, SCOPES_YOUTUBE, load_config
import ai_generator
import generate_video

try:
    import auditor
except ImportError:
    auditor = None

def strip_emojis(text):
    """
    動画合成時の文字化け(□)を防ぐため、絵文字や特殊記号を徹底的に除去する。
    日本語文字は維持する。
    """
    # ASCII + 日本語（ひらがな、カタカナ、漢字、句読点）
    pattern = r'[^\x00-\x7F\u3000-\u303F\u3040-\u309F\u30A0-\u30FF\uFF00-\uFFEF\u4E00-\u9FAF\n]+'
    return re.sub(pattern, ' ', text).strip()

def check_similarity(new_title, past_titles, threshold=0.7): return any(difflib.SequenceMatcher(None, new_title, pt).ratio() >= threshold for pt in past_titles)

async def run_auto_post(work_dir=".", topic=None):
    """
    15秒構成のシンプルな自動投稿プロセス。
    AI監査員とのループ機能を搭載。
    """
    # 1. 認証と設定の読み込み
    config_path = os.path.join(work_dir, 'config.json')
    if not os.path.exists(config_path):
        print(f"FATAL: config not found: {config_path}")
        sys.exit(1)

    with open(config_path, 'r', encoding='utf-8-sig') as f:
        config = json.load(f)
    
    profile_key = list(config.keys())[0]
    p = config[profile_key]
    
    print(f"PROFILE: {profile_key}")
    print(f"CHANNEL_ID: {p['channel_id']}")
    print(f"PROFILE_NAME: {p['profile_name']}")
    
    # チャンネル固有のテーマ設定
    if not topic:
        gemini_key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get(f"GEMINI_API_KEY_{profile_key.upper()}")
            or p.get('gemini_api_key')
        )
        if not gemini_key:
            raise Exception("FATAL: No Gemini API key available for topic generation")
        
        service_account_str = os.environ.get("GEMINI_SERVICE_ACCOUNT")
        credentials = None
        if service_account_str:
            import json
            from google.oauth2 import service_account
            try:
                info = json.loads(service_account_str)
                credentials = service_account.Credentials.from_service_account_info(info)
            except Exception:
                if os.path.exists(service_account_str):
                    try:
                        credentials = service_account.Credentials.from_service_account_file(service_account_str)
                    except Exception:
                        pass
        if credentials:
            genai.configure(credentials=credentials)
        else:
            genai.configure(api_key=gemini_key)

        model = genai.GenerativeModel('gemini-2.5-flash')
        prompt = "猫に関する、まだ誰も見たことがないような新しいサブトピック（例：特定の行動の心理、不思議な身体能力など）を日本語で1件だけ出力してください。前置きや解説、マークダウン記号などは一切含めず、サブトピック名のみを出力してください。"
        response = model.generate_content(prompt)
        topic = response.text.strip().replace('"', '').replace("'", "")
        
    # 言語の判定と音声モデルの厳格割り当て
    language = "ja" if "_jp" in profile_key else "en"
    # 日本向けは Aoi, 海外向けは高品質な Ava を使用
    voice_model = p.get("voice") or ("ja-JP-AoiNeural" if language == "ja" else "en-US-AvaNeural")
    
    print(f"=== AUTO POST START: {p['profile_name']} (topic: {topic}, lang: {language}, voice: {voice_model}) ===")
    
    try:
        # 1. 認証
        youtube_token = os.path.join(work_dir, "tokens", "youtube.pickle")
        env_token_key = f"YOUTUBE_TOKEN_{profile_key.upper()}_B64"
        os.makedirs(os.path.join(work_dir, "tokens"), exist_ok=True)
        
        youtube_service = get_authenticated_service(
            'youtube', 'v3', SCOPES_YOUTUBE, 
            token_path=youtube_token, 
            env_token_key=env_token_key, 
            profile_key=profile_key,
            work_dir=work_dir
        )
        
        expected_channel_id = p['channel_id']
        if not check_youtube_channel(youtube_service, expected_channel_id):
            raise Exception(f"Channel ID mismatch for {expected_channel_id}")

        # 2. 台本生成（AIエージェント・ループ）
        print("STEP: Script generation starting...")
        
        # APIキー取得（フォールバック付き）
        gemini_key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get(f"GEMINI_API_KEY_{profile_key.upper()}")
            or p.get('gemini_api_key')
        )
        print(f"GEMINI_KEY exists: {bool(gemini_key)}")
        if not gemini_key:
            raise Exception("FATAL: No Gemini API key available")
        
        title, script_content, search_query = "", "", ""
        current_feedback = None
        max_attempts = 2
        
        channel_auditor_path = os.path.join(work_dir, "auditor.py")
        has_auditor = os.path.exists(channel_auditor_path)
        print(f"AUDITOR exists: {has_auditor}")
        
        final_valid_content = False
        
        # チャンネルの文脈（ターゲット動物など）を構築
        target_animal = p.get('target_animal', 'pets')
        forbidden = ", ".join(p.get('forbidden_animals', []))
        history_file = os.path.join(work_dir, "script_history.txt")
        past_titles = []
        past_topics = []
        if os.path.exists(history_file):
            try:
                with open(history_file, "r", encoding="utf-8") as f:
                    for line in f.readlines()[-20:]:
                        line_str = line.strip()
                        if not line_str:
                            continue
                        if "|" in line_str:
                            parts = line_str.split("|", 1)
                            past_titles.append(parts[0].strip())
                            past_topics.append(parts[1].strip())
                        else:
                            past_titles.append(line_str)
            except Exception:
                pass
        past_topics = list(dict.fromkeys(past_topics)) # 重複排除
        history_context = ""
        if past_titles:
            history_context += " 過去に作成した以下の動画タイトルと類似する内容は絶対に避けてください: " + ", ".join(past_titles) + "."
        if past_topics:
            history_context += " また、過去に使用した以下のテーマ（トピック）に関する事実や構成、視点は絶対に避け、全く異なる新規の切り口で作成してください: " + ", ".join(past_topics) + "."
        channel_context = f"This channel is dedicated to {target_animal}. DO NOT mention: {forbidden}.{history_context}"
        
        for attempt in range(1, max_attempts + 1):
            print(f"ATTEMPT {attempt}/{max_attempts}: Generating script...")
            title, script_content, search_query = ai_generator.generate_viral_script(
                topic, channel_context=channel_context, api_key=gemini_key, feedback=current_feedback, language=language
            )
            if check_similarity(title, past_titles):
                continue
            if topic in past_topics:
                continue
            
            # 【重要】Gemini失敗時のプレースホルダー漏出を検知して即座に停止
            if "Short dog insight" in script_content or not script_content:
                print("FATAL: AI generated fallback English script. Aborting to prevent bad post.")
                sys.exit(1)
            
            # 文字化け対策
            title = strip_emojis(title)
            script_content = strip_emojis(script_content)
            try:
                with open(history_file, "a", encoding="utf-8") as f:
                    f.write(f"{title}|{topic}\n")
            except Exception:
                pass
            
            print(f"TITLE: {title}")
            print(f"SCRIPT: {script_content[:100]}...")
            print(f"SEARCH_QUERY: {search_query}")

            # 2.1 物理的な長さチェック
            print("STEP: Audio duration check...")
            temp_audio_path = os.path.join(work_dir, "temp_audio_check.mp3")
            try:
                audio_result = await generate_video.generate_speech(script_content, temp_audio_path, voice=voice_model, rate="+15%")
                if audio_result and os.path.exists(temp_audio_path) and os.path.getsize(temp_audio_path) >= 100:
                    from moviepy.editor import AudioFileClip
                    a_clip = AudioFileClip(temp_audio_path)
                    audio_dur = a_clip.duration
                    a_clip.close()
                    print(f"AUDIO_DURATION: {audio_dur:.2f}s")
                else:
                    # Fallback estimated duration based on character count (approx 6 characters per second)
                    char_count = len(script_content)
                    audio_dur = max(char_count / 6.0, 5.0)
                    print(f"AUDIO_GENERATION_FAILED (Returned None). Using estimated duration: {audio_dur:.2f}s")
                
                if audio_dur > 15.0:
                    current_feedback = f"THE SCRIPT IS TOO LONG ({audio_dur:.1f}s). Please shorten it to be under 14 seconds. Current text: {script_content}"
                    print(f"FAIL: Too long ({audio_dur:.1f}s). Retrying.")
                    continue
                
                # 長さがOKなら監査員へ
                if has_auditor:
                    print("STEP: Auditor check...")
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("channel_auditor", channel_auditor_path)
                    channel_auditor = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(channel_auditor)
                    
                    is_pass, feedback = channel_auditor.audit_dog_content(
                        title, script_content, search_query, api_key=gemini_key
                    )
                    
                    if is_pass:
                        print("AUDITOR: PASS")
                        final_valid_content = True
                        break
                    elif feedback == "CRITICAL_SAFETY_ABORT":
                        print("AUDITOR: CRITICAL_SAFETY_ABORT")
                        sys.exit(1)
                    else:
                        print(f"AUDITOR: FAIL - {feedback}")
                        current_feedback = feedback
                else:
                    final_valid_content = True
                    break
            except Exception as e:
                print(f"DURATION_CHECK_ERROR: {e}")
                traceback.print_exc()
                current_feedback = "Error checking duration. Please try again with a simpler script."

        if not final_valid_content:
            print("FATAL: Could not generate valid script in max attempts.")
            sys.exit(1)

        # 3. 素材取得
        print(f"STEP: Fetching visual for '{search_query}'")
        pexels_key = (
            os.environ.get("PEXELS_API_KEY")
            or os.environ.get(f"PEXELS_API_KEY_{profile_key.upper()}")
            or p.get('pexels_api_key')
        )
        print(f"PEXELS_KEY exists: {bool(pexels_key)}")
        if not pexels_key:
            raise Exception("FATAL: No Pexels API key available")
        
        asset_path, asset_type = await generate_video.fetch_best_visual(
            search_query, 
            pexels_key, 
            target_animal=target_animal, 
            forbidden_animals=p.get('forbidden_animals', []), 
            work_dir=work_dir
        )
        print(f"ASSET: path={asset_path}, type={asset_type}")
        
        if not asset_path:
            raise Exception("FATAL: No visual asset found")
        
        # 4. 動画合成
        print("STEP: Video assembly (15s)...")
        video_output_path = os.path.join(work_dir, "youtube_short.mp4")
        
        # BGMの存在確認とフォールバック
        bgm_path = p.get('bgm', 'bgm.mp3')
        if not os.path.exists(bgm_path):
            work_bgm = os.path.join(work_dir, bgm_path)
            if os.path.exists(work_bgm):
                bgm_path = work_bgm
            elif os.path.exists("bgm.mp3"):
                print(f"BGM_FALLBACK: Using root bgm.mp3")
                bgm_path = "bgm.mp3"
            else:
                print(f"BGM_MISSING: No BGM file found")
                bgm_path = None

        video_file, success = await generate_video.assemble_video_professional(
            script_content, 
            asset_path,
            asset_type,
            bgm_path, 
            video_output_path,
            voice=voice_model,
            topic=profile_key,
            work_dir=work_dir
        )
        
        if not success or not video_file:
            raise Exception("FATAL: Video generation failed")
        
        if not os.path.exists(video_file):
            raise Exception(f"FATAL: Video file does not exist: {video_file}")
        
        video_size = os.path.getsize(video_file)
        print(f"VIDEO_FILE: {video_file} ({video_size} bytes)")
        
        if video_size < 1000:
            raise Exception(f"FATAL: Video file too small: {video_size} bytes")

        # 5. YouTubeアップロード + 検証
        print("STEP: YouTube upload starting...")
        print(f"UPLOAD_TITLE: {title}")
        print(f"UPLOAD_CHANNEL: {expected_channel_id}")
        
        full_description = f"{script_content}\n\n{p['tags']}"
            
        body = {
            'snippet': {
                'title': title,
                'description': full_description,
                'tags': ['Shorts'] + p['tags'].replace('#', '').split(),
                'categoryId': '22'
            },
            'status': {
                'privacyStatus': 'public',
                'selfDeclaredMadeForKids': False
            }
        }
        
        media = MediaFileUpload(video_file, chunksize=-1, resumable=True, mimetype='video/mp4')
        request = youtube_service.videos().insert(part=','.join(body.keys()), body=body, media_body=media)
        
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"UPLOAD_PROGRESS: {int(status.progress() * 100)}%")
        
        print(f"UPLOAD_RESPONSE: {json.dumps(response, ensure_ascii=False, default=str)}")
        
        video_id = response.get("id")
        if not video_id:
            print(f"UPLOAD_RESPONSE_FULL: {response}")
            raise Exception("FATAL: video_id missing from upload response")
        
        print(f"VIDEO_ID: {video_id}")
        
        # 6. アップロード検証 - videos().list() で実在確認
        print("STEP: Upload verification...")
        import time
        time.sleep(3)  # YouTube側の処理待ち
        
        verify = youtube_service.videos().list(
            part="status,snippet",
            id=video_id
        ).execute()
        
        print(f"VERIFY_RESPONSE: {json.dumps(verify, ensure_ascii=False, default=str)}")
        
        items = verify.get("items", [])
        if not items:
            raise Exception(f"FATAL: Uploaded video {video_id} not found via videos().list()")
        
        video = items[0]
        actual_channel = video["snippet"]["channelId"]
        upload_status = video["status"].get("uploadStatus")
        
        print(f"VERIFY_CHANNEL: {actual_channel}")
        print(f"VERIFY_UPLOAD_STATUS: {upload_status}")
        
        if actual_channel != expected_channel_id:
            raise Exception(f"FATAL: Channel mismatch. Expected={expected_channel_id}, Actual={actual_channel}")
        
        if upload_status not in ("uploaded", "processed"):
            raise Exception(f"FATAL: Upload status invalid: {upload_status}")
        
        print(f"UPLOAD_SUCCESS: {video_id}")
        print(f"URL: https://www.youtube.com/shorts/{video_id}")

    except HttpError as e:
        print(f"HTTP_ERROR: status={e.resp.status}")
        print(f"HTTP_ERROR_CONTENT: {e.content.decode('utf-8') if hasattr(e, 'content') else str(e)}")
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"FATAL_ERROR: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    w_dir = sys.argv[1] if len(sys.argv) > 1 else '.'
    t_key = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(run_auto_post(w_dir, t_key))
