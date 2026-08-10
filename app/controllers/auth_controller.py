from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, session,
    jsonify, current_app
)
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_mail import Message
from datetime import datetime
import google.auth.transport.requests
import google.oauth2.id_token
import cloudinary.uploader
from app.extensions import db, mail
from app.models import User
from flask_login import login_user
auth_bp = Blueprint('auth', __name__)

def get_user_by_email(email):
    """Tìm người dùng bằng email trong DB."""
    return User.query.filter_by(email=email).first()

def get_reset_token(user_email, expires_sec=1800):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    return s.dumps(user_email, salt=current_app.config['SECURITY_PASSWORD_SALT'])

def verify_reset_token(token, max_age=1800):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        email = s.loads(token, salt=current_app.config['SECURITY_PASSWORD_SALT'], max_age=max_age)
    except Exception as e:
        print("VERIFY TOKEN ERROR:", e)
        return None
    return email

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        user = get_user_by_email(email)

        if user and user.password_hash and user.check_password(password):
            login_user(user)
            session["user_id"] = user.id
            session["user_email"] = user.email
            session["username"] = user.username
            session['role'] = user.role
            session["fullname"] = user.fullname
            session["avatar"] = user.avatar
            session["gender"] = user.gender
            flash("Đăng nhập thành công!", "success")
            return redirect(url_for("movie.home"))
        else:
            flash("Email hoặc mật khẩu sai!", "danger")
    return render_template("login.html")

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        fullname = request.form.get("fullname")
        email = request.form.get("email")
        username = request.form.get("username")
        gender = request.form.get("gender")
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if password != confirm_password:
            flash("Mật khẩu không khớp!", "danger")
            return redirect(url_for("auth.register"))

        if get_user_by_email(email):
            flash("Email đã tồn tại!", "warning")
            return redirect(url_for("auth.register"))

        if User.query.filter_by(username=username).first():
            flash("Tên người dùng đã tồn tại!", "warning")
            return redirect(url_for("auth.register"))

        new_user = User(
            fullname=fullname,
            email=email,
            username=username,
            gender=gender,
            role='user'
        )
        new_user.set_password(password)

        try:
            db.session.add(new_user)
            db.session.commit()
            session["user_id"] = new_user.id
            session["user_email"] = new_user.email
            session["username"] = new_user.username
            flash("Tạo tài khoản thành công!", "success")
            return redirect(url_for("movie.home"))
        except Exception as e:
            db.session.rollback()
            print(f"LỖI DB KHI ĐĂNG KÝ: {e}")
            flash("Có lỗi xảy ra, không thể tạo tài khoản.", "danger")
            return redirect(url_for("auth.register"))

    return render_template("register.html")

@auth_bp.route("/google-login", methods=["POST"])
def google_login():
    try:
        id_token = request.json.get("credential")
        request_adapter = google.auth.transport.requests.Request()
        user_info = google.oauth2.id_token.verify_oauth2_token(
            id_token,
            request_adapter,
            "103196067800-jgevbof1vcb85gbc2a9igh452c7ld6ig.apps.googleusercontent.com"
        )
        email = user_info.get("email")
        name = user_info.get("name")
        picture = user_info.get("picture")
        user = get_user_by_email(email) 

        if not user:
            new_user = User(
                fullname=name,
                email=email,
                username=email.split("@")[0],
                role='user',
                avatar=picture,
                password_hash=None 
            )
            db.session.add(new_user)
            db.session.commit()
            user = new_user
        elif picture and not user.avatar:
            user.avatar = picture
            db.session.commit()
            
        login_user(user)
        session["user_id"] = user.id
        session["user_email"] = email
        session["username"] = user.username
        session["role"] = user.role
        session["fullname"] = user.fullname
        session["avatar"] = user.avatar
        session["gender"] = user.gender
        return {"status": "ok"}
    except Exception as e:
        print("GOOGLE LOGIN ERROR:", e)
        if 'db' in globals() and db.session:
            db.session.rollback()
        return {"status": "error", "message": str(e)}, 400

@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("Đã đăng xuất!", "info")
    return redirect(url_for('auth.login'))

def send_async_email(app, msg):
    """Hàm gửi email chạy nền đảm bảo push đúng App Context."""
    with app.app_context():
        try:
            print(f">>> [MAIL START] Đang kết nối SMTP gửi cho {msg.recipients}...")
            mail.send(msg)
            print(f">>> [MAIL SUCCESS] Đã gửi mật khẩu mới tới: {msg.recipients}")
        except Exception as e:
            print(f">>> [MAIL ERROR] Lỗi khi gửi mail: {e}")

@auth_bp.route("/reset_password", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        email = request.form.get('email')
        user = get_user_by_email(email)

        if user:
            import secrets
            new_password = secrets.token_hex(4)  
            user.set_password(new_password)
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"[DB ERROR] Không thể cập nhật mật khẩu: {e}")
                flash("Có lỗi xảy ra, không thể cập nhật mật khẩu.", "danger")
                return redirect(url_for('auth.reset_password'))

            msg = Message(
                'Mật khẩu mới của bạn',
                sender=current_app.config.get('MAIL_USERNAME'),
                recipients=[email]
            )
            msg.body = f'''Xin chào {user.fullname},

Mật khẩu mới của bạn là: {new_password}

Vui lòng đăng nhập bằng mật khẩu này và đổi lại mật khẩu sau khi đăng nhập.
'''
            
            try:
                print(f">>> [MAIL START] Đang gửi mail tới {email}...")
                mail.send(msg)
                print(f">>> [MAIL SUCCESS] Đã gửi mật khẩu tới {email}")
                flash('Mật khẩu mới đã được gửi đến email của bạn.', 'info')
                return redirect(url_for('auth.login'))
            except Exception as e:
                import traceback
                print(f">>> [MAIL ERROR] Không thể gửi email:")
                traceback.print_exc()
                flash('Mật khẩu đã đổi nhưng hệ thống không thể gửi email thông báo. Vui lòng liên hệ Admin.', 'warning')
                return redirect(url_for('auth.reset_password'))
        else:
            flash('Email không tồn tại trong hệ thống.', 'danger')

    return render_template('reset_request.html')

@auth_bp.route("/change_password", methods=["GET", "POST"])
def change_password():
    if "user_email" not in session:
        flash("Vui lòng đăng nhập để đổi mật khẩu.", "warning")
        return redirect(url_for("auth.login"))

    user = get_user_by_email(session["user_email"])

    if request.method == "POST":
        old_password = request.form.get("old_password")
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        if not user.check_password(old_password):
            flash("Mật khẩu cũ không đúng!", "danger")
            return render_template("reset_token.html", user=user)

        if new_password != confirm_password:
            flash("Mật khẩu mới không khớp!", "danger")
            return render_template("reset_token.html", user=user)

        user.set_password(new_password)
        try:
            db.session.commit()
            flash("Đổi mật khẩu thành công!", "success")
            return redirect(url_for("auth.profile"))
        except Exception as e:
            db.session.rollback()
            print(f"LỖI DB KHI ĐỔI MẬT KHẨU: {e}")
            flash("Có lỗi xảy ra, không thể cập nhật mật khẩu.", "danger")

    return render_template("reset_token.html", user=user)

@auth_bp.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_email" not in session:
        flash("Vui lòng đăng nhập để xem hồ sơ.", "warning")
        return redirect(url_for("auth.login"))

    user = get_user_by_email(session["user_email"])

    if request.method == "POST":
        user.fullname = request.form.get("fullname")
        user.username = request.form.get("username")
        user.gender = request.form.get("gender")

        if "avatar" in request.files:
            file = request.files["avatar"]
            if file and file.filename != "":
                try:
                    upload_result = cloudinary.uploader.upload(
                        file, folder="bee_movie/avatars"
                    )
                    user.avatar = upload_result["secure_url"]
                except Exception as e:
                    print(f"CLOUDINARY UPLOAD ERROR: {e}")
                    flash("Lỗi khi tải ảnh lên đám mây Cloudinary.", "danger")

        try:
            db.session.commit()

            session["username"] = user.username
            session["fullname"] = user.fullname
            session["gender"] = user.gender
            session["avatar"] = user.avatar

            flash("Cập nhật hồ sơ thành công!", "success")
        except Exception as e:
            db.session.rollback()
            print(f"Lỗi DB: {e}")
            flash(f"Lỗi khi cập nhật database: {e}", "danger")

        return redirect(url_for("auth.profile"))

    session["username"] = user.username
    session["fullname"] = user.fullname
    session["gender"] = user.gender
    session["avatar"] = user.avatar

    return render_template("profile.html", user=user)

@auth_bp.route("/delete_account", methods=["POST"])
def delete_account():
    if "user_email" not in session:
        return redirect(url_for('auth.login'))
    
    user = User.query.filter_by(email=session["user_email"]).first()
    if not user:
        return redirect(url_for("movie.home"))

    try:
        from app.models import Comment, Rating, Booking
        from sqlalchemy import text
        
        user_comments = Comment.query.filter_by(user_id=user.id).all()
        for comm in user_comments:
            Comment.query.filter_by(parent_id=comm.id).delete()
            db.session.delete(comm)

        Rating.query.filter_by(user_id=user.id).delete()

        db.session.execute(
            text("DELETE FROM booking_concessions WHERE user_id = :u_id OR booking_id IN (SELECT id FROM bookings WHERE user_id = :u_id)"),
            {"u_id": user.id}
        )

        Booking.query.filter_by(user_id=user.id).delete()
        
        db.session.flush()

        db.session.delete(user)
        db.session.commit()

        session.clear()
        flash("Tài khoản của bạn đã được xóa vĩnh viễn.", "info")
    except Exception as e:
        db.session.rollback()
        print(f"LỖI XÓA TÀI KHOẢN: {e}")
        flash("Không thể xóa tài khoản do có ràng buộc dữ liệu.", "danger")
    
    return redirect(url_for("movie.home"))
