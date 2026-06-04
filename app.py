import streamlit as st
import pandas as pd
import google.generativeai as genai
import plotly.express as px
import re
import io

# --- 画面の基本設定 ---
st.set_page_config(page_title="医療コンサルデータ分析AI", layout="wide")
st.title("生データ自動連動・追加分析チャット (ダッシュボード最適化版)")
st.write("「金額」列を基準に売上を算出。単位を「万円」に最適化し、小数点以下を切り捨てて日本語で可視化。")

# --- APIキーの埋め込み ---
YOUR_API_KEY = "AIzaSyCPeYyfVauZ-9YD1c2EyMJ0yB-ghFHcxyg" 

# --- セキュリティ設定 ---
st.sidebar.markdown("### 設定")
if YOUR_API_KEY == "ここにあなたのAPIキーを貼り付ける" or YOUR_API_KEY == "":
    st.sidebar.warning("コード内の YOUR_API_KEY を実際のものに書き換えてください。")
    api_key = st.sidebar.text_input("または、ここにAPI Keyを入力", type="password")
else:
    st.sidebar.success("APIキーは自動認証されている。")
    api_key = YOUR_API_KEY

# --- データ入力エリア ---
st.markdown("### 📊 データの連動（どちらか片方に入力してください）")
col_file, col_url = st.columns(2)

df = None

with col_file:
    uploaded_file = st.file_uploader("【ルートA】CSVファイルをアップロード", type=["csv"])

with col_url:
    sheet_url = st.text_input(
        "【ルートB】Googleスプレッドシートの共有URLを入力",
        placeholder="https://docs.google.com/spreadsheets/d/.../edit?usp=sharing"
    )

# --- データ読み込みロジック ---
if uploaded_file is not None:
    try:
        with st.spinner("ローカルファイルを解析中..."):
            file_bytes = uploaded_file.read()
            try:
                raw_text = file_bytes.decode('cp932')
            except Exception:
                raw_text = file_bytes.decode('utf-8', errors='replace')
            
            string_data = io.StringIO(raw_text, newline='')
            df = pd.read_csv(
                string_data, 
                sep=None, 
                engine='python',
                quoting=0,
                on_bad_lines='skip'
            )
    except Exception as e:
        st.error(f"ファイル読み込み中にエラーが発生した: {e}")

elif sheet_url:
    try:
        with st.spinner("クラウドからデータを同期中..."):
            if "/edit" in sheet_url:
                csv_url = sheet_url.split("/edit")[0] + "/export?format=csv"
            else:
                csv_url = sheet_url
            df = pd.read_csv(csv_url)
    except Exception as e:
        st.error(f"スプレッドシートからの読み込み中にエラーが発生した: {e}")

# --- データが正常に読み込めた後の共通処理 ---
if df is not None:
    try:
        # 列名とデータのクレンジング
        df.columns = df.columns.astype(str).str.strip().str.replace('"', '')
        for col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.replace('"', '')

        # --- 【修正】「金額」列の特定と処理 ---
        if '金額' in df.columns:
            target_val_col = '金額'
        else:
            # 万が一「金額」という名前の列がない場合のフォールバック
            target_val_col = None
            for col in df.columns:
                if '金額' in col or '売上' in col:
                    target_val_col = col
                    break
            if not target_val_col:
                target_val_col = df.columns[-1]

        # 日付・月列の抽出
        target_date_col = None
        for col in df.columns:
            if any(k in col for k in ['月', '日', '日付', '期間', '年度']):
                target_date_col = col
                break

        # 症例・処置・分類列の抽出（横軸を数字コードではなく「名称」にするため）
        target_cat_col = None
        # まずは明確に「名称」「内容」「病名」などを含む列を探す
        for col in df.columns:
            if any(k in col for k in ['内容', '処置', '疾患', '病名', '名称', '項目名', '分類', '品名']):
                # 数字コードらしき列（IDやCD、NO）は除外する
                if not any(x in col.upper() for x in ['ID', 'CD', 'NO', 'コード']):
                    target_cat_col = col
                    break
        if not target_cat_col:
            target_cat_col = df.columns[0]

        # 金額数値のクレンジング処理（小数点以下切り捨て）
        def clean_to_int(val):
            if pd.isna(val) or val in ['nan', 'None', '', '未入力']:
                return 0
            cleaned = re.sub(r'[^\d\.\-]', '', str(val))
            if cleaned == '' or cleaned == '-':
                return 0
            try:
                # 一度floatにしてからintに変換（小数点以下切り捨て）
                return int(float(cleaned))
            except ValueError:
                return 0

        # 計算用の売上列（円単位・整数）を作成
        df['__売上高_円'] = df[target_val_col].apply(clean_to_int)
        
        # 月データの表記揺れを統一
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

        st.success(f"病院データの同期に成功した ({len(df)} 行の全データを正常検出)")
        
        col1, col2 = st.columns([6, 4])

        with col1:
            st.subheader("📊 経営データ・ダッシュボード")
            
            tab1, tab2, tab3 = st.tabs(["💰 項目別売上（TOP10）", "📈 月次売上推移", "🩺 症例・処置件数内訳"])
            
            with tab1:
                st.markdown(f"### 各{target_cat_col}ごとの売上合計")
                df_grouped = df.groupby(target_cat_col)['__売上高_円'].sum().reset_index()
                
                # 金額ベースで「万円」単位に変換（小数点以下切り捨て）
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
                
            with tab2:
                st.markdown("### 月ごとの全体売上推移（棒グラフ）")
                df_month_sales = df.groupby('__対象月')['__売上高_円'].sum().reset_index()
                df_month_sales = df_month_sales.sort_values(by='__対象月')
                
                # 「万円」単位に変換（小数点以下切り捨て）
                df_month_sales['__売上高_万円'] = (df_month_sales['__売上高_円'] / 10000).astype(int)
                
                # 要望通り棒グラフ（bar）に変更
                fig2 = px.bar(
                    df_month_sales, 
                    x='__対象月', 
                    y='__売上高_万円', 
                    title="月次 総売上高推移（万円）",
                    labels={'__対象月': '対象月', '__売上高_万円': '総売上高（万円）'}
                )
                fig2.update_layout(yaxis_tickformat=',d')
                st.plotly_chart(fig2, use_container_width=True)
                
            with tab3:
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

            with st.expander("生データプレビュー（先頭50行）", expanded=False):
                st.dataframe(df.head(50))

        # --- 右カラム：チャット機能 ---
        with col2:
            st.subheader("AIコンサルタントと対話する")
            
            with st.form(key="chat_form", clear_on_submit=False):
                user_question = st.text_area(
                    "ここに質問を入力してください", 
                    placeholder="（例：売上が高い上位の処置名と、その月次トレンドを分析して）\n※入力後、Ctrl + Enter でも送信可能", 
                    height=100
                )
                send_btn = st.form_submit_button(label="生データを自動解析して質問する（無料）")

            if send_btn and user_question:
                if not api_key:
                    st.error("有効なAPIキーが設定されていない。")
                else:
                    with st.spinner("ダッシュボードの集計結果と生データから因果関係を抽出中..."):
                        try:
                            sales_summary = df.groupby(target_cat_col)['__売上高_円'].sum().sort_values(ascending=False).head(20)
                            sales_summary_wan = (sales_summary / 10000).astype(int).to_string()
                            
                            month_summary = df.groupby('__対象月')['__売上高_円'].sum()
                            month_summary_wan = (month_summary / 10000).astype(int).to_string()
                            
                            case_summary = df.groupby([target_date_col, target_cat_col]).size().sort_values(ascending=False).head(20).to_string() if target_date_col else "なし"
                            preview_rows = df.head(50).to_string()
                            
                            genai.configure(api_key=api_key)
                            model = genai.GenerativeModel('gemini-2.5-flash')
                            
                            prompt = f"あなたは医療経営コンサルタントである。以下のダッシュボード集計値（万円単位）および生データの構造に基づき、ユーザーの質問に対してプロフェッショナルな回答を行え。\n\n【集計データ：項目別売上高（万円）】\n{sales_summary_wan}\n\n【集計データ：月次総売上推移（万円）】\n{month_summary_wan}\n\n【集計データ：月次症例・処置件数トップ20】\n{case_summary}\n\n【生データプレビュー（先頭50行）】\n{preview_rows}\n\n【ユーザーの質問】\n{user_question}\n\n【出力フォーマット】\n1. 【回答】（売上や件数の具体的変動に対する直接的な分析）\n2. 【根拠】（タブ内の各グラフから読み取れる数値・トレンドの理由）\n3. 【コンサル提案】（季節変動や処置トレンドを踏まえた、次月のオペレーション・経営改善案）\n\n文章スタイルは「〜である」「〜だ」の常体で統一すること。"
                            
                            response = model.generate_content(prompt)
                            st.subheader("生成されたコンサル回答")
                            st.markdown(response.text)
                        except Exception as chat_err:
                            st.error(f"AI呼び出し中にエラーが発生した: {chat_err}")
                            
    except Exception as e:
        st.error(f"データ処理またはグラフ生成中にエラーが発生した: {e}")