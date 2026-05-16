import asyncio
import auto_main
import generate_video
import ai_generator
import re
import random
import glob

async def test_render():
    profile_key = "baby_en"
    config = auto_main.load_config()
    p = config[profile_key]
    
    topics = ["baby and golden retriever", "baby and husky", "baby and gentle cat", "baby and puppy"]
    topic = random.choice(topics)
    print(f"=== ローカルテスト開始: {p['profile_name']} (テーマ: {topic}) ===")
    
    title, script_content = ai_generator.generate_viral_script(topic, channel_context="")
    print(f"Title generated: {title}")
    # 文字コードエラー防止のため、スクリプト内容の表示を簡略化
    print("Script content generated successfully.")
    
    mp3_files = glob.glob("*.mp3")
    bgm_path = random.choice(mp3_files) if mp3_files else "bgm.mp3"
    
    forced_keywords = {"baby_en": "baby and puppy"}
    pexels_query = forced_keywords["baby_en"]
    
    keyword_match = re.search(r'PexelsKeyword:\s*(.*)', script_content)
    if keyword_match:
        extracted_query = keyword_match.group(1).strip()
        required_words = ["baby"]
        if any(w.lower() in extracted_query.lower() for w in required_words):
            pexels_query = extracted_query
            print(f"AIキーワードを採用: {pexels_query}")
        else:
            print(f"AIキーワード '{extracted_query}' は主題と不一致のため、デフォルト '{pexels_query}' を使用")

    print(f"Pexels検索クエリ: {pexels_query}")
    
    video_file = await generate_video.make_short_video(
        script_content, 
        'bg.jpg', 
        bgm_path, 
        "test_baby.mp4",
        voice=p['voice'],
        pexels_key=p.get('pexels_api_key'),
        topic=profile_key,
        pexels_query=pexels_query
    )
    print(f"\n✅ テスト動画の生成が完了しました！\nファイル名: {video_file} を確認してください。")

if __name__ == "__main__":
    asyncio.run(test_render())
