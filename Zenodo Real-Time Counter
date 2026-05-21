import streamlit as st
import requests
import re

# 웹 앱 타이틀 설정
st.set_page_config(page_title="Zenodo Real-Time Counter", page_icon="📊", layout="centered")

st.title("📊 Zenodo 실시간 1차 카운터 확인기")
st.markdown("제노도 웹페이지의 고장난 카운터 대신, **API 내부의 진짜 실시간 데이터**를 가져옵니다.")

# 사용자 입력창 (링크 또는 레코드 ID)
url_input = st.text_input(
    "Zenodo 논문 링크 또는 레코드 번호를 입력하세요:",
    placeholder="예: https://zenodo.org/records/18813202 또는 18813202"
)

if url_input:
    # 입력값에서 숫자(레코드 ID)만 추출하는 정규식
    record_id_match = re.search(r'\d+', url_input)
    
    if record_id_match:
        record_id = record_id_match.group()
        api_url = f"https://zenodo.org/api/records/{record_id}"
        
        with st.spinner("Zenodo 서버에서 진짜 데이터를 불러오는 중..."):
            try:
                response = requests.get(api_url)
                
                if response.status_size == 200:
                    data = response.json()
                    stats = data.get("stats", {})
                    metadata = data.get("metadata", {})
                    title = metadata.get("title", f"Record #{record_id}")
                    
                    st.success(f"✅ 연결 성공: **{title}**")
                    st.write("---")
                    
                    # 보기 좋게 4개의 레이아웃 구획으로 나누어 메트릭 표시
                    col1, col2 = st.columns(2)
                    col3, col4 = st.columns(2)
                    
                    with col1:
                        st.metric(label="📥 총 다운로드 수 (Downloads)", value=f"{stats.get('downloads', 0)} 회")
                    with col2:
                        st.metric(label="👥 고유 다운로드 수 (Unique)", value=f"{stats.get('unique_downloads', 0)} 명")
                    with col3:
                        st.metric(label="👀 총 조회수 (Views)", value=f"{stats.get('views', 0)} 회")
                    with col4:
                        st.metric(label="👤 고유 조회수 (Unique Views)", value=f"{stats.get('unique_views', 0)} 명")
                        
                    st.write("---")
                    # 현재 버전 전용 데이터도 추가로 확인 가능하게 배치
                    st.markdown("**현재 버전(Current Version) 통계**")
                    st.json({
                        "version_downloads": stats.get("version_downloads", 0),
                        "version_unique_downloads": stats.get("version_unique_downloads", 0),
                        "version_views": stats.get("version_views", 0),
                        "version_unique_views": stats.get("version_unique_views", 0)
                    })
                    
                else:
                    st.error(f"❌ 데이터를 가져오지 못했습니다. 레코드 번호를 확인해 주세요. (에러 코드: {response.status_code})")
            except Exception as e:
                st.error(f"⚠️ 오류가 발생했습니다: {e}")
    else:
        st.warning("⚠️ 올바른 URL 형식이나 레코드 번호를 입력해 주세요.")
