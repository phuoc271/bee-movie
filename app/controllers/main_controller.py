from flask import Blueprint, request, jsonify
from app.utils.tmdb import fetch_from_tmdb

main_bp = Blueprint('main', __name__)

@main_bp.route('/api/tmdb-proxy')
def tmdb_proxy():
    endpoint = request.args.get('endpoint')
    if not endpoint:
        return jsonify({"error": "Thiếu tham số 'endpoint'!"}), 400
    params = request.args.to_dict()
    params.pop('endpoint', None) 
    try:
        data = fetch_from_tmdb(endpoint, params=params)
        if data is None:
            return jsonify({"error": "Không thể lấy dữ liệu từ TMDB"}), 502
        return jsonify(data)
    except Exception as e:
        print(f"LỖI PROXY: {e}")
        return jsonify({"error": "Lỗi hệ thống nội bộ"}), 500