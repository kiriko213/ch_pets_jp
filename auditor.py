import os
import google.generativeai as genai
import re
import json

def audit_dog_content(title, content, keyword, api_key=None):
    """
    AI監査員 (Cat Paradise Version)
    投稿内容が「猫」に特化しているか、他の動物が混じっていないかを厳格にチェックします。
    ※関数名は互換性維持のため audit_dog_content のままにしています。
    """
    if api_key:
        import json
        import os
        from google.oauth2 import service_account

        service_account_str = os.environ.get("GEMINI_SERVICE_ACCOUNT")
        credentials = None
        if service_account_str:
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
            genai.configure(api_key=api_key)
    
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    audit_prompt = f"""
    You are a HIGHLY STRICT Content Compliance Officer for the YouTube channel "Cat Paradise (猫の楽園)".
    
    Current Content to Audit:
    Title: {title}
    Content: {content}
    Search Keyword: {keyword}
    
    === QUALITY & SAFETY RULES ===
    1. NO OTHER ANIMALS: This channel is EXCLUSIVELY for cats. Absolute FAIL if hamsters, dogs, birds, or any other animals are mentioned or suggested.
    2. NO EMOJIS OR SYMBOLS: Use ONLY letters and basic punctuation (.,!). Emojis cause rendering errors. FAIL if you see any.
    3. SHORT & PUNCHY: Must be readable within 15 seconds. If too long, FAIL.
    4. NATURAL JAPANESE: If the content is in Japanese, it must be natural and catchy.
    
    === OUTPUT FORMAT ===
    Result: [PASS or FAIL]
    Feedback: [If FAIL, explain why and give clear instructions to fix. Mention specifically if an illegal animal was found.]
    """
    
    try:
        response = model.generate_content(audit_prompt)
        text = response.text
        
        is_pass = "Result: PASS" in text
        feedback = ""
        if "Feedback:" in text:
            match = re.search(r"Feedback:\s*(.*)", text, re.DOTALL)
            if match: feedback = match.group(1).strip()
            
        # 重大な違反（他の動物の混入など）がある場合は強制終了フラグを立てることも可能
        if any(bad in content.lower() or bad in title.lower() for bad in ["ハムスター", "犬", "いぬ", "hamster", "dog"]):
            if not is_pass:
                feedback = "CRITICAL_SAFETY_ABORT: Forbidden animal detected."
            
        return is_pass, feedback
        
    except Exception as e:
        print(f"Audit Error: {e}")
        return False, "Audit system error. Please retry."

def check_monetization_health(logs):
    if any("403" in log or "429" in log for log in logs): return "CRITICAL_SAFETY_ABORT"
    if any("[WARN]" in log for log in logs): return "WARNING_CHECK_REQUIRED"
    return "PASS"
