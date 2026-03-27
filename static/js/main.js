// Mobile menu functionality
document.addEventListener('DOMContentLoaded', function () {
    const mobileMenuBtn = document.querySelector('.mobile-menu-btn');
    const navLinks = document.querySelector('.nav-links');

    if (!mobileMenuBtn || !navLinks) {
        return;
    }

    // Toggle mobile menu - ALWAYS works when button is visible
    mobileMenuBtn.addEventListener('click', function () {
        // Check if we're in mobile view (button is visible)
        const isMobileView = window.getComputedStyle(mobileMenuBtn).display !== 'none';

        if (isMobileView) {
            navLinks.classList.toggle('mobile-active');
        }
    });

    // Close mobile menu when clicking on a link or button
    const navItems = document.querySelectorAll('.nav-links a, .nav-links .login-btn');
    navItems.forEach(item => {
        item.addEventListener('click', function () {
            navLinks.classList.remove('mobile-active');
        });
    });

    // Close mobile menu when clicking outside
    document.addEventListener('click', function (event) {
        const isClickInsideNav = event.target.closest('.nav-container');
        if (!isClickInsideNav && navLinks.classList.contains('mobile-active')) {
            navLinks.classList.remove('mobile-active');
        }
    });

    // Close mobile menu on window resize (if we switch back to desktop)
    window.addEventListener('resize', function () {
        const isMobileView = window.getComputedStyle(mobileMenuBtn).display !== 'none';
        if (!isMobileView) {
            navLinks.classList.remove('mobile-active');
        }
    });

    // Login button functionality
    const loginBtn = document.querySelector('.login-btn');
    const loginUrl = loginBtn ? loginBtn.dataset.loginUrl : null;

    if (loginBtn && loginUrl) {
        loginBtn.addEventListener('click', function () {
            window.location.href = loginUrl;
        });
    }

    // Smooth scrolling for navigation links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            const targetId = this.getAttribute('href');
            if (targetId === '#') return;

            const targetElement = document.querySelector(targetId);
            if (targetElement) {
                // Calculate the scroll position, accounting for fixed navbar
                const targetPosition = targetElement.offsetTop - document.querySelector('.navbar').offsetHeight;

                window.scrollTo({
                    top: targetPosition,
                    behavior: 'smooth'
                });
            }
        });
    });

    // Add active class to current section in view
    function highlightActiveSection() {
        const sections = document.querySelectorAll('.section');
        const navLinks = document.querySelectorAll('.nav-links a');
        const navbarHeight = document.querySelector('.navbar').offsetHeight;

        let currentSection = '';

        sections.forEach(section => {
            const sectionTop = section.offsetTop - navbarHeight - 100;
            const sectionHeight = section.clientHeight;
            if (window.scrollY >= sectionTop && window.scrollY < sectionTop + sectionHeight) {
                currentSection = section.getAttribute('id');
            }
        });

        navLinks.forEach(link => {
            link.classList.remove('active');
            if (link.getAttribute('href') === `#${currentSection}`) {
                link.classList.add('active');
            }
        });
    }

    // Listen for scroll events
    window.addEventListener('scroll', highlightActiveSection);

    // Initialize
    highlightActiveSection();
});
