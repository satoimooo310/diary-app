import streamlit as st
import google.generativeai as genai
import gspread
import google.auth
import os
import datetime
import json
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# ユーザー指定のカスタムパスから.envを読み込む
env_path = r"C:\Users\consi\.secrets\MyDiaryApp.env"
load_dotenv(dotenv_path=env_path)

# ==========================================
# 定数・設定
# ==========================================
SYSTEM_PROMPT = """あなたは完全な客観性と戦略的視点を持つ、成長のための真実を語るアドバイザーです。
ユーザーが語る1日の出来事や思考に対して、感情的な慰めや無駄な共感を一切排除し、事実関係の整理と戦略的な改善点を指摘してください。
トーンは極めて冷静で合理的、かつ忖度のないリアリストを維持し、次の一手を示唆してください。
返答は必ず【300文字以内】に収めてください。無駄な長文は不要です。
"""

# GeminiがJSONで出力するためのスキーマ定義（Structured Outputs風）
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis_response": {
            "type": "string",
            "description": "アドバイザーとしての冷静な分析と次の一手を示す返答内容（300文字以内）"
        },
        "score": {
            "type": "number",
            "description": "テキスト全体から読み取れるユーザーの感情スコア（-1.0:極めてネガティブ 〜 1.0:極めてポジティブ）"
        },
        "reason": {
            "type": "string",
            "description": "その感情スコアを判定した客観的な短い理由"
        }
    },
    "required": ["analysis_response", "score", "reason"]
}


# ==========================================
# 関数定義（モジュール化）
# ==========================================
def init_session_state():
    """セッション状態の初期化"""
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "sentiment_score" not in st.session_state:
        st.session_state.sentiment_score = 0.0
    if "sentiment_reason" not in st.session_state:
        st.session_state.sentiment_reason = ""
    if "processed_audio" not in st.session_state:
        st.session_state.processed_audio = None


def setup_gemini():
    """Gemini APIの初期設定"""
    gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_api_key:
        try:
            gemini_api_key = st.secrets["gemini_api_key"]
        except Exception:
            pass

    # 画面から直接入力するフォールバック
    if not gemini_api_key:
        gemini_api_key = st.sidebar.text_input("🔑 Gemini API Key", type="password", placeholder="AI Studio等で取得したAPIキー")

    if gemini_api_key:
        genai.configure(api_key=gemini_api_key)
        return True
    else:
        st.warning("⚠️ Gemini APIキーが設定されていません。サイドバーに入力するか、環境変数を確認してください。")
        return False


def get_gspread_client():
    """スプレッドシートクライアントを取得（OAuth認証 / WIFフォールバック）"""
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    try:
        has_secrets = False
        client_secret_val = ""
        token_val = ""
        
        # 1. まず.env（環境変数）に設定されているか確認する
        env_client_secret = os.environ.get("CLIENT_SECRET_JSON", "")
        env_token = os.environ.get("TOKEN_JSON", "")
        
        if env_client_secret and env_token:
            client_secret_val = env_client_secret
            token_val = env_token
            has_secrets = True
        else:
            # 2. なければStreamlit Cloud環境のSecretsを確認する
            try:
                client_secret_val = st.secrets["client_secret"]
                token_val = st.secrets["token"]
                has_secrets = True
            except Exception:
                pass

        if has_secrets:
            with open("client_secret.json", "w", encoding="utf-8") as f:
                f.write(client_secret_val)
            with open("token.json", "w", encoding="utf-8") as f:
                f.write(token_val)
            
            return gspread.oauth(
                credentials_filename='client_secret.json',
                authorized_user_filename='token.json'
            )
        else:
            # ローカル環境などの場合はWIF（デフォルト認証）を試行
            credentials, _ = google.auth.default(scopes=scopes)
            return gspread.authorize(credentials)
            
    except Exception as e:
        raise Exception(f"認証に失敗しました。OAuthのSecrets情報が正しく設定されているか確認してください。({e})")


def transcribe_audio(audio_bytes):
    """音声をテキストに文字起こしする"""
    model_transcribe = genai.GenerativeModel('gemini-2.5-flash')
    media_part = {"mime_type": "audio/wav", "data": audio_bytes}
    resp = model_transcribe.generate_content([media_part, "この音声をそのまま文字起こししてください。文字起こし結果のみを出力してください。"])
    return resp.text.strip()


def save_diary_entry(spreadsheet_url, scent_val, sentiment_val):
    """現在のチャット履歴を要約し、スプレッドシートに保存する"""
    if not st.session_state.messages:
        st.toast("会話履歴がありません。会話してから保存してください。", icon="⚠️")
        return
    if not spreadsheet_url:
        st.toast("スプレッドシートのURLが登録されていません。", icon="⚠️")
        return

    with st.spinner("日記を生成し、保存しています..."):
        try:
            # 1. 認証とシート取得
            client = get_gspread_client()
            if "https://" in spreadsheet_url:
                sheet = client.open_by_url(spreadsheet_url).sheet1
            else:
                sheet = client.open_by_key(spreadsheet_url).sheet1

            # 2. 会話履歴の集約
            conversation_text = ""
            raw_inputs = ""
            for msg in st.session_state.messages:
                role_name = "User" if msg["role"] == "user" else "Advisor"
                conversation_text += f"{role_name}: {msg['content']}\n"
                if msg["role"] == "user":
                    raw_inputs += msg['content'] + "\n\n"
            raw_inputs = raw_inputs.strip()

            # 3. 要約と分析の生成
            summary_prompt = f"""
以下の「対話履歴」と「本日の匂いデータ」に基づき、指定のJSON形式で出力してください。
匂いデータ: {scent_val if scent_val else "記録なし"}

【対話履歴】
{conversation_text}

出力するJSONのキー：
1. "content": 発言の要約（ユーザーの入力内容から読み取れる出来事と思考の客観的で分かりやすい要約。）
2. "analysis": 冷静な分析（客観的な視点からのフィードバック。成長のための真実を語ること。必ず最大【300文字以内】で出力。）
"""
            model_json = genai.GenerativeModel('gemini-2.5-flash', generation_config={"response_mime_type": "application/json"})
            resp = model_json.generate_content(summary_prompt)
            result_json = json.loads(resp.text)
            
            content = result_json.get("content", "内容の生成に失敗しました。")
            analysis = result_json.get("analysis", "分析の生成に失敗しました。")
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")

            # 4. スプレッドシート追記
            sheet.append_row([date_str, raw_inputs, content, analysis, scent_val, sentiment_val])
            
            st.toast("スプレッドシートへの保存が完了しました！", icon="✅")
            
            return {
                "date": date_str,
                "raw_inputs": raw_inputs,
                "content": content,
                "analysis": analysis,
                "scent": scent_val,
                "sentiment": sentiment_val
            }
                
        except Exception as e:
            st.error(f"⚠️ 保存エラーが発生しました:\n{e}")
            return None


# ==========================================
# メインUI構成
# ==========================================
def main():
    st.set_page_config(page_title="対話型AI日記アプリ", page_icon="📓", layout="wide")
    st.title("📓 対話型AI日記アプリ (チャット型・サービスアカウント版)")

    init_session_state()
    is_gemini_ready = setup_gemini()
    spreadsheet_url = os.environ.get("SPREADSHEET_URL", "")
    if not spreadsheet_url:
        try:
            spreadsheet_url = st.secrets["spreadsheet_url"]
        except Exception:
            pass
            
    # --- サイドバー構成 ---
    st.sidebar.header("🔑 設定 / Settings")
    if not spreadsheet_url:
        spreadsheet_url = st.sidebar.text_input("🔗 スプレッドシートURL", type="default", placeholder="https://docs.google...")

    st.sidebar.markdown("---")
    st.sidebar.header("🌸 今日の記録オプション")
    scent_input = st.sidebar.text_input("今日の匂い (Scent)", placeholder="例: 雨上がり、コーヒーの香り")

    st.sidebar.markdown("---")
    st.sidebar.header("📊 感情アナリティクス")
    if st.session_state.sentiment_reason:
        st.sidebar.info(f"**判定理由:** {st.session_state.sentiment_reason}")
    st.session_state.sentiment_score = st.sidebar.slider(
        "感情スコア (手動修正可能: -1.0〜1.0)",
        min_value=-1.0,
        max_value=1.0,
        value=float(st.session_state.sentiment_score),
        step=0.1
    )

    st.sidebar.markdown("---")
    if st.sidebar.button("💾 これを日記として保存"):
        saved_data = save_diary_entry(spreadsheet_url, scent_input if scent_input else "なし", st.session_state.sentiment_score)
        if saved_data:
            with st.sidebar.expander("保存されたデータ詳細", expanded=False):
                st.write("**日付:**", saved_data["date"])
                st.write("**生の入力:**", saved_data["raw_inputs"])
                st.write("**発言の要約:**", saved_data["content"])
                st.write("**冷静な分析:**", saved_data["analysis"])
                st.write("**匂い:**", saved_data["scent"])
                st.write("**感情スコア:**", saved_data["sentiment"])

    # --- メインチャット画面 ---
    # 過去の会話履歴を描画
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    st.markdown("---")
    st.write("▼ 文字入力、または音声で一日の出来事を話してください。")

    # 入力UI
    user_audio = st.audio_input("音声で入力（🎙️）")
    user_text = st.chat_input("文字で入力して会話...", disabled=not is_gemini_ready)

    input_prompt = ""
    display_text = ""

    # 入力処理（文字優先、なければ音声）
    if user_text:
        if user_text.strip() == "":
            st.toast("テキストを入力してください", icon="⚠️")
        else:
            input_prompt = user_text
            display_text = user_text
            
    elif user_audio:
        audio_bytes = user_audio.getvalue()
        if st.session_state.processed_audio != audio_bytes:
            with st.spinner("音声を文字起こし中...🎙️"):
                try:
                    transcribed_text = transcribe_audio(audio_bytes)
                    st.session_state.processed_audio = audio_bytes
                    
                    if transcribed_text == "":
                        st.toast("音声を認識できませんでした。", icon="⚠️")
                    else:
                        input_prompt = transcribed_text
                        display_text = f"🎙️ {transcribed_text}"
                except Exception as e:
                    st.session_state.processed_audio = audio_bytes
                    st.toast(f"文字起こしエラー: {e}", icon="⚠️")

    # チャット処理実行
    if input_prompt:
        # 1. ユーザー発言を表示＆履歴保存
        with st.chat_message("user"):
            st.write(display_text)
        
        st.session_state.messages.append({
            "role": "user", 
            "content": input_prompt
        })
        
        # 2. Geminiの思考・応答プロセス（ストリーミング＆感情分析統合）
        with st.chat_message("assistant"):
            try:
                # 履歴の構築
                history_gemini = []
                for msg in st.session_state.messages[:-1]: # 直前の発言以外
                    gemini_role = "user" if msg["role"] == "user" else "model"
                    history_gemini.append({"role": gemini_role, "parts": [msg["content"]]})
                
                # 通常のチャットモデル（ストリーミング表示用）
                model_chat = genai.GenerativeModel('gemini-2.5-flash', system_instruction=SYSTEM_PROMPT)
                chat_session = model_chat.start_chat(history=history_gemini)
                
                # UIストリーミング出力
                response_stream = chat_session.send_message(input_prompt, stream=True)
                
                def stream_chunks():
                    for chunk in response_stream:
                        yield chunk.text
                        
                full_response = st.write_stream(stream_chunks)
                
                # アシスタントの応答を履歴保存
                st.session_state.messages.append({"role": "assistant", "content": full_response})
                
                # 3. 裏側で感情スコアの算出（並行/後続処理）
                # ユーザーの最新の入力から感情を分析する
                sys_prompt_sentiment = 'あなたは日記テキストの感情を分析するシステムです。入力されたテキストを分析し、JSONフォーマットのみで出力してください。 {"score": 数値（-1.0〜1.0）, "reason": "判定の短い理由"}'
                model_sentiment = genai.GenerativeModel('gemini-2.5-flash', system_instruction=sys_prompt_sentiment, generation_config={"response_mime_type": "application/json"})
                resp_sentiment = model_sentiment.generate_content(input_prompt)
                
                try:
                    sentiment_data = json.loads(resp_sentiment.text)
                    st.session_state.sentiment_score = float(sentiment_data.get("score", 0.0))
                    st.session_state.sentiment_reason = sentiment_data.get("reason", "")
                    st.rerun() # サイドバーのスコアを更新
                except Exception:
                    st.session_state.sentiment_reason = "解析エラー（手動でスコアを入力してください）"

            except Exception as e:
                st.warning(f"通信エラーが発生しました: {e}")

if __name__ == "__main__":
    main()
