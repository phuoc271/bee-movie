from .auth_controller import auth_bp
from .movie_controller import movie_bp
from .booking_controller import booking_bp
from .admin_controller import admin_bp
from .main_controller import main_bp
from .chatbot_controller import chatbot_bp

def register_controllers(app):
    app.register_blueprint(auth_bp)
    app.register_blueprint(movie_bp)
    app.register_blueprint(booking_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(chatbot_bp)