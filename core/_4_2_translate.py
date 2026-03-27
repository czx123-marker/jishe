import pandas as pd
import json
import concurrent.futures
import re
from core.utils import check_file_exists
from core.utils.models import _3_2_SPLIT_BY_MEANING, _4_1_TERMINOLOGY, _4_2_TRANSLATION, _2_CLEANED_CHUNKS
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from difflib import SequenceMatcher
console = Console()

# --- Helper functions ---
def check_len_then_trim(text: str, duration: float, cps: int = 15) -> str:
    """Trims text based on duration and characters per second (cps)."""
    max_len = int(duration * cps)
    if len(text) > max_len:
        return text[:max_len] + '...'
    return text

def remove_punctuation(text):
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\s]', '', text)
    return text.strip()

def get_sentence_timestamps(df_words, sentence):
    clean_sentence = remove_punctuation(sentence.lower()).replace(" ", "")
    
    # Find words that make up this sentence
    matching_words = df_words[df_words['clean_text'].str.contains(clean_sentence, na=False)]
    if not matching_words.empty:
        return float(matching_words.iloc[0]['start']), float(matching_words.iloc[-1]['end'])
    return None, None
# Function to split text into chunks
def split_chunks_by_chars(chunk_size, max_i): 
    """Split text into chunks based on character count, return a list of multi-line text chunks"""
    with open(_3_2_SPLIT_BY_MEANING, "r", encoding="utf-8") as file:
        sentences = file.read().strip().split('\n')

    chunks = []
    chunk = ''
    sentence_count = 0
    for sentence in sentences:
        if len(chunk) + len(sentence + '\n') > chunk_size or sentence_count == max_i:
            chunks.append(chunk.strip())
            chunk = sentence + '\n'
            sentence_count = 1
        else:
            chunk += sentence + '\n'
            sentence_count += 1
    chunks.append(chunk.strip())
    return chunks

# Get context from surrounding chunks
def get_previous_content(chunks, chunk_index):
    return None if chunk_index == 0 else chunks[chunk_index - 1].split('\n')[-3:] # Get last 3 lines
def get_after_content(chunks, chunk_index):
    return None if chunk_index == len(chunks) - 1 else chunks[chunk_index + 1].split('\n')[:2] # Get first 2 lines

# 🔍 Translate a single chunk
def translate_chunk(chunk, chunks, theme_prompt, i):
    from core._4_1_summarize import search_things_to_note_in_prompt
    from core.translate_lines import translate_lines

    things_to_note_prompt = search_things_to_note_in_prompt(chunk)
    previous_content_prompt = get_previous_content(chunks, i)
    after_content_prompt = get_after_content(chunks, i)
    translation, english_result = translate_lines(chunk, previous_content_prompt, after_content_prompt, things_to_note_prompt, theme_prompt, i)
    return i, english_result, translation

# Add similarity calculation function
def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()

# 🚀 Main function to translate all chunks
@check_file_exists(_4_2_TRANSLATION)
def translate_all():
    from core.utils import load_key

    console.print("[bold green]Start Translating All...[/bold green]")
    chunks = split_chunks_by_chars(chunk_size=600, max_i=10)
    with open(_4_1_TERMINOLOGY, 'r', encoding='utf-8') as file:
        theme_prompt = json.load(file).get('theme')

    # 🔄 Use concurrent execution for translation
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
        task = progress.add_task("[cyan]Translating chunks...", total=len(chunks)) #!
        with concurrent.futures.ThreadPoolExecutor(max_workers=load_key("max_workers")) as executor:
            futures = []
            for i, chunk in enumerate(chunks):
                future = executor.submit(translate_chunk, chunk, chunks, theme_prompt, i)
                futures.append(future)
            results = []
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
                progress.update(task, advance=1)

    results.sort(key=lambda x: x[0])  # Sort results based on original order
    
    # 💾 Save results to lists and Excel file
    src_text, trans_text = [], []
    for i, chunk in enumerate(chunks):
        chunk_lines = chunk.split('\n')
        src_text.extend(chunk_lines)
        
        # Calculate similarity between current chunk and translation results
        chunk_text = ''.join(chunk_lines).lower()
        matching_results = [(r, similar(''.join(r[1].split('\n')).lower(), chunk_text)) 
                          for r in results]
        best_match = max(matching_results, key=lambda x: x[1])
        
        # Check similarity and handle exceptions
        if best_match[1] < 0.9:
            console.print(f"[yellow]Warning: No matching translation found for chunk {i}[/yellow]")
            raise ValueError(f"Translation matching failed (chunk {i})")
        elif best_match[1] < 1.0:
            console.print(f"[yellow]Warning: Similar match found (chunk {i}, similarity: {best_match[1]:.3f})[/yellow]")
            
        trans_text.extend(best_match[0][2].split('\n'))
    
    # Create DataFrame with source and translation
    df_translate = pd.DataFrame({'Source': src_text, 'Translation': trans_text})
    
    # Load segment-level timestamps from ASR
    df_segments = pd.read_excel(_2_CLEANED_CHUNKS)

    # Clean text for more reliable matching
    df_segments['clean_text'] = df_segments['text'].str.strip().str.lower()
    df_translate['clean_source'] = df_translate['Source'].str.strip().str.lower()

    # --- New Timestamp Assignment Logic ---
    def get_segment_timestamps(sentence, segments_df):
        # Find the segment that contains the sentence
        for _, segment in segments_df.iterrows():
            if sentence in segment['clean_text']:
                return segment['start'], segment['end']
        # If no direct match, find the best partial match (fallback)
        best_match = {'start': 0, 'end': 0, 'ratio': 0}
        for _, segment in segments_df.iterrows():
            ratio = similar(sentence, segment['clean_text'])
            if ratio > best_match['ratio']:
                best_match.update({'start': segment['start'], 'end': segment['end'], 'ratio': ratio})
        if best_match['ratio'] > 0.8:
            return best_match['start'], best_match['end']
        return 0, 0 # Return 0 if no good match is found

    # Apply the function to get timestamps for each sentence
    timestamps = df_translate['clean_source'].apply(lambda s: get_segment_timestamps(s, df_segments))
    df_translate['start'] = timestamps.apply(lambda x: x[0])
    df_translate['end'] = timestamps.apply(lambda x: x[1])
    df_translate['duration'] = df_translate['end'] - df_translate['start']

    # Drop the temporary columns
    df_translate.drop(columns=['clean_source'], inplace=True)

    # Trim long translation text
    df_translate['Translation'] = df_translate.apply(lambda x: check_len_then_trim(x['Translation'], x['duration']) if x['duration'] > load_key("min_trim_duration") else x['Translation'], axis=1)
    
    console.print(df_translate)
    df_translate.to_excel(_4_2_TRANSLATION, index=False)
    console.print("[bold green]✅ Translation completed and results saved.[/bold green]")

if __name__ == '__main__':
    translate_all()