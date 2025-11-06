# run.py

from flask import Flask, render_template

app = Flask(__name__)

# Định nghĩa Route cho trang chủ
@app.route('/')
def index():
    # Giả lập dữ liệu phim đang chiếu
    current_movies = [
        {"title": "THÁI CHIẾU TÀI", "date": "07.11.2025"},
        {"title": "TÌNH NGƯỜI DUYÊN MA", "date": "07.11.2025"},
        {"title": "GODZILLA MINUS ONE", "date": "07.11.2025"},
        {"title": "MỘ ĐƠM ĐÓM", "date": "07.11.2025"},
    ]
    
    # *** ĐÃ ĐỔI TÊN index.html THÀNH home.html ***
    return render_template('home.html', movies=current_movies) 

if __name__ == '__main__':
    app.run(debug=True)