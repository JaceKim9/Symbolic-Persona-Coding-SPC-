import streamlit as st
import requests
import re

# Set web app configuration
st.set_page_config(page_title="Zenodo Real-Time Counter", page_icon="📊", layout="centered")

st.title("📊 Zenodo Real-Time Counter")
st.markdown("Fetch the **actual primary metrics** directly from Zenodo's internal API, bypassing web interface lag or display issues.")

# User input field
url_input = st.text_input(
    "Enter Zenodo Record URL or Record ID:",
    placeholder="e.g., https://zenodo.org/records/18813202 or 18813202"
)

if url_input:
    # 앞뒤 공백 완전히 제거
    clean_input = url_input.strip()
    record_id = None
    
    # [개선] 1. URL에서 records/뒤에 오는 숫자만 정확하게 추출
    url_match = re.search(r'records/(\d+)', clean_input)
    
    # [추가] 혹시 DOI 형태로 입력했을 경우 (zenodo.org/record/가 아니라 doi인 경우 대비)
    doi_match = re.search(r'zenodo\.(\d+)', clean_input)
    
    if url_match:
        record_id = url_match.group(1)
    elif doi_match:
        record_id = doi_match.group(1)
    else:
        # 3. URL 구조가 아니고 사용자가 순수하게 '숫자만' 입력했을 때 (8자리 내외)
        # DOI의 10.5281 패턴과 겹치지 않도록 완전히 독립된 7~9자리 숫자만 매칭
        pure_id_match = re.search(r'\b\d{7,9}\b', clean_input)
        if pure_id_match:
            record_id = pure_id_match.group()
        else:
            # 최종 방어선: 입력값에 숫자가 있다면 가장 마지막에 등장하는 연속된 숫자를 ID로 추정
            all_numbers = re.findall(r'\d+', clean_input)
            if all_numbers:
                # 10이나 5281을 피하기 위해 가장 뒤에 있는 긴 숫자를 선택
                record_id = all_numbers[-1]

    if record_id:
        api_url = f"https://zenodo.org/api/records/{record_id}"
        
        with st.spinner(f"Connecting to Zenodo API (ID: {record_id})..."):
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                response = requests.get(api_url, headers=headers)
                
                if response.status_code == 200:
                    data = response.json()
                    stats = data.get("stats", {})
                    metadata = data.get("metadata", {})
                    title = metadata.get("title", f"Record #{record_id}")
                    
                    st.success(f"✅ Connection Successful: **{title}**")
                    st.write("---")
                    
                    # Metrics Grid
                    col1, col2 = st.columns(2)
                    col3, col4 = st.columns(2)
                    
                    with col1:
                        st.metric(label="📥 Total Downloads", value=f"{stats.get('downloads', 0):,}")
                    with col2:
                        st.metric(label="👥 Unique Downloads", value=f"{stats.get('unique_downloads', 0):,}")
                    with col3:
                        st.metric(label="👀 Total Views", value=f"{stats.get('views', 0):,}")
                    with col4:
                        st.metric(label="👤 Unique Views", value=f"{stats.get('unique_views', 0):,}")
                        
                    st.write("---")
                    st.markdown("### 📌 Current Version Specifics")
                    
                    v_col1, v_col2 = st.columns(2)
                    v_col3, v_col4 = st.columns(2)
                    
                    with v_col1:
                        st.write(f"🔹 **Version Downloads:** {stats.get('version_downloads', 0):,}")
                    with v_col2:
                        st.write(f"🔹 **Version Unique Downloads:** {stats.get('version_unique_downloads', 0):,}")
                    with v_col3:
                        st.write(f"🔹 **Version Views:** {stats.get('version_views', 0):,}")
                    with v_col4:
                        st.write(f"🔹 **Version Unique Views:** {stats.get('version_unique_views', 0):,}")
                    
                elif response.status_code == 404:
                    st.error(f"❌ Record Not Found (404). Tested API URL: {api_url}. The ID `{record_id}` does not exist or index sync is lagging.")
                else:
                    st.error(f"❌ Failed to fetch data. (Status Code: {response.status_code})")
            except Exception as e:
                st.error(f"⚠️ An error occurred: {e}")
    else:
        st.warning("⚠️ Invalid format. Please check your Zenodo link or ID.")
