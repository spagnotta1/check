from app import create_app, db
from flask_migrate import init, migrate, upgrade

def init_database():
    app = create_app()
    with app.app_context():
        # Initialize migrations
        init()
        migrate()
        upgrade()
        
        print("Database initialized successfully!")

if __name__ == '__main__':
    init_database() 