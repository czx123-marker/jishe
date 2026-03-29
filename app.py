import os
import uuid
import json
import shutil
from flask import Flask, request, render_template, jsonify, send_from_directory, redirect, url_for, session
from werkzeug.utils import secure_filename
from core.processor import run_pipeline, get_word_details_from_llm, generate_quiz_questions_from_vocab
from core.qwen_clone_bridge import build_dubbing_result_for_history, ensure_dubbed_playback_url, run_dubbing_for_current_output
from core.utils import rprint
from core.database import init_db, add_word, get_all_words, add_translation_to_history, get_translation_history, get_history_entry, clear_vocabulary
from backend.auth_system import AuthManager

# Define absolute paths based on the application's root directory
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(APP_ROOT, 'uploads')
OUTPUT_FOLDER = os.path.join(APP_ROOT, 'output')
HISTORY_FOLDER = os.path.join(OUTPUT_FOLDER, 'history')
ALLOWED_EXTENSIONS = {'mp4', 'm4a', 'mp3', 'wav', 'mpeg', 'webm', 'mov'}
AUTH_DB_PATH = os.path.join(APP_ROOT, 'backend', 'users.db')
QUIZ_BATCH_LIMIT = 5

app = Flask(__name__, template_folder='frontend', static_folder='static')
app.secret_key = os.urandom(24) # Needed to use sessions
auth_manager = AuthManager(db_path=AUTH_DB_PATH)

# Store absolute paths in app.config for consistency
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['HISTORY_FOLDER'] = HISTORY_FOLDER

# Create necessary folders using absolute paths
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)
os.makedirs(app.config['HISTORY_FOLDER'], exist_ok=True)

# Initialize the database and clear vocabulary
init_db()
clear_vocabulary()

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def is_truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_local_media_path(media_url: str | None) -> str | None:
    normalized = str(media_url or "").strip()
    if not normalized:
        return None
    if normalized.startswith("/uploads/"):
        relative = normalized.removeprefix("/uploads/").replace("/", os.sep)
        return os.path.join(app.config['UPLOAD_FOLDER'], relative)
    if normalized.startswith("/output/"):
        relative = normalized.removeprefix("/output/").replace("/", os.sep)
        return os.path.join(app.config['OUTPUT_FOLDER'], relative)
    if os.path.isabs(normalized):
        return normalized
    return None

@app.route('/')
def index():
    return render_template('home.html')


@app.route('/login')
def login_page():
    return render_template('login.html')


@app.route('/video-translation.html')
@app.route('/video-translation')
@app.route('/test.html')
def video_translation_page():
    # Establish a session ID if one doesn't exist
    if 'session_id' not in session:
        session['session_id'] = uuid.uuid4().hex
    
    # Load history for the current session
    history_list = get_translation_history(session_id=session['session_id'])  # 每次都从数据库获取最新数据
    return render_template('test.html', history=history_list)

@app.route('/new')
def new_translation():
    """Redirects to the main video translation page to start a new session."""
    return redirect(url_for('video_translation_page'))

@app.route('/vocabulary.html')
def vocabulary_page():
    words = get_all_words()
    return render_template('vocabulary.html', words=words)


@app.route('/practice')
def practice_page():
    total_words = len(get_all_words())
    return render_template('lianxiti.html', total_words=total_words)


@app.route('/api/practice/quizzes', methods=['POST'])
def generate_practice_quizzes():
    data = request.get_json() or {}

    try:
        offset = max(int(data.get('offset', 0)), 0)
    except (TypeError, ValueError):
        offset = 0

    # 强制每批生成固定数量的题目
    desired_count = QUIZ_BATCH_LIMIT

    words = get_all_words()
    total = len(words)

    if total == 0:
        return jsonify({
            "success": False,
            "message": "你的生词本为空，请先在翻译页面添加生词。"
        })

    focus_cycle = ["meaning", "usage", "example", "pronunciation", "grammar"]
    focus_cycle_len = len(focus_cycle)

    word_indices = []
    selected_entries = []
    focuses = []

    for idx in range(desired_count):
        base_index = (offset + idx) % total
        word_indices.append(base_index)
        entry_copy = dict(words[base_index])
        selected_entries.append(entry_copy)
        focuses.append(focus_cycle[idx % focus_cycle_len])

    batch_token = uuid.uuid4().hex[:8]
    questions = generate_quiz_questions_from_vocab(
        selected_entries,
        full_pool=words,
        focuses=focuses,
        batch_token=batch_token
    )

    unique_indices = list(dict.fromkeys(word_indices))
    covered_words = [words[i]["word"] for i in unique_indices]

    next_offset = (offset + desired_count) % total if total else 0

    return jsonify({
        "success": True,
        "questions": questions,
        "count": len(questions),
        "next_offset": next_offset,
        "has_more": total > 0,
        "total_words": total,
        "coverage": {
            "unique_count": len(unique_indices),
            "indices": unique_indices,
            "batch_token": batch_token
        },
        "covered_words": covered_words
    })


@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify({"success": False, "message": "Username and password are required."}), 400

    success, result = auth_manager.login(username, password)
    if success:
        session['user'] = result
        return jsonify({"success": True, "message": "Login successful", "user": result})

    return jsonify({"success": False, "message": result}), 401


@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()

    if not username or not email or not password:
        return jsonify({"success": False, "message": "Username, email, and password are required."}), 400

    success, message = auth_manager.register(username, email, password)
    status_code = 200 if success else 400
    return jsonify({"success": success, "message": message}), status_code


@app.route('/api/test', methods=['GET'])
def api_test():
    return jsonify({"message": "API is working!"})

@app.route('/process-video', methods=['POST'])
def process_video():
    if 'session_id' not in session:
        return jsonify({"error": "Session not initialized"}), 400
    session_id = session['session_id']

    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    if file and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        file_uuid = uuid.uuid4().hex
        filename = f"{file_uuid}_{original_filename}"
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(video_path)
        rprint(f"[green]文件已上传至: {video_path}[/green]")

        source_lang = request.form.get('source_language', 'auto')
        target_lang = request.form.get('target_language', 'en') # 使用 'en' 作为默认值
        enable_dubbing = is_truthy(request.form.get('enable_dubbing'))

        try:
            final_json_path = run_pipeline(video_path, source_lang=source_lang, target_lang=target_lang)

            if final_json_path and os.path.exists(final_json_path):
                history_subtitle_filename = f"{file_uuid}.json"
                history_subtitle_path = os.path.join(app.config['HISTORY_FOLDER'], history_subtitle_filename)
                shutil.copy(final_json_path, history_subtitle_path)

                video_url = f"/uploads/{filename}"
                add_translation_to_history(original_filename, video_url, history_subtitle_path, session_id=session_id)

                with open(history_subtitle_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                dubbing_result = (
                    run_dubbing_for_current_output(
                        file_uuid,
                        target_language=target_lang,
                        source_language=source_lang,
                    )
                    if enable_dubbing
                    else build_dubbing_result_for_history(history_subtitle_path)
                )
                playback_url = (
                    ensure_dubbed_playback_url(file_uuid, video_path)
                    if dubbing_result.status == "completed"
                    else None
                )

                updated_history = get_translation_history(session_id=session_id)
                response_payload = {
                    "success": True,
                    "data": data,
                    "video_url": playback_url or video_url,
                    "history": updated_history,
                }
                response_payload.update(dubbing_result.to_dict())
                return jsonify(response_payload)
            else:
                return jsonify({"error": "Processing failed, result file not found."}, 500)

        except Exception as e:
            rprint(f"[bold red]处理视频时发生错误: {e}[/bold red]")
            import traceback
            traceback.print_exc()
            return jsonify({"error": f"An internal error occurred: {str(e)}"}), 500

    return jsonify({"error": "File type not allowed"}), 400

@app.route('/get-history-entry/<int:history_id>')
def get_history_entry_json(history_id):
    if 'session_id' not in session:
        return jsonify({"success": False, "error": "Session not initialized"}), 400
    session_id = session['session_id']

    entry = get_history_entry(history_id, session_id=session_id)
    if not entry:
        return jsonify({"success": False, "error": "History not found or access denied"}), 404
    
    try:
        with open(entry['subtitles_path'], 'r', encoding='utf-8') as f:
            subtitle_data = json.load(f)
        dubbing_result = build_dubbing_result_for_history(entry['subtitles_path'])
        playback_url = (
            ensure_dubbed_playback_url(
                os.path.splitext(os.path.basename(entry['subtitles_path']))[0],
                resolve_local_media_path(entry['video_path']),
            )
            if dubbing_result.status == "completed"
            else None
        )
        video_url = playback_url or entry['video_path']
        
        return jsonify({
            "success": True,
            "video_url": video_url,
            "subtitle_data": subtitle_data,
            "original_filename": entry['original_video_name'],
            **dubbing_result.to_dict(),
        })
    except FileNotFoundError:
        return jsonify({"success": False, "error": "Subtitle file not found for this history entry."}, 404)
    except Exception as e:
        rprint(f"[bold red]Error loading history entry {history_id}: {e}[/bold red]")
        return jsonify({"success": False, "error": "An error occurred while loading this history entry."}, 500)

# --- Other Routes (mostly unchanged) ---

@app.route('/get-word-details', methods=['POST'])
def get_word_details():
    data = request.get_json() or {}
    word = data.get('word')
    subtitle_language = (data.get('subtitle_language') or '').lower()

    if not word:
        return jsonify({"error": "Word not provided"}), 400

    try:
        raw_details = get_word_details_from_llm(word, subtitle_language=subtitle_language)

        if isinstance(raw_details, dict):
            details = raw_details.copy()
        else:
            details = {}

        if subtitle_language.startswith('en'):
            normalized = {
                "pinyin": details.get('ipa') or details.get('pinyin') or '',
                "meaning": details.get('meaning_cn') or details.get('meaning') or '',
                "example_sentence_cn": details.get('example_sentence_cn') or '',
                "example_sentence_en": details.get('example_sentence_en') or '',
                "grammar_note": details.get('usage_note') or details.get('grammar_note') or ''
            }
            ipa_value = normalized.get('pinyin')
            if ipa_value and not ipa_value.lower().startswith('ipa'):
                normalized['pinyin'] = f"IPA: {ipa_value}"
            elif ipa_value:
                normalized['pinyin'] = ipa_value
            else:
                normalized['pinyin'] = 'IPA: 未提供'
        else:
            normalized = {
                "pinyin": details.get('pinyin') or '',
                "meaning": details.get('meaning') or '',
                "example_sentence_cn": details.get('example_sentence_cn') or '',
                "example_sentence_en": details.get('example_sentence_en') or '',
                "grammar_note": details.get('grammar_note') or details.get('usage_note') or ''
            }

        return jsonify({"success": True, "details": normalized})
    except Exception as e:
        rprint(f"[bold red]获取单词详情时发生错误: {e}[/bold red]")
        return jsonify({"error": f"An internal error occurred while fetching word details: {str(e)}"}), 500

@app.route('/add-word', methods=['POST'])
def add_word_route():
    data = request.get_json()
    word = data.get('word')
    pinyin = data.get('pinyin')
    definition = data.get('definition')
    example = data.get('example')

    if not word:
        return jsonify({"success": False, "error": "Word not provided"}), 400
    
    success, message = add_word(word, pinyin, definition, example)
    if success:
        return jsonify({"success": True, "message": message})
    else:
        return jsonify({"success": False, "error": message}), 500

@app.route('/uploads/<filename>')
def send_upload_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/output/<path:filename>')
def send_output_file(filename):
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename)


if __name__ == '__main__':
    if not os.path.exists('config.json'):
        rprint("[bold red]错误: 配置文件 'config.json' 未找到。请先参考原项目创建一个。[/bold red]")
    else:
        app.run(debug=False, port=5001)
