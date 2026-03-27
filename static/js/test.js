
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
    const progressPercent = document.getElementById('progress-percent');
    const targetLanguageSelect = document.getElementById('target-language');

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
    let currentSubtitleLanguage = targetLanguageSelect ? targetLanguageSelect.value : 'zh';
    let progressInterval = null;
    let currentProgress = 0;

    function stopProgressInterval() {
        if (progressInterval) {
            clearInterval(progressInterval);
            progressInterval = null;
        }
    }

    function setProgress(value) {
        currentProgress = Math.max(0, Math.min(100, Math.round(value)));
        if (progressBarFill) {
            progressBarFill.style.width = `${currentProgress}%`;
        }
        if (progressPercent) {
            progressPercent.textContent = `${currentProgress}%`;
        }
    }

    function showProgress(message, value = null) {
        if (progressContainer) {
            progressContainer.style.display = 'block';
        }
        if (statusText) {
            statusText.textContent = message;
        }
        if (value !== null) {
            setProgress(value);
        }
    }

    function resetProgress(message = '准备上传...') {
        stopProgressInterval();
        setProgress(0);
        showProgress(message, 0);
    }

    function startProcessingProgress(message) {
        if (message) {
            statusText.textContent = message;
        }
        if (currentProgress < 85) {
            setProgress(85);
        }
        stopProgressInterval();
        progressInterval = setInterval(() => {
            if (currentProgress >= 95) {
                stopProgressInterval();
            } else {
                setProgress(currentProgress + 1);
            }
        }, 700);
    }

    function finishProgress(message) {
        stopProgressInterval();
        setProgress(100);
        if (message) {
            statusText.textContent = message;
        }
        setTimeout(() => {
            if (progressContainer) {
                progressContainer.style.display = 'none';
            }
        }, 600);
    }

    function handleProgressError(message) {
        stopProgressInterval();
        showProgress(message || '发生错误', 0);
        setTimeout(() => {
            if (progressContainer) {
                progressContainer.style.display = 'none';
            }
        }, 1500);
    }

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
            if (targetLanguageSelect) {
                currentSubtitleLanguage = targetLanguageSelect.value;
            }
            uploadFile(file);
            startButton.disabled = true;
        }
    });

    if (targetLanguageSelect) {
        targetLanguageSelect.addEventListener('change', (event) => {
            currentSubtitleLanguage = event.target.value;
        });
    }

    addToVocabButton.addEventListener('click', () => {
        if (modalWord.textContent) addWordToVocab();
    });

    videoPlayer.addEventListener('timeupdate', highlightCurrentSubtitle);
    
    closeButton.onclick = () => { modal.style.display = 'none'; };
    window.onclick = (event) => { if (event.target == modal) modal.style.display = 'none'; };

    // Initialize history item listeners on page load
    document.addEventListener('DOMContentLoaded', () => {
        renderHistoryEventListeners();
        initHistorySidebar();
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
            resetProgress('准备上传...');
        };

        xhr.upload.onprogress = function(event) {
            if (event.lengthComputable) {
                const percent = Math.round((event.loaded / event.total) * 80);
                showProgress('上传中...', percent);
            }
        };

        xhr.upload.onload = function() {
            startProcessingProgress('上传完成，正在翻译...');
        };

        xhr.onload = function() {
            startButton.disabled = false;
            if (xhr.status === 200) {
                const response = JSON.parse(xhr.responseText);
                if (response.success) {
                    finishProgress('翻译完成！');
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
                    handleProgressError('处理失败，请重试。');
                    alert('Error: ' + response.error);
                }
            } else {
                handleProgressError('处理过程中发生错误。');
                try {
                    alert('Error: ' + JSON.parse(xhr.responseText).error);
                } catch (e) {
                    alert('An unknown error occurred.');
                }
            }
        };

        xhr.onerror = function() {
            startButton.disabled = false;
            handleProgressError('上传过程中出现问题，请检查网络连接。');
            alert('An error occurred during the upload. Please check your network connection.');
        };

        xhr.send(formData);
    }

    function loadHistoryItem(historyId) {
        resetProgress('正在加载历史记录...');
        startProcessingProgress('正在加载历史记录...');
        resultContainer.style.display = 'none';

        fetch(`/get-history-entry/${historyId}`)
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    finishProgress('历史记录加载完成');
                    document.getElementById('upload-form').style.display = 'none'; // Hide the upload form
                    resultFilename.textContent = data.original_filename;
                    videoPlayer.src = data.video_url;
                    renderSubtitles(data.subtitle_data.segments || []);
                    resultContainer.style.display = 'block';
                    videoPlayer.load(); // Important to load the new source
                } else {
                    handleProgressError('加载历史记录失败。');
                    alert('Error loading history: ' + data.error);
                }
            })
            .catch(error => {
                handleProgressError('获取历史记录时出现错误。');
                console.error('Error fetching history item:', error);
                alert('An error occurred while fetching the history item.');
            });
    }

    // --- Rendering and UI Functions ---

    function renderSubtitles(segments) {
        subtitlesContainer.innerHTML = '';
        lastActiveLineIndex = -1;
        subtitlesContainer.scrollTop = 0;
        currentSubtitleLanguage = inferSubtitleLanguageFromSegments(segments, currentSubtitleLanguage);
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

        // 先移除所有激活状态
        lines.forEach(line => {
            line.classList.remove('active');
        });

        // 查找当前时间对应的字幕行或最接近的字幕行
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            const start = parseFloat(line.dataset.start);
            const end = parseFloat(line.dataset.end);
            const index = parseInt(line.dataset.index, 10);

            // 精确匹配当前时间范围
            if (currentTime >= start && currentTime <= end) {
                line.classList.add('active');
                activeLine = line;
                currentActiveIndex = index;
                break; // 找到第一个匹配的就退出循环，避免多行高亮
            }
        }

        // 如果没有精确匹配，尝试找到最近的字幕行
        if (!activeLine && lines.length > 0) {
            // 寻找最接近当前时间的字幕行
            let closestLine = null;
            let closestIndex = -1;
            let minTimeDiff = Infinity;

            for (let i = 0; i < lines.length; i++) {
                const line = lines[i];
                const start = parseFloat(line.dataset.start);
                const end = parseFloat(line.dataset.end);
                const index = parseInt(line.dataset.index, 10);

                // 计算与当前片段的时间差
                let timeDiff;
                if (currentTime < start) {
                    timeDiff = start - currentTime; // 当前时间早于片段开始
                } else if (currentTime > end) {
                    timeDiff = currentTime - end; // 当前时间晚于片段结束
                } else {
                    timeDiff = 0; // 在片段时间内
                }

                if (timeDiff < minTimeDiff) {
                    minTimeDiff = timeDiff;
                    closestLine = line;
                    closestIndex = index;
                }
            }

            // 如果时间差在合理范围内（比如2秒内），则高亮最近的字幕行
            if (minTimeDiff <= 2) {
                closestLine.classList.add('active');
                activeLine = closestLine;
                currentActiveIndex = closestIndex;
            }
        }

        if (activeLine && currentActiveIndex !== lastActiveLineIndex) {
            // 确保滚动到视图中
            activeLine.scrollIntoView({ 
                behavior: 'smooth', 
                block: 'center' // 改为中心对齐，更便于观看
            });
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
            historyList.innerHTML = '<li class="empty-message">No history yet.</li>';
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
            body: JSON.stringify({
                word: word,
                subtitle_language: currentSubtitleLanguage
            }),
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const details = data.details;
                if (currentSubtitleLanguage.startsWith('en')) {
                    modalPinyin.textContent = details.pinyin || '';
                } else {
                    modalPinyin.textContent = details.pinyin;
                }
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

    function inferSubtitleLanguageFromSegments(segments, fallback) {
        if (!segments || !segments.length) {
            return fallback || 'zh';
        }

        const candidate = segments.find(seg => seg && seg.text && seg.text.trim().length) || segments[0];
        const sampleText = (candidate && candidate.text) || '';
        const chineseChars = (sampleText.match(/[\u4e00-\u9fff]/g) || []).length;
        const latinChars = (sampleText.match(/[A-Za-z]/g) || []).length;

        if (latinChars > chineseChars && latinChars > 0) {
            return 'en';
        }
        if (chineseChars > 0) {
            return 'zh';
        }
        return fallback || 'zh';
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