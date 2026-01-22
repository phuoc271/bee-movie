from flask import (
    render_template, request, redirect, url_for, flash, session,
    jsonify, current_app
)
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import google.auth.transport.requests
import google.oauth2.id_token
import os
import time

from app.extensions import db, mail
from app.models import User, Comment

def auth_routes(app):
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

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email")
            password = request.form.get("password")
            user = get_user_by_email(email)

            if user and user.password_hash and user.check_password(password):
                session["user_id"] = user.id
                session["user_email"] = user.email
                session["username"] = user.username
                session["fullname"] = user.fullname
                session["avatar"] = user.avatar
                session["gender"] = user.gender
                flash("Đăng nhập thành công!", "success")
                return redirect(url_for("home"))
            else:
                flash("Email hoặc mật khẩu sai!", "danger")
        return render_template("login.html")

    @app.route("/register", methods=["GET", "POST"])
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
                return redirect(url_for("register"))

            if get_user_by_email(email):
                flash("Email đã tồn tại!", "warning")
                return redirect(url_for("register"))

            if User.query.filter_by(username=username).first():
                flash("Tên người dùng đã tồn tại!", "warning")
                return redirect(url_for("register"))

            new_user = User(
                fullname=fullname,
                email=email,
                username=username,
                gender=gender,
            )
            new_user.set_password(password)

            try:
                db.session.add(new_user)
                db.session.commit()
                session["user_id"] = new_user.id
                session["user_email"] = new_user.email
                session["username"] = new_user.username
                flash("Tạo tài khoản thành công!", "success")
                return redirect(url_for("home"))
            except Exception as e:
                db.session.rollback()
                print(f"LỖI DB KHI ĐĂNG KÝ: {e}")
                flash("Có lỗi xảy ra, không thể tạo tài khoản.", "danger")
                return redirect(url_for("register"))

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
            
            user = get_user_by_email(email) 

            if not user:
                new_user = User(
                    fullname=name,
                    email=email,
                    username=email.split("@")[0],
                    password_hash=None 
                )
                db.session.add(new_user)
                db.session.commit()
                user = new_user

            session["user_id"] = user.id
            session["user_email"] = email
            session["username"] = user.username
            session["fullname"] = user.fullname
            session["avatar"] = user.avatar
            session["gender"] = user.gender
            return {"status": "ok"}
        except Exception as e:
            print("GOOGLE LOGIN ERROR:", e)
            if 'db' in globals() and db.session:
                db.session.rollback()
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
            user = get_user_by_email(email)

            if user:
                token = get_reset_token(email)
                reset_link = url_for('reset_token', token=token, _external=True)
                from flask_mail import Message
                msg = Message('Yêu cầu Đặt lại Mật khẩu',
                              sender=current_app.config.get('MAIL_USERNAME'),
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

        user = get_user_by_email(email)
        if user is None:
            flash('Tài khoản không tồn tại.', 'danger')
            return redirect(url_for('reset_request'))

        if request.method == "POST":
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')

            if new_password != confirm_password:
                flash("Mật khẩu không khớp!", "danger")
                return render_template('reset_token.html', title='Đặt lại Mật khẩu', token=token)

            user.set_password(new_password)
            try:
                db.session.commit()
                flash('Mật khẩu của bạn đã được cập nhật!', 'success')
                return redirect(url_for('login'))
            except Exception as e:
                db.session.rollback()
                print(f"LỖI DB KHI CẬP NHẬT MẬT KHẨU: {e}")
                flash('Có lỗi xảy ra, không thể cập nhật mật khẩu.', 'danger')

        return render_template('reset_token.html', title='Đặt lại Mật khẩu', token=token)

    @app.route("/profile", methods=["GET", "POST"])
    def update_profile():
        if "user_email" not in session:
            flash("Vui lòng đăng nhập để xem hồ sơ.", "warning")
            return redirect(url_for("login"))

        user = get_user_by_email(session["user_email"])

        if request.method == "POST":
            user.fullname = request.form.get("fullname")
            user.username = request.form.get("username")
            user.gender = request.form.get("gender")

            if 'avatar' in request.files:
                file = request.files['avatar']
                if file and file.filename != '':
                    if user.avatar:
                        old_physical_path = os.path.join(current_app.root_path, 'static', user.avatar)
                        if os.path.exists(old_physical_path):
                            try:
                                os.remove(old_physical_path)
                                print(f"DEBUG: Đã xóa file cũ tại {old_physical_path}")
                            except Exception as e:
                                print(f"DEBUG: Lỗi khi xóa file vật lý: {e}")

                    upload_folder = current_app.config.get('UPLOAD_FOLDER', 'static/uploads/avatars')
                    if not os.path.exists(upload_folder):
                        os.makedirs(upload_folder)

                    timestamp = int(time.time())
                    filename = secure_filename(f"user_{user.id}_{timestamp}_{file.filename}")
                    file_path = os.path.join(upload_folder, filename)
                    file.save(file_path)
                    user.avatar = f"uploads/avatars/{filename}"

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

            return redirect(url_for("update_profile"))

        return render_template("profile.html", user=user)

    @app.route("/delete_account", methods=["POST"])
    def delete_account():
        if "user_email" not in session:
            return redirect(url_for("login"))
        
        user = User.query.filter_by(email=session["user_email"]).first()
        if not user:
            return redirect(url_for("home"))

        try:
            from app.models import Comment, Rating, Booking

            Comment.query.filter_by(user_id=user.id).delete()
            Comment.query.filter_by(reply_to_id=user.id).update({Comment.reply_to_id: None})
            Rating.query.filter_by(user_id=user.id).delete()
            Booking.query.filter_by(user_id=user.id).delete()

            if user.avatar:
                avatar_path = os.path.join(current_app.root_path, 'static', user.avatar)
                if os.path.exists(avatar_path):
                    try:
                        os.remove(avatar_path)
                    except Exception as e:
                        print(f"Lỗi khi xóa avatar: {e}")

            db.session.delete(user)
            db.session.commit()

            session.clear()
            flash("Tài khoản của bạn đã được xóa vĩnh viễn.", "info")
        except Exception as e:
            db.session.rollback()
            print(f"LỖI XÓA TÀI KHOẢN: {e}")
            flash("Không thể xóa tài khoản do có ràng buộc dữ liệu.", "danger")
        
        return redirect(url_for("home"))



