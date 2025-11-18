from flask import Flask, render_template, request, redirect, url_for, flash
from flask import session
import json
import os
import google.auth.transport.requests
import google.oauth2.id_token
import requests
# Quên mật khẩu
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from flask import current_app

from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

USER_FILE = "fake_users.json"

app.secret_key = "mysecretkey123456"

# --- Cấu hình TMDB ---
TMDB_API_KEY = "f39ba5c15f6a58e7a1bfec8acefe938e"
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500" # URL cơ sở cho ảnh poster
TMDB_BACKDROP_BASE_URL = "https://image.tmdb.org/t/p/original"

# Hàm gọi API TMDB
def fetch_movies(endpoint, params=None):
    """
    Hàm chung để gọi API TMDB.
    endpoint: Ví dụ: /movie/now_playing
    params: Tham số query bổ sung (ví dụ: 'language', 'page')
    """
    url = f"{TMDB_BASE_URL}{endpoint}"
    
    # Thiết lập tham số mặc định và tham số bổ sung
    default_params = {
        "api_key": TMDB_API_KEY,
        "language": "vi-VN", # Lấy ngôn ngữ Việt Nam nếu có
        "region": "VN" # Lấy phim tại Việt Nam
    }
    if params:
        default_params.update(params)

    try:
        response = requests.get(url, params=default_params)
        response.raise_for_status() # Báo lỗi nếu mã trạng thái là 4xx hoặc 5xx
        data = response.json()
        
        # Chỉ lấy 6-8 phim đầu tiên (hoặc theo yêu cầu)
        return data.get('results', []) 
    except requests.exceptions.RequestException as e:
        print(f"Lỗi khi gọi TMDB API: {e}")
        return []
    
# Lưu , đọc data
def load_users():
    if not os.path.exists(USER_FILE):
        return {}
    try:
        with open(USER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(f"Lỗi: File {USER_FILE} không phải JSON hợp lệ hoặc trống.")
        return {}

def save_users(data):
    with open(USER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


# Cấu hình Email (Sử dụng Gmail SMTP làm ví dụ)
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'nguyenhoaiphuoc271@gmail.com' 
app.config['MAIL_PASSWORD'] = 'khrh snlo wgth pdeu' # Dùng Mật khẩu ứng dụng của Gmail
# cấu hình bảo mật
app.config['SECRET_KEY'] = app.secret_key  # hoặc đặt trực tiếp 1 chuỗi mạnh ở đây
app.config['SECURITY_PASSWORD_SALT'] = 'some-random-salt-value'  # thay bằng chuỗi ngẫu nhiên của bạn

mail = Mail(app)

def get_reset_token(user_email, expires_sec=1800):
    s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    return s.dumps(user_email, salt=app.config['SECURITY_PASSWORD_SALT'])

def verify_reset_token(token, max_age=1800):
    s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    try:
        email = s.loads(token, salt=app.config['SECURITY_PASSWORD_SALT'], max_age=max_age)
    except Exception as e:
        print("VERIFY TOKEN ERROR:", e)
        return None
    return email

GENRE_MAP = {} 

def fetch_movie_videos(movie_id):
    """Lấy danh sách video/trailer của một bộ phim."""
    endpoint = f"movie/{movie_id}/videos"
    return fetch_from_tmdb(endpoint)

def get_trailer_key(videos_data):
    """Tìm kiếm khóa (key) của Trailer chính thức từ YouTube."""
    if not videos_data or not videos_data.get('results'):
        return None
    
    # Ưu tiên tìm video có type là 'Trailer' và site là 'YouTube'
    for video in videos_data['results']:
        if video.get('site') == 'YouTube' and video.get('type') == 'Trailer':
            return video.get('key')
            
    # Nếu không tìm thấy Trailer, lấy video đầu tiên
    if videos_data['results']:
        return videos_data['results'][0].get('key')
        
    return None

def fetch_genres():
    """Lấy danh sách ID và Tên thể loại từ TMDB và tạo dictionary ánh xạ."""
    global GENRE_MAP
    if GENRE_MAP:
        return GENRE_MAP
        
    url = f"{TMDB_BASE_URL}/genre/movie/list"
    params = {
        "api_key": TMDB_API_KEY,
        "language": "vi-VN" # Lấy tên thể loại bằng tiếng Việt
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        # Xây dựng dictionary ánh xạ ID -> Tên
        genre_map = {genre['id']: genre['name'] for genre in data.get('genres', [])}
        GENRE_MAP = genre_map
        return genre_map
    except requests.exceptions.RequestException as e:
        print(f"Lỗi khi lấy danh sách thể loại: {e}")
        return {}

# Gọi hàm này khi ứng dụng khởi động lần đầu (hoặc trong hàm home/now_playing)

# Hàm chuyển đổi ID thành Tên
def get_genre_names(genre_ids, genre_map):
    """Chuyển đổi danh sách ID thể loại thành chuỗi tên thể loại."""
    if not genre_ids or not genre_map:
        return "Chưa rõ"
    
    # Lấy tên thể loại, dùng 'N/A' nếu không tìm thấy ID
    names = [genre_map.get(id, 'N/A') for id in genre_ids]
    # Nối các tên lại bằng dấu "/"
    return " / ".join(names)

def fetch_from_tmdb(endpoint, params=None):
    """Hàm chung để gọi TMDB API."""
    if params is None:
        params = {}
    params['api_key'] = TMDB_API_KEY
    url = f"{TMDB_BASE_URL}/{endpoint}"
    try:
        response = requests.get(url, params=params)
        response.raise_for_status() # Bắt lỗi HTTP (4xx hoặc 5xx)
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Lỗi khi gọi TMDB API tại endpoint '{endpoint}': {e}")
        return None
    
def fetch_movie_details(movie_id):
    """Lấy thông tin chi tiết của một bộ phim bằng ID."""
    endpoint = f"movie/{movie_id}"
    # Lấy thông tin bằng tiếng Việt
    return fetch_from_tmdb(endpoint, params={'language': 'vi-VN' , 'append_to_response': 'credits'})

@app.context_processor
def inject_user():
    return dict(session=session)
# Định nghĩa Route cho trang chủ
@app.route('/')
def home():
    # 1. Lấy ánh xạ ID thể loại -> Tên thể loại
    genre_map = fetch_genres()
    
    # 2. Lấy phim đang chiếu rạp (Now Playing)
    now_playing_movies_data = fetch_movies("/movie/now_playing", {"page": 1}) 
    
    # 3. BỔ SUNG: Lấy phim SẮP CHIẾU (Upcoming)
    upcoming_data = fetch_movies("/movie/upcoming", {"page": 1})
    
    current_movies_list = []
    featured_movies_list = []
    upcoming_list = [] # <--- Danh sách mới
    
    # 4. Xử lý DỮ LIỆU ĐANG CHIẾU
    for movie in now_playing_movies_data:
        genre_ids = movie.get("genre_ids", [])
        genre_names = get_genre_names(genre_ids, genre_map)

        movie_info = {
            "id": movie.get("id"),
            "title": movie.get("title"),
            "release_date": movie.get("release_date"),
            "overview": movie.get("overview"),
            "genre": genre_names,
            "poster_url": f"{TMDB_IMAGE_BASE_URL}{movie.get('poster_path')}" if movie.get('poster_path') else None,
            "backdrop_url": f"{TMDB_BACKDROP_BASE_URL}{movie.get('backdrop_path')}" if movie.get('backdrop_path') else None,
        }
        
        current_movies_list.append(movie_info)
        
        # Chuẩn bị danh sách cho Banner Carousel (Featured Movies)
        if movie_info.get('backdrop_url') and len(featured_movies_list) < 7:
            featured_movies_list.append(movie_info)

    # 5. Xử lý DỮ LIỆU SẮP CHIẾU
    for movie in upcoming_data:
        genre_ids = movie.get("genre_ids", [])
        genre_names = get_genre_names(genre_ids, genre_map)

        movie_info = {
            "id": movie.get("id"),
            "title": movie.get("title"),
            "release_date": movie.get("release_date"),
            "overview": movie.get("overview"),
            "genre": genre_names,
            "poster_url": f"{TMDB_IMAGE_BASE_URL}{movie.get('poster_path')}" if movie.get('poster_path') else None,
            "backdrop_url": f"{TMDB_BACKDROP_BASE_URL}{movie.get('backdrop_path')}" if movie.get('backdrop_path') else None,
        }
        
        # Thêm vào danh sách phim sắp chiếu
        upcoming_list.append(movie_info)
        
    # 6. Trả về template
    return render_template('home.html', 
                            # movies: 8 phim đang chiếu đầu tiên
                            movies=current_movies_list[:8], 
                            # featured_movies: Tối đa 7 phim cho banner carousel
                            featured_movies=featured_movies_list,
                            # BỔ SUNG: Phim sắp chiếu cho section mới
                            upcoming=upcoming_list)

# Route mới cho tất cả phim đang chiếu
@app.route("/now-playing")
def now_playing():
    # 1. Lấy ánh xạ thể loại
    genre_map = fetch_genres()
    
    # 2. Lấy danh sách phim
    all_now_playing_movies = fetch_movies("/movie/now_playing", {"page": 1})
    
    movie_list = []
    for movie in all_now_playing_movies:
        # Lấy ID thể loại từ dữ liệu TMDB
        genre_ids = movie.get("genre_ids", [])
        
        # 3. Chuyển đổi ID thành tên thể loại thật
        genre_names = get_genre_names(genre_ids, genre_map)

        movie_list.append({
            "id": movie.get("id"),
            "title": movie.get("title"),
            "genre": genre_names, 
            "desc": movie.get("overview"),
            "img": f"{TMDB_IMAGE_BASE_URL}{movie.get('poster_path')}" if movie.get('poster_path') else "placeholder_url",
        })
        
    return render_template("all-movies.html", movies_data_from_server=movie_list, title="Phim Đang Chiếu")



@app.route("/upcoming")
def upcoming():
    genre_map = fetch_genres() 
    upcoming_data = fetch_movies("/movie/upcoming", {"page": 1})
    
    movie_list = []
    for movie in upcoming_data:
        genre_ids = movie.get("genre_ids", [])
        genre_names = get_genre_names(genre_ids, genre_map) 
        
        movie_list.append({
             "id": movie.get("id"),
            "title": movie.get("title"),
            "genre": genre_names, 
            "desc": movie.get("overview"),
            "img": f"{TMDB_IMAGE_BASE_URL}{movie.get('poster_path')}" if movie.get('poster_path') else "placeholder_url",
        })
        
    return render_template("all-movies.html", movies_data_from_server=movie_list, title="Phim Sắp Chiếu")
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        users = load_users()
        user_data = users.get(email)

        # PHẦN ĐÃ SỬA: Dùng check_password_hash
        if user_data and 'password' in user_data and check_password_hash(user_data['password'], password):
            session["user_email"] = email
            session["username"] = user_data["username"]
            session["fullname"] = user_data["fullname"]

            flash("Đăng nhập thành công!", "success")
            return redirect("/")
        else:
            flash("Email hoặc mật khẩu sai!", "danger")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        fullname = request.form["fullname"]
        email = request.form["email"]
        username = request.form["username"]
        password = request.form["password"]
        confirm_password = request.form["confirm_password"]

        users = load_users() 
        
        if password != confirm_password:
            flash("Mật khẩu không khớp!", "danger")
            return redirect(url_for("register"))
        
        elif email in users: 
            flash("Email đã tồn tại!", "warning")
            return redirect(url_for("register"))

        #Băm mật khẩu
        hashed_password = generate_password_hash(password)

        users[email] = {
            "fullname": fullname,
            "username": username,
            "password": hashed_password # LƯU MẬT KHẨU ĐÃ BĂM
        }

        save_users(users)
        flash("Tạo tài khoản thành công!", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/google-login", methods=["POST"])
def google_login():
    try:
        id_token = request.json.get("credential")
        request_adapter = google.auth.transport.requests.Request()

        user_info = google.oauth2.id_token.verify_oauth2_token(
            id_token,
            request_adapter,
            "326949134175-muq4egv1vofb5ln8dh1fov9vq6nkr25s.apps.googleusercontent.com"
        )

        email = user_info.get("email")
        name = user_info.get("name")

        users = load_users()

        if email not in users:
            users[email] = {
                "fullname": name,
                "username": email.split("@")[0],
                "password": ""
            }
            save_users(users)

        session["email"] = email
        session["username"] = users[email]["username"]
        session["fullname"] = users[email]["fullname"]  

        return {"status": "ok"}

    except Exception as e:
        print("GOOGLE LOGIN ERROR:", e)
        return {"status": "error", "message": str(e)}, 400

@app.route("/logout")
def logout():
    session.clear()
    flash("Đã đăng xuất!", "info")
    return redirect(url_for('login')) 

@app.route("/reset_password", methods=["GET", "POST"])
def reset_request():
    if request.method == "POST":
        email = request.form.get('email')
        users = load_users()

        if email in users:
            token = get_reset_token(email)
            reset_link = url_for('reset_token', token=token, _external=True)

            msg = Message('Yêu cầu Đặt lại Mật khẩu',
                          sender=app.config['MAIL_USERNAME'],
                          recipients=[email])
            
            msg.body = f'''Để đặt lại mật khẩu của bạn, nhấn vào link:
{reset_link}

Link hết hạn sau 30 phút.
'''

            mail.send(msg)
            flash('Email đặt lại mật khẩu đã được gửi!', 'info')
            return redirect(url_for('login'))
        else:
            flash('Email không tồn tại trong hệ thống.', 'danger')

    return render_template('reset_request.html')

@app.route("/reset_password/<token>", methods=['GET', 'POST'])
def reset_token(token):
    email = verify_reset_token(token, max_age=1800)  # 30 phút
    if email is None:
        flash('Liên kết đặt lại mật khẩu không hợp lệ hoặc đã hết hạn.', 'warning')
        return redirect(url_for('reset_request'))

    users = load_users()
    if email not in users:
        flash('Tài khoản không tồn tại.', 'danger')
        return redirect(url_for('reset_request'))

    if request.method == "POST":
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if new_password != confirm_password:
            flash("Mật khẩu không khớp!", "danger")
            return render_template('reset_token.html', title='Đặt lại Mật khẩu', token=token)

        hashed_password = generate_password_hash(new_password)
        users[email]['password'] = hashed_password
        save_users(users)

        flash('Mật khẩu của bạn đã được cập nhật!', 'success')
        return redirect(url_for('login'))

    return render_template('reset_token.html', title='Đặt lại Mật khẩu', token=token)

@app.route("/movie/<int:movie_id>")
def movie_detail(movie_id):
    movie_data = fetch_movie_details(movie_id)
    videos_data = fetch_movie_videos(movie_id)
    trailer_key = get_trailer_key(videos_data)
    trailer_url = f"https://www.youtube.com/embed/{trailer_key}?autoplay=1" if trailer_key else None
    print(f"Trailer URL: {trailer_url}")
    if movie_data:
        LANG_MAP = {
            "en": "Anh Quốc",
            "vi": "Việt Nam",
            "th": "Thái Lan",
            "ko": "Hàn Quốc",
            "ja": "Nhật Bản",
            "zh": "Trung Quốc"
        }
        
        details = {
            "title": movie_data.get("title"),
            "release_date": movie_data.get("release_date"),
            "overview": movie_data.get("overview"),
            "tagline": movie_data.get("tagline"),
            "poster_url": f"{TMDB_IMAGE_BASE_URL}{movie_data.get('poster_path')}",
            "backdrop_url": f"{TMDB_BACKDROP_BASE_URL}{movie_data.get('backdrop_path')}",
            "vote_average": movie_data.get("vote_average", 0),
            "vote_count": movie_data.get("vote_count", 0),
            "genres": [g["name"] for g in movie_data.get("genres", [])],
            "original_language": LANG_MAP.get(movie_data.get("original_language"), "Không xác định"),
            "runtime": movie_data.get("runtime"),
            "director": next(
                (c["name"] for c in movie_data.get("credits", {}).get("crew", [])
                if c.get("job") == "Director"),
                None
            ),
            "cast": [
                c["name"] for c in movie_data.get("credits", {}).get("cast", [])[:10]
            ],
            "trailer_url": trailer_url 
        }
        return render_template("movies.html", movie=details)
    
    flash("Không tìm thấy thông tin phim.", "danger")
    return redirect(url_for('home'))

@app.route("/all-movies")
def all_movies():
    return render_template("all-movies.html")

@app.route("/movies")
def movies():
    return render_template("movies.html")

if __name__ == '__main__':
    app.run(debug=True)