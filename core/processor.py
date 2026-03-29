import os
import shutil
import json
import random
import jieba
from typing import Dict, List, Optional
from rich.console import Console

# 直接从具体模块导入，避免 __init__.py 的循环导入问题
from . import _2_asr
from . import _3_1_split_nlp
from . import _3_2_split_meaning
from . import _4_1_summarize
from . import _4_2_translate
from . import _5_split_sub
from . import _6_gen_sub
# from . import _7_sub_into_vid
from .utils import load_key, rprint, models, ask_gpt, update_key
import pandas as pd

OUTPUT_DIR = "output"
console = Console()

def clear_output_directory():
    """清理 output 目录，为新的处理任务做准备，但保留 history 子目录和数据库文件。"""
    history_dir_name = "history"
    dubbing_dir_name = "dub"
    db_filename = "main.db"
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        return

    rprint(f"[yellow]清理旧的 output 目录内容（保留 history 和数据库）...[/yellow]")
    for item in os.listdir(OUTPUT_DIR):
        item_path = os.path.join(OUTPUT_DIR, item)
        # 跳过 history 目录和数据库文件
        if item in {history_dir_name, dubbing_dir_name, db_filename}:
            continue
        try:
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
        except Exception as e:
            rprint(f"[red]清理 {item_path} 时出错: {e}[/red]")

def prepare_video_file(video_path: str):
    """将上传的视频文件复制到 output 目录中，这是现有流程所期望的。"""
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频文件不存在于 '{video_path}'")

    base_name = os.path.basename(video_path)
    target_video_path = os.path.join(OUTPUT_DIR, base_name)
    shutil.copy(video_path, target_video_path)
    rprint(f"[green]视频文件已复制到 '{target_video_path}'[/green]")
    return target_video_path

def combine_results():
    """将 ASR 结果和翻译结果合并成一个前端友好的 JSON。"""
    rprint("[cyan]开始合并最终结果...[/cyan]")
    # df_trans 包含 'Source', 'Translation', 'start', 'end'
    df_trans = pd.read_excel(models._4_2_TRANSLATION)

    segments = []
    for _, row in df_trans.iterrows():
        translation_text = str(row['Translation']) if pd.notna(row['Translation']) else ""
        start_time = float(row['start']) if pd.notna(row['start']) else 0.0
        end_time = float(row['end']) if pd.notna(row['end']) else 0.0
        # 使用jieba对翻译后的中文进行分词，为前端提供可点击的单词
        translated_words = jieba.lcut(translation_text)
        
        # 构建前端期望的单个 segment 对象
        segment_obj = {
            "start": start_time,
            "end": end_time,
            "text": translation_text,  # 用于整行显示的翻译文本
            "source": row['Source'],    # 源文本，为完整性保留
            "words": [{"word": w} for w in translated_words] # 用于生成可点击单词的数组
        }
        segments.append(segment_obj)

    # 最终的数据结构是一个包含 'segments' 键的字典
    final_data = {"segments": segments}

    final_json_path = os.path.join(OUTPUT_DIR, "final_result.json")
    with open(final_json_path, 'w', encoding='utf-8') as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
    
    rprint(f"[bold green]Final result saved to {final_json_path}[/bold green]")
    return final_json_path

def run_pipeline(video_path: str, source_lang: str = 'auto', target_lang: str = '英文'):
    """
    从视频文件生成可交互字幕的完整流程。
    """
    # ---
    # 动态更新配置
    rprint(f"[blue]动态设置语言: 源语言 -> {source_lang}, 目标语言 -> {target_lang}[/blue]")
    if source_lang == 'auto':
        update_key('whisper.auto_detect_language', True)
    else:
        update_key('whisper.auto_detect_language', False)
        update_key('whisper.language', source_lang)
    update_key('target_language', target_lang)
    # ---

    clear_output_directory()
    prepare_video_file(video_path)

    console.rule("[bold cyan]1. 开始语音识别 (ASR)[/bold cyan]")
    _2_asr.transcribe()

    console.rule("[bold cyan]2. 开始句子分割[/bold cyan]")
    _3_1_split_nlp.split_by_spacy()
    _3_2_split_meaning.split_sentences_by_meaning()

    console.rule("[bold cyan]3. 开始总结与翻译[/bold cyan]")
    _4_1_summarize.get_summary()
    _4_2_translate.translate_all()

    console.rule("[bold cyan]4. 切割长字幕并生成最终字幕文件[/bold cyan]")
    _5_split_sub.split_for_sub_main()
    _6_gen_sub.align_timestamp_main()
    # Keep the timestamp alignment step so final_result.json uses aligned timings.

    # if load_key("burn_in_subtitles"):
    #     console.rule("[bold cyan]5. 烧录字幕到视频[/bold cyan]")
    #     # _7_sub_into_vid.burn_subtitles_to_video()

    console.rule("[bold cyan]5. 合并最终结果[/bold cyan]")
    final_json_path = combine_results()

    return final_json_path

def get_word_details_from_llm(word: str, subtitle_language: str = None):
    """
    使用 LLM 获取单词的详细信息（根据字幕语言定制输出）。
    """
    subtitle_language = (subtitle_language or '').lower()
    rprint(f"[cyan]正在为单词 '{word}' 查询详细信息（字幕语言: {subtitle_language or '默认'}）...[/cyan]")

    if subtitle_language.startswith('en'):
        prompt = f'''
        你是一位熟悉英语教学的中文老师，正在帮助母语为中文的学习者理解一个英文单词。
        请严格按照以下 JSON 格式返回内容，不要添加任何额外文本：

        单词: "{word}"

        JSON 格式:
        {{
          "ipa": "该英文单词的国际音标 (IPA)",
          "meaning_cn": "使用简洁中文写出的释义",
          "example_sentence_en": "一个包含该英文单词的英文例句",
          "example_sentence_cn": "上述英文例句的中文翻译",
          "usage_note": "关于该词常见搭配或语法提示的简短中文说明"
        }}
        '''
    else:
        prompt = f'''
        你是一个专业的中文老师。请为一个中文单词提供详细的解释，以便英语母语者学习。
        请严格按照以下 JSON 格式返回，不要添加任何额外的解释或文本。

        单词: "{word}"

        JSON 格式:
        {{
          "pinyin": "单词的拼音",
          "meaning": "简单易懂的英文释义",
          "example_sentence_cn": "一个包含该单词的中文例句",
          "example_sentence_en": "上面中文例句的英文翻译",
          "grammar_note": "关于这个词的常见用法或语法要点的简短说明 (in English)"
        }}
        '''

    try:
        response = ask_gpt(
            prompt,
            resp_type="json",
            log_title=f"word_detail_{word}_{subtitle_language or 'default'}"
        )
        if isinstance(response, str):
            details = json.loads(response)
        else:
            details = response
        return details
    except Exception as e:
        rprint(f"[bold red]解析 LLM 返回的单词详情时出错: {e}[/bold red]")
        raise


def _detect_language_category(word: str) -> str:
    """粗略判断单词所属语言类别，用于生成提示。"""
    if not word:
        return "other"

    for char in word:
        if '\u4e00' <= char <= '\u9fff':
            return "chinese"

    if any(ch.isalpha() for ch in word):
        return "english"

    return "other"


def _validate_quiz_payload(resp: Dict, expected_count: int) -> Dict[str, str]:
    if not isinstance(resp, dict):
        return {"status": "error", "message": "Response is not a JSON object."}

    questions = resp.get("questions")
    if not isinstance(questions, list):
        return {"status": "error", "message": "Missing 'questions' array."}

    if len(questions) != expected_count:
        return {"status": "error", "message": f"Expected {expected_count} questions, got {len(questions)}."}

    for idx, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            return {"status": "error", "message": f"Question {idx} is not an object."}
        for key in ("word", "question", "options", "answer", "explanation"):
            if key not in item:
                return {"status": "error", "message": f"Question {idx} is missing '{key}'."}
        options = item.get("options")
        if not isinstance(options, list) or len(options) < 4:
            return {"status": "error", "message": f"Question {idx} must contain at least four options."}
        if item["answer"] not in options:
            return {"status": "error", "message": f"Question {idx} answer is not included in options."}

    return {"status": "success", "message": "ok"}


def _fallback_quiz_generation(words: List[Dict], pool: List[Dict]) -> List[Dict]:
    """在 LLM 不可用时基于词库构建基础多选题。"""
    if not words:
        return []

    definitions_pool = [item.get("definition") for item in pool if item.get("definition")]
    examples_pool = [item.get("example") for item in pool if item.get("example")]

    fallback_questions = []
    for entry in words:
        word = entry.get("word", "")
        definition = entry.get("definition") or entry.get("example") or "该词暂缺释义"
        language = entry.get("language") or _detect_language_category(word)
        focus = entry.get("focus") or "meaning"

        prompt_text = ""
        answer = ""
        explanation = ""
        options: List[str] = []

        if focus == "pronunciation" and entry.get("pinyin"):
            answer = entry.get("pinyin")
            prompt_text = f"请选择“{word}”的正确拼音。"
            pinyin_pool = [item.get("pinyin") for item in pool if item.get("pinyin") and item.get("pinyin") != answer]
            random.shuffle(pinyin_pool)
            options = [answer] + pinyin_pool[:3]
            while len(options) < 4:
                candidate = random.choice(pinyin_pool or [answer + "（变体）"])
                if candidate not in options:
                    options.append(candidate)
            explanation = f"正确的拼音是 {answer}。"
        elif focus in {"usage", "example"} and entry.get("example"):
            answer = entry.get("example")
            prompt_text = f"哪一句最适合作为“{word}”的使用示例？"
            example_pool = [item.get("example") for item in pool if item.get("example") and item.get("example") != answer]
            random.shuffle(example_pool)
            options = [answer] + example_pool[:3]
            while len(options) < 4:
                filler = random.choice(example_pool or [f"{word} is used in daily conversation."])
                if filler not in options:
                    options.append(filler)
            explanation = "示例句展示了该词在真实语境中的应用。"
        else:
            answer = definition
            prompt_text = f"下列哪项最贴近“{word}”的含义？" if language == "chinese" else f"What is the best meaning of \"{word}\"?"
            distractors = [opt for opt in definitions_pool if opt and opt != definition]
            random.shuffle(distractors)
            options = [answer] + distractors[:3]
            while len(options) < 4:
                candidate = random.choice(distractors or examples_pool or ["一个不相关的解释"])
                if candidate not in options:
                    options.append(candidate)
            explanation = entry.get("example") or definition or "该词暂无额外说明。"

        random.shuffle(options)

        fallback_questions.append({
            "word": word,
            "language": language,
            "question": prompt_text,
            "options": options,
            "answer": answer,
            "explanation": explanation,
            "focus": focus,
        })

    return fallback_questions


def generate_quiz_questions_from_vocab(
    words: List[Dict],
    full_pool: Optional[List[Dict]] = None,
    focuses: Optional[List[str]] = None,
    batch_token: Optional[str] = None
) -> List[Dict]:
    """基于生词表生成多选题，优先调用 LLM，失败时回退到规则生成。"""
    if not words:
        return []

    enriched_words = []
    for index, entry in enumerate(words):
        enriched_words.append({
            "word": entry.get("word", ""),
            "pinyin": entry.get("pinyin") or "",
            "definition": entry.get("definition") or "",
            "example": entry.get("example") or "",
            "language": _detect_language_category(entry.get("word", "")),
            "focus": (focuses[index] if focuses and index < len(focuses) else entry.get("focus")),
        })

    full_pool = full_pool or words

    lines = []
    for idx, item in enumerate(enriched_words, start=1):
        lines.append(
            f"{idx}. word: {item['word']}\n"
            f"   language: {item['language']}\n"
            f"   pinyin: {item['pinyin'] or 'N/A'}\n"
            f"   definition: {item['definition'] or 'N/A'}\n"
            f"   example: {item['example'] or 'N/A'}\n"
            f"   focus: {item['focus'] or 'mixed'}"
        )

    prompt = f"""
你是一位精通中英双语的语言教师。请根据提供的生词列表，为每个单词设计一道多项选择题。

严格遵循以下要求：
1. 仅按照提供的顺序逐一出题，题目数量必须等于单词数量（{len(enriched_words)} 道）。
2. 根据 language 字段决定呈现方式：
   - chinese: 可以考察释义、拼音、例句或用法。
   - english: 可以考察中文释义、词性、语境或例句。
   - other: 设计合理的语义理解题。
3. 每题必须提供 4 个不重复的选项，并在其中标记唯一正确答案。
4. 对于每个条目的 focus 字段，请尽量从该角度设计题目（如 pronunciation 强调读音，usage/example 强调语境）。
5. 解析（explanation）需简短说明为什么正确，并可引用示例或释义。
6. 输出必须是 JSON 对象，结构如下：
{{
  "questions": [
    {{
      "word": "原始单词",
      "language": "chinese|english|other",
      "question": "题干",
      "options": ["选项1", "选项2", "选项3", "选项4"],
      "answer": "正确选项文本",
      "explanation": "简短解析"
    }}, ...
  ]
}}

请严格返回符合上面结构的 JSON，避免多余文本或 Markdown。

当前批次标识：{batch_token or 'default'}。请确保题目新颖且符合该批次的要求。

单词资料：
{os.linesep.join(lines)}
"""

    try:
        response = ask_gpt(
            prompt,
            resp_type="json",
            valid_def=lambda data: _validate_quiz_payload(data, len(enriched_words)),
            log_title="practice_quiz"
        )

        raw_questions = response.get("questions", [])
        processed_questions = []
        for index, (meta, fallback) in enumerate(zip(raw_questions, enriched_words)):
            processed_questions.append({
                "word": meta.get("word") or fallback["word"],
                "language": meta.get("language") or fallback["language"],
                "question": meta.get("question", ""),
                "options": meta.get("options", []),
                "answer": meta.get("answer", ""),
                "explanation": meta.get("explanation", ""),
                "focus": focuses[index] if focuses and index < len(focuses) else fallback.get("focus"),
            })

        return processed_questions
    except Exception as exc:
        rprint(f"[yellow]LLM quiz generation failed, using fallback logic: {exc}[/yellow]")
        enriched_with_focus = []
        for idx, item in enumerate(enriched_words):
            clone = dict(item)
            clone["focus"] = focuses[idx] if focuses and idx < len(focuses) else item.get("focus")
            enriched_with_focus.append(clone)
        return _fallback_quiz_generation(enriched_with_focus, full_pool)
