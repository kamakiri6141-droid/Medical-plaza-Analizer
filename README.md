# 医療経営データ分析システム

病院・クリニックの生データ（CSV/Excel、複数ファイル・複数スプレッドシート対応）を取り込み、売上・症例件数・年次サマリー表を自動集計し、統計的な異常検知とAIコンサルタント（Gemini）による分析を行うStreamlitアプリです。

## ローカル実行

```
pip install -r requirements.txt
streamlit run app.py
```

`GEMINI_API_KEY` を `.env`（ローカル）に設定してください。

## デプロイ（Render）

リポジトリ直下の `render.yaml` を使って、RenderのBlueprintから自動構築できます。`GEMINI_API_KEY` はRenderダッシュボードの環境変数に設定してください（`render.yaml`には含まれません）。
