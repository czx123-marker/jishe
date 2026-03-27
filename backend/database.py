import sqlite3
import os

class Database:
    def __init__(self, db_path='users.db'):
        self.db_path = db_path
        self.init_database()
    
    def get_connection(self):
        """Get database connection"""
        return sqlite3.connect(self.db_path)
    
    def init_database(self):
        """Initialize database and create tables"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        print(f"✅ Database initialized: {self.db_path}")
    
    def get_all_users(self):
        """Get all users from the database"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, username, email, created_at 
                FROM users 
                ORDER BY created_at DESC
            ''')
            
            users = cursor.fetchall()
            conn.close()
            
            return users
            
        except sqlite3.Error as e:
            print(f"❌ Error fetching users: {e}")
            return []
    
    def get_database_info(self):
        """Get complete database information"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Get all table names
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            
            # Get user count
            cursor.execute("SELECT COUNT(*) FROM users")
            user_count = cursor.fetchone()[0]
            
            conn.close()
            
            return {
                "tables": [table[0] for table in tables],
                "user_count": user_count
            }
            
        except sqlite3.Error as e:
            print(f"❌ Error getting database info: {e}")
            return {}