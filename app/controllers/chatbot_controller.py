import os
import random
import requests
from flask import Blueprint, render_template, request, jsonify, current_app
from app.extensions import db 
from app.models import Showtime, MovieExtra 
from app.utils.tmdb import fetch_movies_list, tmdb_movie_detail
from datetime import datetime, timedelta
from app.prompts import build_chatbot_prompt
from sqlalchemy import text

chatbot_bp = Blueprint('chatbot', __name__)

MOVIE_DATA_CACHE = {
    "content": "",
    "last_updated": None
}

def call_ai_api(prompt):
    """Logic ưu tiên gọi Groq API trước, nếu lỗi hoặc hết key mới nhảy sang Gemini"""
    groq_keys = get_groq_keys()
    if groq_keys:
        current_key = random.choice(groq_keys)
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {current_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3
        }
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
            print(f"Groq trả về mã lỗi {response.status_code}! Chuyển sang Gemini...")
        except Exception as e:
            print(f"Lỗi gọi Groq: {e}. Chuyển sang Gemini...")

    return call_gemini_fallback(prompt)


def call_gemini_fallback(prompt):
    """Logic gọi Gemini API làm dự phòng khi Groq gặp sự cố"""
    keys = get_gemini_keys()
    if not keys:
        return "Lỗi: Hệ thống đang bận, vui lòng thử lại sau."
    
    current_key = random.choice(keys)
    model_candidates = ["gemini-flash-latest", "gemini-2.0-flash-lite", "gemini-pro-latest"]
    
    for model_name in model_candidates:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={current_key}"
        try:
            response = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
            if response.status_code == 200:
                return response.json()['candidates'][0]['content']['parts'][0]['text']
            if response.status_code == 429:
                print("Gemini hết hạn mức!")
                break
        except Exception as e:
            print(f"Lỗi gọi Gemini ({model_name}): {e}")
            continue
            
    return "Lỗi: Hệ thống đang bảo trì, vui lòng quay lại sau."

def get_gemini_keys():
    """Lấy danh sách Gemini Keys từ .env"""
    keys_str = os.getenv("GEMINI_API_KEY", "")
    return [k.strip() for k in keys_str.split(",")] if keys_str else []

def call_gemini_api(prompt):
    """Logic gọi Gemini API với cơ chế xoay vòng và thử lại"""
    keys = get_gemini_keys()
    if not keys:
        return call_groq_api(prompt)
    
    current_key = random.choice(keys)
    model_candidates = ["gemini-flash-latest", "gemini-2.0-flash-lite", "gemini-pro-latest"]
    
    for model_name in model_candidates:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={current_key}"
        try:
            response = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
            if response.status_code == 200:
                return response.json()['candidates'][0]['content']['parts'][0]['text']
            if response.status_code == 429:
                print("Gemini hết hạn mức! Đang chuyển hướng sang Groq...")
                break
        except:
            continue
    return call_groq_api(prompt)
def get_groq_keys():
    """Lấy danh sách Groq Keys từ .env"""
    keys_str = os.getenv("GROQ_API_KEYS", "")
    return [k.strip() for k in keys_str.split(",")] if keys_str else []

def call_groq_api(prompt):
    """Logic gọi Groq API làm dự phòng khi Gemini hết hạn mức"""
    keys = get_groq_keys()
    if not keys:
        return "Lỗi: Hệ thống đang bận, vui lòng thử lại sau."
    
    current_key = random.choice(keys)
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {current_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
    except Exception as e:
        print(f"Lỗi gọi Groq: {e}")
    return "Lỗi: Hệ thống đang bảo trì, vui lòng quay lại sau."

def search_concessions_db(keyword=""):
    """Lấy menu bắp nước từ DB. Nếu tìm kiếm từ khóa không thấy thì tự động lấy toàn bộ menu"""
    try:
        result = None
        
        if keyword:
            sql = "SELECT name, price, description FROM concessions WHERE name LIKE :kw OR description LIKE :kw"
            result = db.session.execute(text(sql), {"kw": f"%{keyword}%"}).fetchall()
        
        if not result:
            sql = "SELECT name, price, description FROM concessions"
            result = db.session.execute(text(sql)).fetchall()
            
        if not result:
            return None
            
        info = "Dữ liệu bắp nước/combo tại rạp:\n"
        for row in result:
            desc = f" ({row[2]})" if row[2] else ""
            info += f"- {row[0]}: {row[1]:,.0f}đ{desc}\n"
        return info
    except Exception as e:
        print(f"Lỗi SQL Bắp Nước: {e}")
        return None
    
@chatbot_bp.route('/chatbot')
def chatbot_page():
    """Render trang giao diện chatbot[cite: 1]"""
    return render_template('chatbot.html')

@chatbot_bp.route('/ask', methods=['POST'])
def ask():
    user_message = request.json.get('message', '').strip()
    
    if user_message.lower() in ['hi', 'hello', 'alo', 'chào', 'chào bạn']:
        return jsonify({'reply': "Chào bạn! Bee Movie có thể giúp gì cho bạn về lịch chiếu, giá vé hay bắp nước?"})

    intent = classify_user_intent(user_message)
    
    current_movies = get_current_movies_from_db() if intent == "MOVIE" else ""
    search_result = ""
    if intent in ["FOOD", "GENERAL"]:
        search_result = search_concessions_db()
    
    prompt = build_chatbot_prompt(
        user_message=user_message,
        current_movies=current_movies,
        search_result=search_result
    )
    
    return jsonify({'reply': call_ai_api(prompt)})

MOVIE_CACHE = {
    "content": "",
    "last_updated": None
}
TMDB_GENRES_MAP = {
    28: "Hành động", 12: "Phiêu lưu", 16: "Hoạt hình", 35: "Hài", 80: "Tội phạm",
    99: "Tài liệu", 18: "Chính kịch", 10751: "Gia đình", 14: "Kỳ ảo", 36: "Lịch sử",
    27: "Kinh dị", 10402: "Âm nhạc", 9648: "Bí ẩn", 10749: "Lãng mạn", 878: "Khoa học viễn tưởng",
    10770: "Phim truyền hình", 53: "Gây cấn", 10752: "Chiến tranh", 37: "Miền tây"
}

def get_current_movies_from_db():
    global MOVIE_CACHE
    now = datetime.now()
    
    if MOVIE_CACHE.get("last_updated") and now < MOVIE_CACHE["last_updated"] + timedelta(minutes=10):
        return MOVIE_CACHE["content"]

    try:
        sql = text("SELECT movie_id, start_time FROM showtimes WHERE start_time >= :now ORDER BY start_time ASC")
        results = db.session.execute(sql, {"now": now}).fetchall()
        if not results: 
            return "Rạp hiện chưa có lịch chiếu mới."

        movie_times = {}
        for row in results:
            m_id = str(row[0])
            t_str = row[1].strftime("%H:%M")
            movie_times.setdefault(m_id, []).append(t_str)

        all_now_playing = fetch_movies_list("movie/now_playing", params={"language": "vi-VN", "region": "VN"}) or []
        tmdb_dict = {str(m.get("id")): m for m in all_now_playing}
        
        final_list = []
        for m_id, times in movie_times.items():
            tmdb_item = tmdb_dict.get(m_id)
            extra = MovieExtra.query.get(m_id) 

            if extra and extra.title:
                title = extra.title
            elif tmdb_item and tmdb_item.get("title"):
                title = tmdb_item.get("title")
            else:
                title = f"Phim {m_id}"

            if extra and extra.genres:
                genres = extra.genres
            elif tmdb_item and tmdb_item.get("genre_ids"):
                g_names = [TMDB_GENRES_MAP.get(gid) for gid in tmdb_item.get("genre_ids", []) if TMDB_GENRES_MAP.get(gid)]
                genres = ", ".join(g_names) if g_names else "Đang cập nhật"
            else:
                genres = "Đang cập nhật"

            runtime_num = 0

            if extra and extra.runtime:
                try:
                    runtime_num = int(extra.runtime)
                except (ValueError, TypeError):
                    runtime_num = 0

            if runtime_num == 0 and tmdb_item and tmdb_item.get("runtime"):
                try:
                    runtime_num = int(tmdb_item.get("runtime"))
                except (ValueError, TypeError):
                    runtime_num = 0

            if runtime_num == 0:
                detail = tmdb_movie_detail(m_id, language='vi-VN')
                if detail and detail.get("runtime"):
                    try:
                        runtime_num = int(detail.get("runtime"))
                    except (ValueError, TypeError):
                        runtime_num = 0

            runtime = f"{runtime_num} phút" if runtime_num > 0 else "Đang cập nhật"
            movie_link = f"/movie/{m_id}"
            
            final_list.append(
                f"- Tên phim: {title} | Link: {movie_link} | Thể loại: {genres} | Thời lượng: {runtime} | Suất chiếu: {', '.join(times)}"
            )
        res_text = "\n".join(final_list)
        MOVIE_CACHE.update({"content": res_text, "last_updated": now})
        return res_text

    except Exception as e:
        print(f"Lỗi khi cập nhật dữ liệu phim cho AI: {e}")
        return MOVIE_CACHE.get("content") or "Đang cập nhật lịch..."
def classify_user_intent(user_message):
    """Phân loại ý định khách hàng bằng Llama-3.1-8b (chỉ tốn ~10 token và ~0.1s)"""
    msg = user_message.lower().strip()
    if msg in ['hi', 'hello', 'alo', 'chào', 'chào bạn']:
        return "GENERAL"

    prompt = f"""
    Bạn là bộ phân loại ý định cho rạp chiếu phim Bee Movie.
    Hãy phân tích câu hỏi của khách hàng và CHỈ TRẢ VỀ 1 TRONG 3 TỪ CỐ ĐỊNH:
    - MOVIE: Khách hỏi về danh sách phim, thể loại, diễn viên, hãng phim (Disney, Marvel...), lịch chiếu, giờ chiếu, tìm phim.
    - FOOD: Khách hỏi về bắp, nước, combo, đồ ăn, đồ uống.
    - GENERAL: Khách hỏi giá vé, địa chỉ, khuyến mãi, chào hỏi hoặc các chủ đề khác.

    Câu hỏi: "{user_message}"
    Kết quả (chỉ trả về MOVIE, FOOD hoặc GENERAL):
    """
    
    groq_keys = get_groq_keys()
    if groq_keys:
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {random.choice(groq_keys)}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0
            }
            res = requests.post(url, json=payload, headers=headers, timeout=3)
            if res.status_code == 200:
                intent = res.json()['choices'][0]['message']['content'].strip().upper()
                if intent in ["MOVIE", "FOOD", "GENERAL"]:
                    return intent
        except Exception as e:
            print(f"Lỗi phân loại Intent: {e}")

    return "MOVIE"  