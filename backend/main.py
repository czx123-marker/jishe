from .auth_system import AuthManager

def display_menu():
    """Display main menu"""
    print("\n" + "="*40)
    print("🔐 AUTHENTICATION SYSTEM")
    print("="*40)
    print("1. Register")
    print("2. Login") 
    print("3. View All Users")
    print("4. Database Info")
    print("5. Exit")
    print("="*40)

def register_user(auth):
    """Handle user registration"""
    print("\n--- REGISTER ---")
    username = input("Username: ").strip()
    email = input("Email: ").strip()
    password = input("Password: ").strip()
    
    if not username or not email or not password:
        print("❌ All fields are required!")
        return
    
    success, message = auth.register(username, email, password)
    print(f"Result: {message}")

def login_user(auth):
    """Handle user login"""
    print("\n--- LOGIN ---")
    username = input("Username: ").strip()
    password = input("Password: ").strip()
    
    if not username or not password:
        print("❌ Username and password are required!")
        return
    
    success, result = auth.login(username, password)
    
    if success:
        print(f"✅ Login successful!")
        print(f"   Welcome {result['username']}!")
        print(f"   Email: {result['email']}")
        print(f"   User ID: {result['id']}")
    else:
        print(f"❌ Login failed: {result}")

def view_all_users(auth):
    """Display all users in database"""
    auth.display_all_users()

def show_database_info(auth):
    """Show database information"""
    info = auth.get_database_info()
    
    print("\n" + "="*40)
    print("🗃️ DATABASE INFORMATION")
    print("="*40)
    print(f"Database file: users.db")
    print(f"Tables: {', '.join(info.get('tables', []))}")
    print(f"Total users: {info.get('user_count', 0)}")
    print("="*40)

def main():
    """Main application loop"""
    auth = AuthManager()
    
    while True:
        display_menu()
        choice = input("Choose option (1-5): ").strip()
        
        if choice == "1":
            register_user(auth)
        elif choice == "2":
            login_user(auth)
        elif choice == "3":
            view_all_users(auth)
        elif choice == "4":
            show_database_info(auth)
        elif choice == "5":
            print("👋 Goodbye!")
            break
        else:
            print("❌ Invalid option! Please choose 1-5.")

if __name__ == "__main__":
    main()
