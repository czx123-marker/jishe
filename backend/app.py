from flask import Flask, request, jsonify
from flask_cors import CORS
from .auth_system import AuthManager

app = Flask(__name__)
CORS(app)  # This is essential!
auth = AuthManager()

# Add this root route for testing
@app.route('/')
def home():
    return jsonify({"message": "Backend is running!"})

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        
        print(f"Login attempt: {username}")  # Debug print
        
        success, result = auth.login(username, password)
        
        if success:
            return jsonify({
                "success": True,
                "message": "Login successful",
                "user": result
            })
        else:
            return jsonify({
                "success": False,
                "message": result
            }), 401
            
    except Exception as e:
        print(f"Login error: {e}")  # Debug print
        return jsonify({
            "success": False,
            "message": f"Server error: {str(e)}"
        }), 500

@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.get_json()
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')
        
        print(f"Register attempt: {username}, {email}")  # Debug print
        
        success, message = auth.register(username, email, password)
        
        return jsonify({
            "success": success,
            "message": message
        })
        
    except Exception as e:
        print(f"Register error: {e}")  # Debug print
        return jsonify({
            "success": False,
            "message": f"Server error: {str(e)}"
        }), 500

# Add this test route
@app.route('/api/test', methods=['GET'])
def test():
    return jsonify({"message": "API is working!"})

if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')  # Added host='0.0.0.0'
