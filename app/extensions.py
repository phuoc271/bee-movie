from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
from flask_caching import Cache
from flask_login import LoginManager

db = SQLAlchemy()
mail = Mail()
cache = Cache(config={"CACHE_TYPE": "simple"})
login_manager = LoginManager()
