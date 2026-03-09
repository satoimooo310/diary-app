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
if "sentiment_score" not in st.session_state:
    st.session_state.sentiment_score = 0.0
if "sentiment_reason" not in st.session_state:
    st.session_state.sentiment_reason = ""
if "processed_audio" not in st.session_state:
    st.session_state.processed_audio = None

# ==========================================
# サイドバー: 匂い入力と保存ボタン
# ==========================================
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
                    with open("client_secret.json", "w", encoding="utf-8") as f:
                        f.write(st.secrets["client_secret"])
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
1. "content": 発言の要約（ユーザーの入力内容から読み取れる出来事と思考の客観的で分かりやすい要約。）
2. "analysis": 冷静な分析（客観的な視点からのフィードバック。成長のための真実を語ること。必ず最大【300文字以内】で出力すること。）
"""
                model_json = genai.GenerativeModel('gemini-2.5-flash', generation_config={"response_mime_type": "application/json"})
                resp = model_json.generate_content(summary_prompt)
                result_json = json.loads(resp.text)
                
                date_str = datetime.datetime.now().strftime("%Y-%m-%d")
                content = result_json.get("content", "内容の生成に失敗しました。")
                analysis = result_json.get("analysis", "分析の生成に失敗しました。")
                scent_val = scent_input if scent_input else "なし"
                sentiment_val = st.session_state.sentiment_score
                
                # 生のユーザー発言をまとめる
                raw_inputs = ""
                for msg in st.session_state.messages:
                    if msg["role"] == "user":
                        raw_inputs += msg['content'] + "\n\n"
                raw_inputs = raw_inputs.strip()
                
                # ------------------------------------------
                # 4. スプレッドシートへ追記: [日付, 生の入力, 発言の要約, 冷静な分析, 匂いの記録, 感情スコア]
                # ------------------------------------------
                sheet.append_row([date_str, raw_inputs, content, analysis, scent_val, sentiment_val])
                
                st.sidebar.success("✅ スプレッドシートへの保存が完了しました！")
                
                # 動作確認用エクスパンダー
                with st.sidebar.expander("保存されたデータ詳細"):
                    st.write("**日付:**", date_str)
                    st.write("**生の入力:**", raw_inputs)
                    st.write("**発言の要約:**", content)
                    st.write("**冷静な分析:**", analysis)
                    st.write("**匂い:**", scent_val)
                    st.write("**感情スコア:**", sentiment_val)
                    
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
if user_text is not None:
    if user_text.strip() == "":
        st.warning("テキストを入力してください")
    else:
        input_prompt = user_text
        display_text = user_text
elif user_audio:
    audio_bytes = user_audio.getvalue()
    if st.session_state.processed_audio != audio_bytes:
        with st.spinner("音声を文字起こし中...🎙️"):
            try:
                media_part = {
                    "mime_type": "audio/wav", 
                    "data": audio_bytes
                }
                model_transcribe = genai.GenerativeModel('gemini-2.5-flash')
                resp = model_transcribe.generate_content([media_part, "この音声をそのまま文字起こししてください。文字起こし結果のみを出力してください。"])
                transcribed_text = resp.text.strip()
                st.session_state.processed_audio = audio_bytes
                
                if transcribed_text == "":
                    st.warning("テキストを入力してください")
                else:
                    input_prompt = transcribed_text
                    display_text = f"🎙️ {transcribed_text}"
            except Exception as e:
                st.session_state.processed_audio = audio_bytes  # リトライループ防止
                st.warning("通信エラーが発生しました。時間をおいて再試行してください。")

if input_prompt:
    # 1. ユーザー発言を画面に表示＆履歴保存
    with st.chat_message("user"):
        st.write(display_text)
    
    st.session_state.messages.append({
        "role": "user", 
        "content": input_prompt
    })
    
    # 1.5 感情分析の実行
    with st.spinner("感情状態を分析中..."):
        try:
            sys_prompt_sentiment = 'あなたは日記テキストの感情を分析するシステムです。入力されたテキストを分析し、-1.0（極めてネガティブ）から1.0（極めてポジティブ）の範囲でスコア化してください。出力は必ず以下のJSONフォーマットのみとしてください。{"score": 数値, "reason": "判定の短い理由"}'
            model_sentiment = genai.GenerativeModel('gemini-2.5-flash', system_instruction=sys_prompt_sentiment, generation_config={"response_mime_type": "application/json"})
            resp_sentiment = model_sentiment.generate_content(input_prompt)
            
            try:
                sentiment_data = json.loads(resp_sentiment.text)
                st.session_state.sentiment_score = float(sentiment_data.get("score", 0.0))
                st.session_state.sentiment_reason = sentiment_data.get("reason", "")
            except (json.JSONDecodeError, ValueError) as json_err:
                st.warning("AIによる判定に失敗しました")
                st.session_state.sentiment_reason = "解析エラー（手動でスコアを入力してください）"
                
        except Exception as api_err:
            st.warning("通信エラーが発生しました。時間をおいて再試行してください。")
            st.session_state.sentiment_reason = "通信エラー（手動でスコアを入力してください）"

    # 2. Geminiの思考・応答プロセス
    with st.chat_message("assistant"):
        with st.spinner("冷静に分析中..."):
            try:
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
                
                # 感情スコアなどをサイドバーに即時反映させるために再描画
                st.rerun()
            except Exception as e:
                st.warning("通信エラーが発生しました。時間をおいて再試行してください。")
