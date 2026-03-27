document.addEventListener('DOMContentLoaded', () => {
    const container = document.querySelector('.container');
    const toggleRegisterButton = document.querySelector('.register-btn');
    const toggleLoginButton = document.querySelector('.login-btn');
    const loginForm = document.getElementById('login-form');
    const registerForm = document.getElementById('register-form');

    if (!container || !toggleRegisterButton || !toggleLoginButton || !loginForm || !registerForm) {
        console.error('Login page initialization failed: required elements not found.');
        return;
    }

    const loginUrl = loginForm.dataset.loginUrl;
    const homeUrl = loginForm.dataset.homeUrl;
    const registerUrl = registerForm.dataset.registerUrl;

    toggleRegisterButton.addEventListener('click', () => {
        container.classList.add('active');
    });

    toggleLoginButton.addEventListener('click', () => {
        container.classList.remove('active');
    });

    loginForm.addEventListener('submit', async (event) => {
        event.preventDefault();

        if (!loginUrl || !homeUrl) {
            alert('Login configuration missing. Please contact support.');
            return;
        }

        const username = document.getElementById('login-username').value;
        const password = document.getElementById('login-password').value;

        try {
            const response = await fetch(loginUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ username, password })
            });

            const data = await response.json();

            if (response.ok && data.success) {
                alert('Login successful! Redirecting to home page...');
                window.location.href = homeUrl;
            } else {
                alert('Login failed: ' + (data.message || 'Unknown error'));
            }
        } catch (error) {
            console.error('Login error:', error);
            alert('Network error: Could not connect to the server.');
        }
    });

    registerForm.addEventListener('submit', async (event) => {
        event.preventDefault();

        if (!registerUrl) {
            alert('Register configuration missing. Please contact support.');
            return;
        }

        const username = document.getElementById('reg-username').value;
        const email = document.getElementById('reg-email').value;
        const password = document.getElementById('reg-password').value;

        try {
            const response = await fetch(registerUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ username, email, password })
            });

            const data = await response.json();

            if (response.ok && data.success) {
                alert('Registration successful! You can now login.');
                container.classList.remove('active');
                document.getElementById('reg-username').value = '';
                document.getElementById('reg-email').value = '';
                document.getElementById('reg-password').value = '';
            } else {
                alert('Registration failed: ' + (data.message || 'Unknown error'));
            }
        } catch (error) {
            console.error('Register error:', error);
            alert('Network error: Could not connect to the server.');
        }
    });
});

