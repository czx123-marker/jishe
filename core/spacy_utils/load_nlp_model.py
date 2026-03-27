import spacy
from spacy.cli import download
from core.utils import rprint, load_key, except_handler

SPACY_MODEL_MAP = load_key("spacy_model_map")

def get_spacy_model(language: str):
    # 确保 language 是一个有效的字符串，如果不是（比如 None），则视为空字符串
    lang_key = language.lower() if isinstance(language, str) else ""
    
    # .get() 方法的第二个参数是默认值。如果 lang_key 在映射中找不到，则返回 "en_core_web_md"
    model = SPACY_MODEL_MAP.get(lang_key, "en_core_web_md")
    
    # 仅在传入的 language 有效但不在映射中时打印警告
    if lang_key and lang_key not in SPACY_MODEL_MAP:
        rprint(f"[yellow]Spacy model does not support '{language}', using en_core_web_md model as fallback...[/yellow]")
    return model

@except_handler("Failed to load NLP Spacy model")
def init_nlp():
    # 修复逻辑：正确处理语言选择
    # 1. 如果用户明确指定了语言，则使用该语言。
    # 2. 如果开启了自动检测，则使用检测到的语言。
    # 3. 如果以上两种情况都没有有效值，则默认使用 'en'。
    user_lang = load_key("whisper.language")
    detected_lang = load_key("whisper.detected_language")

    if user_lang and user_lang != 'auto':
        language = user_lang
    else:
        language = detected_lang if detected_lang else "en"
    model = get_spacy_model(language)
    rprint(f"[blue]⏳ Loading NLP Spacy model: <{model}> ...[/blue]")
    try:
        nlp = spacy.load(model)
    except:
        rprint(f"[yellow]Downloading {model} model...[/yellow]")
        rprint("[yellow]If download failed, please check your network and try again.[/yellow]")
        download(model)
        nlp = spacy.load(model)
    rprint("[green]✅ NLP Spacy model loaded successfully![/green]")
    return nlp

# --------------------
# define the intermediate files
# --------------------
SPLIT_BY_COMMA_FILE = "output/log/split_by_comma.txt"
SPLIT_BY_CONNECTOR_FILE = "output/log/split_by_connector.txt"
SPLIT_BY_MARK_FILE = "output/log/split_by_mark.txt"
