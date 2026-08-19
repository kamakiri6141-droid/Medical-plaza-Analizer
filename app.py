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
import csv
import gc
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
        st.title("ログイン")
        pw_input = st.text_input("パスワードを入力してください", type="password")
        if st.button("ログイン"):
            if pw_input == _app_password:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("パスワードが違います。")
        st.stop()

# --- 全体デザイン用CSS（読みやすさ改善：日本語フォント・余白・コントラスト） ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Noto Sans JP', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    h1, h2, h3 { letter-spacing: 0.02em; }
    section.main > div { padding-top: 1rem; }

    /* ユーザーのメッセージ（青い吹き出しに白文字） */
    .user-bubble {
        background-color: #2c4a63;
        color: #ffffff;
        padding: 12px 16px;
        border-radius: 18px 18px 0px 18px;
        margin-bottom: 15px;
        max-width: 85%;
        margin-left: auto;
        box-shadow: 0 1px 2px rgba(0,0,0,0.1);
        font-size: 0.95rem;
        line-height: 1.6;
    }
    /* AIのメッセージ（灰色の吹き出しに黒文字） */
    .ai-bubble {
        background-color: #f1f1f2;
        color: #1c1c1e;
        padding: 12px 16px;
        border-radius: 18px 18px 18px 0px;
        margin-bottom: 15px;
        max-width: 90%;
        margin-right: auto;
        box-shadow: 0 1px 2px rgba(0,0,0,0.1);
        font-size: 0.95rem;
        line-height: 1.7;
    }
    /* チャット表示エリアのスクロール枠固定 */
    .chat-scroll-container {
        max-height: 520px;
        overflow-y: auto;
        padding: 14px;
        border: 1px solid #e5e5ea;
        border-radius: 10px;
        background-color: #ffffff;
    }
    .user-label {
        text-align: right;
        font-size: 0.75rem;
        font-weight: 500;
        color: #8e8e93;
        margin-bottom: 3px;
        margin-right: 5px;
    }
    .ai-label {
        text-align: left;
        font-size: 0.75rem;
        font-weight: 500;
        color: #8e8e93;
        margin-bottom: 3px;
        margin-left: 5px;
    }
    .anomaly-card {
        background-color: #f7f5f1;
        border-left: 4px solid #8a6d3b;
        border-radius: 6px;
        padding: 10px 14px;
        margin-bottom: 10px;
        font-size: 0.92rem;
        line-height: 1.6;
        color: #3a3a3a;
    }
</style>
""", unsafe_allow_html=True)

st.title("医療経営データ分析システム")
st.caption("複数ファイル・複数シートのデータを統合し、見落とされやすい変化をダッシュボードとAIが自動的に検出します。")

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

if st.sidebar.button("会話履歴を削除", use_container_width=True):
    st.session_state.chat_history = []
    st.rerun()

st.sidebar.divider()
st.sidebar.markdown("### 目標設定（任意）")
budget_target_man = st.sidebar.number_input("月間目標売上（万円）", min_value=0, value=0, step=10)

st.sidebar.divider()
st.sidebar.markdown("### 自動検知の感度")
anomaly_threshold = st.sidebar.slider(
    "変動検知のしきい値（前月比 ±%）", min_value=20, max_value=100, value=50, step=10,
    help="この数値を超えて変動した項目を、注意すべき変化として自動的に検出します。数値を小さくするほど検出範囲は広がります。"
)

FILE_SOURCE_COL = "__ファイル名"
MUTED_PALETTE = ["#3b5c78", "#4a7d72", "#7c8a4a", "#a6803d", "#a25c42", "#8a4a5c", "#6b5480", "#55507a"]
PRESET_FILE = "column_mapping_presets.json"


def _stream_ai_response(prompt: str) -> str:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-flash-latest')
    placeholder = st.empty()
    full_response = ""
    try:
        for chunk in model.generate_content(prompt, stream=True):
            if chunk.text:
                full_response += chunk.text
                safe_partial = html.escape(full_response).replace("\n", "<br>")
                placeholder.markdown(
                    f'<div class="ai-label">AIコンサルタント</div><div class="ai-bubble">{safe_partial}▌</div>',
                    unsafe_allow_html=True
                )
    except Exception:
        # ストリーミング通信が環境要因（ネットワーク・認証周り）で失敗することがあるため、
        # その場合は通常呼び出し（非ストリーミング）に自動でフォールバックする
        full_response = ""
        placeholder.markdown(
            '<div class="ai-label">AIコンサルタント</div><div class="ai-bubble">回答を生成中…</div>',
            unsafe_allow_html=True
        )
        response = model.generate_content(prompt)
        if response.candidates and response.candidates[0].content.parts:
            full_response = response.text

    if not full_response:
        raise ValueError("AIからの応答が空でした（安全フィルタ等でブロックされた可能性がある）。")

    safe_full = html.escape(full_response).replace("\n", "<br>")
    placeholder.markdown(
        f'<div class="ai-label">AIコンサルタント</div><div class="ai-bubble">{safe_full}</div>',
        unsafe_allow_html=True
    )
    return full_response


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
with st.container(border=True):
    st.markdown("#### データの取り込み（いずれか一方を入力／複数ファイル・複数URL対応）")
    col_file, col_url = st.columns(2)

    with col_file:
        uploaded_files = st.file_uploader(
            "ルートA: CSVまたはExcelファイルをアップロード（複数選択可）",
            type=["csv", "xlsx", "xls"],
            accept_multiple_files=True,
            help="Excelファイルは先頭のシートのみを読み込みます。"
        )

    with col_url:
        sheet_urls_text = st.text_area(
            "ルートB: Googleスプレッドシートの共有URL（複数の場合は改行で区切って入力）",
            placeholder="https://docs.google.com/spreadsheets/d/.../edit?usp=sharing\nhttps://docs.google.com/spreadsheets/d/.../edit?usp=sharing",
            height=100
        )

with st.expander("年次サマリー表（年ごとの列×月ごとの行のクロス集計）を追加する（任意）", expanded=False):
    st.caption(
        "「2026／2025／2024」のように年が横に並び、各年の下に来院数・売上・客単価などの指標と前年比が続き、"
        "行が1月〜12月になっている集計表を想定しています。既存のデータと合わせて分析に利用されます。"
    )
    summary_files = st.file_uploader(
        "年次サマリー表ファイル（CSVまたはExcel、複数選択可）",
        type=["csv", "xlsx", "xls"],
        accept_multiple_files=True,
        key="summary_uploader"
    )


def _sniff_delimiter(sample_text: str) -> str:
    """先頭数行から区切り文字を軽量に推定する（低メモリなCエンジンを使うため）"""
    try:
        return csv.Sniffer().sniff(sample_text, delimiters=',\t;|').delimiter
    except csv.Error:
        return ','


@st.cache_data(show_spinner=False, max_entries=8)
def _read_csv_bytes(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """CSVまたはExcelのバイト列を読み込み、出所列を付与したDataFrameを返す"""
    if filename.lower().endswith(('.xlsx', '.xls')):
        tmp_df = pd.read_excel(io.BytesIO(file_bytes))
    else:
        try:
            raw_text = file_bytes.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                raw_text = file_bytes.decode('cp932')
            except UnicodeDecodeError:
                raw_text = file_bytes.decode('utf-8', errors='replace')

        sep = _sniff_delimiter(raw_text[:5000])
        try:
            # Cエンジンはpythonエンジンよりメモリ効率・速度がよいため優先する
            tmp_df = pd.read_csv(
                io.StringIO(raw_text, newline=''),
                sep=sep,
                engine='c',
                quoting=0,
                on_bad_lines='skip'
            )
        except Exception:
            # 区切り文字の推定が外れた場合のみ、遅いが柔軟なpythonエンジンにフォールバック
            tmp_df = pd.read_csv(
                io.StringIO(raw_text, newline=''),
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


def _parse_summary_number(val):
    """「3,871,913」「93.5%」のような表示形式の値を数値に変換する。変換できなければNoneを返す"""
    if pd.isna(val):
        return None
    s = str(val).strip()
    if s in ('', 'nan', 'None'):
        return None
    s = s.replace(',', '').replace('%', '').replace('¥', '').replace('円', '').strip()
    try:
        return float(s)
    except ValueError:
        return None


@st.cache_data(show_spinner=False)
def _parse_yearly_summary(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """年（列ブロック）×月（行）形式のKPIクロス集計表を、(年, 月)ごとの縦持ちデータに変換する"""
    if filename.lower().endswith(('.xlsx', '.xls')):
        raw = pd.read_excel(io.BytesIO(file_bytes), header=None)
    else:
        try:
            raw_text = file_bytes.decode('utf-8-sig')
        except UnicodeDecodeError:
            raw_text = file_bytes.decode('cp932', errors='replace')
        raw = pd.read_csv(io.StringIO(raw_text), header=None)

    # 4桁の年が2つ以上横に並んでいる行を「年ラベル行」とみなす（先頭5行の中から探す）
    year_row_idx = None
    for i in range(min(5, len(raw))):
        year_like = raw.iloc[i].astype(str).str.match(r'^(19|20)\d{2}(\.0)?$').sum()
        if year_like >= 2:
            year_row_idx = i
            break
    if year_row_idx is None:
        raise ValueError("年が横に並んだ見出し行が見つからない（年×月のクロス集計表を想定しています）")

    metric_row_idx = year_row_idx + 1
    years = raw.iloc[year_row_idx].ffill()
    metrics_row = raw.iloc[metric_row_idx]

    # 各列を (年, 指標名) に対応付ける。「前年比」は直前に出てきた指標名を引き継ぐ
    col_meta = []
    last_metric = None
    for c in range(raw.shape[1]):
        m = str(metrics_row.iloc[c]).strip()
        if m == '前年比':
            name = f"{last_metric}_前年比" if last_metric else None
        elif m in ('', 'nan', 'None', '月'):
            name = None
        else:
            name = m
            last_metric = m
        try:
            y_val = int(float(years.iloc[c]))
        except (ValueError, TypeError):
            y_val = None
        col_meta.append((y_val, name))

    records = []
    for r in range(metric_row_idx + 1, len(raw)):
        month_val = str(raw.iat[r, 0]).strip()
        if not re.match(r'^\d{1,2}月$', month_val):
            continue  # 「集計」行や空行はスキップ（合計は集計データから再計算できるため）
        for c in range(1, raw.shape[1]):
            y_val, name = col_meta[c]
            if y_val is None or not name:
                continue
            num = _parse_summary_number(raw.iat[r, c])
            if num is None:
                continue
            records.append({'年': y_val, '月': month_val, '指標': name, '値': num})

    if not records:
        raise ValueError("月次データ（1月〜12月の行）を検出できなかった")

    long_df = pd.DataFrame(records)
    wide = long_df.pivot_table(index=['年', '月'], columns='指標', values='値', aggfunc='first').reset_index()
    wide['月数'] = wide['月'].str.extract(r'(\d+)').astype(int)
    wide['年月'] = wide['年'].astype(str) + '-' + wide['月数'].astype(str).str.zfill(2)
    wide = wide.sort_values(['年', '月数']).drop(columns='月数').reset_index(drop=True)
    wide[FILE_SOURCE_COL] = filename
    return wide


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

# --- 年次サマリー表（任意）の読み込み ---
summary_df = None
if summary_files:
    summary_dfs = []
    with st.spinner(f"{len(summary_files)}件の年次サマリー表を解析中..."):
        for sf in summary_files:
            try:
                tmp = _parse_yearly_summary(sf.read(), sf.name)
                summary_dfs.append(tmp)
            except Exception as e:
                st.error(f"年次サマリー表「{sf.name}」の読み込みに失敗した: {e}")
    if summary_dfs:
        summary_df = pd.concat(summary_dfs, ignore_index=True, sort=False)
        summary_dfs.clear()

# --- 複数データソースを1つに統合 ---
df = None
if source_dfs:
    try:
        # 列構成が多少異なっていても、無い列はNaN埋めで統合される
        df = pd.concat(source_dfs, ignore_index=True, sort=False)
        source_dfs.clear()  # 個別ファイル分のメモリを早めに解放する
        gc.collect()
    except Exception as e:
        st.error(f"複数データの統合中にエラーが発生した（列構成が大きく異なる可能性がある）: {e}")

# --- データが正常に読み込めた後の共通処理 ---
if df is not None:
    try:
        # 出所列を保護しつつ、他の列は文字列クレンジング
        # （数値列まで文字列型に変換するとメモリを余計に消費するため、数値・真偽値型の列はそのまま保持する。
        # 　object型の列は、Excel由来でTrue/False等の非文字列値が混じっていることがあるため
        # 　.astype(str)で明示的に文字列化してから .str アクセサを使う）
        df.columns = df.columns.astype(str).str.strip().str.replace('"', '')
        for col in df.columns:
            if col == FILE_SOURCE_COL:
                continue
            if df[col].dtype == object:
                df[col] = df[col].astype(str).str.strip().str.replace('"', '')

        n_sources = len(source_dfs)

        # --- 見出しが複数行にまたがる集計表が誤って読み込まれていないかチェック ---
        data_cols = [c for c in df.columns if c != FILE_SOURCE_COL]
        unnamed_cols = [c for c in data_cols if c.startswith('Unnamed:')]
        if data_cols and len(unnamed_cols) / len(data_cols) > 0.5:
            st.warning(
                "アップロードされたファイルは、列名の多くが空欄（Unnamed）として読み込まれています。"
                "見出しが複数行にまたがる集計表（年ごとの列×月ごとの行のクロス集計など）を、"
                "1行1件の取引データ用の欄に入れている可能性があります。"
                "その場合は、上の「年次サマリー表を追加する」の欄からアップロードし直してください。"
            )

        # --- ファイル別フィルター（複数データソースがある場合のみ表示） ---
        if n_sources > 1:
            st.markdown("#### 対象データソースの絞り込み")
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

        with st.expander("自動検出された列（内容が異なる場合は変更可能。同じ列構成であれば次回以降も保持されます）", expanded=False):
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
            st.warning(f"「{target_val_col}」列のうち{n_unparseable}件は数値として解析できず、0円として処理しています。")

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

        # --- 統計的な異常検知（人が全件を目視しなくても気になる変化を拾い上げる） ---
        def _detect_anomalies(data: pd.DataFrame, cat_col: str, threshold_pct: int) -> list:
            found = []

            # (1) カテゴリ×月の前月比急変動
            pivot = data.groupby([cat_col, '__対象月'])['__売上高_円'].sum().reset_index()
            for cat, g in pivot.groupby(cat_col):
                g = g.sort_values('__対象月').reset_index(drop=True)
                if len(g) < 2:
                    continue
                g['prev'] = g['__売上高_円'].shift(1)
                for _, row in g.iterrows():
                    if pd.isna(row['prev']) or row['prev'] < 10000:
                        continue
                    change = (row['__売上高_円'] - row['prev']) / row['prev'] * 100
                    if abs(change) >= threshold_pct:
                        direction = "急増" if change > 0 else "急減"
                        found.append({
                            'severity': abs(change),
                            'detail': f"「{cat}」は{row['__対象月']}に前月比 {change:+.0f}%（{direction}）"
                                      f"： {int(row['prev'] / 10000):,}万円 → {int(row['__売上高_円'] / 10000):,}万円",
                        })

            # (2) カテゴリ内の行レベル外れ値（IQRベース）
            for cat, g in data.groupby(cat_col):
                vals = g['__売上高_円']
                if len(vals) < 5:
                    continue
                q1, q3 = vals.quantile(0.25), vals.quantile(0.75)
                iqr = q3 - q1
                if iqr <= 0:
                    continue
                upper = q3 + 1.5 * iqr
                outliers = g[vals > upper]
                for _, row in outliers.iterrows():
                    found.append({
                        'severity': (row['__売上高_円'] - upper) / upper * 100 if upper else 0,
                        'detail': f"「{cat}」に通常より突出した記録が1件： {int(row['__売上高_円'] / 10000):,}万円"
                                  f"（同カテゴリの目安上限 約{int(upper / 10000):,}万円）",
                    })

            found.sort(key=lambda x: -x['severity'])
            return found

        anomalies = _detect_anomalies(df, target_cat_col, anomaly_threshold)

        if n_sources > 1:
            st.success(
                f"{n_sources}件のデータソースを統合し、合計{len(df)}行のデータを正常検出"
            )
        else:
            st.success(f"病院データの同期に成功した ({len(df)} 行の全データを正常検出)")

        # --- KPIサマリー（詳細を見る前に全体像を一目で把握できるように） ---
        with st.container(border=True):
            kpi_cols = st.columns(4)
            kpi_cols[0].metric("総売上高", f"{int(df['__売上高_円'].sum() / 10000):,}万円")
            kpi_cols[1].metric("データ件数", f"{len(df):,}件")
            avg_val = df['__売上高_円'].mean()
            kpi_cols[2].metric("平均単価", f"{int(avg_val):,}円")
            kpi_cols[3].metric("自動検知件数", f"{len(anomalies)}件")

        col1, col2 = st.columns([6, 4])

        with col1:
            st.subheader("経営データダッシュボード")

            tab_labels = ["項目別売上（TOP10）", "月次売上推移", "症例・処置件数内訳", f"自動検知（{len(anomalies)}）"]
            if n_sources > 1:
                tab_labels.append("ファイル別内訳")
            tabs = st.tabs(tab_labels)

            with tabs[0]:
                st.markdown(f"##### 各{target_cat_col}ごとの売上合計")
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
                fig1.update_traces(marker_color=MUTED_PALETTE[0])
                fig1.update_layout(yaxis_tickformat=',d')
                st.plotly_chart(fig1, use_container_width=True)

            with tabs[1]:
                st.markdown("##### 月ごとの全体売上推移（棒グラフ）")
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
                fig2.update_traces(marker_color=MUTED_PALETTE[0])
                fig2.update_layout(yaxis_tickformat=',d')
                if budget_target_man > 0:
                    fig2.add_hline(
                        y=budget_target_man, line_dash="dash", line_color="#8a6d3b",
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
                st.markdown(f"##### 月ごとの{target_cat_col}（症例・処置）の発生件数")
                st.caption(f"件数が多い上位{len(MUTED_PALETTE)}項目に絞って表示しています（全項目を混在させると判読しづらいため）。")
                top_categories = df[target_cat_col].value_counts().head(len(MUTED_PALETTE)).index.tolist()
                df_counts = df[df[target_cat_col].isin(top_categories)].groupby(['__対象月', target_cat_col]).size().reset_index(name='件数')
                df_counts = df_counts.sort_values('__対象月')

                fig3 = px.line(
                    df_counts,
                    x='__対象月',
                    y='件数',
                    color=target_cat_col,
                    color_discrete_sequence=MUTED_PALETTE,
                    markers=True,
                    title=f"月次 主要{target_cat_col}件数推移（全期間トップ{len(MUTED_PALETTE)}）",
                    labels={'__対象月': '対象月', '件数': '発生件数（件）', target_cat_col: f'{target_cat_col}'}
                )
                fig3.update_layout(legend_title_text=target_cat_col)
                st.plotly_chart(fig3, use_container_width=True)

            with tabs[3]:
                st.markdown("##### 人が見落としがちな変化を統計的に自動検知")
                st.caption("前月比の急変動と、カテゴリ内で突出した記録（外れ値）を機械的にスキャンした結果です。サイドバーの「検知の感度」で調整できます。")
                if anomalies:
                    for a in anomalies[:30]:
                        st.markdown(f'<div class="anomaly-card">{a["detail"]}</div>', unsafe_allow_html=True)
                else:
                    st.info("しきい値を超える急変動・外れ値は検出されませんでした。")

            if n_sources > 1:
                with tabs[4]:
                    st.markdown("##### ファイル／シート別の売上・件数内訳")
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
                    fig4.update_traces(marker_color=MUTED_PALETTE[0])
                    fig4.update_layout(yaxis_tickformat=',d')
                    st.plotly_chart(fig4, use_container_width=True)

                    st.dataframe(
                        df_by_source[[FILE_SOURCE_COL, '売上高_万円', '件数']],
                        use_container_width=True
                    )

            with st.expander("生データプレビュー（先頭50行）", expanded=False):
                st.dataframe(df.head(50))

            st.download_button(
                "クレンジング済みデータをCSVでダウンロード",
                data=df.to_csv(index=False).encode('utf-8-sig'),
                file_name="cleaned_data.csv",
                mime="text/csv"
            )

        # --- 右カラム：チャット機能（履歴スクロール＆カスタムデザイン対応） ---
        with col2:
            st.subheader("AIコンサルタントとの対話")

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
                    "会話履歴をMarkdownでダウンロード",
                    data=chat_md.encode('utf-8'),
                    file_name="ai_consultation_log.md",
                    mime="text/markdown"
                )

            def _build_context():
                sales_summary = df.groupby(target_cat_col)['__売上高_円'].sum().sort_values(ascending=False).head(20)
                sales_summary_wan = (sales_summary / 10000).astype(int).to_string()

                month_summary = df.groupby('__対象月')['__売上高_円'].sum()
                month_summary_wan = (month_summary / 10000).astype(int).to_string()

                case_summary = df.groupby(['__対象月', target_cat_col]).size().sort_values(ascending=False).head(20).to_string() if target_date_col else "なし"
                preview_rows = df.head(50).to_string()

                if n_sources > 1:
                    source_summary = df.groupby(FILE_SOURCE_COL)['__売上高_円'].sum()
                    source_summary_wan = (source_summary / 10000).astype(int).to_string()
                    source_context = f"【統合データソース数】{n_sources}件\n【データソース別売上高（万円）】\n{source_summary_wan}\n\n"
                else:
                    source_context = ""

                anomaly_context = ""
                if anomalies:
                    top_anomalies = "\n".join(f"- {a['detail']}" for a in anomalies[:15])
                    anomaly_context = f"【統計的に自動検知された気になる変化】\n{top_anomalies}\n\n"

                summary_context = ""
                if summary_df is not None:
                    summary_text = summary_df.drop(columns=[FILE_SOURCE_COL], errors='ignore').to_string(index=False)
                    summary_context = (
                        "【年次サマリー表（来院数・新患数・売上・客単価と前年比、年×月）】\n"
                        f"{summary_text}\n\n"
                        "上記の年次サマリー表と、個々の処置・項目データを突き合わせて、"
                        "傾向の一致・乖離があれば言及すること。\n\n"
                    )

                return sales_summary_wan, month_summary_wan, case_summary, preview_rows, source_context, anomaly_context, summary_context

            # 2. 質問しなくても使える自動診断ボタン
            auto_diag_btn = st.button(
                "データ全体をAIに自動診断させる（質問の入力は不要です）",
                use_container_width=True,
                help="統計的な異常検知の結果を踏まえ、AIが人間では気づきにくい重要な変化・リスクを能動的に抽出します。"
            )
            if auto_diag_btn:
                if not api_key:
                    st.error("有効なAPIキーが設定されていない。")
                else:
                    try:
                        st.session_state.chat_history.append({
                            "role": "user",
                            "content": "【自動診断】データ全体から人が気づきにくい重要な変化・リスク・改善余地を洗い出してください。"
                        })
                        sales_summary_wan, month_summary_wan, case_summary, preview_rows, source_context, anomaly_context, summary_context = _build_context()
                        prompt = (
                            "あなたは経験豊富な医療経営コンサルタントである。以下は病院の経営データの集計値と、"
                            "統計的に自動検知された気になる変化のリストである。人間の担当者が日々の業務では気づきにくい"
                            "重要な兆候を、具体的な数字を根拠にしながら客観的に洗い出せ。\n\n"
                            f"{source_context}{anomaly_context}{summary_context}"
                            f"【集計データ：項目別売上高（万円）】\n{sales_summary_wan}\n\n"
                            f"【集計データ：月次総売上推移（万円）】\n{month_summary_wan}\n\n"
                            "【出力フォーマット】\n"
                            "1. 【最も注視すべき変化 TOP3】（具体的な数字を挙げて）\n"
                            "2. 【放置すると危険な兆候】（無ければ「特になし」と明記）\n"
                            "3. 【次の一手】（3つ以内、優先度順）\n\n"
                            "文章スタイルは「〜である」「〜だ」の常体で統一すること。"
                        )
                        full_response = _stream_ai_response(prompt)
                        st.session_state.chat_history.append({"role": "model", "content": full_response})
                        st.rerun()
                    except Exception as chat_err:
                        st.error(f"AI呼び出し中にエラーが発生した: {chat_err}")

            # 3. 質問入力用フォーム
            with st.form(key="chat_form", clear_on_submit=True):
                user_question = st.text_area(
                    "ここに質問を入力してください",
                    placeholder="（例：売上が高い上位の処置名と、その月次トレンドを分析して）\n※入力後、Ctrl + Enter でも送信可能",
                    height=100
                )
                send_btn = st.form_submit_button(label="生データを自動解析して質問する", use_container_width=True)

            # 4. 送信時のアクション
            if send_btn and user_question:
                if not api_key:
                    st.error("有効なAPIキーが設定されていない。")
                else:
                    try:
                        # 履歴を即座にUIへ反映させるために、まずユーザーの発言を保存
                        st.session_state.chat_history.append({"role": "user", "content": user_question})

                        sales_summary_wan, month_summary_wan, case_summary, preview_rows, source_context, anomaly_context, summary_context = _build_context()

                        # 過去の文脈もAIに引き継がせるために直近数件の会話をプロンプトに統合
                        history_context = ""
                        for h in st.session_state.chat_history[-5:-1]:  # 直近のやり取りを最大4件抽入
                            history_context += f"{'ユーザー' if h['role']=='user' else 'AI'}: {h['content']}\n"

                        prompt = f"あなたは医療経営コンサルタントである。以下のダッシュボード集計値（万円単位）および生データの構造、そしてこれまでの会話履歴に基づき、ユーザーの質問に対してプロフェッショナルな回答を行え。\n\n【これまでの会話履歴】\n{history_context}\n\n{source_context}{anomaly_context}{summary_context}【集計データ：項目別売上高（万円）】\n{sales_summary_wan}\n\n【集計データ：月次総売上推移（万円）】\n{month_summary_wan}\n\n【集計データ：月次症例・処置件数トップ20】\n{case_summary}\n\n【生データプレビュー（先頭50行）】\n{preview_rows}\n\n【ユーザーの新しい質問】\n{user_question}\n\n【出力フォーマット】\n1. 【回答】（売上や件数の具体的変動に対する直接的な分析）\n2. 【根拠】（タブ内の各グラフから読み取れる数値・トレンドの理由）\n3. 【コンサル提案】（季節変動や処置トレンドを踏まえた、次月のオペレーション・経営改善案）\n\n文章スタイルは「〜である」「〜だ」の常体で統一すること。"

                        full_response = _stream_ai_response(prompt)

                        # AIの回答を履歴に保存して画面をリライト
                        st.session_state.chat_history.append({"role": "model", "content": full_response})
                        st.rerun()  # 履歴を最新状態で再描画

                    except Exception as chat_err:
                        st.error(f"AI呼び出し中にエラーが発生した: {chat_err}")

    except Exception as e:
        st.error(f"データ処理またはグラフ生成中にエラーが発生した: {e}")

# --- 年次サマリー表の分析（年×月のクロス集計データが1件以上ある場合のみ表示） ---
if summary_df is not None:
    try:
        st.divider()
        st.subheader("年次KPIサマリー比較")
        st.caption("アップロードされた年次サマリー表を年ごとに重ねて表示し、同月内の年次比較ができるようにしています。")

        metric_cols_available = [
            c for c in summary_df.columns
            if c not in ('年', '月', '年月', FILE_SOURCE_COL) and not c.endswith('_前年比')
        ]
        yoy_alerts = []

        if metric_cols_available:
            # --- 概況（デフォルトで表示される軽量な自動分析） ---
            st.markdown("##### 概況")
            narrative_lines = []
            for metric in metric_cols_available:
                series = summary_df.dropna(subset=[metric]).sort_values('年月')
                if series.empty:
                    continue
                latest = series.iloc[-1]
                line = f"- **{metric}**：直近は{int(latest['年'])}年{latest['月']}で {latest[metric]:,.0f}"
                if len(series) >= 2:
                    prev_val = series.iloc[-2][metric]
                    if prev_val:
                        mom = (latest[metric] - prev_val) / prev_val * 100
                        line += f"（前月比 {mom:+.1f}%）"
                yoy_col_name = f"{metric}_前年比"
                if yoy_col_name in summary_df.columns and pd.notna(latest.get(yoy_col_name)):
                    line += f"、前年比 {latest[yoy_col_name]:.1f}%"
                narrative_lines.append(line)
            if narrative_lines:
                st.markdown("\n".join(narrative_lines))

            summary_tabs = st.tabs(metric_cols_available)
            for tab, metric in zip(summary_tabs, metric_cols_available):
                with tab:
                    plot_df = summary_df.dropna(subset=[metric]).copy()
                    plot_df['年'] = plot_df['年'].astype(str)
                    fig_s = px.line(
                        plot_df.sort_values('年月'),
                        x='月',
                        y=metric,
                        color='年',
                        markers=True,
                        color_discrete_sequence=MUTED_PALETTE,
                        title=f"{metric} の年次比較",
                        category_orders={'月': [f"{m}月" for m in range(1, 13)]},
                        labels={'月': '月', metric: metric, '年': '年'}
                    )
                    st.plotly_chart(fig_s, use_container_width=True)

            # 前年比の急変動を自動検知（人間が見落としがちな年次比較の変化）
            yoy_cols = [c for c in summary_df.columns if c.endswith('_前年比')]
            for _, row in summary_df.iterrows():
                for c in yoy_cols:
                    v = row[c]
                    if pd.isna(v):
                        continue
                    if v <= 85 or v >= 120:
                        base_metric = c.replace('_前年比', '')
                        yoy_alerts.append({
                            'severity': abs(v - 100),
                            'detail': f"{int(row['年'])}年{row['月']}の{base_metric}は前年比{v:.1f}%"
                        })
            if yoy_alerts:
                yoy_alerts.sort(key=lambda x: -x['severity'])
                st.markdown("##### 前年比の観点で注意すべき月")
                for a in yoy_alerts[:20]:
                    st.markdown(f'<div class="anomaly-card">{a["detail"]}</div>', unsafe_allow_html=True)

        with st.expander("年次サマリー表（変換後データ）を確認", expanded=False):
            st.dataframe(summary_df, use_container_width=True)

        st.download_button(
            "年次サマリー表（変換後）をCSVでダウンロード",
            data=summary_df.to_csv(index=False).encode('utf-8-sig'),
            file_name="yearly_summary_parsed.csv",
            mime="text/csv"
        )

        # --- 年次サマリー表についてのAIチャット ---
        # 1行1件の取引データ（df）も同時に読み込まれている場合は、そちらのAIチャットが
        # このサマリー表も含めて横断的に回答するため、ここでは重複して表示しない。
        if df is None:
            st.divider()
            st.subheader("AIコンサルタントとの対話（年次サマリー表について）")

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
                    "会話履歴をMarkdownでダウンロード",
                    data=chat_md.encode('utf-8'),
                    file_name="ai_consultation_log.md",
                    mime="text/markdown",
                    key="summary_chat_md_download"
                )

            def _build_summary_only_context():
                summary_text = summary_df.drop(columns=[FILE_SOURCE_COL], errors='ignore').to_string(index=False)
                alert_text = "\n".join(f"- {a['detail']}" for a in yoy_alerts[:15]) if yoy_alerts else "特になし"
                return summary_text, alert_text

            summary_auto_btn = st.button(
                "データ全体をAIに自動診断させる（質問の入力は不要です）",
                use_container_width=True,
                key="summary_auto_diag",
                help="前年比の急変動などの検知結果を踏まえ、AIが人間では気づきにくい重要な変化・リスクを能動的に抽出します。"
            )
            if summary_auto_btn:
                if not api_key:
                    st.error("有効なAPIキーが設定されていない。")
                else:
                    try:
                        st.session_state.chat_history.append({
                            "role": "user",
                            "content": "【自動診断】年次サマリー表から人が気づきにくい重要な変化・リスク・改善余地を洗い出してください。"
                        })
                        summary_text, alert_text = _build_summary_only_context()
                        prompt = (
                            "あなたは経験豊富な医療経営コンサルタントである。以下は病院・クリニックの年次KPIサマリー表"
                            "（年×月、来院数・新患数・売上・客単価などと前年比）である。人間の担当者が日々の業務では"
                            "気づきにくい重要な兆候を、具体的な数字を根拠にしながら客観的に洗い出せ。\n\n"
                            f"【年次サマリー表】\n{summary_text}\n\n"
                            f"【統計的に検知された前年比の注意点】\n{alert_text}\n\n"
                            "【出力フォーマット】\n"
                            "1. 【最も注視すべき変化 TOP3】（具体的な数字を挙げて）\n"
                            "2. 【放置すると危険な兆候】（無ければ「特になし」と明記）\n"
                            "3. 【次の一手】（3つ以内、優先度順）\n\n"
                            "文章スタイルは「〜である」「〜だ」の常体で統一すること。"
                        )
                        full_response = _stream_ai_response(prompt)
                        st.session_state.chat_history.append({"role": "model", "content": full_response})
                        st.rerun()
                    except Exception as chat_err:
                        st.error(f"AI呼び出し中にエラーが発生した: {chat_err}")

            with st.form(key="summary_chat_form", clear_on_submit=True):
                summary_question = st.text_area(
                    "ここに質問を入力してください",
                    placeholder="（例：来院数と客単価、どちらの改善を優先すべきか分析して）",
                    height=100,
                    key="summary_question_input"
                )
                summary_send_btn = st.form_submit_button(label="年次サマリー表について質問する", use_container_width=True)

            if summary_send_btn and summary_question:
                if not api_key:
                    st.error("有効なAPIキーが設定されていない。")
                else:
                    try:
                        st.session_state.chat_history.append({"role": "user", "content": summary_question})
                        summary_text, alert_text = _build_summary_only_context()

                        history_context = ""
                        for h in st.session_state.chat_history[-5:-1]:
                            history_context += f"{'ユーザー' if h['role']=='user' else 'AI'}: {h['content']}\n"

                        prompt = (
                            "あなたは医療経営コンサルタントである。以下の年次サマリー表とこれまでの会話履歴に基づき、"
                            "ユーザーの質問にプロフェッショナルな回答を行え。\n\n"
                            f"【これまでの会話履歴】\n{history_context}\n\n"
                            f"【年次サマリー表】\n{summary_text}\n\n"
                            f"【統計的に検知された前年比の注意点】\n{alert_text}\n\n"
                            f"【ユーザーの新しい質問】\n{summary_question}\n\n"
                            "文章スタイルは「〜である」「〜だ」の常体で統一すること。"
                        )
                        full_response = _stream_ai_response(prompt)
                        st.session_state.chat_history.append({"role": "model", "content": full_response})
                        st.rerun()
                    except Exception as chat_err:
                        st.error(f"AI呼び出し中にエラーが発生した: {chat_err}")

    except Exception as e:
        st.error(f"年次サマリー表の分析中にエラーが発生した: {e}")
