import hashlib
import secrets
from .database import Database

class AuthManager:
    def __init__(self, db_path='users.db'):
        self.db = Database(db_path)
    
    def hash_password(self, password, salt=None):
        """Hash password with salt for security"""
        if salt is None:
            salt = secrets.token_hex(16)
        
        password_hash = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt.encode('utf-8'),
            100000
        ).hex()
        
        return password_hash, salt
    
    def verify_password(self, password, stored_hash, salt):
        """Verify password against stored hash"""
        password_hash, _ = self.hash_password(password, salt)
        return password_hash == stored_hash
    
    def register(self, username, email, password):
        """Register a new user"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            # Check if user exists
            cursor.execute(
                'SELECT id FROM users WHERE username = ? OR email = ?', 
                (username, email)
            )
            if cursor.fetchone():
                return False, "❌ Username or email already exists"
            
            # Hash password and create user
            password_hash, salt = self.hash_password(password)
            
            cursor.execute('''
                INSERT INTO users (username, email, password_hash, salt)
                VALUES (?, ?, ?, ?)
            ''', (username, email, password_hash, salt))
            
            conn.commit()
            conn.close()
            return True, "✅ User registered successfully"
            
        except Exception as e:
            return False, f"❌ Database error: {str(e)}"
    
    def login(self, username, password):
        """Login user and verify credentials"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            # Get user data
            cursor.execute('''
                SELECT id, username, email, password_hash, salt 
                FROM users WHERE username = ?
            ''', (username,))
            
            user = cursor.fetchone()
            conn.close()
            
            if not user:
                return False, "❌ User not found"
            
            # Verify password
            user_id, username, email, stored_hash, salt = user
            if self.verify_password(password, stored_hash, salt):
                user_data = {
                    "id": user_id,
                    "username": username,
                    "email": email
                }
                return True, user_data
            else:
                return False, "❌ Invalid password"
                
        except Exception as e:
            return False, f"❌ Database error: {str(e)}"
    
    def user_exists(self, username):
        """Check if user exists"""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
        exists = cursor.fetchone() is not None
        
        conn.close()
        return exists

    def get_all_users(self):
        """Get all users (without sensitive data)"""
        return self.db.get_all_users()
    
    def get_database_info(self):
        """Get database information"""
        return self.db.get_database_info()
    
    def display_all_users(self):
        """Display all users in a formatted way"""
        users = self.get_all_users()
        
        if not users:
            print("📭 No users found in database")
            return
        
        print("\n" + "="*60)
        print("👥 ALL REGISTERED USERS")
        print("="*60)
        print(f"{'ID':<4} {'Username':<15} {'Email':<20} {'Created At'}")
        print("-" * 60)
        
        for user in users:
            user_id, username, email, created_at = user
            # Format the timestamp
            created_str = created_at[:16] if created_at else "Unknown"
            print(f"{user_id:<4} {username:<15} {email:<20} {created_str}")
        
        print("=" * 60)
        print(f"Total users: {len(users)}")
