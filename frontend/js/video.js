  const fileInput = document.getElementById('file-input');
    const fileNameSpan = document.getElementById('file-name');
    const startButton = document.getElementById('start-button');
    const progressContainer = document.getElementById('progress-container');
    const progressBarFill = document.getElementById('progress-bar-fill');
    const statusText = document.getElementById('status-text');
    const resultContainer = document.getElementById('result-container');
    const resultFilename = document.getElementById('result-filename');
    const videoPlayer = document.getElementById('video-player');
    const subtitlesContainer = document.getElementById('subtitles');
    const historyList = document.getElementById('history-list');

    // Modal Element Constants
    const modal = document.getElementById('word-modal');
    const closeButton = document.querySelector('.close-button');
    const modalWord = document.getElementById('modal-word');
    const modalPinyin = document.getElementById('modal-pinyin');
    const modalMeaning = document.getElementById('modal-meaning');
    const modalExampleCn = document.getElementById('modal-example-cn');
    const modalExampleEn = document.getElementById('modal-example-en');
    const modalGrammar = document.getElementById('modal-grammar');
    const addToVocabButton = document.getElementById('add-to-vocab-button');

    // Global State
    let lastActiveLineIndex = -1;

    // --- Event Listeners ---

    fileInput.addEventListener('change', () => {
        const file = fileInput.files[0];
        if (file) {
            fileNameSpan.textContent = file.name;
            startButton.disabled = false;
        } else {
            fileNameSpan.textContent = 'No file chosen';
            startButton.disabled = true;
        }
    });

    startButton.addEventListener('click', () => {
        const file = fileInput.files[0];
        if (file) {
            uploadFile(file);
            startButton.disabled = true;
        }
    });

    addToVocabButton.addEventListener('click', () => {
        if (modalWord.textContent) addWordToVocab();
    });

    videoPlayer.addEventListener('timeupdate', highlightCurrentSubtitle);
    
    closeButton.onclick = () => { modal.style.display = 'none'; };
    window.onclick = (event) => { if (event.target == modal) modal.style.display = 'none'; };

    // Initialize history item listeners on page load
    document.addEventListener('DOMContentLoaded', () => {
        renderHistoryEventListeners();
    });

    // --- Core Functions ---

    function uploadFile(file) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('source_language', document.getElementById('source-language').value);
        formData.append('target_language', document.getElementById('target-language').value);

        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/process-video', true);

        xhr.onloadstart = function() {
            progressContainer.style.display = 'block';
            statusText.textContent = 'Translating...';
        };

        xhr.onload = function() {
            startButton.disabled = false;
            progressContainer.style.display = 'none';
            if (xhr.status === 200) {
                const response = JSON.parse(xhr.responseText);
                if (response.success) {
                    statusText.textContent = 'Processing complete!';
                    document.getElementById('upload-form').style.display = 'none'; // Hide the upload form

                    // Display results
                    resultFilename.textContent = file.name;
                    videoPlayer.src = response.video_url;
                    renderSubtitles(response.data.segments || []);
                    resultContainer.style.display = 'block';
                    videoPlayer.load(); // Important to load the new source
                    
                    // Update history sidebar
                    renderHistory(response.history);
                } else {
                    alert('Error: ' + response.error);
                }
            } else {
                statusText.textContent = 'An error occurred during processing.';
                try {
                    alert('Error: ' + JSON.parse(xhr.responseText).error);
                } catch (e) {
                    alert('An unknown error occurred.');
                }
            }
        };

        xhr.onerror = function() {
            startButton.disabled = false;
            progressContainer.style.display = 'none';
            statusText.textContent = 'An error occurred during the upload.';
            alert('An error occurred during the upload. Please check your network connection.');
        };

        xhr.send(formData);
    }

    function loadHistoryItem(historyId) {
        statusText.textContent = `Loading history item...`;
        progressContainer.style.display = 'block';
        resultContainer.style.display = 'none';

        fetch(`/get-history-entry/${historyId}`)
            .then(response => response.json())
            .then(data => {
                progressContainer.style.display = 'none';
                if (data.success) {
                    document.getElementById('upload-form').style.display = 'none'; // Hide the upload form
                    resultFilename.textContent = data.original_filename;
                    videoPlayer.src = data.video_url;
                    renderSubtitles(data.subtitle_data.segments || []);
                    resultContainer.style.display = 'block';
                    videoPlayer.load(); // Important to load the new source
                } else {
                    alert('Error loading history: ' + data.error);
                }
            })
            .catch(error => {
                progressContainer.style.display = 'none';
                console.error('Error fetching history item:', error);
                alert('An error occurred while fetching the history item.');
            });
    }

    // --- Rendering and UI Functions ---

    function renderSubtitles(segments) {
        subtitlesContainer.innerHTML = '';
        (segments || []).forEach((segment, index) => {
            const line = document.createElement('p');
            line.classList.add('subtitle-line');
            line.dataset.start = segment.start;
            line.dataset.end = segment.end;
            line.dataset.index = index;

            if (segment.words && segment.words.length > 0) {
                segment.words.forEach(wordInfo => {
                    const wordSpan = document.createElement('span');
                    wordSpan.classList.add('clickable-word');
                    wordSpan.textContent = wordInfo.word + ' ';
                    wordSpan.dataset.word = wordInfo.word;
                    wordSpan.addEventListener('click', () => {
                        showWordDetails(wordInfo.word);
                    });
                    line.appendChild(wordSpan);
                });
            } else {
                line.textContent = segment.text;
            }
            subtitlesContainer.appendChild(line);
        });
    }

    function highlightCurrentSubtitle() {
        const currentTime = videoPlayer.currentTime;
        const lines = subtitlesContainer.querySelectorAll('.subtitle-line');
        let activeLine = null;
        let currentActiveIndex = -1;

        lines.forEach(line => {
            const start = parseFloat(line.dataset.start);
            const end = parseFloat(line.dataset.end);
            const index = parseInt(line.dataset.index, 10);

            if (currentTime >= start && currentTime <= end) {
                line.classList.add('active');
                activeLine = line;
                currentActiveIndex = index;
            } else {
                line.classList.remove('active');
            }
        });

        if (activeLine && currentActiveIndex !== lastActiveLineIndex) {
            activeLine.scrollIntoView({ behavior: 'smooth', block: 'center' });
            lastActiveLineIndex = currentActiveIndex;
        }
    }

    function renderHistory(historyData) {
        historyList.innerHTML = ''; // Clear existing list
        if (historyData && historyData.length > 0) {
            historyData.forEach(item => {
                const li = document.createElement('li');
                const a = document.createElement('a');
                a.href = '#';
                a.className = 'history-item';
                a.dataset.historyId = item.id;
                a.innerHTML = `<strong>${item.original_video_name}</strong><small>${item.timestamp.split(' ')[0]}</small>`;
                li.appendChild(a);
                historyList.appendChild(li);
            });
        } else {
            historyList.innerHTML = '<p class="empty-message">No history yet.</p>';
        }
        // Re-attach event listeners to the new history items
        renderHistoryEventListeners();
    }

    function renderHistoryEventListeners() {
        const historyItems = document.querySelectorAll('.history-item');
        historyItems.forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                const historyId = item.dataset.historyId;
                loadHistoryItem(historyId);
            });
        });
    }

    // --- Word Details and Vocabulary Functions ---

    function showWordDetails(word) {
        addToVocabButton.disabled = false;
        addToVocabButton.textContent = 'Add to Vocabulary';
        modalWord.textContent = word;
        modalPinyin.textContent = 'Loading...';
        modalMeaning.textContent = '...';
        modalExampleCn.textContent = '...';
        modalExampleEn.textContent = '...';
        modalGrammar.textContent = '...';
        modal.style.display = 'block';

        fetch('/get-word-details', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ word: word }),
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const details = data.details;
                modalPinyin.textContent = details.pinyin;
                modalMeaning.textContent = details.meaning;
                modalExampleCn.textContent = details.example_sentence_cn;
                modalExampleEn.textContent = details.example_sentence_en;
                modalGrammar.textContent = details.grammar_note;
            } else {
                modalMeaning.textContent = `Error: ${data.error}`;
            }
        })
        .catch(error => {
            console.error('Error fetching word details:', error);
            modalMeaning.textContent = 'Failed to load details. Please try again.';
        });
    }

    function addWordToVocab() {
        addToVocabButton.disabled = true;
        addToVocabButton.textContent = 'Adding...';

        const payload = {
            word: modalWord.textContent,
            pinyin: modalPinyin.textContent,
            definition: modalMeaning.textContent,
            example: (modalExampleCn.textContent + '\n' + modalExampleEn.textContent).trim()
        };

        fetch('/add-word', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                addToVocabButton.textContent = 'Added!';
            } else {
                alert(data.error || 'Failed to add word.');
                addToVocabButton.textContent = 'Add to Vocabulary';
                addToVocabButton.disabled = false;
            }
        })
        .catch(error => {
            console.error('Error adding word:', error);
            alert('An error occurred while adding the word.');
            addToVocabButton.textContent = 'Add to Vocabulary';
            addToVocabButton.disabled = false;
        });
    }


// 历史记录侧边栏控制功能
function initHistorySidebar() {
    // 创建遮罩层
    const overlay = document.createElement('div');
    overlay.className = 'sidebar-overlay';
    document.body.appendChild(overlay);
    
    // 获取相关元素
    const historyBtn = document.getElementById('history-btn');
    const sidebar = document.getElementById('history-sidebar');
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const showHistoryBtn = document.getElementById('show-history');
    
    // 显示侧边栏函数
    function showSidebar() {
        sidebar.classList.add('show');
        overlay.classList.add('show');
    }
    
    // 隐藏侧边栏函数
    function hideSidebar() {
        sidebar.classList.remove('show');
        overlay.classList.remove('show');
    }
    
    // 添加点击事件监听（添加null检查）
    if (historyBtn) {
        historyBtn.addEventListener('click', showSidebar);
    }
    
    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', hideSidebar);
    }
    
    if (showHistoryBtn) {
        showHistoryBtn.addEventListener('click', hideSidebar);
    }
    
    if (overlay) {
        overlay.addEventListener('click', hideSidebar);
    }
}