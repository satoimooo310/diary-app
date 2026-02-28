import streamlit as st
import google.generativeai as genai
import gspread
import datetime
import json
import os

# ページ設定
st.set_page_config(page_title="対話型AI日記アプリ", page_icon="📓", layout="wide")

st.title("対話型AI日記アプリ")

# --- 認証情報の読み込み (個人使用のためサイドバーUIは削除) ---
# Streamlit CloudのSecretsから自動的に読み込みます。
# ※「spreadsheet_url = "..."」という形でもう一つSecretsに追加する必要があります。
gemini_api_key = st.secrets.get("gemini_api_key", "")
spreadsheet_url = st.secrets.get("spreadsheet_url", "")

# --- システムプロンプト設定 ---
SYSTEM_PROMPT = """あなたは極めて冷静で合理的、かつ忖度のないリアリストなプロのアドバイザーです。
ユーザーが提供したチャットログから、以下の2つを生成してください。

1. 今日の要約: 140文字以内で、事実ベースで無駄なく客観的にまとめること。
2. 鋭い分析: 100文字以内で、一切の忖度なしに、冷静かつ合理的な分析と今後の改善点やアドバイスを提供すること。感情的な慰めは不要。

必ず以下のJSONフォーマットでのみ出力してください:
{
  "summary": "今日の要約...",
  "analysis": "鋭い分析..."
}
"""

def generate_diary(api_key, chat_log):
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        'gemini-2.5-flash',
        system_instruction=SYSTEM_PROMPT,
        generation_config={"response_mime_type": "application/json"}
    )
    response = model.generate_content(chat_log)
    return json.loads(response.text)

def save_to_spreadsheet(sheet_url, date_str, summary, analysis):
    # クラウド（Streamlit Secrets）から認証情報を読み込むか、ローカルファイルを使うか分岐
    if "gcp_service_account" in st.secrets:
        # Streamlit Cloud環境: Secretsから読み込む (Service Account方式に戻すのが一番確実で簡単)
        # 今回はOAuthで進めたので、OAuthのSecretsを使います
        pass # 下で詳細実装
        
    # === 修正箇所: Secrets対応 OAuth ===
    # Streamlit CloudのSecretsに値があれば、一時ファイルを作成してそれを使用する
    import json
    import os
    
    # client_secret.json の用意
    if "client_secret" in st.secrets:
        with open("client_secret.json", "w") as f:
            f.write(st.secrets["client_secret"])
            
    # token.json の用意
    if "token" in st.secrets:
        with open("token.json", "w") as f:
            f.write(st.secrets["token"])

    client = gspread.oauth(
        credentials_filename='client_secret.json',
        authorized_user_filename='token.json'
    )
    
    # URLかIDかで開き方を分ける
    if "https://" in sheet_url:
        sheet = client.open_by_url(sheet_url).sheet1
    else:
        sheet = client.open_by_key(sheet_url).sheet1
        
    # [日付, 今日の要約, 客観的アドバイザーによる鋭い分析] を追記
    sheet.append_row([date_str, summary, analysis])


# --- メイン画面 (Main) ---
st.write("一日のチャットログ（行動履歴や考え）を以下に貼り付けてください。")
chat_input = st.text_area("チャットログ", height=250)

if st.button("生成と保存"):
    if not gemini_api_key:
        st.error("サイドバーからGemini APIキーを入力してください。")
    elif not spreadsheet_url:
        st.error("サイドバーからスプレッドシートのURLまたはIDを入力してください。")
    elif not chat_input.strip():
        st.error("チャットログを入力してください。")
    elif not os.path.exists("client_secret.json") and not os.path.exists("token.json"):
        st.error("サイドバーからOAuthのJSONキー(client_secret.json)をアップロードしてください。")
    else:
        with st.spinner("AIがログを分析し、スプレッドシートへの保存を試みています...（初回はブラウザでGoogle認証画面が開く場合があります）"):
            try:
                # Geminiで生成
                result = generate_diary(gemini_api_key, chat_input)
                summary = result.get("summary", "要約の生成に失敗しました。")
                analysis = result.get("analysis", "分析の生成に失敗しました。")
                
                # スプレッドシートへ保存
                today_str = datetime.datetime.now().strftime("%Y-%m-%d")
                save_to_spreadsheet(spreadsheet_url, today_str, summary, analysis)
                
                # 結果表示
                st.subheader("📝 今日の要約")
                st.write(summary)
                
                st.subheader("🧐 アドバイザーによる鋭い分析")
                st.write(analysis)
                
                st.markdown("---")
                st.success("記録完了。明日もこの調子で。")
                
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")
