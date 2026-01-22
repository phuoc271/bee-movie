from app.extensions import db
from werkzeug.security import generate_password_hash, check_password_hash

class User(db.Model):
    """Mô hình người dùng ánh xạ tới bảng 'users' trong MySQL."""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False) 
    username = db.Column(db.String(80), unique=True, nullable=False)
    fullname = db.Column(db.String(120), nullable=True)
    gender = db.Column(db.String(10), nullable=True)
    avatar = db.Column(db.String(255), nullable=True)
    password_hash = db.Column(db.String(256), nullable=True) 
    comments = db.relationship('Comment', foreign_keys='Comment.user_id', back_populates='user', cascade="all, delete-orphan")

    def set_password(self, password):
        """Hash mật khẩu và lưu vào cột password_hash."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Kiểm tra mật khẩu nhập vào có khớp với mật khẩu hash không."""
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.email}>'
