/**
 * 动态加载页头文件
 * @param {string} elementId - 要插入页头的元素ID
 */
function loadHeader(elementId) {
    fetch('/template/header.html')
        .then(response => {
            if (!response.ok) {
                throw new Error('Network response was not ok');
            }
            return response.text();
        })
        .then(html => {
            const container = document.getElementById(elementId);
            if (container) {
                container.innerHTML = html;
            }
        })
        .catch(error => {
            console.error('There was a problem with the fetch operation:', error);
        });
}

// 当DOM加载完成后自动执行
document.addEventListener('DOMContentLoaded', function() {
    // 查找页面中是否有id为"header-container"的元素
    if (document.getElementById('header-container')) {
        loadHeader('header-container');
    }
});