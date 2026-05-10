import os
import random
import requests
from flask import Blueprint, render_template, request, jsonify, current_app
from app.extensions import db # Đảm bảo đã import db
# Khởi tạo Blueprint
chatbot_bp = Blueprint('chatbot', __name__)

def get_gemini_keys():
    """Lấy danh sách Gemini Keys từ .env"""
    keys_str = os.getenv("GEMINI_API_KEY", "")
    return [k.strip() for k in keys_str.split(",")] if keys_str else []

def call_gemini_api(prompt):
    """Logic gọi Gemini API với cơ chế xoay vòng và thử lại"""
    keys = get_gemini_keys()
    if not keys:
        return "Lỗi: Chưa cấu hình GEMINI_API_KEY."
    
    current_key = random.choice(keys)
    model_candidates = ["gemini-flash-latest", "gemini-2.0-flash-lite", "gemini-pro-latest"]
    
    for model_name in model_candidates:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={current_key}"
        try:
            response = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
            if response.status_code == 200:
                return response.json()['candidates'][0]['content']['parts'][0]['text']
            if response.status_code == 429:
                return "QUOTA_EXCEEDED"
        except:
            continue
    return "Lỗi: Không tìm thấy model nào hoạt động."

def search_concessions_db(keyword):
    """Tìm kiếm bắp nước trong Database dựa trên từ khóa"""
    try:
        # Câu lệnh SQL tìm kiếm theo tên hoặc mô tả
        sql = "SELECT name, price, description FROM concessions WHERE name LIKE :kw OR description LIKE :kw"
        result = db.session.execute(db.text(sql), {"kw": f"%{keyword}%"}).fetchall()
        
        if not result:
            return None
            
        # Chuyển kết quả thành văn bản để gửi cho AI
        info = "Dữ liệu tìm thấy trong kho:\n"
        for row in result:
            info += f"- {row[0]}: {row[1]:,.0f}đ ({row[2]})\n"
        return info
    except Exception as e:
        print(f"Lỗi SQL: {e}")
        return None
    
@chatbot_bp.route('/chatbot')
def chatbot_page():
    """Render trang giao diện chatbot[cite: 1]"""
    return render_template('chatbot.html')

@chatbot_bp.route('/ask', methods=['POST'])
def ask():
    user_message = request.json.get('message').lower()
    
    # 1. Thử tìm kiếm nhanh trong Database (Ví dụ khách hỏi "bắp", "pepsi", "combo")
    search_result = ""
    keywords = ["bắp", "nước", "combo", "pepsi", "ly", "box", "quà"]
    if any(k in user_message for k in keywords):
        # Lấy từ khóa chính để tìm (ví dụ khách hỏi "muốn mua bắp" -> tìm "bắp")
        found_kw = next((k for k in keywords if k in user_message), "")
        search_result = search_concessions_db(found_kw)

    # 2. Xây dựng Prompt thông minh
    prompt = f"""
    Bạn là Bee AI - Trợ lý rạp Bee Movie.
    Dữ liệu thực tế từ kho hàng:
    {search_result if search_result else "Không tìm thấy sản phẩm cụ thể, hãy trả lời dựa trên kiến thức chung về rạp phim."}
    
    Giá vé mặc định: 110k (thường), 130k (lễ), HSSV 80k.
    
    Yêu cầu: 
    - Nếu khách hỏi giá bắp nước, hãy dùng dữ liệu thực tế ở trên để báo giá.
    - Trả lời ngắn gọn, thân thiện.
    - Câu hỏi của khách: '{user_message}'
    """
    
    ai_reply = call_gemini_api(prompt)
    
    # Giữ nguyên phần xử lý TMDB và Quota phía dưới...
    return jsonify({'reply': ai_reply})