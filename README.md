# 医療経営データ分析システム

病院・クリニックの生データ（CSV/Excel、複数ファイル・複数スプレッドシート対応）を取り込み、売上・症例件数・年次サマリー表を自動集計し、統計的な異常検知とAIコンサルタント（Gemini）による分析を行うStreamlitアプリです。

## ローカル実行

```
pip install -r requirements.txt
streamlit run app.py
```

`GEMINI_API_KEY` を `.env`（ローカル）に設定してください。

## 会話履歴の保存（Supabase・任意）

`SUPABASE_URL` と `SUPABASE_KEY` を設定すると、AIコンサルタントとの会話（質問と回答）をSupabase上に保存できます（未設定の場合は会話がブラウザを閉じると消える、これまで通りの動作です）。アップロードしたデータ自体は保存されません。

アプリを開くたび（またはサイドバーの「新しい会話を始める」を押すたび）に新しい1つの会話として区別され、過去の会話は日時ごとに分けて一覧から閲覧できます。

1. [supabase.com](https://supabase.com) で新規プロジェクトを作成
2. プロジェクトの Settings → API から `Project URL`（→ `SUPABASE_URL`）と `service_role` キー（→ `SUPABASE_KEY`）を取得
3. SQL Editor で以下を実行してテーブルを作成

```sql
create table if not exists chat_messages (
  id bigint generated always as identity primary key,
  clinic_id text not null,
  session_id text not null,
  role text not null,
  content text not null,
  created_at timestamptz not null default now()
);

create index if not exists chat_messages_session_idx on chat_messages (clinic_id, session_id, created_at);
```

4. `.env`（ローカル）またはSecrets（デプロイ先）に `SUPABASE_URL` / `SUPABASE_KEY` を設定

`service_role` キーはサーバー側（Streamlitの実行環境）でのみ使われ、ブラウザには渡らないため安全です。サイドバーの「施設名」を変えることで、将来的に複数施設の会話履歴を分けて保存できます。

## デプロイ（Render）

リポジトリ直下の `render.yaml` を使って、RenderのBlueprintから自動構築できます。`GEMINI_API_KEY` はRenderダッシュボードの環境変数に設定してください（`render.yaml`には含まれません）。
