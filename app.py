import streamlit as st
import pandas as pd
import google.generativeai as genai
import plotly.express as px
import re
import io
import os
import html
import json
import hashlib
from dotenv import load_dotenv

load_dotenv()

# --- 画面の基本設定 ---
st.set_page_config(page_title="医療コンサルデータ分析AI", layout="wide")

# --- パスワード認証（Secrets/.env に APP_PASSWORD が設定されている場合のみ有効） ---
try:
    _app_password = st.secrets.get("APP_PASSWORD", "")
except Exception:
    _app_password = ""
if not _app_password:
    _app_password = os.getenv("APP_PASSWORD", "")

if _app_password:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if not st.session_state.authenticated:
        st.title("🔒 ログイン")
        pw_input = st.text_input("パスワードを入力してください", type="password")
        if st.button("ログイン"):
            if pw_input == _app_password:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("パスワードが違います。")
        st.stop()

st.title("生データ自動連動・追加分析チャット（複数ファイル対応版）")
st.write("金額列を基準に売上を算出。単位を万円に最適化し、小数点以下を切り捨てて日本語で可視化。")

# --- チャットバブル専用のカスタムCSS（青と灰色の吹き出しを完全制御） ---
st.markdown("""
<style>
    /* ユーザーのメッセージ（青い吹き出しに白文字） */
    .user-bubble {
        background-color: #007aff;
        color: #ffffff;
        padding: 12px 16px;
        border-radius: 18px 18px 0px 18px;
        margin-bottom: 15px;
        max-width: 80%;
        margin-left: auto;
        box-shadow: 0 1px 2px rgba(0,0,0,0.1);
        font-family: sans-serif;
    }
    /* AIのメッセージ（灰色の吹き出しに黒文字） */
    .ai-bubble {
        background-color: #f1f1f2;
        color: #1c1c1e;
        padding: 12px 16px;
        border-radius: 18px 18px 18px 0px;
        margin-bottom: 15px;
        max-width: 80%;
        margin-right: auto;
        box-shadow: 0 1px 2px rgba(0,0,0,0.1);
        font-family: sans-serif;
        line-height: 1.5;
    }
    /* チャット表示エリアのスクロール枠固定 */
    .chat-scroll-container {
        max-height: 500px;
        overflow-y: auto;
        padding: 10px;
        border: 1px solid #e5e5ea;
        border-radius: 8px;
        background-color: #ffffff;
    }
    .user-label {
        text-align: right;
        font-size: 0.8rem;
        color: #8e8e93;
        margin-bottom: 2px;
        margin-right: 5px;
    }
    .ai-label {
        text-align: left;
        font-size: 0.8rem;
        color: #8e8e93;
        margin-bottom: 2px;
        margin-left: 5px;
    }
</style>
""", unsafe_allow_html=True)

# --- 会話履歴を保持するためのセッション状態の初期化 ---
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# --- APIキーの読み込み ---
# 優先順位: Streamlit Cloud の Secrets → ローカルの .env(GEMINI_API_KEY) → 手入力
st.sidebar.markdown("### 設定")
try:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.sidebar.success("APIキーは自動認証されている。")
except (KeyError, FileNotFoundError):
    api_key = os.getenv("GEMINI_API_KEY", "")
    if api_key:
        st.sidebar.success("APIキーは自動認証されている。")
    else:
        st.sidebar.warning("Secrets/.env に GEMINI_API_KEY が見つからない。")
        api_key = st.sidebar.text_input("ここにAPI Keyを入力", type="password")

if st.sidebar.button("🗑️ 会話履歴をクリア"):
    st.session_state.chat_history = []
    st.rerun()

st.sidebar.markdown("### 目標設定（任意）")
budget_target_man = st.sidebar.number_input("月間目標売上（万円）", min_value=0, value=0, step=10)

FILE_SOURCE_COL = "__ファイル名"
PRESET_FILE = "column_mapping_presets.json"


def _load_presets() -> dict:
    if os.path.exists(PRESET_FILE):
        try:
            with open(PRESET_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_presets(presets: dict) -> None:
    try:
        with open(PRESET_FILE, "w", encoding="utf-8") as f:
            json.dump(presets, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# --- データ入力エリア（複数ファイル / 複数URL対応） ---
st.markdown("### データの連動（どちらか片方に入力してください／複数ファイル・複数URL対応）")
col_file, col_url = st.columns(2)

with col_file:
    uploaded_files = st.file_uploader(
        "ルートA: CSVファイルをアップロード（複数選択可）",
        type=["csv"],
        accept_multiple_files=True
    )

with col_url:
    sheet_urls_text = st.text_area(
        "ルートB: Googleスプレッドシートの共有URL（複数の場合は改行で区切って入力）",
        placeholder="https://docs.google.com/spreadsheets/d/.../edit?usp=sharing\nhttps://docs.google.com/spreadsheets/d/.../edit?usp=sharing",
        height=100
    )


@st.cache_data(show_spinner=False)
def _read_csv_bytes(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """CSVバイト列を読み込み、出所列を付与したDataFrameを返す"""
    try:
        raw_text = file_bytes.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            raw_text = file_bytes.decode('cp932')
        except UnicodeDecodeError:
            raw_text = file_bytes.decode('utf-8', errors='replace')

    string_data = io.StringIO(raw_text, newline='')
    tmp_df = pd.read_csv(
        string_data,
        sep=None,
        engine='python',
        quoting=0,
        on_bad_lines='skip'
    )
    tmp_df[FILE_SOURCE_COL] = filename
    return tmp_df


@st.cache_data(show_spinner=False)
def _load_sheet_csv(csv_url: str) -> pd.DataFrame:
    return pd.read_csv(csv_url)


source_dfs = []
load_errors = []

# --- ルートA: 複数CSVファイルの読み込み ---
if uploaded_files:
    with st.spinner(f"{len(uploaded_files)}件のローカルファイルを解析中..."):
        for f in uploaded_files:
            try:
                file_bytes = f.read()
                tmp_df = _read_csv_bytes(file_bytes, f.name)
                source_dfs.append(tmp_df)
            except Exception as e:
                load_errors.append(f"「{f.name}」の読み込みに失敗した: {e}")

# --- ルートB: 複数スプレッドシートURLの読み込み ---
elif sheet_urls_text.strip():
    urls = [u.strip() for u in sheet_urls_text.splitlines() if u.strip()]
    with st.spinner(f"{len(urls)}件のスプレッドシートを同期中..."):
        for idx, url in enumerate(urls):
            try:
                if not re.match(r'^https://docs\.google\.com/spreadsheets/', url):
                    raise ValueError("Googleスプレッドシートの共有URL（https://docs.google.com/spreadsheets/...）を入力してください。")
                gid_match = re.search(r'[?#&]gid=(\d+)', url)
                if "/edit" in url:
                    csv_url = url.split("/edit")[0] + "/export?format=csv"
                    if gid_match:
                        csv_url += f"&gid={gid_match.group(1)}"
                else:
                    csv_url = url
                tmp_df = _load_sheet_csv(csv_url)
                tmp_df[FILE_SOURCE_COL] = f"スプレッドシート{idx + 1}"
                source_dfs.append(tmp_df)
            except Exception as e:
                load_errors.append(f"URL {idx + 1} の読み込みに失敗した: {e}")

for err in load_errors:
    st.error(err)

if not source_dfs and (uploaded_files or sheet_urls_text.strip()):
    st.warning("有効なデータを読み込めませんでした。ファイル形式やURLを確認してください。")

# --- 複数データソースを1つに統合 ---
df = None
if source_dfs:
    try:
        # 列構成が多少異なっていても、無い列はNaN埋めで統合される
        df = pd.concat(source_dfs, ignore_index=True, sort=False)
    except Exception as e:
        st.error(f"複数データの統合中にエラーが発生した（列構成が大きく異なる可能性がある）: {e}")

# --- データが正常に読み込めた後の共通処理 ---
if df is not None:
    try:
        # 出所列を保護しつつ、他の列は文字列クレンジング
        df.columns = df.columns.astype(str).str.strip().str.replace('"', '')
        for col in df.columns:
            if col == FILE_SOURCE_COL:
                continue
            df[col] = df[col].astype(str).str.strip().str.replace('"', '')

        n_sources = len(source_dfs)

        # --- ファイル別フィルター（複数データソースがある場合のみ表示） ---
        if n_sources > 1:
            st.markdown("### 対象データソースの絞り込み")
            all_sources = sorted(df[FILE_SOURCE_COL].dropna().unique().tolist())
            selected_sources = st.multiselect(
                "分析対象にするファイル／シートを選択（未選択の場合は全件が対象）",
                options=all_sources,
                default=all_sources
            )
            if selected_sources:
                df = df[df[FILE_SOURCE_COL].isin(selected_sources)]

        if '金額' in df.columns:
            target_val_col = '金額'
        else:
            target_val_col = None
            for col in df.columns:
                if col == FILE_SOURCE_COL:
                    continue
                if '金額' in col or '売上' in col:
                    target_val_col = col
                    break
            if not target_val_col:
                candidate_cols = [c for c in df.columns if c != FILE_SOURCE_COL]
                target_val_col = candidate_cols[-1]

        target_date_col = None
        for col in df.columns:
            if col == FILE_SOURCE_COL:
                continue
            if any(k in col for k in ['月', '日', '日付', '期間', '年度']):
                target_date_col = col
                break

        target_cat_col = None
        for col in df.columns:
            if col == FILE_SOURCE_COL:
                continue
            if any(k in col for k in ['内容', '処置', '疾患', '病名', '名称', '項目名', '分類', '品名']):
                if not any(x in col.upper() for x in ['ID', 'CD', 'NO', 'コード']):
                    target_cat_col = col
                    break
        if not target_cat_col:
            candidate_cols = [c for c in df.columns if c != FILE_SOURCE_COL]
            target_cat_col = candidate_cols[0]

        # --- 自動検出された列を確認・修正できるようにする（前回の選択は自動で記憶） ---
        candidate_cols = [c for c in df.columns if c != FILE_SOURCE_COL]
        col_signature = hashlib.md5("|".join(sorted(candidate_cols)).encode("utf-8")).hexdigest()
        presets = _load_presets()
        preset = presets.get(col_signature, {})

        with st.expander("🔍 自動検出された列（間違っていれば変更可能／同じ列構成なら次回から記憶されます）", expanded=False):
            default_val_col = preset.get("val_col", target_val_col)
            if default_val_col not in candidate_cols:
                default_val_col = target_val_col
            target_val_col = st.selectbox(
                "金額（売上）として扱う列", candidate_cols,
                index=candidate_cols.index(default_val_col)
            )

            date_options = ["（日付列なし）"] + candidate_cols
            default_date_col = preset.get("date_col", target_date_col)
            if default_date_col in candidate_cols:
                default_date_idx = date_options.index(default_date_col)
            else:
                default_date_idx = 0
            selected_date = st.selectbox("日付・月として扱う列", date_options, index=default_date_idx)
            target_date_col = None if selected_date == "（日付列なし）" else selected_date

            default_cat_col = preset.get("cat_col", target_cat_col)
            if default_cat_col not in candidate_cols:
                default_cat_col = target_cat_col
            target_cat_col = st.selectbox(
                "処置・疾患名などカテゴリとして扱う列", candidate_cols,
                index=candidate_cols.index(default_cat_col)
            )

        new_preset = {"val_col": target_val_col, "date_col": target_date_col, "cat_col": target_cat_col}
        if new_preset != preset:
            presets[col_signature] = new_preset
            _save_presets(presets)

        def clean_to_int(val):
            if pd.isna(val) or val in ['nan', 'None', '', '未入力']:
                return 0
            cleaned = re.sub(r'[^\d\.\-]', '', str(val))
            if cleaned == '' or cleaned == '-':
                return 0
            try:
                return int(float(cleaned))
            except ValueError:
                return 0

        def _is_unparseable(val):
            if pd.isna(val) or val in ['nan', 'None', '', '未入力']:
                return False
            cleaned = re.sub(r'[^\d\.\-]', '', str(val))
            if cleaned == '' or cleaned == '-':
                return True
            try:
                float(cleaned)
                return False
            except ValueError:
                return True

        df['__売上高_円'] = df[target_val_col].apply(clean_to_int)

        n_unparseable = int(df[target_val_col].apply(_is_unparseable).sum())
        if n_unparseable > 0:
            st.warning(f"⚠️「{target_val_col}」列のうち {n_unparseable}件 は数値として解析できず、0円として扱われました。")

        if target_date_col:
            def clean_month(val):
                val_str = str(val).strip()
                match = re.search(r'(\d{4}[-/]\d{1,2})|(\d{1,2}月)', val_str)
                if match:
                    return match.group(0)
                if '/' in val_str or '-' in val_str:
                    return val_str[:7]
                return val_str
            df['__対象月'] = df[target_date_col].apply(clean_month)
        else:
            df['__対象月'] = '未分類'

        if n_sources > 1:
            st.success(
                f"{n_sources}件のデータソースを統合し、合計{len(df)}行のデータを正常検出"
            )
        else:
            st.success(f"病院データの同期に成功した ({len(df)} 行の全データを正常検出)")

        col1, col2 = st.columns([6, 4])

        with col1:
            st.subheader("経営データ・ダッシュボード")

            tab_labels = ["項目別売上（TOP10）", "月次売上推移", "症例・処置件数内訳"]
            if n_sources > 1:
                tab_labels.append("ファイル別内訳")
            tabs = st.tabs(tab_labels)

            with tabs[0]:
                st.markdown(f"### 各{target_cat_col}ごとの売上合計")
                df_grouped = df.groupby(target_cat_col)['__売上高_円'].sum().reset_index()
                df_grouped['__売上高_万円'] = (df_grouped['__売上高_円'] / 10000).astype(int)
                df_grouped = df_grouped.sort_values(by='__売上高_万円', ascending=False).head(10)

                fig1 = px.bar(
                    df_grouped,
                    x=target_cat_col,
                    y='__売上高_万円',
                    title=f"{target_cat_col}別 売上上位トップ10（万円）",
                    labels={target_cat_col: f"{target_cat_col}", '__売上高_万円': '売上高（万円）'}
                )
                fig1.update_layout(yaxis_tickformat=',d')
                st.plotly_chart(fig1, use_container_width=True)

            with tabs[1]:
                st.markdown("### 月ごとの全体売上推移（棒グラフ）")
                df_month_sales = df.groupby('__対象月')['__売上高_円'].sum().reset_index()
                df_month_sales = df_month_sales.sort_values(by='__対象月')
                df_month_sales['__売上高_万円'] = (df_month_sales['__売上高_円'] / 10000).astype(int)

                fig2 = px.bar(
                    df_month_sales,
                    x='__対象月',
                    y='__売上高_万円',
                    title="月次 総売上高推移（万円）",
                    labels={'__対象月': '対象月', '__売上高_万円': '総売上高（万円）'}
                )
                fig2.update_layout(yaxis_tickformat=',d')
                if budget_target_man > 0:
                    fig2.add_hline(
                        y=budget_target_man, line_dash="dash", line_color="red",
                        annotation_text="目標", annotation_position="top left"
                    )
                st.plotly_chart(fig2, use_container_width=True)

                metric_cols = st.columns(2)
                if len(df_month_sales) >= 2:
                    latest = df_month_sales.iloc[-1]
                    prev = df_month_sales.iloc[-2]
                    if prev['__売上高_万円'] != 0:
                        mom_pct = (latest['__売上高_万円'] - prev['__売上高_万円']) / prev['__売上高_万円'] * 100
                        metric_cols[0].metric(
                            f"{latest['__対象月']} 売上（前月比）",
                            f"{latest['__売上高_万円']:,}万円",
                            f"{mom_pct:+.1f}%"
                        )
                if budget_target_man > 0 and len(df_month_sales) >= 1:
                    latest = df_month_sales.iloc[-1]
                    achievement = latest['__売上高_万円'] / budget_target_man * 100
                    metric_cols[1].metric(
                        f"{latest['__対象月']} 目標達成率",
                        f"{achievement:.1f}%",
                        f"目標 {budget_target_man:,}万円"
                    )

            with tabs[2]:
                st.markdown(f"### 月ごとの{target_cat_col}（症例・処置）の発生件数")
                df_counts = df.groupby(['__対象月', target_cat_col]).size().reset_index(name='件数')
                df_counts = df_counts.sort_values(['__対象月', '件数'], ascending=[True, False]).groupby('__対象月').head(5)

                fig3 = px.bar(
                    df_counts,
                    x='__対象月',
                    y='件数',
                    color=target_cat_col,
                    barmode='group',
                    title=f"月次 主要{target_cat_col}件数推移",
                    labels={'__対象月': '対象月', '件数': '発生件数（件）', target_cat_col: f'{target_cat_col}'}
                )
                st.plotly_chart(fig3, use_container_width=True)

            if n_sources > 1:
                with tabs[3]:
                    st.markdown("### ファイル／シート別の売上・件数内訳")
                    df_by_source = df.groupby(FILE_SOURCE_COL).agg(
                        売上高_円=('__売上高_円', 'sum'),
                        件数=('__売上高_円', 'count')
                    ).reset_index()
                    df_by_source['売上高_万円'] = (df_by_source['売上高_円'] / 10000).astype(int)

                    fig4 = px.bar(
                        df_by_source,
                        x=FILE_SOURCE_COL,
                        y='売上高_万円',
                        title="データソース別 売上高（万円）",
                        labels={FILE_SOURCE_COL: 'データソース', '売上高_万円': '売上高（万円）'}
                    )
                    fig4.update_layout(yaxis_tickformat=',d')
                    st.plotly_chart(fig4, use_container_width=True)

                    st.dataframe(
                        df_by_source[[FILE_SOURCE_COL, '売上高_万円', '件数']],
                        use_container_width=True
                    )

            with st.expander("生データプレビュー（先頭50行）", expanded=False):
                st.dataframe(df.head(50))

            st.download_button(
                "📥 クレンジング済みデータをCSVでダウンロード",
                data=df.to_csv(index=False).encode('utf-8-sig'),
                file_name="cleaned_data.csv",
                mime="text/csv"
            )

        # --- 右カラム：チャット機能（履歴スクロール＆カスタムデザイン対応） ---
        with col2:
            st.subheader("AIコンサルタントと対話する")

            # 1. 過去の会話ログをスクロールコンテナ形式で出力
            st.markdown('<div class="chat-scroll-container">', unsafe_allow_html=True)
            for chat in st.session_state.chat_history:
                safe_content = html.escape(chat["content"]).replace("\n", "<br>")
                if chat["role"] == "user":
                    st.markdown(f'<div class="user-label">あなた</div><div class="user-bubble">{safe_content}</div>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div class="ai-label">AIコンサルタント</div><div class="ai-bubble">{safe_content}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

            if st.session_state.chat_history:
                md_lines = []
                for chat in st.session_state.chat_history:
                    speaker = "あなた" if chat["role"] == "user" else "AIコンサルタント"
                    md_lines.append(f"### {speaker}\n\n{chat['content']}\n")
                chat_md = "\n---\n\n".join(md_lines)
                st.download_button(
                    "📥 会話履歴をMarkdownでダウンロード",
                    data=chat_md.encode('utf-8'),
                    file_name="ai_consultation_log.md",
                    mime="text/markdown"
                )

            # 2. 質問入力用フォーム
            with st.form(key="chat_form", clear_on_submit=True):
                user_question = st.text_area(
                    "ここに質問を入力してください",
                    placeholder="（例：売上が高い上位の処置名と、その月次トレンドを分析して）\n※入力後、Ctrl + Enter でも送信可能",
                    height=100
                )
                send_btn = st.form_submit_button(label="生データを自動解析して質問する")

            # 3. 送信時のアクション
            if send_btn and user_question:
                if not api_key:
                    st.error("有効なAPIキーが設定されていない。")
                else:
                    try:
                        # 履歴を即座にUIへ反映させるために、まずユーザーの発言を保存
                        st.session_state.chat_history.append({"role": "user", "content": user_question})

                        # AIに送信するコンテキスト情報の組み立て
                        sales_summary = df.groupby(target_cat_col)['__売上高_円'].sum().sort_values(ascending=False).head(20)
                        sales_summary_wan = (sales_summary / 10000).astype(int).to_string()

                        month_summary = df.groupby('__対象月')['__売上高_円'].sum()
                        month_summary_wan = (month_summary / 10000).astype(int).to_string()

                        case_summary = df.groupby(['__対象月', target_cat_col]).size().sort_values(ascending=False).head(20).to_string() if target_date_col else "なし"
                        preview_rows = df.head(50).to_string()

                        # データソース情報（複数ファイル統合時のみ）
                        if n_sources > 1:
                            source_summary = df.groupby(FILE_SOURCE_COL)['__売上高_円'].sum()
                            source_summary_wan = (source_summary / 10000).astype(int).to_string()
                            source_context = f"【統合データソース数】{n_sources}件\n【データソース別売上高（万円）】\n{source_summary_wan}\n\n"
                        else:
                            source_context = ""

                        # 過去の文脈もAIに引き継がせるために直近数件の会話をプロンプトに統合
                        history_context = ""
                        for h in st.session_state.chat_history[-5:-1]:  # 直近のやり取りを最大4件抽入
                            history_context += f"{'ユーザー' if h['role']=='user' else 'AI'}: {h['content']}\n"

                        genai.configure(api_key=api_key)
                        model = genai.GenerativeModel('gemini-flash-latest')

                        prompt = f"あなたは医療経営コンサルタントである。以下のダッシュボード集計値（万円単位）および生データの構造、そしてこれまでの会話履歴に基づき、ユーザーの質問に対してプロフェッショナルな回答を行え。\n\n【これまでの会話履歴】\n{history_context}\n\n{source_context}【集計データ：項目別売上高（万円）】\n{sales_summary_wan}\n\n【集計データ：月次総売上推移（万円）】\n{month_summary_wan}\n\n【集計データ：月次症例・処置件数トップ20】\n{case_summary}\n\n【生データプレビュー（先頭50行）】\n{preview_rows}\n\n【ユーザーの新しい質問】\n{user_question}\n\n【出力フォーマット】\n1. 【回答】（売上や件数の具体的変動に対する直接的な分析）\n2. 【根拠】（タブ内の各グラフから読み取れる数値・トレンドの理由）\n3. 【コンサル提案】（季節変動や処置トレンドを踏まえた、次月のオペレーション・経営改善案）\n\n文章スタイルは「〜である」「〜だ」の常体で統一すること。"

                        placeholder = st.empty()
                        full_response = ""
                        for chunk in model.generate_content(prompt, stream=True):
                            if chunk.text:
                                full_response += chunk.text
                                safe_partial = html.escape(full_response).replace("\n", "<br>")
                                placeholder.markdown(
                                    f'<div class="ai-label">AIコンサルタント</div><div class="ai-bubble">{safe_partial}▌</div>',
                                    unsafe_allow_html=True
                                )

                        if not full_response:
                            raise ValueError("AIからの応答が空でした（安全フィルタ等でブロックされた可能性がある）。")

                        # AIの回答を履歴に保存して画面をリライト
                        st.session_state.chat_history.append({"role": "model", "content": full_response})
                        st.rerun()  # 履歴を最新状態で再描画

                    except Exception as chat_err:
                        st.error(f"AI呼び出し中にエラーが発生した: {chat_err}")

    except Exception as e:
        st.error(f"データ処理またはグラフ生成中にエラーが発生した: {e}")
