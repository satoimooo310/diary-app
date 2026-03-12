import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.credentials import Credentials
import os
import json
import datetime
import tempfile
from dotenv import load_dotenv

# ユーザー指定のカスタムパスから.envを読み込む
env_path = r"C:\Users\consi\.secrets\MyDiaryApp.env"
load_dotenv(dotenv_path=env_path)

# ==========================================
# 定数・設定（#2: モデル名を一元管理）
# ==========================================
MODEL_NAME = "gemini-2.5-flash"

# チャット履歴の上限件数（#8: 無制限防止）
MAX_HISTORY_TURNS = 10

SYSTEM_PROMPT = """あなたは完全な客観性と戦略的視点を持つ、成長のための真実を語るアドバイザーです。
ユーザーが語る1日の出来事や思考に対して、感情的な慰めや無駄な共感を一切排除し、事実関係の整理と戦略的な改善点を指摘してください。
トーンは極めて冷静で合理的、かつ忖度のないリアリストを維持し、次の一手を示唆してください。
返答の末尾には必ず以下のJSON形式のメタ情報を追加してください。本文と区切るために ```json ``` で囲むこと。
```json
{"score": <-1.0〜1.0の感情スコア>, "reason": "<短い判定理由>"}
```
本文は【300文字以内】に収めてください。"""


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
    # #3: 感情スコア更新フラグ（rerun 管理用）
    if "pending_rerun" not in st.session_state:
        st.session_state.pending_rerun = False


def setup_gemini():
    """Gemini APIの初期設定"""
    gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_api_key:
        try:
            gemini_api_key = st.secrets["gemini_api_key"]
        except Exception:
            pass

    if not gemini_api_key:
        gemini_api_key = st.sidebar.text_input(
            "🔑 Gemini API Key", type="password", placeholder="AI Studio等で取得したAPIキー"
        )

    if gemini_api_key:
        genai.configure(api_key=gemini_api_key)
        return True
    else:
        # #6: 重大エラーは st.error、軽微なものは st.toast に統一
        st.error("⚠️ Gemini APIキーが設定されていません。サイドバーに入力するか、環境変数を確認してください。")
        return False


def get_gspread_client():
    """スプレッドシートクライアントを取得（#1: tempfileで認証情報を安全に処理）"""
    try:
        client_secret_val = ""
        token_val = ""

        # 1. まず.env（環境変数）を確認
        env_client_secret = os.environ.get("CLIENT_SECRET_JSON", "")
        env_token = os.environ.get("TOKEN_JSON", "")

        if env_client_secret and env_token:
            client_secret_val = env_client_secret
            token_val = env_token
        else:
            # 2. Streamlit Cloud Secretsを確認
            try:
                client_secret_val = st.secrets["client_secret"]
                token_val = st.secrets["token"]
            except Exception:
                pass

        if client_secret_val and token_val:
            # #1: tempfileを使い、ディスクへの恒久的な書き出しを回避
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as cs_file:
                cs_file.write(client_secret_val)
                cs_path = cs_file.name

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as tk_file:
                tk_file.write(token_val)
                tk_path = tk_file.name

            try:
                client = gspread.oauth(
                    credentials_filename=cs_path,
                    authorized_user_filename=tk_path,
                )
            finally:
                # 使い終わったら即削除
                os.unlink(cs_path)
                os.unlink(tk_path)

            return client
        else:
            # ローカルファイルが存在すればそちらを使用
            if os.path.exists("client_secret.json") and os.path.exists("token.json"):
                return gspread.oauth(
                    credentials_filename="client_secret.json",
                    authorized_user_filename="token.json",
                )
            raise Exception("認証情報が見つかりません。環境変数またはStreamlit Secretsを確認してください。")

    except Exception as e:
        raise Exception(f"認証に失敗しました。({e})")


def transcribe_audio(audio_bytes):
    """音声をテキストに文字起こしする"""
    model_transcribe = genai.GenerativeModel(MODEL_NAME)  # #2: 定数使用
    media_part = {"mime_type": "audio/wav", "data": audio_bytes}
    resp = model_transcribe.generate_content(
        [media_part, "この音声をそのまま文字起こししてください。文字起こし結果のみを出力してください。"]
    )
    return resp.text.strip()


def _parse_sentiment_from_response(full_response: str) -> dict | None:
    """
    アシスタント応答テキストの末尾に埋め込まれたJSONブロックを抽出する。
    (#4+5: API呼び出しを2回→1回に統合)
    """
    try:
        start = full_response.rfind("```json")
        end = full_response.rfind("```", start + 1)
        if start != -1 and end != -1:
            json_str = full_response[start + 7 : end].strip()
            return json.loads(json_str)
    except Exception:
        pass
    return None


def _clean_response_text(full_response: str) -> str:
    """応答テキストから末尾のJSONブロックを除去してUIに表示するテキストを返す"""
    start = full_response.rfind("```json")
    if start != -1:
        return full_response[:start].strip()
    return full_response.strip()


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
                # JSON埋め込みブロックを除いた表示用テキストを使う
                clean_content = _clean_response_text(msg["content"])
                conversation_text += f"{role_name}: {clean_content}\n"
                if msg["role"] == "user":
                    raw_inputs += msg["content"] + "\n\n"
            raw_inputs = raw_inputs.strip()

            # 3. 要約と分析の生成（#2: 定数使用）
            summary_prompt = f"""
以下の「対話履歴」と「本日の匂いデータ」に基づき、指定のJSON形式で出力してください。
匂いデータ: {scent_val if scent_val else "記録なし"}

【対話履歴】
{conversation_text}

出力するJSONのキー：
1. "content": 発言の要約（ユーザーの入力内容から読み取れる出来事と思考の客観的で分かりやすい要約。）
2. "analysis": 冷静な分析（客観的な視点からのフィードバック。成長のための真実を語ること。必ず最大【300文字以内】で出力。）
"""
            model_json = genai.GenerativeModel(
                MODEL_NAME,
                generation_config={"response_mime_type": "application/json"},
            )
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
                "sentiment": sentiment_val,
            }

        except Exception as e:
            st.error(f"⚠️ 保存エラーが発生しました:\n{e}")  # #6: 重大エラーは st.error
            return None


# ==========================================
# メインUI構成
# ==========================================
def main():
    st.set_page_config(page_title="対話型AI日記アプリ", page_icon="📓", layout="wide")
    st.title("📓 対話型AI日記アプリ (チャット型・サービスアカウント版)")

    init_session_state()

    # #3: pending_rerun フラグを処理（感情スコア更新後の1回だけ rerun）
    if st.session_state.pending_rerun:
        st.session_state.pending_rerun = False
        st.rerun()

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
        spreadsheet_url = st.sidebar.text_input(
            "🔗 スプレッドシートURL", type="default", placeholder="https://docs.google..."
        )

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
        step=0.1,
    )

    st.sidebar.markdown("---")
    if st.sidebar.button("💾 これを日記として保存"):
        saved_data = save_diary_entry(
            spreadsheet_url,
            scent_input if scent_input else "なし",
            st.session_state.sentiment_score,
        )
        if saved_data:
            with st.sidebar.expander("保存されたデータ詳細", expanded=False):
                st.write("**日付:**", saved_data["date"])
                st.write("**生の入力:**", saved_data["raw_inputs"])
                st.write("**発言の要約:**", saved_data["content"])
                st.write("**冷静な分析:**", saved_data["analysis"])
                st.write("**匂い:**", saved_data["scent"])
                st.write("**感情スコア:**", saved_data["sentiment"])

    # --- メインチャット画面 ---
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            # #4+5: 表示時はJSONブロックを除去してクリーンなテキストを表示
            st.write(_clean_response_text(msg["content"]))

    st.markdown("---")
    st.write("▼ 文字入力、または音声で一日の出来事を話してください。")

    # 入力UI
    user_audio = st.audio_input("音声で入力（🎙️）")
    user_text = st.chat_input("文字で入力して会話...", disabled=not is_gemini_ready)

    input_prompt = ""
    display_text = ""

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
        with st.chat_message("user"):
            st.write(display_text)

        st.session_state.messages.append({"role": "user", "content": input_prompt})

        with st.chat_message("assistant"):
            try:
                # #8: 直近 MAX_HISTORY_TURNS 件のみ履歴として渡す（トークン上限対策）
                recent_messages = st.session_state.messages[:-1]
                if len(recent_messages) > MAX_HISTORY_TURNS:
                    recent_messages = recent_messages[-MAX_HISTORY_TURNS:]

                history_gemini = [
                    {
                        "role": "user" if msg["role"] == "user" else "model",
                        "parts": [_clean_response_text(msg["content"])],
                    }
                    for msg in recent_messages
                ]

                # #2: MODEL_NAME 定数を使用
                # #4+5: チャット応答に感情スコアを埋め込み、API呼び出しを1回に統合
                model_chat = genai.GenerativeModel(MODEL_NAME, system_instruction=SYSTEM_PROMPT)
                chat_session = model_chat.start_chat(history=history_gemini)

                response_stream = chat_session.send_message(input_prompt, stream=True)

                def stream_chunks():
                    for chunk in response_stream:
                        yield chunk.text

                full_response = st.write_stream(stream_chunks)

                # 表示後にJSONブロックを除去して再描画
                clean_text = _clean_response_text(full_response)
                if clean_text != full_response:
                    # JSONブロックが混入している場合はクリーンなテキストで上書き表示
                    st.empty()
                    st.write(clean_text)

                st.session_state.messages.append({"role": "assistant", "content": full_response})

                # #4+5: 応答に埋め込まれた感情スコアを抽出
                sentiment_data = _parse_sentiment_from_response(full_response)
                if sentiment_data:
                    st.session_state.sentiment_score = float(sentiment_data.get("score", 0.0))
                    st.session_state.sentiment_reason = sentiment_data.get("reason", "")
                else:
                    st.session_state.sentiment_reason = "解析エラー（手動でスコアを入力してください）"

                # #3: フラグで次回 rerun を制御（無限ループ防止）
                st.session_state.pending_rerun = True

            except Exception as e:
                st.error(f"通信エラーが発生しました: {e}")  # #6: 重大エラーは st.error


if __name__ == "__main__":
    main()
