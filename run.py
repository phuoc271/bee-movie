from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_caching import Cache
import json
import os
import google.auth.transport.requests
import google.oauth2.id_token
import requests
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import generate_password_hash, check_password_hash
from concurrent.futures import ThreadPoolExecutor, as_completed

# -------------------- Cấu hình Flask & Cache --------------------
app = Flask(__name__)
USER_FILE = "fake_users.json"

# Bí mật ứng dụng (đổi khi deploy thật)
app.secret_key = "mysecretkey123456"
app.config['SECRET_KEY'] = app.secret_key
app.config['SECURITY_PASSWORD_SALT'] = 'some-random-salt-value'

# Flask-Caching (simple). Khi deploy production, chuyển sang Redis/Memcached.
cache = Cache(app, config={'CACHE_TYPE': 'simple'})

# -------------------- Cấu hình Mail --------------------
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'nguyenhoaiphuoc271@gmail.com'
app.config['MAIL_PASSWORD'] = 'khrh snlo wgth pdeu'  # mật khẩu app (example)
mail = Mail(app)

# -------------------- TMDB config & HTTP session --------------------
TMDB_API_KEY = "f39ba5c15f6a58e7a1bfec8acefe938e"
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"
TMDB_BACKDROP_BASE_URL = "https://image.tmdb.org/t/p/original"

# Tạo session tái sử dụng (giảm overhead TCP)
http = requests.Session()

# Thời gian chờ cho request (giúp tránh treo lâu)
REQUEST_TIMEOUT = 6

# -------------------- Utils: read/write users --------------------
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

# -------------------- Token reset password --------------------
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

# Sử dụng memoize để cache theo args của hàm (endpoint + params)
@cache.memoize(timeout=600)
def fetch_from_tmdb(endpoint, params=None):
    """Hàm chung gọi TMDB, cache theo endpoint + params (memoize)."""
    if params is None:
        params = {}
    params = dict(params)  # copy để an toàn
    params['api_key'] = TMDB_API_KEY
    url = f"{TMDB_BASE_URL}/{endpoint.lstrip('/')}"
    try:
        resp = http.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"[TMDB ERROR] endpoint={endpoint} params={params} -> {e}")
        return None

# Memoize: cache theo movie_id
@cache.memoize(timeout=7200)
def fetch_movie_videos(movie_id):
    return fetch_from_tmdb(f"movie/{movie_id}/videos")

def get_trailer_key(videos_data):
    if not videos_data or not videos_data.get('results'):
        return None
    for v in videos_data['results']:
        if v.get('site') == 'YouTube' and v.get('type') == 'Trailer':
            return v.get('key')
    return videos_data['results'][0].get('key') if videos_data['results'] else None

# Cache danh sách thể loại (một lần, hoặc 2 giờ)
GENRE_MAP = {}
@cache.cached(timeout=7200, key_prefix="tmdb_genres")
def fetch_genres():
    global GENRE_MAP
    if GENRE_MAP:
        return GENRE_MAP
    data = fetch_from_tmdb("genre/movie/list", params={"language": "vi-VN"})
    if not data:
        return {}
    GENRE_MAP = {g['id']: g['name'] for g in data.get('genres', [])}
    return GENRE_MAP

def get_genre_names(genre_ids, genre_map):
    if not genre_ids or not genre_map:
        return "Chưa rõ"
    names = [genre_map.get(gid, 'N/A') for gid in genre_ids]
    return " / ".join(names)

# Thêm một hàm chuyên cho gọi list phim (memoize dựa trên endpoint+params)
@cache.memoize(timeout=600)
def fetch_movies_list(endpoint, params=None):
    # endpoint ví dụ: "movie/now_playing" hoặc "movie/upcoming"
    data = fetch_from_tmdb(endpoint, params=params)
    if not data:
        return []
    return data.get('results', [])

# -------------------- Context processor --------------------
@app.context_processor
def inject_user():
    return dict(session=session)

# -------------------- Routes chính --------------------
@app.route('/')
def home():
    """Trang chủ: lấy now_playing + upcoming. Gọi song song để tăng tốc."""
    genre_map = fetch_genres()

    # Gọi song song 2 API (giảm tổng latency)
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_now = ex.submit(fetch_movies_list, "movie/now_playing", {"page": 1, "language": "vi-VN", "region": "VN"})
        fut_up = ex.submit(fetch_movies_list, "movie/upcoming", {"page": 1, "language": "vi-VN", "region": "VN"})

        now_playing_movies_data = fut_now.result()
        upcoming_data = fut_up.result()

    current_movies_list = []
    featured_movies_list = []
    upcoming_list = []

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
        if movie_info.get('backdrop_url') and len(featured_movies_list) < 7:
            featured_movies_list.append(movie_info)

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
        upcoming_list.append(movie_info)

    return render_template('home.html',
                           movies=current_movies_list[:8],
                           featured_movies=featured_movies_list,
                           upcoming=upcoming_list)

# Route all now playing - cache riêng cho route này
@app.route("/now-playing")
@cache.cached(timeout=600, key_prefix="cache_now_playing_page")
def now_playing():
    genre_map = fetch_genres()
    all_now_playing_movies = fetch_movies_list("movie/now_playing", {"page": 1, "language": "vi-VN", "region": "VN"})
    movie_list = []
    for movie in all_now_playing_movies:
        genre_ids = movie.get("genre_ids", [])
        genre_names = get_genre_names(genre_ids, genre_map)
        movie_list.append({
            "id": movie.get("id"),
            "title": movie.get("title"),
            "genre": genre_names,
            "desc": movie.get("overview"),
            "img": f"{TMDB_IMAGE_BASE_URL}{movie.get('poster_path')}" if movie.get('poster_path') else "placeholder_url",
        })
    return render_template("all-movies.html", movies_data_from_server=movie_list, title="Phim Đang Chiếu")

# Route all upcoming - cache riêng
@app.route("/upcoming")
@cache.cached(timeout=600, key_prefix="cache_upcoming_page")
def upcoming():
    genre_map = fetch_genres()
    upcoming_data = fetch_movies_list("movie/upcoming", {"page": 1, "language": "vi-VN", "region": "VN"})
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

# -------------------- Auth routes  --------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        users = load_users()
        user_data = users.get(email)

        if user_data and 'password' in user_data and check_password_hash(user_data['password'], password):
            session["user_email"] = email
            session["username"] = user_data.get("username")
            session["fullname"] = user_data.get("fullname")
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
        if email in users:
            flash("Email đã tồn tại!", "warning")
            return redirect(url_for("register"))

        hashed_password = generate_password_hash(password)
        users[email] = {
            "fullname": fullname,
            "username": username,
            "password": hashed_password
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
            users[email] = {"fullname": name, "username": email.split("@")[0], "password": ""}
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

# -------------------- Reset password --------------------
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
            try:
                mail.send(msg)
                flash('Email đặt lại mật khẩu đã được gửi!', 'info')
            except Exception as e:
                print("MAIL SEND ERROR:", e)
                flash('Gửi email thất bại. Kiểm tra cấu hình SMTP.', 'danger')
            return redirect(url_for('login'))
        else:
            flash('Email không tồn tại trong hệ thống.', 'danger')
    return render_template('reset_request.html')

@app.route("/reset_password/<token>", methods=['GET', 'POST'])
def reset_token(token):
    email = verify_reset_token(token, max_age=1800)
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
        users[email]['password'] = generate_password_hash(new_password)
        save_users(users)
        flash('Mật khẩu của bạn đã được cập nhật!', 'success')
        return redirect(url_for('login'))
    return render_template('reset_token.html', title='Đặt lại Mật khẩu', token=token)

# -------------------- Movie detail --------------------
@app.route("/movie/<int:movie_id>")
def movie_detail(movie_id):
    movie_data = fetch_from_tmdb(f"movie/{movie_id}", params={'language': 'vi-VN', 'append_to_response': 'credits'})
    videos_data = fetch_movie_videos(movie_id)
    trailer_key = get_trailer_key(videos_data)
    trailer_url = f"https://www.youtube.com/embed/{trailer_key}?autoplay=1" if trailer_key else None

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
            "poster_url": f"{TMDB_IMAGE_BASE_URL}{movie_data.get('poster_path')}" if movie_data.get('poster_path') else None,
            "backdrop_url": f"{TMDB_BACKDROP_BASE_URL}{movie_data.get('backdrop_path')}" if movie_data.get('backdrop_path') else None,
            "vote_average": movie_data.get("vote_average", 0),
            "vote_count": movie_data.get("vote_count", 0),
            "genres": [g["name"] for g in movie_data.get("genres", [])],
            "original_language": LANG_MAP.get(movie_data.get("original_language"), "Không xác định"),
            "runtime": movie_data.get("runtime"),
            "director": next((c["name"] for c in movie_data.get("credits", {}).get("crew", []) if c.get("job") == "Director"), None),
            "cast": [c["name"] for c in movie_data.get("credits", {}).get("cast", [])[:10]],
            "trailer_url": trailer_url
        }
        return render_template("movies.html", movie=details)

    flash("Không tìm thấy thông tin phim.", "danger")
    return redirect(url_for('home'))

# -------------------- Static routes --------------------
@app.route("/all-movies")
def all_movies():
    return render_template("all-movies.html")

@app.route("/movies")
def movies():
    return render_template("movies.html")

# -------------------- Khởi chạy app --------------------
if __name__ == '__main__':
    # Tải genres khi start app (giúp request đầu nhanh hơn)
    try:
        print("Đang tải danh sách thể loại (genres)...")
        fetch_genres()
        print("Đã tải xong thể loại.")
    except Exception as e:
        print("Lỗi tải genre khi start:", e)
    app.run(debug=True)
