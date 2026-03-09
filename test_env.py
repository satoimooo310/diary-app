import os
import streamlit as st

key1 = os.environ.get("GEMINI_API_KEY", "")
key2 = ""
try:
    if "gemini_api_key" in st.secrets:
        key2 = "Exists in st.secrets"
except Exception as e:
    key2 = f"Error: {e}"

print(f"OS Environ: {'Set' if key1 else 'Not Set'}")
print(f"st.secrets: {key2}")
