@echo off
cd /d "c:\dev\日記"

echo GitHubへアプリの更新を送信します...

git add app.py requirements.txt .gitignore
git commit -m "Update app"
git push origin master

echo.
echo 送信が完了しました。何かキーを押して終了してください。
pause
