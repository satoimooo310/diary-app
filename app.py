import streamlit as st
import google.generativeai as genai
import gspread
import google.auth
import os
import datetime
import json

# ==========================================
# ページ初期設定
# ==========================================
st.set_page_config(page_title="対話型AI日記アプリ", page_icon="📓", layout="wide")
st.title("📓 対話型AI日記アプリ (チャット型・WIF認証版)")

# ==========================================
# 秘密情報の読み込み
# ==========================================
gemini_api_key = st.secrets.get("gemini_api_key", os.environ.get("GEMINI_API_KEY", ""))
spreadsheet_url = st.secrets.get("spreadsheet_url", os.environ.get("SPREADSHEET_URL", ""))

if gemini_api_key:
    genai.configure(api_key=gemini_api_key)
else:
    st.warning("⚠️ Gemini APIキーが設定されていません。Secretsを確認してください。")

# ==========================================
# キャラ設定（システムプロンプト）
# ==========================================
SYSTEM_PROMPT = """あなたは完全な客観性と戦略的視点を持つ、成長のための真実を語るアドバイザーです。
ユーザーが語る1日の出来事や思考に対して、感情的な慰めや無駄な共感を一切排除し、事実関係の整理と戦略的な改善点を指摘してください。
トーンは極めて冷静で合理的、かつ忖度のないリアリストを維持し、次の一手を示唆してください。
返答は必ず【300文字以内】に収めてください。無駄な長文は不要です。
"""

# ==========================================
# セッション状態（チャット履歴）の初期化
# ==========================================
if "messages" not in st.session_state:
    st.session_state.messages = []

# ==========================================
# サイドバー: 匂い入力と保存ボタン
# ==========================================
st.sidebar.header("🌸 今日の記録オプション")
scent_input = st.sidebar.text_input("今日の匂い (Scent)", placeholder="例: 雨上がり、コーヒーの香り")

st.sidebar.markdown("---")
if st.sidebar.button("💾 これを日記として保存"):
    if not st.session_state.messages:
        st.sidebar.error("会話履歴がありません。会話してから保存してください。")
    elif not spreadsheet_url:
        st.sidebar.error("スプレッドシートのURLがSecretsに登録されていません。")
    else:
        with st.spinner("日記を生成し、スプレッドシートへ保存しています..."):
            try:
                # ------------------------------------------
                # 1. 認証フロー (Streamlit CloudではOAuthを優先)
                # ------------------------------------------
                scopes = [
                    'https://www.googleapis.com/auth/spreadsheets',
                    'https://www.googleapis.com/auth/drive'
                ]
                
                # Streamlit Cloud環境（Secretsが存在する）場合は即座にOAuthを使用する
                # これにより、GCPメタデータサーバーへの無駄なアクセス（タイムアウトエラー）を防止
                if "token" in st.secrets and "client_secret" in st.secrets:
                    if not os.path.exists("client_secret.json"):
                        with open("client_secret.json", "w", encoding="utf-8") as f:
                            f.write(st.secrets["client_secret"])
                    if not os.path.exists("token.json"):
                        with open("token.json", "w", encoding="utf-8") as f:
                            f.write(st.secrets["token"])
                    
                    client = gspread.oauth(
                        credentials_filename='client_secret.json',
                        authorized_user_filename='token.json'
                    )
                else:
                    # ローカル環境などの場合はWIF（デフォルト認証）を試行
                    credentials, project_id = google.auth.default(scopes=scopes)
                    client = gspread.authorize(credentials)
                
                # 指定シートを開く
                if "https://" in spreadsheet_url:
                    sheet = client.open_by_url(spreadsheet_url).sheet1
                else:
                    sheet = client.open_by_key(spreadsheet_url).sheet1

                # ------------------------------------------
                # 2. 会話履歴を一つのテキストにまとめる
                # ------------------------------------------
                conversation_text = ""
                for msg in st.session_state.messages:
                    role_name = "User" if msg["role"] == "user" else "Advisor"
                    conversation_text += f"{role_name}: {msg['content']}\n"

                # ------------------------------------------
                # 3. 保存用のサマリ生成（JSON形式強制）
                # ------------------------------------------
                summary_prompt = f"""
以下の「対話履歴」と「本日の匂いデータ」に基づき、指定のJSON形式で出力してください。
匂いデータ: {scent_input if scent_input else "記録なし"}

【対話履歴】
{conversation_text}

出力するJSONのキーは以下の2つのみにしてください：
1. "content": 日記内容（対話から読み取れる情景描写を重視した内容。起こった事実と環境を描写。）
2. "analysis": 冷静な分析（客観的な視点からのフィードバック。成長のための真実を語ること。必ず最大【300文字以内】で出力すること。）
"""
                model_json = genai.GenerativeModel('gemini-2.5-flash', generation_config={"response_mime_type": "application/json"})
                resp = model_json.generate_content(summary_prompt)
                result_json = json.loads(resp.text)
                
                date_str = datetime.datetime.now().strftime("%Y-%m-%d")
                content = result_json.get("content", "内容の生成に失敗しました。")
                analysis = result_json.get("analysis", "分析の生成に失敗しました。")
                scent_val = scent_input if scent_input else "なし"
                
                # 生のユーザー発言をまとめる
                raw_inputs = ""
                for msg in st.session_state.messages:
                    if msg["role"] == "user":
                        raw_inputs += msg['content'] + "\n\n"
                raw_inputs = raw_inputs.strip()
                
                # ------------------------------------------
                # 4. スプレッドシートへ追記: [日付, 生の入力, 日記内容(情景描写), 冷静な分析, 匂いの記録]
                # ------------------------------------------
                sheet.append_row([date_str, raw_inputs, content, analysis, scent_val])
                
                st.sidebar.success("✅ スプレッドシートへの保存が完了しました！")
                
                # 動作確認用エクスパンダー
                with st.sidebar.expander("保存されたデータ詳細"):
                    st.write("**日付:**", date_str)
                    st.write("**生の入力:**", raw_inputs)
                    st.write("**情景描写:**", content)
                    st.write("**冷静な分析:**", analysis)
                    st.write("**匂い:**", scent_val)
                    
            except Exception as e:
                st.sidebar.error(f"⚠️ エラーが発生しました:\n{e}")

# ==========================================
# メイン画面: チャットUI
# ==========================================
# 過去の会話履歴を描画
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

# --- 音声入力とテキスト入力 ---
st.markdown("---")
st.write("▼ 文字入力、または音声で一日の出来事を話してください。")

# st.audio_input は Streamlit 1.36 以降で動作します。
user_audio = st.audio_input("音声で入力（🎙️）")
user_text = st.chat_input("文字で入力して会話...")

# 送信処理
input_prompt = ""

# 文字優先、なければ音声
if user_text:
    input_prompt = user_text
    display_text = user_text
elif user_audio:
    with st.spinner("音声を文字起こし中...🎙️"):
        media_part = {
            "mime_type": "audio/wav", 
            "data": user_audio.read()
        }
        model_transcribe = genai.GenerativeModel('gemini-2.5-flash')
        resp = model_transcribe.generate_content([media_part, "この音声をそのまま文字起こししてください。文字起こし結果のみを出力してください。"])
        transcribed_text = resp.text.strip()
    input_prompt = transcribed_text
    display_text = f"🎙️ {transcribed_text}"

if input_prompt:
    # 1. ユーザー発言を画面に表示＆履歴保存
    with st.chat_message("user"):
        st.write(display_text)
    
    st.session_state.messages.append({
        "role": "user", 
        "content": input_prompt
    })
    
    # 2. Geminiの思考・応答プロセス
    with st.chat_message("assistant"):
        with st.spinner("冷静に分析中..."):
            model_chat = genai.GenerativeModel('gemini-2.5-flash', system_instruction=SYSTEM_PROMPT)
            
            # Geminiのstart_chatには過去履歴を渡す
            history_gemini = []
            for msg in st.session_state.messages[:-1]: # 直前の発言以外
                gemini_role = "user" if msg["role"] == "user" else "model"
                history_gemini.append({"role": gemini_role, "parts": [msg["content"]]})
                
            chat_session = model_chat.start_chat(history=history_gemini)
            
            response = chat_session.send_message(input_prompt)
                
            st.write(response.text)
            
            # アシスタントの応答を履歴保存
            st.session_state.messages.append({"role": "assistant", "content": response.text})
