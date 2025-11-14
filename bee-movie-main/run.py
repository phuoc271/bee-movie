from flask import Flask, render_template, request, redirect, url_for, flash
from flask import session
import json
import os
import google.auth.transport.requests 
import google.oauth2.id_token 
# Quên mật khẩu
from flask_mail import Mail, Message 
from itsdangerous import URLSafeTimedSerializer
from flask import current_app

from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

USER_FILE = "fake_users.json"

app.secret_key = "mysecretkey123456"

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

@app.context_processor
def inject_user():
    return dict(session=session)
# Định nghĩa Route cho trang chủ
@app.route('/')
def home():
    # Giả lập dữ liệu phim đang chiếu
    current_movies = [
        {"title": "THÁI CHIẾU TÀI", "date": "07.11.2025"},
        {"title": "TÌNH NGƯỜI DUYÊN MA", "date": "07.11.2025"},
        {"title": "GODZILLA MINUS ONE", "date": "07.11.2025"},
        {"title": "MỘ ĐƠM ĐÓM", "date": "07.11.2025"},
    ]
    
    return render_template('home.html', movies=current_movies) 

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
    return redirect("/login")
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



@app.route("/all-movies")
def all_movies():
    return render_template("all-movies.html")

@app.route("/movies")
def movies():
    return render_template("movies.html")


if __name__ == '__main__':
    app.run(debug=True)