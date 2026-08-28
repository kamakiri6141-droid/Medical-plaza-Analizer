# 医療経営データ分析システム

病院・クリニックの生データ（CSV/Excel、複数ファイル・複数スプレッドシート対応）を取り込み、売上・症例件数・年次サマリー表を自動集計し、統計的な異常検知とAIコンサルタント（Gemini）による分析を行うStreamlitアプリです。

## ローカル実行

```
pip install -r requirements.txt
streamlit run app.py
```

`GEMINI_API_KEY` を `.env`（ローカル）に設定してください。

## データの永続化（Supabase・任意）

`SUPABASE_URL` と `SUPABASE_KEY` を設定すると、アップロードしたデータをSupabase上に保存し、次回以降は自動で読み込めるようになります（未設定の場合はこれまで通り、アップロードのたびに集計するだけの動作です）。

1. [supabase.com](https://supabase.com) で新規プロジェクトを作成
2. プロジェクトの Settings → API から `Project URL`（→ `SUPABASE_URL`）と `service_role` キー（→ `SUPABASE_KEY`）を取得
3. SQL Editor で以下を実行してテーブルを作成

```sql
create table if not exists upload_log (
  id bigint generated always as identity primary key,
  clinic_id text not null,
  file_source text not null,
  file_hash text not null,
  kind text not null,
  uploaded_at timestamptz not null default now(),
  unique (clinic_id, file_hash, kind)
);

create table if not exists uploads (
  id bigint generated always as identity primary key,
  clinic_id text not null,
  file_source text not null,
  kind text not null,
  file_hash text not null,
  row_data jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists uploads_clinic_kind_idx on uploads (clinic_id, kind);
create index if not exists uploads_file_hash_idx on uploads (clinic_id, file_hash, kind);
```

4. `.env`（ローカル）またはSecrets（デプロイ先）に `SUPABASE_URL` / `SUPABASE_KEY` を設定

`service_role` キーはサーバー側（Streamlitの実行環境）でのみ使われ、ブラウザには渡らないため安全です。サイドバーの「施設名」を変えることで、将来的に複数施設のデータを分けて保存できます。

## デプロイ（Render）

リポジトリ直下の `render.yaml` を使って、RenderのBlueprintから自動構築できます。`GEMINI_API_KEY` はRenderダッシュボードの環境変数に設定してください（`render.yaml`には含まれません）。
