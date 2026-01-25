import os
import sys
import traceback
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler

from app.extensions import db, cache, mail
from app.controllers import register_controllers

def create_app():
    app = Flask(__name__)

    app.secret_key = os.environ.get("APP_SECRET_KEY", "mysecretkey123456")
    app.config["SECRET_KEY"] = app.secret_key
    app.config["SECURITY_PASSWORD_SALT"] = os.environ.get("SECURITY_PASSWORD_SALT", "some-random-salt-value")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL",
        "mysql+pymysql://root:Phuoc2714002.@localhost:3306/bee_movie_db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_FOLDER", "static/uploads/avatars")

    app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
    app.config['MAIL_USE_TLS'] = True
    app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'nguyenhoaiphuoc271@gmail.com')
    app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', 'khrh snlo wgth pdeu')

    app.config["TMDB_API_KEY"] = os.environ.get("TMDB_API_KEY", "f39ba5c15f6a58e7a1bfec8acefe938e")
    app.config["TMDB_BASE_URL"] = os.environ.get("TMDB_BASE_URL", "https://api.themoviedb.org/3")
    app.config["TMDB_IMAGE_BASE_URL"] = os.environ.get("TMDB_IMAGE_BASE_URL", "https://image.tmdb.org/t/p/w500")
    app.config["TMDB_BACKDROP_BASE_URL"] = os.environ.get("TMDB_BACKDROP_BASE_URL", "https://image.tmdb.org/t/p/original")

    return app

app = create_app()

db.init_app(app)
cache.init_app(app)
mail.init_app(app)

try:
    register_controllers(app)
except Exception:
    print("LỖI KHI ĐĂNG KÝ CONTROLLERS:")
    traceback.print_exc(file=sys.stdout)

def startup_tasks(app):
    """Tạo bảng nếu chưa có và seed dữ liệu mẫu (chỉ khi DB rỗng)."""
    try:
        from app.controllers.booking_controller import seed_showtimes_rolling
    except Exception:
        seed_showtimes_rolling = None
        print("Không import được seed_showtimes_rolling.")

    try:
        from app.controllers.movie_controller import fetch_genres as load_genres
    except Exception:
        load_genres = None

    with app.app_context():
        try:
            print("--- BẮT ĐẦU KHỞI TẠO HỆ THỐNG ---")
            db.create_all()
            print("1. Đã kiểm tra/tạo bảng Database.")

            try:
                from app.models import Showtime
                existing = Showtime.query.count()
            except Exception:
                existing = None

            flask_env = os.environ.get("FLASK_ENV", "").lower()
            dev_seed_flag = os.environ.get("DEV_SEED", "true").lower() in ("1", "true", "yes")
            should_seed = (flask_env == "development" or dev_seed_flag) and (existing == 0 or existing is None)

            if should_seed and seed_showtimes_rolling:
                print("2. Bắt đầu tạo lịch chiếu mẫu (rolling, 7 ngày)...")
                seed_showtimes_rolling(days=7)
                print("   Đã tạo lịch chiếu mẫu (rolling).")
            else:
                print(f"2. Bỏ qua seed. FLASK_ENV={flask_env}, DEV_SEED={dev_seed_flag}, existing_showtimes={existing}")

            if load_genres:
                load_genres()
                print("3. Đã tải danh sách thể loại (genres).")

            print("--- HỆ THỐNG SẴN SÀNG ---")
        except Exception:
            print("LỖI KHỞI ĐỘNG CHUNG:")
            traceback.print_exc(file=sys.stdout)

def init_scheduler(app):
    """Khởi tạo scheduler chạy ngầm để seed lịch chiếu mỗi ngày."""
    from app.controllers.booking_controller import seed_showtimes_rolling

    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: seed_showtimes_rolling(days=7), 'cron', hour=0, minute=0)
    scheduler.start()

if __name__ == '__main__':
    startup_tasks(app)
    init_scheduler(app)

    debug_mode = os.environ.get("FLASK_ENV", "").lower() == "development"
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=debug_mode)
