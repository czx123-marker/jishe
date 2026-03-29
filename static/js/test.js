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
const sourceLanguageSelect = document.getElementById('source-language');
const targetLanguageSelect = document.getElementById('target-language');
const enableDubbingCheckbox = document.getElementById('enable-dubbing');
const dubbingLanguageNote = document.getElementById('dubbing-language-note');
const dubbingResultCard = document.getElementById('dubbing-result');
const dubStatusBadge = document.getElementById('dub-status-badge');
const dubStatusMessage = document.getElementById('dub-status-message');
const dubLinks = document.getElementById('dub-links');
const dubDownloadLink = document.getElementById('dub-download-link');
const dubManifestLink = document.getElementById('dub-manifest-link');
const dubLogLink = document.getElementById('dub-log-link');

const modal = document.getElementById('word-modal');
const closeButton = document.querySelector('.close-button');
const modalWord = document.getElementById('modal-word');
const modalPinyin = document.getElementById('modal-pinyin');
const modalMeaning = document.getElementById('modal-meaning');
const modalExampleCn = document.getElementById('modal-example-cn');
const modalExampleEn = document.getElementById('modal-example-en');
const modalGrammar = document.getElementById('modal-grammar');
const addToVocabButton = document.getElementById('add-to-vocab-button');

const SUPPORTED_DUBBING_LANGUAGES = new Set(['zh', 'en', 'ja', 'ko', 'de', 'fr', 'ru', 'pt', 'es', 'it']);
const DUBBING_STATUS_META = {
    completed: {
        badge: '配音完成',
        className: 'is-completed',
        message: '已生成原声克隆配音音轨。',
    },
    failed: {
        badge: '配音失败',
        className: 'is-failed',
        message: '配音没有完成，请查看日志。',
    },
    unsupported: {
        badge: '暂不支持',
        className: 'is-unsupported',
        message: '当前目标语言不支持 Qwen3-TTS 配音。',
    },
    disabled: {
        badge: '未启用',
        className: 'is-disabled',
        message: '本次仅生成字幕，未触发配音。',
    },
};

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
    if (message && statusText) {
        statusText.textContent = message;
    }
    if (currentProgress < 85) {
        setProgress(85);
    }
    stopProgressInterval();
    progressInterval = setInterval(() => {
        if (currentProgress >= 95) {
            stopProgressInterval();
            return;
        }
        setProgress(currentProgress + 1);
    }, 700);
}

function finishProgress(message) {
    stopProgressInterval();
    setProgress(100);
    if (message && statusText) {
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
    showProgress(message || '处理失败', 0);
    setTimeout(() => {
        if (progressContainer) {
            progressContainer.style.display = 'none';
        }
    }, 1500);
}

function updateStartButtonState() {
    const hasFile = !!(fileInput && fileInput.files && fileInput.files.length > 0);
    startButton.disabled = !hasFile;
}

function updateDubbingAvailability() {
    if (!targetLanguageSelect || !enableDubbingCheckbox || !dubbingLanguageNote) {
        return;
    }

    const targetLanguage = targetLanguageSelect.value;
    const supported = SUPPORTED_DUBBING_LANGUAGES.has(targetLanguage);

    if (supported) {
        enableDubbingCheckbox.disabled = false;
        dubbingLanguageNote.textContent = '当前目标语言支持 Qwen3-TTS 配音。';
        dubbingLanguageNote.classList.remove('is-unsupported');
    } else {
        enableDubbingCheckbox.checked = false;
        enableDubbingCheckbox.disabled = true;
        dubbingLanguageNote.textContent = '当前目标语言仅生成字幕，不触发配音。';
        dubbingLanguageNote.classList.add('is-unsupported');
    }
}

function uploadFile(file) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('source_language', sourceLanguageSelect.value);
    formData.append('target_language', targetLanguageSelect.value);
    formData.append('enable_dubbing', enableDubbingCheckbox.checked ? '1' : '0');

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/process-video', true);

    xhr.onloadstart = function () {
        resetProgress('准备上传...');
    };

    xhr.upload.onprogress = function (event) {
        if (!event.lengthComputable) {
            return;
        }
        const percent = Math.round((event.loaded / event.total) * 80);
        showProgress('上传中...', percent);
    };

    xhr.upload.onload = function () {
        startProcessingProgress(enableDubbingCheckbox.checked ? '上传完成，正在翻译并准备配音...' : '上传完成，正在翻译...');
    };

    xhr.onload = function () {
        startButton.disabled = false;
        if (xhr.status !== 200) {
            handleProgressError('处理过程中发生错误。');
            try {
                const errorPayload = JSON.parse(xhr.responseText);
                alert(`Error: ${errorPayload.error}`);
            } catch (error) {
                alert('An unknown error occurred.');
            }
            return;
        }

        const response = JSON.parse(xhr.responseText);
        if (!response.success) {
            handleProgressError('处理失败，请重试。');
            alert(`Error: ${response.error}`);
            return;
        }

        finishProgress(response.dub_status === 'completed' ? '翻译和配音已完成。' : '翻译完成。');
        document.getElementById('upload-form').style.display = 'none';
        resultFilename.textContent = file.name;
        videoPlayer.src = response.video_url;
        renderSubtitles(response.data.segments || []);
        renderDubbingResult(response);
        resultContainer.style.display = 'block';
        videoPlayer.load();
        renderHistory(response.history);
    };

    xhr.onerror = function () {
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
        .then((response) => response.json())
        .then((data) => {
            if (!data.success) {
                handleProgressError('加载历史记录失败。');
                alert(`Error loading history: ${data.error}`);
                return;
            }

            finishProgress('历史记录加载完成');
            document.getElementById('upload-form').style.display = 'none';
            resultFilename.textContent = data.original_filename;
            videoPlayer.src = data.video_url;
            renderSubtitles(data.subtitle_data.segments || []);
            renderDubbingResult(data);
            resultContainer.style.display = 'block';
            videoPlayer.load();
        })
        .catch((error) => {
            handleProgressError('获取历史记录时出现错误。');
            console.error('Error fetching history item:', error);
            alert('An error occurred while fetching the history item.');
        });
}

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
            segment.words.forEach((wordInfo) => {
                const wordSpan = document.createElement('span');
                wordSpan.classList.add('clickable-word');
                wordSpan.textContent = `${wordInfo.word} `;
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

function renderDubbingResult(payload) {
    if (!dubbingResultCard) {
        return;
    }

    const status = payload.dub_status || 'disabled';
    const meta = DUBBING_STATUS_META[status] || DUBBING_STATUS_META.disabled;
    const statusMessage = payload.dub_error
        || (payload.dub_mix_status === 'dry_only' && (payload.dub_mix_error || '配音已生成，但背景回混失败，成品已回退为干声。'))
        || meta.message;

    dubbingResultCard.hidden = false;
    dubStatusBadge.textContent = meta.badge;
    dubStatusBadge.className = `dub-status-badge ${meta.className}`;
    dubStatusMessage.textContent = statusMessage;

    const hasDownloadLink = setLinkState(
        dubDownloadLink,
        status === 'completed' ? payload.video_url : null,
    );
    const hasManifestLink = setLinkState(dubManifestLink, payload.dub_manifest_url);
    const hasLogLink = setLinkState(dubLogLink, payload.dub_log_url);
    const hasAnyLink = hasDownloadLink || hasManifestLink || hasLogLink;

    dubLinks.hidden = !hasAnyLink;
}

function setLinkState(anchor, href) {
    if (!anchor) {
        return false;
    }
    if (href) {
        anchor.href = href;
        anchor.hidden = false;
        return true;
    }
    anchor.hidden = true;
    anchor.removeAttribute('href');
    return false;
}

function highlightCurrentSubtitle() {
    const currentTime = videoPlayer.currentTime;
    const lines = subtitlesContainer.querySelectorAll('.subtitle-line');
    let activeLine = null;
    let currentActiveIndex = -1;

    lines.forEach((line) => {
        line.classList.remove('active');
    });

    for (let i = 0; i < lines.length; i += 1) {
        const line = lines[i];
        const start = parseFloat(line.dataset.start);
        const end = parseFloat(line.dataset.end);
        const index = parseInt(line.dataset.index, 10);

        if (currentTime >= start && currentTime <= end) {
            line.classList.add('active');
            activeLine = line;
            currentActiveIndex = index;
            break;
        }
    }

    if (!activeLine && lines.length > 0) {
        let closestLine = null;
        let closestIndex = -1;
        let minTimeDiff = Infinity;

        for (let i = 0; i < lines.length; i += 1) {
            const line = lines[i];
            const start = parseFloat(line.dataset.start);
            const end = parseFloat(line.dataset.end);
            const index = parseInt(line.dataset.index, 10);

            let timeDiff = 0;
            if (currentTime < start) {
                timeDiff = start - currentTime;
            } else if (currentTime > end) {
                timeDiff = currentTime - end;
            }

            if (timeDiff < minTimeDiff) {
                minTimeDiff = timeDiff;
                closestLine = line;
                closestIndex = index;
            }
        }

        if (minTimeDiff <= 2) {
            closestLine.classList.add('active');
            activeLine = closestLine;
            currentActiveIndex = closestIndex;
        }
    }

    if (activeLine && currentActiveIndex !== lastActiveLineIndex) {
        activeLine.scrollIntoView({ behavior: 'smooth', block: 'center' });
        lastActiveLineIndex = currentActiveIndex;
    }
}

function renderHistory(historyData) {
    historyList.innerHTML = '';
    if (historyData && historyData.length > 0) {
        historyData.forEach((item) => {
            const li = document.createElement('li');
            const anchor = document.createElement('a');
            anchor.href = '#';
            anchor.className = 'history-item';
            anchor.dataset.historyId = item.id;
            anchor.innerHTML = `<strong>${item.original_video_name}</strong><small>${item.timestamp.split(' ')[0]}</small>`;
            li.appendChild(anchor);
            historyList.appendChild(li);
        });
    } else {
        historyList.innerHTML = '<li class="empty-message">No history yet.</li>';
    }
    renderHistoryEventListeners();
}

function renderHistoryEventListeners() {
    const historyItems = document.querySelectorAll('.history-item');
    historyItems.forEach((item) => {
        item.addEventListener('click', (event) => {
            event.preventDefault();
            loadHistoryItem(item.dataset.historyId);
        });
    });
}

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
            word,
            subtitle_language: currentSubtitleLanguage,
        }),
    })
        .then((response) => response.json())
        .then((data) => {
            if (!data.success) {
                modalMeaning.textContent = `Error: ${data.error}`;
                return;
            }

            const details = data.details;
            modalPinyin.textContent = details.pinyin || '';
            modalMeaning.textContent = details.meaning || '';
            modalExampleCn.textContent = details.example_sentence_cn || '';
            modalExampleEn.textContent = details.example_sentence_en || '';
            modalGrammar.textContent = details.grammar_note || '';
        })
        .catch((error) => {
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
        example: `${modalExampleCn.textContent}\n${modalExampleEn.textContent}`.trim(),
    };

    fetch('/add-word', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then((response) => response.json())
        .then((data) => {
            if (data.success) {
                addToVocabButton.textContent = 'Added!';
                return;
            }

            alert(data.error || 'Failed to add word.');
            addToVocabButton.textContent = 'Add to Vocabulary';
            addToVocabButton.disabled = false;
        })
        .catch((error) => {
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

    const candidate = segments.find((segment) => segment && segment.text && segment.text.trim().length) || segments[0];
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

function initHistorySidebar() {
    const overlay = document.createElement('div');
    overlay.className = 'sidebar-overlay';
    document.body.appendChild(overlay);

    const historyBtn = document.getElementById('history-btn');
    const sidebar = document.getElementById('history-sidebar');
    const sidebarToggle = document.getElementById('sidebar-toggle');

    function showSidebar(event) {
        if (event) {
            event.preventDefault();
        }
        sidebar.classList.add('show');
        overlay.classList.add('show');
    }

    function hideSidebar() {
        sidebar.classList.remove('show');
        overlay.classList.remove('show');
    }

    if (historyBtn) {
        historyBtn.addEventListener('click', showSidebar);
    }
    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', hideSidebar);
    }
    overlay.addEventListener('click', hideSidebar);
}

fileInput.addEventListener('change', () => {
    const file = fileInput.files[0];
    fileNameSpan.textContent = file ? file.name : '未选择文件';
    updateStartButtonState();
});

startButton.addEventListener('click', () => {
    const file = fileInput.files[0];
    if (!file) {
        return;
    }
    currentSubtitleLanguage = targetLanguageSelect ? targetLanguageSelect.value : currentSubtitleLanguage;
    uploadFile(file);
    startButton.disabled = true;
});

if (targetLanguageSelect) {
    targetLanguageSelect.addEventListener('change', (event) => {
        currentSubtitleLanguage = event.target.value;
        updateDubbingAvailability();
    });
}

addToVocabButton.addEventListener('click', () => {
    if (modalWord.textContent) {
        addWordToVocab();
    }
});

videoPlayer.addEventListener('timeupdate', highlightCurrentSubtitle);
closeButton.onclick = () => {
    modal.style.display = 'none';
};

window.onclick = (event) => {
    if (event.target === modal) {
        modal.style.display = 'none';
    }
};

document.addEventListener('DOMContentLoaded', () => {
    renderHistoryEventListeners();
    updateDubbingAvailability();
    updateStartButtonState();
    initHistorySidebar();
});
