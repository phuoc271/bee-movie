import os
import random
import requests
from flask import Blueprint, render_template, request, jsonify, current_app
from app.extensions import db 
from app.models import Showtime, MovieExtra 
from app.utils.tmdb import fetch_movies_list
from datetime import datetime, timedelta
from sqlalchemy import text

chatbot_bp = Blueprint('chatbot', __name__)

MOVIE_DATA_CACHE = {
    "content": "",
    "last_updated": None
}
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
        sql = "SELECT name, price, description FROM concessions WHERE name LIKE :kw OR description LIKE :kw"
        result = db.session.execute(db.text(sql), {"kw": f"%{keyword}%"}).fetchall()
        
        if not result:
            return None
            
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
    user_message = request.json.get('message', '').strip()
    msg_lower = user_message.lower()
    
    if msg_lower in ['hi', 'hello', 'alo', 'chào', 'chào bạn']:
        return jsonify({'reply': "Chào bạn! Bee Movie có thể giúp gì cho bạn về lịch chiếu, giá vé hay bắp nước?"})

    current_movies = get_current_movies_from_db() if any(k in msg_lower for k in ["phim", "chiếu", "lịch", "giờ", "xem"]) else ""
    search_result = search_concessions_db(msg_lower) if any(k in msg_lower for k in ["bắp", "nước", "combo", "ăn", "uống"]) else ""
    
    prompt = f"""
    Bạn là Bee AI. Nhiệm vụ: Trả lời CỰC KỲ NGẮN GỌN và TRỰC TIẾP. Không chào hỏi thừa thãi.
    Dữ liệu:
    - Lịch chiếu: {current_movies}
    - Bắp nước: {search_result}
    - Giá vé: 65k-95k (HSSV 55k).
    
    Khách hỏi: {user_message}
    Quy tắc:
    - Nếu khách hỏi phim, chỉ liệt kê phim .
    - Nếu khách hỏi suất chiếu , thì hỏi ngày cụ thể , sau khi cung cấp ngày thì liệt kê suất chiếu và phim đó ra.
    - Nếu không có thông tin trong dữ liệu, bảo là 'Dạ rạp hiện chưa có thông tin này'.
    - Không lặp lại câu hỏi của khách.
    """
    
    return jsonify({'reply': call_gemini_api(prompt)})

MOVIE_CACHE = {
    "content": "",
    "last_updated": None
}
def get_current_movies_from_db():
    global MOVIE_CACHE
    now = datetime.now()
    
    if MOVIE_CACHE["last_updated"] and now < MOVIE_CACHE["last_updated"] + timedelta(minutes=10):
        return MOVIE_CACHE["content"]

    try:
        sql = text("SELECT movie_id, start_time FROM showtimes WHERE start_time >= :now ORDER BY start_time ASC")
        results = db.session.execute(sql, {"now": now}).fetchall()
        if not results: return "Rạp hiện chưa có lịch chiếu mới."

        movie_times = {}
        for row in results:
            m_id = str(row[0])
            t_str = row[1].strftime("%H:%M")
            movie_times.setdefault(m_id, []).append(t_str)

        all_now_playing = fetch_movies_list("movie/now_playing", params={"language": "vi-VN", "region": "VN"})
        tmdb_dict = {str(m.get("id")): m.get("title") for m in all_now_playing}
        
        final_list = []
        for m_id, times in movie_times.items():
            title = tmdb_dict.get(m_id) or (MovieExtra.query.get(m_id).title if MovieExtra.query.get(m_id) else f"Phim {m_id}")
            final_list.append(f"- {title}: {', '.join(times)}")

        res_text = "\n".join(final_list)
        MOVIE_CACHE.update({"content": res_text, "last_updated": now})
        return res_text
    except Exception as e:
        print(f"Lỗi: {e}")
        return MOVIE_CACHE["content"] or "Đang cập nhật lịch..."