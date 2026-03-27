(function () {
    const config = window.PRACTICE_CONFIG || {};
    const apiUrl = config.apiUrl;
    const batchLimit = config.batchLimit || 5;

    const quizRoot = document.getElementById('quiz-root');
    const questionArea = document.getElementById('question-area');
    const progressLabel = document.getElementById('progress-label');
    const batchLabel = document.getElementById('batch-label');
    const gradeButton = document.getElementById('grade-btn');
    const resetButton = document.getElementById('reset-btn');
    const resultPanel = document.getElementById('result-panel');
    const scoreCn = document.getElementById('score-cn');
    const scoreEn = document.getElementById('score-en');
    const reviewSummary = document.getElementById('review-summary');
    const nextBatchButton = document.getElementById('next-batch-btn');

    if (!quizRoot || !questionArea || !gradeButton || !resetButton || !nextBatchButton) {
        console.error('Practice page initialisation failed: required elements are missing.');
        return;
    }

    if (!apiUrl) {
        questionArea.innerHTML = '<div class="loading">练习配置缺失，请联系管理员。</div>';
        gradeButton.disabled = true;
        nextBatchButton.disabled = true;
        return;
    }

    let totalWords = Number(config.totalWords || quizRoot.dataset.totalWords || 0);
    let nextOffset = 0;
    let hasMore = false;
    let isGraded = false;
    let currentOffset = 0;
    let questions = [];
    let batchCounter = 1;
    let coverageInfo = null;
    let coveredWords = [];
    const selections = new Map();

    gradeButton.addEventListener('click', handleGrade);
    resetButton.addEventListener('click', handleReset);
    nextBatchButton.addEventListener('click', () => {
        const offsetToUse = hasMore ? nextOffset : 0;
        loadBatch(offsetToUse, true);
    });

    loadBatch(0, false);

    async function loadBatch(offset, triggeredByNext) {
        isGraded = false;
        currentOffset = offset;
        gradeButton.disabled = true;
        nextBatchButton.disabled = true;
        nextBatchButton.textContent = '再练一批';
        hideResult();
        selections.clear();
        renderLoading('正在生成练习题，请稍候...');

        try {
            const response = await fetch(apiUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ offset, limit: batchLimit })
            });

            if (!response.ok) {
                throw new Error(`Server responded with status ${response.status}`);
            }

            const payload = await response.json();

            if (!payload.success) {
                renderMessage(payload.message || '暂时无法生成练习题，请稍后重试。');
                return;
            }

            questions = payload.questions || [];
            totalWords = payload.total_words ?? totalWords;
            nextOffset = payload.next_offset ?? 0;
            hasMore = Boolean(payload.has_more);
            coverageInfo = payload.coverage || null;
            coveredWords = Array.isArray(payload.covered_words) ? payload.covered_words : [];

            batchCounter = triggeredByNext ? batchCounter + 1 : 1;

            if (questions.length === 0) {
                renderMessage('生词表为空，请先在翻译页面添加一些生词。');
                return;
            }

            renderQuestions();
            updateStatus();
            gradeButton.disabled = selections.size !== questions.length;
        } catch (error) {
            console.error('Failed to generate practice questions:', error);
            renderMessage('练习题生成失败，请检查网络或稍后重试。');
        }
    }

    function renderLoading(message) {
        questionArea.innerHTML = `<div class="loading">${message}</div>`;
    }

    function renderMessage(message) {
        questionArea.innerHTML = `<div class="loading">${escapeHtml(message)}</div>`;
        progressLabel.textContent = '练习提示';
        batchLabel.textContent = '';
        gradeButton.disabled = true;
        coverageInfo = null;
        coveredWords = [];
    }

    function renderQuestions() {
        questionArea.innerHTML = '';
        questions.forEach((question, index) => {
            const card = document.createElement('article');
            card.className = 'question-card';
            card.dataset.index = String(index);

            const title = document.createElement('h3');
            title.className = 'question-title';
            title.textContent = `第 ${index + 1} 题：${question.question}`;
            if (question.language) {
                const langNote = document.createElement('small');
                langNote.textContent = question.language === 'chinese'
                    ? '考察中文理解'
                    : question.language === 'english'
                        ? '考察英文理解'
                        : '综合练习';
                title.appendChild(langNote);
            }
            card.appendChild(title);

            const optionsWrapper = document.createElement('div');
            optionsWrapper.className = 'options';

            (question.options || []).forEach(optionText => {
                const optionButton = document.createElement('button');
                optionButton.type = 'button';
                optionButton.className = 'option-button';
                optionButton.dataset.option = optionText;
                optionButton.textContent = optionText;
                optionButton.addEventListener('click', () => handleOptionSelect(index, optionButton));
                optionsWrapper.appendChild(optionButton);
            });

            card.appendChild(optionsWrapper);
            questionArea.appendChild(card);
        });

        gradeButton.disabled = questions.length === 0 || selections.size !== questions.length;
    }

    function handleOptionSelect(questionIndex, button) {
        if (isGraded) {
            return;
        }

        const card = button.closest('.question-card');
        const buttons = card.querySelectorAll('.option-button');
        buttons.forEach(btn => btn.classList.remove('selected'));
        button.classList.add('selected');
        selections.set(questionIndex, button.dataset.option);

        gradeButton.disabled = selections.size !== questions.length;
    }

    function handleGrade() {
        if (selections.size !== questions.length) {
            alert('请先完成所有题目再批改。');
            return;
        }

        isGraded = true;
        let correctCount = 0;
        reviewSummary.innerHTML = '';

        questions.forEach((question, index) => {
            const card = questionArea.querySelector(`.question-card[data-index="${index}"]`);
            const optionButtons = card.querySelectorAll('.option-button');
            const selectedValue = selections.get(index);
            const explanationExists = card.querySelector('.explanation');
            if (explanationExists) {
                explanationExists.remove();
            }

            optionButtons.forEach(btn => {
                btn.classList.add('disabled');
                if (btn.dataset.option === question.answer) {
                    btn.classList.add('correct');
                }
                if (btn.dataset.option === selectedValue && selectedValue !== question.answer) {
                    btn.classList.add('incorrect');
                }
            });

            if (selectedValue === question.answer) {
                correctCount += 1;
                const reviewItem = document.createElement('div');
                reviewItem.className = 'review-item correct';
                reviewItem.textContent = `第 ${index + 1} 题正确：${question.question}`;
                reviewSummary.appendChild(reviewItem);
            } else {
                const explanation = document.createElement('div');
                explanation.className = 'explanation';
                explanation.innerHTML = `<strong>正确答案：</strong>${escapeHtml(question.answer)}<br><strong>解析：</strong>${escapeHtml(question.explanation || '暂无解析')}`;
                card.appendChild(explanation);
                const reviewItem = document.createElement('div');
                reviewItem.className = 'review-item incorrect';
                reviewItem.innerHTML = `第 ${index + 1} 题：${escapeHtml(question.question)}<br><strong>正确答案：</strong>${escapeHtml(question.answer)}<br><strong>你的选择：</strong>${escapeHtml(selectedValue || '未作答')}<br><strong>解析：</strong>${escapeHtml(question.explanation || '暂无解析')}`;
                reviewSummary.appendChild(reviewItem);
            }
        });

        scoreCn.textContent = `${correctCount}/${questions.length}`;
        scoreEn.textContent = `${correctCount}/${questions.length}`;
        showResult();
        gradeButton.disabled = true;
        nextBatchButton.disabled = false;
        nextBatchButton.textContent = hasMore ? '再练一批' : '重新出题';
    }

    function handleReset() {
        selections.clear();
        isGraded = false;
        gradeButton.disabled = true;
        hideResult();

        const optionButtons = questionArea.querySelectorAll('.option-button');
        optionButtons.forEach(button => {
            button.classList.remove('selected', 'correct', 'incorrect', 'disabled');
        });

        const explanations = questionArea.querySelectorAll('.explanation');
        explanations.forEach(exp => exp.remove());
    }

    function updateStatus() {
        if (questions.length === 0) {
            progressLabel.textContent = '暂无练习题';
            batchLabel.textContent = '';
            return;
        }

        if (coverageInfo) {
            const uniqueCount = coverageInfo.unique_count ?? coveredWords.length ?? 0;
            if (uniqueCount > 0) {
                const preview = coveredWords.slice(0, 4).join('、');
                const ellipsis = coveredWords.length > 4 ? '…' : '';
                progressLabel.textContent = preview
                    ? `本批覆盖生词 ${uniqueCount} 个：${preview}${ellipsis}`
                    : `本批覆盖生词 ${uniqueCount} 个`;
            } else {
                progressLabel.textContent = `本批共 ${questions.length} 题`;
            }
        } else {
            progressLabel.textContent = `本批共 ${questions.length} 题`;
        }

        batchLabel.textContent = `Batch ${batchCounter}`;
    }

    function showResult() {
        resultPanel.classList.remove('hidden');
        resultPanel.classList.add('visible');
    }

    function hideResult() {
        resultPanel.classList.add('hidden');
        resultPanel.classList.remove('visible');
        reviewSummary.innerHTML = '';
    }

    function escapeHtml(value) {
        const div = document.createElement('div');
        div.textContent = value ?? '';
        return div.innerHTML;
    }
})();
