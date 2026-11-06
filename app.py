import streamlit as st
import pandas as pd
import re
import json
import time
from io import BytesIO
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

st.set_page_config(
    page_title="무재고 위탁판매 SEO 자동화 대시보드",
    page_icon="⚡",
    layout="wide"
)

class ProductSEOItem(BaseModel):
    row_id: int = Field(description="입력된 상품의 고유 순번(ID)")
    category: str = Field(description="네이버 표준 카테고리 (대분류 > 중분류 > 소분류 > 세분류)")
    title_basic: str = Field(description="기본 정석형 SEO 상품명")
    title_target: str = Field(description="타겟/상황 강조형 SEO 상품명")
    title_feature: str = Field(description="기능/스펙 강조형 SEO 상품명")
    tags: list[str] = Field(description="상품명 3종에 사용된 단어와 절대 중복되지 않는 네이버 태그사전 키워드 10개")

class BatchSEOResponse(BaseModel):
    results: list[ProductSEOItem]

def clean_raw_title(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = re.sub(r'\[.*?(무료|배송|특가|할인|당일|출고|이벤트|기획|모음|사은품).*?\]', ' ', text)
    text = re.sub(r'\(.*?(무료|배송|특가|할인|당일|출고|이벤트|기획|모음|사은품|컬러|색상).*?\)', ' ', text)
    stop_words = ['무료배송', '대박특가', '초특가', '특가', '모음전', '1+1', '할인', '당일발송', '유기농', '완치', '1위', '최고급']
    for word in stop_words:
        text = text.replace(word, ' ')
    text = re.sub(r'[^\w\s\d\.\*\-\/\+\%mlgLkgcmMM]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

def process_batch_gemini(client, model_name: str, batch_items: list[dict], status_box) -> list[dict]:
    system_instruction = """
    너는 네이버 스마트스토어 및 쿠팡 무재고 위탁판매 전문 'SEO 및 키워드 최적화 엔진'이다.
    제공된 상품 리스트를 분석하여 각각의 row_id에 맞춰 아래 규칙을 엄격히 준수한 JSON을 반환하라.

    [핵심 규칙]
    1. 불용어 및 홍보성 문구, 미검증 효능 단어를 완벽히 배제할 것.
    2. 카테고리: 네이버 표준 카테고리 [대분류 > 중분류 > 소분류 > 세분류] 체계로 매칭.
    3. SEO 상품명 공식: [타겟/용도] + [핵심 소재/기능] + [상품 유형] + [스펙/용량]
       - 한 상품명 내에 중복 단어가 없도록 띄어쓰기를 준수하여 3가지 버전(기본형, 타겟강조형, 기능강조형) 작성.
    4. 네이버 쇼핑 태그사전 키워드 10개:
       - 반드시 '네이버 쇼핑 태그 사전'에 실재하는 표준 명사형 태그로만 10개 구성.
       - [절대 규칙] 생성한 SEO 상품명 3종에 이미 포함된 단어는 태그 10개에 절대 포함하지 말 것(완벽한 중복 제거).
       - 타겟, 상황/용도, 계절/시즌, 이벤트/목적, 소재 관련 틈새 키워드로 구성.
    """
    prompt_text = "다음 상품 목록을 최적화하라:\n" + json.dumps(batch_items, ensure_ascii=False)

    for attempt in range(4):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt_text,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.2,
                    response_mime_type="application/json",
                    response_schema=BatchSEOResponse,
                )
            )
            parsed = json.loads(response.text)
            return parsed.get("results", [])
        except Exception as e:
            err_msg = str(e)
            if any(code in err_msg for code in ["503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED"]) and "402" not in err_msg:
                if attempt < 3:
                    wait_sec = 4 * (attempt + 1)
                    status_box.warning(f"⏳ 구글 서버 혼잡으로 {wait_sec}초 후 자동 재시도합니다... ({attempt + 1}/3회)")
                    time.sleep(wait_sec)
                    continue
            raise e

# 세션 상태 초기화 (데이터 안전성 보장: 새로고침 시 날아감 방지)
if "processed_df" not in st.session_state:
    st.session_state.processed_df = None
if "is_completed" not in st.session_state:
    st.session_state.is_completed = False

with st.sidebar:
    st.header("⚙️ 시스템 설정")
    
    default_key = st.secrets.get("GEMINI_API_KEY", "") if "GEMINI_API_KEY" in st.secrets else ""
    
    user_input_key = st.text_input(
        "🔑 내 Gemini API 키 입력",
        type="password",
        placeholder="선불 결제된 API 키 입력",
        help="선불 충전된 본인의 API 키를 입력하면 최우선 적용됩니다."
    )
    
    if user_input_key.strip():
        api_key = user_input_key.strip()
        st.success("✅ 사용자 전용 API 키 적용됨")
    elif default_key:
        api_key = default_key
        st.info("🟢 기본 시스템 API 키 작동 중")
    else:
        api_key = ""
        st.warning("⚠️ API 키를 입력해주세요.")

    model_choice = st.selectbox(
        "🤖 AI 엔진 선택",
        options=["gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-2.0-flash"],
        index=0,
        help="서버 혼잡(503) 시 lite 모델로 변경하세요."
    )
        
    # 토큰 효율과 속도를 위해 기본값을 20개로 상향하여 프롬프트 반복 호출 비용 대폭 절감
    batch_size = st.slider(
        "📦 묶음 처리 단위", 
        min_value=10, max_value=30, value=20, step=5,
        help="한 번에 처리할 상품 수 (20개 설정 시 입력 토큰 비용 및 왕복 횟수가 대폭 절감됩니다)"
    )
    st.markdown("---")
    st.caption("by **Quantum CJ**")

st.title("⚡ 무재고 위탁판매 SEO·태그·카테고리 자동화 대시보드")
st.markdown("도매처 원본 엑셀을 업로드하면 최적화된 상품명 3종과 태그 10개가 안전하게 추가됩니다.")

uploaded_file = st.file_uploader("📂 1단계: 도매처 원본 엑셀/CSV 파일을 여기에 끌어다 놓으세요", type=["xlsx", "xls", "csv"])

if uploaded_file:
    try:
        if uploaded_file.name.endswith('.csv'):
            try:
                df = pd.read_csv(uploaded_file, encoding='utf-8')
            except UnicodeDecodeError:
                uploaded_file.seek(0)
                df = pd.read_csv(uploaded_file, encoding='cp949')
        else:
            df = pd.read_excel(uploaded_file)
    except Exception as e:
        st.error(f"파일을 읽는 중 오류가 발생했습니다: {e}")
        st.stop()

    st.success(f"✅ 파일 확인 완료! 총 **{len(df):,}개**의 상품이 들어있습니다.")

    col1, col2 = st.columns([1, 2])
    with col1:
        default_idx = 0
        for idx, col_name in enumerate(df.columns):
            if any(k in str(col_name) for k in ['상품명', '제품명', '품명', '이름', 'title', 'name']):
                default_idx = idx
                break
        target_col = st.selectbox("🎯 2단계: 원본 '상품명'이 적힌 열을 선택하세요", df.columns, index=default_idx)

    with col2:
        st.write("📋 **선택한 열 미리보기 (상위 3개)**")
        st.dataframe(df[[target_col]].head(3), use_container_width=True)

    if st.button("🚀 3단계: AI SEO 일괄 최적화 시작", type="primary", use_container_width=True):
        if not api_key:
            st.error("⚠️ 왼쪽 설정창에 Gemini API 키를 먼저 입력해주세요.")
            st.stop()

        client = genai.Client(api_key=api_key, http_options={'api_version': 'v1beta'})
        total_rows = len(df)
        results_map = {}
        
        progress_bar = st.progress(0)
        status_text = st.empty()

        cleaned_items = []
        for idx, raw_val in enumerate(df[target_col]):
            cleaned_str = clean_raw_title(str(raw_val))
            cleaned_items.append({"row_id": idx, "raw_title": cleaned_str})

        total_batches = (total_rows + batch_size - 1) // batch_size
        start_time = time.time()

        success_flag = True
        for b_idx in range(total_batches):
            batch_slice = cleaned_items[b_idx * batch_size : (b_idx + 1) * batch_size]
            status_text.info(f"⏳ AI가 상품명을 다듬고 있습니다... ({b_idx + 1}/{total_batches} 구간 처리 중)")
            
            try:
                batch_res = process_batch_gemini(client, model_choice, batch_slice, status_text)
                for item in batch_res:
                    results_map[item['row_id']] = item
            except Exception as e:
                st.error(f"⚠️ {b_idx + 1}구간 처리 실패: {e}")
                success_flag = False
                break

            progress_bar.progress((b_idx + 1) / total_batches)
            time.sleep(0.8)

        if success_flag:
            df['[AI] 1차정제_상품명'] = [item['raw_title'] for item in cleaned_items]
            df['[AI] 추천_카테고리'] = [results_map.get(i, {}).get('category', '미분류') for i in range(total_rows)]
            df['[AI] SEO상품명_기본형'] = [results_map.get(i, {}).get('title_basic', '') for i in range(total_rows)]
            df['[AI] SEO상품명_타겟형'] = [results_map.get(i, {}).get('title_target', '') for i in range(total_rows)]
            df['[AI] SEO상품명_기능형'] = [results_map.get(i, {}).get('title_feature', '') for i in range(total_rows)]
            df['[AI] 네이버태그_10개'] = [", ".join(results_map.get(i, {}).get('tags', [])) for i in range(total_rows)]

            st.session_state.processed_df = df
            st.session_state.is_completed = True
            
            elapsed = round(time.time() - start_time, 1)
            status_text.success(f"🎉 총 {total_rows:,}개 상품 최적화 완료! ({elapsed}초 소요)")

if st.session_state.is_completed and st.session_state.processed_df is not None:
    st.subheader("📊 변환 결과 확인 (상위 10개)")
    result_df = st.session_state.processed_df
    preview_cols = [c for c in result_df.columns if c.startswith('[AI]') or c == result_df.columns[0]]
    st.dataframe(result_df.head(10), use_container_width=True)

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        result_df.to_excel(writer, index=False, sheet_name='SEO최적화완료')
    excel_data = output.getvalue()

    st.download_button(
        label="📥 4단계: 완성된 엑셀 파일 다운로드 (.xlsx)",
        data=excel_data,
        file_name=f"SEO최적화완료_{uploaded_file.name if uploaded_file else 'result'}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        use_container_width=True
    )
