import os
import re

import autocorrect_py as autocorrect
import pandas as pd
from rich.console import Console
from rich.panel import Panel

from core.utils import *
from core.utils.models import *

console = Console()

SUBTITLE_OUTPUT_CONFIGS = [
    ("src.srt", ["Source"]),
    ("trans.srt", ["Translation"]),
    ("src_trans.srt", ["Source", "Translation"]),
    ("trans_src.srt", ["Translation", "Source"]),
]

AUDIO_SUBTITLE_OUTPUT_CONFIGS = [
    ("src_subs_for_audio.srt", ["Source"]),
    ("trans_subs_for_audio.srt", ["Translation"]),
]


def convert_to_srt_format(start_time, end_time):
    """Convert seconds to SRT timestamp format."""

    def seconds_to_hmsm(seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds = seconds % 60
        milliseconds = int(seconds * 1000) % 1000
        return f"{hours:02d}:{minutes:02d}:{int(seconds):02d},{milliseconds:03d}"

    return f"{seconds_to_hmsm(start_time)} --> {seconds_to_hmsm(end_time)}"


def remove_punctuation(text):
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return text.strip()


def show_difference(str1, str2):
    """Show the difference positions between two strings."""
    min_len = min(len(str1), len(str2))
    diff_positions = []

    for i in range(min_len):
        if str1[i] != str2[i]:
            diff_positions.append(i)

    if len(str1) != len(str2):
        diff_positions.extend(range(min_len, max(len(str1), len(str2))))

    print("Difference positions:")
    print(f"Expected sentence: {str1}")
    print(f"Actual match: {str2}")
    print(
        "Position markers: "
        + "".join("^" if i in diff_positions else " " for i in range(max(len(str1), len(str2))))
    )
    print(f"Difference indices: {diff_positions}")


def get_sentence_timestamps(df_words, df_sentences):
    time_stamp_list = []

    full_words_str = ""
    position_to_word_idx = {}

    for idx, word in enumerate(df_words["text"]):
        clean_word = remove_punctuation(str(word).lower())
        start_pos = len(full_words_str)
        full_words_str += clean_word
        for pos in range(start_pos, len(full_words_str)):
            position_to_word_idx[pos] = idx

    current_pos = 0
    for idx, sentence in df_sentences["Source"].items():
        clean_sentence = remove_punctuation(str(sentence).lower()).replace(" ", "")
        sentence_len = len(clean_sentence)

        match_found = False
        while current_pos <= len(full_words_str) - sentence_len:
            if full_words_str[current_pos : current_pos + sentence_len] == clean_sentence:
                start_word_idx = position_to_word_idx[current_pos]
                end_word_idx = position_to_word_idx[current_pos + sentence_len - 1]
                time_stamp_list.append(
                    (
                        float(df_words["start"][start_word_idx]),
                        float(df_words["end"][end_word_idx]),
                    )
                )
                current_pos += sentence_len
                match_found = True
                break
            current_pos += 1

        if not match_found:
            print(f"\nWarning: No exact match found for sentence: {sentence}")
            show_difference(
                clean_sentence,
                full_words_str[current_pos : current_pos + len(clean_sentence)],
            )
            print("\nOriginal sentence:", df_sentences["Source"][idx])
            raise ValueError("No match found for sentence.")

    return time_stamp_list


def build_aligned_dataframe(df_text, df_translate):
    """Attach numeric timestamps to each translated sentence."""
    df_trans_time = df_translate.copy()
    time_stamp_list = get_sentence_timestamps(df_text, df_translate)
    df_trans_time["start"] = [stamp[0] for stamp in time_stamp_list]
    df_trans_time["end"] = [stamp[1] for stamp in time_stamp_list]

    for i in range(len(df_trans_time) - 1):
        delta_time = df_trans_time.loc[i + 1, "start"] - df_trans_time.loc[i, "end"]
        if 0 < delta_time < 1:
            df_trans_time.at[i, "end"] = df_trans_time.loc[i + 1, "start"]

    df_trans_time["duration"] = df_trans_time["end"] - df_trans_time["start"]
    return df_trans_time


def align_timestamp(df_text, df_translate, subtitle_output_configs: list, output_dir: str, for_display: bool = True):
    """Align timestamps and render subtitle files."""
    df_trans_time = build_aligned_dataframe(df_text, df_translate)
    df_trans_time["timestamp"] = df_trans_time.apply(
        lambda row: convert_to_srt_format(row["start"], row["end"]),
        axis=1,
    )

    if for_display:
        df_trans_time["Translation"] = df_trans_time["Translation"].apply(
            lambda x: re.sub(r"[，。]", " ", x).strip()
        )

    def generate_subtitle_string(df, columns):
        return "".join(
            [
                f"{i + 1}\n{row['timestamp']}\n{row[columns[0]].strip()}\n"
                f"{row[columns[1]].strip() if len(columns) > 1 else ''}\n\n"
                for i, row in df.iterrows()
            ]
        ).strip()

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        for filename, columns in subtitle_output_configs:
            subtitle_str = generate_subtitle_string(df_trans_time, columns)
            with open(os.path.join(output_dir, filename), "w", encoding="utf-8") as f:
                f.write(subtitle_str)

    return df_trans_time


def clean_translation(x):
    if pd.isna(x):
        return ""
    cleaned = str(x).strip("。").strip("，")
    return autocorrect.format(cleaned)


def align_timestamp_main():
    df_text = pd.read_excel(_2_CLEANED_CHUNKS)
    df_text["text"] = df_text["text"].str.strip('"').str.strip()

    df_translate_full = pd.read_excel(_4_2_TRANSLATION)
    df_translate_full = build_aligned_dataframe(df_text, df_translate_full)
    df_translate_full.to_excel(_4_2_TRANSLATION, index=False)

    df_translate = pd.read_excel(_5_SPLIT_SUB)
    df_translate["Translation"] = df_translate["Translation"].apply(clean_translation)
    align_timestamp(df_text, df_translate, SUBTITLE_OUTPUT_CONFIGS, _OUTPUT_DIR)
    console.print(
        Panel("[bold green]Subtitles generation completed. Please check the `output` folder.[/bold green]")
    )

    df_translate_for_audio = pd.read_excel(_5_REMERGED)
    df_translate_for_audio["Translation"] = df_translate_for_audio["Translation"].apply(clean_translation)
    align_timestamp(df_text, df_translate_for_audio, AUDIO_SUBTITLE_OUTPUT_CONFIGS, _AUDIO_DIR)
    console.print(
        Panel(
            f"[bold green]Audio subtitles generation completed. Please check the `{_AUDIO_DIR}` folder.[/bold green]"
        )
    )


if __name__ == "__main__":
    align_timestamp_main()
